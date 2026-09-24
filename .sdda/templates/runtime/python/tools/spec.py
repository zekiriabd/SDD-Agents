"""Le contrat d'outil, côté runtime — GÉNÉRÉ, ne pas éditer.

Ce module porte deux choses que tout outil partage, et rien d'autre :

    ToolSpec      ce que le CONTRAT dit de l'outil, résolu au build
    ToolContext   ce que le RUNTIME lui fournit pour un appel

La séparation compte. Le `ToolSpec` est figé et hashé : c'est lui qui entre dans
le tuple d'épinglage P10, et c'est sur sa `description` — celle du contrat, pas
un résumé réécrit dans le code — que le modèle décide d'appeler l'outil. Le
`ToolContext` change à chaque appel : il porte le run, l'agent, l'horloge et le
tracer.

**Le contrat Markdown n'est jamais lu au runtime.** `gen_source_tools.py` écrit
un `tool_specs.json` résolu à côté du code. Trois raisons, la dernière étant la
vraie : parser du Markdown au démarrage d'un service est une dépendance inutile ;
`workspace/pipeline/contracts/` n'est pas livré avec l'application ; et **ce qui est
résolu est épinglable** — `tool_schema_hash` doit désigner ce que l'outil fait
réellement, pas ce qu'un fichier disait au moment où on l'a relu.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

SPEC_FILE = "tool_specs.json"

#: Ce qu'un outil peut faire au monde. Déclaré, jamais déduit : une classe
#: devinée depuis le nom de la fonction est une classe fausse le jour où
#: quelqu'un renomme.
SideEffectClass = Literal["read-only", "idempotent-write", "external-side-effect", "write-destructive"]

#: La sortie de l'outil est-elle du texte hostile ? (P8)
Trust = Literal["trusted", "untrusted"]


class ToolError(Exception):
    """Erreur déclarée d'un outil. `code` est ce que voit l'agent."""

    def __init__(self, code: str, message: str, *, tool: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.tool = tool


@dataclass(frozen=True)
class ToolSpec:
    """Le contrat, résolu. Immuable pour la durée du processus."""

    id: str
    name: str
    description: str
    side_effect_class: SideEffectClass = "read-only"
    trust: Trust = "trusted"
    timeout_s: int = 10
    rate_limit_rpm: int = 60
    retry_policy: str = "none"
    max_response_bytes: int = 65536
    errors: tuple[str, ...] = ()
    tool_schema_hash: str = ""

    @property
    def idempotent(self) -> bool:
        return self.side_effect_class in ("read-only", "idempotent-write")

    def check_retry_is_safe(self) -> None:
        """Un retry sur un outil non idempotent crée trois tickets.

        C'est le piège principal des outils d'écriture, et il ne se voit pas en
        test : il se voit chez le client, en trois exemplaires.
        """
        if not self.idempotent and self.retry_policy not in ("none", "no-retry"):
            raise ToolError(
                "TOOL_RETRY_UNSAFE",
                f"`{self.name}` est `{self.side_effect_class}` avec `retry_policy: "
                f"{self.retry_policy}` — un réessai produirait un second effet de bord",
                tool=self.name)

    @classmethod
    def from_contract(cls, contract_id: str) -> "ToolSpec":
        specs = load_specs()
        spec = specs.get(contract_id)
        if spec is None:
            raise ToolError(
                "TOOL_SPEC_MISSING",
                f"aucun contrat résolu pour `{contract_id}` — lancer "
                f"`gen_source_tools.py --write`. Un outil sans contrat n'a ni description "
                f"revue, ni classe d'effet de bord, ni hash d'épinglage",
                tool=contract_id)
        return spec


def spec_files(base: Path | None = None) -> list[Path]:
    """Tous les `tool_specs.json` du paquet applicatif.

    Un fichier par famille d'outils (les outils de données sont générés, ceux de
    `architect-tools` sont écrits ailleurs) : les fusionner en un seul fichier
    ferait qu'une régénération partielle en effacerait la moitié.
    """
    root = base or Path(__file__).resolve().parent.parent
    # Un environnement virtuel laissé DANS le paquet (`.venv/`) en contient une
    # copie installée : la compter doublait chaque outil ([TOOL_SPEC_DUPLICATE]).
    skip = {".venv", "venv", "site-packages", "build", "dist", "__pycache__"}
    return sorted(p for p in root.rglob(SPEC_FILE) if not skip & set(p.relative_to(root).parts))


@lru_cache(maxsize=4)
def load_specs(base: str | None = None) -> dict[str, ToolSpec]:
    out: dict[str, ToolSpec] = {}
    for path in spec_files(Path(base) if base else None):
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload.get("tools") or []:
            spec = ToolSpec(
                id=str(entry["id"]), name=str(entry["name"]),
                description=str(entry.get("description") or ""),
                side_effect_class=entry.get("side_effect_class", "read-only"),
                trust=entry.get("trust", "trusted"),
                timeout_s=int(entry.get("timeout_s", 10)),
                rate_limit_rpm=int(entry.get("rate_limit_rpm", 60)),
                retry_policy=str(entry.get("retry_policy", "none")),
                max_response_bytes=int(entry.get("max_response_bytes", 65536)),
                errors=tuple(entry.get("errors") or ()),
                tool_schema_hash=str(entry.get("tool_schema_hash") or ""),
            )
            if spec.id in out:
                # Deux fichiers qui déclarent le même contrat : lequel gouverne
                # dépendrait de l'ordre de lecture, donc du système de fichiers.
                raise ToolError("TOOL_SPEC_DUPLICATE",
                                f"contrat `{spec.id}` déclaré deux fois", tool=spec.id)
            out[spec.id] = spec
    return out


@dataclass
class ToolContext:
    """Ce que le runtime fournit à un outil, pour UN appel.

    Porte aussi ce dont l'accès aux données a besoin (`base`, `registry_path`,
    le cache d'index) : les outils de données sont des outils, et leur donner un
    second contexte obligerait chaque wrapper à en composer deux.
    """

    base: Path
    run_id: str = ""
    agent_id: str = ""
    registry_path: str | None = None
    span: Callable[[str, dict[str, Any]], None] | None = None
    clock: Callable[[], float] | None = None
    #: L'identité de l'APPELANT, établie par le transport (`--tenant` en CLI,
    #: l'authentification en HTTP) — jamais par le modèle. Chaque source qui
    #: déclare `required_filter: [customer_id]` est filtrée sur ces valeurs par
    #: le runtime ; absente, la lecture est refusée (fail-closed).
    identity: dict[str, str] = field(default_factory=dict)

    _indexes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.clock is None:
            import time
            self.clock = time.monotonic

    def emit(self, name: str, payload: dict[str, Any]) -> None:
        """Émet un span. Le `run_id` y est joint : une trace sans lui ne se rejoue pas."""
        if self.span is None:
            return
        enriched = {"run_id": self.run_id, "agent_id": self.agent_id, **payload}
        self.span(name, {k: v for k, v in enriched.items() if v not in (None, "")})

"""Taxonomie d'erreurs `[CLASS]` et rapports de validation.

Tout bloc ERROR porte une classe dans son `CAUSE:` pour que hooks, boucles de
reprise et tableaux de bord classent sans interpréter du texte
(ARCHITECTURE.md §9). Format canonique, 3 lignes :

    ERROR: <ce qui a échoué>
    CAUSE: [CLASS] <détail>
    FIX: <l'action qui corrige>

Familles : le préfixe d'une classe désigne sa famille. Quelques classes nommées
par la documentation ne suivent pas ce préfixe (`[AC_NOT_EVALUABLE]`,
`[UNBOUNDED_LOOP]`, `[TRACEABILITY_GAP]`…) : elles sont enregistrées ici
explicitement avec leur famille, pour que `family_of` ne renvoie jamais None
sur une classe que le framework émet réellement.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

FAMILIES: tuple[str, ...] = (
    "MISSION", "CAP", "TOPOLOGY", "AGENT", "TOOL", "RETRIEVAL", "MEMORY",
    "PROMPT", "EVAL", "JUDGE", "BUDGET", "SAFETY", "TRACE", "CONFIG",
    # La couture entre l'IR et le monde extérieur (G6, part `api`). Famille
    # ajoutée quand ses classes ont enfin reçu un émetteur
    # (`validate_api_contract.py`) : elles étaient annoncées bloquantes dans
    # ARCHITECTURE.md §4 et appliquées par rien.
    "API",
)

#: Classes nommées par PHILOSOPHY / ARCHITECTURE / LIFECYCLE / AGENTIC-IR dont le
#: préfixe n'est pas une famille : classe -> famille.
EXTRA_CLASSES: dict[str, str] = {
    "AC_NOT_EVALUABLE": "CAP",
    "TRACEABILITY_GAP": "CAP",
    "UNBOUNDED_LOOP": "TOPOLOGY",
    "ROUTER_NO_FALLBACK": "TOPOLOGY",
    "HANDOFF_UNCONTRACTED": "TOPOLOGY",
    "REFLECTION_SELF_GRADING": "TOPOLOGY",
    "IR_SCHEMA_INVALID": "TOPOLOGY",
    # Scission intent/binding de l'IR : un composant d'infrastructure nommé
    # hors de `binding`. Jumelle de FRAMEWORK_LEAK_IN_CONTRACT — même faute,
    # une couche plus bas.
    "INFRA_LEAK_IN_INTENT": "TOPOLOGY",
    "SIDE_EFFECT_UNDECLARED": "TOOL",
    "DATASET_OVERLAP": "EVAL",
    "DATASET_TOO_SMALL": "EVAL",
    "DATASET_ITEM_INVALID": "EVAL",
    "DATASET_DUPLICATE_ID": "EVAL",
    "DATASET_MISSING": "EVAL",
    "PACK_UNUSABLE": "CONFIG",
    "CONTEXT_BUDGET_EXCEEDED": "CONFIG",
    "CLI_COMMAND_UNKNOWN": "CONFIG",
    "STATE_RUN_NOT_FOUND": "TRACE",
    "STATE_PHASE_UNKNOWN": "TRACE",
    "STATE_SKIP_FORBIDDEN": "TRACE",
    "STATE_PHASE_NOT_ITEMIZED": "TRACE",
    "STATE_ITEM_UNKNOWN": "TRACE",
    "STATUS_UNBACKED": "TRACE",
    "STATUS_PINNED_HASH_MOVED": "TRACE",
    "STATUS_ILLEGAL_JUMP": "TRACE",
    "INJECTION_SUCCEEDED": "SAFETY",
    "INJECTION_SUITE_MISSING": "SAFETY",
    "EXFILTRATION_SUCCEEDED": "SAFETY",
    "TENANT_BOUNDARY_CROSSED": "SAFETY",
    "REFUSAL_POLICY_BYPASSED": "SAFETY",
    "ADVERSARIAL": "SAFETY",
    "ADVERSARIAL_REQUIRED": "SAFETY",
    "BOUND_BEHAVIOR_MISMATCH": "AGENT",
    "CITATION_UNRESOLVED": "RETRIEVAL",
    "GOLDEN_SET_MISSING": "EVAL",
    "MEASUREMENT_MISSING": "EVAL",
    "BYPASS_REASON_MISSING": "CONFIG",
    "IR_NOT_FOUND": "TOPOLOGY",
    # `project-init` / `gen-app-context` : le projet applicatif et son contexte.
    "PROJECT_NOT_INIT": "CONFIG",
    "PROJECT_CONTEXT_STALE": "CONFIG",
    "PROJECT_DEPS_NOT_INSTALLED": "CONFIG",
    "PROJECT_DEPS_INSTALL_FAILED": "CONFIG",
    "GOAL_NOT_MET": "EVAL",
    "REGRESSION": "EVAL",
}

_CLASS_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


def family_of(cls: str) -> str | None:
    """Famille d'une classe (`MISSION_INCOMPLETE` -> `MISSION`), None si inconnue."""
    if cls in EXTRA_CLASSES:
        return EXTRA_CLASSES[cls]
    prefix = cls.split("_", 1)[0]
    return prefix if prefix in FAMILIES else None


def format_error_block(error: str, cls: str, detail: str, fix: str) -> str:
    """Le bloc ERROR canonique en 3 lignes."""
    return f"ERROR: {error}\nCAUSE: [{cls}] {detail}\nFIX: {fix}"


class SddaError(Exception):
    """Erreur portant une classe `[CLASS]`. `str(e)` rend le bloc 3 lignes."""

    def __init__(self, error: str, cls: str, fix: str, *, detail: str = "", location: str | None = None):
        if not _CLASS_RE.match(cls):
            raise ValueError(f"classe d'erreur mal formée : {cls!r}")
        super().__init__(error)
        self.error = error
        self.cls = cls
        self.detail = detail or error
        self.fix = fix
        self.location = location

    def format_block(self) -> str:
        return format_error_block(self.error, self.cls, self.detail, self.fix)

    def __str__(self) -> str:
        return self.format_block()

    def to_finding(self) -> "Finding":
        return Finding("error", self.cls, self.detail, self.fix, self.location, title=self.error)


@dataclass
class Finding:
    severity: str            # "error" | "warn"
    cls: str
    message: str
    fix: str = ""
    location: str | None = None
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"severity": self.severity, "class": self.cls, "message": self.message}
        if self.fix:
            d["fix"] = self.fix
        if self.location:
            d["location"] = self.location
        return d

    def render(self) -> str:
        loc = f" ({self.location})" if self.location else ""
        if self.severity == "error":
            return format_error_block((self.title or self.message) + loc, self.cls, self.message, self.fix or "voir la documentation de la classe")
        return f"WARN [{self.cls}] {self.message}{loc}"


@dataclass
class Report:
    """Résultat d'un validateur : liste de findings + verdict."""

    name: str
    target: str = ""
    findings: list[Finding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def error(self, cls: str, message: str, fix: str = "", location: str | None = None, title: str = "") -> None:
        self.findings.append(Finding("error", cls, message, fix, location, title))

    def warn(self, cls: str, message: str, fix: str = "", location: str | None = None) -> None:
        self.findings.append(Finding("warn", cls, message, fix, location))

    def extend(self, other: "Report") -> None:
        self.findings.extend(other.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def classes(self) -> set[str]:
        return {f.cls for f in self.findings}

    def has(self, cls: str) -> bool:
        return cls in self.classes

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target": self.target,
            "ok": self.ok,
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
            **({"data": self.data} if self.data else {}),
        }

    def render_text(self) -> str:
        verdict = "OK" if self.ok else "ROUGE"
        lines = [f"[{self.name}] {self.target} -> {verdict} ({len(self.errors)} erreur(s), {len(self.warnings)} avertissement(s))"]
        for f in self.findings:
            lines.append(f.render())
        return "\n".join(lines)


def emit(report: Report, json_mode: bool, stream: TextIO | None = None) -> int:
    """Écrit le rapport (texte ou JSON) et renvoie le code de sortie 0/1."""
    out = stream or sys.stdout
    if json_mode:
        out.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        out.write(report.render_text() + "\n")
    return report.exit_code

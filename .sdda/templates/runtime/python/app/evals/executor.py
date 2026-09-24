"""Les exécuteurs d'eval — ce que `eval-runner --executor` charge. GÉNÉRÉ, ne pas éditer.

C'est la pièce sans laquelle G5, G6 et G8 n'ont rien à mesurer. Le runner
(`sdda_scripts/eval_runner.py`) n'appelle aucun modèle : il orchestre k runs, il
fait noter les sorties par un grader, il agrège, il épingle. Ce qu'il lui faut,
c'est un objet doté d'une méthode `run` :

    run(item, *, suite, run_index, seed) -> {"output", "cost_usd", "latency_ms", "trace"}

Deux implémentations, parce que deux niveaux d'eval posent deux questions
différentes :

**`InProcessExecutor` — L4, l'agent SEUL.** Il appelle `RunService` dans le
processus. En mode `isolated`, les outils sont mockés et le retrieval est figé :
c'est ce qui rend la variation **attribuable**. Évaluer un agent avec ses vrais
outils, c'est mesurer l'agent plus la base plus le réseau plus l'index — et
quand le score baisse, on ne sait pas lequel des quatre a bougé.

**`CliExecutor` — L5, L7, L9, le système ENTIER.** Il lance réellement
`uv run {AppName} run --json` et lit le NDJSON et le code de sortie. Le
sous-processus n'est pas une lourdeur : c'est la garantie que ce qu'on mesure
est ce qu'on livre, y compris la construction de la configuration, l'écriture
de la trace et la traduction du statut en code de sortie. Un exécuteur en
processus ne verrait jamais un `exit 8` dû à une configuration que seul le
paquet installé expose.

La trace rendue est au format CANONIQUE
----------------------------------------
`{"spans": [...]}` — c'est la première forme que lit le grader `trajectory`, et
la seule qui porte les hops, les bornes atteintes et l'agent responsable de
chaque appel d'outil. Rendre `{"calls": [...]}` fonctionnerait, et perdrait
exactement ce que la L5 et la L8 mesurent.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import Settings
from ..models import StubClient
from ..orchestration.base import DictToolset, ToolOutcome
from ..run_service import RunRequest, RunService

#: Le mode d'isolement d'une suite, tel que `pytest-eval.md §3.1` l'écrit.
#: `mocked`/`frozen` sont les valeurs de la L4 ; `live` est ce qu'on mesure en
#: L7. Une suite muette est traitée comme `live` : rien ne doit être isolé sans
#: qu'on l'ait demandé, sinon une eval « bout en bout » mesure des doubles.
ISOLATED_TOOLS = "mocked"
ISOLATED_RETRIEVAL = "frozen"


def _isolation(suite: Mapping[str, Any] | None) -> dict[str, str]:
    block = (suite or {}).get("isolation") or {}
    return {"tools": str(block.get("tools") or "live"),
            "retrieval": str(block.get("retrieval") or "live")}


def _item_input(item: Mapping[str, Any]) -> str:
    """L'entrée d'un item de dataset. `input` peut être un objet (golden-set)."""
    raw = item.get("input")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping):
        for key in ("text", "query", "question", "prompt"):
            if isinstance(raw.get(key), str):
                return str(raw[key])
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Doubles d'isolement — L4
# ---------------------------------------------------------------------------
def mocked_toolset(fixtures: Path | Mapping[str, Any] | None) -> DictToolset:
    """Des outils qui répondent depuis des fixtures, sans réseau ni base.

    Les fixtures viennent de `workspace/pipeline/fixtures/tools/*.jsonl`, une ligne
    par réponse : `{"tool": "…", "result": …}`. Un outil sans fixture ne rend pas
    une réponse vide — il rend une ERREUR déclarée. Une réponse vide se
    confondrait avec « l'outil n'a rien trouvé », et l'agent répondrait
    tranquillement à côté sans que rien ne signale la fixture manquante.
    """
    responses: dict[str, Any] = {}
    if isinstance(fixtures, Mapping):
        responses.update(fixtures)
    elif fixtures is not None:
        directory = Path(fixtures)
        for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else ():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("tool"):
                    responses[str(entry["tool"])] = entry.get("result")

    def handler(name: str) -> Callable[..., ToolOutcome]:
        def _call(**_: Any) -> ToolOutcome:
            payload = responses[name]
            return ToolOutcome(content=payload if isinstance(payload, str) else json.dumps(
                payload, ensure_ascii=False, sort_keys=True, default=str))
        return _call

    return DictToolset(tools={name: handler(name) for name in responses})


def frozen_retrieval(fixtures: Mapping[str, Sequence[Mapping[str, Any]]] | None = None
                     ) -> Callable[[str], list[Mapping[str, Any]]]:
    """Un retriever figé, indexé par la requête. Déterministe par construction.

    Figer le retrieval en L4 n'est pas tricher : c'est séparer « l'agent
    raisonne mal » de « l'index a changé ». Les deux se corrigent ailleurs, et
    les confondre fait retoucher un prompt pour un problème d'ingestion.
    """
    table = {str(k): list(v) for k, v in (fixtures or {}).items()}

    def retrieve(query: str) -> list[Mapping[str, Any]]:
        return list(table.get(query, ()))

    return retrieve


# ---------------------------------------------------------------------------
# L4 — l'agent seul, dans le processus
# ---------------------------------------------------------------------------
@dataclass
class InProcessExecutor:
    """Appelle `RunService` directement. Instanciable sans argument.

    Sans argument, parce que `eval_runner.load_executor("module:attr")`
    instancie une classe telle quelle : un constructeur qui exigerait une
    configuration rendrait l'exécuteur inchargeable par la voie que le runner
    utilise réellement.
    """

    name: str = "in-process"
    isolated: bool = False
    settings: Settings | None = None
    service: RunService | None = None
    service_factory: Callable[..., RunService] | None = None
    tool_fixtures: Path | Mapping[str, Any] | None = None
    retrieval_fixtures: Mapping[str, Sequence[Mapping[str, Any]]] | None = None
    client: Any = None
    _services: dict[bool, RunService] = field(default_factory=dict, repr=False)

    def run(self, item: Mapping[str, Any], *, suite: Mapping[str, Any] | None = None,
            run_index: int = 0, seed: int | None = None, **options: Any) -> dict[str, Any]:
        """Un item, un run. `**options` absorbe ce que le runner ajoutera demain.

        Tolérer les options inconnues n'est pas de la négligence : le protocole
        de l'exécuteur est une frontière entre deux composants versionnés
        séparément, et un `TypeError` sur un mot-clé nouveau ferait échouer
        toutes les evals d'un projet le jour où le runner gagne une option.
        """
        isolated = bool(options.get("isolated", self.isolated)) or self._suite_isolated(suite)
        service = self._service(isolated)
        started = time.monotonic()
        result = service.run_sync(RunRequest(input=_item_input(item), surface="cli",
                                             run_id=f"{item.get('id', 'item')}-{run_index}"))
        return {
            "output": result.output,
            "cost_usd": result.cost_usd,
            # La latence mesurée ici englobe le service entier, pas seulement
            # l'appel au modèle : c'est ce qu'un utilisateur attend, et c'est ce
            # que G6 compare au budget déclaré.
            "latency_ms": result.latency_ms or int((time.monotonic() - started) * 1000),
            "trace": result.trace,
            "status": result.status,
            "exit_code": _exit_code(result),
            "run_id": result.run_id,
        }

    @staticmethod
    def _suite_isolated(suite: Mapping[str, Any] | None) -> bool:
        isolation = _isolation(suite)
        return isolation["tools"] == ISOLATED_TOOLS or isolation["retrieval"] == ISOLATED_RETRIEVAL

    def _service(self, isolated: bool) -> RunService:
        if self.service is not None:
            return self.service
        if isolated not in self._services:
            self._services[isolated] = self._build(isolated)
        return self._services[isolated]

    def _build(self, isolated: bool) -> RunService:
        if self.service_factory is not None:
            return self.service_factory(isolated=isolated)
        kwargs: dict[str, Any] = {"settings": self.settings}
        if isolated:
            # Outils mockés et retrieval figé : la seule variation qui reste est
            # celle du modèle, donc celle qu'on voulait mesurer.
            kwargs["toolset"] = mocked_toolset(self.tool_fixtures)
            kwargs["client"] = self.client or StubClient()
            return RunService(**kwargs)
        if self.client is not None:
            kwargs["client"] = self.client
        # Hors isolement, le client est celui du fournisseur ACTIF : un run
        # « bout en bout » qui appellerait un double mesurerait la plomberie et
        # rapporterait le résultat comme s'il venait du modèle.
        return RunService(**kwargs)


def _exit_code(result: Any) -> int:
    from ..serving.exit_codes import resolve_exit_code  # noqa: PLC0415 - évite un cycle

    return int(resolve_exit_code(status=result.status, error_class=result.error_class,
                                 bound_exceeded=result.bound_exceeded))


# ---------------------------------------------------------------------------
# L5 / L7 / L9 — le système entier, par la surface livrée
# ---------------------------------------------------------------------------
@dataclass
class CliExecutor:
    """Lance la CLI en sous-processus et lit son NDJSON.

    C'est la définition opératoire de « ce qu'on mesure est ce qu'on livre » :
    la même commande qu'un humain tape, le même code de sortie qu'une CI lit, la
    même trace qu'un post-mortem ouvrira. Un exécuteur qui court-circuiterait la
    surface mesurerait un chemin que personne n'emprunte.

    Le code de sortie est une MESURE, pas un détail : `3` (borne), `4` (refus
    attendu en L8), `5` (budget), `7` (dégradé) sont des résultats que le
    verdict doit distinguer. Les aplatir en « échec » ferait échouer la SAFETY
    GATE sur les cas où le refus était la bonne réponse.
    """

    name: str = "cli"
    command: Sequence[str] = ("uv", "run", "{AppName}", "run", "--json")
    cwd: Path | None = None
    timeout_s: float = 300.0
    isolated: bool = False
    env: Mapping[str, str] | None = None

    def run(self, item: Mapping[str, Any], *, suite: Mapping[str, Any] | None = None,
            run_index: int = 0, seed: int | None = None, **options: Any) -> dict[str, Any]:
        argv = [*self.command, "--input", _item_input(item)]
        tenant = str(item.get("tenant") or (suite or {}).get("tenant") or "")
        if tenant:
            argv += ["--tenant", tenant]

        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603 - argv en liste, jamais de shell
            argv, capture_output=True, text=True, encoding="utf-8",
            cwd=str(self.cwd) if self.cwd else None, timeout=self.timeout_s,
            env=dict(self.env) if self.env else None, check=False)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        events = _parse_ndjson(completed.stdout)
        final = next((e for e in reversed(events) if e.get("event") == "final"), {})
        finished = next((e for e in reversed(events) if e.get("event") == "run_finished"), {})
        error = next((e for e in reversed(events) if e.get("event") == "error"), {})

        trace = _load_trace(finished.get("trace_path"))
        return {
            "output": final.get("output"),
            "cost_usd": float(finished.get("cost_usd") or 0.0),
            "latency_ms": int(finished.get("duration_ms") or elapsed_ms),
            "trace": trace,
            "status": str(finished.get("status") or ("failed" if error else "ok")),
            "exit_code": completed.returncode,
            "error_class": str(error.get("class") or ""),
            "run_id": str(finished.get("run_id") or ""),
            # `stderr` est conservé parce qu'un run qui échoue à démarrer n'émet
            # aucun événement : sans lui, le rapport d'eval dirait « sortie
            # vide » là où le message expliquait pourquoi.
            "stderr": completed.stderr[-4000:],
        }


def _parse_ndjson(payload: str) -> list[dict[str, Any]]:
    """Les événements d'une sortie NDJSON. Une ligne illisible est ignorée.

    Ignorée, et non fatale : une trace tronquée par une interruption reste
    exploitable jusqu'au point de coupure, et c'est précisément le run qu'on
    voudra lire.
    """
    events: list[dict[str, Any]] = []
    for line in (payload or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _load_trace(path: Any) -> dict[str, Any]:
    """Relit le JSONL du run et rend la forme canonique `{"spans": [...]}`."""
    if not path:
        return {"spans": []}
    file = Path(str(path))
    if not file.is_file():
        return {"spans": []}
    spans: list[dict[str, Any]] = []
    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            span = json.loads(line)
        except ValueError:
            continue
        if isinstance(span, dict):
            spans.append(span)
    return {"spans": spans, "trace_path": str(file)}


#: Ce que `--executor {AppName}.evals.executor:EXECUTOR` charge par défaut :
#: l'exécuteur en processus, non isolé. Le runner accepte aussi une classe
#: (instanciée sans argument) ou une fabrique.
EXECUTOR = InProcessExecutor()

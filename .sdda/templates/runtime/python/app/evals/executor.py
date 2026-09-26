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
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import Settings
from ..isolation import frozen_retrieval, mocked_toolset
from ..models import StubClient  # noqa: F401 - réexporté : les tests des projets s'en servent
from ..run_service import RunRequest, RunService, composed_service

__all__ = ["CliExecutor", "EXECUTOR", "InProcessExecutor", "frozen_retrieval", "mocked_toolset"]

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
# `mocked_toolset` et `frozen_retrieval` vivent dans `..isolation` : l'isolement
# est aussi celui de la CLI (`SDDA_EVAL_ISOLATION`, `serving/cli.md §3.5`), et
# deux implémentations d'un même double rendraient deux mesures. Réexportés ici
# pour les projets qui les importent depuis `evals.executor`.


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
    _services: dict[tuple[bool, bool], RunService] = field(default_factory=dict, repr=False)

    def run(self, item: Mapping[str, Any], *, suite: Mapping[str, Any] | None = None,
            run_index: int = 0, seed: int | None = None, **options: Any) -> dict[str, Any]:
        """Un item, un run. `**options` absorbe ce que le runner ajoutera demain.

        Tolérer les options inconnues n'est pas de la négligence : le protocole
        de l'exécuteur est une frontière entre deux composants versionnés
        séparément, et un `TypeError` sur un mot-clé nouveau ferait échouer
        toutes les evals d'un projet le jour où le runner gagne une option.
        """
        isolation = _isolation(suite)
        forced = bool(options.get("isolated", self.isolated))
        mode = (forced or isolation["tools"] == ISOLATED_TOOLS,
                forced or isolation["retrieval"] == ISOLATED_RETRIEVAL)
        service = self._service(mode)
        started = time.monotonic()
        # L'identité d'item devient un nom de fichier de trace : le traceur
        # l'assainit (`../` ou `:` n'y survivent pas) et le suffixe si un run
        # précédent a déjà écrit ce fichier.
        tenant = str(item.get("tenant") or (suite or {}).get("tenant") or "")
        result = service.run_sync(RunRequest(input=_item_input(item), surface="cli", tenant_id=tenant,
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
            "error_class": result.error_class,
            "exit_code": _exit_code(result),
            "run_id": result.run_id,
        }

    def _service(self, mode: tuple[bool, bool]) -> RunService:
        if self.service is not None:
            return self.service
        if mode not in self._services:
            self._services[mode] = self._build(mode)
        return self._services[mode]

    def _build(self, mode: tuple[bool, bool]) -> RunService:
        tools_mocked, retrieval_frozen = mode
        if self.service_factory is not None:
            return self.service_factory(isolated=tools_mocked or retrieval_frozen)
        kwargs: dict[str, Any] = {}
        if tools_mocked:
            # Outils mockés et retrieval figé : la seule variation qui reste est
            # celle du MODÈLE, donc celle qu'on voulait mesurer. Le client est
            # celui du fournisseur actif (`stub` si `## Runtime Models` le dit) :
            # un `StubClient` par défaut faisait mesurer à L4 un double qui
            # répond « stub », et G5 notait la plomberie au lieu de l'agent.
            kwargs["toolset"] = mocked_toolset(self.tool_fixtures)
        if retrieval_frozen:
            # Le retrieval figé n'était déclaré que dans la fiche : l'exécuteur
            # ne le passait à personne, et une L4 « isolée » interrogeait
            # l'index réel. `composed_service` refuse désormais une composition
            # qui ne sait pas le recevoir, au lieu de l'ignorer.
            kwargs["retriever"] = frozen_retrieval(self.retrieval_fixtures)
        if self.client is not None:
            kwargs["client"] = self.client
        # Le système est celui que l'APPLICATION compose (`app/composition.py`,
        # `build_system`) : son agent, son prompt épinglé, ses outils câblés. Un
        # `RunService` nu faisait tourner un agent générique au prompt vide — on
        # évaluait ce que personne n'a livré. Repli sur `RunService` seulement
        # si l'application n'a pas (encore) de composition — `composed_service`,
        # le même chemin que la CLI.
        return composed_service(self.settings, **kwargs)


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
    #: Environnement EXPLICITE du sous-processus. Absent : l'environnement
    #: courant FILTRÉ (`child_env`), jamais recopié en entier.
    env: Mapping[str, str] | None = None
    #: Variables à transmettre en plus du socle, par NOM (une clé exportée dans
    #: le shell plutôt que posée dans `.env`, par exemple).
    pass_env: Sequence[str] = ()

    def child_env(self) -> dict[str, str]:
        """L'environnement du sous-processus : le socle d'exécution, et rien d'autre.

        Le runner tourne dans le shell de CONSTRUCTION, qui porte les
        identifiants du harnais et de l'opérateur : les recopier dans
        l'application évaluée les exposait à tout ce qu'elle exécute — y compris
        à une injection réussie. L'application lit ses propres secrets dans son
        `.env` ; les noms qu'elle déclare (`secretEnv`) sont transmis s'ils sont
        posés.
        """
        if self.env is not None:
            return dict(self.env)
        wanted = set(self.pass_env) | _declared_secret_names(self.cwd)
        return {k: v for k, v in os.environ.items()
                if k.upper() in _BASE_ENV or k.upper().startswith(_BASE_ENV_PREFIXES) or k in wanted}

    def run(self, item: Mapping[str, Any], *, suite: Mapping[str, Any] | None = None,
            run_index: int = 0, seed: int | None = None, **options: Any) -> dict[str, Any]:
        # L'entrée passe par STDIN, pas par argv : une ligne de commande est
        # bornée (32 767 caractères sous Windows) et visible de tout le poste
        # (`ps`, gestionnaire de tâches) — un item de dataset porte souvent des
        # données personnelles.
        argv = [*self.command, "--input-file", "-"]   # le contrat d'évaluation (`serving/cli.md §3.5`)
        tenant =str(item.get("tenant") or (suite or {}).get("tenant") or "")
        if tenant:
            argv += ["--tenant", tenant]

        started = time.monotonic()
        # En OCTETS : en mode texte, Windows traduisait les `\n` de l'entrée en
        # `\r\n` — l'item évalué n'était plus octet pour octet celui du dataset.
        raw = subprocess.run(  # noqa: S603 - argv en liste, jamais de shell
            argv, input=_item_input(item).encode("utf-8"), capture_output=True,
            cwd=str(self.cwd) if self.cwd else None, timeout=self.timeout_s,
            env=self.child_env(), check=False)
        completed = subprocess.CompletedProcess(
            raw.args, raw.returncode, raw.stdout.decode("utf-8", errors="replace"),
            raw.stderr.decode("utf-8", errors="replace"))
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


#: Le socle qu'un sous-processus Python, `uv` ou Windows exige pour démarrer.
_BASE_ENV = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "HOME", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ", "VIRTUAL_ENV", "SOURCE_DATE_EPOCH",
})
_BASE_ENV_PREFIXES = ("PYTHON", "UV_", "SDDA_WORKSPACE_ROOT", "SDDA_TENANT_ID")


def _declared_secret_names(cwd: Path | None) -> set[str]:
    """Les NOMS de variables que l'application déclare (`secretEnv` de son `app_config.json`)."""
    for base in (cwd, Path(__file__).resolve().parents[1]):
        config = (base / "app_config.json") if base else None
        if config is not None and config.is_file():
            try:
                payload = json.loads(config.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return set()
            return {str(v) for v in (payload.get("secretEnv") or {}).values()}
    return set()


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

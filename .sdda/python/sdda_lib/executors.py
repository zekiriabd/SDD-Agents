"""L'exécuteur du système évalué — une seule fois, pour les trois runners.

Trois formes de `--executor`, par ordre de préférence :

- `cli` — l'application LIVRÉE, lancée par sa ligne de commande, quel que soit
  son langage. La commande vient du langage actif (`## Active Language &
  Runtime`) : `LAUNCH_COMMANDS`. C'est la forme qui rend G4 à G8 mesurables en
  C#, TypeScript, Kotlin et Java — les runners chargeaient un objet Python par
  `importlib`, et hors Python il n'y avait RIEN à charger ;
- `cmd:<commande>` — la même chose, commande imposée (`cmd:dotnet run --project
  workspace/src/Shop --`), quand le lancement par défaut ne convient pas ;
- `module:attr` — un objet Python chargé dans le processus du runner
  (`{App}.evals.executor:InProcessExecutor`), pour l'eval L4 en processus du
  squelette Python.

Le contrat que parle `cli`/`cmd:` est `stacks/serving/cli.md` §3.1-3.3 et
§3.5 : `run --json --input-file -` (entrée sur stdin, NDJSON sur stdout, code
de sortie mesuré), l'isolement L4 par `SDDA_EVAL_ISOLATION` et
`SDDA_EVAL_FIXTURES`, et `retrieve --json --index ID --query-file -` pour G4.

`module:attr` a deux lacunes connues, que cette fonction traite :

- le PAQUET introuvable — `workspace/src/` est ajouté au chemin, toujours : c'est
  là que le framework range l'application, ce n'est pas une option ;
- une DÉPENDANCE introuvable (le SDK du fournisseur, le framework d'agents) —
  l'outillage est stdlib seule et ne l'installera pas. Le message dit de lancer
  le runner avec l'interpréteur de l'application, où `project-init` a fait
  `uv sync` : `uv run --project workspace/src/{App} python .sdda/sdda.py …`.
"""
from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from sdda_lib import paths


class ExecutorLoadError(ValueError):
    """L'exécuteur ne se charge pas ; le message porte déjà la correction."""


#: La commande qui lance l'application générée, par langage. `{AppName}` est
#: remplacé ; les chemins sont relatifs à la racine du projet (le `cwd` du
#: sous-processus). Chaque fiche `lang/*.md` recopie la sienne dans sa section
#: « Contrat d'exécution » — c'est un contrat, pas une suggestion.
LAUNCH_COMMANDS: dict[str, tuple[str, ...]] = {
    "python": ("uv", "run", "--project", "workspace/src/{AppName}", "{AppName}"),
    "csharp": ("dotnet", "run", "--project", "workspace/src/{AppName}", "--"),
    "typescript": ("node", "workspace/src/{AppName}/dist/cli.js"),
    "kotlin": ("java", "-jar", "workspace/src/{AppName}/build/libs/{AppName}.jar"),
    "java": ("java", "-jar", "workspace/src/{AppName}/build/libs/{AppName}.jar"),
}

#: Le socle qu'un sous-processus exige pour démarrer, dans les cinq écosystèmes.
#: Rien d'autre du shell de CONSTRUCTION ne passe : il porte les identifiants du
#: harnais et de l'opérateur, qu'une injection réussie dans l'application
#: évaluée pourrait exfiltrer. L'application lit ses propres secrets dans son
#: `.env` (`install-env`).
BASE_ENV = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "HOME", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "TEMP", "TMP",
    "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "VIRTUAL_ENV", "SOURCE_DATE_EPOCH", "JAVA_HOME",
    "NODE_PATH", "NODE_OPTIONS", "DOTNET_ROOT", "NUGET_PACKAGES", "GRADLE_USER_HOME",
})
BASE_ENV_PREFIXES = ("PYTHON", "UV_", "DOTNET_", "MSBUILD", "NPM_CONFIG_", "SDDA_WORKSPACE_ROOT", "SDDA_TENANT_ID")

ISOLATION_ENV = "SDDA_EVAL_ISOLATION"
FIXTURES_ENV = "SDDA_EVAL_FIXTURES"


def _ensure_src_on_path(root: Path | None) -> None:
    if root is None:
        return
    src = paths.workspace(root) / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def launch_command(root: Path) -> list[str]:
    """La commande de l'application générée, depuis le langage et l'`AppName` actifs."""
    from sdda_lib.layered_config import active_stacks, app_name  # noqa: PLC0415 — évite un cycle d'import

    languages = active_stacks(root, "Active Language & Runtime")
    language = languages[0] if languages else ""
    template = LAUNCH_COMMANDS.get(language)
    if template is None:
        raise ExecutorLoadError(
            f"`--executor cli` : aucun lancement connu pour le langage `{language or '<aucun>'}` — "
            f"langages : {sorted(LAUNCH_COMMANDS)} ; imposer la commande par `--executor cmd:<commande>`")
    app = app_name(root)
    return [part.replace("{AppName}", app) for part in template]


def load_executor(spec: str, *, method: str = "run", root: Path | None = None) -> Any:
    """`cli` | `cmd:<commande>` | `module:attr` -> objet qui expose `method`.

    Pour `module:attr`, l'attribut peut être une fabrique sans argument.
    """
    if spec == "cli" or spec.startswith("cmd:"):
        base = Path(root) if root is not None else paths.find_root()
        if spec == "cli":
            command = launch_command(base)
        else:
            command = shlex.split(spec[4:], posix=os.name != "nt")
            if not command:
                raise ExecutorLoadError("`--executor cmd:` sans commande")
        return CommandExecutor(command=tuple(command), root=base)
    if ":" not in spec:
        raise ExecutorLoadError(f"`{spec}` : attendu `cli`, `cmd:<commande>` ou `module:attr`")
    mod_name, attr = spec.rsplit(":", 1)
    _ensure_src_on_path(root)
    try:
        module = importlib.import_module(mod_name)
    except ModuleNotFoundError as exc:
        app = mod_name.split(".", 1)[0]
        missing = exc.name or ""
        if missing and missing.split(".", 1)[0] != app:
            raise ExecutorLoadError(
                f"`{spec}` : dépendance `{missing}` absente de cet interpréteur — l'outillage est "
                f"stdlib seule ; lancer le runner avec celui de l'application : "
                f"`uv run --project workspace/src/{app} python .sdda/sdda.py …`, ou `--executor cli`") from exc
        raise ExecutorLoadError(
            f"`{spec}` : module `{mod_name}` introuvable, y compris sous workspace/src/") from exc
    obj = getattr(module, attr)
    if isinstance(obj, type) or (callable(obj) and not hasattr(obj, method)):
        obj = obj()
    if not hasattr(obj, method):
        raise ExecutorLoadError(f"`{spec}` ne fournit pas de méthode `{method}`")
    return obj


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


def parse_ndjson(payload: str) -> list[dict[str, Any]]:
    """Les événements d'une sortie NDJSON ; une ligne illisible est ignorée, pas fatale."""
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


@dataclass
class CommandExecutor:
    """L'application générée lancée en sous-processus, par son contrat CLI.

    C'est la définition opératoire de « ce qu'on mesure est ce qu'on livre »,
    et elle ne dépend d'aucun langage : la même commande qu'un humain tape, le
    même code de sortie qu'une CI lit, la même trace qu'un post-mortem ouvrira.
    Le code de sortie est une MESURE : `3` (borne), `4` (refus attendu en L8),
    `5` (budget), `7` (dégradé) sont des résultats que le verdict distingue.
    """

    command: tuple[str, ...]
    root: Path
    timeout_s: float = 300.0
    name: str = "command"
    pass_env: Sequence[str] = ()
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def child_env(self, *, isolated: bool = False) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if k.upper() in BASE_ENV or k.upper().startswith(BASE_ENV_PREFIXES) or k in set(self.pass_env)}
        env.update(self.extra_env)
        if isolated:
            env[ISOLATION_ENV] = "mocked"
            env[FIXTURES_ENV] = str(paths.workspace(self.root) / "pipeline" / "fixtures")
        else:
            env.pop(ISOLATION_ENV, None)
            env.pop(FIXTURES_ENV, None)
        return env

    def _call(self, args: Sequence[str], stdin: str, *, isolated: bool) -> tuple[subprocess.CompletedProcess[str], int]:
        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603 — argv en liste, jamais de shell
            [*self.command, *args], input=stdin, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(self.root), timeout=self.timeout_s,
            env=self.child_env(isolated=isolated), check=False)
        return completed, int((time.monotonic() - started) * 1000)

    def _trace(self, trace_path: Any) -> dict[str, Any]:
        if not trace_path:
            return {"spans": []}
        file = Path(str(trace_path))
        if not file.is_absolute():
            file = self.root / file
        if not file.is_file():
            return {"spans": []}
        spans = parse_ndjson(file.read_text(encoding="utf-8", errors="replace"))
        return {"spans": spans, "trace_path": str(file)}

    def run(self, item: Mapping[str, Any], *, suite: Mapping[str, Any] | None = None,
            run_index: int = 0, seed: int | None = None, **options: Any) -> dict[str, Any]:
        """Un item, un run. `**options` absorbe ce que le runner ajoutera demain."""
        isolation = (suite or {}).get("isolation") or {}
        isolated = bool(options.get("isolated")) or str(isolation.get("tools") or "") == "mocked" \
            or str(isolation.get("retrieval") or "") == "frozen"
        args = ["run", "--json", "--input-file", "-"]
        tenant = str(item.get("tenant") or (suite or {}).get("tenant") or "")
        if tenant:
            args += ["--tenant", tenant]
        completed, elapsed_ms = self._call(args, _item_input(item), isolated=isolated)
        events = parse_ndjson(completed.stdout)
        final = next((e for e in reversed(events) if e.get("event") == "final"), {})
        finished = next((e for e in reversed(events) if e.get("event") == "run_finished"), {})
        error = next((e for e in reversed(events) if e.get("event") == "error"), {})
        return {
            "output": final.get("output"),
            "cost_usd": float(finished.get("cost_usd") or 0.0),
            "latency_ms": int(finished.get("duration_ms") or elapsed_ms),
            "trace": self._trace(finished.get("trace_path")),
            "status": str(finished.get("status") or ("failed" if error or completed.returncode else "ok")),
            "exit_code": completed.returncode,
            "error_class": str(error.get("class") or ""),
            "run_id": str(finished.get("run_id") or ""),
            "isolated": isolated,
            # Un run qui échoue à démarrer n'émet aucun événement : sans stderr,
            # le rapport dirait « sortie vide » là où le message expliquait pourquoi.
            "stderr": completed.stderr[-4000:],
        }

    def retrieve(self, query: str, *, retriever: str | None = None, k: int | None = None,
                 item: Mapping[str, Any] | None = None, **options: Any) -> dict[str, Any]:
        """G4 : `retrieve --json --index ID --query-file -`, sans agent ni modèle."""
        args = ["retrieve", "--json", "--query-file", "-"]
        if retriever:
            args += ["--index", str(retriever)]
        if k:
            args += ["--k", str(int(k))]
        completed, _elapsed = self._call(args, query, isolated=bool(options.get("isolated")))
        events = parse_ndjson(completed.stdout)
        hit = next((e for e in reversed(events) if e.get("event") == "retrieval"), None)
        if hit is None:
            error = next((e for e in reversed(events) if e.get("event") == "error"), {})
            raise RuntimeError(f"`retrieve` n'a émis aucun événement `retrieval` (exit {completed.returncode}"
                               f"{', ' + str(error.get('class')) if error.get('class') else ''}) — "
                               f"{completed.stderr.strip()[-300:]}")
        return {"docIds": [str(d) for d in hit.get("result_ids") or []],
                "scores": list(hit.get("scores") or []), "indexId": hit.get("index_id")}

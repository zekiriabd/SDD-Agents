"""Isolement L4 — outils et retrieval servis depuis des fixtures. GÉNÉRÉ, ne pas éditer.

Le contrat d'évaluation (`stacks/serving/cli.md §3.5`) est le même dans tous
les langages : les runners du framework LANCENT l'application, ils ne
l'importent pas. L'isolement ne peut donc pas passer par un argument Python :
il passe par l'environnement du processus.

    SDDA_EVAL_ISOLATION=mocked     l'isolement est demandé
    SDDA_EVAL_FIXTURES=<dir>       <dir>/tools/{outil}.jsonl      une réponse par ligne
                                   <dir>/retrieval/{index}.jsonl  une requête figée par ligne

Isolé, l'agent ne touche AUCUN outil réel ni aucun index : ses outils sont
remplacés par les réponses enregistrées, son retrieval par des résultats figés.
Un outil de l'agent sans fixture n'est pas « servi vide » : l'application
refuse de démarrer (`[CONFIG_INVALID]`, code 8). Un agent évalué isolé qui
touche le vrai monde n'est pas isolé, et le score qu'il obtient n'est
attribuable à rien.

Avant ce module, l'isolement n'existait que dans l'exécuteur en processus, et
il se perdait en silence : un `build_system` qui ne recevait pas `toolset`
faisait tourner la L4 sur les vrais outils, et le retrieval figé n'était passé
à personne.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import ConfigError, Settings
from .orchestration.base import DictToolset, ToolOutcome

MOCKED = "mocked"

#: Clés qui font d'une ligne de fixture une ligne STRUCTURÉE ; sans elles, la
#: ligne entière est la réponse enregistrée (`serving/cli.md §3.5`).
_STRUCTURED_KEYS = frozenset({"tool", "args", "result", "error"})


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def mocked_toolset(fixtures: Path | Mapping[str, Any] | None,
                   metadata: Mapping[str, Mapping[str, Any]] | None = None) -> DictToolset:
    """Des outils qui répondent depuis des fixtures, sans réseau ni base.

    Les fixtures sont un répertoire de `*.jsonl` (récursif) ou un mapping
    `{outil: résultat}`. Une ligne vaut :

    - `{"tool": "…", "args": {…}, "result": …}` — rendue quand l'appel porte ces
      arguments (chaque argument nommé, égal ; les autres sont libres) ;
    - `{"tool": "…", "args": {…}, "error": {"code": "…", "message": "…"}}` — une
      erreur DÉCLARÉE du contrat, rejouée telle quelle (`ok=False`) ;
    - sans `args` : la réponse par défaut de l'outil (la dernière l'emporte) ;
    - sans `tool` : l'outil est celui que nomme le fichier (`tools/{outil}.jsonl`),
      et une ligne sans aucune de ces clés est la réponse elle-même.

    Un appel qu'aucune ligne ne couvre rend une ERREUR (`TOOL_FIXTURE_MISSING`),
    pas une réponse vide : une réponse vide se confondrait avec « l'outil n'a
    rien trouvé ».
    """
    matched: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    defaults: dict[str, Mapping[str, Any]] = {}

    def add(entry: Mapping[str, Any]) -> None:
        name = str(entry["tool"])
        args = entry.get("args")
        if isinstance(args, Mapping) and args:
            matched.setdefault(name, []).append((args, entry))
        else:
            defaults[name] = entry

    if isinstance(fixtures, Mapping):
        for name, result in fixtures.items():
            add({"tool": name, "result": result})
    elif fixtures is not None:
        directory = Path(fixtures)
        for path in sorted(directory.rglob("*.jsonl")) if directory.is_dir() else ():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("tool"):
                    add(entry)
                elif isinstance(entry, dict) and _STRUCTURED_KEYS & set(entry):
                    add({**entry, "tool": path.stem})
                else:
                    add({"tool": path.stem, "result": entry})

    def outcome(entry: Mapping[str, Any]) -> ToolOutcome:
        error = entry.get("error")
        if isinstance(error, Mapping):
            code = str(error.get("code") or "TOOL_ERROR")
            return ToolOutcome(content=_canon({"error": dict(error)}), ok=False, error_code=code)
        payload = entry.get("result")
        return ToolOutcome(content=payload if isinstance(payload, str) else _canon(payload))

    def handler(name: str) -> Callable[..., ToolOutcome]:
        def _call(**kwargs: Any) -> ToolOutcome:
            for args, entry in matched.get(name, ()):
                if all(k in kwargs and _canon(kwargs[k]) == _canon(v) for k, v in args.items()):
                    return outcome(entry)
            if name in defaults:
                return outcome(defaults[name])
            missing = {"error": {"code": "TOOL_FIXTURE_MISSING", "tool": name, "args": kwargs}}
            return ToolOutcome(content=_canon(missing), ok=False, error_code="TOOL_FIXTURE_MISSING")
        return _call

    names = sorted(set(matched) | set(defaults))
    return DictToolset(tools={name: handler(name) for name in names},
                       metadata={n: dict((metadata or {}).get(n, {})) for n in names})


def _query_hash(query: str) -> str:
    return "sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest()


def frozen_retrieval(fixtures: Mapping[str, Sequence[Mapping[str, Any]]] | None = None
                     ) -> Callable[..., list[Mapping[str, Any]]]:
    """Un retriever figé, indexé par la requête (ou son `query_hash`). Déterministe.

    Figer le retrieval en L4 n'est pas tricher : c'est séparer « l'agent
    raisonne mal » de « l'index a changé ». Les deux se corrigent ailleurs, et
    les confondre fait retoucher un prompt pour un problème d'ingestion.
    `index_id` est accepté et ignoré : une table sert un seul index.
    """
    table = {str(k): list(v) for k, v in (fixtures or {}).items()}

    def retrieve(query: str, index_id: str = "") -> list[Mapping[str, Any]]:
        hit = table.get(query)
        if hit is None:
            hit = table.get(_query_hash(query))
        return list(hit or ())

    return retrieve


def _results_of(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`results: [{id, score}]`, ou `result_ids[]` + `scores[]` (la forme de l'événement)."""
    results = entry.get("results")
    if isinstance(results, list):
        return [dict(r) for r in results if isinstance(r, Mapping)]
    ids = entry.get("result_ids") or []
    scores = entry.get("scores") or []
    return [{"id": str(i), "score": float(scores[n]) if n < len(scores) else 0.0}
            for n, i in enumerate(ids)]


def frozen_retrieval_dir(directory: Path) -> Callable[..., list[Mapping[str, Any]]]:
    """Le retrieval figé de `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl`, par index.

    Une ligne : `{"query": "…"}` ou `{"query_hash": "sha256:…"}`, et ses
    résultats. Une requête sans ligne rend une liste VIDE — c'est un résultat de
    retrieval légitime, que la RETRIEVAL GATE compte comme un rappel nul.
    """
    tables: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for path in sorted(Path(directory).glob("*.jsonl")) if Path(directory).is_dir() else ():
        table = tables.setdefault(path.stem, {})
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                entry = json.loads(line) if line.strip() else None
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("query_hash") or "") or (
                str(entry["query"]) if entry.get("query") is not None else "")
            if key:
                table[key] = _results_of(entry)

    def retrieve(query: str, index_id: str = "") -> list[Mapping[str, Any]]:
        if index_id:
            table = tables.get(index_id)
        else:   # sans index nommé : l'index unique, s'il n'y en a qu'un
            table = next(iter(tables.values())) if len(tables) == 1 else None
        if table is None:
            return []
        hit = table.get(query)
        if hit is None:
            hit = table.get(_query_hash(query))
        return list(hit or ())

    return retrieve


def isolation_kwargs(settings: Settings) -> dict[str, Any]:
    """Ce que l'isolement demandé remplace (`toolset`, `retriever`) — vide s'il n'est pas demandé.

    Refuse (`ConfigError`, code 8) un isolement sans répertoire de fixtures, ou
    un outil de l'agent (`app_config.json` > `tools`, projeté de l'IR) sans sa
    fixture : l'application ne démarre pas plutôt que de toucher le vrai monde.
    """
    if not settings.isolated:
        return {}
    root = settings.eval_fixtures
    if root is None or not root.is_dir():
        raise ConfigError(
            "`SDDA_EVAL_ISOLATION=mocked` sans `SDDA_EVAL_FIXTURES` lisible",
            cls="CONFIG_INVALID",
            fix="poser SDDA_EVAL_FIXTURES sur le répertoire des fixtures (tools/, retrieval/)")
    tools_dir = root / "tools"
    missing = [t for t in settings.tool_names if not (tools_dir / f"{t}.jsonl").is_file()]
    if missing:
        raise ConfigError(
            f"isolement demandé, fixture absente pour {missing}",
            cls="CONFIG_INVALID",
            fix=f"enregistrer une réponse par outil dans {tools_dir}/{{outil}}.jsonl — un outil sans "
                "fixture serait appelé pour de vrai, et l'évaluation ne serait plus isolée")
    kwargs: dict[str, Any] = {"toolset": mocked_toolset(tools_dir, settings.tool_meta)}
    retrieval_dir = root / "retrieval"
    if retrieval_dir.is_dir() and any(retrieval_dir.glob("*.jsonl")):
        kwargs["retriever"] = frozen_retrieval_dir(retrieval_dir)
    return kwargs

#!/usr/bin/env python3
"""État des runs du pipeline : identité, phases, reprise, audit.

Ce que ce script est — et n'est pas. Il **ne calcule aucun état d'artefact** :
l'état d'une MISSION ou d'une CAP est dérivé des rapports de gate sur disque par
`compute_status.py` (LIFECYCLE R1), et il n'existe pas de seconde source. Ici on
tient le journal de ce qui a été TENTÉ : quel run, lancé par quelle commande,
quelles phases ont abouti, et où reprendre.

Séparer les deux est délibéré. Un journal d'exécution qui prétendrait dire si la
MISSION est `Approved` deviendrait une deuxième vérité — et deux vérités sur le
même fait, c'est zéro vérité vérifiable. Le journal dit « la phase `build_orch`
a échoué à 14:02 » ; les gates disent « G6 est rouge ». Les deux ensemble
permettent de reprendre sans rejouer ce qui a marché ; aucune des deux ne
remplace l'autre.

Sur disque, sous `workspace/.sys/.state/` :

    runs/{run_id}.json   un run : mission, commande, tags, phases, verdict
    runs.jsonl           index append-only (une ligne par événement de run)

Sous-commandes (les 9 appelants du pipeline) :

    new-run --mission 1 --command /sdda-build --tags force
    new-run --mission 1 --command /sdda-full --resume      # lié au run repris (`resumedFrom`)
    get-run --mission 1 --latest
    resume-target --run-id RID
    should-skip-step --target build_agents --current caps      # exit 0 = SKIP, 1 = RUN
    set-phase --phase caps --status pass --payload-json '{"capCount":4}'
    end-run --run-id RID --status partial
    status [--mission 1] --json

Reprise à la granularité de l'AGENT (items de phase) :

    inputs-hash      --mission 1 --phase build_agents --item billing            # sha256:… sur stdout
    set-item         --phase build_agents --item billing --status pass --inputs-hash sha256:…
    should-skip-item --phase build_agents --item billing --inputs-hash sha256:…   # exit 0 = SKIP
    done-items       --phase build_agents                                          # items `pass`, un par ligne

Une phase `build_agents` regroupe N instances de `dev-agent`. Sans items, une
reprise rejouait la phase entière : trois agents verts repayés parce que le
quatrième avait échoué. Un item porte le hash de ses ENTRÉES (prompt, entrée IR
de l'agent) : un item `pass` dont les entrées ont bougé n'est pas sauté — un
succès sur d'autres entrées n'est pas un succès.

`inputs-hash` calcule ce hash depuis l'IR compilé, pour que la commande n'ait
pas à le composer à la main (elle se tromperait de champ, et le saut deviendrait
un pari) :

    build_agents/{agent}   entrée `agents[]` de l'IR + texte du prompt (`promptRef`)
    build_socle/tools      `tools[]`        build_socle/retrieval  `retrievers[]`
    build_socle/data       `dataAccess[]`
    eval/{suite}           entrée `evaluation.suites[]`

Toujours le hash canonique (`sdda_lib.hashing.sha256_struct`) : l'ordre des clés
de l'IR ne compte pas, son contenu compte.

Les sous-commandes qui rendent UNE valeur (`new-run`, `get-run`,
`resume-target`) l'écrivent seule sur stdout : elles sont consommées par
`RUN_ID=$(…)`. Toute diagnostic part sur stderr, jamais dans la valeur.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, paths, tracing  # noqa: E402
from sdda_lib.errors import Report, emit  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso, run_id_now, slash_command  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, load_config, resolve_root  # noqa: E402

#: Les phases canoniques du pipeline, DANS L'ORDRE. C'est cette liste que
#: `/sdda-full --resume` parcourt ; elle est la seule définition de « avant » et
#: « après » dans le framework.
PIPELINE_PHASES: tuple[str, ...] = (
    "mission", "caps", "topology", "eval_datasets",
    "build_socle", "build_agents", "build_orch",
    "eval", "review", "acceptance",
)

#: Sous-phases enregistrées par les commandes, rattachées à leur phase canonique.
#: Elles existent pour l'audit (savoir que l'étage B de la revue est passé et le
#: C non), pas pour la reprise : reprendre au milieu d'une revue en trois étages
#: donnerait un verdict composite dont personne ne saurait dire de quoi il parle.
SUB_PHASES: dict[str, str] = {
    "contracts": "topology",
    "topology_gate": "topology",
    "review_a": "review",
    "review_b": "review",
    "review_c": "review",
}

PHASE_STATUSES: tuple[str, ...] = ("pass", "warn", "fail")

#: `running` est l'état d'un run non terminé — un run tué sans `end-run` le
#: reste, et c'est une information : il n'est pas allé au bout.
RUN_STATUSES: tuple[str, ...] = ("running", "pass", "partial", "fail", "aborted")

RESUME_DONE = "done"

#: Identifiant du span racine de la trace de construction. Fixe et partagé avec
#: `build_trace.py` : un fichier de trace vaut pour UN run, donc la racine n'a
#: pas besoin d'être transmise entre deux processus qui ne se connaissent pas.
TRACE_ROOT_SPAN_ID = "root"


# ---------------------------------------------------------------------------
# Chemins et écriture
# ---------------------------------------------------------------------------
def runs_dir(root: Path) -> Path:
    return paths.state_dir(root) / "runs"


def run_path(root: Path, run_id: str) -> Path:
    return runs_dir(root) / f"{run_id}.json"


def journal_path(root: Path) -> Path:
    return paths.state_dir(root) / "runs.jsonl"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _append_journal(root: Path, entry: dict[str, Any]) -> None:
    """Journal append-only : il survit à une corruption d'un fichier de run.

    Un index reconstruit par scan de dossier perdrait l'ordre réel des runs
    lancés la même seconde ; un journal le conserve par construction.
    """
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def load_run(root: Path, run_id: str) -> dict[str, Any] | None:
    path = run_path(root, run_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def all_runs(root: Path) -> list[dict[str, Any]]:
    """Tous les runs lisibles, du plus ancien au plus récent (ordre du journal)."""
    seen: list[str] = []
    for line in (journal_path(root).read_text(encoding="utf-8-sig").splitlines()
                 if journal_path(root).is_file() else []):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        rid = str(entry.get("runId") or "")
        if rid and rid not in seen:
            seen.append(rid)
    # Un run dont le fichier existe sans ligne de journal reste visible : mieux
    # vaut un run orphelin affiché qu'un run réel invisible.
    for path in sorted(runs_dir(root).glob("*.json")) if runs_dir(root).is_dir() else []:
        if path.stem not in seen:
            seen.append(path.stem)
    return [r for r in (load_run(root, rid) for rid in seen) if r]


def latest_run(root: Path, mission: str | None = None, *, exclude: str | None = None) -> dict[str, Any] | None:
    runs = [r for r in all_runs(root)
            if (mission is None or str(r.get("mission")) == str(mission)) and str(r.get("runId")) != str(exclude)]
    return runs[-1] if runs else None


# ---------------------------------------------------------------------------
# Lignée — ce qu'une reprise hérite du run qu'elle reprend
# ---------------------------------------------------------------------------
def lineage(root: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    """Le run et ceux qu'il reprend, du plus ancien au plus récent (`resumedFrom`).

    `/sdda-full --resume` ouvrait un run NEUF, puis lisait `get-run --latest`
    — c'est-à-dire le run vide qu'il venait de créer. `resume-target` rendait
    donc `mission`, et la reprise repartait de zéro ; les items `pass` du run
    interrompu étaient invisibles (`should-skip-item` ne voyait rien), et le
    compteur `BuildLoopMaxIter` repartait à 0 à chaque reprise : une boucle
    qu'on relance par `--resume` n'était plus bornée du tout.

    Un run de reprise porte désormais `resumedFrom`, et ce qu'on en lit pour
    décider (phases, items, coûts de boucle) est la concaténation de sa
    lignée. Un cycle — un fichier édité à la main — est coupé au premier
    identifiant déjà vu : mieux vaut une lignée courte qu'une boucle infinie.
    """
    chain: list[dict[str, Any]] = [run]
    seen = {str(run.get("runId"))}
    parent = run.get("resumedFrom")
    while parent and str(parent) not in seen:
        seen.add(str(parent))
        previous = load_run(root, str(parent))
        if previous is None:
            break
        chain.append(previous)
        parent = previous.get("resumedFrom")
    return list(reversed(chain))


def merged_run(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    """Vue de décision d'un run : ses événements précédés de ceux de sa lignée.

    L'ordre chronologique est conservé (le plus ancien d'abord), donc les
    règles « le dernier événement gagne » de `effective_statuses` et
    `item_statuses` s'appliquent telles quelles à travers les reprises. Le
    cumul `costUsd` n'est PAS fusionné : `MaxCostPerRun` plafonne le run
    courant, et la facture de chaque run reste la sienne.
    """
    chain = lineage(root, run)
    if len(chain) == 1:
        return run
    merged = dict(run)
    for key in ("phases", "items", "costs"):
        merged[key] = [e for r in chain for e in (r.get(key) or [])]
    merged["lineage"] = [str(r.get("runId")) for r in chain]
    return merged


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
def canonical_phase(phase: str) -> str | None:
    """Phase canonique d'une phase ou sous-phase ; None si le nom est inconnu."""
    if phase in PIPELINE_PHASES:
        return phase
    return SUB_PHASES.get(phase)


def phase_index(phase: str) -> int:
    canonical = canonical_phase(phase)
    return PIPELINE_PHASES.index(canonical) if canonical else -1


def effective_statuses(run: dict[str, Any]) -> dict[str, str]:
    """Dernier statut connu par phase canonique, le pire d'une phase à étages.

    Deux règles, et l'ordre compte :

    - **par (sous-)phase nommée, le DERNIER événement gagne.** Une phase rejouée
      dans le même run — `mission: fail` puis, la MISSION corrigée, `mission:
      pass` — est une phase passée. Garder le pire des deux rendait toute
      reprise dans le run impossible : `resume-target` renvoyait pour toujours
      la phase qu'on venait de réussir.
    - **entre les étages d'une même phase canonique, le PIRE gagne.** Une revue
      dont l'étage C a échoué n'est pas une revue passée, même si son dernier
      événement enregistré est l'agrégat.
    """
    rank = {"pass": 0, "warn": 1, "fail": 2}
    latest_by_name: dict[str, tuple[str, str]] = {}
    for event in run.get("phases") or []:
        name = str(event.get("phase") or "")
        canonical = canonical_phase(name)
        status = str(event.get("status") or "")
        if not canonical or status not in rank:
            continue
        latest_by_name[name] = (canonical, status)
    out: dict[str, str] = {}
    for canonical, status in latest_by_name.values():
        current = out.get(canonical)
        if current is None or rank[status] > rank[current]:
            out[canonical] = status
    return out


def resume_target(run: dict[str, Any]) -> str:
    """La première phase canonique qui n'a pas abouti — sinon `done`.

    Seul `pass` est sauté (c'est la règle écrite de `/sdda-full --resume`). Un
    `warn` est rejoué : un jaune est un résultat qu'on a accepté de livrer, pas
    un travail dont on est sûr qu'il n'a plus rien à dire.
    """
    statuses = effective_statuses(run)
    for phase in PIPELINE_PHASES:
        if statuses.get(phase) != "pass":
            return phase
    return RESUME_DONE


def derive_run_status(run: dict[str, Any]) -> str:
    statuses = effective_statuses(run).values()
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "warn" for s in statuses):
        return "partial"
    return "pass" if statuses else "aborted"


# ---------------------------------------------------------------------------
# Identité d'un run
# ---------------------------------------------------------------------------
def make_run_id(mission: str, *, at: _dt.datetime | None = None, entropy: str | None = None) -> str:
    """`{horodatage}-m{mission}-{4 hex}` : triable, lisible, et sans collision.

    L'horodatage seul collisionne dès que deux commandes démarrent la même
    seconde — ce que `/sdda-full` fait en enchaînant ses sous-commandes.
    """
    stamp = run_id_now(at)
    suffix = entropy or secrets.token_hex(2)
    slug = str(mission or "x").replace("/", "-").replace("\\", "-")
    return f"{stamp}-m{slug}-{suffix}"


def new_run(root: Path, *, mission: str, command: str, tags: list[str], run_id: str | None = None,
            resumed_from: str | None = None) -> dict[str, Any]:
    rid = run_id or make_run_id(mission)
    run = {
        "runId": rid,
        "mission": str(mission),
        "command": command,
        "tags": tags,
        "startedAt": now_iso(),
        "endedAt": None,
        "status": "running",
        "phases": [],
    }
    if resumed_from:
        run["resumedFrom"] = str(resumed_from)
    _write_json(run_path(root, rid), run)
    _append_journal(root, {"event": "new-run", "at": run["startedAt"], "runId": rid, "mission": str(mission),
                           "command": command, "tags": tags, **({"resumedFrom": str(resumed_from)} if resumed_from else {})})
    return run


def set_phase(root: Path, run_id: str, *, phase: str, status: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    run = load_run(root, run_id)
    if run is None:
        raise KeyError(run_id)
    event = {"phase": phase, "status": status, "at": now_iso(), "payload": payload or {}}
    run.setdefault("phases", []).append(event)
    _write_json(run_path(root, run_id), run)
    _append_journal(root, {"event": "set-phase", "runId": run_id, **event})
    return run


def add_cost(root: Path, run_id: str, *, usd: float, label: str = "",
             phase: str | None = None, item: str | None = None) -> dict[str, Any]:
    """Ajoute une dépense de CONSTRUCTION au cumul du run — et, si nommé, à l'item qui l'a coûtée.

    Sans cette primitive, personne n'écrivait jamais de coût : `MaxCostPerRun`
    était lu par un hook qui trouvait toujours 0,00 $ et autorisait toujours.
    Un plafond qu'on croit actif est plus dangereux qu'un plafond absent —
    on cesse de surveiller la facture.

    Le cumul est porté par le run (`costUsd`), pas par une phase : c'est le run
    qui est plafonné, et une phase relancée ne doit pas effacer ce que les
    précédentes ont coûté.

    `phase` / `item` rattachent AUSSI la dépense à une boucle de correction
    (`costs[]`) : c'est ce que `BuildLoopMaxCostUsd` plafonne. Sans
    rattachement, la dépense compte pour le run, jamais pour une boucle —
    elle n'appartient à aucune.
    """
    run = load_run(root, run_id)
    if run is None:
        raise KeyError(run_id)
    amount = max(0.0, float(usd))
    run["costUsd"] = round(float(run.get("costUsd") or 0.0) + amount, 6)
    if item:
        run.setdefault("costs", []).append({"phase": phase, "item": item, "usd": amount, "label": label, "at": now_iso()})
    _write_json(run_path(root, run_id), run)
    _append_journal(root, {"event": "add-cost", "runId": run_id, "usd": amount,
                           "label": label, "cumulativeUsd": run["costUsd"], "at": now_iso(),
                           **({"phase": phase, "item": item} if item else {})})
    return run


# ---------------------------------------------------------------------------
# Items de phase — la reprise à la granularité de l'agent
# ---------------------------------------------------------------------------
#: Phases dont le travail se découpe en items indépendants (une instance
#: d'agent, une couche du socle). Une phase hors liste n'a pas d'items : la
#: reprise y reste au niveau de la phase, et `set-item` la refuse.
ITEMIZED_PHASES: tuple[str, ...] = ("build_socle", "build_agents", "eval")


def set_item(root: Path, run_id: str, *, phase: str, item: str, status: str,
             inputs_hash: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Enregistre le verdict d'UN item d'une phase (un agent, une couche).

    Le hash d'entrées est ce qui rend le saut légitime : `should_skip_item`
    ne saute un `pass` que si les entrées présentées sont celles qui ont
    produit ce `pass`.
    """
    run = load_run(root, run_id)
    if run is None:
        raise KeyError(run_id)
    if canonical_phase(phase) not in ITEMIZED_PHASES:
        raise ValueError(phase)
    event = {"phase": phase, "item": item, "status": status, "at": now_iso(),
             "inputsHash": inputs_hash, "payload": payload or {}}
    run.setdefault("items", []).append(event)
    _write_json(run_path(root, run_id), run)
    _append_journal(root, {"event": "set-item", "runId": run_id, **event})
    return run


def item_statuses(run: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    """Dernier verdict connu par item d'une phase : {item: {status, inputsHash, at}}."""
    canonical = canonical_phase(phase)
    out: dict[str, dict[str, Any]] = {}
    for event in run.get("items") or []:
        if canonical_phase(str(event.get("phase") or "")) != canonical:
            continue
        item = str(event.get("item") or "")
        if item:
            out[item] = {"status": str(event.get("status") or ""),
                         "inputsHash": event.get("inputsHash"), "at": event.get("at")}
    return out


def item_attempts(run: dict[str, Any], phase: str, item: str, inputs_hash: str | None = None) -> int:
    """Nombre de tentatives déjà enregistrées pour cet item, verdicts compris.

    Avec `inputs_hash`, seules comptent les tentatives faites sur CES entrées :
    c'est la définition de « relancer à l'identique ». Un contrat ou un prompt
    corrigé change le hash, donc ouvre une boucle neuve — ce que le message de
    `BUILD_LOOP_EXHAUSTED` promettait sans que rien ne le fasse.
    """
    canonical = canonical_phase(phase)
    return sum(1 for e in (run.get("items") or [])
               if canonical_phase(str(e.get("phase") or "")) == canonical
               and str(e.get("item") or "") == item
               and (not inputs_hash or str(e.get("inputsHash") or "") == str(inputs_hash)))


def loop_cost(run: dict[str, Any], phase: str, item: str) -> float:
    """Ce que la boucle de correction de CET item a coûté (dépenses rattachées, `costs[]`).

    Une dépense rattachée à l'item sans phase (un span `build-trace` sans
    `--phase`) compte : l'item seul suffit à la désigner, et l'ignorer ferait
    sortir de la borne exactement la dépense qu'on a oublié de qualifier.
    """
    canonical = canonical_phase(phase)
    return round(sum(float(e.get("usd") or 0.0) for e in (run.get("costs") or [])
                     if str(e.get("item") or "") == item
                     and (not e.get("phase") or canonical_phase(str(e.get("phase"))) == canonical)), 6)


def should_retry_item(run: dict[str, Any], phase: str, item: str, *,
                      max_iter: int, max_cost_usd: float, inputs_hash: str | None = None) -> tuple[bool, str, str]:
    """(rejouer ?, classe, raison) — la boucle `build_loop`, appliquée.

    `BuildLoopMaxIter` et `BuildLoopMaxCostUsd` existaient dans `config.base.yml`
    et dans la prose du prompt envoyé à l'agent. Aucun script ne les lisait :
    la borne était donc tenue par le modèle qu'elle est censée borner, ce qui
    n'est pas une borne mais une suggestion. Une boucle de correction qui
    s'emballe ne se voit pas dans les gates — toutes finissent par passer — elle
    se voit sur la facture, après.

    Trois conditions d'arrêt, dans l'ordre de `budget-and-loop.md §6` : succès,
    itérations épuisées, budget épuisé. Le succès est traité par l'appelant
    (`should_skip_item`), les deux autres ici.

    **Le budget est celui de la BOUCLE, pas du run.** `BuildLoopMaxCostUsd`
    était comparé au cumul du run entier : passé $15 de construction — ce qu'un
    `/sdda-full` atteint en PHASE 4 sans qu'aucune boucle ne s'emballe — plus
    aucun item ne pouvait être retenté, même à sa première correction. Et
    inversement, une boucle réellement emballée sur un item restait invisible
    tant que le run n'avait pas dépensé $15 ailleurs. `MaxCostPerRun` plafonne
    le run ; cette borne plafonne ce qu'un item a coûté à force d'être repris,
    reprises `--resume` comprises (le `run` reçu est la vue fusionnée de la
    lignée, `merged_run`).
    """
    attempts = item_attempts(run, phase, item, inputs_hash)
    if attempts >= max_iter:
        return False, "BUILD_LOOP_EXHAUSTED", (
            f"`{item}` : {attempts} tentative(s) sur les mêmes entrées pour un plafond BuildLoopMaxIter={max_iter}. "
            "Relancer à l'identique achète le même échec : corriger la cause (contrat, prompt, "
            "borne) change le hash d'entrées et ouvre une boucle neuve")
    spent = loop_cost(run, phase, item)
    if max_cost_usd > 0 and spent >= max_cost_usd:
        return False, "BUILD_LOOP_BUDGET_EXHAUSTED", (
            f"`{item}` : ${spent:.2f} dépensés par la boucle de correction de cet item "
            f"(reprises comprises) pour un plafond BuildLoopMaxCostUsd=${max_cost_usd:.2f}")
    return True, "", f"{attempts} tentative(s) sur {max_iter} · boucle ${spent:.2f}/{max_cost_usd:.2f}"


def should_skip_item(run: dict[str, Any], phase: str, item: str, inputs_hash: str | None) -> tuple[bool, str]:
    """(sauter ?, raison). Trois conditions, toutes nécessaires :

    - un verdict `pass` est enregistré pour cet item ;
    - si un hash d'entrées est présenté, celui enregistré existe et lui est égal
      (un `pass` sans hash face à un hash présenté n'est pas prouvable : RUN) ;
    - `warn` et `fail` se rejouent toujours — même règle que les phases.
    """
    known = item_statuses(run, phase).get(item)
    if known is None:
        return False, "aucun verdict enregistré"
    if known["status"] != "pass":
        return False, f"dernier verdict `{known['status']}`"
    recorded = known.get("inputsHash")
    if inputs_hash:
        if not recorded:
            return False, "pass enregistré sans hash d'entrées : non prouvable"
        if str(recorded) != str(inputs_hash):
            return False, f"entrées modifiées ({str(recorded)[:19]}… -> {str(inputs_hash)[:19]}…)"
    return True, "pass sur les mêmes entrées"


#: Items FIXES de `build_socle` : une couche du socle, pas un identifiant de l'IR.
#: La clé de l'IR dont la couche dépend est ce qui entre dans le hash.
SOCLE_ITEMS: dict[str, str] = {"tools": "tools", "retrieval": "retrievers", "data": "dataAccess"}

#: Classes rendues par `item_inputs_hash` — nommées ici pour que le registre
#: d'erreurs les voie (il lit les littéraux, pas les variables).
CLS_PHASE_NOT_ITEMIZED = "STATE_PHASE_NOT_ITEMIZED"
CLS_ITEM_UNKNOWN = "STATE_ITEM_UNKNOWN"
CLS_IR_NOT_FOUND = "IR_NOT_FOUND"
CLS_AGENT_NOT_IN_IR = "AGENT_NOT_IN_IR"


def _agent_key(entry: dict[str, Any]) -> str:
    return str(entry.get("id") or "")


def _agent_matches(entry: dict[str, Any], item: str, mission: str) -> bool:
    """`agents[].id` est préfixé du numéro de MISSION (`1-billing`) ; la commande
    passe l'un ou l'autre — `--agent billing` est ce qu'un humain tape."""
    aid = _agent_key(entry)
    return aid == item or aid == f"{mission}-{item}"


def item_inputs_hash(root: Path, mission: str, phase: str, item: str) -> tuple[str | None, str, str]:
    """(hash | None, classe d'erreur, détail). Le hash des ENTRÉES d'un item, depuis l'IR.

    Ce qui est haché est ce que l'agent de construction lit pour produire
    l'item — jamais sa sortie : un hash de la sortie dirait « le code a changé »,
    ce qui est vrai après chaque run et ne permettrait jamais de sauter.
    """
    canonical = canonical_phase(phase)
    if canonical not in ITEMIZED_PHASES:
        return None, CLS_PHASE_NOT_ITEMIZED, f"phase `{phase}` sans items"
    ir_file = paths.ir_path(root, mission)
    if not ir_file.is_file():
        return None, CLS_IR_NOT_FOUND, f"{paths.rel(root, ir_file)} absent — le hash des entrées se lit dans l'IR compilé"
    try:
        ir = json.loads(ir_file.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        return None, CLS_IR_NOT_FOUND, f"{paths.rel(root, ir_file)} illisible : {exc}"

    if canonical == "build_agents":
        entries = [a for a in ir.get("agents") or [] if isinstance(a, dict) and _agent_matches(a, item, mission)]
        if not entries:
            known = [_agent_key(a) for a in ir.get("agents") or [] if isinstance(a, dict)]
            return None, CLS_AGENT_NOT_IN_IR, f"`{item}` absent de agents[] ({', '.join(known) or 'aucun agent'})"
        entry = entries[0]
        prompt_ref = str(entry.get("promptRef") or "")
        prompt_file = paths.resolve_rel(root, prompt_ref) if prompt_ref else None
        prompt_hash = hashing.sha256_file(prompt_file) if prompt_file and prompt_file.is_file() else None
        return hashing.sha256_struct({"agent": entry, "prompt": prompt_hash}), "", ""

    if canonical == "build_socle":
        key = SOCLE_ITEMS.get(item)
        if key is None:
            return None, CLS_ITEM_UNKNOWN, f"item `{item}` inconnu pour build_socle (attendu : {', '.join(SOCLE_ITEMS)})"
        return hashing.sha256_struct({key: ir.get(key) or []}), "", ""

    suites = [s for s in (ir.get("evaluation") or {}).get("suites") or [] if isinstance(s, dict) and str(s.get("id") or "") == item]
    if not suites:
        return None, CLS_ITEM_UNKNOWN, f"suite `{item}` absente de evaluation.suites[]"
    return hashing.sha256_struct({"suite": suites[0]}), "", ""


def items_summary(run: dict[str, Any]) -> dict[str, dict[str, str]]:
    """{phase: {item: status}} — ce que le récap et `/sdda-status` affichent."""
    out: dict[str, dict[str, str]] = {}
    for phase in ITEMIZED_PHASES:
        statuses = item_statuses(run, phase)
        if statuses:
            out[phase] = {item: v["status"] for item, v in sorted(statuses.items())}
    return out


def close_trace(root: Path, run: dict[str, Any]) -> Path | None:
    """Écrit le span racine `sdda.run` de la trace de construction.

    C'est ici et nulle part ailleurs, parce que le journal est le seul à
    connaître le début, la fin et le cumul du run. Les spans enfants
    (`sdda.build.agent`, `sdda.gate`) sont écrits au fil de l'eau par
    `build_trace.py` et référencent cette racine avant qu'elle existe : l'ordre
    d'écriture n'a pas de sens dans un exportateur de spans, `summarize` lit le
    fichier entier.

    Sans cette racine, une trace de construction serait refusée par
    `postflight_trace_present` — et ARCHITECTURE §8 promet une trace pour tout
    run, de construction comme d'exécution.
    """
    run_id = str(run.get("runId") or "")
    if not run_id:
        return None
    writer = tracing.TraceWriter(root, run_id)
    writer.emit(
        f"{tracing.RUN_SPAN} {run.get('mission') or '?'}",
        span_id=TRACE_ROOT_SPAN_ID,
        start=str(run.get("startedAt") or ""), end=str(run.get("endedAt") or ""),
        status="ERROR" if run.get("status") == "fail" else "OK",
        attributes={
            "sdda.run.id": run_id,
            "sdda.mission.id": str(run.get("mission") or ""),
            "sdda.command": str(run.get("command") or ""),
            "sdda.run.status": str(run.get("status") or ""),
            tracing.A_COST_DECLARED: float(run.get("costUsd") or 0.0),
        },
    )
    return writer.path


def end_run(root: Path, run_id: str, *, status: str | None = None) -> dict[str, Any]:
    run = load_run(root, run_id)
    if run is None:
        raise KeyError(run_id)
    run["status"] = status or derive_run_status(run)
    run["endedAt"] = now_iso()
    _write_json(run_path(root, run_id), run)
    _append_journal(root, {"event": "end-run", "at": run["endedAt"], "runId": run_id, "status": run["status"]})
    close_trace(root, run)
    return run


# ---------------------------------------------------------------------------
# Bypasses (R5) — un contournement se rattache au run qui l'a posé
# ---------------------------------------------------------------------------
def bypasses_of(root: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    path = paths.audit_dir(root) / "bypasses.jsonl"
    if not path.is_file():
        return []
    start = str(run.get("startedAt") or "")
    end = str(run.get("endedAt") or "9999-12-31T23:59:59Z")
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        at = str(entry.get("at") or "")
        # Les horodatages sont en ISO 8601 UTC : la comparaison de chaînes suit
        # l'ordre chronologique, sans dépendance à un parseur de dates.
        if start <= at <= end:
            out.append(entry)
    return out


def run_summary(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    own = run
    run = merged_run(root, run)       # ce qu'une reprise hérite : phases, items
    statuses = effective_statuses(run)
    bypasses = bypasses_of(root, own)
    return {
        "resumedFrom": own.get("resumedFrom"),
        "lineage": run.get("lineage") or [own.get("runId")],
        "runId": run.get("runId"),
        "mission": run.get("mission"),
        "command": run.get("command"),
        "tags": run.get("tags") or [],
        "startedAt": run.get("startedAt"),
        "endedAt": run.get("endedAt"),
        "status": run.get("status"),
        "phases": {p: statuses[p] for p in PIPELINE_PHASES if p in statuses},
        "subPhases": {
            str(e.get("phase")): str(e.get("status"))
            for e in run.get("phases") or [] if str(e.get("phase")) in SUB_PHASES
        },
        "resumeTarget": resume_target(run),
        "items": items_summary(run),
        "bypasses": bypasses,
        "bypassCount": len(bypasses),
    }


def render_run_line(summary: dict[str, Any]) -> str:
    glyph = {"pass": "✅", "partial": "🟡", "fail": "🔴", "running": "…", "aborted": "⊘"}.get(str(summary.get("status")), "?")
    phases = " ".join(f"{p}:{s}" for p, s in (summary.get("phases") or {}).items()) or "aucune phase enregistrée"
    bypass = f" · bypasses : {summary['bypassCount']}" if summary.get("bypassCount") else ""
    items = ""
    for phase, statuses in (summary.get("items") or {}).items():
        done = sum(1 for s in statuses.values() if s == "pass")
        items += f" · {phase} items {done}/{len(statuses)} pass"
    return (f"{glyph} {summary['runId']} MISSION {summary['mission']} {summary['command']} "
            f"({summary['status']}) — {phases} · reprise : {summary['resumeTarget']}{items}{bypass}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Journal des runs du pipeline : identité, phases, reprise, audit (0 token)")
    sub = p.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new-run", help="ouvrir un run et écrire son identifiant sur stdout")
    new.add_argument("--mission", required=True)
    new.add_argument("--command", required=True, help="la commande qui ouvre le run, ex. /sdda-build")
    new.add_argument("--tags", default="", help="tags séparés par des virgules (force, resume, from-phase=…)")
    new.add_argument("--run-id", default=None, help="identifiant imposé (reprise d'un run externe, tests)")
    new.add_argument("--resume", action="store_true",
                     help="reprendre le dernier run de la MISSION : lu AVANT de créer le nouveau, lié par `resumedFrom`")
    new.add_argument("--resume-from", default=None, help="reprendre ce run précis (lié par `resumedFrom`)")
    add_common_args(new)

    get = sub.add_parser("get-run", help="écrire l'identifiant du dernier run sur stdout")
    get.add_argument("--mission", default=None)
    get.add_argument("--latest", action="store_true", help="accepté pour la lisibilité des commandes ; c'est le seul mode")
    add_common_args(get)

    res = sub.add_parser("resume-target", help="écrire la phase où reprendre sur stdout")
    res.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID, sinon le dernier run")
    res.add_argument("--mission", default=None)
    add_common_args(res)

    skip = sub.add_parser("should-skip-step", help="exit 0 = SKIP, exit 1 = RUN (un nom inconnu fait RUN)")
    skip.add_argument("--target", required=True)
    skip.add_argument("--current", required=True)
    add_common_args(skip)

    setp = sub.add_parser("set-phase", help="enregistrer le verdict d'une phase")
    setp.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    setp.add_argument("--phase", required=True)
    setp.add_argument("--status", required=True, choices=list(PHASE_STATUSES))
    setp.add_argument("--payload-json", default=None, help="objet JSON de mesures (compte de CAPs, scores, coût…)")
    add_common_args(setp)

    cost = sub.add_parser("add-cost", help="ajouter une dépense de construction au cumul du run")
    cost.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    cost.add_argument("--usd", required=True, type=float, help="montant en USD (>= 0)")
    cost.add_argument("--label", default="", help="ce qui a été payé, ex. `dev-agent billing`")
    cost.add_argument("--phase", default=None, help="phase de la boucle de correction à laquelle la dépense appartient")
    cost.add_argument("--item", default=None, help="item de cette boucle (BuildLoopMaxCostUsd le plafonne)")
    add_common_args(cost)

    end = sub.add_parser("end-run", help="clore un run")
    end.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    end.add_argument("--status", default=None, choices=[s for s in RUN_STATUSES if s != "running"], help="défaut : dérivé des phases")
    add_common_args(end)

    ih = sub.add_parser("inputs-hash", help="écrire sur stdout le hash des entrées d'un item, lu dans l'IR compilé")
    ih.add_argument("--mission", required=True)
    ih.add_argument("--phase", required=True, help=f"phase à items : {', '.join(ITEMIZED_PHASES)}")
    ih.add_argument("--item", required=True, help="agents[].id (avec ou sans préfixe {n}-), tools|retrieval|data, ou un id de suite")
    add_common_args(ih)

    seti = sub.add_parser("set-item", help="enregistrer le verdict d'un item de phase (un agent, une couche)")
    seti.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    seti.add_argument("--phase", required=True, help=f"phase à items : {', '.join(ITEMIZED_PHASES)}")
    seti.add_argument("--item", required=True, help="identifiant de l'item, ex. l'agents[].id de l'IR")
    seti.add_argument("--status", required=True, choices=list(PHASE_STATUSES))
    seti.add_argument("--inputs-hash", default=None, help="hash des entrées ayant produit ce verdict (promptHash, entrée IR…)")
    seti.add_argument("--payload-json", default=None, help="objet JSON de mesures")
    add_common_args(seti)

    skipi = sub.add_parser("should-skip-item", help="exit 0 = SKIP (pass sur les mêmes entrées), exit 1 = RUN")
    skipi.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    skipi.add_argument("--phase", required=True)
    skipi.add_argument("--item", required=True)
    skipi.add_argument("--inputs-hash", default=None, help="hash courant des entrées ; sans lui, un pass sans hash suffit")
    add_common_args(skipi)

    retry = sub.add_parser("should-retry-item",
                           help="exit 0 = une nouvelle tentative est autorisée, exit 1 = build_loop épuisé")
    retry.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    retry.add_argument("--phase", required=True)
    retry.add_argument("--item", required=True)
    retry.add_argument("--max-iter", type=int, default=None, help="défaut : BuildLoopMaxIter du Project Config")
    retry.add_argument("--max-cost-usd", type=float, default=None, help="défaut : BuildLoopMaxCostUsd")
    retry.add_argument("--inputs-hash", default=None,
                       help="hash courant des entrées : seules les tentatives sur CES entrées comptent pour BuildLoopMaxIter")
    add_common_args(retry)

    done = sub.add_parser("done-items", help="écrire les items `pass` d'une phase, un par ligne")
    done.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    done.add_argument("--phase", required=True)
    add_common_args(done)

    st = sub.add_parser("status", help="dernier run et bypasses")
    st.add_argument("--mission", default=None)
    st.add_argument("--all", action="store_true", help="tous les runs, pas seulement le dernier par mission")
    add_common_args(st)
    return p


def _resolve_run_id(args: argparse.Namespace, root: Path) -> str | None:
    explicit = getattr(args, "run_id", None) or os.environ.get("SDDA_RUN_ID") or None
    if explicit:
        return explicit
    run = latest_run(root, getattr(args, "mission", None))
    return str(run["runId"]) if run else None


def _fail(report: Report, args: argparse.Namespace) -> int:
    """Diagnostic sur stderr : stdout est réservé à la valeur rendue."""
    return emit(report, bool(getattr(args, "json", False)), sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="STATE", target=str(root))

    if args.cmd == "should-skip-step":
        # Aucune I/O : c'est une comparaison d'ordre. Un nom inconnu fait RUN —
        # sauter une phase qu'on n'a pas su situer serait le pire des deux.
        ti, ci = phase_index(args.target), phase_index(args.current)
        if args.target == RESUME_DONE:
            return 0
        if ti < 0 or ci < 0:
            report.warn("STATE_PHASE_UNKNOWN", f"phase inconnue (--target `{args.target}`, --current `{args.current}`) : exécution par sécurité",
                        f"phases canoniques : {', '.join(PIPELINE_PHASES)}")
            _fail(report, args)
            return 1
        return 0 if ci < ti else 1

    if args.cmd == "new-run":
        tags = [t.strip() for t in str(args.tags or "").replace(" ", ",").split(",") if t.strip()]
        resumed_from = args.resume_from
        if args.resume and not resumed_from:
            # Lu AVANT la création : après, le « dernier run » serait celui-ci,
            # vide — et la reprise repartirait de la PHASE 0.
            previous = latest_run(root, str(args.mission))
            if previous is None:
                report.error("STATE_RUN_NOT_FOUND", f"--resume : aucun run antérieur pour la MISSION {args.mission}",
                             "lancer sans --resume : il n'y a rien à reprendre", str(paths.state_dir(root)))
                return _fail(report, args)
            resumed_from = str(previous["runId"])
        if resumed_from and load_run(root, str(resumed_from)) is None:
            report.error("STATE_RUN_NOT_FOUND", f"--resume-from `{resumed_from}` introuvable",
                         "passer un runId existant (sdda_state.py status --all)", str(resumed_from))
            return _fail(report, args)
        if resumed_from and "resume" not in tags:
            tags.append("resume")
        run = new_run(root, mission=str(args.mission), command=slash_command(args.command), tags=tags,
                      run_id=args.run_id, resumed_from=resumed_from)
        print(json.dumps(run_summary(root, run), indent=2, ensure_ascii=False, sort_keys=True) if args.json else run["runId"])
        return 0

    if args.cmd == "get-run":
        run = latest_run(root, args.mission)
        if run is None:
            report.error("STATE_RUN_NOT_FOUND", f"aucun run enregistré{f' pour la MISSION {args.mission}' if args.mission else ''}",
                         "ouvrir un run : sdda_state.py new-run --mission {n} --command /sdda-full", str(paths.state_dir(root)))
            return _fail(report, args)
        print(json.dumps(run_summary(root, run), indent=2, ensure_ascii=False, sort_keys=True) if args.json else run["runId"])
        return 0

    if args.cmd == "resume-target":
        rid = _resolve_run_id(args, root)
        run = load_run(root, rid) if rid else None
        if run is None:
            report.error("STATE_RUN_NOT_FOUND", f"run `{rid or '(aucun)'}` introuvable — impossible de dire où reprendre",
                         "vérifier --run-id / $SDDA_RUN_ID, ou lancer le pipeline sans --resume", rid or str(paths.state_dir(root)))
            return _fail(report, args)
        target = resume_target(merged_run(root, run))
        print(json.dumps({"runId": run["runId"], "resumeTarget": target, "phases": run_summary(root, run)["phases"]},
                         indent=2, ensure_ascii=False, sort_keys=True) if args.json else target)
        return 0

    if args.cmd == "add-cost":
        rid = _resolve_run_id(args, root)
        try:
            run = add_cost(root, str(rid), usd=args.usd, label=args.label, phase=args.phase, item=args.item)
        except KeyError:
            report.error("STATE_RUN_NOT_FOUND", f"run `{rid}` introuvable",
                         "vérifier --run-id / $SDDA_RUN_ID", str(rid))
            return _fail(report, args)
        print(json.dumps({"runId": run["runId"], "costUsd": run["costUsd"]},
                         indent=2, ensure_ascii=False, sort_keys=True)
              if args.json else f'{run["costUsd"]:.6f}')
        return 0

    if args.cmd == "set-phase":
        rid = _resolve_run_id(args, root)
        if canonical_phase(args.phase) is None:
            report.error("STATE_PHASE_UNKNOWN", f"phase `{args.phase}` inconnue — elle ne serait rattachée à rien",
                         f"phases : {', '.join(PIPELINE_PHASES)} ; sous-phases : {', '.join(sorted(SUB_PHASES))}", args.phase)
            return _fail(report, args)
        payload: dict[str, Any] = {}
        if args.payload_json:
            try:
                parsed = json.loads(args.payload_json)
            except ValueError as exc:
                report.error("INVALID_ARG", f"--payload-json illisible : {exc}", "passer un objet JSON, ex. '{\"capCount\":4}'", args.phase)
                return _fail(report, args)
            if not isinstance(parsed, dict):
                report.error("INVALID_ARG", "--payload-json doit être un objet JSON", "ex. '{\"capCount\":4,\"critical\":1}'", args.phase)
                return _fail(report, args)
            payload = parsed
        try:
            run = set_phase(root, str(rid), phase=args.phase, status=args.status, payload=payload)
        except KeyError:
            report.error("STATE_RUN_NOT_FOUND", f"run `{rid or '(aucun)'}` introuvable : la phase ne serait rattachée à aucun run",
                         "exporter SDDA_RUN_ID depuis `new-run`, ou passer --run-id", rid or "")
            return _fail(report, args)
        summary = run_summary(root, run)
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) if args.json else render_run_line(summary))
        return 0

    if args.cmd == "inputs-hash":
        digest, cls, detail = item_inputs_hash(root, str(args.mission), args.phase, args.item)
        if digest is None:
            fixes = {
                CLS_PHASE_NOT_ITEMIZED: f"phases à items : {', '.join(ITEMIZED_PHASES)}",
                CLS_IR_NOT_FOUND: f"/sdda-topology {args.mission} --recompile-only",
                CLS_AGENT_NOT_IN_IR: f"lister les agents avec /sdda-status {args.mission}, ou relancer /sdda-topology {args.mission} si l'IR est périmé",
                CLS_ITEM_UNKNOWN: "passer un identifiant présent dans l'IR",
            }
            report.error(cls, detail, fixes.get(cls, ""), f"{args.phase}/{args.item}")
            return _fail(report, args)
        if args.json:
            print(json.dumps({"mission": str(args.mission), "phase": args.phase, "item": args.item, "inputsHash": digest},
                             ensure_ascii=False, sort_keys=True))
        else:
            print(digest)
        return 0

    if args.cmd in ("set-item", "should-skip-item", "should-retry-item", "done-items"):
        rid = _resolve_run_id(args, root)
        run = load_run(root, rid) if rid else None
        if run is None:
            report.error("STATE_RUN_NOT_FOUND", f"run `{rid or '(aucun)'}` introuvable : l'item ne serait rattaché à aucun run",
                         "exporter SDDA_RUN_ID depuis `new-run`, ou passer --run-id", rid or "")
            return _fail(report, args)
        if canonical_phase(args.phase) not in ITEMIZED_PHASES:
            report.error("STATE_PHASE_NOT_ITEMIZED", f"phase `{args.phase}` sans items : la reprise y reste au niveau de la phase",
                         f"phases à items : {', '.join(ITEMIZED_PHASES)}", args.phase)
            return _fail(report, args)
        # Les décisions (sauter, retenter, lister) se prennent sur la lignée ;
        # l'écriture (`set-item`) va au run courant seul.
        view = merged_run(root, run)

        if args.cmd == "set-item":
            payload: dict[str, Any] = {}
            if args.payload_json:
                try:
                    parsed = json.loads(args.payload_json)
                except ValueError as exc:
                    report.error("INVALID_ARG", f"--payload-json illisible : {exc}", "passer un objet JSON", args.item)
                    return _fail(report, args)
                payload = parsed if isinstance(parsed, dict) else {"value": parsed}
            run = set_item(root, str(rid), phase=args.phase, item=args.item, status=args.status,
                           inputs_hash=args.inputs_hash, payload=payload)
            summary = run_summary(root, run)
            print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) if args.json else render_run_line(summary))
            return 0

        if args.cmd == "should-retry-item":
            config = load_config(root, report)
            max_iter = args.max_iter if args.max_iter is not None else (config.get_int("BuildLoopMaxIter", 3) if config else 3)
            max_cost = args.max_cost_usd if args.max_cost_usd is not None else (
                config.get_float("BuildLoopMaxCostUsd", 15.0) if config else 15.0)
            allowed, cls, why = should_retry_item(view, args.phase, args.item,
                                                  max_iter=max_iter, max_cost_usd=max_cost,
                                                  inputs_hash=args.inputs_hash)
            if not allowed:
                report.error(cls, why,
                             "corriger la CAUSE avant de relancer — la boucle s'arrête à la première des "
                             "trois conditions : succès, itérations épuisées, budget épuisé "
                             "(budget-and-loop.md §6)", f"{args.phase}/{args.item}")
                return _fail(report, args)
            if args.json:
                print(json.dumps({"runId": run["runId"], "phase": args.phase, "item": args.item,
                                  "retry": True, "reason": why}, ensure_ascii=False, sort_keys=True))
            else:
                sys.stderr.write(f"[state] {args.phase}/{args.item} : RETRY autorisé — {why}\n")
            return 0

        if args.cmd == "should-skip-item":
            skip, why = should_skip_item(view, args.phase, args.item, args.inputs_hash)
            if args.json:
                print(json.dumps({"runId": run["runId"], "phase": args.phase, "item": args.item, "skip": skip, "reason": why},
                                 ensure_ascii=False, sort_keys=True))
            else:
                sys.stderr.write(f"[state] {args.phase}/{args.item} : {'SKIP' if skip else 'RUN'} — {why}\n")
            return 0 if skip else 1

        statuses = item_statuses(view, args.phase)
        passed = sorted(item for item, v in statuses.items() if v["status"] == "pass")
        if args.json:
            print(json.dumps({"runId": run["runId"], "phase": args.phase, "done": passed,
                              "items": {k: v["status"] for k, v in sorted(statuses.items())}}, ensure_ascii=False, sort_keys=True))
        else:
            for item in passed:
                print(item)
        return 0

    if args.cmd == "end-run":
        rid = _resolve_run_id(args, root)
        try:
            run = end_run(root, str(rid), status=args.status)
        except KeyError:
            report.error("STATE_RUN_NOT_FOUND", f"run `{rid or '(aucun)'}` introuvable", "vérifier --run-id / $SDDA_RUN_ID", rid or "")
            return _fail(report, args)
        summary = run_summary(root, run)
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) if args.json else render_run_line(summary))
        return 0

    # status
    runs = all_runs(root)
    if args.mission:
        runs = [r for r in runs if str(r.get("mission")) == str(args.mission)]
    if not args.all:
        by_mission: dict[str, dict[str, Any]] = {}
        for r in runs:
            by_mission[str(r.get("mission"))] = r  # le journal est ordonné : le dernier écrase
        runs = [by_mission[m] for m in sorted(by_mission)]
    summaries = [run_summary(root, r) for r in runs]
    if args.json:
        print(json.dumps({"runs": summaries, "phasesOrder": list(PIPELINE_PHASES)}, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    if not summaries:
        print("aucun run enregistré")
        return 0
    for summary in summaries:
        print(render_run_line(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

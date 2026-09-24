"""Liaison d'instance — quelle instance de `dev-agent` écrit, et où elle a le droit.

`loader.yml` donne à `dev-agent` la zone `workspace/src/**/agents/{agent}/**`,
et `{agent}` se compilait en « un segment quelconque » : une instance lancée
pour `billing` pouvait écrire `agents/triage/`. Les N instances de la phase 4
tournent EN PARALLÈLE, et c'est précisément là que deux répertoires disjoints
cessent de l'être sans que rien ne le voie — `agent_of` ignorait `agent_id`.

Le mécanisme, en deux temps, avec ce que le harnais fournit réellement :

1. **Au spawn** (`preflight_instance_bind`, PreToolUse sur `Task|Agent`), le
   hook voit le PROMPT de l'instance. `/sdda-build` y écrit une ligne
   `SDDA-INSTANCE: {agent}` ; le hook l'enregistre comme instance DÉCLARÉE
   (`.sys/.state/instances/{agent-dev}/declared/{instance}`). Un agent à
   instances lancé sans cette ligne ne part pas : une instance anonyme n'a pas
   de zone.
2. **À la première écriture**, le payload du sous-agent porte `agent_id`
   (identifiant opaque de l'instance) — mais le spawn ne le connaissait pas
   encore : c'est le harnais qui l'attribue. La liaison se fait donc là :
   l'instance que désigne le chemin écrit doit être DÉCLARÉE et LIBRE ; elle
   est alors réservée à cet `agent_id` par création exclusive (`O_EXCL`) d'un
   fichier de réservation. Toute écriture suivante de cet `agent_id` hors de
   SON instance est `[OWNERSHIP_INSTANCE_ESCAPE]`, toute écriture d'un autre
   `agent_id` dans une instance réservée aussi.

**La limite, dite honnêtement.** La liaison est « premier arrivé » : si deux
instances déclarées (`billing`, `triage`) s'échangent leurs répertoires DÈS leur
première écriture, chacune réserve celui de l'autre et le hook ne le voit pas —
il ne sait pas quel `agent_id` correspond à quel prompt, le harnais ne le dit
pas au spawn. Ce qui reste garanti : une instance n'écrit que dans UN
répertoire d'agent, jamais dans celui d'une autre instance déjà liée, jamais
dans celui d'un agent non déclaré. L'échange est rattrapé après la vague par
`audit-ownership --since-snapshot --instances …`, qui confronte les fichiers
réellement écrits aux instances déclarées, et par le contenu : l'instance
`triage` qui a écrit `agents/billing/` y a implémenté le mauvais contrat, et G5
l'évalue contre le bon.

Sans `agent_id` dans le payload (harnais ancien, invocation manuelle), il n'y a
rien à lier : le motif statique s'applique, et le contrôle après coup reste.

Ce module ÉCRIT, par exception à « un hook lit, il n'écrit pas » : l'état de
liaison, sous `.sys/.state/instances/` seulement, jamais un rapport de gate.
Une réservation n'est pas une mesure — elle ne rend aucune gate verte.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from _hook import ALLOW, deny

CLS_INSTANCE_ESCAPE = "OWNERSHIP_INSTANCE_ESCAPE"
CLS_INSTANCE_UNDECLARED = "OWNERSHIP_INSTANCE_UNDECLARED"

#: La ligne que `/sdda-build` écrit dans le prompt de chaque instance.
INSTANCE_LINE_RE = re.compile(r"^\s*SDDA-INSTANCE\s*:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$", re.M)
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def placeholder_of(loader: dict[str, Any], agent: str) -> str | None:
    """Le placeholder d'instance que `loader.yml` déclare pour cet agent (`agent`), ou None."""
    spec = loader.get(agent)
    if not isinstance(spec, dict):
        return None
    value = spec.get("instance_placeholder")
    return str(value) if value else None


def state_dir(root: Path, agent: str) -> Path:
    from sdda_lib import paths

    return paths.workspace(root) / ".sys" / ".state" / "instances" / agent


def declare(root: Path, agent: str, instance: str) -> None:
    """Instance déclarée au spawn. Une re-déclaration (reprise après échec)
    libère la réservation précédente : l'ancienne instance est terminée."""
    base = state_dir(root, agent)
    (base / "declared").mkdir(parents=True, exist_ok=True)
    marker = base / "declared" / instance
    marker.write_text(json.dumps({"instance": instance, "declaredAt": time.time()}), encoding="utf-8")
    claim = base / "claims" / instance
    if claim.is_file():
        try:
            old = json.loads(claim.read_text(encoding="utf-8")).get("agentId")
        except (OSError, ValueError):
            old = None
        claim.unlink(missing_ok=True)
        if old:
            (base / "bound" / f"{_safe_id(old)}.json").unlink(missing_ok=True)


def _safe_id(agent_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", agent_id)[:128]


def bound_instance(root: Path, agent: str, agent_id: str) -> str | None:
    path = state_dir(root, agent) / "bound" / f"{_safe_id(agent_id)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("instance")
    except (OSError, ValueError):
        return None


def _claim(root: Path, agent: str, instance: str, agent_id: str) -> str | None:
    """Réserve `instance` pour `agent_id`. Rend None si réservée, sinon l'`agent_id` titulaire."""
    base = state_dir(root, agent)
    (base / "claims").mkdir(parents=True, exist_ok=True)
    (base / "bound").mkdir(parents=True, exist_ok=True)
    claim = base / "claims" / instance
    payload = json.dumps({"instance": instance, "agentId": agent_id, "claimedAt": time.time()})
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            holder = json.loads(claim.read_text(encoding="utf-8")).get("agentId")
        except (OSError, ValueError):
            holder = "?"
        return None if holder == agent_id else str(holder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
    (base / "bound" / f"{_safe_id(agent_id)}.json").write_text(payload, encoding="utf-8")
    return None


def instance_in_path(loader: dict[str, Any], agent: str, placeholder: str, rel: str) -> str | None:
    """La valeur du placeholder que `rel` désigne dans les `writes:` de l'agent, ou None."""
    from sdda_scripts import audit_ownership as ao

    token = "{" + placeholder + "}"
    sentinel = "\x02"
    for pattern in ao.writes_of(loader, agent):
        if token not in pattern:
            continue
        # Le motif compilé, le placeholder remplacé par un groupe de capture.
        compiled = ao._to_regex(pattern.replace(token, sentinel), (), False).pattern
        regex = re.compile(compiled.replace(re.escape(sentinel), "([^/]+)"),
                           re.I if ao.CASE_INSENSITIVE else 0)
        m = regex.match(ao.normalize(rel))
        if m:
            return m.group(1)
    return None


def bindings_for_write(root: Path, loader: dict[str, Any], agent: str, rel: str, data: dict,
                       hook: str) -> tuple[dict[str, str] | None, int]:
    """`(bindings, verdict)` pour une écriture de `agent` sur `rel`."""
    placeholder = placeholder_of(loader, agent)
    if not placeholder:
        return None, ALLOW
    agent_id = str(data.get("agent_id") or data.get("agentId") or "").strip()
    if not agent_id:
        return None, ALLOW                       # rien à lier : motif statique + contrôle après coup
    bound = bound_instance(root, agent, agent_id)
    target = instance_in_path(loader, agent, placeholder, rel)
    if bound:
        if target is not None and target != bound:
            return None, deny(hook, CLS_INSTANCE_ESCAPE,
                              f"l'instance `{bound}` de `{agent}` écrit dans celle de `{target}` (`{rel}`)",
                              f"une instance n'écrit que sous SON répertoire (`{{{placeholder}}}` = `{bound}`) : "
                              "ce qui manque chez un autre agent se signale, il ne s'écrit pas à sa place")
        return {placeholder: bound}, ALLOW
    if target is None:
        return None, ALLOW                       # hors zone d'instance : la matrice juge
    if not _SAFE.match(target) or not (state_dir(root, agent) / "declared" / target).is_file():
        return None, deny(hook, CLS_INSTANCE_UNDECLARED,
                          f"`{agent}` écrit `{rel}` pour l'instance `{target}`, qu'aucun spawn n'a déclarée",
                          f"`/sdda-build` lance chaque instance avec la ligne `SDDA-INSTANCE: {{agent}}` dans son "
                          "prompt ; une instance non déclarée n'a pas de répertoire")
    holder = _claim(root, agent, target, agent_id)
    if holder is not None:
        return None, deny(hook, CLS_INSTANCE_ESCAPE,
                          f"`{rel}` : l'instance `{target}` est déjà liée à un autre `{agent}` ({holder})",
                          "deux instances n'écrivent jamais le même répertoire d'agent : c'est la condition "
                          "du parallélisme de la phase 4")
    return {placeholder: target}, ALLOW

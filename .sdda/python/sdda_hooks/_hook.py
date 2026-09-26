"""Plomberie des hooks — protocole d'exécution, lecture de gate, sortie.

Un **hook** n'est pas un validateur. Le validateur répond « est-ce correct ? » et
écrit un rapport ; le hook répond « ai-je le droit de faire CETTE action, MAINTENANT ? »
et il répond assez vite pour s'intercaler entre deux frappes du harnais.

Trois différences qui gouvernent tout ce module :

1. **Le code de sortie est un verdict d'autorisation.** `0` autorise, `2` refuse.
   Claude Code interprète `2` sur un `PreToolUse` comme un refus de l'action, et
   rend `stderr` au modèle. C'est le seul canal par lequel un invariant peut
   arrêter un agent **avant** qu'il écrive, et non après.

2. **Un hook qui plante doit AUTORISER — en session interactive.** Un bug de
   hook qui bloque chaque `Write` paralyse le pipeline entier, et la réaction
   humaine sera de désactiver les hooks — donc de perdre tous les invariants,
   pas seulement le fautif. Un hook qui plante émet un avertissement sur
   `stderr` et laisse passer. Le contrôle correspondant reste joué en CI par
   son validateur, plus tard mais sûrement. En mode strict
   (`SDDA_HOOKS_STRICT=1`, la CI), la panne REFUSE : c'est là qu'une panne qui
   autorise se confond avec un jugement qui autorise. Et un interpréteur absent
   du PATH — le hook ne démarre même pas, code ≠ 2, donc autorisé par le
   harnais — est rattrapé par la commande générée (`harness_build`) et par
   `hooks-selfcheck`, qui exécute chaque hook câblé.

3. **Un hook lit, il n'écrit pas.** Aucun rapport de gate, aucun état. Il
   consulte ce que les validateurs ont déjà écrit. Deux écrivains sur
   `.sys/.validation/` produiraient exactement la course que la matrice
   d'ownership existe pour empêcher.

Le payload du harnais arrive sur `stdin` en JSON (`tool_name`, `tool_input`,
`subagent_type`…). Il peut être absent : un hook doit rester exécutable à la main.

**Chaque hook déclare son câblage** dans un `WIRING` de module — l'événement, le
matcher, et les agents sur lesquels il se prononce. `harness_build.py` génère
`settings.json` depuis ces déclarations : un hook présent sur le disque mais sans
`WIRING` n'est câblé nulle part, et `framework_smoke` le signale. C'est ce qui
empêche qu'un enforcer déclaré dans `INVARIANTS.yml` existe sans jamais
s'exécuter — la forme la plus coûteuse de doc-theater, parce qu'elle est
indiscernable d'une protection active.

`applies_to` n'est pas une commodité : un hook de gate câblé sur **tout** `Task`
refuse le premier agent du pipeline, quand aucune gate ne peut encore être verte.
Un hook qui paralyse se fait désactiver, et on perd alors tous les invariants —
pas seulement le fautif.

Le matcher de délégation s'écrit `Task|Agent`, et les deux noms comptent :
l'outil de sous-agent s'est appelé `Task` dans Claude Code et s'appelle `Agent`
dans l'Agent SDK. Un matcher qui ne nomme que l'un des deux ne se plaint pas —
il ne se déclenche simplement jamais, et les neuf hooks de gate deviennent
décoratifs sur le harnais qui emploie l'autre nom. Un enforcer muet coûte plus
cher qu'un enforcer absent : `INVARIANTS.yml` continue de le déclarer câblé.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.gate_reports import load_gate_reports  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

# `stderr` est le canal par lequel un refus atteint le modèle. Sous Windows il
# est en `cp1252` par défaut : « borne dépassée » y devient « borne d�pass�e »,
# et le `FIX:` que le modèle doit lire arrive abîmé — au moment précis où il
# doit comprendre quoi corriger. Même piège que `serving/cli.md §7.5`.
ensure_utf8_stdout()

#: Codes de sortie. `2` est la valeur que Claude Code interprète comme un refus.
ALLOW = 0
DENY = 2

# ---------------------------------------------------------------------------
# Groupes d'agents — nommés ici, une seule fois
# ---------------------------------------------------------------------------
# Les `applies_to` des hooks s'y réfèrent plutôt que de recopier des listes :
# une renommée d'agent se répercute alors en un point, et un `applies_to` se lit
# comme une phrase au lieu d'un tuple de chaînes.

#: Phase 2 — ceux qui architecturent sur les CAPs. Rien ne doit partir chez eux
#: si G1 n'est pas verte : ils bâtiraient sur des critères non mesurables.
PHASE2_ARCHITECTS = (
    "architect-topology", "architect-rag", "architect-data",
    "architect-memory", "architect-tools",
)

#: Phases 4-5 — ceux qui câblent les agents et l'orchestration. Ils s'appuient
#: sur les couches basses (outils, retrieval) : elles doivent être prouvées avant.
AGENT_BUILDERS = ("dev-agent", "dev-orchestration")

#: Ceux dont le code touche des données — base, sources déclarées ou corpus.
DATA_BUILDERS = ("dev-data", "dev-retrieval", "dev-agent")

#: Ceux qui produisent ou consomment des mesures d'évaluation.
EVAL_BUILDERS = ("qa-evals", "qa-tests", "dev-orchestration")


#: Options acceptées en ligne de commande, et la clé de payload qu'elles posent.
#: Les commandes invoquent les hooks à la main (`--mission {n}`) ; sans cette
#: table, l'option était lue par personne et le hook se prononçait sur TOUTES les
#: missions au lieu de celle qu'on lui nommait — un faux vert silencieux.
ARGV_KEYS = {
    "--mission": "mission",
    "--agent": "subagent_type",
    "--run-id": "runId",
    "--root": "cwd",
}


#: Les drapeaux d'aide. Un hook lancé par le lanceur (`sdda preflight-cost-cap
#: --help`) EXÉCUTAIT le hook et rendait son verdict — un usage qui juge n'est
#: pas un usage.
HELP_FLAGS = ("-h", "--help")

#: Une option inconnue. `--misson 1` était ignoré sans un mot : le hook se
#: prononçait sur TOUTES les missions au lieu de celle qu'on croyait lui
#: nommer — un faux vert qui ne ressemble à rien.
CLS_ARG_UNKNOWN = "HOOK_ARG_UNKNOWN"


def parse_argv(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """(clés de payload, options inconnues) depuis la ligne de commande.

    Une option connue consomme sa valeur (`--mission 1` ou `--mission=1`) ;
    une option inconnue est rendue à l'appelant, qui décide (cf. `run`) ;
    un positionnel isolé est ignoré — il n'a pas de sens pour un hook.
    """
    out: dict[str, Any] = {}
    unknown: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        key, _, inline = token.partition("=")
        target = ARGV_KEYS.get(key)
        if target is None:
            if token.startswith("-"):
                unknown.append(key)
            i += 1
            continue
        if inline:
            out[target] = inline
            i += 1
        elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            out[target] = argv[i + 1]
            i += 2
        else:
            i += 1
    return out, unknown


def argv_data(argv: list[str] | None = None) -> dict[str, Any]:
    """Les options de la ligne de commande, en clés de payload (options inconnues écartées)."""
    return parse_argv(list(argv if argv is not None else sys.argv[1:]))[0]


def invoked_argv() -> list[str] | None:
    """Les arguments de la ligne de commande QUAND le processus est le hook lui-même.

    Lancé par le harnais (`python …/sdda_hooks/preflight_x.py`) ou par le
    lanceur (`sys.argv[0] == "sdda preflight-x"`), `sys.argv` est au hook. Importé
    dans un autre processus — la suite de tests, un validateur — il est à ce
    processus, et `-q` ou `--tb=short` n'y sont pas des options inconnues du
    hook : on ne juge alors ni l'aide ni les options, seulement le payload.
    """
    prog = str(sys.argv[0] if sys.argv else "")
    if prog.startswith("sdda ") or Path(prog).name.startswith(("preflight_", "postflight_")):
        return list(sys.argv[1:])
    return None


def usage(hook: str) -> str:
    options = "\n".join(f"  {flag:<12} -> payload `{key}`" for flag, key in ARGV_KEYS.items())
    return (f"usage: {hook} [--mission N] [--agent NOM] [--run-id ID] [--root DIR]  < payload.json\n\n"
            f"Hook PreToolUse/SubagentStop : lit le payload JSON du harnais sur stdin, complété par les options.\n"
            f"Exit 0 = autorise, 2 = refuse (bloc ERROR/CAUSE/FIX sur stderr).\n\n{options}\n")


def payload(argv: list[str] | None = None) -> dict[str, Any]:
    """Le JSON du harnais sur stdin, enrichi des options de la ligne de commande.

    Jamais bloquant : un hook doit rester lançable à la main pour être debogable,
    et un payload illisible n'est pas une raison de refuser une action. La ligne
    de commande l'emporte sur stdin — c'est l'opérateur qui restreint le scope,
    et restreindre ne peut pas être moins sûr qu'élargir.
    """
    data: dict[str, Any] = {}
    if sys.stdin is not None and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
    data.update(argv_data(argv))
    return data


def root_of(data: dict[str, Any]) -> Path:
    """La racine du projet, REMONTÉE depuis le `cwd` du harnais, ou la variable d'env.

    Le `cwd` n'est pas la racine : c'est le répertoire courant de la session, et
    il suffit d'un `cd workspace/src/{App}` pour lancer les tests du projet
    généré. Pris tel quel, il décalait toute la matrice d'ownership — un Edit de
    `workspace/src/App/agents/x/agent.py` devenait `agents/x/agent.py`, hors de
    la zone `workspace/src/**/agents/{agent}/**`, et le hook refusait à
    `dev-agent` d'écrire dans son propre répertoire.
    """
    for candidate in (data.get("cwd"), os.environ.get("SDDA_ROOT")):
        if candidate and Path(str(candidate)).is_dir():
            return paths.find_root(Path(str(candidate))).resolve()
    try:
        return paths.find_root().resolve()
    except Exception:
        return Path.cwd().resolve()


def deny(hook: str, cls: str, detail: str, fix: str) -> int:
    """Refuse l'action, en disant au modèle ce qui la débloque.

    Le bloc 3 lignes n'est pas décoratif : c'est ce que le modèle reçoit, et
    « refusé » sans `FIX:` produit un agent qui réessaie la même action.
    """
    sys.stderr.write(f"ERROR: hook {hook} — action refusée\nCAUSE: [{cls}] {detail}\nFIX: {fix}\n")
    return DENY


def allow(note: str = "") -> int:
    if note:
        sys.stderr.write(f"[hook] {note}\n")
    return ALLOW


#: Mode strict : un hook qui plante REFUSE. Activé par la CI et par qui veut
#: qu'aucune panne ne passe pour un feu vert.
STRICT_ENV = "SDDA_HOOKS_STRICT"


def strict() -> bool:
    return os.environ.get(STRICT_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def degrade(hook: str, exc: BaseException) -> int:
    """Un hook cassé autorise — et le dit. Cf. §2 du module.

    SAUF en mode strict (`SDDA_HOOKS_STRICT=1`). Le §2 arbitre entre deux
    risques pour une session INTERACTIVE : un hook cassé qui bloque tout se fait
    désactiver. En CI, ou pour un opérateur qui l'a demandé, l'arbitrage
    s'inverse : un hook qui plante et autorise est indiscernable d'un hook qui a
    jugé et autorisé, et c'est précisément la panne qu'on ne voit jamais. Le
    mode strict la rend bruyante — `[HOOK_FAILED]`, code 2.
    """
    if strict():
        return deny(hook, "HOOK_FAILED",
                    f"le hook n'a pas pu s'exécuter ({exc.__class__.__name__}: {exc}) — refus en mode strict",
                    f"corriger le hook ; `{STRICT_ENV}=0` rend le comportement interactif (panne = autorisation "
                    "avertie), jamais silencieux")
    sys.stderr.write(
        f"[hook] {hook} n'a pas pu s'exécuter ({exc.__class__.__name__}: {exc}) — action AUTORISÉE.\n"
        f"[hook] le contrôle reste joué en CI par son validateur. Corriger le hook.\n"
        f"[hook] {STRICT_ENV}=1 transforme cette panne en refus.\n")
    return ALLOW


def agent_of(data: dict[str, Any]) -> str:
    """L'agent que l'action concerne, `""` si le payload n'en nomme aucun.

    **Deux situations différentes, deux emplacements différents dans le payload,
    et les confondre désarme la moitié de la couche de protection.**

    1. *Le spawn* — le fil principal lance un sous-agent. L'action jugée est
       l'appel de l'outil de délégation lui-même, et le nom de l'agent visé
       arrive sous `tool_input` (`subagent_type`). C'est ce que voient les hooks
       de gate, qui décident si ce spawn a le droit d'avoir lieu.
    2. *L'action DANS le sous-agent* — un `Write`, un `Bash`, un `Read` émis par
       l'agent déjà lancé. Ici `tool_input` ne décrit que le fichier ou la
       commande : l'identité de l'auteur arrive **à la racine**, en `agent_type`.
       C'est ce que voient les hooks d'ownership, et c'est exactement le cas
       qu'ils existent pour juger.

    Ne lire que `tool_input.subagent_type` rendait donc `""` pour tout le cas 2.
    Un hook d'ownership interprète `""` comme « écriture du fil principal », donc
    **autorise** : `preflight_ownership`, `preflight_bash_ownership` et
    `preflight_forbidden_reads` laissaient passer toutes les écritures de tous
    les sous-agents. La règle la plus citée de l'architecture — un `dev-agent`
    ne touche ni `workspace/pipeline/datasets/` ni `workspace/src/{App}/prompts/` — n'était plus
    appliquée qu'en CI par `audit_ownership`, alors que trois hooks prétendaient
    la tenir au runtime. C'est la forme la plus coûteuse d'enforcer absent :
    indiscernable d'une protection active.

    On lit donc les deux emplacements, plus les clés que pose la ligne de
    commande (`--agent`). Élargir la liste des candidats ne peut que faire
    aboutir une identification qui échouait ; le repli `""` reste « fil
    principal », comme avant.

    `agent_id` n'est volontairement pas lu : c'est un identifiant opaque
    d'instance, pas un nom d'agent, et la matrice d'ownership est indexée par
    nom. L'accepter ferait chercher `a3f2c1…` dans `loader.yml`, donc rendrait
    « agent hors matrice », donc autoriserait — une panne silencieuse de plus.
    """
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    candidates = [
        # Cas 1 — le spawn : la cible est nommée dans l'entrée de l'outil.
        (tool_input or {}).get("subagent_type"),
        (tool_input or {}).get("agent"),
        # Cas 2 — l'action dans le sous-agent : l'auteur est nommé à la racine.
        data.get("agent_type"),
        data.get("agentType"),
        data.get("subagent_type"),
        data.get("agent"),
    ]
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return ""


#: Ce qu'un sous-agent que la matrice ne connaît pas a le droit de toucher sous
#: `workspace/` : rien.
#:
#: Les trois hooks d'ownership répondaient « agent hors matrice : ce n'est pas
#: au hook de le trancher » et AUTORISAIENT. Le raisonnement valait pour le fil
#: principal ; appliqué à un sous-agent, il ouvrait la porte la plus large de
#: toute la couche de protection : lancer `Agent(subagent_type="general-purpose",
#: prompt="tu es dev-agent…")`, et cet agent — qui n'est dans aucune matrice —
#: écrit le golden, réécrit un prompt, dépose un rapport de gate. Les gates de
#: spawn le laissaient passer aussi (`out_of_scope` : hors liste = hors
#: périmètre), donc AUCUN contrôle ne le voyait, du lancement à l'écriture.
#:
#: On ferme au moment de l'ÉCRITURE et non du spawn, délibérément : un agent
#: `Explore` lancé par l'utilisateur pour un travail sans rapport avec le
#: pipeline est légitime et ne doit pas être refusé par une gate de phase 3. Ce
#: qu'il ne peut pas faire, c'est produire dans le workspace. Un sous-agent
#: blanchi obtient donc un agent qui lit et ne peut rien livrer — la fraude ne
#: rapporte plus rien.
#:
#: Le fil principal (aucun `agent_type`) reste libre : c'est l'humain.
WORKSPACE_PREFIX = "workspace/"


#: Ce qui APPLIQUE la matrice : un sous-agent hors matrice qui les réécrit
#: neutralise les hooks — pour lui et pour tous les agents après lui. C'était
#: le cas que le module disait fermer (« general-purpose à qui l'on dit tu es
#: dev-agent ») : il réécrivait `_hook.py`, puis écrivait dans le workspace.
ENFORCEMENT_FILES = (
    ".sdda/python/sdda_hooks/", ".sdda/loader.yml", ".sdda/agent-bounds.yaml", ".sdda/INVARIANTS.yml",
    ".claude/settings.json", ".claude/settings.local.json", ".codex/hooks.json", ".codex/config.toml",
    ".gemini/settings.json", ".agents/hooks.json", ".git/hooks/",
)
#: Le développement du FRAMEWORK par sous-agents est légitime : il se déclare,
#: il ne se devine pas.
FRAMEWORK_DEV_ENV = "SDDA_FRAMEWORK_DEV"


def unknown_subagent(hook: str, agent: str, rel: str, *, write: bool = False) -> int:
    """Verdict pour un SOUS-AGENT absent de `loader.yml` : refus sous `workspace/`,
    et refus d'ÉCRIRE ce qui applique la matrice (`ENFORCEMENT_FILES`)."""
    from sdda_scripts.audit_ownership import CASE_INSENSITIVE, normalize  # noqa: E402

    normalized = normalize(rel)
    probe = normalized.casefold() if CASE_INSENSITIVE else normalized
    if write and not bypassed(FRAMEWORK_DEV_ENV):
        for guarded in ENFORCEMENT_FILES:
            g = guarded.casefold() if CASE_INSENSITIVE else guarded
            if probe == g.rstrip("/") or (g.endswith("/") and probe.startswith(g)):
                return deny(hook, "OWNERSHIP_AGENT_UNKNOWN",
                            f"`{agent}` n'est dans aucune matrice et réécrit `{normalized}`, qui applique la matrice",
                            "un sous-agent hors matrice ne touche ni aux hooks, ni à loader.yml, ni aux réglages "
                            f"de hooks des harnais ; pour développer le framework par sous-agents, lancer la "
                            f"session avec `{FRAMEWORK_DEV_ENV}=1` (décision explicite, pas une déduction)")
    if not (probe == WORKSPACE_PREFIX.rstrip("/") or probe.startswith(WORKSPACE_PREFIX)):
        return ALLOW  # hors du workspace : pas notre affaire
    return deny(hook, "OWNERSHIP_AGENT_UNKNOWN",
                f"`{agent}` n'est dans aucune matrice d'ownership et touche `{normalized}`",
                "un sous-agent qui écrit ou lit sous workspace/ est l'un des agents déclarés dans loader.yml, "
                "lancé sous son propre nom — un `general-purpose` à qui l'on dit « tu es dev-agent » "
                "n'a ni ses droits, ni ses interdits, donc aucun des deux ne s'applique")


def out_of_scope(data: dict[str, Any], applies_to: tuple[str, ...]) -> bool:
    """L'action en cours sort-elle du périmètre de ce hook ?

    Trois cas, et leur raison :

    - `applies_to` vide → le hook se prononce sur tout (l'ownership vaut pour
      chaque écriture, quel qu'en soit l'auteur) ;
    - le payload ne nomme aucun agent → **on se prononce**. C'est l'invocation
      manuelle ou le CI : refuser de juger faute d'étiquette rendrait le hook
      inutilisable précisément là où il sert de filet ;
    - le payload nomme un agent hors liste → on laisse passer. Une gate de
      phase 3 n'a rien à dire sur le lancement d'un `po-elicitor`, et prétendre
      le contraire est ce qui transforme un invariant en obstacle qu'on
      désactive.
    """
    if not applies_to:
        return False
    agent = agent_of(data)
    if not agent:
        return False
    return agent not in applies_to


# ---------------------------------------------------------------------------
# Payloads des autres harnais -> forme Claude Code
# ---------------------------------------------------------------------------
# Tous les hooks jugent `tool_name` + `tool_input.file_path` / `.command` /
# `.subagent_type` : la forme Claude Code. Un payload Gemini (`write_file`) ou
# Codex (`apply_patch`) qui arrivait tel quel était un outil inconnu, donc
# AUTORISÉ — un hook câblé et muet. La commande générée par `harness_build`
# pose `SDDA_HARNESS` ; sans elle (Claude Code, appel manuel), rien ne change.
#
# Ce qui n'est PAS traduit, faute de source : l'identité de l'agent auteur
# d'un appel (aucun des deux payloads ne la porte) — les hooks qui en dépendent
# ne sont pas câblés sur ces harnais (cf. `harness_build`, rapport d'impact).
HARNESS_ENV = "SDDA_HARNESS"

#: https://geminicli.com/docs/reference/tools/ — nom Gemini -> nom Claude.
_GEMINI_TOOLS = {"write_file": "Write", "replace": "Edit", "read_file": "Read", "glob": "Glob",
                 "list_directory": "Glob", "grep_search": "Grep", "run_shell_command": "Bash"}

#: En-têtes de fichier de la grammaire `apply_patch` de Codex
#: (github.com/openai/codex, codex-rs/apply-patch/apply_patch_tool_instructions.md).
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


def _command_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        import shlex
        return shlex.join(str(v) for v in value)
    return str(value or "")


def foreign_payloads(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Le payload traduit en une ou plusieurs actions de forme Claude Code."""
    harness = os.environ.get(HARNESS_ENV, "").strip().lower()
    if harness in ("", "claude-code"):
        return [data]
    tool = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input")
    raw: dict[str, Any] = tool_input if isinstance(tool_input, dict) else {}
    base = {k: v for k, v in data.items() if k not in ("tool_name", "tool_input")}

    def action(name: str, tool_input: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**base, **extra, "tool_name": name, "tool_input": tool_input}

    if harness == "gemini-cli":
        mapped = _GEMINI_TOOLS.get(tool)
        if mapped in ("Write", "Edit", "Read"):
            return [action(mapped, dict(raw))]
        if mapped == "Glob":
            return [action("Glob", {"pattern": raw.get("pattern", ""), "path": raw.get("dir_path") or "."})]
        if mapped == "Grep":
            return [action("Grep", {"pattern": raw.get("pattern", ""), "path": raw.get("dir_path") or ".",
                                    "glob": raw.get("include_pattern") or ""})]
        if mapped == "Bash":
            extra = {}
            if raw.get("dir_path"):
                extra["cwd"] = str(Path(str(data.get("cwd") or ".")) / str(raw["dir_path"]))
            return [action("Bash", {"command": _command_text(raw.get("command"))}, **extra)]
        if tool == "read_many_files":
            items = raw.get("include") or []
            items = [items] if isinstance(items, str) else list(items)
            return [action("Grep", {"pattern": "", "path": ".", "glob": str(i)}) if any(c in str(i) for c in "*?[{")
                    else action("Read", {"file_path": str(i)}) for i in items] or [data]
        if tool:
            # « Subagents are exposed to the main agent as a tool of the same
            # name » (geminicli.com/docs/core/subagents/) : les hooks de spawn
            # ne sont câblés que sur les noms des agents. Les paramètres de cet
            # outil ne sont pas documentés : tout texte qu'il porte tient lieu
            # de `prompt` (là où vivent `SDDA-INSTANCE:` et les marqueurs).
            prompt = "\n".join(str(v) for v in raw.values() if isinstance(v, str))
            return [action("Agent", {"subagent_type": tool, "prompt": prompt})]
        return [data]

    if harness == "codex":
        if tool == "apply_patch":
            files = _PATCH_FILE_RE.findall(_command_text(raw.get("command")))
            return [action("Write", {"file_path": f}) for f in dict.fromkeys(files)] or [data]
        if tool == "Bash":
            return [action("Bash", {"command": _command_text(raw.get("command"))})]
        if tool == "spawn_agent":
            return [action("Agent", dict(raw))]
    return [data]


def run(hook: str, fn, applies_to: tuple[str, ...] = ()) -> int:
    """Enveloppe standard : usage, options, payload, périmètre, racine, verdict, dégradation sûre.

    `-h`/`--help` écrit l'usage et rend 0 sans rien juger. Une option inconnue
    REFUSE en mode strict (`[HOOK_ARG_UNKNOWN]`, code 2) : l'appelant croit
    restreindre le périmètre, et le hook jugerait tout — c'est le faux vert de
    la CI. En session interactive elle est signalée sur stderr et le hook
    juge quand même : un opérateur qui se trompe de drapeau voit l'erreur,
    sans que le pipeline s'arrête sur une faute de frappe.
    """
    argv = invoked_argv()
    if argv is not None and any(a in HELP_FLAGS for a in argv):
        sys.stdout.write(usage(hook))
        return ALLOW
    try:
        _, unknown = parse_argv(argv or [])
        if unknown:
            known = ", ".join(ARGV_KEYS)
            if strict():
                return deny(hook, CLS_ARG_UNKNOWN, f"option(s) inconnue(s) {unknown} — options : {known}",
                            "corriger l'appel : une option ignorée élargit le périmètre du hook au lieu de le restreindre")
            sys.stderr.write(f"[hook] {hook} : option(s) inconnue(s) {unknown} ignorée(s) — options : {known}\n")
        # Un payload d'un autre harnais peut désigner PLUSIEURS actions (un
        # `apply_patch` Codex touche N fichiers) : chacune est jugée, le
        # premier refus l'emporte. Sous Claude Code, une seule — comme avant.
        for data in foreign_payloads(payload(argv)):
            if out_of_scope(data, applies_to):
                continue
            verdict = fn(root_of(data), data)
            if verdict != ALLOW:
                return verdict
        return ALLOW
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — tout échec doit dégrader, pas bloquer
        return degrade(hook, exc)


# ---------------------------------------------------------------------------
# Lecture des gates déjà franchies
# ---------------------------------------------------------------------------
import re as _re  # noqa: E402

_MISSION_RE = _re.compile(r"\bMISSION\s*:?\s*(\d+)\b")


def mission_of(data: dict[str, Any]) -> str | None:
    """La MISSION d'un spawn : champ du payload, sinon `MISSION : n` du brief, sinon `SDDA_MISSION`.

    Le harnais n'envoie jamais `mission` : les hooks de gate lisaient donc les
    rapports de TOUTES les missions, et un G1 vert d'une autre mission ouvrait
    la phase 2 de celle-ci.
    """
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    mission = data.get("mission") or (tool_input or {}).get("mission")
    if not mission:
        m = _MISSION_RE.search(str((tool_input or {}).get("prompt") or ""))
        mission = m.group(1) if m else os.environ.get("SDDA_MISSION") or None
    head = str(mission).split("-", 1)[0] if mission else ""
    return head if head.isdigit() else None


def _of_mission(artifact: str, mission: str | None) -> bool:
    """Un artefact de rapport relève-t-il de la MISSION (`1`, `1-Name`, `1-2-Cap`, `1-lookup`) ou du projet ?"""
    if mission is None:
        return True
    a = str(artifact)
    return a == mission or a.startswith(mission + "-") or a == "stack"


def gate_status(root: Path, gate: str, mission: str | None = None) -> tuple[str, list[str]]:
    """`(verdict, raisons)` pour une gate : `green` | `red` | `absent`.

    Une gate composite (G2, G3, G7, G8) n'est verte que si **toutes** ses parts
    OBLIGATOIRES le sont, pour chaque artefact qui en porte ; une part
    contributive ne compte que par son rouge. Une part manquante, ou périmée
    (source modifiée depuis le rapport), rend `absent`, jamais `green` :
    l'absence de preuve n'est pas une preuve. Cette fonction ne regardait que le
    champ `ok` de tous les rapports de la gate, toutes missions confondues —
    un seul `G3-stack.dataaccess` vert suffisait à câbler les agents.
    """
    from sdda_lib.gate_reports import GATE_PARTS, GATE_PARTS_ADVISORY  # noqa: PLC0415

    reports = [r for r in load_gate_reports(root)
               if r.get("gate") == gate and _of_mission(str(r.get("artifact", "")), mission)]
    where = paths.rel(root, paths.validation_dir(root))
    if not reports:
        return "absent", [f"aucun rapport {gate}{f' pour la mission {mission}' if mission else ''} dans {where}"]

    red = [r for r in reports if not r.get("ok")]
    if red:
        reasons = []
        for r in red:
            classes = [e.get("class", "?") for e in (r.get("errors") or [])]
            reasons.append(f"{r.get('gate')}{'.' + r['part'] if r.get('part') else ''} : {classes}")
        return "red", reasons

    try:
        from sdda_scripts.compute_status import stale_keys  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — sans l'index, on juge sans la péremption plutôt que de refuser
        stale_keys = None  # type: ignore[assignment]
    if stale_keys is not None:
        stale = [(r, stale_keys(root, r)) for r in reports]
        stale = [(r, keys) for r, keys in stale if keys]
        if stale:
            r, keys = stale[0]
            return "absent", [f"{gate}{'.' + r['part'] if r.get('part') else ''} ({r.get('artifact')}) périmé : "
                              f"{', '.join(keys)} a changé depuis le rapport"]

    required = set(GATE_PARTS.get(gate, ()))
    if required:
        advisory = set(GATE_PARTS_ADVISORY.get(gate, ()))
        by_artifact: dict[str, set[str]] = {}
        for r in reports:
            by_artifact.setdefault(str(r.get("artifact")), set()).add(str(r.get("part") or ""))
        carrying = {a: parts for a, parts in by_artifact.items() if parts - advisory}
        if not carrying:
            return "absent", [f"{gate} : seules des parts contributives ({', '.join(sorted(advisory))}) — "
                              f"parts obligatoires {sorted(required)} absentes"]
        for artifact, parts in sorted(carrying.items()):
            missing = required - parts
            if missing:
                return "absent", [f"{gate} ({artifact}) : part(s) obligatoire(s) absente(s) {sorted(missing)}"]
    return "green", [f"{len(reports)} rapport(s) vert(s)"]


def require_gate(hook: str, root: Path, gate: str, artifact: str | None, cls: str, fix: str) -> int:
    verdict, reasons = gate_status(root, gate, artifact)
    if verdict == "green":
        return allow()
    return deny(hook, cls, f"{gate} non franchie — {'; '.join(reasons)}", fix)


def bypassed(name: str) -> bool:
    """Un bypass nominatif est actif ? (LIFECYCLE R5 — audité par la commande.)"""
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")

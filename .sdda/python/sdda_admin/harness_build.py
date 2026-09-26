#!/usr/bin/env python3
"""
harness_build — compile `.sdda/` vers les façades des harnais.

C'est **la jonction** : sans elle, les agents et les commandes de `.sdda/`
ne sont visibles d'aucun harnais et ne s'exécutent jamais. Le fichier mémoire
(`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) est compilé depuis
`.sdda/ARCHITECTURE.fr.md` — le jumeau français, parce que les agents lisent
des prompts français — avec repli sur `ARCHITECTURE.md` (cf. `memory_source`).

    .sdda/  (source neutre, la seule chose qu'on écrit)
        │
        ├─► .claude/   agents/*.md · commands/*.md · CLAUDE.md · settings.json
        ├─► .codex/    agents/*.toml · AGENTS.md · hooks.json
        ├─► .gemini/   agents/*.md · commands/*.toml · GEMINI.md · settings.json
        ├─► .agents/   rules/*.md · agents/*.md          (Antigravity)
        │   └─ skills/{cmd}/SKILL.md                     (Codex ET Antigravity)
        └─► AGENTS.md · GEMINI.md à la racine            (pointeurs)

Chaque emplacement et chaque format ci-dessus vient de la documentation
OFFICIELLE du harnais ; l'URL est citée à côté de la constante ou de
l'adaptateur qui en dépend. Ce que la documentation ne dit pas n'est pas codé :
c'est dit dans le rapport d'impact et dans `docs/MULTI-HARNESS.md` (« non
vérifié »), jamais deviné ici.

**Les façades sont générées, jamais éditées.** Une modification directe dans
`.claude/` est écrasée au build suivant, et le test de parité la détecte.

**Le rapport d'impact est obligatoire.** Un harnais dont les hooks ne couvrent
pas tout ne perd pas ses invariants : ils se DÉPLACENT vers le CI. Le prétendre
appliqué au runtime serait exactement le doc-theater que `INVARIANTS.yml`
existe pour empêcher — cf. `docs/MULTI-HARNESS.md` §3.

Usage :
    python .sdda/sdda.py harness-build                 # tous
    python .sdda/sdda.py harness-build --harness claude-code
    python .sdda/sdda.py harness-build --check         # CI : dérive ?
    python .sdda/sdda.py harness-build --impact-only

Exit : 0 si tout est compilé (ou à jour en `--check`) · 1 sinon.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import yaml_mini  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()

SDDA = Path(__file__).resolve().parents[2]
ROOT = SDDA.parent

GENERATED_BANNER = (
    "<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis {source}.\n"
    "     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,\n"
    "     et le test de parité la signale. Éditer la source. -->\n"
)

#: Marqueur que porte TOUT fichier généré, bannière HTML ou commentaire TOML.
#: Il sert à ne déclarer orphelin, dans un répertoire partagé avec l'humain
#: (`.agents/skills/`, `.agents/rules/`), que ce que ce build a lui-même écrit :
#: une skill écrite à la main n'est pas une façade périmée, et `--prune` ne
#: doit jamais la supprimer.
GENERATED_MARK = "GÉNÉRÉ"


# ---------------------------------------------------------------------------
# Limites et formats documentés — une constante par fait sourcé
# ---------------------------------------------------------------------------
#: Codex s'arrête d'ajouter des fichiers d'instructions à 32 KiB cumulés, en
#: silence (`project_doc_max_bytes`) :
#: https://developers.openai.com/codex/guides/agents-md (-> learn.chatgpt.com).
#: Le pointeur racine doit tenir bien en dessous, sinon la fin — le statut — est
#: tronquée sans que personne le voie.
CODEX_PROJECT_DOC_MAX_BYTES = 32 * 1024

#: Valeurs de `sandbox_mode` d'un agent Codex :
#: https://developers.openai.com/codex/subagents (-> learn.chatgpt.com/docs/agent-configuration/subagents).
CODEX_SANDBOX_MODES = ("read-only", "workspace-write")

#: Antigravity : 24 000 octets par fichier de règle, includes développés
#: (https://antigravity.google/docs/rules). Au-delà, la règle n'est pas chargée
#: entière — un agent qui croit avoir lu l'architecture en a lu un morceau.
ANTIGRAVITY_RULE_MAX_BYTES = 24_000

#: Valeurs de `trigger` d'une règle Antigravity (même page).
ANTIGRAVITY_RULE_TRIGGERS = ("always_on", "model_decision", "glob", "manual")

#: Gemini CLI : le `name` d'un sous-agent est aussi le nom de l'outil qui le
#: lance ; « Only lowercase letters, numbers, hyphens, and underscores »
#: (https://geminicli.com/docs/core/subagents/).
GEMINI_AGENT_NAME_RE = re.compile(r"^[a-z0-9_-]+$")

#: Outils Claude -> outils Gemini CLI (https://geminicli.com/docs/reference/tools/).
#: `Glob` couvre aussi le listage d'un répertoire côté Claude : il porte donc
#: `list_directory` en plus de `glob`. `MultiEdit` et `NotebookEdit` n'ont pas
#: d'équivalent, et un outil inexistant dans la liste d'un agent n'est pas
#: une restriction — c'est une erreur de chargement.
GEMINI_TOOLS: dict[str, tuple[str, ...]] = {
    "Read": ("read_file",),
    "Write": ("write_file",),
    "Edit": ("replace",),
    "Glob": ("glob", "list_directory"),
    "Grep": ("grep_search",),
    "Bash": ("run_shell_command",),
}


# ---------------------------------------------------------------------------
# Matrice de capacités
# ---------------------------------------------------------------------------
@dataclass
class Harness:
    name: str
    protection_level: str
    status: str
    mechanisms: dict[str, str]
    memory_file: str
    impact: str = ""
    tier_models: dict[str, str] = field(default_factory=dict)

    def supports(self, mechanism: str) -> bool:
        return self.mechanisms.get(mechanism) == "native"

    def model_for(self, tier: str) -> str:
        """Le sélecteur de modèle du harnais pour un tier déclaré.

        `""` quand le harnais n'en déclare aucun : on n'invente pas un modèle
        à sa place, on laisse l'héritage jouer et le rapport d'impact le dira.
        """
        return self.tier_models.get(str(tier).strip(), "")


def load_matrix() -> dict[str, Harness]:
    data = yaml_mini.parse_mapping((SDDA / "capability-matrix.yml").read_text(encoding="utf-8"))
    out: dict[str, Harness] = {}
    for name, payload in (data.get("harnesses") or {}).items():
        out[name] = Harness(
            name=name,
            protection_level=str(payload.get("protection_level", "?")),
            status=str(payload.get("status", "planned")),
            mechanisms={k: str(v) for k, v in (payload.get("mechanisms") or {}).items()},
            memory_file=str(payload.get("memory_file", "AGENTS.md")),
            impact=str(payload.get("impact", "") or ""),
            tier_models={k: str(v) for k, v in (payload.get("tier_models") or {}).items()},
        )
    return out


# ---------------------------------------------------------------------------
# Réécriture des références
# ---------------------------------------------------------------------------
AT_REF_RE = re.compile(r"@(\.sdda/[\w\-./{}*]+)")


def inline_refs(text: str) -> str:
    """`@.sdda/x.md` devient « Read .sdda/x.md avant de poursuivre ».

    Le repli est verbeux à dessein : une référence muette sur un harnais qui ne
    la résout pas produirait un agent qui croit avoir lu une règle qu'il n'a
    jamais vue — un pack manquant à l'échelle d'une règle.
    """
    return AT_REF_RE.sub(lambda m: f"`{m.group(1)}` (Read ce fichier avant de poursuivre)", text)


def rewrite_refs(text: str, harness: Harness) -> str:
    """Adapte les `@`-refs à ce que le harnais sait faire (`at_include`)."""
    if harness.supports("at_include"):
        return text
    return inline_refs(text)


#: Marqueurs de `sync_counters.py` : la SOURCE porte `<!--sdda:count agents-->22<!--/sdda:count-->`
#: pour que le chiffre soit régénéré ; la FAÇADE ne porte que `22`. Un agent ne
#: paie aucun token pour un mécanisme d'entretien de la doc.
SYNC_MARKER_RE = re.compile(r"<!--sdda:(count|config|graders)(?: [^>]*)?-->(.*?)<!--/sdda:\1-->", re.S)


def strip_sync_markers(text: str) -> str:
    return SYNC_MARKER_RE.sub(lambda m: m.group(2), text)


#: Ce qu'un scalaire YAML peut porter NU sans qu'aucun parseur ne le retype ni
#: ne le coupe : un identifiant (`sonnet`, `balanced`, `dev-app`). Tout le reste
#: — une phrase, un chemin, un nombre, une date, `yes`/`no`/`null` — est cité.
YAML_PLAIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
YAML_RETYPED = frozenset({"true", "false", "yes", "no", "on", "off", "null", "y", "n"})
_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)\Z", re.S)


def yaml_scalar(value: Any) -> str:
    """Une valeur de frontmatter que le harnais relit en YAML strict.

    `yaml_mini` relit la source avec tolérance ; Claude Code, non. Une
    description qui porte `Profile: poc` ou ` # ` écrite sans guillemets est un
    en-tête invalide, et le harnais ÉCARTE l'agent sans rien dire :
    `Agent type 'dev-app' not found`, redémarrage ou pas. Une liste noire de
    caractères aurait toujours un retard sur le parseur ; la règle est donc
    inverse : seul un identifiant nu reste nu, tout le reste est émis entre
    guillemets doubles (JSON est du YAML valide, et `json.loads` suffit à le
    vérifier sans PyYAML — c'est ce que le test de parité relit).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    text = value if isinstance(value, str) else str(value)
    if YAML_PLAIN_RE.match(text) and text.lower() not in YAML_RETYPED:
        return text
    return json.dumps(text, ensure_ascii=False)


def frontmatter(meta: dict[str, Any]) -> str:
    return "---\n" + "\n".join(f"{k}: {yaml_scalar(v)}" for k, v in meta.items()) + "\n---\n"


def frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    # Délimiteurs sur leur PROPRE ligne : un `---` dans une valeur ne coupe
    # plus l'en-tête à mi-chemin.
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml_mini.parse_mapping(m.group(1))
    except Exception:
        meta = {}
    return meta, m.group(2).lstrip("\n")


# ---------------------------------------------------------------------------
# TOML — Codex (agents) et Gemini CLI (commandes)
# ---------------------------------------------------------------------------
# Un TOML invalide n'est pas une façade dégradée : c'est une façade absente, et
# le harnais ne dit pas toujours pourquoi. Les deux fonctions ci-dessous
# émettent un TOML que `tomllib` relit — le test et `framework-smoke` le font.
_TOML_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def toml_string(text: str) -> str:
    """Chaîne TOML de base sur une ligne.

    `json.dumps` produit les mêmes échappements que TOML (`\\"`, `\\\\`, `\\n`,
    `\\uXXXX`), à une exception près : il laisse passer DEL (U+007F), que TOML
    interdit en clair. L'ancienne forme n'échappait que `"` — un `\\` dans une
    description suffisait à rendre la commande illisible.
    """
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_multiline(text: str) -> str:
    """Chaîne TOML multi-ligne, littérale quand c'est possible.

    Le littéral `'''…'''` ne connaît AUCUN échappement : on ne peut pas y
    « échapper » un `'''`, seulement changer de forme. L'ancienne version
    écrivait `\\'\\'\\'`, qui arrivait tel quel — antislashs compris — dans le
    prompt. Quand le texte contient `'''`, un caractère de contrôle ou finit
    par `'`, on bascule donc sur la chaîne de base `\"\"\"…\"\"\"`, échappée.
    """
    body = text if text.endswith("\n") else text + "\n"
    if "'''" not in body and not _TOML_CTRL_RE.search(body) and "\r" not in body:
        return "'''\n" + body + "'''"
    escaped = body.replace("\\", "\\\\").replace('"""', '""\\"').replace("\r", "\\r")
    escaped = _TOML_CTRL_RE.sub(lambda m: f"\\u{ord(m.group(0)):04x}", escaped)
    return '"""\n' + escaped + '"""'


# ---------------------------------------------------------------------------
# Adaptateurs
# ---------------------------------------------------------------------------
# Avertissements collectés pendant un build, par harnais. Rapportés par main
# après la ligne de build, pour qu'une dégradation de façade ne soit jamais
# silencieuse.
BUILD_NOTES: dict[str, list[str]] = {}

#: Le fichier mémoire des harnais (`.claude/CLAUDE.md`, `.codex/AGENTS.md`,
#: `.gemini/GEMINI.md`, `.agents/rules/`) est compilé depuis le jumeau FRANÇAIS
#: de l'architecture. La documentation est en anglais par défaut
#: (`ARCHITECTURE.md`), mais le fichier mémoire est lu par les Developer Agents
#: avec leurs prompts, qui sont en français : leur servir l'architecture dans
#: une autre langue que leurs fiches, c'est deux vocabulaires pour une même
#: règle. Si le jumeau manque, on compile l'anglais plutôt que rien — et le
#: build le dit.
MEMORY_SOURCE_FR = "ARCHITECTURE.fr.md"
MEMORY_SOURCE_FALLBACK = "ARCHITECTURE.md"


def memory_source(sdda: Path | None = None) -> Path:
    """La source du fichier mémoire : `ARCHITECTURE.fr.md`, sinon `ARCHITECTURE.md`."""
    base = sdda or SDDA
    preferred = base / MEMORY_SOURCE_FR
    return preferred if preferred.is_file() else base / MEMORY_SOURCE_FALLBACK


def tier_of(meta: dict[str, Any]) -> str:
    """Le tier déclaré par un agent : `model_tier`, sinon `tier_default`.

    Une seule lecture pour tous les adaptateurs : Claude lisait les deux clés,
    Codex et Gemini seulement la première avec `balanced` en dur — un agent
    qui ne déclarait que `tier_default: deep` tournait en `balanced` ailleurs.
    """
    return str(meta.get("model_tier") or meta.get("tier_default") or "balanced").strip()


def tools_of(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("tools") or []
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",")]
    return [str(t).strip() for t in raw if str(t).strip()]


def writes_files(meta: dict[str, Any]) -> bool:
    return bool({"Write", "Edit", "MultiEdit", "NotebookEdit"} & set(tools_of(meta)))


@dataclass
class HookPort:
    """Le sort d'un hook sur un harnais : câblé, dégradé, ou absent — et pourquoi."""

    status: str          # "native" | "degraded" | "absent"
    note: str = ""


@dataclass
class BuildPlan:
    """Ce qu'un build produit : chemin -> contenu. Rien n'est écrit avant que
    tout soit calculé, pour qu'un échec ne laisse pas une façade à moitié
    régénérée."""

    files: dict[Path, str] = field(default_factory=dict)
    #: module de hook -> son sort sur ce harnais (lu par le rapport d'impact).
    ported: dict[str, HookPort] = field(default_factory=dict)

    def add(self, path: Path, content: str) -> None:
        self.files[path] = content


class Adapter:
    """Socle commun. Une sous-classe ne redéfinit que ce qui diffère."""

    out_dir: str = ""
    #: Fichiers mémoire que le harnais lit à la RACINE du dépôt, et qui
    #: reçoivent donc un pointeur vers la façade.
    root_pointers: tuple[str, ...] = ()
    #: (répertoire relatif à ROOT, n'y compter que les fichiers générés ?) —
    #: où chercher les orphelins. `True` pour un répertoire que l'humain peut
    #: aussi garnir (`.agents/skills/`) : seul ce que le build a écrit y est
    #: jugé.
    managed: tuple[tuple[str, bool], ...] = ()

    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    def note(self, text: str) -> None:
        BUILD_NOTES.setdefault(self.harness.name, []).append(text)

    # -- agents ------------------------------------------------------------
    def emit_agents(self, plan: BuildPlan, out: Path) -> int:
        count = 0
        for src in sorted((SDDA / "agents").glob("*.md")):
            meta, body = frontmatter_and_body(src.read_text(encoding="utf-8"))
            # Les marqueurs de compteurs sont retirés pour TOUS les harnais :
            # Gemini les gardait, faute de passer par ce socle.
            path, content = self.render_agent_file(out, src, meta, strip_sync_markers(body))
            plan.add(path, content)
            count += 1
        return count

    def render_agent_file(self, out: Path, src: Path, meta: dict[str, Any], body: str) -> tuple[Path, str]:
        return out / "agents" / src.name, self.render_agent(src, meta, body)

    def render_agent(self, src: Path, meta: dict[str, Any], body: str) -> str:
        raise NotImplementedError

    # -- commandes ---------------------------------------------------------
    def emit_commands(self, plan: BuildPlan, out: Path) -> int:
        count = 0
        for src in sorted((SDDA / "commands").glob("*.md")):
            meta, body = frontmatter_and_body(src.read_text(encoding="utf-8"))
            self.render_command(plan, out, src, meta, strip_sync_markers(body))
            count += 1
        return count

    def render_command(self, plan, out, src, meta, body) -> None:
        raise NotImplementedError

    # -- mémoire -----------------------------------------------------------
    def memory_text(self) -> tuple[Path, str]:
        source = memory_source()
        if source.name != MEMORY_SOURCE_FR:
            self.note(
                f".sdda/{MEMORY_SOURCE_FR} absent — fichier mémoire compilé depuis "
                f".sdda/{source.name} (anglais), alors que les prompts sont en français"
            )
        return source, rewrite_refs(strip_sync_markers(source.read_text(encoding="utf-8")), self.harness)

    def emit_memory_file(self, plan: BuildPlan, out: Path) -> None:
        # Le fichier mémoire de chaque harnais EST l'architecture : une seule
        # source, pas de « corps d'entrée » optionnel qu'aucun dépôt n'a jamais eu.
        source, text = self.memory_text()
        plan.add(
            out / self.harness.memory_file,
            GENERATED_BANNER.format(source=f".sdda/{source.name}") + "\n" + text,
        )

    def emit_settings(self, plan: BuildPlan, out: Path) -> None:
        """Par défaut : aucun hook câblé — et chaque hook est déclaré absent."""
        for module, _wiring in discover_hook_wirings():
            plan.ported[module] = HookPort("absent", "aucun câblage pour ce harnais")

    def build(self) -> tuple[BuildPlan, dict[str, int]]:
        out = ROOT / self.out_dir
        plan = BuildPlan()
        counts = {
            "agents": self.emit_agents(plan, out),
            "commands": self.emit_commands(plan, out),
        }
        self.emit_memory_file(plan, out)
        self.emit_settings(plan, out)
        return plan, counts


# ---------------------------------------------------------------------------
# Découverte du câblage des hooks
# ---------------------------------------------------------------------------
HOOKS_DIR = SDDA / "python" / "sdda_hooks"


def discover_hook_wirings() -> list[tuple[str, dict[str, str] | None]]:
    """`[(module, {'event', 'matcher'} | None)]` pour chaque hook du disque.

    Lecture par `ast`, pas par `import` : un build ne doit pas exécuter le code
    qu'il compile. Un hook dont l'import échouerait — dépendance manquante,
    erreur de syntaxe — disparaîtrait autrement de `settings.json` en silence,
    et la façade dirait « aucun hook » là où le disque en porte onze.

    Seuls `event` et `matcher` sont extraits : `applies_to` est résolu au
    runtime par le hook lui-même, et référence des constantes de `_hook.py` que
    `ast.literal_eval` ne saurait pas évaluer.
    """
    import ast

    out: list[tuple[str, dict[str, str] | None]] = []
    for path in sorted(HOOKS_DIR.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        wiring: dict[str, str] | None = None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            out.append((path.stem, None))
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "WIRING" for t in node.targets):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            found: dict[str, str] = {}
            for key, value in zip(node.value.keys, node.value.values):
                if (isinstance(key, ast.Constant) and key.value in ("event", "matcher")
                        and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    found[key.value] = value.value
            if "event" in found and "matcher" in found:
                wiring = found
            break
        out.append((path.stem, wiring))
    return out


def wiring_kind(wiring: dict[str, str]) -> str:
    """La famille d'un câblage Claude : `write`, `read`, `shell`, `spawn`, `stop`."""
    if wiring["event"] == "SubagentStop":
        return "stop"
    tools = set(wiring["matcher"].split("|"))
    if tools & {"Task", "Agent"}:
        return "spawn"
    if tools & {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return "write"
    if tools & {"Bash", "PowerShell"}:
        return "shell"
    if tools & {"Read", "Glob", "Grep"}:
        return "read"
    return "other"


#: Refus NATIFS de lecture des fichiers de secrets du workspace — un filet non
#: lexical sous les hooks.
#:
#: Les hooks de lecture jugent un chemin qu'ils savent lire ; le harnais, lui,
#: applique ses règles `permissions.deny` à `Read` (et, selon la documentation,
#: à `Grep` et `Glob`) AVANT tout hook, sans analyse de commande et sans
#: dépendre d'un interpréteur Python présent sur la machine. Syntaxe des règles
#: (docs Claude Code, « permissions ») : `/chemin` est relatif à la RACINE DU
#: PROJET, `./chemin` au répertoire courant, `//chemin` absolu ; motifs à la
#: gitignore. Les deux premières formes sont écrites, parce que la seconde
#: couvre une session lancée depuis la racine même si la première était lue
#: autrement par une version du harnais.
#:
#: `.env.example` n'est PAS refusé : c'est un gabarit de noms, que `dev-backend`
#: édite (et `Edit` exige un `Read` préalable). Ce que ce filet ne couvre pas :
#: le shell (`cat .env`) — c'est le hook `preflight_bash_ownership` qui le tient.
SECRET_READ_DENY = tuple(
    f"Read({prefix}workspace/{where}/{name})"
    for prefix in ("/", "./")
    for where in ("assets", "src/**")
    for name in (".env", ".env.local", ".env.production", ".env.development")
)

#: Ancres de la racine du projet dans la commande d'un hook, par harnais.
#: - Claude Code pose `$CLAUDE_PROJECT_DIR` ;
#: - Gemini CLI pose `$GEMINI_PROJECT_DIR` (https://geminicli.com/docs/hooks/) ;
#: - Codex lance la commande dans le `cwd` de la session et ne documente AUCUNE
#:   variable de racine ; ses propres exemples s'ancrent par
#:   `$(git rev-parse --show-toplevel)` (https://developers.openai.com/codex/hooks
#:   -> learn.chatgpt.com/docs/hooks). Le repli `pwd` couvre un dépôt sans git.
ANCHOR_CLAUDE = "$CLAUDE_PROJECT_DIR"
ANCHOR_GEMINI = "$GEMINI_PROJECT_DIR"
ANCHOR_CODEX = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"


def hook_command(script: str, anchor: str = ANCHOR_CLAUDE, harness: str = "") -> str:
    """La commande shell d'un hook câblé — interpréteur configurable, échec de LANCEMENT visible.

    La forme d'avant, `python "<hook>"`, avait deux pannes silencieuses :

    - `python` absent du PATH (courant sous Windows, où seul `py` est installé,
      ou où `python` est l'alias du Store qui rend 9009) : le hook ne démarre
      pas, le code de sortie n'est pas 2, et le harnais AUTORISE. Les quatorze
      invariants disparaissaient sans un mot. `SDDA_PYTHON` choisit
      l'interpréteur (`SDDA_PYTHON="py -3"`, un chemin absolu…), `python` restant
      le repli documenté ;
    - un lancement raté (code ∉ {0, 2}) ne se distinguait pas d'un hook qui juge.
      Il est désormais DIT sur stderr, et REFUSÉ en mode strict
      (`SDDA_HOOKS_STRICT=1`, la CI) — même règle que `_hook.degrade` pour une
      exception, étendue au cas où Python n'a jamais démarré.

    `harness` (hors Claude) pose `SDDA_HARNESS` : c'est ce qui dit à `_hook.py`
    de traduire le payload du harnais (`write_file`, `apply_patch`…) en la forme
    que les hooks jugent. Sans lui, un hook reçoit un outil qu'il ne connaît
    pas et AUTORISE — un enforcer câblé et muet.

    Le shell est POSIX (Git Bash sous Windows pour Claude Code). Le chemin est
    ancré sur la racine du projet, entre guillemets (cf. `emit_settings`).
    `hooks-selfcheck` exécute chaque commande ainsi générée avec un payload
    inoffensif et un payload à refuser.
    """
    name = script.rsplit("/", 1)[-1]
    prefix = f"SDDA_HARNESS={harness} " if harness else ""
    return (
        f'{prefix}${{SDDA_PYTHON:-python}} "{anchor}/{script}"; rc=$?; '
        f'if [ $rc -ne 0 ] && [ $rc -ne 2 ]; then '
        # Message ASCII : il traverse un shell dont l'encodage de stderr n'est
        # pas garanti, au moment précis où il doit être lu.
        f'echo "[hook] {name} ne demarre pas (code $rc) - interpreteur: SDDA_PYTHON=${{SDDA_PYTHON:-python}}" >&2; '
        f'if [ "${{SDDA_HOOKS_STRICT:-0}}" = "1" ]; then exit 2; fi; fi; exit $rc'
    )


def hook_rel(module: str) -> str:
    return f".sdda/python/sdda_hooks/{module}.py"


#: `$0`, `$1`… dans le corps d'une commande Claude Code sont des PLACEHOLDERS
#: d'arguments (indexés à partir de 0) : https://code.claude.com/docs/en/skills.
#: `coût $0.09` devenait `coût 1.09` pour `/sdda-full 1` — et, pire, un
#: placeholder qui reçoit un argument supprime l'ajout de `ARGUMENTS: …` : tout
#: argument au-delà du premier (`--resume`) n'arrivait jamais au modèle.
#: L'échappement documenté est l'antislash (`\$1.00`).
CLAUDE_INDEXED_ARG_RE = re.compile(r"(?<!\\)\$(?=\d)")


class ClaudeAdapter(Adapter):
    """Harnais de référence (niveau A) : tout est natif."""

    out_dir = ".claude"
    managed = ((".claude/agents", False), (".claude/commands", False))

    def render_agent(self, src, meta, body) -> str:
        # Claude Code lit le frontmatter tel quel ; on le conserve verbatim et
        # on n'ajoute que la bannière, en commentaire HTML après le bloc.
        #
        # Une seule clé est AJOUTÉE : `model:`, résolue depuis le tier déclaré
        # par l'agent et la table `tier_models` du harnais. La source ne la
        # porte pas et ne doit pas la porter — un agent déclare un tier, jamais
        # un modèle (ARCHITECTURE §6, P11) — mais la façade, elle, s'adresse au
        # harnais, et le harnais ne lit que `model:`. Sans cette ligne les
        # tiers, leurs planchers et leurs plafonds ne gouvernaient aucun appel :
        # les 22 agents héritaient du modèle du fil parent, et la seule trace
        # de l'écart était la facture.
        meta = dict(meta)
        if "model" not in meta:
            selector = self.harness.model_for(tier_of(meta))
            if selector:
                meta["model"] = selector
        return frontmatter(meta) + GENERATED_BANNER.format(source=f".sdda/agents/{src.name}") + "\n" + body

    def render_command(self, plan, out, src, meta, body) -> None:
        # Pas de `name:` : une commande `.claude/commands/` accepte les champs
        # d'une skill « except `name` and `paths` » — son nom vient du fichier
        # (https://code.claude.com/docs/en/slash-commands). La clé était ignorée.
        front = f"---\ndescription: {yaml_scalar(meta.get('description', ''))}\n---\n"
        plan.add(
            out / "commands" / src.name,
            front + GENERATED_BANNER.format(source=f".sdda/commands/{src.name}") + "\n"
            + CLAUDE_INDEXED_ARG_RE.sub(r"\\$", body),
        )

    def emit_settings(self, plan, out) -> None:
        """Les hooks bloquants — ce qui fait le niveau A.

        Ils s'exécutent AU MOMENT de l'action. Sur les autres harnais, les
        mêmes invariants ne s'appliquent au runtime que pour la part que leur
        payload permet de juger ; le reste bascule en contrôles CI.

        **Le câblage est dérivé, jamais listé ici.** Chaque hook déclare son
        `WIRING` (cf. `sdda_hooks/_hook.py`), et tout module présent sur le
        disque est câblé. Une table codée en dur à cet endroit a laissé cinq
        enforcers déclarés dans `INVARIANTS.yml` ne jamais s'exécuter : ils
        existaient, ils étaient testés, et aucun chemin ne les atteignait. Un
        invariant qu'on croit appliqué est pire qu'un invariant absent, parce
        qu'on cesse de chercher ailleurs.

        Un hook sans `WIRING` n'est pas câblé et c'est dit à voix haute :
        `framework_smoke.hooks.reachable` en fait un échec.
        """
        hooks: dict[str, list[dict[str, Any]]] = {}
        undeclared: list[str] = []
        by_slot: dict[tuple[str, str], list[str]] = {}

        for module, wiring in discover_hook_wirings():
            if wiring is None:
                undeclared.append(module)
                plan.ported[module] = HookPort("absent", "aucun WIRING déclaré")
                continue
            by_slot.setdefault((wiring["event"], wiring["matcher"]), []).append(hook_rel(module))
            plan.ported[module] = HookPort("native")

        # Ordre stable : l'événement, puis le matcher, puis le nom du script.
        # Un `settings.json` dont l'ordre bouge à chaque build ferait échouer
        # `--check` sans qu'aucune source ait changé.
        #
        # Le chemin est ancré sur la RACINE DU PROJET, pas sur le répertoire
        # courant : un chemin relatif se résout contre le cwd du harnais, et il
        # suffit d'un `cd workspace/src/` pour qu'il ne désigne plus rien. Le
        # hook ne s'exécute alors pas — au mieux il laisse passer en silence
        # (les enforcers disparaissent sans un mot), au pire il BLOQUE tout
        # outil tant que le cwd n'est pas revenu. Les deux ont été observés.
        # `$CLAUDE_PROJECT_DIR` est la variable que le harnais pose pour cet
        # usage ; les guillemets tiennent les espaces des chemins Windows.
        for (event, matcher), scripts in sorted(by_slot.items()):
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": hook_command(s)} for s in sorted(scripts)],
            })

        if undeclared:
            self.note(f"{len(undeclared)} hook(s) sans WIRING — non câblé(s) : " + ", ".join(sorted(undeclared)))
        settings = {"permissions": {"deny": list(SECRET_READ_DENY)}, "hooks": hooks}
        plan.add(out / "settings.json", json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Skills partagées — Codex et Antigravity lisent le MÊME `.agents/skills/`
# ---------------------------------------------------------------------------
#: Codex : `.agents/skills/` de chaque répertoire du cwd jusqu'à la racine du
#: dépôt, `SKILL.md` avec `name` et `description` ; invocation `$nom` ou
#: `/skills` (https://developers.openai.com/codex/skills -> learn.chatgpt.com/docs/build-skills).
#: Les custom prompts, que la façade écrivait sous `.codex/prompts/`, ne sont lus
#: QUE sous `~/.codex/prompts` et sont dépréciés au profit des skills
#: (https://developers.openai.com/codex/custom-prompts) : la façade n'était
#: chargée par personne.
#: Antigravity : `.agents/skills/<dossier>/SKILL.md`, `name` + `description`,
#: invocation `/nom` (https://antigravity.google/docs/skills) ; les workflows
#: sont retirés le 1er novembre 2026 (https://antigravity.google/docs/migration/workflows-to-skills/).
#: Aucune limite de taille n'est documentée pour une skill : les commandes ne
#: sont donc pas découpées (les 12 000 caractères valent pour les workflows).
SKILLS_DIR = ".agents/skills"

SKILL_PREAMBLE = (
    "> **Invocation** — Codex CLI : `${name} {{arguments}}` · Antigravity : `/{name} {{arguments}}`.\n"
    "> Les arguments sont le texte tapé après le nom de la skill : là où cette fiche\n"
    "> parle d'arguments de la commande `/{name}`, ce sont eux.\n"
    "> **Déléguer à un agent** veut dire lancer le sous-agent natif du même nom :\n"
    "> `.codex/agents/{{agent}}.toml` sous Codex, `.agents/agents/{{agent}}.md` sous\n"
    "> Antigravity. Les appels `python .sdda/sdda.py …` sont les mêmes partout.\n\n"
)


def emit_shared_skills(plan: BuildPlan) -> int:
    """`.agents/skills/{cmd}/SKILL.md` — identique quel que soit l'adaptateur qui l'émet.

    Codex et Antigravity partagent ce répertoire : si les deux adaptateurs en
    produisaient des versions différentes, `--check` échouerait sur celui qui
    n'a pas été construit en dernier. Le contenu ne dépend donc d'aucun
    `Harness` — les deux ont `at_include` ≠ `native`, les `@`-refs sont inlinées.
    """
    count = 0
    for src in sorted((SDDA / "commands").glob("*.md")):
        meta, body = frontmatter_and_body(src.read_text(encoding="utf-8"))
        name = str(meta.get("name") or src.stem)
        front = frontmatter({"name": name, "description": str(meta.get("description", ""))})
        plan.add(
            ROOT / SKILLS_DIR / name / "SKILL.md",
            front + GENERATED_BANNER.format(source=f".sdda/commands/{src.name}") + "\n"
            + SKILL_PREAMBLE.format(name=name) + inline_refs(strip_sync_markers(body)),
        )
        count += 1
    return count


def ported_tool_header(meta: dict[str, Any], tier: str, tools: str) -> str:
    return (
        f"# Agent `{meta.get('name', '')}`\n\n"
        f"- Tier : `{tier}` "
        f"(plancher `{meta.get('tier_floor', '?')}`, plafond `{meta.get('tier_ceiling', '?')}`)\n"
        f"- Outils autorisés : {tools}\n\n"
    )


class CodexAdapter(Adapter):
    """Codex CLI : sous-agents TOML, skills, hooks `apply_patch` (zones protégées)."""

    out_dir = ".codex"
    root_pointers = ("AGENTS.md",)
    # `.codex/prompts/` : ancienne façade, jamais lue par Codex — ses fichiers
    # générés sont des orphelins à purger.
    managed = ((".codex/agents", False), (".codex/prompts", True), (SKILLS_DIR, True))

    def render_agent_file(self, out, src, meta, body):
        # https://developers.openai.com/codex/subagents : un fichier TOML par
        # agent sous `.codex/agents/`, `name`, `description` et
        # `developer_instructions` obligatoires ; `model` et `sandbox_mode`
        # optionnels. Le Markdown qu'on y déposait n'était pas un agent Codex.
        name = str(meta.get("name") or src.stem)
        tier = tier_of(meta)
        tools = tools_of(meta)
        instructions = (
            GENERATED_BANNER.format(source=f".sdda/agents/{src.name}") + "\n"
            + ported_tool_header(meta, tier, ", ".join(f"`{t}`" for t in tools) or "—")
            + rewrite_refs(body, self.harness)
        )
        lines = [
            f"# GÉNÉRÉ depuis .sdda/agents/{src.name} — ne pas éditer ici.",
            f"name = {toml_string(name)}",
            f"description = {toml_string(str(meta.get('description', '')))}",
        ]
        model = self.harness.model_for(tier)
        if model:
            lines.append(f"model = {toml_string(model)}")
        # Seule restriction d'écriture que Codex offre par agent : un reviewer
        # sans Write/Edit est lancé en lecture seule, les autres dans le dépôt.
        # La matrice d'ownership reste plus fine que le bac à sable, et c'est
        # dit dans le rapport d'impact.
        lines.append(f"sandbox_mode = {toml_string('workspace-write' if writes_files(meta) else 'read-only')}")
        lines.append(f"developer_instructions = {toml_multiline(instructions)}")
        return out / "agents" / f"{src.stem}.toml", "\n".join(lines) + "\n"

    def emit_commands(self, plan, out) -> int:
        return emit_shared_skills(plan)

    def emit_settings(self, plan, out) -> None:
        """`.codex/hooks.json` — seulement ce que le payload Codex permet de juger.

        Documenté (https://developers.openai.com/codex/hooks -> learn.chatgpt.com/docs/hooks) :
        `PreToolUse` intercepte `Bash` et `apply_patch` (`tool_input.command`),
        code 2 = blocage, hooks activés par défaut, hooks de projet chargés
        seulement si la couche `.codex/` est approuvée. NON documenté : un champ
        qui nomme l'agent auteur de l'appel, ou celui que lance `spawn_agent`.
        """
        by_slot: dict[tuple[str, str], list[str]] = {}
        for module, wiring in discover_hook_wirings():
            if wiring is None:
                plan.ported[module] = HookPort("absent", "aucun WIRING déclaré")
                continue
            kind = wiring_kind(wiring)
            if kind == "write":
                # Les zones protégées (rapports de gate, baselines, audit) sont
                # refusées quel que soit l'auteur : ce jugement-là ne demande pas
                # l'identité que Codex ne transmet pas.
                by_slot.setdefault(("PreToolUse", "^apply_patch$"), []).append(hook_rel(module))
                plan.ported[module] = HookPort(
                    "degraded", "`apply_patch` : zones protégées refusées au runtime ; la matrice par agent "
                                "exige l'identité de l'auteur, absente du payload Codex -> CI")
            elif kind == "spawn":
                plan.ported[module] = HookPort(
                    "absent", "le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : "
                              "une gate qui ne sait pas qui part jugerait tout spawn -> CI")
            elif kind in ("read", "shell"):
                plan.ported[module] = HookPort(
                    "absent", "ce hook ne juge qu'un sous-agent nommé ; le payload Codex ne nomme pas l'auteur -> CI")
            elif kind == "stop":
                plan.ported[module] = HookPort(
                    "absent", "`SubagentStop` existe, mais ni l'agent qui s'arrête ni l'effet du code 2 "
                              "ne sont documentés -> CI")
            else:
                plan.ported[module] = HookPort("absent", f"matcher `{wiring['matcher']}` sans équivalent")
        hooks: dict[str, list[dict[str, Any]]] = {}
        for (event, matcher), scripts in sorted(by_slot.items()):
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": hook_command(s, ANCHOR_CODEX, "codex")}
                          for s in sorted(scripts)],
            })
        document = {"description": "GÉNÉRÉ par .sdda/python/sdda_admin/harness_build.py — ne pas éditer ici.",
                    "hooks": hooks}
        plan.add(out / "hooks.json", json.dumps(document, indent=2, ensure_ascii=False) + "\n")


#: Gemini CLI traite `@{chemin}` (injection de fichier) et `!{cmd}` (injection
#: de shell) dans le prompt d'une commande (https://geminicli.com/docs/cli/custom-commands/).
#: `recall@{k}` déclenchait donc une lecture du fichier `k` — et une erreur
#: « Failed to inject content for '@{k}' » — à chaque `/sdda-build`. Aucun
#: échappement n'est documenté : on insère une espace, qui garde le sens.
GEMINI_INJECTION_RE = re.compile(r"([@!])\{")


class GeminiAdapter(Adapter):
    """Gemini CLI : commandes TOML, sous-agents `.gemini/agents/`, hooks `BeforeTool`."""

    out_dir = ".gemini"
    root_pointers = ("GEMINI.md",)
    managed = ((".gemini/agents", False), (".gemini/agents-inline", True), (".gemini/commands", False))

    def render_agent_file(self, out, src, meta, body):
        # https://geminicli.com/docs/core/subagents/ : `.gemini/agents/*.md`,
        # frontmatter `name`, `description` (obligatoires), `tools`, `model`.
        # Les agents étaient déposés sous `agents-inline/`, que Gemini ne lit pas.
        name = str(meta.get("name") or src.stem)
        if not GEMINI_AGENT_NAME_RE.match(name):
            self.note(f"agent `{name}` : nom hors [a-z0-9_-], refusé par Gemini CLI")
        tier = tier_of(meta)
        claude_tools = tools_of(meta)
        tools: list[str] = []
        for tool in claude_tools:
            for mapped in GEMINI_TOOLS.get(tool, ()):
                if mapped not in tools:
                    tools.append(mapped)
        unmapped = [t for t in claude_tools if t not in GEMINI_TOOLS]
        if unmapped:
            self.note(f"agent `{name}` : outil(s) sans équivalent Gemini, omis : {', '.join(unmapped)}")
        head: dict[str, Any] = {"name": name, "description": str(meta.get("description", ""))}
        if tools:
            head["tools"] = tools
        model = self.harness.model_for(tier)
        if model:
            head["model"] = model
        text = (
            frontmatter(head) + GENERATED_BANNER.format(source=f".sdda/agents/{src.name}") + "\n"
            + ported_tool_header(meta, tier, ", ".join(f"`{t}`" for t in tools) or "—")
            + rewrite_refs(body, self.harness)
        )
        return out / "agents" / src.name, text

    def render_command(self, plan, out, src, meta, body) -> None:
        prompt = GEMINI_INJECTION_RE.sub(r"\1 {", rewrite_refs(body, self.harness))
        plan.add(
            out / "commands" / f"{src.stem}.toml",
            f"# GÉNÉRÉ depuis .sdda/commands/{src.name} — ne pas éditer ici.\n"
            f"description = {toml_string(str(meta.get('description', '')))}\n"
            f"prompt = {toml_multiline(prompt)}\n",
        )

    def emit_settings(self, plan, out) -> None:
        """`.gemini/settings.json` — hooks `BeforeTool`, dans la mesure du payload.

        Documenté (https://geminicli.com/docs/hooks/, …/hooks/reference/) :
        `BeforeTool` avec matcher regex, `tool_name` + `tool_input`, code 2 =
        blocage avec `stderr` pour raison, `$GEMINI_PROJECT_DIR`, empreinte des
        hooks de projet (confirmation à chaque changement de commande). Un
        sous-agent est « exposé à l'agent principal comme un outil du même nom »
        (…/core/subagents/). NON documenté : un champ qui nomme l'agent auteur
        d'un appel d'outil, un événement de fin de sous-agent, les paramètres de
        l'outil d'un sous-agent.
        """
        # Pas de `re.escape` : il écrit `\-`, qu'une regex JavaScript en mode
        # `u` refuse hors d'une classe. Les noms sont déjà bornés à
        # [a-z0-9_-] (GEMINI_AGENT_NAME_RE), sans métacaractère.
        agents = sorted(p.stem for p in (SDDA / "agents").glob("*.md") if GEMINI_AGENT_NAME_RE.match(p.stem))
        spawn_matcher = "^(" + "|".join(agents) + ")$"
        write_matcher = "^(write_file|replace)$"
        by_slot: dict[tuple[str, str], list[str]] = {}
        for module, wiring in discover_hook_wirings():
            if wiring is None:
                plan.ported[module] = HookPort("absent", "aucun WIRING déclaré")
                continue
            kind = wiring_kind(wiring)
            if kind == "write":
                by_slot.setdefault(("BeforeTool", write_matcher), []).append(hook_rel(module))
                plan.ported[module] = HookPort(
                    "degraded", "`write_file`/`replace` : zones protégées refusées au runtime ; la matrice par "
                                "agent exige l'identité de l'auteur, absente du payload Gemini -> CI")
            elif kind == "spawn":
                by_slot.setdefault(("BeforeTool", spawn_matcher), []).append(hook_rel(module))
                plan.ported[module] = HookPort(
                    "degraded", "sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de "
                                "`BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — "
                                "non vérifié par un run")
            elif kind in ("read", "shell"):
                plan.ported[module] = HookPort(
                    "absent", "ce hook ne juge qu'un sous-agent nommé ; le payload Gemini ne nomme pas l'auteur -> CI")
            elif kind == "stop":
                plan.ported[module] = HookPort("absent", "aucun événement de fin de sous-agent -> CI")
            else:
                plan.ported[module] = HookPort("absent", f"matcher `{wiring['matcher']}` sans équivalent")
        hooks: dict[str, list[dict[str, Any]]] = {}
        for (event, matcher), scripts in sorted(by_slot.items()):
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"name": f"sdda-{Path(s).stem}", "type": "command",
                           "command": hook_command(s, ANCHOR_GEMINI, "gemini-cli")} for s in sorted(scripts)],
            })
        plan.add(out / "settings.json", json.dumps({"hooks": hooks}, indent=2, ensure_ascii=False) + "\n")


#: Modèles d'un sous-agent Antigravity : `inherit`, `flash` ou `pro`
#: (https://antigravity.google/docs/subagents/).
ANTIGRAVITY_MODELS = ("inherit", "flash", "pro")


def split_markdown(text: str, budget: int) -> list[str]:
    """Découpe un Markdown en morceaux de `budget` octets au plus, aux titres.

    D'abord aux `## `, puis aux `### ` d'une section trop longue, puis aux
    lignes vides : on ne coupe jamais une phrase. Les sections consécutives
    sont regroupées tant qu'elles tiennent, pour que l'architecture reste en
    peu de fichiers.
    """
    def pieces(block: str, level: int) -> list[str]:
        if len(block.encode("utf-8")) <= budget:
            return [block]
        if level <= 3:
            marker = "\n" + "#" * level + " "
            parts = block.split(marker)
            if len(parts) > 1:
                chunks = [parts[0]] + [marker.lstrip("\n") + p for p in parts[1:]]
                chunks = [c if i == 0 else "\n" + c for i, c in enumerate(chunks)]
                out: list[str] = []
                for chunk in chunks:
                    out.extend(pieces(chunk, level + 1))
                return out
            return pieces(block, level + 1)
        out = []
        for para in block.split("\n\n"):
            out.extend([para + "\n\n"] if len((para + "\n\n").encode("utf-8")) <= budget
                       else [para[i:i + budget // 4] for i in range(0, len(para), budget // 4)])
        return out

    grouped: list[str] = []
    for piece in pieces(text, 2):
        if grouped and len((grouped[-1] + piece).encode("utf-8")) <= budget:
            grouped[-1] += piece
        else:
            grouped.append(piece)
    return [g.strip("\n") + "\n" for g in grouped if g.strip()]


class AntigravityAdapter(Adapter):
    """Antigravity : règles `.agents/rules/`, skills et sous-agents `.agents/`.

    Il partageait l'adaptateur et le répertoire `.gemini/` de Gemini CLI, qu'il
    ne lit pas : seuls les `AGENTS.md`/`GEMINI.md` racine l'atteignaient.
    """

    out_dir = ".agents"
    root_pointers = ("AGENTS.md", "GEMINI.md")
    managed = ((".agents/agents", True), (".agents/rules", True), (SKILLS_DIR, True))

    def render_agent_file(self, out, src, meta, body):
        # https://antigravity.google/docs/subagents/ : `.agents/agents/<nom>.md`,
        # `name` et `description` obligatoires, `model` parmi inherit|flash|pro,
        # `subagent: true` pour l'appel par `invoke_subagent`. `tools` n'est PAS
        # émis : la doc ne publie pas la liste des noms d'outils, et signale
        # qu'un nom mal orthographié peut bloquer l'agent. Les outils autorisés
        # restent écrits dans le corps — une consigne, pas un verrou.
        name = str(meta.get("name") or src.stem)
        tier = tier_of(meta)
        head: dict[str, Any] = {"name": name, "description": str(meta.get("description", ""))}
        model = self.harness.model_for(tier)
        if model:
            if model not in ANTIGRAVITY_MODELS:
                self.note(f"tier `{tier}` -> `{model}` : hors {ANTIGRAVITY_MODELS}")
            head["model"] = model
        head["subagent"] = True
        text = (
            frontmatter(head) + GENERATED_BANNER.format(source=f".sdda/agents/{src.name}") + "\n"
            + ported_tool_header(meta, tier, ", ".join(f"`{t}`" for t in tools_of(meta)) or "—")
            + rewrite_refs(body, self.harness)
        )
        return out / "agents" / src.name, text

    def emit_commands(self, plan, out) -> int:
        return emit_shared_skills(plan)

    def emit_memory_file(self, plan, out) -> None:
        """L'architecture en règles de 24 000 octets au plus, `model_decision`.

        59 Ko d'un seul tenant dépassent la limite par fichier ; `always_on`
        partout mangerait l'essentiel des 20 000 tokens du budget des règles
        actives. Les pointeurs racine (toujours actifs) disent de les lire.
        """
        source, text = self.memory_text()
        banner = GENERATED_BANNER.format(source=f".sdda/{source.name}")
        heads: list[tuple[str, str]] = []
        overhead = 400 + len(banner.encode("utf-8"))
        chunks = split_markdown(text, ANTIGRAVITY_RULE_MAX_BYTES - overhead)
        for index, chunk in enumerate(chunks, 1):
            titles = re.findall(r"^## (.+)$", chunk, re.M)
            label = titles[0] if titles else "préambule"
            description = (f"Architecture SDD_Agents, partie {index}/{len(chunks)} ({label}) — "
                           "lire avant toute action du pipeline SDD_Agents")
            heads.append((f"sdda-architecture-{index:02d}.md",
                          frontmatter({"trigger": "model_decision", "description": description})
                          + banner + "\n" + chunk))
        for filename, content in heads:
            plan.add(out / "rules" / filename, content)

    def emit_settings(self, plan, out) -> None:
        # Antigravity a des hooks (`.agents/hooks.json`, `PreToolUse`), mais sa
        # doc ne publie ni les noms des arguments de `toolCall.args`, ni l'effet
        # d'un code de sortie : le refus passe par un JSON `decision` sur stdout
        # (https://antigravity.google/docs/hooks?tab=ide). Câbler un hook qui ne
        # sait pas lire le chemin visé produirait un enforcer muet — pire
        # qu'absent. Rien n'est donc câblé, et c'est dit.
        for module, _wiring in discover_hook_wirings():
            plan.ported[module] = HookPort(
                "absent", "arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI")


ADAPTERS: dict[str, type[Adapter]] = {
    "claude-code": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini-cli": GeminiAdapter,
    "antigravity": AntigravityAdapter,
}


# ---------------------------------------------------------------------------
# Rapport d'impact — obligatoire
# ---------------------------------------------------------------------------
def invariant_hooks() -> list[tuple[str, list[str], bool]]:
    """`[(invariant, hooks enforcers, a-t-il un enforcer déterministe ?)]`.

    Lu par `yaml_mini`, comme `framework_smoke` et le test du manifeste : une
    regex sur l'indentation cassait dès qu'un invariant ou un enforcer
    changeait de forme, et le rapport d'impact disait alors « aucun invariant
    déplacé » sur un manifeste qu'il n'avait pas lu.
    """
    data = yaml_mini.parse((SDDA / "INVARIANTS.yml").read_text(encoding="utf-8"))
    out: list[tuple[str, list[str], bool]] = []
    for inv in (data.get("invariants") or []) if isinstance(data, dict) else []:
        if not isinstance(inv, dict):
            continue
        iid = str(inv.get("id") or "").strip()
        enforcers = [str(e) for e in (inv.get("enforcers") or []) if str(e).startswith(".sdda/python/")]
        hooks = [Path(e).stem for e in enforcers if "sdda_hooks/" in e]
        if iid and hooks:
            out.append((iid, hooks, len(hooks) < len(enforcers)))
    return out


def invariants_by_enforcer_kind() -> dict[str, list[str]]:
    """Invariants dont tous les enforcers sont des hooks (`hook_only`) ou une partie (`mixed`)."""
    kinds: dict[str, list[str]] = {"hook_only": [], "mixed": []}
    for iid, _hooks, mixed in invariant_hooks():
        kinds["mixed" if mixed else "hook_only"].append(iid)
    return kinds


def impact_report(harness: Harness, ported: dict[str, HookPort] | None = None) -> str:
    runtime_hooks = harness.mechanisms.get("runtime_hooks", "unsupported")
    ported = ported or {}
    lines = [
        f"# Rapport d'impact — {harness.name}",
        "",
        f"- Niveau de protection : **{harness.protection_level}**",
        f"- Statut : **{harness.status}**"
        + ("  ⚠️ *compilable, non validé par un run de conformance*"
           if harness.status != "reference" else ""),
        "",
        "## Mécanismes",
        "",
        "| Mécanisme | Support | Conséquence |",
        "|---|---|---|",
    ]
    consequences = {
        "native": "aucune",
        "partial": "**en partie au runtime** — le reste reporté au CI (cf. hooks ci-dessous)",
        "emulated": "dégradation contrôlée (consigne écrite, pas de mécanisme)",
        "ci_fallback": "**reporté au CI** — appliqué plus tard, pas au moment de l'action",
        "unsupported": "**absent** — repli documenté",
    }
    for mechanism, support in sorted(harness.mechanisms.items()):
        lines.append(f"| `{mechanism}` | {support} | {consequences.get(support, '?')} |")

    if runtime_hooks != "native" and ported:
        lines += ["", "## Hooks", "", "| Hook | Sort | Pourquoi |", "|---|---|---|"]
        for module in sorted(ported):
            port = ported[module]
            lines.append(f"| `{module}` | {port.status} | {port.note or '—'} |")

    lines += ["", "## Invariants déplacés vers le CI", ""]
    if runtime_hooks == "native":
        lines.append("Aucun : tous les invariants s'appliquent **au moment de l'action**.")
    else:
        lines += [
            f"`runtime_hooks: {runtime_hooks}` — ce qui suit n'est pas, ou pas entièrement,",
            "appliqué au moment de l'action :",
            "",
        ]
        for iid, hooks, mixed in invariant_hooks():
            states = {ported.get(h, HookPort("absent")).status for h in hooks}
            if states == {"native"}:
                continue
            if states <= {"absent"}:
                verdict = ("partiellement différé (un enforcer déterministe subsiste)" if mixed
                           else "**appliqué en différé (CI)**")
            else:
                verdict = "**en partie au runtime**, le reste appliqué en différé (CI)"
            lines.append(f"- `{iid}` — {verdict}")
        lines += [
            "",
            "> **À dire clairement** : ce qu'aucun hook ne juge ici n'est empêché par",
            "> rien au moment de l'action. Le CI le rattrape — après que le travail a",
            "> été fait sur une base fausse.",
        ]

    if harness.mechanisms.get("structured_output") not in (None, "native"):
        lines += [
            "",
            "## Sorties structurées",
            "",
            f"`structured_output: {harness.mechanisms.get('structured_output')}` — les rapports",
            "d'eval, les verdicts de gate et l'IR sont du JSON. Un JSON approximatif impose",
            "un parsing défensif et crée une classe de faux verts : un rapport mal parsé",
            "dont les champs manquants passent pour « aucun finding ». **À requalifier par",
            "mesure au premier run de conformance, jamais par optimisme.**",
        ]

    if harness.impact:
        lines += ["", "## Note de la matrice", "", harness.impact.strip()]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Pointeurs racine — Codex CLI lit `AGENTS.md`, Gemini CLI `GEMINI.md`,
# Antigravity les deux, à la RACINE du dépôt. Claude Code lit `.claude/CLAUDE.md`
# nativement : pas de pointeur.
# ---------------------------------------------------------------------------
def root_pointer(filename: str) -> str:
    """Le fichier mémoire racine : un renvoi par harnais, pas une copie.

    Le contenu ne dépend QUE du nom du fichier : Codex et Antigravity écrivent
    tous deux `AGENTS.md`, Gemini CLI et Antigravity `GEMINI.md`. Un contenu
    propre à chaque harnais ferait échouer `--check` sur celui qui n'a pas été
    construit en dernier — et Antigravity, qui lit les deux fichiers, lisait
    sinon un `AGENTS.md` qui se présentait comme « Codex CLI ».

    `GEMINI.md` IMPORTE la façade (`@./.gemini/GEMINI.md`, syntaxe documentée :
    https://geminicli.com/docs/cli/gemini-md/) : Gemini CLI la charge alors
    nativement au lieu de compter sur une consigne « lire en entier ». Codex
    n'a pas d'import, et sa façade (59 Ko) dépasserait `project_doc_max_bytes`
    de toute façon : `AGENTS.md` reste un renvoi.
    """
    text = (
        GENERATED_BANNER.format(source=".sdda/capability-matrix.yml")
        + "\n# SDD_Agents — harnais expérimentaux (Codex CLI, Gemini CLI, Antigravity)\n\n"
        "Les instructions du framework sont compilées par harnais ; **les lire avant\n"
        "toute action** :\n\n"
        "- **Codex CLI** : `.codex/AGENTS.md` (le lire en entier) ; commandes en skills\n"
        "  `.agents/skills/` (`$sdda-…`) ; agents `.codex/agents/*.toml` ;\n"
        "  hooks `.codex/hooks.json`.\n"
        "- **Gemini CLI** : `.gemini/GEMINI.md` (importé ci-dessous) ; commandes\n"
        "  `.gemini/commands/*.toml` ; agents `.gemini/agents/` ; hooks `.gemini/settings.json`.\n"
        "- **Antigravity** : règles `.agents/rules/` (l'architecture, en parties) ;\n"
        "  skills `.agents/skills/` (`/sdda-…`) ; agents `.agents/agents/`.\n\n"
        "**Statut : expérimental.** Ces façades se compilent et sont vérifiées, mais\n"
        "aucun run de conformance ne les a validées. Codex CLI et Antigravity n'ont\n"
        "**aucune gate bloquante au runtime** ; sous Codex et Gemini CLI, seuls les\n"
        "hooks que leur payload permet de juger sont câblés (zones protégées, et les\n"
        "gates de spawn sous Gemini CLI). Le reste est reporté au CI et aux scripts\n"
        "déterministes — une écriture hors ownership n'est rattrapée qu'après coup.\n"
        "Détail : `{.codex,.gemini,.agents}/harness-impact.md`, `.sdda/docs/MULTI-HARNESS.md`.\n\n"
        "Avant de considérer un travail terminé : `python .sdda/sdda.py framework-smoke`.\n"
    )
    if filename == "GEMINI.md":
        text += "\n@./.gemini/GEMINI.md\n"
    return text


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build_harness(name: str, harness: Harness) -> tuple[BuildPlan, dict[str, int]]:
    adapter_cls = ADAPTERS.get(name)
    if adapter_cls is None:
        raise KeyError(f"aucun adaptateur pour `{name}`")
    adapter = adapter_cls(harness)
    plan, counts = adapter.build()
    # Le rapport d'impact fait partie du plan : on ne peut pas produire une
    # façade sans lui.
    plan.add(ROOT / adapter.out_dir / "harness-impact.md", impact_report(harness, plan.ported))
    for filename in adapter.root_pointers:
        plan.add(ROOT / filename, root_pointer(filename))
    return plan, counts


def write_plan(plan: BuildPlan) -> int:
    written = 0
    for path, content in sorted(plan.files.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != content:
            path.write_text(content, encoding="utf-8")
            written += 1
    return written


def drift(plan: BuildPlan) -> list[str]:
    out: list[str] = []
    for path, content in sorted(plan.files.items()):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            out.append(f"{rel} : absent")
        elif path.read_text(encoding="utf-8") != content:
            out.append(f"{rel} : diverge de la source")
    return out


def orphans(plan: BuildPlan, adapter: Adapter | type[Adapter]) -> list[str]:
    """Fichiers de la façade que la source ne produit plus.

    Un agent retiré de `.sdda/` qui survit dans `.claude/` reste invocable —
    c'est une commande fantôme qui référence des fichiers disparus. Dans un
    répertoire partagé avec l'humain (`managed` à `True`), seul un fichier qui
    porte la marque de génération est jugé : `--prune` ne supprime jamais une
    skill ou une règle écrite à la main.
    """
    expected = set(plan.files)
    found = []
    for rel_dir, generated_only in adapter.managed:
        directory = ROOT / rel_dir
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path in expected or "__pycache__" in path.parts:
                continue
            if generated_only:
                try:
                    if GENERATED_MARK not in path.read_text(encoding="utf-8", errors="replace")[:600]:
                        continue
                except OSError:
                    continue
            found.append(path.relative_to(ROOT).as_posix())
    return sorted(found)


def default_targets(matrix: dict[str, Harness]) -> list[str]:
    """Les harnais à construire quand aucun n'est nommé.

    Un harnais par répertoire de façade : deux harnais qui partageraient un
    `out_dir` écraseraient chacun le `harness-impact.md` de l'autre, et
    `--check` échouerait à tout coup sur celui qui a perdu — un faux rouge
    permanent, donc du bruit qu'on apprend à ignorer. Chaque adaptateur a
    aujourd'hui son répertoire ; la règle reste pour le prochain.
    """
    def rank(name: str, out_dir: str) -> tuple[int, int]:
        owns_dir = name.startswith(out_dir.lstrip("."))
        return (matrix[name].status != "planned", owns_dir)

    chosen: dict[str, str] = {}
    keep: list[str] = []
    for name in sorted(matrix):
        adapter_cls = ADAPTERS.get(name)
        if adapter_cls is None:
            keep.append(name)  # main l'annonce en [ skip ]
            continue
        out_dir = adapter_cls.out_dir
        held = chosen.get(out_dir)
        if held is None or rank(name, out_dir) > rank(held, out_dir):
            chosen[out_dir] = name
    return sorted(keep + list(chosen.values()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile .sdda/ vers les façades des harnais.")
    parser.add_argument("--harness", help="un seul harnais (défaut : tous ceux de la matrice)")
    parser.add_argument("--check", action="store_true", help="CI : ne rien écrire, échouer si dérive")
    parser.add_argument("--impact-only", action="store_true", help="n'imprimer que les rapports d'impact")
    parser.add_argument("--prune", action="store_true", help="supprimer les orphelins des façades")
    args = parser.parse_args()

    matrix = load_matrix()
    targets = [args.harness] if args.harness else default_targets(matrix)
    unknown = [t for t in targets if t not in matrix]
    if unknown:
        print("ERROR: harness_build — harnais inconnu")
        print(f"CAUSE: [HARNESS_UNKNOWN] {', '.join(unknown)} absent(s) de capability-matrix.yml")
        print("FIX: déclarer le harnais dans .sdda/capability-matrix.yml")
        return 1

    failed = False
    print()
    for name in targets:
        harness = matrix[name]
        if name not in ADAPTERS:
            print(f"  [ skip ] {name:<14} aucun adaptateur (déclaré dans la matrice, pas encore implémenté)")
            continue

        plan, counts = build_harness(name, harness)
        adapter = ADAPTERS[name]

        if args.impact_only:
            print(impact_report(harness, plan.ported))
            continue

        if args.check:
            diverged = drift(plan)
            stale = orphans(plan, adapter)
            if diverged or stale:
                failed = True
                print(f"  [ FAIL ] {name:<14} {len(diverged)} fichier(s) divergent(s), {len(stale)} orphelin(s)")
                for item in (diverged + stale)[:6]:
                    print(f"           {item}")
            else:
                print(f"  [  ok  ] {name:<14} façade à jour "
                      f"({counts['agents']} agents, {counts['commands']} commandes)")
            continue

        written = write_plan(plan)
        stale = orphans(plan, adapter)
        if stale and args.prune:
            for rel in stale:
                (ROOT / rel).unlink()
            # Un répertoire de façade vidé (`.codex/prompts/`, ancienne façade)
            # disparaît avec son dernier fichier, pour ne pas laisser croire
            # qu'un harnais y lit encore quelque chose.
            for rel_dir, _generated_only in adapter.managed:
                for directory in sorted((ROOT / rel_dir).glob("**/"), key=lambda p: len(p.parts), reverse=True):
                    if directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
        level = harness.protection_level
        suffix = "" if harness.status == "reference" else "  (compilable, non validé)"
        print(f"  [ build ] {name:<14} {counts['agents']} agents · {counts['commands']} commandes "
              f"· {written} fichier(s) écrit(s) · niveau {level}{suffix}")
        if stale:
            action = "supprimé(s)" if args.prune else "à supprimer (--prune)"
            print(f"            {len(stale)} orphelin(s) {action}")
        for note in BUILD_NOTES.get(name, []):
            print(f"            ⚠  {note}")

    if args.check and failed:
        print()
        print("ERROR: harness_build — les façades ont dérivé de la source")
        print("CAUSE: [HARNESS_PARITY_DRIFT] une façade a été éditée à la main, ou la source a bougé")
        print("FIX: python .sdda/sdda.py harness-build --prune")
        return 1

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

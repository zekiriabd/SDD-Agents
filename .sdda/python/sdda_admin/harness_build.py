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
        ├─► .claude/   agents/ · commands/ · CLAUDE.md · settings.json
        ├─► .codex/    prompts/ · AGENTS.md · config.toml
        └─► .gemini/   commands/*.toml · GEMINI.md · settings.json

**Les façades sont générées, jamais éditées.** Une modification directe dans
`.claude/` est écrasée au build suivant, et le test de parité la détecte.

**Le rapport d'impact est obligatoire.** Un harnais sans hooks au runtime ne
perd pas ses invariants : ils se DÉPLACENT vers le CI. Le prétendre appliqué
au runtime serait exactement le doc-theater que `INVARIANTS.yml` existe pour
empêcher — cf. `docs/MULTI-HARNESS.md` §3.

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
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, yaml_mini  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()

SDDA = Path(__file__).resolve().parents[2]
ROOT = SDDA.parent

GENERATED_BANNER = (
    "<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis {source}.\n"
    "     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,\n"
    "     et le test de parité la signale. Éditer la source. -->\n"
)


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


def rewrite_refs(text: str, harness: Harness) -> str:
    """Adapte les `@`-refs à ce que le harnais sait faire.

    `at_include: native`   -> on laisse : le harnais charge le fichier à la demande.
    sinon                  -> `@.sdda/x.md` devient « Read .sdda/x.md avant ce STEP ».

    Le repli est verbeux à dessein : une référence muette sur un harnais qui ne
    la résout pas produirait un agent qui croit avoir lu une règle qu'il n'a
    jamais vue — un pack manquant à l'échelle d'une règle.
    """
    if harness.supports("at_include"):
        return text
    return AT_REF_RE.sub(lambda m: f"`{m.group(1)}` (Read ce fichier avant de poursuivre)", text)


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
    """Une valeur de frontmatter que Claude Code relit en YAML strict.

    `yaml_mini` relit la source avec tolérance ; Claude Code, non. Une
    description qui porte `Profile: poc` ou ` # ` écrite sans guillemets est un
    en-tête invalide, et le harnais ÉCARTE l'agent sans rien dire :
    `Agent type 'dev-app' not found`, redémarrage ou pas. Une liste noire de
    caractères aurait toujours un retard sur le parseur ; la règle est donc
    inverse : seul un identifiant nu reste nu, tout le reste est émis entre
    guillemets doubles (JSON est du YAML valide, et `json.loads` suffit à le
    vérifier sans PyYAML — c'est ce que le test de parité relit).
    """
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    text = value if isinstance(value, str) else str(value)
    if YAML_PLAIN_RE.match(text) and text.lower() not in YAML_RETYPED:
        return text
    return json.dumps(text, ensure_ascii=False)


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
# Adaptateurs
# ---------------------------------------------------------------------------
# Avertissements collectés pendant un build, par harnais. Rapportés par main
# après la ligne de build, pour qu'une dégradation de façade ne soit jamais
# silencieuse.
BUILD_NOTES: dict[str, list[str]] = {}

#: Le fichier mémoire des harnais (`.claude/CLAUDE.md`, `.codex/AGENTS.md`,
#: `.gemini/GEMINI.md`) est compilé depuis le jumeau FRANÇAIS de l'architecture.
#: La documentation est en anglais par défaut (`ARCHITECTURE.md`), mais le
#: fichier mémoire est lu par les Developer Agents avec leurs prompts, qui sont
#: en français : leur servir l'architecture dans une autre langue que leurs
#: fiches, c'est deux vocabulaires pour une même règle. Si le jumeau manque, on
#: compile l'anglais plutôt que rien — et le build le dit.
MEMORY_SOURCE_FR = "ARCHITECTURE.fr.md"
MEMORY_SOURCE_FALLBACK = "ARCHITECTURE.md"


def memory_source(sdda: Path | None = None) -> Path:
    """La source du fichier mémoire : `ARCHITECTURE.fr.md`, sinon `ARCHITECTURE.md`."""
    base = sdda or SDDA
    preferred = base / MEMORY_SOURCE_FR
    return preferred if preferred.is_file() else base / MEMORY_SOURCE_FALLBACK


@dataclass
class BuildPlan:
    """Ce qu'un build produit : chemin -> contenu. Rien n'est écrit avant que
    tout soit calculé, pour qu'un échec ne laisse pas une façade à moitié
    régénérée."""

    files: dict[Path, str] = field(default_factory=dict)

    def add(self, path: Path, content: str) -> None:
        self.files[path] = content


class Adapter:
    """Socle commun. Une sous-classe ne redéfinit que ce qui diffère."""

    out_dir: str = ""

    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    # -- agents ------------------------------------------------------------
    def emit_agents(self, plan: BuildPlan, out: Path) -> int:
        count = 0
        for src in sorted((SDDA / "agents").glob("*.md")):
            meta, body = frontmatter_and_body(src.read_text(encoding="utf-8"))
            plan.add(out / "agents" / src.name, self.render_agent(src, meta, strip_sync_markers(body)))
            count += 1
        return count

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
    def emit_memory_file(self, plan: BuildPlan, out: Path) -> None:
        # Le fichier mémoire de chaque harnais EST l'architecture : une seule
        # source, pas de « corps d'entrée » optionnel qu'aucun dépôt n'a jamais eu.
        source = memory_source()
        if source.name != MEMORY_SOURCE_FR:
            BUILD_NOTES.setdefault(self.harness.name, []).append(
                f".sdda/{MEMORY_SOURCE_FR} absent — fichier mémoire compilé depuis "
                f".sdda/{source.name} (anglais), alors que les prompts sont en français"
            )
        text = rewrite_refs(strip_sync_markers(source.read_text(encoding="utf-8")), self.harness)
        plan.add(
            out / self.harness.memory_file,
            GENERATED_BANNER.format(source=f".sdda/{source.name}") + "\n" + text,
        )

    def emit_settings(self, plan: BuildPlan, out: Path) -> None:
        """Par défaut : rien. Seul Claude Code câble des hooks bloquants."""

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


def hook_command(script: str) -> str:
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

    Le shell est celui du harnais (Git Bash sous Windows, sh ailleurs) : la
    syntaxe reste POSIX. Le chemin est ancré sur `$CLAUDE_PROJECT_DIR`, entre
    guillemets (cf. `emit_settings`). `hooks-selfcheck` exécute chaque commande
    ainsi générée avec un payload inoffensif et un payload à refuser.
    """
    name = script.rsplit("/", 1)[-1]
    return (
        f'${{SDDA_PYTHON:-python}} "$CLAUDE_PROJECT_DIR/{script}"; rc=$?; '
        f'if [ $rc -ne 0 ] && [ $rc -ne 2 ]; then '
        # Message ASCII : il traverse un shell dont l'encodage de stderr n'est
        # pas garanti, au moment précis où il doit être lu.
        f'echo "[hook] {name} ne demarre pas (code $rc) - interpreteur: SDDA_PYTHON=${{SDDA_PYTHON:-python}}" >&2; '
        f'if [ "${{SDDA_HOOKS_STRICT:-0}}" = "1" ]; then exit 2; fi; fi; exit $rc'
    )


class ClaudeAdapter(Adapter):
    """Harnais de référence (niveau A) : tout est natif."""

    out_dir = ".claude"

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
            selector = self.harness.model_for(meta.get("model_tier", meta.get("tier_default", "")))
            if selector:
                meta["model"] = selector
        front = "---\n" + "\n".join(f"{k}: {yaml_scalar(v)}" for k, v in meta.items()) + "\n---\n"
        return front + GENERATED_BANNER.format(source=f".sdda/agents/{src.name}") + "\n" + body

    def render_command(self, plan, out, src, meta, body) -> None:
        front = (
            f"---\nname: {yaml_scalar(meta.get('name', src.stem))}\n"
            f"description: {yaml_scalar(meta.get('description', ''))}\n---\n"
        )
        plan.add(
            out / "commands" / src.name,
            front + GENERATED_BANNER.format(source=f".sdda/commands/{src.name}") + "\n" + body,
        )

    def emit_settings(self, plan, out) -> None:
        """Les hooks bloquants — ce qui fait le niveau A.

        Ils s'exécutent AU MOMENT de l'action. Sur les autres harnais, les
        mêmes invariants basculent en contrôles CI : plus tard, après que le
        travail a été fait sur une base fausse.

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
            rel = f".sdda/python/sdda_hooks/{module}.py"
            if wiring is None:
                undeclared.append(module)
                continue
            by_slot.setdefault((wiring["event"], wiring["matcher"]), []).append(rel)

        # Ordre stable : l'événement, puis le matcher, puis le nom du script.
        # Un `settings.json` dont l'ordre bouge à chaque build ferait échouer
        # `--check` sans qu'aucune source ait changé.
        # Le chemin est ancré sur la RACINE DU PROJET, pas sur le répertoire
        # courant.
        #
        # Un chemin de hook écrit en relatif se résout contre le cwd du
        # harnais. Il suffit qu'une commande entre dans un sous-répertoire —
        # une fixture, `workspace/src/`, n'importe quel `cd` — pour que le
        # chemin ne désigne plus rien. Le hook ne s'exécute alors pas, et
        # l'effet dépend du harnais : au mieux il laisse passer en silence,
        # c'est-à-dire que les quatorze enforcers disparaissent sans un mot ;
        # au pire il rend une erreur qui BLOQUE l'outil, et plus aucune
        # commande ne passe tant que le cwd n'est pas revenu.
        #
        # Les deux comportements ont été observés. Le second est spectaculaire
        # et se corrige tout seul ; le premier est celui qui coûte cher, parce
        # qu'il ressemble exactement à un pipeline dont tous les contrôles sont
        # verts.
        #
        # `$CLAUDE_PROJECT_DIR` est la variable que le harnais pose pour cet
        # usage précis. Les guillemets sont obligatoires : un chemin de projet
        # sous Windows contient des espaces bien plus souvent qu'ailleurs.
        for (event, matcher), scripts in sorted(by_slot.items()):
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": hook_command(s)} for s in sorted(scripts)],
            })

        if undeclared:
            BUILD_NOTES.setdefault(self.harness.name, []).append(
                f"{len(undeclared)} hook(s) sans WIRING — non câblé(s) : " + ", ".join(sorted(undeclared))
            )
        settings = {"permissions": {"deny": list(SECRET_READ_DENY)}, "hooks": hooks}
        plan.add(out / "settings.json", json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


class CodexAdapter(Adapter):
    """Niveau B : sous-agents émulés, hooks reportés au CI."""

    out_dir = ".codex"

    def render_agent(self, src, meta, body) -> str:
        # Pas de frontmatter exploité : l'identité et le tier sont rappelés en
        # tête du corps, sinon le wrapper ne saurait pas quel modèle employer.
        header = (
            f"# Agent `{meta.get('name', src.stem)}`\n\n"
            f"- Tier : `{meta.get('model_tier', 'balanced')}` "
            f"(plancher `{meta.get('tier_floor', '?')}`, plafond `{meta.get('tier_ceiling', '?')}`)\n"
            f"- Outils autorisés : {meta.get('tools', '—')}\n\n"
        )
        return (
            GENERATED_BANNER.format(source=f".sdda/agents/{src.name}")
            + "\n" + header + rewrite_refs(body, self.harness)
        )

    def render_command(self, plan, out, src, meta, body) -> None:
        plan.add(
            out / "prompts" / src.name,
            GENERATED_BANNER.format(source=f".sdda/commands/{src.name}")
            + f"\n# /{meta.get('name', src.stem)}\n\n"
            + rewrite_refs(body, self.harness),
        )


class GeminiAdapter(Adapter):
    """Niveau B : commandes natives en TOML, hooks reportés au CI."""

    out_dir = ".gemini"

    def render_agent(self, src, meta, body) -> str:
        return CodexAdapter.render_agent(self, src, meta, body)  # même dégradation

    def render_command(self, plan, out, src, meta, body) -> None:
        # TOML : le corps va dans un littéral multi-ligne. On échappe la
        # séquence fermante plutôt que de tronquer silencieusement.
        prompt = rewrite_refs(body, self.harness).replace("'''", "\\'\\'\\'")
        description = str(meta.get("description", "")).replace('"', '\\"')
        plan.add(
            out / "commands" / f"{src.stem}.toml",
            f"# GÉNÉRÉ depuis .sdda/commands/{src.name} — ne pas éditer ici.\n"
            f'description = "{description}"\n'
            f"prompt = '''\n{prompt}\n'''\n",
        )

    def emit_agents(self, plan, out) -> int:
        # Gemini CLI n'a pas de répertoire d'agents : ils sont inlinés dans les
        # prompts par le wrapper. On les dépose quand même pour que le wrapper
        # les trouve, sous un nom qui dit qu'ils ne sont pas auto-chargés.
        count = 0
        for src in sorted((SDDA / "agents").glob("*.md")):
            meta, body = frontmatter_and_body(src.read_text(encoding="utf-8"))
            plan.add(out / "agents-inline" / src.name, self.render_agent(src, meta, body))
            count += 1
        return count


ADAPTERS: dict[str, type[Adapter]] = {
    "claude-code": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini-cli": GeminiAdapter,
    "antigravity": GeminiAdapter,
}


# ---------------------------------------------------------------------------
# Rapport d'impact — obligatoire
# ---------------------------------------------------------------------------
def invariants_by_enforcer_kind() -> dict[str, list[str]]:
    """Invariants dont TOUS les enforcers sont des hooks runtime.

    Ce sont eux qui basculent en CI sur un harnais sans hooks. Les nommer est
    le cœur du rapport d'impact : dire « niveau B » sans dire lesquels ne
    renseigne personne.
    """
    # Lu par `yaml_mini`, comme `framework_smoke` et le test du manifeste : une
    # regex sur l'indentation cassait dès qu'un invariant ou un enforcer
    # changeait de forme, et le rapport d'impact disait alors « aucun
    # invariant déplacé » sur un manifeste qu'il n'avait pas lu.
    data = yaml_mini.parse((SDDA / "INVARIANTS.yml").read_text(encoding="utf-8"))
    hook_only: list[str] = []
    mixed: list[str] = []
    for inv in (data.get("invariants") or []) if isinstance(data, dict) else []:
        if not isinstance(inv, dict):
            continue
        iid = str(inv.get("id") or "").strip()
        enforcers = [str(e) for e in (inv.get("enforcers") or []) if str(e).startswith(".sdda/python/")]
        if not iid or not enforcers:
            continue
        hooks = [e for e in enforcers if "sdda_hooks/" in e]
        if hooks and len(hooks) == len(enforcers):
            hook_only.append(iid)
        elif hooks:
            mixed.append(iid)
    return {"hook_only": hook_only, "mixed": mixed}


def impact_report(harness: Harness) -> str:
    kinds = invariants_by_enforcer_kind()
    runtime_hooks = harness.mechanisms.get("runtime_hooks", "unsupported")
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
        "emulated": "dégradation contrôlée via wrapper",
        "ci_fallback": "**reporté au CI** — appliqué plus tard, pas au moment de l'action",
        "unsupported": "**absent** — repli documenté",
    }
    for mechanism, support in sorted(harness.mechanisms.items()):
        lines.append(f"| `{mechanism}` | {support} | {consequences.get(support, '?')} |")

    lines += ["", "## Invariants déplacés vers le CI", ""]
    if runtime_hooks == "native":
        lines.append("Aucun : tous les invariants s'appliquent **au moment de l'action**.")
    else:
        lines += [
            f"`runtime_hooks: {runtime_hooks}` — les invariants suivants ne sont plus",
            "appliqués au moment de l'action mais **en différé (CI)** :",
            "",
        ]
        for iid in kinds["hook_only"]:
            lines.append(f"- `{iid}` — **appliqué en différé (CI)**")
        for iid in kinds["mixed"]:
            lines.append(f"- `{iid}` — partiellement différé (un enforcer déterministe subsiste)")
        lines += [
            "",
            "> **À dire clairement** : entre deux exécutions du wrapper, rien n'empêche",
            "> une écriture hors scope. Le CI la rattrape — après que le travail a été",
            "> fait sur une base fausse.",
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
# Pointeur racine — Codex CLI lit `AGENTS.md`, Gemini CLI `GEMINI.md`, à la
# RACINE du dépôt, jamais dans `.codex/` ou `.gemini/`. Sans pointeur, la façade
# compilée existe et aucun des deux harnais ne l'ouvre. Claude Code lit
# `.claude/CLAUDE.md` nativement : pas de pointeur.
# ---------------------------------------------------------------------------
ROOT_POINTER_DIRS = {".codex": "Codex CLI", ".gemini": "Gemini CLI"}


def root_pointer(harness: Harness, out_dir: str) -> str:
    """Contenu du fichier mémoire racine : un renvoi, pas une copie.

    Ne dépend que du répertoire de la façade, pas du harnais : `gemini-cli` et
    `antigravity` partagent `.gemini/` et doivent produire le même pointeur,
    sinon `--check` échoue sur celui qui n'a pas été construit en dernier.
    """
    label = ROOT_POINTER_DIRS[out_dir]
    commands = "prompts/*.md" if out_dir == ".codex" else "commands/*.toml"
    agents = "agents/" if out_dir == ".codex" else "agents-inline/"
    return (
        GENERATED_BANNER.format(source=".sdda/capability-matrix.yml")
        + f"\n# SDD_Agents — {label} (expérimental)\n\n"
        f"Les instructions du framework sont dans `{out_dir}/{harness.memory_file}` :\n"
        "**le lire en entier avant toute action.** Commandes :\n"
        f"`{out_dir}/{commands}`. Agents : `{out_dir}/{agents}`.\n\n"
        f"**Statut : expérimental.** La façade {label} est compilable, jamais\n"
        "validée par un run de conformance, et n'a\n"
        "**aucune gate bloquante au runtime** : les hooks d'ownership et de gates\n"
        "n'existent que sous Claude Code. Ce qu'ils appliquent est reporté au CI\n"
        "et aux scripts déterministes — une écriture hors scope n'est rattrapée\n"
        "qu'après coup. Détail :\n"
        f"`{out_dir}/harness-impact.md`, `.sdda/docs/MULTI-HARNESS.md`.\n\n"
        "Avant de considérer un travail terminé : `python .sdda/sdda.py framework-smoke`.\n"
    )


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
    plan.add(ROOT / adapter.out_dir / "harness-impact.md", impact_report(harness))
    if adapter.out_dir in ROOT_POINTER_DIRS:
        plan.add(ROOT / harness.memory_file, root_pointer(harness, adapter.out_dir))
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


def orphans(plan: BuildPlan, out_dir: Path) -> list[str]:
    """Fichiers de la façade que la source ne produit plus.

    Un agent retiré de `.sdda/` qui survit dans `.claude/` reste invocable —
    c'est une commande fantôme qui référence des fichiers disparus.
    """
    if not out_dir.is_dir():
        return []
    expected = set(plan.files)
    found = []
    for sub in ("agents", "commands", "prompts", "agents-inline"):
        directory = out_dir / sub
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path not in expected:
                found.append(path.relative_to(ROOT).as_posix())
    return sorted(found)


def default_targets(matrix: dict[str, Harness]) -> list[str]:
    """Les harnais à construire quand aucun n'est nommé.

    Deux harnais peuvent partager un `out_dir` — `antigravity` suit les
    conventions de Gemini (`memory_file: GEMINI.md`, donc `.gemini/`). Les
    construire tous deux fait que le second écrase le `harness-impact.md` du
    premier, et `--check` échoue alors à tout coup sur celui qui a perdu : le
    dispositif anti-dérive devient un faux rouge permanent, donc du bruit
    qu'on apprend à ignorer.

    On ne garde donc qu'un harnais par répertoire : celui qui n'est pas
    `planned`, sinon celui dont le répertoire porte le nom — `.gemini/`
    appartient à `gemini-cli`, `antigravity` n'y est qu'invité.
    `--harness antigravity` reste possible, et explicite.
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
        print(f"ERROR: harness_build — harnais inconnu")
        print(f"CAUSE: [HARNESS_UNKNOWN] {', '.join(unknown)} absent(s) de capability-matrix.yml")
        print(f"FIX: déclarer le harnais dans .sdda/capability-matrix.yml")
        return 1

    failed = False
    print()
    for name in targets:
        harness = matrix[name]
        if name not in ADAPTERS:
            print(f"  [ skip ] {name:<14} aucun adaptateur (déclaré dans la matrice, pas encore implémenté)")
            continue

        plan, counts = build_harness(name, harness)
        out_dir = ROOT / ADAPTERS[name].out_dir

        if args.impact_only:
            print(impact_report(harness))
            continue

        if args.check:
            diverged = drift(plan)
            stale = orphans(plan, out_dir)
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
        stale = orphans(plan, out_dir)
        if stale and args.prune:
            for rel in stale:
                (ROOT / rel).unlink()
        level = harness.protection_level
        note = "" if harness.status == "reference" else "  (compilable, non validé)"
        print(f"  [ build ] {name:<14} {counts['agents']} agents · {counts['commands']} commandes "
              f"· {written} fichier(s) écrit(s) · niveau {level}{note}")
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

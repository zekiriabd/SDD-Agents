#!/usr/bin/env python3
"""
harness_build — compile `.sdda/` vers les façades des harnais.

C'est **la jonction** : sans elle, les 28 agents et les 10 commandes de
`.sdda/` ne sont visibles d'aucun harnais et ne s'exécutent jamais.

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
    python .sdda/python/sdda_admin/harness_build.py                 # tous
    python .sdda/python/sdda_admin/harness_build.py --harness claude-code
    python .sdda/python/sdda_admin/harness_build.py --check         # CI : dérive ?
    python .sdda/python/sdda_admin/harness_build.py --impact-only

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

    def supports(self, mechanism: str) -> bool:
        return self.mechanisms.get(mechanism) == "native"


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


def frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml_mini.parse_mapping(parts[1])
    except Exception:
        meta = {}
    return meta, parts[2].lstrip("\n")


# ---------------------------------------------------------------------------
# Adaptateurs
# ---------------------------------------------------------------------------
# Avertissements collectés pendant un build, par harnais. Rapportés par main
# après la ligne de build, pour qu'une dégradation de façade ne soit jamais
# silencieuse.
BUILD_NOTES: dict[str, list[str]] = {}


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
        body = (SDDA / "entrypoint-body.md")
        source = body if body.is_file() else (SDDA / "ARCHITECTURE.md")
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


class ClaudeAdapter(Adapter):
    """Harnais de référence (niveau A) : tout est natif."""

    out_dir = ".claude"

    def render_agent(self, src, meta, body) -> str:
        # Claude Code lit le frontmatter tel quel ; on le conserve verbatim et
        # on n'ajoute que la bannière, en commentaire HTML après le bloc.
        front = "---\n" + "\n".join(
            f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v}"
            for k, v in meta.items()
        ) + "\n---\n"
        return front + GENERATED_BANNER.format(source=f".sdda/agents/{src.name}") + "\n" + body

    def render_command(self, plan, out, src, meta, body) -> None:
        front = f"---\nname: {meta.get('name', src.stem)}\ndescription: {meta.get('description', '')}\n---\n"
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
        for (event, matcher), scripts in sorted(by_slot.items()):
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": f"python {s}"} for s in sorted(scripts)],
            })

        if undeclared:
            BUILD_NOTES.setdefault(self.harness.name, []).append(
                f"{len(undeclared)} hook(s) sans WIRING — non câblé(s) : " + ", ".join(sorted(undeclared))
            )
        plan.add(out / "settings.json", json.dumps({"hooks": hooks}, indent=2, ensure_ascii=False) + "\n")


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
    text = (SDDA / "INVARIANTS.yml").read_text(encoding="utf-8")
    hook_only: list[str] = []
    mixed: list[str] = []
    for block in re.split(r"\n  - id: ", text)[1:]:
        iid = block.split("\n")[0].strip()
        enforcers = re.findall(r"^\s+- (\.sdda/python/[\w\-./]+\.py)", block, re.M)
        if not enforcers:
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
        print("FIX: python .sdda/python/sdda_admin/harness_build.py --prune")
        return 1

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

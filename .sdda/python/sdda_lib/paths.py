"""Résolution des chemins du framework et du workspace.

Racine de projet = le dossier qui contient `workspace/` (et en général `.sdda/`).
Le framework lui-même (`.sdda/`) est localisé depuis ce fichier, ce qui permet
aux scripts de tourner sur un workspace de test qui n'embarque pas `.sdda/`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# .sdda/python/sdda_lib/paths.py -> parents[2] == .sdda
FRAMEWORK_SDDA_DIR: Path = Path(__file__).resolve().parents[2]


def find_root(start: Path | None = None) -> Path:
    """Remonte depuis `start` (défaut : cwd) jusqu'à un dossier contenant `workspace/`.

    Repli : le dossier contenant `.sdda/`, sinon `start` lui-même.
    """
    cur = (start or Path(os.getcwd())).resolve()
    candidates = [cur, *cur.parents]
    for d in candidates:
        if (d / "workspace").is_dir():
            return d
    for d in candidates:
        if (d / ".sdda").is_dir():
            return d
    return cur


def workspace(root: Path) -> Path:
    return root / "workspace"


#: Le workspace sépare ce que l'HUMAIN fournit de ce que le FRAMEWORK produit.
#:
#: La v5 rangeait côte à côte, sous `feats/` et `proof/`, des fichiers que
#: l'humain écrit (brief, roster, vérité terrain) et d'autres que le pipeline
#: génère (MISSION, CAPs, contrats, jeux, suites). Un lecteur ne pouvait pas
#: savoir, en ouvrant `feats/`, ce qu'il devait remplir et ce qu'il devait
#: laisser au framework. La v6 le dit par l'arbre :
#:
#:   CE QUE L'HUMAIN FOURNIT
#:   stack/     `STACK.md`, seul : les choix techniques, des NOMS de variables.
#:   feats/     ses spécifications en Markdown, à plat : le brief
#:              `{n}-{Name}.md` et le roster `{n}-roster.md`. Rien d'autre.
#:   assets/    les données (exports, corpus) — racine des stores `kind: local` —
#:              et `.env`, les VALEURS des secrets de l'application générée.
#:   seed/      la vérité terrain : scénarios annotés, labels.
#:
#:   CE QUE LE FRAMEWORK PRODUIT
#:   pipeline/  tout ce que le pipeline génère pour préparer, concevoir et
#:              prouver : missions/, caps/, topology/, contracts/, decisions/,
#:              datasets/, suites/, baselines/, calibration/, fixtures/.
#:   src/       l'application générée, `.env` copié depuis `assets/` compris.
#:   .sys/      l'état interne et les sorties de run — effaçable.
#:
#: La frontière qui ne souffre aucune exception reste celle du JUGEMENT :
#: aucun `dev-*` n'écrit sous `pipeline/datasets|suites|baselines|calibration/`.
#: Elle tient aux zones de la matrice d'ownership, pas au nom du parent.
FEATS = "feats"
SEED = "seed"
ASSETS = "assets"
PIPELINE = "pipeline"


def feats_dir(root: Path) -> Path:
    """`workspace/feats/` : les spécifications de l'HUMAIN, en Markdown, à plat."""
    return workspace(root) / FEATS


def pipeline_dir(root: Path) -> Path:
    """`workspace/pipeline/` : tout ce que le framework génère avant et autour du code."""
    return workspace(root) / PIPELINE


def assets_dir(root: Path) -> Path:
    return workspace(root) / ASSETS


def missions_dir(root: Path) -> Path:
    return pipeline_dir(root) / "missions"


def caps_dir(root: Path) -> Path:
    return pipeline_dir(root) / "caps"


def topology_dir(root: Path) -> Path:
    return pipeline_dir(root) / "topology"


def roster_path(root: Path, mission_number: int | str) -> Path:
    """Le roster déclaré par l'architecte HUMAIN : `feats/{n}-roster.md`.

    Un fichier Markdown dont le premier bloc ```yaml est la déclaration :
    combien d'agents, lesquels, qui porte quelle CAP (P7). Il a vécu à côté de
    la topologie que l'agent écrit (`pipeline/topology/`) — deux owners dans un
    même répertoire, et l'humain ne savait pas lequel des deux fichiers était
    le sien. Il est désormais avec ses autres spécifications. `architect-topology`
    le lit, ne l'écrit jamais (`loader.yml`).
    """
    return feats_dir(root) / f"{mission_number}-roster.md"


def contracts_dir(root: Path, kind: str) -> Path:
    """kind ∈ {agents, tools, retrieval, memory}."""
    return pipeline_dir(root) / "contracts" / kind


def decisions_dir(root: Path) -> Path:
    """Les ADR. UN seul endroit : `pipeline/decisions/`.

    Ils vivaient à deux : `docs/adr/` que citait le gabarit, et
    `.sys/.context/adrs/` que déclarait la matrice d'ownership. Le script des
    tâches humaines cherchait dans les deux — c'est-à-dire que la question
    « cet ADR a-t-il été écrit ? » avait deux réponses possibles, et que celle
    qui gouvernait était celle que personne ne relisait.
    """
    return pipeline_dir(root) / "decisions"


def prompts_dir(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}/prompts/` : un prompt système est un actif d'EXÉCUTION, il part AVEC l'application.

    Il a vécu dans `workspace/src/prompts/`, à côté du répertoire de
    l'application et non dedans : « sous `src/` » avait été lu comme « avec le
    code », mais un exécutable ou un conteneur bâti depuis `src/{App}/` partait
    sans ses prompts — exactement ce que le déplacement hors de `feats/` voulait
    éviter. Dans le paquet, le chargeur le trouve par un chemin relatif à
    lui-même, et la roue l'embarque.

    Ce que l'ownership protégeait reste protégé : `dev-agent` ne réécrit pas le
    prompt qu'il implémente. Cela tient à une zone interdite de la matrice
    (`workspace/src/*/prompts/**`), pas à un répertoire de premier niveau.
    """
    return app_dir(root, app_name) / "prompts"


def skills_dir(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}/skills/` : un fichier par compétence déclarée au roster.

    Une skill dit ce que l'agent SAIT FAIRE. Son texte détaillé (quand elle
    s'applique, ce qu'elle produit, ce qui prouve qu'elle a joué) vit ici, un
    Markdown par slug ; le prompt système de l'agent la cite sous
    `## Compétences`. Le fragment est la matière, le prompt est l'exécutable
    hashé. Owner : `dev-prompt`.
    """
    return app_dir(root, app_name) / "skills"


def rules_dir(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}/rules/` : un fichier par règle de comportement déclarée au roster.

    Jumelle de la skill : une rule dit ce que l'agent DOIT ou NE DOIT JAMAIS
    faire. Même mécanique — fragment ici, citation sous `## Règles` du prompt.
    Owner : `dev-prompt`.
    """
    return app_dir(root, app_name) / "rules"


def datasets_dir(root: Path, kind: str | None = None) -> Path:
    base = pipeline_dir(root) / "datasets"
    return base / kind if kind else base


def suites_dir(root: Path) -> Path:
    return pipeline_dir(root) / "suites"


def baselines_dir(root: Path) -> Path:
    return pipeline_dir(root) / "baselines"


def calibration_dir(root: Path) -> Path:
    return pipeline_dir(root) / "calibration"


def reports_dir(root: Path) -> Path:
    """Les rapports d'eval. Sous `.sys/` parce qu'ils sont une SORTIE DE RUN.

    Ils grossissent à chaque exécution et se régénèrent intégralement : les
    ranger à côté des jeux et des baselines mettait au même rang ce qui juge
    (et qu'on ne peut pas perdre) et ce qui est jugé (et qu'on jette).
    """
    return workspace(root) / ".sys" / "reports"


def traces_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / "traces" / "runs"


def ir_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".ir"


def ir_path(root: Path, mission_number: int | str) -> Path:
    return ir_dir(root) / f"{mission_number}-system.ir.json"


def validation_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".validation"


def state_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".state"


def audit_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".audit"


def stack_dir(root: Path) -> Path:
    return workspace(root) / "stack"


def stack_md_path(root: Path) -> Path:
    return stack_dir(root) / "STACK.md"


#: Un nom d'application : il devient un segment de chemin, jamais plus.
APP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def app_dir(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}` : la racine du LIVRABLE — projet, build, `.env`.

    Refuse un nom qui n'est pas un segment (`..`, `/`, `\\`) : dernier filet
    derrière `layered_config.app_name`, pour les appelants qui passent un nom
    venu d'ailleurs (argument, IR).
    """
    if not APP_NAME_RE.match(str(app_name)):
        raise ValueError(f"nom d'application invalide pour un chemin : {app_name!r}")
    return workspace(root) / "src" / app_name


def env_rel(app_name: str) -> str:
    """Le chemin de `.env` tel qu'on l'écrit dans un message : `workspace/src/{App}/.env`."""
    return f"workspace/src/{app_name}/.env"


#: Là où l'HUMAIN dépose les valeurs des secrets. `install-env` les copie dans
#: `src/{App}/.env`, avec l'application qui les consomme.
ENV_SOURCE_REL = "workspace/assets/.env"


def env_source_path(root: Path) -> Path:
    """`workspace/assets/.env` : le fichier de secrets que l'humain fournit.

    L'humain dépose ses entrées à trois endroits — `stack/`, `feats/`,
    `assets/` — et non dans l'arbre généré. Le runtime, lui, lit `src/{App}/.env`
    (l'application part de là en exécutable ou en conteneur) : `install-env`
    fait la copie, sans LLM. Aucun agent ne lit l'un ou l'autre fichier
    (`audit_ownership.is_secret_file`, appliqué par les hooks de lecture).
    """
    return workspace(root) / ENV_SOURCE_REL.split("/", 1)[1]


def env_path(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}/.env` : les VALEURS des secrets, gitignoré.

    Le fichier vit avec l'application, pas à la racine du dépôt : c'est
    l'application générée qui consomme la clé (Runtime Models, §6), et c'est
    depuis `workspace/src/{App}/` qu'elle part en exécutable ou en conteneur.
    Un `.env` à la racine du dépôt restait hors du livrable — et SDD_Pro l'a
    retiré pour cette raison (`workspace/src/*/.env` dans son .gitignore). Le
    harnais de CONSTRUCTION, lui, ne lit jamais ce fichier : il paie ses
    tokens avec son propre compte.

    STACK.md n'en porte que les noms (`${LLM_API_KEY}`), et c'est ce qui le
    rend versionnable. Le même fichier sert aux stores de `declared-sources`
    (`SourceSecretsFile`, relatif à ce répertoire) et au code généré
    (`config.py`, qui le charge au démarrage sans écraser l'environnement).
    """
    return app_dir(root, app_name) / ".env"


def app_src_root(root: Path, app_name: str) -> Path:
    """La racine du paquet applicatif généré : `workspace/src/{App}` — la même que le projet.

    Layout PLAT, celui de SDD_Pro (`workspace/src/{Backend}/main.py`,
    `endpoints/`, `services/`) : le répertoire de l'application EST le paquet
    Python. `pyproject.toml`, `.env`, `README.md` à sa racine ; `agents/`,
    `tools/`, `prompts/`, `data/`, `orchestration/`, `serving/`, `app/` en
    dessous, un seul niveau. Le « src layout » de uv (`{App}/src/{App}/`)
    doublait le nom du projet et cachait l'application deux répertoires plus
    bas — le premier lecteur du premier workspace réel n'a pas trouvé le code.
    Le paquet reste installable : `pyproject.toml` (hatchling) réécrit la racine
    en `{App}/` dans la roue, et `tests/` en est exclu.

    Une seule fonction pour cet endroit : le générateur de schémas, celui
    d'outils, le squelette, le validateur de sources et la migration la lisent
    tous ici. Trois conventions coexistaient (fiches à plat sous `src/`,
    ownership à profondeur libre, générateurs en src layout) : c'est ce qui a
    produit l'arbre doublé.
    """
    return app_dir(root, app_name)


def base_config_path(root: Path) -> Path:
    """`.sdda/config.base.yml` du projet, sinon celui du framework."""
    local = root / ".sdda" / "config.base.yml"
    return local if local.is_file() else FRAMEWORK_SDDA_DIR / "config.base.yml"


def project_config_schema_path(root: Path) -> Path:
    local = root / ".sdda" / "templates" / "project-config.schema.json"
    if local.is_file():
        return local
    return FRAMEWORK_SDDA_DIR / "templates" / "project-config.schema.json"


def ir_schema_path(root: Path | None = None) -> Path:
    if root is not None:
        local = root / ".sdda" / "registry" / "ir.schema.json"
        if local.is_file():
            return local
    return FRAMEWORK_SDDA_DIR / "registry" / "ir.schema.json"


def rel(root: Path, path: Path) -> str:
    """Chemin relatif POSIX (stable Windows/Linux) pour les rapports et l'IR."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_rel(root: Path, ref: str) -> Path:
    """Inverse de `rel` : une référence `workspace/...` devient un chemin absolu."""
    return (root / ref).resolve()

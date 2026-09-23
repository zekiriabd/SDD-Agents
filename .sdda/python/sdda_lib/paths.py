"""Résolution des chemins du framework et du workspace.

Racine de projet = le dossier qui contient `workspace/` (et en général `.sdda/`).
Le framework lui-même (`.sdda/`) est localisé depuis ce fichier, ce qui permet
aux scripts de tourner sur un workspace de test qui n'embarque pas `.sdda/`.
"""
from __future__ import annotations

import os
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


#: Les quatre entrées du workspace, et ce qui les sépare.
#:
#: L'arbre portait douze répertoires au même niveau, qui mélangeaient quatre
#: NATURES sans le dire : ce qu'on spécifie, ce qu'on configure, ce qu'on
#: produit, et ce qui juge. Un lecteur ne pouvait pas deviner, en regardant
#: `caps/` et `traces/` côte à côte, que l'un se relit en revue et que l'autre
#: se supprime sans perte.
#:
#:   feats/   la SPÉCIFICATION — briefs, missions, caps, topologie, roster,
#:            contrats, décisions. **Du Markdown, et rien d'autre.** Éditée par
#:            l'humain et les agents de spécification, relue en revue,
#:            versionnée. C'est l'ENTRÉE de la génération, jamais sa sortie.
#:   stack/   la CONFIGURATION technique : `STACK.md`, seul. Versionné — il ne
#:            porte que des NOMS de variables ; les valeurs vivent dans
#:            `src/{App}/.env`, gitignoré, avec l'application qui les consomme
#:            (même mécanisme que SDD_Pro).
#:   src/     le CODE GÉNÉRÉ, prompts et schémas figés compris. Un prompt est
#:            un actif d'exécution : hors du paquet, l'application livrée part
#:            sans lui. Un schéma figé aussi : c'est contre lui que l'application
#:            valide sa donnée au démarrage.
#:   proof/   ce qui JUGE — la vérité terrain fournie par l'humain (`seed/`),
#:            puis les jeux, suites, baselines, calibration. Aucun `dev-*` n'y
#:            écrit jamais. C'est la seule frontière du framework qui ne souffre
#:            aucune exception : l'agent qui écrit le code ne peut pas toucher
#:            au jeu qui le note ni à la référence qui mesure sa régression.
#:   .sys/    l'ÉTAT INTERNE et les sorties de run — IR, validation, traces,
#:            rapports. Intégralement régénérable, donc effaçable.
#:
#: L'entrée de l'utilisateur tient donc en trois choses : `STACK.md` (les choix
#: techniques), des fichiers Markdown sous `feats/` (ce que le système doit
#: faire), et sa vérité terrain sous `proof/seed/`. Les URL d'API et les
#: serveurs MCP sont des choix techniques : ils vont dans `STACK.md`, leurs
#: identifiants dans `src/{App}/.env`.
FEATS = "feats"
PROOF = "proof"


def feats_dir(root: Path) -> Path:
    return workspace(root) / FEATS


def missions_dir(root: Path) -> Path:
    return feats_dir(root) / "missions"


def caps_dir(root: Path) -> Path:
    return feats_dir(root) / "caps"


def topology_dir(root: Path) -> Path:
    return feats_dir(root) / "topology"


def roster_path(root: Path, mission_number: int | str) -> Path:
    """Le roster déclaré par l'architecte : `feats/topology/{n}-roster.md`.

    Un fichier Markdown dont le premier bloc ```yaml est la déclaration. Il a
    vécu en `stack/topology/{n}-roster.yml` : un YAML dans la zone de
    configuration, à côté d'un STACK.md gitignoré. Or le roster n'est pas une
    configuration, c'est la première décision de la SPÉCIFICATION (P7) — il se
    relit en revue avec la topologie qu'il commande. `feats/` ne contient que du
    Markdown, et un YAML dans un bloc clôturé reste lisible par un humain autant
    que par `yaml_mini`.

    Il appartient à l'HUMAIN : `architect-topology` le lit, ne l'écrit jamais
    (`loader.yml`). Le voisin `{n}-topology.md` appartient à l'agent. Deux
    fichiers, deux owners, un même répertoire.
    """
    return topology_dir(root) / f"{mission_number}-roster.md"


def seed_dir(root: Path) -> Path:
    """La vérité terrain fournie par l'HUMAIN : `proof/seed/`.

    Scénarios annotés, labels, exemples de référence — tout ce que `po-elicitor`
    demande sous le nom de « ground truth » et que `qa-evals` étend en golden,
    holdout et adversarial. Sous `proof/` parce que c'est ce qui JUGE, hors de
    `datasets/` parce que `datasets/` appartient à `qa-evals` seul : la graine
    est humaine, sa dérivation est de l'agent, et la frontière entre les deux
    se lit dans l'arbre. Aucun `dev-*` n'y touche, comme partout sous `proof/`.
    """
    return proof_dir(root) / "seed"


def contracts_dir(root: Path, kind: str) -> Path:
    """kind ∈ {agents, tools, retrieval, memory}."""
    return feats_dir(root) / "contracts" / kind


def decisions_dir(root: Path) -> Path:
    """Les ADR. UN seul endroit.

    Ils vivaient à deux : `docs/adr/` que citait le gabarit, et
    `.sys/.context/adrs/` que déclarait la matrice d'ownership. Le script des
    tâches humaines cherchait dans les deux — c'est-à-dire que la question
    « cet ADR a-t-il été écrit ? » avait deux réponses possibles, et que celle
    qui gouvernait était celle que personne ne relisait.
    """
    return feats_dir(root) / "decisions"


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


def memory_dir(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}/memory/` : l'implémentation du contrat de mémoire.

    `architect-memory` écrit le contrat (`feats/contracts/memory/`) ; ce module
    le réalise — fenêtre de conversation, état partagé entre agents, politique
    PII à l'écriture. Il n'avait aucun owner : le contrat existait, rien ne le
    devenait. Owner : `dev-orchestration`, qui possède déjà l'état du graphe.
    """
    return app_dir(root, app_name) / "memory"


def proof_dir(root: Path) -> Path:
    return workspace(root) / PROOF


def datasets_dir(root: Path, kind: str | None = None) -> Path:
    base = proof_dir(root) / "datasets"
    return base / kind if kind else base


def evals_dir(root: Path) -> Path:
    return proof_dir(root)


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


def app_dir(root: Path, app_name: str) -> Path:
    """`workspace/src/{App}` : la racine du LIVRABLE — projet, build, `.env`."""
    return workspace(root) / "src" / app_name


def env_rel(app_name: str) -> str:
    """Le chemin de `.env` tel qu'on l'écrit dans un message : `workspace/src/{App}/.env`."""
    return f"workspace/src/{app_name}/.env"


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

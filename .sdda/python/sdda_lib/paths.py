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
#:   feats/   la SPÉCIFICATION — missions, caps, topologie, contrats, décisions.
#:            Éditée par l'humain et les agents de spécification, relue en revue,
#:            versionnée. C'est l'ENTRÉE de la génération, jamais sa sortie.
#:   stack/   la CONFIGURATION technique. Gitignorée : elle porte des secrets.
#:   src/     le CODE GÉNÉRÉ, prompts compris. Un prompt est un actif
#:            d'exécution : hors du paquet, l'application livrée part sans lui.
#:   proof/   ce qui JUGE — jeux, suites, baselines, calibration. Aucun `dev-*`
#:            n'y écrit jamais. C'est la seule frontière du framework qui ne
#:            souffre aucune exception : l'agent qui écrit le code ne peut pas
#:            toucher au jeu qui le note ni à la référence qui mesure sa
#:            régression.
#:   .sys/    l'ÉTAT INTERNE et les sorties de run — IR, validation, traces,
#:            rapports. Intégralement régénérable, donc effaçable.
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


def prompts_dir(root: Path) -> Path:
    """Sous `src/` : un prompt système est un actif d'EXÉCUTION.

    Rangé au même rang que les specs, il ne part pas avec le code : une
    application livrée en exécutable ou en conteneur cherchait ses prompts dans
    un répertoire resté dans le dépôt, et ne les trouvait qu'en développement.

    Ce chemin est le PRÉ-REQUIS du packaging, pas le packaging lui-même : c'est
    `gen_app_skeleton` qui devra les embarquer dans la distribution. Le dire
    plutôt que le laisser croire, parce qu'un prompt absent à l'exécution ne
    produit pas une erreur claire — il produit un agent sans consigne.

    Ce que l'ownership protégeait reste protégé : `dev-agent` ne réécrit pas le
    prompt qu'il implémente. Cela tient à un chemin interdit dans la matrice,
    pas à un répertoire de premier niveau.
    """
    return workspace(root) / "src" / "prompts"


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


def stack_md_path(root: Path) -> Path:
    return workspace(root) / "stack" / "STACK.md"


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

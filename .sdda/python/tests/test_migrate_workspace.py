"""`migrate_workspace` : un vieux workspace monte, sans rien perdre.

Deux cas réels, l'un après l'autre.

v0 -> v1 : `workspace/.sys/` portait `.routing`, `.cache` et `.reverse` — créés
par un bootstrap qui recopiait sa propre liste d'arborescence, lus par aucun
script. Sans version sur disque, aucun outil ne pouvait dire si ce workspace
était « à jour ».

v1 -> v2 : l'arbre portait douze répertoires au même niveau qui mélangeaient
quatre natures sans le dire — ce qu'on spécifie, ce qu'on configure, ce qu'on
produit, ce qui juge. C'est la première migration qui DÉPLACE du contenu, et
donc la première qui peut détruire le travail de quelqu'un. Les tests qui
suivent existent surtout pour cela : vérifier qu'après la montée, chaque
fichier écrit avant est toujours là, et lisible au nouvel endroit.

v2 -> v3 : l'entrée de l'utilisateur tient en trois choses — STACK.md
(versionné, les valeurs partent dans `.env`), du Markdown seul sous `feats/`
(le roster devient `{n}-roster.md`, le graphe rentre dans la topologie), la
vérité terrain sous `proof/seed/`. Les schémas figés partent avec le code, les
manifestes de sources rentrent dans STACK.md. C'est la première migration qui
RÉÉCRIT du contenu, pas seulement des chemins : chaque réécriture a son test,
et le seul qui touche un secret vérifie qu'aucune valeur n'est journalisée.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import workspace as ws
from sdda_scripts import migrate_workspace, smoke_check

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap as bs  # noqa: E402

#: L'arborescence telle qu'elle était en v1. Figée ici, en dur, et c'est
#: voulu : une migration se teste contre l'état RÉEL dont elle part, pas contre
#: la liste courante — qui, elle, bougera encore. Dériver ce fixture de
#: `WORKSPACE_TREE` reviendrait à tester la migration contre son propre
#: résultat.
TREE_V1: tuple[str, ...] = (
    "stack", "stack/sources",
    "contracts/dataaccess/schemas",
    "missions", "caps", "topology",
    "contracts/agents", "contracts/tools", "contracts/retrieval", "contracts/memory",
    "prompts",
    "datasets/golden", "datasets/holdout", "datasets/calibration", "datasets/adversarial",
    "evals/suites", "evals/baselines", "evals/reports", "evals/calibration",
    "traces/runs",
    "src", "docs",
    ".sys/.ir", ".sys/.context/adrs", ".sys/.context/packs",
    ".sys/.state", ".sys/.validation", ".sys/.audit",
)

#: Ce qu'un utilisateur avait écrit, et où on doit le retrouver après la montée.
CONTENT_V1: tuple[tuple[str, str], ...] = (
    ("missions/1-Demo.md", "pipeline/missions/1-Demo.md"),
    ("caps/1-1-Classify.md", "pipeline/caps/1-1-Classify.md"),
    ("topology/1-topology.md", "pipeline/topology/1-topology.md"),
    ("contracts/agents/1-router.agent.md", "pipeline/contracts/agents/1-router.agent.md"),
    ("prompts/router.system.md", "src/Projet/prompts/router.system.md"),
    ("datasets/golden/g-v1.jsonl", "pipeline/datasets/golden/g-v1.jsonl"),
    ("datasets/holdout/mission-1-v1.jsonl", "pipeline/datasets/holdout/mission-1-v1.jsonl"),
    ("evals/suites/s.yaml", "pipeline/suites/s.yaml"),
    ("evals/baselines/1-system.json", "pipeline/baselines/1-system.json"),
    ("evals/calibration/groundedness.json", "pipeline/calibration/groundedness.json"),
    ("evals/reports/1-run.json", ".sys/reports/1-run.json"),
    ("traces/runs/run-1.jsonl", ".sys/traces/runs/run-1.jsonl"),
    ("docs/adr/ADR-depuis-docs.md", "pipeline/decisions/ADR-depuis-docs.md"),
    (".sys/.context/adrs/ADR-depuis-sys.md", "pipeline/decisions/ADR-depuis-sys.md"),
)

MISSING_DIR = "pipeline/calibration"     # un répertoire attendu qu'un vieux bootstrap ne créait pas


@pytest.fixture
def v0(tmp_path: Path) -> Path:
    """Un workspace amorcé avant la v1 : tree v1 incomplet, trois fantômes, pas de workspace.json."""
    root = tmp_path / "old"
    for rel in TREE_V1:
        if rel != "evals/calibration":
            (root / "workspace" / rel).mkdir(parents=True)
    for ghost in migrate_workspace.GHOST_DIRS_V1:
        d = root / "workspace" / ghost
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()
    stack = root / "workspace/stack/STACK.md"
    stack.write_text(bs.build_stack_md("Legacy", bs.COMBOS["c1"], {}), encoding="utf-8")
    return root


@pytest.fixture
def v1_with_content(tmp_path: Path) -> Path:
    """Un workspace v1 complet ET peuplé : le cas où une migration peut détruire."""
    root = tmp_path / "projet"
    for rel in TREE_V1:
        (root / "workspace" / rel).mkdir(parents=True, exist_ok=True)
    for old, _new in CONTENT_V1:
        target = root / "workspace" / old
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"contenu de {old}", encoding="utf-8")
    (root / "workspace/stack/STACK.md").write_text(
        bs.build_stack_md("Projet", bs.COMBOS["c1"], {}), encoding="utf-8")
    ws.write_workspace_version(root, version=1, written_by="test")
    return root


def _snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def _ghosts_present(root: Path) -> list[str]:
    return [g for g in migrate_workspace.GHOST_DIRS_V1 if (root / "workspace" / g).exists()]


# ---------------------------------------------------------------------------
# v0 -> courant
# ---------------------------------------------------------------------------
def test_a_v0_workspace_reaches_the_current_version(v0: Path) -> None:
    code, out = run_main(migrate_workspace.main, ["--root", str(v0)])
    assert code == 0, out
    assert (v0 / "workspace" / MISSING_DIR).is_dir()
    assert _ghosts_present(v0) == []
    assert ws.read_workspace_version(v0) == ws.WORKSPACE_VERSION
    data = json.loads(ws.workspace_json_path(v0).read_text(encoding="utf-8"))
    assert data["createdBy"].startswith("migrate_workspace ") and data["createdAt"].endswith("Z")
    assert run_main(smoke_check.main, ["--root", str(v0)])[0] == 0


def test_a_ghost_dir_with_content_is_kept_and_reported(v0: Path) -> None:
    keep = v0 / "workspace/.sys/.reverse/notes.md"
    keep.write_text("quelque chose que quelqu'un a mis là", encoding="utf-8")
    code, out = run_main(migrate_workspace.main, ["--root", str(v0), "--json"])
    assert code == 1
    report = json.loads(out)
    assert {e["class"] for e in report["errors"]} == {"WORKSPACE_GHOST_DIR_NOT_EMPTY"}
    assert "notes.md" in report["errors"][0]["message"]
    assert keep.is_file()
    assert _ghosts_present(v0) == [".sys/.reverse"]
    # la migration n'a pas abouti : la version n'est PAS bumpée, le prochain run la rejoue
    assert ws.read_workspace_version(v0) is None
    assert report["data"]["stoppedAt"] == 1 and report["data"]["applied"] == []


def test_a_second_run_changes_nothing(v0: Path) -> None:
    assert run_main(migrate_workspace.main, ["--root", str(v0)])[0] == 0
    before = _snapshot(v0)
    stamp = ws.workspace_json_path(v0).read_text(encoding="utf-8")
    code, out = run_main(migrate_workspace.main, ["--root", str(v0), "--json"])
    assert code == 0
    assert json.loads(out)["data"]["actions"] == []
    assert _snapshot(v0) == before
    assert ws.workspace_json_path(v0).read_text(encoding="utf-8") == stamp


def test_dry_run_writes_nothing_but_lists_every_action(v0: Path) -> None:
    before = _snapshot(v0)
    code, out = run_main(migrate_workspace.main, ["--root", str(v0), "--dry-run", "--json"])
    assert code == 0, out
    actions = json.loads(out)["data"]["actions"]
    assert {"mkdir", "rmdir", "write"} <= {a["op"] for a in actions}
    assert all(a["applied"] is False for a in actions)
    assert _snapshot(v0) == before
    assert ws.read_workspace_version(v0) is None


# ---------------------------------------------------------------------------
# v1 -> v2 : la première migration qui DÉPLACE
# ---------------------------------------------------------------------------
def test_every_file_of_a_v1_workspace_survives_at_its_new_place(v1_with_content: Path) -> None:
    root = v1_with_content
    code, out = run_main(migrate_workspace.main, ["--root", str(root)])
    assert code == 0, out
    assert ws.read_workspace_version(root) == ws.WORKSPACE_VERSION

    for old, new in CONTENT_V1:
        target = root / "workspace" / new
        assert target.is_file(), f"perdu : {old} -> {new}"
        assert target.read_text(encoding="utf-8") == f"contenu de {old}"
        assert not (root / "workspace" / old).exists(), f"resté en double : {old}"


def test_the_two_adr_locations_are_merged_into_one(v1_with_content: Path) -> None:
    """La question « cet ADR a-t-il été écrit ? » avait deux réponses possibles."""
    root = v1_with_content
    assert run_main(migrate_workspace.main, ["--root", str(root)])[0] == 0
    decisions = sorted(p.name for p in (root / "workspace/pipeline/decisions").glob("ADR-*.md"))
    assert decisions == ["ADR-depuis-docs.md", "ADR-depuis-sys.md"]
    assert not (root / "workspace/.sys/.context/adrs").exists()
    assert not (root / "workspace/docs/adr").exists()


def test_a_migrated_workspace_passes_the_smoke(v1_with_content: Path) -> None:
    root = v1_with_content
    assert run_main(migrate_workspace.main, ["--root", str(root)])[0] == 0
    code, out = run_main(smoke_check.main, ["--root", str(root)])
    assert code == 0, out


def test_an_empty_legacy_dir_does_not_block_the_migration(tmp_path: Path) -> None:
    """Un sous-répertoire jamais peuplé empêchait son parent d'être retiré, et la
    migration échouait sur un workspace parfaitement sain."""
    root = tmp_path / "vide"
    for rel in TREE_V1:
        (root / "workspace" / rel).mkdir(parents=True, exist_ok=True)
    (root / "workspace/stack/STACK.md").write_text(
        bs.build_stack_md("Vide", bs.COMBOS["c1"], {}), encoding="utf-8")
    ws.write_workspace_version(root, version=1, written_by="test")

    code, out = run_main(migrate_workspace.main, ["--root", str(root)])
    assert code == 0, out
    assert not (root / "workspace/evals").exists()


# ---------------------------------------------------------------------------
# Le registre
# ---------------------------------------------------------------------------
def test_smoke_flags_a_missing_then_an_outdated_version(v0: Path) -> None:
    code, out = run_main(smoke_check.main, ["--root", str(v0), "--json"])
    assert code == 1
    errors = {e["class"]: e for e in json.loads(out)["errors"]}
    assert "WORKSPACE_VERSION_MISSING" in errors
    # Le FIX doit être une commande qu'on TAPE, pas un fichier qu'on ouvre.
    assert "python .sdda/sdda.py migrate-workspace" in errors["WORKSPACE_VERSION_MISSING"]["fix"]

    ws.write_workspace_version(v0, version=0)
    code, out = run_main(smoke_check.main, ["--root", str(v0), "--json"])
    assert code == 1
    errors = {e["class"]: e for e in json.loads(out)["errors"]}
    assert "WORKSPACE_VERSION_OUTDATED" in errors and "WORKSPACE_VERSION_MISSING" not in errors
    assert "python .sdda/sdda.py migrate-workspace" in errors["WORKSPACE_VERSION_OUTDATED"]["fix"]


def test_the_migration_chain_is_consecutive_up_to_the_current_version() -> None:
    """Ajouter une version = une fonction + une entrée ; un trou serait un workspace bloqué."""
    assert [m.target for m in migrate_workspace.MIGRATIONS] == list(range(1, ws.WORKSPACE_VERSION + 1))


def test_bootstrap_shares_the_tree_with_smoke_check() -> None:
    """Une seule liste : celle que le smoke vérifie est celle que le bootstrap crée."""
    assert bs.WORKSPACE_TREE is smoke_check.WORKSPACE_TREE
    assert not (set(smoke_check.WORKSPACE_TREE) & set(migrate_workspace.GHOST_DIRS_V1))
    assert not (set(smoke_check.WORKSPACE_TREE) & set(migrate_workspace.GHOST_DIRS_V2))
    assert not (set(smoke_check.WORKSPACE_TREE) & set(migrate_workspace.GHOST_DIRS_V3))


# ---------------------------------------------------------------------------
# v2 -> v3 : la première migration qui RÉÉCRIT du contenu
# ---------------------------------------------------------------------------
SECRET_VALUE = "sk-test-0123456789abcdef"

ROSTER_V2 = """\
mission: 1
pattern: router
orchestrator:
  id: intent-classifier
  role: classe l'intention
  responsibilities: route ou clarifie
  tier: fast
  tools: []
  rules: route si confidence >= 0.7
subagents:
  - id: billing-specialist
    role: facturation
    responsibilities: explique une facture
    tools: [invoice_lookup]
    tier: balanced
allocation:
  - cap: 1-1-Classify
    agent: intent-classifier
relations:
  - from: intent-classifier
    to: billing-specialist
    condition: "intent == 'billing'"
    counts_as_hop: true
  - from: intent-classifier
    to: clarify_request
    condition: "aucune classe — chemin de repli"
    counts_as_hop: true
loop_bounds: []
merge_strategy:
"""

SOURCES_V2 = """\
# Manifeste de sources — VERSIONNÉ.
Stores:
  - id: exports_local
    kind: local
    root: workspace/assets/exports
    read_only: true
    auth: { mode: none }

Sources:
  - id: order_tracking
    connector: file
    store: exports_local
    glob: tracking/*.jsonl
    format: jsonl
    key: order_id
    filters: [order_id]
    description: |
      Suivi transporteur, un enregistrement par commande. Utiliser pour localiser un colis.
      Ne pas utiliser pour le contenu de la commande. as_of porte la date de l'export.
"""

GRAPH_V2 = "flowchart TD\n  classify{intent-classifier} -->|billing| billing[billing-specialist]\n  billing --> finalize[compose_answer]\n"

TOPOLOGY_V2 = """\
# TOPOLOGY: 1-Demo

MISSION: 1-Demo
Root Pattern: router

## 1. Allocation des capabilities

| CAP | Portée par | Pourquoi là et pas ailleurs |
|---|---|---|
| 1-1-Classify | agent `intent-classifier` | jugement |

## 4. Le graphe

Fichier : `workspace/feats/topology/1-topology.mmd` (Mermaid).

```mermaid
flowchart TD
  ancien --> graphe
```

- **Nœud d'entrée** : `classify`

## 5. Budget estimé
"""


def _legacy_stack_md(app_name: str) -> str:
    """Un STACK.md tel que la v2 l'écrivait : clés de manifestes, secret en clair."""
    text = bs.build_stack_md(app_name, bs.COMBOS["c1"], {})
    text = text.replace(
        "## Active Agent Topology\n",
        "## Active Agent Topology\nRosterManifestRoot: workspace/stack/topology\nRosterManifests:\n  - path: 1-roster.yml\n", 1)
    assert "\nStores:\n" in text
    text = text.replace(
        "\nStores:\n",
        "\nSourceManifestRoot: workspace/stack/sources\nSourceManifests:\n  - path: files.sources.yml\nStores:\n", 1)
    assert " - LLM_API_KEY: ${LLM_API_KEY}" in text
    text = text.replace(" - LLM_API_KEY: ${LLM_API_KEY}", f" - LLM_API_KEY: {SECRET_VALUE}", 1)
    return text


@pytest.fixture
def v2_with_content(tmp_path: Path) -> Path:
    """Un workspace v2 peuplé de tout ce que la v3 range ailleurs."""
    root = tmp_path / "projet"
    for rel in migrate_workspace.TREE_V2:
        (root / "workspace" / rel).mkdir(parents=True, exist_ok=True)
    (root / "workspace/stack/STACK.md").write_text(_legacy_stack_md("Projet"), encoding="utf-8")
    (root / ".gitignore").write_text("workspace/stack/STACK.md\nworkspace/.sys/\n", encoding="utf-8")
    (root / "workspace/stack/topology").mkdir(parents=True, exist_ok=True)
    (root / "workspace/stack/topology/1-roster.yml").write_text(ROSTER_V2, encoding="utf-8")
    (root / "workspace/stack/sources/files.sources.yml").write_text(SOURCES_V2, encoding="utf-8")
    (root / "workspace/feats/missions/1-Demo.md").write_text("# MISSION: 1-Demo\n", encoding="utf-8")
    (root / "workspace/feats/topology/1-topology.md").write_text(TOPOLOGY_V2, encoding="utf-8")
    (root / "workspace/feats/topology/1-topology.mmd").write_text(GRAPH_V2, encoding="utf-8")
    (root / "workspace/feats/contracts/dataaccess/schemas/order_tracking.schema.json").write_text(
        '{"type": "object", "properties": {"order_id": {"type": "string"}}}\n', encoding="utf-8")
    (root / "workspace/feats/briefs/1-Demo.md").write_text("# Brief\n", encoding="utf-8")
    (root / "workspace/feats/briefs/1-Demo.scenarios.jsonl").write_text('{"id": "SC-001"}\n', encoding="utf-8")
    ws.write_workspace_version(root, version=2, written_by="test")
    return root


def _migrate(root: Path) -> dict:
    code, out = run_main(migrate_workspace.main, ["--root", str(root), "--json"])
    assert code == 0, out
    return json.loads(out)


def test_v3_secret_values_leave_stack_md_for_env(v2_with_content: Path) -> None:
    root = v2_with_content
    report = _migrate(root)
    stack = (root / "workspace/stack/STACK.md").read_text(encoding="utf-8")
    assert " - LLM_API_KEY: ${LLM_API_KEY}" in stack and SECRET_VALUE not in stack
    # Le `.env` vit AVEC l'application (`workspace/src/{App}/.env`), jamais à la racine du dépôt.
    env = (root / "workspace/src/Projet/.env").read_text(encoding="utf-8")
    assert f"LLM_API_KEY={SECRET_VALUE}" in env
    assert not (root / ".env").exists()
    # La valeur ne sort JAMAIS dans le journal : seulement le nom.
    assert SECRET_VALUE not in json.dumps(report)
    assert any("LLM_API_KEY" in a.get("detail", "") for a in report["data"]["actions"])


def test_v3_root_env_moves_next_to_the_app(v2_with_content: Path) -> None:
    """Un `.env` laissé à la racine par la première v3 rejoint l'application — déplacé, pas copié."""
    root = v2_with_content
    (root / ".env").write_text("OTHER_TOKEN=abc\n", encoding="utf-8")
    report = _migrate(root)
    env = (root / "workspace/src/Projet/.env").read_text(encoding="utf-8")
    assert "OTHER_TOKEN=abc" in env and f"LLM_API_KEY={SECRET_VALUE}" in env
    assert not (root / ".env").exists()
    assert "abc" not in json.dumps(report) and SECRET_VALUE not in json.dumps(report)


def test_v3_gitignore_versions_stack_md_and_ignores_env(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    lines = {l.strip() for l in (root / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert "workspace/stack/STACK.md" not in lines and "workspace/src/*/.env" in lines


def test_v3_roster_becomes_markdown_in_feats(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    roster_md = root / "workspace/feats/1-roster.md"
    assert roster_md.is_file() and not (root / "workspace/stack/topology").exists()
    from sdda_scripts.validate_architecture import read_roster_yaml
    data = read_roster_yaml(roster_md)
    assert data["mission"] == 1 and data["orchestrator"]["id"] == "intent-classifier"
    stack = (root / "workspace/stack/STACK.md").read_text(encoding="utf-8")
    assert "RosterManifest" not in stack


def test_v3_graph_is_inlined_in_the_topology(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    topo = (root / "workspace/pipeline/topology/1-topology.md").read_text(encoding="utf-8")
    assert "classify{intent-classifier} -->|billing|" in topo and "ancien --> graphe" not in topo
    assert "1-topology.mmd" not in topo
    assert not (root / "workspace/pipeline/topology/1-topology.mmd").exists()


def test_v3_frozen_schemas_travel_with_the_code(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    # Layout plat (v4) : le paquet est `workspace/src/{App}/`, sans `src/{App}/` intermédiaire.
    moved = root / "workspace/src/Projet/data/schemas/order_tracking.schema.json"
    assert moved.is_file() and not (root / "workspace/pipeline/contracts/dataaccess").exists()
    assert not (root / "workspace/src/Projet/src").exists()


def test_v4_flattens_a_src_layout_application(v2_with_content: Path) -> None:
    """Un workspace v3 réel : `src/{App}/data/…` remonte d'un cran, la coquille disparaît.

    Le src layout doublait le nom du projet et cachait l'application deux
    répertoires plus bas — le premier lecteur du premier workspace réel n'a pas
    trouvé le code. Le projet (pyproject, .env) reste à la racine ; les fichiers
    déjà présents à destination ne sont jamais écrasés.
    """
    root = v2_with_content
    _migrate(root)                                            # -> v4, schémas déjà à plat
    ws.write_workspace_version(root, version=3, written_by="test")   # on rejoue depuis un état v3 « src layout »
    pkg = root / "workspace/src/Projet/src/Projet"
    (pkg / "data" / "tools").mkdir(parents=True)
    (pkg / "data" / "tools" / "orders_lookup.py").write_text("# outil\n", encoding="utf-8")
    (pkg / "config.py").write_text("# config\n", encoding="utf-8")
    (pkg / "app_config.json").write_text('{\n  "workspaceRoot": "../../../.."\n}\n', encoding="utf-8")
    (root / "workspace/src/Projet/pyproject.toml").write_text('packages = ["src/Projet"]\n', encoding="utf-8")
    (root / "workspace/src/Projet/.env").write_text("LLM_API_KEY=\n", encoding="utf-8")
    report = _migrate(root)
    app = root / "workspace/src/Projet"
    assert (app / "data/tools/orders_lookup.py").is_file() and (app / "config.py").is_file()
    assert (app / "data/schemas/order_tracking.schema.json").is_file()      # celui de la v3, intact
    assert '"workspaceRoot": "../.."' in (app / "app_config.json").read_text(encoding="utf-8")
    assert (app / ".env").is_file() and (app / "pyproject.toml").is_file()
    assert not (app / "src").exists()
    assert ws.read_workspace_version(root) == ws.WORKSPACE_VERSION
    assert any("pyproject" in str(f.get("message", "")) for f in report.get("findings", []) if f.get("cls") == "WORKSPACE_MIGRATION_COLLISION") or \
        "src layout" in json.dumps(report, ensure_ascii=False)


def test_v3_source_manifests_are_inlined_in_stack_md(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    stack = (root / "workspace/stack/STACK.md").read_text(encoding="utf-8")
    body = stack.split("## Active Data Sources", 1)[1].split("\n## ", 1)[0]
    assert "  - id: exports_local" in body and "  - id: order_tracking" in body
    # Plus aucune clé de manifeste ACTIVE (le gabarit en garde une, en commentaire, pour mcp.json).
    assert not [l for l in body.splitlines() if l.startswith("SourceManifest")]
    assert not (root / "workspace/stack/sources").exists()


def test_v3_ground_truth_moves_to_proof_seed(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    assert (root / "workspace/seed/1-Demo.scenarios.jsonl").is_file()
    assert (root / "workspace/feats/1-Demo.md").is_file()
    assert not (root / "workspace/feats/1-Demo.scenarios.jsonl").exists()


def test_v3_workspace_passes_the_smoke_and_its_three_new_rules(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    code, out = run_main(smoke_check.main, ["--root", str(root), "--json"])
    assert code == 0, out
    # Et les trois règles crient si on les viole après coup.
    (root / "workspace/feats/notes.yml").write_text("x: 1\n", encoding="utf-8")
    (root / "workspace/stack/extra.yml").write_text("x: 1\n", encoding="utf-8")
    stack = root / "workspace/stack/STACK.md"
    stack.write_text(stack.read_text(encoding="utf-8").replace("${LLM_API_KEY}", "sk-live-en-clair"), encoding="utf-8")
    code, out = run_main(smoke_check.main, ["--root", str(root), "--json"])
    classes = {e["class"] for e in json.loads(out)["errors"]}
    assert {"FEATS_NOT_MARKDOWN", "STACK_DIR_UNEXPECTED_FILE", "STACK_SECRET_IN_CLEAR"} <= classes


def test_v3_migration_is_idempotent(v2_with_content: Path) -> None:
    root = v2_with_content
    _migrate(root)
    before = _snapshot(root)
    report = _migrate(root)
    assert report["data"]["actions"] == [] and _snapshot(root) == before

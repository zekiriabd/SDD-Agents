"""Packs et budget de contexte : ce qui entre dans un agent, mesuré avant le spawn.

Les cas qui comptent sont ceux où un chargeur naïf laisserait partir un agent :
un pack périmé (il lirait une doc qui ne décrit plus le code), un budget dépassé
(sortie tronquée et confiante), un motif qui ne désigne rien (l'agent croit
avoir lu ce qu'il n'a pas reçu).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import context_pack

LOADER = """
version: "test"

cross_agent_reads:
  - path: .sdda/rules/commun.md
    cache_layer: stable
    why: vocabulaire partagé

demo-architect:
  model_tier: deep
  budget_bytes: 100000
  reads:
    - workspace/.sys/.context/packs/demo-architect.md
    - workspace/stack/STACK.md
    - workspace/feats/missions/{n}-*.md
    - workspace/feats/caps/{n}-*-*.md
  pack_sources:
    - .sdda/docs/pattern-a.md
    - .sdda/docs/pattern-b.md
  pack_policy: |
    Tranché par rôle : il choisit, il n'implémente pas.
  writes:
    - workspace/feats/topology/{n}-topology.md

tiny-agent:
  model_tier: fast
  budget_bytes: 200
  reads:
    - workspace/stack/STACK.md

per-agent-dev:
  model_tier: balanced
  budget_bytes: 100000
  reads:
    - workspace/src/SupportAssistant/prompts/{agent}.system.md

stack-aware-dev:
  model_tier: balanced
  budget_bytes: 100000
  reads:
    - workspace/stack/STACK.md
  pack_sources:
    - .sdda/stacks/lang/{lang}.md
    - .sdda/stacks/framework/{framework}.md

sliced-architect:
  model_tier: deep
  budget_bytes: 100000
  reads:
    - workspace/.sys/.context/packs/sliced-architect.md
  pack_sources:
    - .sdda/registry/demo.registry.json#families=rag,chunking
"""

#: Un registre miniature à trois familles : deux retenues, une à retirer.
DEMO_REGISTRY = {
    "title": "registre de démonstration",
    "statusLevels": {"recommended": "défaut documenté"},
    "families": {
        "rag": {"default": "hybrid"},
        "chunking": {"default": "recursive-structural"},
        "dataaccess": {"default": "view-per-agent"},
    },
    "patterns": [
        {"id": "hybrid", "family": "rag"},
        {"id": "recursive-structural", "family": "chunking"},
        {"id": "view-per-agent", "family": "dataaccess"},
        {"id": "text-to-sql", "family": "dataaccess"},
    ],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Projet de test portant SON propre `.sdda/` : le loader local l'emporte."""
    root = make_project(tmp_path)
    sdda = root / ".sdda"
    (sdda / "rules").mkdir(parents=True, exist_ok=True)
    (sdda / "docs").mkdir(parents=True, exist_ok=True)
    (sdda / "loader.yml").write_text(LOADER, encoding="utf-8")
    (sdda / "rules" / "commun.md").write_text("# Règle commune\n", encoding="utf-8")
    (sdda / "docs" / "pattern-a.md").write_text("# Pattern A\n" + "a" * 500 + "\n", encoding="utf-8")
    (sdda / "docs" / "pattern-b.md").write_text("# Pattern B\n" + "b" * 500 + "\n", encoding="utf-8")
    for category, names in (("lang", ("python", "csharp")),
                            ("framework", ("langchain", "langgraph", "ms-agent-framework"))):
        (sdda / "stacks" / category).mkdir(parents=True, exist_ok=True)
        for name in names:
            (sdda / "stacks" / category / f"{name}.md").write_text(f"# Stack {name}\n", encoding="utf-8")
    (sdda / "registry").mkdir(parents=True, exist_ok=True)
    (sdda / "registry" / "demo.registry.json").write_text(
        json.dumps(DEMO_REGISTRY, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def _json(root: Path, argv: list[str]) -> tuple[int, dict]:
    code, out = run_main(context_pack.main, argv + ["--root", str(root), "--json"])
    return code, json.loads(out)


# ---------------------------------------------------------------------------
# Résolution
# ---------------------------------------------------------------------------
def test_resolve_orders_layers_and_places_cache_breakpoints(project: Path) -> None:
    context_pack.main(["build", "--agent", "demo-architect", "--root", str(project)])
    code, payload = _json(project, ["resolve", "--agent", "demo-architect", "--mission", "1"])
    data = payload["data"]
    layers = [f["layer"] for f in data["files"]]
    assert code == 0 and layers == sorted(layers, key=["stable", "semi", "volatile"].index)
    assert data["layers"]["semi"]["files"] == ["workspace/stack/STACK.md"]
    assert [b["layer"] for b in data["cacheBreakpoints"]] == ["stable", "semi"]
    assert data["totalBytes"] == sum(f["bytes"] for f in data["files"])


def test_a_pattern_matching_nothing_is_reported_not_silently_dropped(project: Path) -> None:
    context_pack.main(["build", "--agent", "demo-architect", "--root", str(project)])
    code, payload = _json(project, ["resolve", "--agent", "demo-architect", "--mission", "9"])
    assert code == 0  # ce n'est pas bloquant : c'est un avertissement, mais il existe
    assert "workspace/feats/missions/{n}-*.md" in payload["data"]["missing"]
    assert any(w["class"] == "CONFIG_UNKNOWN_KEY" for w in payload["warnings"])


def test_budget_exceeded_refuses_the_spawn(project: Path) -> None:
    code, payload = _json(project, ["resolve", "--agent", "tiny-agent"])
    assert code == 1 and payload["data"]["verdict"] == "red"
    assert [e["class"] for e in payload["errors"]] == ["CONTEXT_BUDGET_EXCEEDED"]


def test_a_context_near_its_ceiling_is_yellow(project: Path) -> None:
    stack = paths.stack_md_path(project)
    budget = int(stack.stat().st_size / 0.85)
    (project / ".sdda" / "loader.yml").write_text(
        LOADER.replace("budget_bytes: 200", f"budget_bytes: {budget}"), encoding="utf-8")
    code, payload = _json(project, ["resolve", "--agent", "tiny-agent"])
    assert code == 0 and payload["data"]["verdict"] == "yellow"


def test_unknown_agent_is_an_error_listing_the_known_ones(project: Path) -> None:
    code, payload = _json(project, ["resolve", "--agent", "inconnu"])
    assert code == 1 and payload["errors"][0]["class"] == "CONFIG_UNKNOWN_KEY"
    assert "demo-architect" in payload["errors"][0]["fix"]


def test_the_target_agent_placeholder_is_substituted(project: Path) -> None:
    code, payload = _json(project, ["resolve", "--agent", "per-agent-dev", "--target", "billing-specialist"])
    assert code == 0
    assert [f["path"] for f in payload["data"]["files"] if "prompts" in f["path"]] == ["workspace/src/SupportAssistant/prompts/billing-specialist.system.md"]
    assert payload["data"]["widenedPlaceholders"] == []


def test_an_unsubstituted_placeholder_is_widened_and_declared(project: Path) -> None:
    code, payload = _json(project, ["resolve", "--agent", "per-agent-dev"])
    assert code == 0 and payload["data"]["widenedPlaceholders"] == ["agent"]
    assert len([f for f in payload["data"]["files"] if "prompts" in f["path"]]) == 2  # les deux prompts


# ---------------------------------------------------------------------------
# Placeholders de stack — un `dev-*` reçoit la stack TRANCHÉE, pas le catalogue
# ---------------------------------------------------------------------------
def _set_stack(root: Path, heading: str, values: list[str]) -> None:
    stack = paths.stack_md_path(root)
    lines = stack.read_text(encoding="utf-8").split("\n")
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == f"## {heading}"
            out.append(line)
            if inside:
                out.extend(f" - .sdda/stacks/{'lang' if 'Language' in heading else 'framework'}/{v}.md"
                           for v in values)
            continue
        if inside and line.strip().startswith("- .sdda/stacks/"):
            continue
        out.append(line)
    stack.write_text("\n".join(out), encoding="utf-8")


def test_lang_placeholder_follows_the_active_language(project: Path) -> None:
    """Sans cela, écrire `lang/csharp.md` ne sert à rien : aucun agent ne le reçoit."""
    assert context_pack.active_stack_values(project)["lang"] == ["python"]
    sources = [p.name for p, _ in context_pack.pack_sources(
        project, context_pack.load_loader(project), "stack-aware-dev")]
    assert "python.md" in sources and "csharp.md" not in sources

    _set_stack(project, "Active Language & Runtime", ["csharp"])
    sources = [p.name for p, _ in context_pack.pack_sources(
        project, context_pack.load_loader(project), "stack-aware-dev")]
    assert "csharp.md" in sources and "python.md" not in sources


def test_a_multi_valued_stack_placeholder_fans_out(project: Path) -> None:
    """LangChain + LangGraph sont deux fiches, pas un glob : la composition est explicite."""
    sources = [p.name for p, _ in context_pack.pack_sources(
        project, context_pack.load_loader(project), "stack-aware-dev")]
    assert sorted(s for s in sources if s.startswith("lang")) == ["langchain.md", "langgraph.md"]
    assert "ms-agent-framework.md" not in sources


# ---------------------------------------------------------------------------
# Tranche par famille (`#families=`)
# ---------------------------------------------------------------------------
# Le registre de patterns est la SSoT de TOUTES les familles. Le servir entier à
# un agent qui en décide deux faisait payer un contexte qui grossissait à chaque
# pattern ajouté, quelle que soit sa famille. `loader.yml` nommait déjà ce
# correctif en commentaire ; ces tests existent pour qu'il ne se perde pas.
def test_slice_keeps_only_the_declared_families(project: Path) -> None:
    path = project / ".sdda" / "registry" / "demo.registry.json"
    sliced, dropped = context_pack.slice_registry(path.read_text(encoding="utf-8"), ("rag", "chunking"))
    data = json.loads(sliced)
    assert dropped == 2
    assert sorted(p["id"] for p in data["patterns"]) == ["hybrid", "recursive-structural"]
    assert sorted(data["families"]) == ["chunking", "rag"]
    assert data["_slicedTo"] == ["chunking", "rag"]


def test_slice_keeps_the_metadata_that_gives_the_words_their_sense(project: Path) -> None:
    """`status: recommended` sans `statusLevels` est un mot sans engagement."""
    path = project / ".sdda" / "registry" / "demo.registry.json"
    data = json.loads(context_pack.slice_registry(path.read_text(encoding="utf-8"), ("rag",))[0])
    assert data["statusLevels"] == {"recommended": "défaut documenté"}
    assert data["title"] == "registre de démonstration"


def test_sliced_pack_declares_what_was_removed(project: Path) -> None:
    """Une famille absente ne doit pas se lire comme une famille inexistante."""
    context_pack.main(["build", "--agent", "sliced-architect", "--root", str(project)])
    pack = context_pack.pack_path(project, "sliced-architect")
    manifest = context_pack.read_manifest(pack)
    assert manifest["trimPolicyApplied"] is True
    assert manifest["trimmed"][0]["keptFamilies"] == ["chunking", "rag"]
    assert manifest["trimmed"][0]["patternsDropped"] == 2
    assert manifest["trimmed"][0]["bytesSaved"] > 0

    text = pack.read_text(encoding="utf-8")
    assert "Ce pack est TRANCHÉ" in text
    assert "view-per-agent" not in text and "text-to-sql" not in text
    assert "hybrid" in text


def test_sliced_pack_still_goes_stale_when_a_removed_family_changes(project: Path) -> None:
    """Le hash reste celui du fichier ENTIER : le manifeste ne certifie pas ce qu'il n'a pas relu."""
    context_pack.main(["build", "--agent", "sliced-architect", "--root", str(project)])
    loader = context_pack.load_loader(project)
    pack = context_pack.pack_path(project, "sliced-architect")
    assert context_pack.pack_state(project, loader, "sliced-architect", pack)["fresh"] is True

    path = project / ".sdda" / "registry" / "demo.registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    registry["patterns"].append({"id": "graphql", "family": "dataaccess"})
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    assert context_pack.pack_state(project, loader, "sliced-architect", pack)["fresh"] is False


def test_source_without_fragment_is_untouched(project: Path) -> None:
    context_pack.main(["build", "--agent", "demo-architect", "--root", str(project)])
    manifest = context_pack.read_manifest(context_pack.pack_path(project, "demo-architect"))
    assert manifest["trimPolicyApplied"] is False and manifest["trimmed"] == []


def test_an_unresolved_stack_placeholder_widens_and_is_declared(project: Path) -> None:
    """Une section de stack vide élargit en `*` — mais le dit, elle ne ment pas."""
    _set_stack(project, "Active Language & Runtime", [])
    assert "lang" not in context_pack.active_stack_values(project)
    _, widened = context_pack.substitute_all(
        ".sdda/stacks/lang/{lang}.md", mission=None, target=None, obj=None,
        stack=context_pack.active_stack_values(project))
    assert widened == ["lang"]


# ---------------------------------------------------------------------------
# Construction et fraîcheur
# ---------------------------------------------------------------------------
def test_build_writes_a_manifest_that_declares_nothing_was_trimmed(project: Path) -> None:
    code, _ = run_main(context_pack.main, ["build", "--agent", "demo-architect", "--root", str(project)])
    path = context_pack.pack_path(project, "demo-architect")
    assert code == 0 and path.is_file()
    manifest = context_pack.read_manifest(path)
    assert manifest["trimPolicyApplied"] is False and manifest["trimmed"] == []
    assert [s["path"] for s in manifest["sources"]] == [".sdda/docs/pattern-a.md", ".sdda/docs/pattern-b.md"]
    text = path.read_text(encoding="utf-8")
    assert "Pattern A" in text and "Pattern B" in text and "il n'implémente pas" in text


def test_check_is_green_right_after_build_and_red_when_a_source_moves(project: Path) -> None:
    run_main(context_pack.main, ["build", "--agent", "demo-architect", "--root", str(project)])
    code, payload = _json(project, ["check", "--agent", "demo-architect"])
    assert code == 0 and payload["data"]["packs"][0]["fresh"] is True

    (project / ".sdda" / "docs" / "pattern-a.md").write_text("# Pattern A\nrévisé\n", encoding="utf-8")
    code, payload = _json(project, ["check", "--agent", "demo-architect"])
    assert code == 1 and payload["errors"][0]["class"] == "PACK_UNUSABLE"
    assert payload["data"]["packs"][0]["moved"] == [".sdda/docs/pattern-a.md"]


def test_a_new_source_also_makes_the_pack_stale(project: Path) -> None:
    run_main(context_pack.main, ["build", "--agent", "demo-architect", "--root", str(project)])
    loader = (project / ".sdda" / "loader.yml").read_text(encoding="utf-8")
    (project / ".sdda" / "docs" / "pattern-c.md").write_text("# Pattern C\n", encoding="utf-8")
    (project / ".sdda" / "loader.yml").write_text(
        loader.replace("    - .sdda/docs/pattern-b.md", "    - .sdda/docs/pattern-b.md\n    - .sdda/docs/pattern-c.md"), encoding="utf-8")
    code, payload = _json(project, ["check", "--agent", "demo-architect"])
    assert code == 1 and payload["data"]["packs"][0]["added"] == [".sdda/docs/pattern-c.md"]


def test_a_missing_pack_blocks_the_resolve(project: Path) -> None:
    code, payload = _json(project, ["resolve", "--agent", "demo-architect", "--mission", "1"])
    assert code == 1 and any(e["class"] == "PACK_UNUSABLE" for e in payload["errors"])


def test_build_on_an_agent_without_pack_sources_says_so(project: Path) -> None:
    code, payload = _json(project, ["build", "--agent", "tiny-agent"])
    assert code == 1 and payload["errors"][0]["class"] == "CONFIG_UNKNOWN_KEY"


# ---------------------------------------------------------------------------
# Packs orphelins — un agent renommé laisse son ancien pack, l'air d'être vivant
# ---------------------------------------------------------------------------
def _plant_orphans(root: Path) -> tuple[Path, list[Path]]:
    """Un pack valide, deux orphelins (dont un avec `.tmp` d'un build interrompu), un `.gitkeep`."""
    context_pack.main(["build", "--agent", "demo-architect", "--root", str(root)])
    folder = context_pack.packs_dir(root)
    orphans = [folder / "topology-architect.md", folder / "dev-rag.md"]
    for path in orphans:
        path.write_text(f"# CONTEXT PACK — {path.stem}\n", encoding="utf-8")
    (folder / "dev-rag.md.tmp").write_text("écriture interrompue\n", encoding="utf-8")
    (folder / ".gitkeep").write_text("", encoding="utf-8")
    return folder, orphans


def test_orphan_detection_names_only_the_packs_of_unknown_agents(project: Path) -> None:
    _plant_orphans(project)
    orphans = context_pack.orphan_packs(project, context_pack.load_loader(project))
    assert [o["agent"] for o in orphans] == ["dev-rag", "topology-architect"]
    assert orphans[0]["companions"] == ["workspace/.sys/.context/packs/dev-rag.md.tmp"]
    assert orphans[1]["companions"] == []


def test_prune_dry_run_lists_and_deletes_nothing(project: Path) -> None:
    folder, planted = _plant_orphans(project)
    before = sorted(p.name for p in folder.iterdir())
    code, out = run_main(context_pack.main, ["prune", "--dry-run", "--root", str(project)])
    assert code == 0
    assert sorted(p.name for p in folder.iterdir()) == before
    assert "dev-rag" in out and "topology-architect" in out and "dry-run" in out
    assert "demo-architect.md" not in out


def test_prune_deletes_exactly_the_orphans_and_keeps_valid_packs_and_gitkeep(project: Path) -> None:
    folder, planted = _plant_orphans(project)
    code, out = run_main(context_pack.main, ["prune", "--root", str(project)])
    assert code == 0
    assert not any(p.exists() for p in planted)
    assert not (folder / "dev-rag.md.tmp").exists()
    assert context_pack.pack_path(project, "demo-architect").is_file()
    assert (folder / ".gitkeep").is_file()
    assert out.count("prune ") == 2 and "supprimé" in out

    # Une seconde passe ne trouve plus rien : la commande est idempotente.
    code, out = run_main(context_pack.main, ["prune", "--root", str(project)])
    assert code == 0 and "aucun pack orphelin" in out


def test_prune_json_shape(project: Path) -> None:
    _plant_orphans(project)
    code, payload = _json(project, ["prune", "--dry-run"])
    data = payload["data"]
    assert code == 0 and payload["ok"] is True and payload["errors"] == []
    assert data["dryRun"] is True and data["removed"] == 0
    assert "demo-architect" in data["known"]
    assert [o["agent"] for o in data["orphans"]] == ["dev-rag", "topology-architect"]
    assert all(set(o) == {"agent", "path", "companions", "removed"} and o["removed"] is False for o in data["orphans"])

    code, payload = _json(project, ["prune"])
    data = payload["data"]
    assert code == 0 and data["dryRun"] is False and data["removed"] == 2
    assert all(o["removed"] is True for o in data["orphans"])


def test_prune_without_a_packs_dir_is_a_quiet_success(project: Path) -> None:
    code, payload = _json(project, ["prune"])
    assert code == 0 and payload["data"]["orphans"] == [] and payload["data"]["removed"] == 0


def test_build_all_warns_about_orphans_without_failing(project: Path) -> None:
    """Personne ne reconstruit un orphelin : `build --agent all` doit au moins le nommer."""
    _plant_orphans(project)
    code, payload = _json(project, ["build", "--agent", "all"])
    assert code == 0
    warns = [w for w in payload["warnings"] if w["class"] == "PACK_UNUSABLE" and "orphelin" in w["message"]]
    assert len(warns) == 1 and "dev-rag.md" in warns[0]["message"] and "topology-architect.md" in warns[0]["message"]
    assert [o["agent"] for o in payload["data"]["orphans"]] == ["dev-rag", "topology-architect"]
    assert context_pack.packs_dir(project).joinpath("dev-rag.md").is_file()  # build n'a rien supprimé


# ---------------------------------------------------------------------------
# Le loader réel du framework
# ---------------------------------------------------------------------------
def test_the_shipped_loader_declares_a_budget_for_every_agent() -> None:
    """Un agent sans budget part quand même — et déborde sans que rien ne le dise.

    Le compte est DÉRIVÉ des fiches sur disque, pas figé : un nombre magique se
    périme au premier agent ajouté ou retiré, et le test devient un obstacle au
    lieu d'une garantie.
    """
    root = paths.FRAMEWORK_SDDA_DIR.parent
    loader = context_pack.load_loader(root)
    agents = context_pack.agent_names(loader)
    on_disk = sorted(p.stem for p in (paths.FRAMEWORK_SDDA_DIR / "agents").glob("*.md"))

    assert agents == on_disk, "loader.yml et .sdda/agents/ ont divergé"
    assert all(isinstance((loader[a] or {}).get("budget_bytes"), int) for a in agents)
    assert all((loader[a] or {}).get("model_tier") in {"fast", "balanced", "deep"} for a in agents)

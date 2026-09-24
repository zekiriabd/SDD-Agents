"""Recouvrements RÉELS entre les `writes:` de deux agents — et leur résolution.

`check_declaration` comparait des chaînes : `contracts/tools/{n}-*.tool.md`
(architect-tools) et `{n}-data-*.tool.md` (architect-data) n'étaient pas « le
même chemin » — le premier englobait pourtant le second, en pleine phase 2
parallèle. Idem `src/**/tools/**` contre `src/**/data/**` en phase 3.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_lib.errors import Report
from sdda_scripts import audit_ownership as ao

APP = "workspace/src/SupportDesk"


@pytest.fixture(scope="module")
def loader() -> dict:
    return ao.load_loader(Path(__file__).resolve().parents[3])


def test_witnesses_show_a_real_common_path() -> None:
    w = ao.overlap_witnesses("workspace/pipeline/contracts/tools/{n}-*.tool.md",
                             "workspace/pipeline/contracts/tools/{n}-data-*.tool.md")
    assert w and all(ao.matches("workspace/pipeline/contracts/tools/{n}-data-*.tool.md", p) for p in w)


def test_both_nestings_are_exhibited() -> None:
    w = set(ao.overlap_witnesses("workspace/src/**/tools/**", "workspace/src/**/data/**"))
    assert any("/data/tools/" in p for p in w) and any("/tools/data/" in p for p in w)


def test_disjoint_patterns_have_no_witness() -> None:
    assert ao.overlap_witnesses("workspace/pipeline/missions/{n}-*.md", "workspace/pipeline/caps/{n}-{m}-*.md") == []
    assert ao.overlap_witnesses("workspace/src/*/*", f"{APP}/skills/*.md".replace(APP, "workspace/src/*")) == []
    # `*/*` = deux segments ; `*/tests/**` = au moins trois.
    assert ao.overlap_witnesses("workspace/src/*/*", "workspace/src/*/tests/**") == []


def test_the_real_matrix_has_no_undeclared_overlap(loader: dict) -> None:
    assert ao.real_overlaps(loader) == []
    report = Report(name="t", target=".")
    ao.check_declaration(loader, report)
    assert report.ok, [e.message for e in report.errors]


def test_removing_the_data_prefix_reservation_is_detected(loader: dict) -> None:
    broken = {k: (dict(v) if isinstance(v, dict) else v) for k, v in loader.items()}
    broken["architect-tools"] = dict(broken["architect-tools"], forbidden_writes=[])
    found = ao.real_overlaps(broken)
    assert any({f["a"], f["b"]} == {"architect-tools", "architect-data"} for f in found)


def test_a_declared_shared_zone_excuses_the_overlap() -> None:
    loader = {
        "a": {"writes": ["workspace/x/**"]},
        "b": {"writes": ["workspace/x/sub/**"]},
    }
    assert ao.real_overlaps(loader)
    loader["shared_writes"] = [{"path": "workspace/x/sub/**", "agents": ["a", "b"], "mode": "serialized", "why": "t"}]
    assert ao.real_overlaps(loader) == []


@pytest.mark.parametrize("path,owner", [
    (f"{APP}/data/tools/query.py", "dev-data"),
    (f"{APP}/agents/billing/tools/lookup.py", "dev-agent"),
    (f"{APP}/agents/billing/memory/state.py", "dev-agent"),
    (f"{APP}/agents/billing/shared/types.py", "dev-agent"),
    (f"{APP}/orchestration/tools/router.py", "dev-orchestration"),
    (f"{APP}/orchestration/shared/types.py", "dev-orchestration"),
    (f"{APP}/tools/crm.py", "dev-tools"),
    (f"{APP}/shared/types.py", "dev-orchestration"),
])
def test_a_nested_path_belongs_to_the_outermost_layer_only(loader: dict, path: str, owner: str) -> None:
    owners = [a for a in ao.agent_names(loader) if ao.check_write(loader, a, path, Report(name="t", target="."))]
    assert owners == [owner], (path, owners)


def test_architect_tools_cannot_take_the_reserved_data_prefix(loader: dict) -> None:
    path = "workspace/pipeline/contracts/tools/1-data-orders.tool.md"
    owners = [a for a in ao.agent_names(loader) if ao.check_write(loader, a, path, Report(name="t", target="."))]
    assert owners == ["architect-data"]

"""pricing — table, provenance par ligne, fraîcheur dérivée, refus bloquant d'un modèle inconnu."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import paths, pricing
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import estimate_budget, ir_compiler

FIXED_AT = "2026-09-20T10:00:00Z"
UNKNOWN = "claude-nonexistent-9"


# ---------------------------------------------------------------------------
# 1. Table
# ---------------------------------------------------------------------------
def test_new_models_are_priced_from_the_authoritative_figures() -> None:
    fable = pricing.get_pricing("claude-fable-5-1")
    assert fable == {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_creation": 12.50}
    opus46 = pricing.get_pricing("claude-opus-4-6")
    assert opus46 == {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25}
    # 1M in + 1M out + 1M cache read, Fable 5.1 : 10 + 50 + 0.25
    assert pricing.estimate_cost_usd("claude-fable-5-1", 1_000_000, 1_000_000, 1_000_000) == pytest.approx(60.25)
    assert pricing.estimate_cost_usd("claude-opus-4-6", 1_000_000, 1_000_000) == pytest.approx(30.0)


def test_existing_rows_are_unchanged() -> None:
    assert pricing.PRICING["claude-sonnet-4-6"] == {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75}
    assert pricing.PRICING["claude-opus-5"] == {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25}
    assert pricing.PRICING["voyage-3-large"]["input"] == 0.18
    assert pricing.FALLBACK_PRICING is pricing.PRICING["claude-sonnet-4-6"]


def test_context_suffix_is_stripped() -> None:
    assert pricing.get_pricing("claude-fable-5-1[1m]") == pricing.PRICING["claude-fable-5-1"]
    assert pricing.pricing_meta("claude-opus-4-6[1m]")["reviewed"] == "2026-09-22"


# ---------------------------------------------------------------------------
# 2. Provenance
# ---------------------------------------------------------------------------
def test_every_model_has_meta_with_reviewed_and_source() -> None:
    assert set(pricing.PRICING) == set(pricing.PRICING_META)
    for model in pricing.PRICING:
        meta = pricing.pricing_meta(model)
        assert set(meta) == {"reviewed", "source"}
        dt.date.fromisoformat(meta["reviewed"])
        assert meta["source"].strip()


def test_new_rows_carry_the_anthropic_source_and_old_rows_the_sdd_pro_table() -> None:
    for model in ("claude-fable-5-1", "claude-opus-4-6"):
        meta = pricing.pricing_meta(model)
        assert meta["reviewed"] == "2026-09-22"
        assert meta["source"] == "https://www.anthropic.com/pricing (via claude-api reference, cached 2026-06-24)"
    assert pricing.pricing_meta("claude-sonnet-4-6") == {"reviewed": "2026-08-30", "source": "SDD_Pro table, reviewed 2026-08-30"}


def test_pricing_meta_of_unknown_model_raises() -> None:
    with pytest.raises(pricing.UnknownModelPricing):
        pricing.pricing_meta(UNKNOWN)


def test_check_table_fails_loudly_when_a_model_lacks_meta() -> None:
    table = dict(pricing.PRICING)
    table["mystery-model"] = {"input": 1.0, "output": 1.0, "cache_read": 1.0, "cache_creation": 1.0}
    with pytest.raises(AssertionError, match="sans PRICING_META.*mystery-model"):
        pricing._check_table(table, pricing.PRICING_META)


def test_check_table_refuses_orphan_meta_and_bad_dates() -> None:
    meta = dict(pricing.PRICING_META)
    meta["ghost"] = {"reviewed": "2026-01-01", "source": "x"}
    with pytest.raises(AssertionError, match="sans ligne de tarif.*ghost"):
        pricing._check_table(pricing.PRICING, meta)
    meta = {k: dict(v) for k, v in pricing.PRICING_META.items()}
    meta["claude-opus-5"]["reviewed"] = "hier"
    with pytest.raises(AssertionError, match="date ISO"):
        pricing._check_table(pricing.PRICING, meta)
    meta = {k: dict(v) for k, v in pricing.PRICING_META.items()}
    meta["claude-opus-5"]["source"] = "  "
    with pytest.raises(AssertionError, match="sans `source`"):
        pricing._check_table(pricing.PRICING, meta)


# ---------------------------------------------------------------------------
# 3. Fraîcheur dérivée de la méta
# ---------------------------------------------------------------------------
def test_review_dates_are_derived_from_meta() -> None:
    dates = sorted(m["reviewed"] for m in pricing.PRICING_META.values())
    assert pricing.PRICING_OLDEST_REVIEWED == dates[0] == "2026-08-30"
    assert pricing.PRICING_LAST_REVIEWED == dates[-1]   # dérivée de la ligne la plus récente, jamais écrite à la main
    assert "claude-sonnet-4-6" in pricing.oldest_reviewed_models()
    assert "claude-fable-5-1" not in pricing.oldest_reviewed_models()


def test_freshness_is_judged_on_the_oldest_row() -> None:
    oldest = dt.date.fromisoformat(pricing.PRICING_OLDEST_REVIEWED)
    fresh, age, reviewed = pricing.check_pricing_freshness(max_age_days=90, today=oldest + dt.timedelta(days=90))
    assert fresh and age == 90 and reviewed == pricing.PRICING_OLDEST_REVIEWED
    # Un jour après le plafond compté depuis la ligne la plus ANCIENNE : stale, même si
    # la plus récente (2026-09-22) est encore largement dans la fenêtre.
    fresh, age, _ = pricing.check_pricing_freshness(max_age_days=90, today=oldest + dt.timedelta(days=91))
    assert not fresh and age == 91
    newest = dt.date.fromisoformat(pricing.PRICING_LAST_REVIEWED)
    assert (oldest + dt.timedelta(days=91) - newest).days < 90


def test_staleness_warning_names_the_stalest_models_and_stays_a_warn() -> None:
    oldest = dt.date.fromisoformat(pricing.PRICING_OLDEST_REVIEWED)
    assert pricing.pricing_staleness_warning(today=oldest + dt.timedelta(days=10)) is None
    msg = pricing.pricing_staleness_warning(today=oldest + dt.timedelta(days=400))
    assert msg is not None and msg.startswith("WARN [BUDGET_PRICING_STALE]")
    assert "claude-sonnet-4-6" in msg and pricing.PRICING_OLDEST_REVIEWED in msg


# ---------------------------------------------------------------------------
# 4. Modèle inconnu : strict lève, non-strict replie
# ---------------------------------------------------------------------------
def test_strict_get_pricing_raises_for_unknown_model() -> None:
    with pytest.raises(pricing.UnknownModelPricing) as exc:
        pricing.get_pricing(UNKNOWN)
    assert exc.value.model_id == UNKNOWN
    assert isinstance(exc.value, KeyError)
    assert UNKNOWN in str(exc.value) and "PRICING_META" in str(exc.value)
    with pytest.raises(pricing.UnknownModelPricing):
        pricing.estimate_cost_usd(UNKNOWN, 1000, 100)
    with pytest.raises(pricing.UnknownModelPricing):
        pricing.get_pricing(None)
    with pytest.raises(pricing.UnknownModelPricing):
        pricing.get_pricing("")


def test_non_strict_get_pricing_falls_back_to_sonnet_4_6() -> None:
    assert pricing.get_pricing(UNKNOWN, strict=False) is pricing.FALLBACK_PRICING
    assert pricing.estimate_cost_usd(UNKNOWN, 1_000_000, 0, strict=False) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 5. Intégration : estimate_budget (G2.budget) passe au ROUGE
# ---------------------------------------------------------------------------
def _compiled(root: Path) -> dict:
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def _remap_tier(root: Path, tier: str, model: str) -> None:
    stack = paths.stack_md_path(root)
    text = stack.read_text(encoding="utf-8")
    old = {"deep": "claude-opus-5", "balanced": "claude-sonnet-5", "fast": "claude-haiku-4-5"}[tier]
    assert f"  {tier}: {old}" in text
    stack.write_text(text.replace(f"  {tier}: {old}", f"  {tier}: {model}"), encoding="utf-8")


def test_estimate_budget_goes_red_when_the_tier_map_points_to_an_unknown_model(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _remap_tier(root, "balanced", UNKNOWN)
    _compiled(root)
    code, out = run_main(estimate_budget.main, ["--root", str(root), "--mission", "1"])
    assert code == 1, out
    assert "[BUDGET_PRICING_UNKNOWN]" in out
    assert UNKNOWN in out and "RuntimeTierMap.balanced" in out
    assert "[BUDGET_EXCEEDED_ESTIMATE]" not in out  # rouge pour la BONNE raison, pas pour un chiffre deviné
    # L'estimation devinée n'est PAS écrite dans l'IR…
    ir = ir_compiler.load_ir(paths.ir_path(root, 1))
    assert "estimated" not in ir["budget"]
    # … mais le rapport de gate est bien rouge sur disque.
    rep = json.loads((paths.validation_dir(root) / "G2-1-SupportAssistant.budget.json").read_text(encoding="utf-8"))
    assert rep["ok"] is False
    assert any(f["class"] == "BUDGET_PRICING_UNKNOWN" for f in rep["errors"])


def test_estimate_budget_unknown_model_is_an_error_not_a_warning_and_no_bypass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(tmp_path)
    _remap_tier(root, "fast", UNKNOWN)
    ir = _compiled(root)
    monkeypatch.setenv("SDDA_BYPASS_BUDGET_ESTIMATE", "1")
    monkeypatch.setenv("SDDA_BYPASS_REASON", "on ne bypasse pas un tarif inconnu")
    report, est = estimate_budget.estimate(ir, root=root, config=read_layered_config(root))
    assert not report.ok
    errors = [f for f in report.errors if f.cls == "BUDGET_PRICING_UNKNOWN"]
    assert errors and UNKNOWN in errors[0].message and "pricing.py" in errors[0].fix
    assert "BUDGET_PRICING_UNKNOWN" not in {w.cls for w in report.warnings}
    assert report.data["pricingUnknown"][0]["model"] == UNKNOWN
    # Le calcul reste lisible (repli explicite) mais n'a aucune autorité.
    assert est["worstCaseCostUsd"] > 0


def test_estimate_budget_goes_red_when_the_embedding_model_is_unknown(project: Path) -> None:
    ir = _compiled(project)
    retrievers = ir.get("retrievers") or []
    assert retrievers, "la fixture doit déclarer un retriever"
    retrievers[0]["binding"]["embeddingModel"] = "embed-nonexistent-1"
    # La fixture n'expose pas le retriever comme nœud d'orchestration : on en
    # ajoute un en mémoire pour exercer la branche `kind == "retriever"`.
    ir["orchestration"]["nodes"].append({"id": "kb-lookup", "kind": "retriever", "ref": retrievers[0]["id"]})
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.has("BUDGET_PRICING_UNKNOWN") and not report.ok
    err = next(f for f in report.errors if f.cls == "BUDGET_PRICING_UNKNOWN")
    assert "embed-nonexistent-1" in err.message and "embeddingModel" in err.message


def test_estimate_budget_stays_green_with_the_fixture_tier_map(project: Path) -> None:
    ir = _compiled(project)
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.ok and not report.has("BUDGET_PRICING_UNKNOWN")
    assert report.data["pricingUnknown"] == []


def test_estimate_budget_prices_the_new_models_when_mapped(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _remap_tier(root, "deep", "claude-fable-5-1")
    _remap_tier(root, "balanced", "claude-opus-4-6")
    ir = _compiled(root)
    report, est = estimate_budget.estimate(ir, root=root, config=read_layered_config(root))
    assert not report.has("BUDGET_PRICING_UNKNOWN")
    assert report.data["tierMap"]["deep"] == "claude-fable-5-1"
    assert est["nominalCostUsd"] > 0

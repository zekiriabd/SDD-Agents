"""Table de prix par modèle et estimation de coût (USD par million de tokens).

Source des tarifs : table SDD_Pro (`.sdd/python/sdd_lib/pricing.py`), revue
contre https://www.anthropic.com/pricing. Aucun appel réseau : la table est
statique et sa fraîcheur est vérifiée par date (`PRICING_LAST_REVIEWED`).

Les agents du produit déclarent un TIER (fast/balanced/deep), jamais un modèle
(P11). La résolution tier -> modèle vient de `STACK.md ## Runtime Models`
(`RuntimeTierMap`) ; `DEFAULT_TIER_MAP` n'est qu'un repli documenté.
"""
from __future__ import annotations

import datetime as _dt

#: Date ISO de la dernière revue manuelle de la table. À BUMPER à chaque édition.
PRICING_LAST_REVIEWED = "2026-08-30"
PRICING_MAX_AGE_DAYS = 90

#: { model_id : { input, output, cache_read, cache_creation } } en USD / MTok.
PRICING: dict[str, dict[str, float]] = {
    "claude-fable-5":    {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_creation": 12.50},
    "claude-opus-5":     {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-sonnet-5":   {"input": 2.00,  "output": 10.00, "cache_read": 0.20, "cache_creation": 2.50},
    "claude-opus-4-8":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "cache_read": 0.10, "cache_creation": 1.25},
    # Embeddings (sortie sans objet)
    "voyage-3-large":    {"input": 0.18,  "output": 0.0,   "cache_read": 0.18, "cache_creation": 0.18},
    "voyage-3":          {"input": 0.06,  "output": 0.0,   "cache_read": 0.06, "cache_creation": 0.06},
}

#: Repli conservateur pour un modèle inconnu : tarif Sonnet 4.6.
FALLBACK_PRICING: dict[str, float] = PRICING["claude-sonnet-4-6"]

DEFAULT_TIER_MAP: dict[str, str] = {
    "deep": "claude-opus-5",
    "balanced": "claude-sonnet-5",
    "fast": "claude-haiku-4-5",
}

#: Modèle de latence par tier : (base_ms, ms_par_token_de_sortie).
#: Hypothèses de planification, pas des mesures — la G6 mesure.
TIER_LATENCY_MS: dict[str, tuple[float, float]] = {
    "fast": (250.0, 6.7),       # ~150 tok/s
    "balanced": (500.0, 14.0),  # ~70 tok/s
    "deep": (800.0, 25.0),      # ~40 tok/s
}


def base_model_id(model_id: str | None) -> str:
    """Retire un suffixe de contexte (`claude-opus-5[1m]` -> `claude-opus-5`)."""
    return (model_id or "").split("[", 1)[0].strip()


def has_known_pricing(model_id: str | None) -> bool:
    return base_model_id(model_id) in PRICING


def get_pricing(model_id: str | None) -> dict[str, float]:
    """Tarif d'un modèle ; repli Sonnet si inconnu (voir `has_known_pricing`)."""
    return PRICING.get(base_model_id(model_id), FALLBACK_PRICING)


def resolve_model(tier: str, tier_map: dict[str, str] | None = None) -> str:
    """Tier -> model_id via la `RuntimeTierMap` (repli : DEFAULT_TIER_MAP)."""
    tm = tier_map or {}
    return str(tm.get(tier) or DEFAULT_TIER_MAP.get(tier) or DEFAULT_TIER_MAP["balanced"])


def estimate_cost_usd(model_id: str, input_tokens: float, output_tokens: float, cache_read_tokens: float = 0.0) -> float:
    """Coût d'un appel : tokens x tarif / 1e6. Arrondi à 6 décimales (stable)."""
    p = get_pricing(model_id)
    cost = (input_tokens * p["input"] + output_tokens * p["output"] + cache_read_tokens * p["cache_read"]) / 1_000_000
    return round(cost, 6)


def estimate_latency_ms(tier: str, output_tokens: float) -> float:
    base, per_tok = TIER_LATENCY_MS.get(tier, TIER_LATENCY_MS["balanced"])
    return base + per_tok * output_tokens


def check_pricing_freshness(max_age_days: int = PRICING_MAX_AGE_DAYS, today: _dt.date | None = None) -> tuple[bool, int, str]:
    """(est_fraîche, âge_en_jours, date_de_revue). Fraîche ssi âge <= max_age_days."""
    reviewed = _dt.date.fromisoformat(PRICING_LAST_REVIEWED)
    ref = today or _dt.date.today()
    age = (ref - reviewed).days
    return (age <= max_age_days, age, PRICING_LAST_REVIEWED)


def pricing_staleness_warning(max_age_days: int = PRICING_MAX_AGE_DAYS, today: _dt.date | None = None) -> str | None:
    """Message WARN `[BUDGET_PRICING_STALE]` si la table a plus de `max_age_days`, sinon None."""
    fresh, age, reviewed = check_pricing_freshness(max_age_days, today)
    if fresh:
        return None
    return (
        f"WARN [BUDGET_PRICING_STALE] la table de prix a {age} jours (revue le {reviewed}, "
        f"plafond {max_age_days}) : re-vérifier les tarifs et bumper PRICING_LAST_REVIEWED"
    )

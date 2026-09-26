"""Table de prix par modèle et estimation de coût (USD par million de tokens).

**Deux sources, une table.** Les fiches `.sdda/providers/*.yaml` sont le
catalogue DÉCLARÉ par fournisseur (bloc `pricing:` + `pricing_last_reviewed`) ;
elles sont lues ici, par `yaml_mini`, et elles GAGNENT sur la table écrite dans
ce module. La table en dur (`FALLBACK_TABLE`) n'est plus qu'un repli : elle
couvre les modèles qu'aucune fiche ne porte (générations antérieures,
embeddings Voyage) et elle garde le framework chiffrable si le répertoire des
fiches est absent ou illisible. Tenir les deux à la main faisait de chaque fiche
un « miroir » que rien ne relisait — ARCHITECTURE §6 le disait lui-même — et
de chaque révision de tarif une occasion de diverger sans que personne le sache.
La concordance des deux, là où elles se recouvrent, est un test
(`test_pricing.py`), et un désaccord entre deux fiches est un problème NOMMÉ
(`CATALOG_PROBLEMS`), pas un choix silencieux.

Aucun appel réseau : la table est statique et sa fraîcheur est vérifiée par date.

Provenance PAR LIGNE (`PRICING_META`) : chaque modèle porte la date de sa
dernière revue et la source du chiffre. La table n'est fraîche que si sa ligne
la PLUS ANCIENNE l'est (`check_pricing_freshness`) ; `PRICING_LAST_REVIEWED`
(la plus récente) n'existe que pour l'affichage. Les deux sont DÉRIVÉES de
`PRICING_META`, jamais écrites à la main — éditer une ligne, c'est éditer sa
méta, et la date suit.

Modèle inconnu = REFUS, pas repli. `get_pricing()` lève
`UnknownModelPricing` par défaut : un coût calculé au tarif d'un autre modèle
est une estimation qui ressemble à un fait, et une gate qui l'accepte est
verte pour de mauvaises raisons. Le repli Sonnet (`FALLBACK_PRICING`) reste
accessible avec `strict=False`, pour un chemin d'affichage qui le dit.

Les agents du produit déclarent un TIER (fast/balanced/deep), jamais un modèle
(P11). La résolution tier -> modèle vient de `STACK.md ## Runtime Models`
(`RuntimeTierMap`) ; `DEFAULT_TIER_MAP` n'est qu'un repli documenté.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from sdda_lib import yaml_mini

PRICING_MAX_AGE_DAYS = 90

#: Le catalogue des fournisseurs : `.sdda/providers/*.yaml`.
PROVIDERS_DIR: Path = Path(__file__).resolve().parents[2] / "providers"

#: REPLI — { model_id : { input, output, cache_read, cache_creation } } en USD / MTok.
#: Les fiches providers le recouvrent (`PRICING` ci-dessous) ; il ne sert seul
#: que pour les modèles qu'aucune fiche ne déclare.
FALLBACK_TABLE: dict[str, dict[str, float]] = {
    "claude-fable-5-1":  {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_creation": 12.50},
    "claude-fable-5":    {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_creation": 12.50},
    "claude-opus-5":     {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-sonnet-5":   {"input": 2.00,  "output": 10.00, "cache_read": 0.20, "cache_creation": 2.50},
    "claude-opus-4-8":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-opus-4-6":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_creation": 6.25},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "cache_read": 0.10, "cache_creation": 1.25},
    "gemini-2.5-flash":  {"input": 0.30,  "output": 2.50,  "cache_read": 0.030, "cache_creation": 0.30},
    # Embeddings (sortie sans objet)
    "voyage-3-large":    {"input": 0.18,  "output": 0.0,   "cache_read": 0.18, "cache_creation": 0.18},
    "voyage-3":          {"input": 0.06,  "output": 0.0,   "cache_read": 0.06, "cache_creation": 0.06},
}

_SDD_PRO = "SDD_Pro table, reviewed 2026-08-30"
_ANTHROPIC_2026_06 = "https://www.anthropic.com/pricing (via claude-api reference, cached 2026-06-24)"

#: { model_id : { reviewed: date ISO de la dernière revue, source: URL ou table d'origine } }.
#: Une ligne de PRICING sans méta fait échouer l'import (`_check_table`).
FALLBACK_META: dict[str, dict[str, str]] = {
    "claude-fable-5-1":  {"reviewed": "2026-09-22", "source": _ANTHROPIC_2026_06},
    "claude-fable-5":    {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-opus-5":     {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-sonnet-5":   {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-opus-4-8":   {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-opus-4-7":   {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-opus-4-6":   {"reviewed": "2026-09-22", "source": _ANTHROPIC_2026_06},
    "claude-sonnet-4-6": {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-sonnet-4-5": {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "claude-haiku-4-5":  {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "gemini-2.5-flash":  {"reviewed": "2026-09-25", "source": "providers/google.yaml (ai.google.dev/gemini-api/docs/pricing, texte <= 200k)"},
    "voyage-3-large":    {"reviewed": "2026-08-30", "source": _SDD_PRO},
    "voyage-3":          {"reviewed": "2026-08-30", "source": _SDD_PRO},
}

_RATE_KEYS = ("input", "output", "cache_read", "cache_creation")


def load_provider_pricing(directory: Path | None = None) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, str]], list[str]]:
    """(tarifs, méta, problèmes) lus dans les fiches `providers/*.yaml`.

    Chaque ligne du bloc `pricing:` d'une fiche devient un tarif, daté par le
    `pricing_last_reviewed` de la fiche et sourcé par son chemin. Rien ici ne
    lève : une fiche illisible, une ligne incomplète ou deux fiches en désaccord
    sur le même modèle deviennent des PROBLÈMES rendus à l'appelant, et la ligne
    fautive est écartée — le repli la couvre alors, ou le modèle reste inconnu,
    ce qui est refusé plus loin (`UnknownModelPricing`). Un tarif deviné à
    moitié serait pire que l'un ou l'autre.

    Deux fiches peuvent porter le même modèle (`azure-openai` recopie la grille
    d'`openai`) : c'est légitime tant qu'elles s'accordent. La première fiche
    (ordre alphabétique, donc stable) l'emporte, et un désaccord est nommé.
    """
    folder = PROVIDERS_DIR if directory is None else directory
    rates_out: dict[str, dict[str, float]] = {}
    meta_out: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    if not folder.is_dir():
        return rates_out, meta_out, [f"répertoire des fiches providers absent ({folder}) : repli sur la table de pricing.py"]
    for path in sorted(folder.glob("*.yaml")):
        try:
            doc = yaml_mini.parse_mapping(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml_mini.YamlMiniError) as exc:
            problems.append(f"{path.name} illisible ({exc}) : ses tarifs sont ignorés")
            continue
        table = doc.get("pricing")
        if not isinstance(table, dict):
            continue
        reviewed = str(doc.get("pricing_last_reviewed") or "").strip()
        try:
            _dt.date.fromisoformat(reviewed)
        except ValueError:
            problems.append(f"{path.name} : `pricing_last_reviewed` absent ou invalide ({reviewed!r}) — tarifs ignorés")
            continue
        source = f"providers/{path.name} (pricing_last_reviewed {reviewed})"
        for model, raw in table.items():
            if not isinstance(raw, dict) or any(not isinstance(raw.get(k), (int, float)) or isinstance(raw.get(k), bool) or raw[k] < 0 for k in _RATE_KEYS):
                problems.append(f"{path.name} : ligne `{model}` incomplète ou négative (attendu {list(_RATE_KEYS)})")
                continue
            rates = {k: float(raw[k]) for k in _RATE_KEYS}
            model_id = str(model)
            if model_id in rates_out:
                if rates_out[model_id] != rates:
                    problems.append(
                        f"`{model_id}` : {path.name} déclare {rates}, {meta_out[model_id]['source']} déclare "
                        f"{rates_out[model_id]} — la première fiche l'emporte ; accorder les deux")
                continue
            rates_out[model_id] = rates
            meta_out[model_id] = {"reviewed": reviewed, "source": source}
    return rates_out, meta_out, problems


def build_table(directory: Path | None = None) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, str]], list[str]]:
    """La table effective : le repli, recouvert par le catalogue des fiches."""
    catalog, catalog_meta, problems = load_provider_pricing(directory)
    table = {**FALLBACK_TABLE, **catalog}
    meta = {**FALLBACK_META, **catalog_meta}
    return table, meta, problems


#: La table EFFECTIVE, lue à l'import. `PRICING_SOURCES` dit d'où vient chaque
#: ligne (`providers` ou `fallback`) ; `CATALOG_PROBLEMS` ce que le catalogue a
#: de faux — affiché par les scripts qui chiffrent, jamais avalé.
PRICING, PRICING_META, CATALOG_PROBLEMS = build_table()
PRICING_SOURCES: dict[str, str] = {m: ("providers" if PRICING_META[m]["source"].startswith("providers/") else "fallback")
                                   for m in PRICING}

#: Repli conservateur pour un modèle inconnu : tarif Sonnet 4.6.
#: Servi UNIQUEMENT par `get_pricing(..., strict=False)`.
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


class UnknownModelPricing(KeyError):
    """Modèle absent de `PRICING` : aucun tarif ne peut lui être attribué sans mentir.

    Porte `model_id` (tel que reçu) pour que l'appelant dise D'OÙ il vient
    (RuntimeTierMap, STACK.md, contrat de retrieval, trace) dans son ERROR
    `[BUDGET_PRICING_UNKNOWN]`.
    """

    def __init__(self, model_id: str | None) -> None:
        self.model_id = model_id or ""
        super().__init__(
            f"modèle `{self.model_id}` absent de la table de prix (fiches .sdda/providers/*.yaml "
            f"et repli sdda_lib/pricing.py) : l'ajouter au bloc `pricing:` de la fiche de son "
            f"fournisseur (ou, à défaut, au repli FALLBACK_TABLE + PRICING_META), ou corriger la tier map"
        )

    def __str__(self) -> str:  # KeyError cite son argument entre quotes ; on veut la phrase.
        return self.args[0]


def _check_table(pricing: dict[str, dict[str, float]] = PRICING, meta: dict[str, dict[str, str]] = PRICING_META) -> None:
    """Invariant de la table, vérifié À L'IMPORT : échoue fort plutôt que de laisser dériver.

    - toute ligne de `pricing` a une méta, et réciproquement (pas de méta orpheline) ;
    - chaque méta porte `reviewed` (date ISO valide) et `source` (non vide) ;
    - chaque ligne de tarif porte les quatre clés, toutes >= 0.
    """
    missing_meta = sorted(set(pricing) - set(meta))
    if missing_meta:
        raise AssertionError(f"pricing.py : modèles sans PRICING_META : {missing_meta}")
    orphan_meta = sorted(set(meta) - set(pricing))
    if orphan_meta:
        raise AssertionError(f"pricing.py : PRICING_META sans ligne de tarif : {orphan_meta}")
    for model, m in meta.items():
        if not str(m.get("source", "")).strip():
            raise AssertionError(f"pricing.py : `{model}` sans `source`")
        try:
            _dt.date.fromisoformat(str(m.get("reviewed", "")))
        except ValueError as exc:
            raise AssertionError(f"pricing.py : `{model}` : `reviewed` n'est pas une date ISO ({m.get('reviewed')!r})") from exc
    for model, rates in pricing.items():
        bad = [k for k in _RATE_KEYS if not isinstance(rates.get(k), (int, float)) or rates[k] < 0]
        if bad:
            raise AssertionError(f"pricing.py : `{model}` : tarifs manquants ou négatifs {bad}")


_check_table()

#: Date ISO de la ligne la plus ANCIENNE : c'est elle qui décide de la fraîcheur.
PRICING_OLDEST_REVIEWED: str = min(m["reviewed"] for m in PRICING_META.values())
#: Date ISO de la ligne la plus RÉCENTE : affichage seulement. Dérivée, jamais éditée.
PRICING_LAST_REVIEWED: str = max(m["reviewed"] for m in PRICING_META.values())


def base_model_id(model_id: str | None) -> str:
    """Retire un suffixe de contexte (`claude-opus-5[1m]` -> `claude-opus-5`)."""
    return (model_id or "").split("[", 1)[0].strip()


def pricing_meta(model_id: str | None) -> dict[str, str]:
    """Provenance d'un tarif (`reviewed`, `source`) ; `UnknownModelPricing` si inconnu."""
    base = base_model_id(model_id)
    if base not in PRICING_META:
        raise UnknownModelPricing(model_id)
    return dict(PRICING_META[base])


def get_pricing(model_id: str | None, *, strict: bool = True) -> dict[str, float]:
    """Tarif d'un modèle.

    `strict=True` (défaut) : lève `UnknownModelPricing` pour un modèle absent de
    la table — un chiffre de budget ou un verdict de gate ne doit jamais reposer
    sur un tarif deviné. `strict=False` : repli Sonnet 4.6 (`FALLBACK_PRICING`),
    réservé aux chemins d'affichage qui annoncent le repli.
    """
    base = base_model_id(model_id)
    if base in PRICING:
        return PRICING[base]
    if strict:
        raise UnknownModelPricing(model_id)
    return FALLBACK_PRICING


def resolve_model(tier: str, tier_map: dict[str, str] | None = None) -> str:
    """Tier -> model_id via la `RuntimeTierMap` (repli : DEFAULT_TIER_MAP)."""
    tm = tier_map or {}
    return str(tm.get(tier) or DEFAULT_TIER_MAP.get(tier) or DEFAULT_TIER_MAP["balanced"])


def estimate_cost_usd(model_id: str, input_tokens: float, output_tokens: float, cache_read_tokens: float = 0.0, *,
                      cache_creation_tokens: float = 0.0, strict: bool = True) -> float:
    """Coût d'un appel : tokens x tarif / 1e6. Arrondi à 6 décimales (stable).

    Les écritures en cache sont facturées (`cache_creation`) : les ignorer ici
    alors que `tracing.span_cost_usd` les compte donnait deux coûts pour le même
    appel — 0 $ contre 6,25 $ pour un million de tokens écrits en cache.

    Propage `UnknownModelPricing` (cf. `get_pricing`) : l'appelant qui produit
    un chiffre de budget doit le transformer en ERROR `[BUDGET_PRICING_UNKNOWN]`.
    """
    p = get_pricing(model_id, strict=strict)
    cost = (input_tokens * p["input"] + output_tokens * p["output"] + cache_read_tokens * p["cache_read"]
            + cache_creation_tokens * p.get("cache_creation", p["input"])) / 1_000_000
    return round(cost, 6)


def estimate_latency_ms(tier: str, output_tokens: float) -> float:
    base, per_tok = TIER_LATENCY_MS.get(tier, TIER_LATENCY_MS["balanced"])
    return base + per_tok * output_tokens


def oldest_reviewed_models() -> list[str]:
    """Les modèles dont la revue est la plus ancienne — ceux à re-vérifier en premier."""
    return sorted(m for m, meta in PRICING_META.items() if meta["reviewed"] == PRICING_OLDEST_REVIEWED)


def check_pricing_freshness(max_age_days: int = PRICING_MAX_AGE_DAYS, today: _dt.date | None = None) -> tuple[bool, int, str]:
    """(est_fraîche, âge_en_jours, date_de_revue). Fraîche ssi âge <= max_age_days.

    L'âge est celui de la ligne la PLUS ANCIENNE (`PRICING_OLDEST_REVIEWED`) :
    la table n'est pas plus fraîche que son tarif le plus vieux.
    """
    reviewed = _dt.date.fromisoformat(PRICING_OLDEST_REVIEWED)
    ref = today or _dt.date.today()
    age = (ref - reviewed).days
    return (age <= max_age_days, age, PRICING_OLDEST_REVIEWED)


def pricing_staleness_warning(max_age_days: int = PRICING_MAX_AGE_DAYS, today: _dt.date | None = None) -> str | None:
    """Message WARN `[BUDGET_PRICING_STALE]` si la table a plus de `max_age_days`, sinon None."""
    fresh, age, reviewed = check_pricing_freshness(max_age_days, today)
    if fresh:
        return None
    stalest = ", ".join(oldest_reviewed_models())
    return (
        f"WARN [BUDGET_PRICING_STALE] la table de prix a {age} jours (ligne la plus ancienne revue le {reviewed} : "
        f"{stalest} ; plafond {max_age_days}) : re-vérifier ces tarifs et mettre à jour leur `reviewed` dans PRICING_META"
    )

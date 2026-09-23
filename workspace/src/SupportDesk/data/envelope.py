"""L'enveloppe de sûreté — le seul chemin par lequel un agent touche une source.

Tous les outils générés passent par `lookup_record`, `search_records` ou
`count_records`. Il n'existe pas d'autre porte : c'est ce qui permet d'affirmer
que **toute** lecture est bornée, tracée, redigée et datée, plutôt que de
l'espérer de N wrappers écrits séparément.

Ce que l'enveloppe applique, dans cet ordre, à chaque appel :

    1. la source est-elle ALLOWLISTÉE ?          sinon elle n'existe pas
    2. la frontière de racine                     (index.py, à la construction)
    3. le budget de lecture                       -> Timeout, jamais un partiel muet
    4. les filtres, sur champs DÉCLARÉS           -> InvalidFilter sinon
    5. la fraîcheur                               -> SourceStale, une ERREUR
    6. l'ordre déterministe                       sinon les evals ne rejouent pas
    7. le plafond, lu à maxRows + 1               -> truncated: true
    8. l'enveloppe des champs de texte libre      (P8)
    9. `as_of` joint à la réponse                 TOUJOURS
   10. le span, PII redigées                      sans quoi aucun post-mortem

L'ordre n'est pas indifférent. Le plafond est appliqué **après** le tri : « les
200 premiers » doivent être les mêmes d'un run à l'autre. La fraîcheur est
vérifiée **avant** de rendre quoi que ce soit : une réponse exacte sur un
instantané périmé est fausse, et elle est fausse avec assurance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .errors import DataAccessError, InvalidFilter, SourceStale, Timeout
from .formats import read_records
from .index import SourceIndex, build_index, record_at
from ..tools.spec import ToolContext
from .registry import Registry, Source, load_registry
from .trust import wrap_record

#: Au-delà, un `IN` n'est plus un filtre : c'est une jointure que l'appelant
#: aurait dû faire côté données.
MAX_IN_VALUES = 20

#: Suffixes des paramètres de plage, dérivés d'un champ déclaré dans `ranges`.
RANGE_SUFFIXES = ("_min", "_max")


@dataclass
class Result:
    """Ce que rend l'enveloppe. `as_of` et `stale` ne sont jamais optionnels."""

    as_of: str
    stale: bool
    record: dict[str, Any] | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of, "stale": self.stale, "record": self.record,
                "records": self.records, "count": self.count, "truncated": self.truncated}


#: Un SEUL contexte pour tous les outils — les outils de données sont des
#: outils. En donner un second obligerait chaque wrapper généré à composer deux
#: objets, et c'est exactement le genre de plomberie qu'une génération répète
#: fidèlement, y compris quand elle est fausse.
Context = ToolContext


def _registry_of(ctx: ToolContext) -> Registry:
    return load_registry(ctx.registry_path)


def _index_of(ctx: ToolContext, source: Source) -> SourceIndex:
    """L'index, construit une fois par processus et par source.

    Le cache vit sur le contexte et non en variable de module : deux runs
    concurrents sur deux racines différentes partageraient sinon le même index,
    et le second lirait les fichiers du premier.
    """
    if source.id not in ctx._indexes:
        ctx._indexes[source.id] = build_index(_registry_of(ctx), source, ctx.base)
    return ctx._indexes[source.id]


def _build(output_model: Any, record_model: Any, payload: dict[str, Any]) -> Any:
    """Construit la sortie TYPÉE du wrapper, ou rend le `Result` brut.

    C'est ici que le schéma figé devient une garantie exécutable : `Record` est
    généré depuis lui, donc un champ requis absent lève à la FRONTIÈRE de
    l'outil — pas trois couches plus haut, où plus personne ne sait quelle
    source l'a produit.

    Un champ non déclaré est ignoré par le modèle, et c'est voulu : une colonne
    `internal_margin_eur` ajoutée par l'amont ne doit pas entrer dans le
    contexte du modèle parce que personne ne l'a interdite (allowlist de
    contexte, pas denylist).
    """
    if output_model is None:
        return Result(**payload)
    data = dict(payload)
    if record_model is not None:
        if data.get("record") is not None:
            data["record"] = record_model(**data["record"])
        if data.get("records"):
            data["records"] = [record_model(**r) for r in data["records"]]
    fields = getattr(output_model, "model_fields", None)
    if fields is not None:
        data = {k: v for k, v in data.items() if k in fields}
    return output_model(**data)


# ---------------------------------------------------------------------------
# Résolution et garde-fous
# ---------------------------------------------------------------------------
def _resolve(ctx: ToolContext, source_id: str) -> tuple[Registry, Source]:
    registry = _registry_of(ctx)
    source = registry.source(source_id)
    if source is None:
        # Même message qu'une source inexistante : distinguer « non déclarée » de
        # « hors allowlist » dirait à un appelant ce que l'allowlist cache.
        raise DataAccessError(f"source `{source_id}` inconnue", source=source_id)
    return registry, source


def _check_freshness(registry: Registry, source: Source, index: SourceIndex) -> bool:
    limit = registry.staleness_hours(source)
    age = index.age_hours
    if limit and age > limit:
        raise SourceStale(
            f"source `{source.id}` périmée : {age:.1f}h > {limit}h",
            source=source.id, as_of=index.as_of, age_hours=age)
    return False


def _validate_filters(source: Source, filters: dict[str, Any]) -> dict[str, Any]:
    """Refuse tout filtre hors des champs déclarés. Rend les filtres normalisés."""
    queryable = source.queryable
    clean: dict[str, Any] = {}

    for name, value in filters.items():
        if value is None:
            continue
        base, suffix = name, ""
        for candidate in RANGE_SUFFIXES:
            if name.endswith(candidate):
                base, suffix = name[: -len(candidate)], candidate
                break
        if suffix and base not in source.ranges:
            raise InvalidFilter(f"`{name}` : `{base}` n'est pas un champ de plage déclaré",
                                source=source.id, detail=f"ranges : {sorted(source.ranges)}")
        if not suffix and base not in queryable:
            raise InvalidFilter(f"`{name}` n'est pas un champ interrogeable",
                                source=source.id, detail=f"admis : {sorted(queryable)}")
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if len(values) > MAX_IN_VALUES:
                raise InvalidFilter(
                    f"`{name}` : {len(values)} valeurs (plafond {MAX_IN_VALUES})", source=source.id,
                    detail="au-delà, ce n'est plus un filtre mais une jointure à faire côté données")
            clean[name] = values
        else:
            clean[name] = value

    missing = [f for f in source.required_filter if f not in clean]
    if missing:
        raise InvalidFilter(f"filtre(s) obligatoire(s) absent(s) : {missing}", source=source.id,
                            detail="la source n'est pas interrogeable sans")
    return clean


def _matches(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    for name, expected in filters.items():
        base, suffix = name, ""
        for candidate in RANGE_SUFFIXES:
            if name.endswith(candidate):
                base, suffix = name[: -len(candidate)], candidate
                break
        actual = record.get(base)
        if actual is None:
            return False
        if suffix == "_min" and str(actual) < str(expected):
            return False
        if suffix == "_max" and str(actual) > str(expected):
            return False
        if not suffix:
            if isinstance(expected, list):
                if str(actual) not in {str(v) for v in expected}:
                    return False
            elif str(actual) != str(expected):
                return False
    return True


def _scan(ctx: ToolContext, source: Source, index: SourceIndex, filters: dict[str, Any],
          budget_ms: int, limit: int | None) -> Iterator[dict[str, Any]]:
    """Parcourt la source en flux, borné par le temps ET par le nombre.

    Le budget est vérifié à chaque enregistrement et non à la fin : une source
    de deux millions de lignes doit rendre `Timeout` au bout de 5 secondes, pas
    après les avoir toutes lues pour constater qu'elle a dépassé.
    """
    deadline = ctx.clock() + budget_ms / 1000.0
    kept = 0
    for path in index.files:
        for record in read_records(path, source):
            if ctx.clock() > deadline:
                raise Timeout(f"budget de lecture dépassé ({budget_ms} ms)", source=source.id,
                              detail=f"{kept} enregistrement(s) retenus avant l'arrêt")
            if not _matches(record, filters):
                continue
            kept += 1
            yield record
            if limit is not None and kept >= limit:
                return


def _sort_key(source: Source) -> Callable[[dict[str, Any]], tuple[str, str]]:
    """Tri par `key` puis `date_field`.

    « Les 200 premiers » doivent être les mêmes d'un run à l'autre : sans ordre
    imposé, l'ordre du glob dépend du système de fichiers, et une eval rejouée
    sur la même donnée rend un autre résultat.
    """
    def key(record: dict[str, Any]) -> tuple[str, str]:
        return (str(record.get(source.key, "")),
                str(record.get(source.date_field, "")) if source.date_field else "")
    return key


def _present(source: Source, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ce qui sort vers le modèle : champs du schéma, texte libre enveloppé."""
    untrusted = frozenset(source.free_text)
    return [wrap_record(r, source=source.id, untrusted_fields=untrusted) for r in records]


def _redact(source: Source, payload: dict[str, Any]) -> dict[str, Any]:
    """Ce qui part dans la trace : les PII deviennent des marqueurs.

    La redaction porte sur le SPAN, pas sur la réponse : l'agent a besoin du nom
    du destinataire pour répondre, le tableau de bord n'en a pas besoin pour
    compter.
    """
    pii = frozenset(source.pii)
    return {k: ("[PII]" if k in pii else v) for k, v in payload.items()}


# ---------------------------------------------------------------------------
# Les trois opérations
# ---------------------------------------------------------------------------
async def lookup_record(*, source: str, key: Any, ctx: ToolContext,
                        record_model: Any = None, output_model: Any = None, **_: Any) -> Any:
    registry, spec = _resolve(ctx, source)
    index = _index_of(ctx, spec)
    stale = _check_freshness(registry, spec, index)

    location = index.by_key.get(str(key))
    record = record_at(location, spec) if location else None
    ctx.emit("data.lookup", _redact(spec, {
        "source": spec.id, "key": str(key), "found": record is not None,
        "as_of": index.as_of, "content_hash": index.content_hash}))

    return _build(output_model, record_model, {
        "as_of": index.as_of, "stale": stale,
        "record": _present(spec, [record])[0] if record else None})


async def search_records(*, source: str, filters: dict[str, Any] | None = None,
                         ctx: ToolContext, record_model: Any = None,
                         output_model: Any = None, **_: Any) -> Any:
    registry, spec = _resolve(ctx, source)
    index = _index_of(ctx, spec)
    stale = _check_freshness(registry, spec, index)
    clean = _validate_filters(spec, filters or {})

    max_rows = registry.envelope.max_records_returned
    budget = registry.envelope.read_timeout_ms
    # maxRows + 1 : lire un de plus est la SEULE façon de savoir qu'il y en
    # avait davantage. Sans lui, `truncated` serait une supposition.
    found = list(_scan(ctx, spec, index, clean, budget, limit=max_rows + 1))

    truncated = len(found) > max_rows
    kept = sorted(found, key=_sort_key(spec))[:max_rows]

    ctx.emit("data.search", _redact(spec, {
        "source": spec.id, "filters": sorted(clean), "returned": len(kept),
        "truncated": truncated, "as_of": index.as_of, "content_hash": index.content_hash}))

    return _build(output_model, record_model, {
        "as_of": index.as_of, "stale": stale, "records": _present(spec, kept),
        "count": len(kept), "truncated": truncated})


async def count_records(*, source: str, filters: dict[str, Any] | None = None,
                        ctx: ToolContext, output_model: Any = None, **_: Any) -> Any:
    """Compte le TOTAL, pas la page.

    C'est la raison d'être de cet outil : « combien de commandes en exception ? »
    répondu depuis 200 enregistrements tronqués donne 200, et le modèle l'annonce
    comme un fait.
    """
    registry, spec = _resolve(ctx, source)
    index = _index_of(ctx, spec)
    stale = _check_freshness(registry, spec, index)
    clean = _validate_filters(spec, filters or {})

    total = sum(1 for _ in _scan(ctx, spec, index, clean,
                                 registry.envelope.read_timeout_ms, limit=None))
    ctx.emit("data.count", _redact(spec, {
        "source": spec.id, "filters": sorted(clean), "count": total,
        "as_of": index.as_of, "content_hash": index.content_hash}))

    return _build(output_model, None, {
        "as_of": index.as_of, "stale": stale, "count": total})

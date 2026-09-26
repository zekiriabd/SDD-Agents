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
    5. la fraîcheur                               -> `stale: true` + `as_of`, jamais tue
    6. l'ordre déterministe                       sinon les evals ne rejouent pas
    7. le plafond, lu à maxRows + 1               -> truncated: true
    8. l'enveloppe des champs de texte libre      (P8)
    9. `as_of` joint à la réponse                 TOUJOURS
   10. le span, PII redigées                      sans quoi aucun post-mortem

L'ordre n'est pas indifférent. Le plafond est appliqué **après** le tri : « les
200 premiers » doivent être les mêmes d'un run à l'autre. La fraîcheur est
vérifiée **avant** de rendre quoi que ce soit, et DITE dans la réponse
(`stale`, `as_of`) : une réponse exacte sur un instantané périmé est fausse
avec assurance si rien ne le signale — et inutile si on refuse de la rendre.

Ce qui est une ERREUR (levée) et ce qui est un ÉTAT (rendu) ne se confondent
pas : une clé inconnue rend `record: null`, un plafond atteint `truncated:
true`, un instantané périmé `stale: true`. Seuls un filtre invalide, une
identité absente, une source illisible et un budget dépassé lèvent.
"""
from __future__ import annotations

import functools
import heapq
import importlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..tools.spec import ToolContext
from .errors import DataAccessError, InvalidFilter, Timeout
from .formats import read_records
from .index import SourceIndex, build_index, record_at
from .registry import Registry, Source, load_registry
from .schema_guard import guard_index
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


def current_tenant() -> str:
    """L'identité posée par le transport pour le run (`identity.current_tenant`), ou `""`.

    Import DYNAMIQUE : `identity.py` appartient au squelette applicatif, et
    cette couche doit rester importable seule (tests L2, projet sans squelette).
    """
    try:
        module = importlib.import_module("..identity", __package__)
    except (ImportError, ValueError, TypeError):
        return ""
    reader = getattr(module, "current_tenant", None)
    return str(reader()) if callable(reader) else ""


def _registry_of(ctx: ToolContext) -> Registry:
    return load_registry(ctx.registry_path)


def _index_of(ctx: ToolContext, source: Source) -> SourceIndex:
    """L'index, construit une fois par processus et par source.

    Le cache vit sur le contexte et non en variable de module : deux runs
    concurrents sur deux racines différentes partageraient sinon le même index,
    et le second lirait les fichiers du premier.
    """
    if source.id not in ctx._indexes:
        registry = _registry_of(ctx)
        built = build_index(registry, source, ctx.base)
        # Le schéma figé fait foi AVANT la première réponse : une source dont
        # l'export a changé de forme lève `SchemaDrift` ici, au lieu d'être
        # servie en silence avec des champs vides ou des types glissés.
        guard_index(Path(__file__).resolve().parent, registry, source, built, read_records)
        ctx._indexes[source.id] = built
    index: SourceIndex = ctx._indexes[source.id]
    return index


def _now(ctx: ToolContext) -> float:
    """L'horloge du contexte (injectable en test), sinon l'horloge monotone."""
    return (ctx.clock or time.monotonic)()


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
    """`True` si l'instantané dépasse `max_staleness_hours` — la donnée est SERVIE, marquée.

    Le contrat d'outil dit « répondre en signalant la date de la donnée » :
    c'est le champ `stale` de la sortie, avec `as_of`, que l'agent cite. Lever
    ici privait l'agent de la donnée ET de sa date — il ne pouvait plus rien
    dire d'exact, même « voici l'état au 3 septembre ».
    """
    limit = registry.staleness_hours(source)
    return bool(limit and index.age_hours > limit)


def _identity_filters(ctx: ToolContext, source: Source) -> dict[str, str]:
    """Les valeurs des `required_filter` de la source, tirées de l'identité de l'APPELANT.

    Le modèle ne fournit pas ce filtre et ne peut pas l'omettre : le runtime
    l'impose. Une identité absente est un refus, pas une lecture non filtrée —
    un outil qui « marche » sans identité est un outil qui lit tout le monde.
    """
    out: dict[str, str] = {}
    for name in source.required_filter:
        # L'identité du contexte d'appel, sinon celle que le TRANSPORT a posée
        # pour le run (`--tenant`, authentification — `identity.current_tenant`).
        # Sans ce repli, `--tenant` n'atteignait jamais la source.
        value = str((ctx.identity or {}).get(name, "") or current_tenant()).strip()
        if not value:
            raise InvalidFilter(f"identité `{name}` absente du contexte d'appel", source=source.id,
                                detail="le cloisonnement est établi par le transport (--tenant, "
                                       "authentification), jamais par le modèle")
        out[name] = value
    return out


@functools.lru_cache(maxsize=None)
def _properties_of(source_id: str) -> dict[str, dict[str, Any]]:
    """Les `properties` du schéma FIGÉ de la source (`data/schemas/{id}.schema.json`).

    Schéma absent : aucune contrainte ici — `schema_guard` refuse déjà de
    démarrer sans lui ; ce n'est pas à la lecture de le redire.
    """
    path = Path(__file__).resolve().parent / "schemas" / f"{source_id}.schema.json"
    if not path.is_file():
        return {}
    properties = (json.loads(path.read_text(encoding="utf-8")).get("properties") or {})
    return {str(n): s for n, s in properties.items() if isinstance(s, dict)}


def _enums_of(source_id: str) -> dict[str, tuple[Any, ...]]:
    return {name: tuple(spec["enum"]) for name, spec in _properties_of(source_id).items()
            if isinstance(spec.get("enum"), list) and spec["enum"]}


def _numeric_fields(source_id: str) -> frozenset[str]:
    """Les champs que le schéma figé type `integer` ou `number`."""
    out = set()
    for name, spec in _properties_of(source_id).items():
        declared = spec.get("type")
        names = [declared] if isinstance(declared, str) else list(declared or [])
        if "integer" in names or "number" in names:
            out.add(name)
    return frozenset(out)


def _ordered(value: Any, numeric: bool) -> tuple[int, Any]:
    """Clé de comparaison d'une borne de plage : numérique si le champ l'est.

    Comparer des chaînes rendait `"9" >= "10"` : `montant_min=10` laissait
    passer 9. Une valeur non numérique d'un champ numérique se classe à part
    (rang 1), elle ne se compare pas à un nombre.
    """
    if numeric:
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (1, str(value))
    return (0, str(value))


def _check_enum(source: Source, name: str, values: list[Any]) -> None:
    """Une valeur hors enum ne filtre pas « rien » : elle dit que l'appel est faux.

    Rendre 0 résultat laisserait le modèle conclure « aucune commande » là où il
    a simplement mal écrit `shipped`. L'erreur le lui dit, avec les valeurs admises.
    """
    allowed = _enums_of(source.id).get(name)
    if not allowed:
        return
    bad = [v for v in values if v not in allowed]
    if bad:
        raise InvalidFilter(f"`{name}` : valeur(s) hors enum {bad}", source=source.id,
                            detail=f"admis : {list(allowed)}")


def _validate_filters(source: Source, filters: dict[str, Any]) -> dict[str, Any]:
    """Refuse tout filtre hors des champs déclarés ou hors enum. Rend les filtres normalisés."""
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
            if not suffix:
                _check_enum(source, name, values)
            clean[name] = values
        else:
            if not suffix:
                _check_enum(source, name, [value])
            clean[name] = value

    missing = [f for f in source.required_filter if f not in clean]
    if missing:
        raise InvalidFilter(f"filtre(s) obligatoire(s) absent(s) : {missing}", source=source.id,
                            detail="la source n'est pas interrogeable sans")
    return clean


def _matches(record: dict[str, Any], filters: dict[str, Any],
             numeric: frozenset[str] = frozenset()) -> bool:
    for name, expected in filters.items():
        base, suffix = name, ""
        for candidate in RANGE_SUFFIXES:
            if name.endswith(candidate):
                base, suffix = name[: -len(candidate)], candidate
                break
        actual = record.get(base)
        if actual is None:
            return False
        if suffix:
            left, right = _ordered(actual, base in numeric), _ordered(expected, base in numeric)
            if left[0] != right[0]:
                return False   # un nombre et une valeur non numérique ne se comparent pas
            if suffix == "_min" and left < right:
                return False
            if suffix == "_max" and left > right:
                return False
            continue
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
    deadline = _now(ctx) + budget_ms / 1000.0
    kept = 0
    numeric = _numeric_fields(source.id)
    for path in index.files:
        for record in read_records(path, source):
            if _now(ctx) > deadline:
                raise Timeout(f"budget de lecture dépassé ({budget_ms} ms)", source=source.id,
                              detail=f"{kept} enregistrement(s) retenus avant l'arrêt")
            if not _matches(record, filters, numeric):
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
    # La clé de la source peut ÊTRE une PII (un e-mail, un numéro client) :
    # le payload la porte sous `key`, pas sous le nom du champ, et la règle
    # par nom ne la voyait jamais.
    return {k: ("[PII]" if k in pii or (k == "key" and source.key in pii) else v)
            for k, v in payload.items()}


# ---------------------------------------------------------------------------
# Les trois opérations
# ---------------------------------------------------------------------------
async def lookup_record(*, source: str, key: Any, ctx: ToolContext,
                        record_model: Any = None, output_model: Any = None, **_: Any) -> Any:
    registry, spec = _resolve(ctx, source)
    index = _index_of(ctx, spec)
    stale = _check_freshness(registry, spec, index)

    identity = _identity_filters(ctx, spec)
    deadline = _now(ctx) + registry.envelope.read_timeout_ms / 1000.0
    location = index.by_key.get(str(key))
    record = record_at(location, spec) if location else None
    if _now(ctx) > deadline:
        raise Timeout(f"budget de lecture dépassé ({registry.envelope.read_timeout_ms} ms)", source=spec.id)
    if record is not None and any(str(record.get(k)) != v for k, v in identity.items()):
        # L'enregistrement d'un AUTRE appelant n'existe pas pour celui-ci :
        # même réponse qu'une clé absente, rien qui dise qu'elle existe ailleurs.
        record = None
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
    supplied = {k: v for k, v in (filters or {}).items() if k not in spec.required_filter}
    clean = _validate_filters(spec, {**supplied, **_identity_filters(ctx, spec)})

    max_rows = registry.envelope.max_records_returned
    budget = registry.envelope.read_timeout_ms
    # Trier AVANT de plafonner, sur TOUTE la source (bornée par le temps) :
    # couper le flux à maxRows+1 puis trier rendait « les 200 premiers DANS
    # L'ORDRE DU FICHIER », donc un résultat qui dépendait du glob et du
    # système de fichiers. `nsmallest` garde maxRows+1 éléments en mémoire, pas
    # la source ; le +1 reste la seule façon de savoir qu'il y en avait plus.
    matched = [0]

    def counted() -> Iterator[dict[str, Any]]:
        for record in _scan(ctx, spec, index, clean, budget, limit=None):
            matched[0] += 1
            yield record

    found = heapq.nsmallest(max_rows + 1, counted(), key=_sort_key(spec))
    truncated = matched[0] > max_rows
    kept = found[:max_rows]

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
    supplied = {k: v for k, v in (filters or {}).items() if k not in spec.required_filter}
    clean = _validate_filters(spec, {**supplied, **_identity_filters(ctx, spec)})

    total = sum(1 for _ in _scan(ctx, spec, index, clean,
                                 registry.envelope.read_timeout_ms, limit=None))
    ctx.emit("data.count", _redact(spec, {
        "source": spec.id, "filters": sorted(clean), "count": total,
        "as_of": index.as_of, "content_hash": index.content_hash}))

    return _build(output_model, None, {
        "as_of": index.as_of, "stale": stale, "count": total})

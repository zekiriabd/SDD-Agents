#!/usr/bin/env python3
"""Profil déterministe d'un corpus local — ce qu'`architect-rag` sait AVANT de choisir.

Le découpage, l'index et la stratégie de requête se décident sur des chiffres
ou sur des habitudes. Sans profil, « 512/50 parce que c'est le défaut du
tutoriel » passe pour une décision ; avec lui, le contrat de retrieval cite un
nombre de documents, une distribution de longueurs, une structure et une langue
— des FAITS, que les choix en aval doivent respecter (ARCHITECTURE §5).

Ce que le script mesure, sans LLM ni réseau (0 token) :

    documents     nombre par type (extension), octets, fichiers non lisibles
    longueurs     tokens APPROXIMÉS (1 token ≈ 4 caractères) : min, p50, p90,
                  p95, max, histogramme — c'est ce qui borne la taille de chunk
    langue        heuristique par mots-outils (fr, en, es, de) ; « indéterminée »
                  plutôt qu'une supposition
    structure     titres Markdown/HTML, tableaux, fichiers tabulaires : ce qui
                  rend un découpage `document-aware` possible ou non
    doublons      exacts (hash du texte normalisé) et quasi-doublons (Jaccard
                  sur shingles de 5 mots, candidats par MinHash, vérifiés)
    PII           motifs de `scan_pii` (mêmes regex, même filtre de Luhn) —
                  type et fichier, JAMAIS la valeur
    accès         clés de front-matter (`tenant`, `access`…) à valeurs multiples :
                  un corpus à niveaux mélangés impose le filtre DANS la requête
    fraîcheur     dates de modification extrêmes

**Où est le corpus.** Par ordre : `--corpus` (répétable) ; sinon les stores
`kind: local` de `STACK.md ## Active Data Sources` ; sinon `workspace/assets/`
entier. Le fichier de secrets (`.env`) n'est jamais ouvert, même s'il est sous
la racine : il n'est pas du corpus, et un profil n'a pas à le voir.

Ce que le script ne prétend pas : un PDF ou un DOCX est compté, pas lu (stdlib
seule) ; il apparaît dans `unparsed`, et le contrat le dit. Une langue ou une
PII « molle » (un nom propre) échappent aux heuristiques — c'est une limite
écrite dans le rapport, pas une absence prouvée.

Usage :
    python .sdda/sdda.py corpus-profile --mission 1 --out workspace/.sys/.validation/corpus-1.json
    python .sdda/sdda.py corpus-profile --mission 1 --corpus workspace/assets/corpus --json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import math
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths, source_registry as sr  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import read_stack_section_kv  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts import scan_pii  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402
from sdda_scripts.audit_ownership import is_secret_file  # noqa: E402

#: Formats lus comme du texte. Le reste est compté, pas interprété.
TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst", ".html", ".htm", ".json",
                           ".jsonl", ".csv", ".tsv", ".xml", ".yaml", ".yml"})
TABULAR_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".parquet"})
SKIP_PARTS = frozenset({"__pycache__", ".git", "node_modules", ".venv"})

#: 1 token ≈ 4 caractères : l'approximation courante des tokenizers BPE sur du
#: texte latin. Elle suffit à dimensionner un chunk ; elle ne facture rien.
CHARS_PER_TOKEN = 4
LENGTH_BUCKETS: tuple[int, ...] = (256, 512, 1024, 2048, 4096, 8192)

#: Mots-outils : la langue d'un texte se lit dans ce qu'il répète sans y penser.
STOPWORDS: dict[str, frozenset[str]] = {
    "fr": frozenset("le la les de des du et est une un que qui dans pour pas sur au aux avec ce cette sont ne".split()),
    "en": frozenset("the and of to is in that for it with as on are be this by not or".split()),
    "es": frozenset("el los las y que en una es por con para no se del al lo".split()),
    "de": frozenset("der die das und ist nicht mit den dem ein eine zu von auf für sich des".split()),
}

#: Clés de front-matter qui portent un niveau d'accès.
ACCESS_KEYS = frozenset({"tenant", "tenant_id", "customer_id", "access", "acl", "confidentiality",
                         "visibility", "classification", "role", "audience"})

#: Quasi-doublons : shingles de 5 mots, MinHash 32 permutations en 8 bandes de 4
#: lignes (seuil de candidature ≈ 0.6), vérification exacte au-delà de 0.85.
SHINGLE_WORDS = 5
NUM_PERM, BANDS = 32, 8
ROWS = NUM_PERM // BANDS
NEAR_DUP_DEFAULT = 0.85
_PRIME = (1 << 61) - 1
_rng = random.Random(20260924)          # graine fixe : même corpus, mêmes paires
_PERMS = [(_rng.randrange(1, _PRIME), _rng.randrange(0, _PRIME)) for _ in range(NUM_PERM)]

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.M)
_HTML_HEADING_RE = re.compile(r"<h([1-6])[\s>]", re.I)
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)
_HTML_TABLE_RE = re.compile(r"<table[\s>]", re.I)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

LIST_CAP = 50


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------
@dataclass
class Document:
    """Un fichier du corpus. `text` est None pour un format que la stdlib ne lit pas."""

    path: Path
    rel: str          # relatif à SA racine de corpus : c'est l'identifiant de document
    root_rel: str     # la racine, relative au projet
    suffix: str
    size: int
    mtime: float
    text: str | None

    @property
    def tokens(self) -> int:
        return math.ceil(len(self.text) / CHARS_PER_TOKEN) if self.text else 0


def local_store_roots(root: Path) -> list[Path]:
    """Racines des stores `kind: local` de `## Active Data Sources`, dans l'ordre déclaré."""
    section = read_stack_section_kv(root, "Active Data Sources")
    if not section.get("Stores") and not section.get("SourceManifests"):
        return []
    registry = sr.load_registry(root, section)
    out: list[Path] = []
    for store in registry.stores.values():
        if str(store.get("kind") or "").strip() != "local" or not store.get("root"):
            continue
        candidate = Path(str(store["root"]).strip())
        out.append(candidate if candidate.is_absolute() else root / candidate)
    return out


def corpus_roots(root: Path, explicit: list[Path] | None) -> tuple[list[Path], str]:
    """(racines, origine). L'origine est écrite dans le profil : on sait d'où il parle."""
    if explicit:
        return [p if p.is_absolute() else root / p for p in explicit], "--corpus"
    stores = local_store_roots(root)
    if stores:
        return stores, "STACK.md ## Active Data Sources (stores kind: local)"
    return [paths.assets_dir(root)], "workspace/assets/ (repli : aucun store local déclaré)"


def collect_documents(root: Path, roots: Iterable[Path]) -> tuple[list[Document], int]:
    """(documents triés, fichiers de secrets écartés). L'ordre est celui des chemins."""
    docs: list[Document] = []
    secrets = 0
    seen: set[Path] = set()
    for base in roots:
        base = base.resolve()
        if not base.is_dir():
            continue
        root_rel = paths.rel(root, base)
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if SKIP_PARTS & set(path.parts) or path in seen:
                continue
            seen.add(path)
            if is_secret_file(path.as_posix()):
                secrets += 1          # jamais ouvert : pas même pour le compter en tokens
                continue
            suffix = path.suffix.lower()
            text: str | None = None
            if suffix in TEXT_SUFFIXES:
                try:
                    text = markdown_io.read_text(path)
                except (OSError, UnicodeDecodeError):
                    text = None
            stat = path.stat()
            docs.append(Document(path, path.relative_to(base).as_posix(), root_rel, suffix or "(sans)",
                                 stat.st_size, stat.st_mtime, text))
    return docs, secrets


# ---------------------------------------------------------------------------
# Mesures
# ---------------------------------------------------------------------------
def percentile(values: list[float], q: float) -> float:
    """Rang le plus proche : sur peu de valeurs, le p95 vaut le maximum — on veut la queue."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def length_profile(tokens: list[int]) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    edges = (0, *LENGTH_BUCKETS)
    for lo, hi in zip(edges, (*LENGTH_BUCKETS, None)):
        label = f"{lo}-{hi}" if hi else f"{lo}+"
        buckets[label] = sum(1 for t in tokens if t >= lo and (hi is None or t < hi))
    vals = [float(t) for t in tokens]
    return {
        "unit": f"tokens approximés (1 token ≈ {CHARS_PER_TOKEN} caractères)",
        "documents": len(tokens), "total": int(sum(tokens)),
        "min": int(min(tokens)) if tokens else 0,
        "mean": round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
        "p50": int(percentile(vals, 0.50)), "p90": int(percentile(vals, 0.90)),
        "p95": int(percentile(vals, 0.95)), "max": int(max(tokens)) if tokens else 0,
        "histogram": buckets,
    }


def words_of(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def language_of(words: list[str]) -> str:
    """Langue par mots-outils. Moins de 3 indices, ou deux langues au coude à coude : indéterminée."""
    scores = sorted(((sum(1 for w in words if w in sw), lang) for lang, sw in STOPWORDS.items()), reverse=True)
    best, second = scores[0], scores[1]
    if best[0] < 3 or best[0] < 1.5 * second[0]:
        return "indéterminée"
    return best[1]


def structure_of(doc: Document) -> dict[str, int]:
    text = doc.text or ""
    if doc.suffix in (".html", ".htm"):
        levels = [int(m) for m in _HTML_HEADING_RE.findall(text)]
        tables = len(_HTML_TABLE_RE.findall(text))
    elif doc.suffix in (".md", ".markdown", ".txt", ".rst"):
        levels = [len(m) for m in _MD_HEADING_RE.findall(text)]
        tables = len(_MD_TABLE_SEP_RE.findall(text))
    else:
        levels, tables = [], 0
    return {"headings": len(levels), "maxDepth": max(levels) if levels else 0, "tables": tables}


def frontmatter_access(text: str) -> dict[str, str]:
    """Les clés d'accès du front-matter YAML d'un document Markdown, ligne à ligne."""
    m = _FRONTMATTER_RE.match(text or "")
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ACCESS_KEYS and value.strip():
            out[key] = value.strip().strip("'\"")
    return out


def normalized(text: str) -> str:
    return " ".join(words_of(text))


def shingles(words: list[str]) -> set[int]:
    if not words:
        return set()
    span = max(1, len(words) - SHINGLE_WORDS + 1)
    return {int.from_bytes(hashlib.blake2b(" ".join(words[i:i + SHINGLE_WORDS]).encode("utf-8"),
                                           digest_size=8).digest(), "big") for i in range(span)}


def minhash(sh: set[int]) -> list[int]:
    return [min((a * x + b) % _PRIME for x in sh) for a, b in _PERMS]


def jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def near_duplicates(docs: list[Document], threshold: float) -> list[dict[str, Any]]:
    """Paires de quasi-doublons : candidates par bandes MinHash, confirmées en Jaccard exact.

    Les doublons exacts sont exclus : ils sont déjà comptés, et une paire à 1.0
    ne dit rien que le hash n'ait dit.
    """
    sets = {i: shingles(words_of(d.text or "")) for i, d in enumerate(docs) if d.text}
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = {}
    for i, sh in sets.items():
        if not sh:
            continue
        sig = minhash(sh)
        for band in range(BANDS):
            buckets.setdefault((band, tuple(sig[band * ROWS:(band + 1) * ROWS])), []).append(i)
    candidates = {(a, b) for members in buckets.values() for x, a in enumerate(members) for b in members[x + 1:]}
    pairs: list[dict[str, Any]] = []
    for a, b in sorted(candidates):
        score = jaccard(sets[a], sets[b])
        if threshold <= score < 1.0:
            pairs.append({"a": f"{docs[a].root_rel}/{docs[a].rel}", "b": f"{docs[b].root_rel}/{docs[b].rel}",
                          "jaccard": round(score, 3)})
    return sorted(pairs, key=lambda p: (-p["jaccard"], p["a"], p["b"]))


def pii_counts(doc: Document) -> dict[str, int]:
    """Types de PII par document, avec les motifs et le filtre de `scan_pii` — jamais la valeur."""
    found: dict[str, int] = {}
    for line in (doc.text or "").split("\n"):
        if scan_pii.EXAMPLE_RE.search(line):
            continue
        for label, pattern in scan_pii.COMPILED:
            m = pattern.search(line)
            if m and not (label == "carte bancaire" and not scan_pii._luhn(m.group(0))):
                found[label] = found.get(label, 0) + 1
    return found


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------
def profile(root: Path, docs: list[Document], *, near_threshold: float = NEAR_DUP_DEFAULT) -> dict[str, Any]:
    by_type: dict[str, dict[str, int]] = {}
    for d in docs:
        entry = by_type.setdefault(d.suffix, {"count": 0, "bytes": 0})
        entry["count"] += 1
        entry["bytes"] += d.size
    readable = [d for d in docs if d.text is not None]
    unparsed = [d for d in docs if d.text is None]

    languages: dict[str, int] = {}
    struct = {"docsWithHeadings": 0, "headingsTotal": 0, "headingDepthMax": 0,
              "docsWithTables": 0, "tablesTotal": 0,
              "tabularFiles": sum(1 for d in docs if d.suffix in TABULAR_SUFFIXES)}
    access_values: dict[str, set[str]] = {}
    top_dirs: dict[str, int] = {}
    pii_by_type: dict[str, int] = {}
    pii_files: list[str] = []
    exact: dict[str, list[str]] = {}

    for d in readable:
        words = words_of(d.text or "")
        lang = language_of(words)
        languages[lang] = languages.get(lang, 0) + 1
        s = structure_of(d)
        if s["headings"]:
            struct["docsWithHeadings"] += 1
            struct["headingsTotal"] += s["headings"]
            struct["headingDepthMax"] = max(struct["headingDepthMax"], s["maxDepth"])
        if s["tables"]:
            struct["docsWithTables"] += 1
            struct["tablesTotal"] += s["tables"]
        for key, value in frontmatter_access(d.text or "").items():
            access_values.setdefault(key, set()).add(value)
        hits = pii_counts(d)
        if hits:
            pii_files.append(f"{d.root_rel}/{d.rel}")
            for label, n in hits.items():
                pii_by_type[label] = pii_by_type.get(label, 0) + n
        if words:
            exact.setdefault(hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest(), []).append(f"{d.root_rel}/{d.rel}")
    for d in docs:
        head = d.rel.split("/", 1)[0] if "/" in d.rel else "(racine)"
        top_dirs[head] = top_dirs.get(head, 0) + 1

    ranked = sorted(((n, lang) for lang, n in languages.items() if lang != "indéterminée"), reverse=True)
    groups = sorted(sorted(g) for g in exact.values() if len(g) > 1)
    near = near_duplicates(readable, near_threshold)
    mtimes = [d.mtime for d in docs]

    return {
        "documents": len(docs),
        "bytesTotal": sum(d.size for d in docs),
        "byType": dict(sorted(by_type.items())),
        "unparsed": {"count": len(unparsed), "types": sorted({d.suffix for d in unparsed}),
                     "files": [f"{d.root_rel}/{d.rel}" for d in unparsed][:LIST_CAP]},
        "lengthTokens": length_profile([d.tokens for d in readable]),
        "languages": {"dominant": ranked[0][1] if ranked else "indéterminée",
                      "byDocument": dict(sorted(languages.items())),
                      "method": "mots-outils fr/en/es/de ; < 3 indices ou écart < ×1.5 = indéterminée"},
        "structure": struct,
        "duplicates": {"exactGroups": groups[:LIST_CAP],
                       "exactDocuments": sum(len(g) for g in groups),
                       "nearPairs": near[:LIST_CAP], "nearPairsTotal": len(near),
                       "nearThreshold": near_threshold,
                       "method": f"Jaccard sur shingles de {SHINGLE_WORDS} mots ; candidats MinHash {NUM_PERM}×{BANDS} bandes"},
        "pii": {"filesWithPii": len(pii_files), "files": pii_files[:LIST_CAP],
                "byType": dict(sorted(pii_by_type.items())),
                "method": "motifs et filtre de Luhn de scan_pii ; aucune valeur recopiée"},
        "access": {"mixed": any(len(v) > 1 for v in access_values.values()),
                   "frontmatterKeys": {k: len(v) for k, v in sorted(access_values.items())},
                   "topLevelDirs": dict(sorted(top_dirs.items()))},
        "freshness": {"oldest": _iso(min(mtimes)) if mtimes else None,
                      "newest": _iso(max(mtimes)) if mtimes else None},
    }


def run(root: Path, *, mission: int | None, corpus: list[Path] | None,
        near_threshold: float = NEAR_DUP_DEFAULT) -> tuple[Report, dict[str, Any]]:
    report = Report(name="CORPUS", target=str(mission) if mission is not None else str(root))
    roots, origin = corpus_roots(root, corpus)
    existing = [r for r in roots if r.is_dir()]
    for r in roots:
        if not r.is_dir():
            report.warn("RETRIEVAL_CORPUS_MISSING", f"racine de corpus `{paths.rel(root, r)}` absente", "monter le store ou corriger `root`",
                        paths.rel(root, r))

    docs, secrets = collect_documents(root, existing)
    payload: dict[str, Any] = {
        "missionId": mission, "generatedAt": now_iso(), "origin": origin,
        "roots": [paths.rel(root, r) for r in roots], "secretFilesSkipped": secrets,
    }
    if not docs:
        report.error("RETRIEVAL_CORPUS_MISSING", f"aucun document sous {payload['roots']} ({origin})",
                     "déposer le corpus sous workspace/assets/ ou déclarer un store `kind: local` ; "
                     "sans corpus, la question « faut-il du RAG ? » n'a pas encore d'objet", origin)
        return report, payload

    payload.update(profile(root, docs, near_threshold=near_threshold))
    if payload["unparsed"]["count"]:
        report.warn("RETRIEVAL_CORPUS_UNPARSED",
                    f"{payload['unparsed']['count']} fichier(s) comptés mais non lus ({', '.join(payload['unparsed']['types'])}) — "
                    "longueurs, langue et doublons ne les couvrent pas",
                    "extraire le texte avant l'ingestion (c'est le travail de dev-retrieval) et relancer le profil")
    if payload["duplicates"]["exactDocuments"]:
        report.warn("RETRIEVAL_CORPUS_DUPLICATES",
                    f"{payload['duplicates']['exactDocuments']} document(s) en doublon exact, "
                    f"{payload['duplicates']['nearPairsTotal']} paire(s) de quasi-doublons",
                    "dédupliquer à l'ingestion : un doublon occupe deux places du top-k pour une seule information")
    if payload["pii"]["filesWithPii"]:
        report.warn("PII_IN_INDEX",
                    f"{payload['pii']['filesWithPii']} document(s) portent des PII détectables ({', '.join(payload['pii']['byType'])}) — "
                    "elles entreront dans l'index telles quelles",
                    "déclarer la redaction à l'ingestion dans le contrat de retrieval (MemoryPIIPolicy) ; "
                    "un index se reconstruit, il ne s'édite pas")
    if payload["access"]["mixed"]:
        report.warn("RETRIEVAL_ACCESS_MIXED",
                    f"niveaux d'accès mélangés dans le front-matter : {payload['access']['frontmatterKeys']}",
                    "écrire au contrat le filtre d'identité DANS la requête d'index (métadonnée), jamais après génération")
    return report, payload


def default_out(root: Path, mission: int | None) -> Path:
    return paths.validation_dir(root) / f"corpus-{mission if mission is not None else 'all'}.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Profil déterministe d'un corpus local (0 token) — le fait qu'architect-rag cite")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION (nomme le rapport)")
    p.add_argument("--corpus", type=Path, action="append", default=None, help="racine de corpus explicite, répétable")
    p.add_argument("--out", type=Path, default=None, help="défaut : workspace/.sys/.validation/corpus-{n}.json")
    p.add_argument("--near-threshold", type=float, default=NEAR_DUP_DEFAULT, help="Jaccard minimal d'un quasi-doublon")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report, payload = run(root, mission=args.mission, corpus=args.corpus, near_threshold=args.near_threshold)
    if payload.get("documents") and not args.no_report:
        out = args.out if args.out else default_out(root, args.mission)
        out = out if out.is_absolute() else root / out
        atomic_write_json(out, payload)
        report.data["written"] = paths.rel(root, out)
    report.data.update({k: payload[k] for k in ("documents", "origin", "roots") if k in payload})
    if "lengthTokens" in payload:
        report.data["lengthTokensP50"] = payload["lengthTokens"]["p50"]
        report.data["dominantLanguage"] = payload["languages"]["dominant"]
    if not args.json and payload.get("documents"):
        lt = payload["lengthTokens"]
        # Préfixe `[RETRIEVAL]` : un préfixe de chat inédit entre crochets
        # entrerait au registre comme une classe d'erreur (sync_error_registry).
        print(f"[RETRIEVAL] corpus : {payload['documents']} document(s) — tokens p50 {lt['p50']} / p95 {lt['p95']} / max {lt['max']}, "
              f"langue {payload['languages']['dominant']}, {payload['structure']['docsWithHeadings']} titrés, "
              f"{payload['duplicates']['exactDocuments']} doublons exacts, {payload['pii']['filesWithPii']} avec PII")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())

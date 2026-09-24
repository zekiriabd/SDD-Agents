#!/usr/bin/env python3
"""Banc comparatif de découpage — ce qui rend vraie la règle « chunking mesuré ».

`architect-rag` refuse tout découpage non mesuré comparativement sur le golden
set (STEP 5). Sans outil, la règle se tenait par la bonne volonté : un tableau
comparatif pouvait s'écrire à la main, donc s'inventer. Ce script le produit.

Pour chaque configuration, sur le MÊME corpus et les MÊMES requêtes :

    1. découper chaque document selon la stratégie ;
    2. indexer les chunks dans un BM25 stdlib (k1 = 1.5, b = 0.75, sans stemming) ;
    3. pour chaque requête, classer les chunks, remonter aux DOCUMENTS (la vérité
       du golden est au niveau document — `retrieval_metrics.recall_at_k`) ;
    4. mesurer recall@k, nDCG@k, MRR, précision de contexte (part des unités
       servies qui viennent d'un document pertinent), nombre et taille des
       chunks, tokens servis par requête, tokens indexés (coût d'ingestion).

Puis recommander : le meilleur recall@k (nDCG en départage) ; mais parmi les
configurations à moins de 2 points du meilleur sur les deux métriques, la moins
chère à l'ingestion — la règle de la fiche, appliquée par le script plutôt que
laissée à l'appréciation.

**Ce que le banc ne prétend pas.** Le retriever est lexical et déterministe :
il compare des découpages ENTRE EUX, sur une base commune, sans embedding ni
réseau (0 token). Il ne prédit pas le recall absolu du retriever hybride de
production — c'est `run_retrieval_eval.py` qui le mesure en G4, sur l'index
réel. Un écart de découpage visible en BM25 subsiste en général en hybride ;
l'inverse n'est pas garanti, et le rapport le dit.

Stratégies (`--config nom:paramètres`, tailles en tokens approximés, 1 ≈ 4 car.) :

    fixed:SIZE/OVERLAP                 fenêtres de mots, recouvrement
    recursive-structural:SIZE/OVERLAP  sections > paragraphes > phrases, fusionnés jusqu'à SIZE
    document-aware:section[/MAX]       un chunk par section de titre (coupée au-delà de MAX)
    paragraph:SIZE                     paragraphes fusionnés jusqu'à SIZE
    sentence:N                         groupes de N phrases
    parent-child:CHILD/OVERLAP[/PARENT] enfants indexés, parent servi (section, ou PARENT tokens)

Golden, par ordre : `--golden` ; le brouillon d'architect-rag
`workspace/.sys/.validation/retrieval-golden-draft-{n}.jsonl` ; les jeux
`pipeline/datasets/golden/` porteurs d'`expected_documents`. Un `doc_id` se
résout par chemin relatif à la racine du corpus, avec ou sans extension, ou
par nom de fichier s'il est unique.

Usage :
    python .sdda/sdda.py chunking-bench --mission 1 \\
      --golden workspace/.sys/.validation/retrieval-golden-draft-1.jsonl \\
      --config recursive-structural:800/120 --config parent-child:400/60 \\
      --config document-aware:section --k 8 --out workspace/.sys/.validation/chunking-bench-1.json
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths, retrieval_metrics  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts import corpus_profile, run_retrieval_eval  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402

CHARS_PER_TOKEN = corpus_profile.CHARS_PER_TOKEN
BM25_K1, BM25_B = 1.5, 0.75

#: La règle de la fiche : sous 2 points d'écart, la moins chère à l'ingestion.
TIE_POINTS = 0.02

#: Ce qu'architect-rag compare quand il ne dit rien : un de chaque famille.
DEFAULT_CONFIGS: tuple[str, ...] = (
    "fixed:512/64", "recursive-structural:800/120", "document-aware:section",
    "paragraph:400", "parent-child:400/60",
)

MIN_QUERIES = 30

_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+\S", re.M)
_PARA_RE = re.compile(r"\n\s*\n")
_SENT_RE = re.compile(r"(?<=[.!?…])\s+")
_TERM_RE = re.compile(r"\w+", re.UNICODE)


def tokens_of(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


# ---------------------------------------------------------------------------
# Découpeurs
# ---------------------------------------------------------------------------
def sections(text: str) -> list[str]:
    """Une section par titre Markdown, titre compris ; le préambule est une section."""
    starts = [m.start() for m in _HEADING_LINE_RE.finditer(text)]
    if not starts:
        return [text] if text.strip() else []
    cuts = ([0] if starts[0] > 0 else []) + starts + [len(text)]
    return [text[a:b] for a, b in zip(cuts, cuts[1:]) if text[a:b].strip()]


def paragraphs(text: str) -> list[str]:
    return [p for p in _PARA_RE.split(text) if p.strip()]


def sentences(text: str) -> list[str]:
    return [s for s in _SENT_RE.split(text) if s.strip()]


def fixed(text: str, size: int, overlap: int) -> list[str]:
    """Fenêtres de mots de SIZE tokens approximés ; chaque fenêtre reprend OVERLAP tokens."""
    words = text.split()
    if not words:
        return []
    out: list[str] = []
    budget, back = size * CHARS_PER_TOKEN, overlap * CHARS_PER_TOKEN
    i = 0
    while i < len(words):
        j, chars = i, 0
        while j < len(words) and (chars == 0 or chars + len(words[j]) + 1 <= budget):
            chars += len(words[j]) + 1
            j += 1
        out.append(" ".join(words[i:j]))
        if j >= len(words):
            break
        # Recul de OVERLAP tokens, jamais jusqu'au début de la fenêtre : sinon boucle.
        k, kept = j, 0
        while k > i + 1 and kept + len(words[k - 1]) + 1 <= back:
            k -= 1
            kept += len(words[k]) + 1
        i = k
    return out


def _tail(text: str, overlap: int) -> str:
    words, kept, out = text.split(), 0, []
    for w in reversed(words):
        if kept + len(w) + 1 > overlap * CHARS_PER_TOKEN:
            break
        out.append(w)
        kept += len(w) + 1
    return " ".join(reversed(out))


def merge(pieces: list[str], size: int, overlap: int = 0) -> list[str]:
    """Fusionne des morceaux adjacents jusqu'à SIZE ; un morceau trop gros est coupé en fenêtres."""
    out: list[str] = []
    current = ""
    for piece in pieces:
        if tokens_of(piece) > size:
            if current:
                out.append(current)
                current = ""
            out.extend(fixed(piece, size, overlap))
            continue
        candidate = f"{current}\n\n{piece}" if current else piece
        if current and tokens_of(candidate) > size:
            out.append(current)
            head = _tail(current, overlap) if overlap else ""
            current = f"{head}\n\n{piece}" if head else piece
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def recursive_structural(text: str, size: int, overlap: int) -> list[str]:
    """Sections, puis paragraphes, puis phrases : on ne descend d'un niveau que si c'est trop gros."""
    def split(piece: str, level: int) -> list[str]:
        if tokens_of(piece) <= size or level > 2:
            return [piece]
        parts = (sections, paragraphs, sentences)[level](piece)
        if len(parts) <= 1:
            return split(piece, level + 1)
        return [p for part in parts for p in split(part, level + 1)]
    return merge(split(text, 0), size, overlap)


@dataclass
class Unit:
    """Ce qu'un découpage produit : le texte indexé, et l'unité SERVIE (le parent, ou lui-même)."""

    doc: int
    text: str
    parent: int = -1       # index dans `Chunking.parents`, -1 : le chunk est servi lui-même


@dataclass
class Chunking:
    units: list[Unit] = field(default_factory=list)
    parents: list[tuple[int, str]] = field(default_factory=list)   # (document, texte servi)

    @property
    def served(self) -> list[tuple[int, str]]:
        """Les unités SERVIES : les parents en parent-child, les chunks eux-mêmes sinon."""
        return self.parents if self.parents else [(u.doc, u.text) for u in self.units]

    def served_of(self, unit: int) -> int:
        return self.units[unit].parent if self.parents else unit


@dataclass
class Config:
    spec: str
    strategy: str
    params: tuple[str, ...]

    def chunk(self, texts: list[str]) -> Chunking:
        out = Chunking()
        for d, text in enumerate(texts):
            if self.strategy == "parent-child":
                child, overlap = int(self.params[0]), int(self.params[1])
                parents = fixed(text, int(self.params[2]), 0) if len(self.params) > 2 else sections(text)
                for p in parents:
                    out.parents.append((d, p))
                    pid = len(out.parents) - 1
                    out.units.extend(Unit(d, c, pid) for c in fixed(p, child, overlap))
                continue
            out.units.extend(Unit(d, c) for c in STRATEGIES[self.strategy](text, self.params))
        return out


STRATEGIES: dict[str, Callable[[str, tuple[str, ...]], list[str]]] = {
    "fixed": lambda t, p: fixed(t, int(p[0]), int(p[1]) if len(p) > 1 else 0),
    "recursive-structural": lambda t, p: recursive_structural(t, int(p[0]), int(p[1]) if len(p) > 1 else 0),
    "document-aware": lambda t, p: [c for s in sections(t) for c in (fixed(s, int(p[1]), 0) if len(p) > 1 and tokens_of(s) > int(p[1]) else [s])],
    "paragraph": lambda t, p: merge(paragraphs(t), int(p[0])),
    "sentence": lambda t, p: sentence_groups(t, int(p[0])),
}


def sentence_groups(text: str, n: int) -> list[str]:
    parts = sentences(text)
    return [" ".join(parts[i:i + n]) for i in range(0, len(parts), n)]
KNOWN = (*STRATEGIES, "parent-child")


def parse_config(spec: str) -> Config:
    """`recursive-structural:800/120` -> Config. Lève ValueError sur une forme invalide."""
    name, _, raw = spec.partition(":")
    name = name.strip()
    if name not in KNOWN:
        raise ValueError(f"stratégie `{name}` inconnue (connues : {', '.join(KNOWN)})")
    params = tuple(p.strip() for p in raw.split("/") if p.strip()) if raw else ()
    numeric = params[1:] if name == "document-aware" else params
    if name == "document-aware" and (not params or params[0] != "section"):
        raise ValueError("`document-aware` attend `section` ou `section/MAX`")
    if name != "document-aware" and not params:
        raise ValueError(f"`{name}` attend des paramètres (ex. `{name}:400/60`)")
    if name == "parent-child" and len(params) < 2:
        raise ValueError("`parent-child` attend `CHILD/OVERLAP[/PARENT]`")
    for pos, p in enumerate(numeric):
        # La taille est > 0 ; le recouvrement (2e paramètre) peut valoir 0.
        if not p.isdigit() or (int(p) == 0 and not (pos == 1 and name != "document-aware")):
            raise ValueError(f"`{spec}` : `{p}` n'est pas un entier positif")
    if name in ("fixed", "recursive-structural", "parent-child") and len(params) > 1 and int(params[1]) >= int(params[0]):
        raise ValueError(f"`{spec}` : le recouvrement doit être inférieur à la taille")
    return Config(spec, name, params)


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------
def terms(text: str) -> list[str]:
    """Minuscules, accents retirés, mots de 2 caractères et plus — sans stemming, par choix."""
    flat = unicodedata.normalize("NFKD", text.lower())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    return [t for t in _TERM_RE.findall(flat) if len(t) > 1 or t.isdigit()]


class BM25:
    def __init__(self, docs: list[str]):
        self.n = len(docs)
        self.lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for i, text in enumerate(docs):
            counts: dict[str, int] = {}
            toks = terms(text)
            self.lengths.append(len(toks))
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for t, tf in counts.items():
                self.postings.setdefault(t, []).append((i, tf))
        self.avg = (sum(self.lengths) / self.n) if self.n else 0.0

    def rank(self, query: str) -> list[int]:
        scores: dict[int, float] = {}
        for t in set(terms(query)):
            plist = self.postings.get(t)
            if not plist:
                continue
            idf = math.log(1 + (self.n - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, tf in plist:
                norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * self.lengths[i] / (self.avg or 1))
                scores[i] = scores.get(i, 0.0) + idf * tf * (BM25_K1 + 1) / norm
        # Égalité départagée par l'ordre des chunks : même entrée, même classement.
        return [i for i, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---------------------------------------------------------------------------
# Golden et résolution des documents
# ---------------------------------------------------------------------------
def resolve_golden(root: Path, mission: int | None, explicit: Path | None) -> tuple[list[Path], str]:
    if explicit is not None:
        return [explicit if explicit.is_absolute() else root / explicit], "explicit"
    if mission is not None:
        draft = paths.validation_dir(root) / f"retrieval-golden-draft-{mission}.jsonl"
        if draft.is_file():
            return [draft], "draft"
    found = [p for p in run_retrieval_eval.golden_files(root)
             if any(it.get("expected_documents") for it in run_retrieval_eval.load_items(p))]
    return found, "dataset"


def alias_map(docs: list[corpus_profile.Document]) -> dict[str, int]:
    """doc_id -> index de document. Un nom de fichier partagé par deux documents n'est PAS un alias."""
    exact: dict[str, int] = {}
    loose: dict[str, list[int]] = {}
    for i, d in enumerate(docs):
        stem = d.rel.rsplit(".", 1)[0] if "." in d.rel.rsplit("/", 1)[-1] else d.rel
        for key in (d.rel, stem, f"{d.root_rel}/{d.rel}"):
            exact.setdefault(key, i)
        name = d.rel.rsplit("/", 1)[-1]
        for key in {name, name.rsplit(".", 1)[0]}:
            loose.setdefault(key, []).append(i)
    for key, idx in loose.items():
        if len(set(idx)) == 1:
            exact.setdefault(key, idx[0])
    return exact


@dataclass
class Query:
    id: str
    text: str
    expected: list[dict[str, Any]]      # doc_id réécrits en index de document (str)


def load_queries(files: list[Path], aliases: dict[str, int]) -> tuple[list[Query], list[str]]:
    """(requêtes mesurables, doc_id introuvables). Une requête sans aucun document résolu est exclue."""
    queries: list[Query] = []
    unresolved: set[str] = set()
    for path in files:
        for item in run_retrieval_eval.load_items(path):
            expected: list[dict[str, Any]] = []
            for entry in item.get("expected_documents") or []:
                doc_id = entry.get("doc_id") if isinstance(entry, dict) else entry
                rel = int(entry.get("relevance", 3)) if isinstance(entry, dict) else 3
                if str(doc_id) in aliases:
                    expected.append({"doc_id": str(aliases[str(doc_id)]), "relevance": rel})
                else:
                    unresolved.add(str(doc_id))
            if expected:
                queries.append(Query(str(item.get("id")), run_retrieval_eval.query_of(item), expected))
    return queries, sorted(unresolved)


# ---------------------------------------------------------------------------
# Mesure
# ---------------------------------------------------------------------------
def measure(config: Config, texts: list[str], queries: list[Query], k: int) -> dict[str, Any]:
    chunking = config.chunk(texts)
    units = chunking.units
    index = BM25([u.text for u in units])
    served_units = chunking.served

    recalls, ndcgs, mrrs, precisions, served_tokens = [], [], [], [], []
    misses: list[str] = []
    for q in queries:
        # Les k premières unités SERVIES (un parent n'est servi qu'une fois,
        # même si trois de ses enfants sont classés), puis leurs documents.
        served: list[int] = []
        for i in index.rank(q.text):
            s = chunking.served_of(i)
            if s not in served:
                served.append(s)
            if len(served) >= k:
                break
        ranked_docs: list[str] = []
        for s in served:
            d = str(served_units[s][0])
            if d not in ranked_docs:
                ranked_docs.append(d)
        relevant = {e["doc_id"] for e in q.expected if e["relevance"] >= retrieval_metrics.RELEVANT_FROM}
        recall = retrieval_metrics.recall_at_k(ranked_docs, q.expected, k)
        recalls.append(recall)
        ndcgs.append(retrieval_metrics.ndcg_at_k(ranked_docs, q.expected, k))
        mrrs.append(retrieval_metrics.mrr(ranked_docs, q.expected))
        precisions.append(sum(1 for s in served if str(served_units[s][0]) in relevant) / len(served) if served else 0.0)
        served_tokens.append(sum(tokens_of(served_units[s][1]) for s in served))
        if recall < 1.0:
            misses.append(q.id)

    sizes = [float(tokens_of(u.text)) for u in units]
    mean = lambda v: (sum(v) / len(v)) if v else 0.0  # noqa: E731
    return {
        "config": config.spec, "strategy": config.strategy, "k": k, "queries": len(queries),
        "recallAtK": round(mean(recalls), 6), "ndcgAtK": round(mean(ndcgs), 6),
        "mrr": round(mean(mrrs), 6), "contextPrecision": round(mean(precisions), 6),
        "chunks": len(units), "parents": len(chunking.parents) if config.strategy == "parent-child" else None,
        "avgChunkTokens": round(mean(sizes), 1),
        "p95ChunkTokens": int(corpus_profile.percentile(sizes, 0.95)),
        "avgServedTokensPerQuery": round(mean([float(t) for t in served_tokens]), 1),
        "indexedTokens": int(sum(sizes)),
        "misses": misses[:20],
    }


def recommend(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Meilleur recall (nDCG en départage) ; à moins de 2 points sur les deux, le moins cher."""
    ranked = sorted(results, key=lambda r: (-r["recallAtK"], -r["ndcgAtK"], r["indexedTokens"], r["config"]))
    best = ranked[0]
    close = [r for r in ranked if best["recallAtK"] - r["recallAtK"] < TIE_POINTS and best["ndcgAtK"] - r["ndcgAtK"] < TIE_POINTS]
    chosen = min(close, key=lambda r: (r["indexedTokens"], r["config"]))
    others = [r for r in ranked if r is not chosen]
    runner = others[0] if others else None
    reason = ("meilleur recall@k" if chosen is best else
              f"à moins de {int(TIE_POINTS * 100)} points de `{best['config']}` sur recall et nDCG, et moins cher à l'ingestion "
              f"({chosen['indexedTokens']} vs {best['indexedTokens']} tokens indexés)")
    return {
        "config": chosen["config"], "reason": reason,
        "runnerUp": runner["config"] if runner else None,
        "recallGap": round(chosen["recallAtK"] - runner["recallAtK"], 6) if runner else None,
        "ndcgGap": round(chosen["ndcgAtK"] - runner["ndcgAtK"], 6) if runner else None,
        "ranking": [r["config"] for r in ranked],
    }


def run(root: Path, *, mission: int | None, specs: list[str], k: int, golden: Path | None,
        corpus: list[Path] | None) -> tuple[Report, dict[str, Any]]:
    report = Report(name="CHUNKING-BENCH", target=str(mission) if mission is not None else str(root))
    payload: dict[str, Any] = {"missionId": mission, "generatedAt": now_iso(), "k": k,
                               "retriever": f"BM25 lexical stdlib (k1={BM25_K1}, b={BM25_B}), sans stemming",
                               "limits": "compare les découpages entre eux ; ne prédit pas le recall absolu "
                                         "du retriever de production (mesuré en G4 par run_retrieval_eval)"}
    configs: list[Config] = []
    for spec in specs:
        try:
            configs.append(parse_config(spec))
        except ValueError as exc:
            report.error("INVALID_ARG", f"--config `{spec}` : {exc}", "voir `chunking-bench --help` pour la grammaire", spec)
    if len({c.spec for c in configs}) < 2:
        report.error("RETRIEVAL_CHUNKING_UNMEASURED", f"{len(configs)} configuration(s) valide(s) : une comparaison en exige au moins deux",
                     "relancer avec >= 2 `--config` (ou sans `--config` : le jeu par défaut compare cinq familles)")
    roots, origin = corpus_profile.corpus_roots(root, corpus)
    docs, _secrets = corpus_profile.collect_documents(root, [r for r in roots if r.is_dir()])
    docs = [d for d in docs if d.text]
    payload.update({"corpus": {"origin": origin, "roots": [paths.rel(root, r) for r in roots], "documents": len(docs)}})
    if not docs:
        report.error("RETRIEVAL_CORPUS_MISSING", f"aucun document lisible sous {payload['corpus']['roots']}",
                     "profiler le corpus d'abord (corpus-profile) ; le banc mesure un découpage, il ne peut pas en mesurer un de rien")

    files, kind = resolve_golden(root, mission, golden)
    files = [f for f in files if f.is_file()]
    payload["golden"] = {"kind": kind, "files": [paths.rel(root, f) for f in files]}
    if not files:
        report.error("GOLDEN_SET_MISSING", "aucun golden de retrieval : ni --golden, ni brouillon, ni jeu porteur d'`expected_documents`",
                     "écrire le brouillon `workspace/.sys/.validation/retrieval-golden-draft-{n}.jsonl` (≥ 30 requêtes, vérité au document)")
    if report.errors:
        return report, payload

    queries, unresolved = load_queries(files, alias_map(docs))
    payload["golden"].update({"queries": len(queries), "unresolvedDocIds": unresolved[:50]})
    if unresolved:
        report.warn("MEASUREMENT_MISSING", f"{len(unresolved)} doc_id du golden introuvables dans le corpus ({', '.join(unresolved[:5])}) — "
                    "leurs requêtes sont exclues si aucun autre document ne se résout",
                    "aligner `doc_id` sur le chemin relatif du document sous la racine du corpus")
    if not queries:
        report.error("MEASUREMENT_MISSING", "aucune requête mesurable : aucun `doc_id` du golden ne se résout dans le corpus",
                     "aligner `doc_id` sur le chemin relatif du document sous la racine du corpus")
        return report, payload
    if len(queries) < MIN_QUERIES:
        report.warn("EVAL_DATASET_TOO_SMALL", f"{len(queries)} requête(s) mesurées < {MIN_QUERIES} : un écart de quelques points n'est pas significatif",
                    "étoffer le golden avant de trancher sur un écart faible")

    texts = [d.text or "" for d in docs]
    results = [measure(c, texts, queries, k) for c in configs]
    payload["results"] = results
    payload["recommendation"] = recommend(results)
    return report, payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Banc comparatif de découpage sur le golden de retrieval — BM25 stdlib, 0 token")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION (brouillon de golden, nom du rapport)")
    p.add_argument("--golden", type=Path, default=None, help="JSONL de requêtes à `expected_documents`")
    p.add_argument("--config", action="append", default=None, help="`stratégie:paramètres`, répétable (défaut : cinq familles)")
    p.add_argument("--k", type=int, default=None, help="défaut : RetrievalK de la Project Config, sinon 8")
    p.add_argument("--corpus", type=Path, action="append", default=None, help="racine de corpus explicite (défaut : celle de corpus-profile)")
    p.add_argument("--out", type=Path, default=None, help="défaut : workspace/.sys/.validation/chunking-bench-{n}.json")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="CHUNKING-BENCH", target=str(root))
    config = load_config(root, report)
    k = args.k or config.get_int("RetrievalK", 8)
    sub, payload = run(root, mission=args.mission, specs=args.config or list(DEFAULT_CONFIGS), k=k,
                       golden=args.golden, corpus=args.corpus)
    report.extend(sub)
    report.target = sub.target
    if payload.get("results") and not args.no_report:
        out = args.out or paths.validation_dir(root) / f"chunking-bench-{args.mission if args.mission is not None else 'all'}.json"
        out = out if out.is_absolute() else root / out
        atomic_write_json(out, payload)
        report.data["written"] = paths.rel(root, out)
    if payload.get("recommendation"):
        report.data["recommendation"] = payload["recommendation"]
        if not args.json:
            for r in payload["results"]:
                print(f"  {r['config']:<32} recall@{k} {r['recallAtK']:.3f}  nDCG {r['ndcgAtK']:.3f}  "
                      f"précision {r['contextPrecision']:.3f}  {r['chunks']} chunks ~{r['avgChunkTokens']:.0f} tok")
            rec = payload["recommendation"]
            print(f"  -> {rec['config']} : {rec['reason']}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())

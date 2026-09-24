"""chunking-bench : une comparaison mesurée, ou un refus — jamais un tableau inventé.

Défendu ici : une seule configuration n'est pas une comparaison ; sans golden
le banc refuse ; la règle « < 2 points -> la moins chère » est appliquée par le
script ; un parent-child sert ses parents ; le résultat est déterministe.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_scripts import chunking_bench as cb

CORPUS = "workspace/assets/corpus"
DRAFT = "workspace/.sys/.validation/retrieval-golden-draft-1.jsonl"
OUT = "workspace/.sys/.validation/chunking-bench-1.json"

TOPICS = {
    "facturation": "La facture mensuelle détaille l'abonnement, les options et la taxe applicable au client.",
    "resiliation": "La résiliation du contrat prend effet après un préavis de trente jours calendaires.",
    "penalites": "Une pénalité de retard s'applique quand le paiement dépasse l'échéance prévue.",
    "livraison": "La livraison du matériel intervient sous cinq jours ouvrés après la commande.",
    "garantie": "La garantie couvre les pannes matérielles pendant vingt-quatre mois.",
}


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _corpus(root: Path) -> None:
    filler = " ".join(f"clause{i} générale sans objet particulier." for i in range(60))
    for name, body in TOPICS.items():
        _write(root, f"{CORPUS}/{name}.md", f"# {name}\n\n{filler}\n\n## Détail\n\n{body}\n\n{filler}\n")


def _golden(root: Path, *, extra: list[dict] | None = None) -> None:
    rows = [{"id": f"retr-{name}-{i:03d}", "input": {"question": body.split(",")[0]},
             "expected_documents": [{"doc_id": f"{name}.md", "relevance": 3}],
             "metadata": {"source": "synthetic", "difficulty": "easy", "class": name}}
            for i, (name, body) in enumerate(TOPICS.items(), start=1)]
    _write(root, DRAFT, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows + (extra or [])) + "\n")


@pytest.fixture
def bench_project(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    _corpus(root)
    _golden(root)
    return root


def _run(root: Path, *configs: str, k: str = "3") -> tuple[int, str, dict]:
    argv = ["--root", str(root), "--mission", "1", "--corpus", CORPUS, "--k", k]
    for c in configs:
        argv += ["--config", c]
    code, out = run_main(cb.main, argv)
    path = root / OUT
    return code, out, (json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {})


def test_a_single_configuration_is_not_a_comparison(bench_project: Path) -> None:
    code, out, payload = _run(bench_project, "fixed:200/20")
    assert code == 1 and "RETRIEVAL_CHUNKING_UNMEASURED" in out
    assert payload == {}


def test_without_golden_the_bench_refuses(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _corpus(root)
    code, out, _ = _run(root, "fixed:200/20", "document-aware:section")
    assert code == 1 and "GOLDEN_SET_MISSING" in out


def test_every_configuration_is_measured_on_the_same_queries(bench_project: Path) -> None:
    code, _, payload = _run(bench_project, "fixed:120/20", "document-aware:section", "parent-child:60/10",
                            "recursive-structural:200/40", "paragraph:150", "sentence:2")
    assert code == 0
    assert payload["golden"]["kind"] == "draft" and payload["golden"]["queries"] == 5
    assert [r["config"] for r in payload["results"]] == ["fixed:120/20", "document-aware:section", "parent-child:60/10",
                                                          "recursive-structural:200/40", "paragraph:150", "sentence:2"]
    for r in payload["results"]:
        assert r["queries"] == 5 and 0.0 <= r["recallAtK"] <= 1.0 and 0.0 <= r["ndcgAtK"] <= 1.0
        assert r["chunks"] > 0 and r["indexedTokens"] > 0
    pc = next(r for r in payload["results"] if r["strategy"] == "parent-child")
    assert pc["parents"] and pc["avgServedTokensPerQuery"] > pc["avgChunkTokens"]   # le parent est servi
    assert payload["recommendation"]["config"] in payload["recommendation"]["ranking"]


def test_within_two_points_the_cheaper_configuration_wins(bench_project: Path) -> None:
    code, _, payload = _run(bench_project, "fixed:400/300", "fixed:400/0")
    assert code == 0
    by = {r["config"]: r for r in payload["results"]}
    assert abs(by["fixed:400/300"]["recallAtK"] - by["fixed:400/0"]["recallAtK"]) < cb.TIE_POINTS
    assert by["fixed:400/0"]["indexedTokens"] < by["fixed:400/300"]["indexedTokens"]
    assert payload["recommendation"]["config"] == "fixed:400/0"


def test_unresolved_doc_ids_are_reported_not_scored_as_misses(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _corpus(root)
    _golden(root, extra=[{"id": "retr-fantome-099", "input": "question", "expected_documents": [{"doc_id": "absent.md", "relevance": 3}],
                          "metadata": {"source": "synthetic", "difficulty": "easy", "class": "x"}}])
    code, out, payload = _run(root, "fixed:200/20", "document-aware:section")
    assert code == 0 and "MEASUREMENT_MISSING" in out
    assert payload["golden"]["queries"] == 5 and payload["golden"]["unresolvedDocIds"] == ["absent.md"]


def test_the_bench_is_deterministic(bench_project: Path) -> None:
    _, _, first = _run(bench_project, "fixed:120/20", "parent-child:60/10")
    _, _, second = _run(bench_project, "fixed:120/20", "parent-child:60/10")
    assert first["results"] == second["results"] and first["recommendation"] == second["recommendation"]


@pytest.mark.parametrize("spec", ["magic:10", "fixed", "fixed:100/100", "parent-child:100", "document-aware:page", "fixed:0/0"])
def test_invalid_configurations_are_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        cb.parse_config(spec)


def test_chunkers_respect_their_contract() -> None:
    text = "# A\n\nun deux trois.\n\n# B\n\nquatre cinq six."
    assert len(cb.sections(text)) == 2
    windows = cb.fixed(" ".join(f"w{i}" for i in range(100)), 20, 5)
    assert len(windows) > 1 and windows[0].split()[-1] in windows[1].split()      # recouvrement
    assert all(cb.tokens_of(c) <= 40 for c in cb.recursive_structural(text * 20, 40, 0))

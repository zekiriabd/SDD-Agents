"""corpus-profile : les chiffres qu'architect-rag cite, et ce qu'il ne doit jamais voir.

Défendu ici : un profil sans corpus refuse plutôt que de rendre un zéro ; le
`.env` posé sous `assets/` n'est jamais ouvert ; doublons et PII sont nommés par
fichier, jamais par valeur ; les stores `kind: local` priment sur le repli.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_project, run_main  # type: ignore
from sdda_scripts import corpus_profile

CORPUS = "workspace/assets/corpus"

FR = ("# Conditions générales\n\n## Article 1\n\nLe client est facturé chaque mois pour les services "
      "et la facture est envoyée par courrier dans les délais prévus par le contrat.\n\n"
      "| ligne | montant |\n|---|---|\n| abonnement | 12 |\n")
EN = "The customer is billed monthly and the invoice is sent by mail within the period set by the contract."


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _profile(root: Path, *extra: str) -> tuple[int, dict]:
    code, _ = run_main(corpus_profile.main, ["--root", str(root), "--mission", "1", *extra])
    out = root / "workspace/.sys/.validation/corpus-1.json"
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {})


def test_without_any_document_the_profile_refuses(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    code, out = run_main(corpus_profile.main, ["--root", str(root), "--mission", "1", "--corpus", CORPUS])
    assert code == 1 and "RETRIEVAL_CORPUS_MISSING" in out
    assert not (root / "workspace/.sys/.validation/corpus-1.json").exists()


def test_counts_types_lengths_structure_and_language(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _write(root, f"{CORPUS}/cgv.md", FR)
    _write(root, f"{CORPUS}/terms.txt", EN)
    _write(root, f"{CORPUS}/grille.csv", "a,b\n1,2\n")
    (root / CORPUS / "scan.pdf").write_bytes(b"%PDF-1.4 binaire")
    code, prof = _profile(root, "--corpus", CORPUS)
    assert code == 0
    assert prof["documents"] == 4
    assert prof["byType"][".md"]["count"] == 1 and prof["byType"][".pdf"]["count"] == 1
    assert prof["unparsed"]["count"] == 1 and prof["unparsed"]["types"] == [".pdf"]
    assert prof["structure"]["docsWithHeadings"] == 1 and prof["structure"]["headingDepthMax"] == 2
    assert prof["structure"]["docsWithTables"] == 1 and prof["structure"]["tabularFiles"] == 1
    assert prof["languages"]["byDocument"]["fr"] == 1 and prof["languages"]["byDocument"]["en"] == 1
    assert prof["lengthTokens"]["max"] >= prof["lengthTokens"]["p50"] > 0


def test_the_env_file_under_assets_is_never_opened(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _write(root, "workspace/assets/doc.md", FR)
    _write(root, "workspace/assets/.env", "LLM_API_KEY=sk-live-ne-doit-jamais-sortir\n")
    code, prof = _profile(root)                       # repli : assets/ entier
    assert code == 0
    assert prof["secretFilesSkipped"] == 1 and prof["documents"] == 1
    assert "sk-live" not in json.dumps(prof)


def test_duplicates_and_pii_are_named_by_file_never_by_value(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    body = " ".join(f"mot{i}" for i in range(200))
    _write(root, f"{CORPUS}/a.md", body)
    _write(root, f"{CORPUS}/b.md", body)                         # doublon exact
    _write(root, f"{CORPUS}/c.md", body + " suffixe ajouté")     # quasi-doublon
    _write(root, f"{CORPUS}/pii.md", "Contact : jean.dupont@corp-reelle.fr pour la facture.")
    code, prof = _profile(root, "--corpus", CORPUS)
    assert code == 0
    assert prof["duplicates"]["exactDocuments"] == 2
    assert any({p["a"].rsplit("/", 1)[1], p["b"].rsplit("/", 1)[1]} <= {"a.md", "b.md", "c.md"}
               for p in prof["duplicates"]["nearPairs"])
    assert prof["pii"]["byType"] == {"e-mail": 1}
    assert "jean.dupont" not in json.dumps(prof)


def test_mixed_access_levels_are_reported(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _write(root, f"{CORPUS}/a.md", "---\ntenant: acme\n---\n" + FR)
    _write(root, f"{CORPUS}/b.md", "---\ntenant: globex\n---\n" + FR)
    code, out = run_main(corpus_profile.main, ["--root", str(root), "--mission", "1", "--corpus", CORPUS])
    assert code == 0 and "RETRIEVAL_ACCESS_MIXED" in out


def test_local_stores_of_stack_md_are_the_default_roots(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_declared_sources")
    code, prof = _profile(root)
    assert code == 0
    assert prof["origin"].startswith("STACK.md")
    assert prof["roots"] == ["workspace/assets/exports"] and prof["documents"] >= 1

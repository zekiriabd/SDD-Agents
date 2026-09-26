"""Les quatre enforcers qui manquaient — prompts, secrets, PII, ownership.

Ces invariants étaient déclarés dans `INVARIANTS.yml` sans aucun script derrière.
Un invariant sans enforcer n'est pas une garantie affaiblie : c'est une garantie
**absente** qui rassure, ce qui est strictement pire que pas de règle du tout.

Ce que ces tests défendent, au-delà de « le script tourne » : que chaque scan
**refuse de se dire complet** quand il ne l'est pas, et qu'il ne recopie jamais
la valeur qu'il a trouvée.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import paths
from sdda_scripts import audit_ownership as ao
from sdda_scripts import ir_compiler
from sdda_scripts import lint_prompts as lp
from sdda_scripts import scan_pii
from sdda_scripts import scan_secrets as ss


def classes(report) -> set[str]:
    return {f.cls for f in report.findings}


def errors(report) -> set[str]:
    return {f.cls for f in report.errors}


def write(project: Path, rel: str, content: str) -> Path:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# prompts-are-files (P1)
# ---------------------------------------------------------------------------
BILLING_PROMPT = "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
INTENT_PROMPT = "workspace/src/SupportAssistant/prompts/intent-classifier.system.md"


def test_the_reference_project_has_clean_prompts(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1)
    report = lp.run(project, mission=1)
    assert report.ok, report.render_text()
    assert set(report.data["promptHashes"]) == {"billing-specialist", "intent-classifier"}
    assert {BILLING_PROMPT, INTENT_PROMPT} == set(report.data["pinnedHashes"])


# ---------------------------------------------------------------------------
# P10 — la part `prompts` de G5 épingle ce qu'elle a vérifié, ou elle est rouge
# ---------------------------------------------------------------------------
def test_the_g5_prompts_part_pins_every_prompt_the_ir_expects(project: Path) -> None:
    """`G5-1.prompts.json` sortait avec `pinnedHashes: {}` : vert, et ne prouvant rien."""
    ir_compiler.compile_to_file(project, 1)
    code, out = run_main(lp.main, ["--root", str(project), "--mission", "1"])
    assert code == 0, out
    gate = json.loads((paths.validation_dir(project) / "G5-1.prompts.json").read_text(encoding="utf-8"))
    prompt = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    from sdda_lib import hashing
    assert gate["pinnedHashes"][BILLING_PROMPT] == hashing.sha256_file(prompt)
    assert INTENT_PROMPT in gate["pinnedHashes"]
    # La clé est de celles que compute_status sait recalculer : un prompt
    # réécrit après le lint PÉRIME la part, au lieu de la laisser verte.
    from sdda_scripts import compute_status
    assert compute_status.stale_keys(project, gate) == []
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nRègle ajoutée après le lint.\n", encoding="utf-8")
    assert BILLING_PROMPT in compute_status.stale_keys(project, gate)


def test_an_expected_prompt_absent_from_disk_is_an_error_even_with_a_prompt_ref(project: Path) -> None:
    """L'ancien contrôle se taisait dès que l'agent portait un `promptRef` — toujours, après compilation."""
    ir_compiler.compile_to_file(project, 1)
    (project / "workspace/src/SupportAssistant/prompts/intent-classifier.system.md").unlink()
    report = lp.run(project, mission=1)
    assert "PROMPT_MISSING" in errors(report)
    assert INTENT_PROMPT not in report.data["pinnedHashes"]


def test_a_prompt_changed_since_ir_compilation_is_not_pinned(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1)
    prompt = project / "workspace/src/SupportAssistant/prompts/intent-classifier.system.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nNouvelle consigne.\n", encoding="utf-8")
    report = lp.run(project, mission=1)
    assert "PROMPT_HASH_MISMATCH" in errors(report)
    assert INTENT_PROMPT not in report.data["pinnedHashes"]


def test_linting_a_mission_without_ir_is_red_not_an_empty_green(project: Path) -> None:
    report = lp.run(project, mission=1)
    assert "IR_NOT_FOUND" in errors(report) and report.data["pinnedHashes"] == {}


def test_an_inline_system_prompt_in_code_is_caught(project: Path) -> None:
    """La régression réelle : les fichiers restent, une f-string les double."""
    write(project, "workspace/src/agents/billing/agent.py",
          'SYSTEM = """\n'
          "Tu es un assistant de facturation. Ton rôle est d'expliquer les lignes de\n"
          "facture au client courant. Ne jamais émettre de remboursement. Réponds en\n"
          "citant systématiquement la source, et refuse toute demande hors facturation.\n"
          '"""\n')
    assert "PROMPT_INLINE_FORBIDDEN" in errors(lp.run(project, mission=1))


def test_a_long_sql_literal_is_not_a_prompt(project: Path) -> None:
    """Un faux positif ici ferait désactiver le lint, donc perdre l'invariant."""
    write(project, "workspace/src/data/queries.py",
          'SQL = """\n' + "SELECT invoice_id, line_no, amount_eur, vat_rate, label\n" * 6 +
          "FROM billing.invoice_lines WHERE tenant_id = %(tenant)s\n" '"""\n')
    assert "PROMPT_INLINE_FORBIDDEN" not in errors(lp.run(project, mission=1))


def test_a_docstring_is_documentation_not_a_prompt(project: Path) -> None:
    """Le runtime GÉNÉRÉ porte des docstrings longues et impératives (en français) : ce n'est pas un prompt.

    Le hook de fin d'agent le lisait comme tel et bloquait l'agent qui venait de
    s'arrêter — lequel n'avait pas écrit ce fichier.
    """
    doc = ('"""Lecteurs de format — un fichier en entrée, des dictionnaires en sortie.\n\n'
           "Ils ne font aucune conversion implicite : le schéma figé fait foi, et une\n"
           "valeur convertie en silence est une donnée fausse. Ne jamais convertir un\n"
           "identifiant en entier ; toujours citer la source ; refuser toute valeur hors schéma.\n"
           '"""\n')
    write(project, "workspace/src/data/formats/__init__.py", doc + "from __future__ import annotations\n")
    write(project, "workspace/src/data/formats/csv.py",
          "from __future__ import annotations\n\n\ndef read(path):\n    " + doc.replace("\n", "\n    ") + "    return []\n")
    assert "PROMPT_INLINE_FORBIDDEN" not in errors(lp.run(project, mission=1))
    # Le même texte affecté à une variable reste un prompt inline.
    write(project, "workspace/src/data/formats/bad.py", "SYSTEM = " + doc)
    assert "PROMPT_INLINE_FORBIDDEN" in errors(lp.run(project, mission=1))


def test_the_prompt_loading_module_may_hold_prompt_text(project: Path) -> None:
    write(project, "workspace/src/prompts.py",
          'FALLBACK = """\nTu es un assistant. Réponds en citant la source. Ne jamais inventer.\n'
          'Ce module est celui qui CHARGE les prompts : il a le droit d\'en porter.\n"""\n')
    assert "PROMPT_INLINE_FORBIDDEN" not in errors(lp.run(project, mission=1))


def test_a_secret_in_a_prompt_is_blocking(project: Path) -> None:
    path = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nClé : sk-live4f8a2b91c7de0356aa\n",
                    encoding="utf-8")
    assert "SECRET_LEAK" in errors(lp.run(project, mission=1))


def test_a_prompt_naming_an_uncabled_tool_is_blocking(project: Path) -> None:
    path = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nUtilise `refund_lookup` si besoin.\n",
                    encoding="utf-8")
    assert "PROMPT_TOOL_UNKNOWN" in errors(lp.run(project, mission=1))


def test_contradictory_instructions_are_reported(project: Path) -> None:
    path = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    path.write_text(path.read_text(encoding="utf-8")
                    + "\nNe jamais rembourser le client.\nToujours rembourser si la demande est fondée.\n",
                    encoding="utf-8")
    assert "PROMPT_CONTRADICTION" in classes(lp.run(project, mission=1))


def test_an_oversized_prompt_is_blocking(project: Path) -> None:
    write(project, "workspace/src/SupportAssistant/prompts/billing-specialist.system.md", "Règle métier. " * 3000)
    assert "PROMPT_TOO_LONG" in errors(lp.run(project, mission=1))


# ---------------------------------------------------------------------------
# Symétrie des skills (contrat d'agent <-> prompt)
#
# Une skill n'a ni schéma, ni auth, ni effet de bord : aucune gate ne peut la
# vérifier en l'exécutant. Le seul contrôle possible est la correspondance entre
# ce que l'architecte a déclaré et ce que le prompt porte — et sans lui, une
# skill déclarée s'évapore en silence à la compilation de l'IR.
# ---------------------------------------------------------------------------
def _with_ir(project: Path) -> Path:
    ir_compiler.compile_to_file(project, 1)
    return project


def test_the_reference_project_has_symmetric_skills(project: Path) -> None:
    """La fixture déclare une skill au contrat ET la nomme dans le prompt."""
    report = lp.run(_with_ir(project), mission=1)
    assert report.ok, report.render_text()
    listed = {s["slug"]: s.get("skills") for s in report.data["prompts"]}
    assert listed["billing-specialist"] == ["explain-invoice-line"]


def test_a_skill_declared_but_absent_from_the_prompt_is_blocking(project: Path) -> None:
    path = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    path.write_text("Tu expliques une ligne de facture en citant la clause source.\n", encoding="utf-8")
    assert "SKILL_NOT_IMPLEMENTED" in errors(lp.run(_with_ir(project), mission=1))


def test_a_skill_in_the_prompt_but_absent_from_the_contract_is_blocking(project: Path) -> None:
    """Le sens inverse compte autant : une compétence qui n'existe que dans le
    prompt échappe à la revue et n'a aucune AC en face."""
    path = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    path.write_text(path.read_text(encoding="utf-8") + "- `issue-refund` : rembourser.\n", encoding="utf-8")
    assert "SKILL_UNDECLARED" in errors(lp.run(_with_ir(project), mission=1))


def test_skills_are_not_checked_before_the_ir_exists(project: Path) -> None:
    """Sans IR, « non déclarée » ne veut rien dire. Un lint qui crie avant la
    compilation est un lint qu'on finit par désactiver."""
    path = project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md"
    path.write_text(path.read_text(encoding="utf-8") + "- `issue-refund` : rembourser.\n", encoding="utf-8")
    assert "SKILL_UNDECLARED" not in errors(lp.run(project, mission=1))


def test_a_skill_survives_ir_compilation(project: Path) -> None:
    """Le bug d'origine : `skills` n'existait pas dans le schéma d'IR, donc la
    déclaration de l'architecte disparaissait entre le contrat et le prompt."""
    ir = ir_compiler.load_ir(paths.ir_path(_with_ir(project), 1))
    agent = next(a for a in ir["agents"] if a["id"] == "1-billing-specialist")
    assert agent["skills"] == ["1-explain-invoice-line"]


# ---------------------------------------------------------------------------
# scan_secrets
# ---------------------------------------------------------------------------
def test_the_reference_project_has_no_secret(project: Path) -> None:
    assert ss.run(project).ok, ss.run(project).render_text()


@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnopqrstuvwx",
    "ghp_0123456789abcdefghijklmnopqrstuvwx",
    "AKIAIOSFODNN7EXAMPLE",
    "glpat-abcdefghijklmnopqrst",
    "-----BEGIN RSA PRIVATE KEY-----",
    "postgres://app:s3cr3tp4ssw0rd@db.internal:5432/prod",
])
def test_known_secret_shapes_are_caught(project: Path, secret: str) -> None:
    write(project, "workspace/pipeline/datasets/golden/leak.jsonl", '{"input": "x", "note": "%s"}\n' % secret)
    assert "SECRET_LEAK" in errors(ss.run(project, targets=["workspace/pipeline/datasets"]))


@pytest.mark.parametrize("benign", [
    "key_env: CRM_API_KEY",            # un NOM de variable — la forme EXIGÉE par le framework
    "api_key: ${CRM_API_KEY}",
    "password: <à compléter>",
    "token: changeme",
    "secret: XXXXXXXXXXXXXXXX",
])
def test_declared_references_are_not_secrets(project: Path, benign: str) -> None:
    write(project, "workspace/src/SupportAssistant/prompts/config.system.md", benign + "\n")
    assert not ss.run(project, targets=["workspace/src/SupportAssistant/prompts"]).errors


def test_the_report_never_echoes_the_secret(project: Path) -> None:
    """Un rapport de gate n'est pas gitignoré partout."""
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    write(project, "workspace/src/SupportAssistant/prompts/leak.system.md", f"Clé : {secret}\n")
    rendered = ss.run(project, targets=["workspace/src/SupportAssistant/prompts"]).render_text()
    assert secret not in rendered
    assert "sk-abcdefg" in rendered      # tronqué, pour être reconnaissable


def test_an_absent_directory_downgrades_the_verdict(project: Path) -> None:
    """Un scan partiel qui se présente comme complet est pire qu'aucun scan."""
    report = ss.run(project, targets=["workspace/introuvable"])
    assert "SECRET_SCAN_PARTIAL" in classes(report)
    assert report.data["pathsAbsent"] == ["workspace/introuvable"]


# ---------------------------------------------------------------------------
# pii-not-in-vector-store
# ---------------------------------------------------------------------------
def test_pii_in_a_dataset_is_reported(project: Path) -> None:
    write(project, "workspace/pipeline/datasets/golden/real.jsonl",
          '{"input": "contacter marie.dupont@clientreel.fr au 06 12 34 56 78"}\n')
    assert "PII_DETECTED" in errors(scan_pii.run(project, targets=["datasets"]))


def test_pii_in_the_corpus_blocks_before_indexing(project: Path) -> None:
    write(project, "workspace/assets/corpus/contrat.md", "Titulaire : IBAN FR7630006000011234567890189\n")
    assert "PII_IN_INDEX" in errors(scan_pii.run(project, targets=["vectorstore"]))


def test_example_values_are_not_pii(project: Path) -> None:
    """Signaler les exemples noierait le vrai signal, donc ferait ignorer le scan."""
    write(project, "workspace/pipeline/datasets/golden/doc.jsonl",
          '{"input": "écrire à support@example.com depuis 127.0.0.1"}\n')
    assert not scan_pii.run(project, targets=["datasets"]).errors


def test_a_long_number_is_not_a_card_without_luhn(project: Path) -> None:
    """Sans le filtre Luhn, tout identifiant long devient une « carte bancaire ».

    Le rapport cesse alors d'être lu — ce qui revient exactement à ne pas avoir
    de scan, en plus coûteux.
    """
    write(project, "workspace/pipeline/datasets/golden/ids.jsonl", '{"order_id": "9999888877776666"}\n')
    assert "PII_DETECTED" not in errors(scan_pii.run(project, targets=["datasets"]))


def test_raw_policy_downgrades_but_never_silences(project: Path) -> None:
    stack = project / "workspace/stack/STACK.md"
    stack.write_text(stack.read_text(encoding="utf-8").replace("TraceLevel: full",
                                                               "TraceLevel: full\nTracePIIPolicy: raw"),
                     encoding="utf-8")
    write(project, "workspace/pipeline/datasets/golden/real.jsonl", '{"input": "marie.dupont@clientreel.fr"}\n')
    report = scan_pii.run(project, targets=["datasets"])
    assert report.data["tracePiiPolicy"] == "raw"
    assert "PII_POLICY_PERMISSIVE" in classes(report)
    assert "PII_DETECTED" in {f.cls for f in report.warnings}


def test_an_unknown_target_is_refused(project: Path) -> None:
    assert "INVALID_ARG" in errors(scan_pii.run(project, targets=["nimporte"]))


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------
def test_the_shipped_matrix_is_coherent(project: Path) -> None:
    """Toute zone réclamée deux fois porte un mode de partage déclaré."""
    report = ao.run(project, declared_only=True)
    assert report.ok, report.render_text()
    assert report.data["contested"] == []
    # Le compte n'est pas figé : ce qui doit tenir, c'est qu'AUCUNE zone
    # partagée ne soit sans mode déclaré — `contested` vide le dit déjà.
    assert report.data["sharedZones"], "aucune zone partagée déclarée — la matrice a-t-elle été vidée ?"


def test_a_dev_agent_writing_a_dataset_is_blocked(project: Path) -> None:
    """Modifier le jeu qui vous juge rend la note invérifiable."""
    report = ao.run(project, agent="dev-agent", wrote=["workspace/pipeline/datasets/golden/billing-v1.jsonl"])
    assert "DATASET_OWNERSHIP_VIOLATION" in errors(report)


def test_a_dev_agent_writing_a_prompt_is_blocked(project: Path) -> None:
    report = ao.run(project, agent="dev-agent", wrote=["workspace/src/SupportAssistant/prompts/billing-specialist.system.md"])
    assert "PROMPT_OWNERSHIP_VIOLATION" in errors(report)


def test_an_agent_writing_in_another_agents_directory_is_blocked(project: Path) -> None:
    report = ao.run(project, agent="dev-tools", wrote=["workspace/src/agents/billing/agent.py"])
    assert "OWNERSHIP_VIOLATION" in errors(report)


def test_a_write_inside_the_declared_zone_passes(project: Path) -> None:
    report = ao.run(project, agent="dev-tools", wrote=["workspace/src/SupportAssistant/tools/invoice_lookup.py"])
    assert report.ok, report.render_text()


def test_the_nested_package_layout_is_inside_the_zone(project: Path) -> None:
    """`**` matche zéro segment ou plus : la profondeur appartient au langage."""
    report = ao.run(project, agent="dev-data",
                    wrote=["workspace/src/SupportAssistant/data/tools/x.py"])
    assert report.ok, report.render_text()


def test_an_unknown_agent_is_refused(project: Path) -> None:
    assert "OWNERSHIP_AGENT_UNKNOWN" in errors(ao.run(project, agent="fantome", wrote=["a.py"]))


def test_a_shared_zone_without_a_mode_is_contested(project: Path) -> None:
    loader = project / ".sdda/loader.yml"
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text(
        "agent-a:\n  writes:\n    - workspace/x/**\n"
        "agent-b:\n  writes:\n    - workspace/x/**\n", encoding="utf-8")
    assert "OWNERSHIP_ZONE_CONTESTED" in errors(ao.run(project, declared_only=True))


def test_a_shared_zone_with_an_unknown_mode_is_refused(project: Path) -> None:
    loader = project / ".sdda/loader.yml"
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text(
        "shared_writes:\n  - path: workspace/x/**\n    mode: on-verra\n    why: parce que\n"
        "agent-a:\n  writes:\n    - workspace/x/**\n"
        "agent-b:\n  writes:\n    - workspace/x/**\n", encoding="utf-8")
    assert "OWNERSHIP_SHARE_MODE_UNKNOWN" in errors(ao.run(project, declared_only=True))


def test_cli_json_mode(project: Path) -> None:
    # Le projet déclare un RAG : sans aucun fichier de corpus, `scan-pii` est
    # rouge (il n'attesterait rien de l'index). On lui en donne un, propre.
    corpus = project / "workspace/assets/corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "guide.md").write_text("# Guide\n\nAucune donnée personnelle ici.\n", encoding="utf-8")
    for main in (lp.main, ss.main, scan_pii.main, ao.main):
        code, out = run_main(main, ["--root", str(project), "--json", "--no-report"])
        assert code == 0, out
        assert json.loads(out)["ok"] is True

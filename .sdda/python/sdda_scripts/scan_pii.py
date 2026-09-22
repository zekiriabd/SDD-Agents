#!/usr/bin/env python3
"""Enforcer de l'invariant `pii-not-in-vector-store` (G7) — 0 token.

Ce que cet enforcer défend : *ce qui entre dans un vector store est difficile à
retirer sélectivement — la décision se prend à l'ingestion, pas après
l'incident.* Un index est reconstruit, pas édité : une PII indexée y reste
jusqu'à la prochaine réindexation complète, et elle ressort dans le contexte du
modèle à chaque récupération qui la matche.

Trois cibles, et la troisième est celle qu'on oublie :

    vectorstore   le corpus destiné à l'indexation (`## Active Retrieval Stack`)
    datasets      golden, holdout, adversarial — **commités**, donc publics dans
                  le dépôt dès qu'un jeu est construit depuis des données réelles
    traces        écrites à chaque run et partagées pour déboguer

La politique vient de `STACK.md` : `TracePIIPolicy` et `MemoryPIIPolicy`.
`redact` ou `hash` rendent une PII détectée bloquante ; `allow` exige un ADR et
transforme le blocage en avertissement tracé.

Le script ne recopie **jamais** une valeur détectée dans son rapport — seulement
son type, son fichier et sa ligne. Un rapport de gate n'est pas gitignoré
partout, et un scanner de PII qui recopie des PII est un incident de plus.

Usage :
    python .sdda/sdda.py scan-pii --mission 1 --target vectorstore --json
    python .sdda/sdda.py scan-pii --target datasets traces
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import read_stack_section_kv  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

TARGETS = ("vectorstore", "datasets", "traces", "prompts")

TARGET_PATHS: dict[str, tuple[str, ...]] = {
    "datasets": ("workspace/datasets",),
    "traces": ("workspace/traces",),
    "prompts": ("workspace/prompts",),
    # Le corpus destiné à l'index : par convention `workspace/data/corpus`, et
    # ce que le contrat de retrieval désigne.
    "vectorstore": ("workspace/data/corpus", "workspace/contracts/retrieval"),
}

#: Motifs à faible taux de faux positifs. Les PII « molles » (un nom propre, une
#: adresse) ne sont PAS détectables par regex sans noyer le rapport — elles se
#: déclarent, par le tag `pii:` des sources et par `TracePIIPolicy`.
PATTERNS: dict[str, str] = {
    "e-mail": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "IBAN": r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Za-z0-9]{4}[ ]?){2,7}[A-Za-z0-9]{1,4}\b",
    "carte bancaire": r"\b(?:\d[ -]?){13,19}\b",
    "téléphone FR": r"\b(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}\b",
    "NIR (sécu FR)": r"\b[12]\d{2}(?:0[1-9]|1[0-2])\d{2}\d{3}\d{3}(?:\s?\d{2})?\b",
    "IPv4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}
COMPILED = [(label, re.compile(p)) for label, p in PATTERNS.items()]

#: Domaines d'exemple : les signaler noierait le vrai signal.
EXAMPLE_RE = re.compile(r"(example\.(com|org|net)|acme\.|test\.|localhost|0\.0\.0\.0|127\.0\.0\.1"
                        r"|\bX{3,}\b|\[REDACTED\]|<[^>]+>)", re.IGNORECASE)

SKIP_SUFFIXES = frozenset({".png", ".jpg", ".pdf", ".zip", ".gz", ".pyc", ".parquet", ".lock"})
SKIP_PARTS = frozenset({"__pycache__", ".git", "node_modules", ".venv"})


def _luhn(digits: str) -> bool:
    """Un numéro à 16 chiffres n'est une carte que s'il passe Luhn.

    Sans ce filtre, tout identifiant long devient une « carte bancaire » et le
    rapport cesse d'être lu — ce qui revient à ne pas avoir de scan.
    """
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def policy_of(root: Path) -> tuple[str, str]:
    trace = str(read_stack_section_kv(root, "Active Observability").get("TracePIIPolicy") or "redact")
    memory = str(read_stack_section_kv(root, "Active Memory Strategy").get("MemoryPIIPolicy") or "redact-before-write")
    return trace.strip().lower(), memory.strip().lower()


def scan_file(root: Path, path: Path, target: str, blocking: bool, report: Report) -> int:
    try:
        text = markdown_io.read_text(path)
    except (OSError, UnicodeDecodeError):
        return 0
    loc = paths.rel(root, path)
    hits = 0
    emit = report.error if blocking else report.warn

    for number, line in enumerate(text.split("\n"), start=1):
        if EXAMPLE_RE.search(line):
            continue
        for label, pattern in COMPILED:
            m = pattern.search(line)
            if not m:
                continue
            if label == "carte bancaire" and not _luhn(m.group(0)):
                continue
            hits += 1
            emit(
                "PII_IN_INDEX" if target == "vectorstore" else "PII_DETECTED",
                f"{loc}:{number} — {label} détecté dans `{target}`",
                fix=("un index se reconstruit, il ne s'édite pas : retirer la PII AVANT l'ingestion, "
                     "ou la rediger à l'ingestion. Après indexation, seule une réindexation complète "
                     "l'enlève") if target == "vectorstore" else
                    ("rediger avant écriture. Un golden set construit depuis des données réelles est "
                     "COMMITÉ : la PII y devient publique dans le dépôt"),
                location=loc,
            )
    return hits


def run(root: Path, targets: list[str] | None = None, mission: int | str | None = None) -> Report:
    report = Report(name="PII", target=str(root))
    wanted = targets or list(TARGETS)

    unknown = sorted(set(wanted) - set(TARGETS))
    if unknown:
        report.error("INVALID_ARG", f"cible(s) inconnue(s) : {unknown}",
                     fix=f"choisir parmi {list(TARGETS)}")
        return report

    trace_policy, memory_policy = policy_of(root)
    # `allow` n'annule pas le scan : il transforme le blocage en avertissement
    # tracé. Un scan désactivé ne laisse aucune trace de ce qu'il aurait vu.
    blocking = trace_policy != "raw"
    if trace_policy == "raw":
        report.warn("PII_POLICY_PERMISSIVE", "`TracePIIPolicy: raw` — les PII détectées ne bloquent pas",
                    fix="`raw` exige un ADR. Sans lui, revenir à `redact`",
                    location="workspace/stack/STACK.md")

    scanned = hits = 0
    absent: list[str] = []
    for target in wanted:
        for rel in TARGET_PATHS[target]:
            base = (root / rel).resolve()
            if not base.exists():
                absent.append(rel)
                continue
            for path in sorted(p for p in base.rglob("*") if p.is_file()):
                if SKIP_PARTS & set(path.parts) or path.suffix.lower() in SKIP_SUFFIXES:
                    continue
                hits += scan_file(root, path, target, blocking, report)
                scanned += 1

    report.data.update({
        "targets": wanted, "filesScanned": scanned, "hits": hits,
        "tracePiiPolicy": trace_policy, "memoryPiiPolicy": memory_policy,
        "pathsAbsent": absent, "mission": mission,
    })
    if absent:
        report.warn("PII_SCAN_PARTIAL", f"chemin(s) absent(s), non scanné(s) : {absent}",
                    fix="normal avant l'ingestion ; anormal en G7 — un scan partiel qui se présente "
                        "comme complet est pire qu'aucun scan")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Invariant `pii-not-in-vector-store` (G7) — 0 token")
    p.add_argument("--mission", default=None)
    p.add_argument("--target", nargs="*", dest="targets", default=None, help=f"défaut : {' '.join(TARGETS)}")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, targets=args.targets, mission=args.mission)

    if not args.no_report:
        try:
            write_gate_report(root, "G7", args.mission or "stack", report, pinned={}, part="pii")
        except OSError:
            pass
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())

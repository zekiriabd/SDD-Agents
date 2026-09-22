#!/usr/bin/env python3
"""Scan de secrets — prompts, traces, datasets, code. 0 token, aucun réseau.

Ce que ce scan défend : *un secret ne doit apparaître dans aucun prompt, aucun
span de trace, aucun dataset.* Le contrat de propagation de `STACK.md` le dit ;
ce script est ce qui le rend opposable.

Les quatre répertoires ne sont pas choisis au hasard — ce sont les quatre par
lesquels un secret sort réellement, et aucun n'est évident :

    prompts/    il finit dans le contexte du modèle, donc chez le fournisseur
    traces/     il est écrit à chaque run, et les traces sont partagées pour déboguer
    datasets/   il est COMMITÉ, parce qu'un golden set se versionne
    src/        le cas classique, et le seul que les outils habituels regardent

Le scan lit des fichiers susceptibles de contenir des secrets. Il n'en recopie
**jamais** la valeur dans son rapport : seulement le motif tronqué, le fichier
et la ligne. Un rapport de gate n'est pas gitignoré partout.

Usage :
    python scan_secrets.py --paths workspace/prompts workspace/traces workspace/datasets workspace/src --json
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
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

DEFAULT_PATHS = ("workspace/prompts", "workspace/traces", "workspace/datasets", "workspace/src")

#: Motifs à préfixe connu — sûrs, quasiment sans faux positif.
PREFIXED = {
    "clé OpenAI": r"sk-[A-Za-z0-9_-]{16,}",
    "token Slack": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "token GitHub": r"gh[pousr]_[A-Za-z0-9]{20,}",
    "PAT GitHub": r"github_pat_[A-Za-z0-9_]{20,}",
    "clé AWS": r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    "clé Google": r"AIza[A-Za-z0-9_-]{30,}",
    "token GitLab": r"glpat-[A-Za-z0-9_-]{16,}",
    "JWT": r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}",
    "clé privée": r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
    "URL avec identifiants": r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]{6,}@",
}

#: Affectation d'une variable au nom sensible. Plus bruyant, donc restreint aux
#: valeurs qui ne ressemblent pas à un placeholder ou à un nom de variable.
ASSIGNMENT = re.compile(
    r"""(?i)\b(api[_-]?key|secret|password|passwd|token|credential|private[_-]?key)\b"""
    r"""\s*[:=]\s*["']?([^\s"',;)]{12,})["']?""")

_PLACEHOLDER = re.compile(
    r"^(\$\{?\w+\}?|<[^>]*>|x{3,}|\*{3,}|\.{3,}|change[_-]?me|todo|tbd|none|null|"
    r"votre[_-]|your[_-]|a[_-]completer|redacted|\[REDACTED\])", re.IGNORECASE)

#: Un NOM de variable en majuscules n'est pas une valeur : `key_env: CRM_API_KEY`
#: est exactement la forme que le framework EXIGE (cf. dataaccess/declared-sources).
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

#: Extensions binaires ou dérivées : les scanner produit du bruit, pas des faits.
SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".whl", ".so", ".dll",
    ".pyc", ".parquet", ".xlsx", ".lock", ".ico", ".woff", ".woff2",
})
SKIP_PARTS = frozenset({"__pycache__", ".git", "node_modules", ".venv", "venv"})

COMPILED = [(label, re.compile(pattern)) for label, pattern in PREFIXED.items()]


def is_placeholder(value: str) -> bool:
    v = value.strip().strip("\"'")
    return bool(_PLACEHOLDER.match(v)) or bool(_ENV_NAME.match(v))


def scan_file(root: Path, path: Path, report: Report) -> int:
    try:
        text = markdown_io.read_text(path)
    except (OSError, UnicodeDecodeError):
        return 0
    loc = paths.rel(root, path)
    found = 0

    for number, line in enumerate(text.split("\n"), start=1):
        for label, pattern in COMPILED:
            m = pattern.search(line)
            if m:
                found += 1
                report.error(
                    "SECRET_LEAK",
                    f"{loc}:{number} — {label} (`{m.group(0)[:10]}…`)",
                    fix="retirer la valeur, la faire porter par la configuration, et FAIRE TOURNER "
                        "le secret : il est dans l'historique dès le premier commit, et le retirer "
                        "du fichier ne l'en retire pas",
                    location=loc,
                )
        m = ASSIGNMENT.search(line)
        if m and not is_placeholder(m.group(2)):
            found += 1
            report.warn(
                "SECRET_LEAK",
                f"{loc}:{number} — affectation `{m.group(1)}` avec une valeur littérale",
                fix="référencer un NOM de variable (`key_env: CRM_API_KEY`) plutôt qu'une valeur. "
                    "Si c'est un exemple, le rendre reconnaissable (`<à compléter>`, `${VAR}`)",
                location=loc,
            )
    return found


def run(root: Path, targets: list[str] | None = None) -> Report:
    report = Report(name="SECRETS", target=str(root))
    scanned = 0
    missing: list[str] = []

    for rel in (targets or list(DEFAULT_PATHS)):
        base = (root / rel).resolve()
        if not base.exists():
            missing.append(rel)
            continue
        candidates = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        for path in candidates:
            if SKIP_PARTS & set(path.parts) or path.suffix.lower() in SKIP_SUFFIXES:
                continue
            scan_file(root, path, report)
            scanned += 1

    report.data.update({"filesScanned": scanned, "pathsAbsent": missing})
    if missing:
        report.warn("SECRET_SCAN_PARTIAL", f"répertoire(s) absent(s), non scanné(s) : {missing}",
                    fix="normal avant la génération ; anormal en G7 — un scan partiel qui se présente "
                        "comme complet est pire qu'aucun scan")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scan de secrets — prompts, traces, datasets, code (0 token)")
    p.add_argument("--paths", nargs="*", default=None, help=f"défaut : {' '.join(DEFAULT_PATHS)}")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, targets=args.paths)

    if not args.no_report:
        try:
            write_gate_report(root, "G7", "stack", report, pinned={}, part="secrets")
        except OSError:
            pass
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())

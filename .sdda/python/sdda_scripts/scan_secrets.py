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
    python .sdda/sdda.py scan-secrets --paths workspace/src workspace/.sys/traces workspace/pipeline/datasets --json
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

#: Les prompts sont sous workspace/src/{App}/prompts/. `reports/` porte les
#: exécutions enregistrées (`runs/{n}-adversarial.jsonl`) : la sortie BRUTE des
#: attaques d'exfiltration — l'endroit où un secret exfiltré atterrit d'abord.
#: `fixtures/`, `calibration/` et `suites/` sont commités comme les datasets.
DEFAULT_PATHS = ("workspace/src", "workspace/.sys/traces", "workspace/.sys/reports", "workspace/pipeline/datasets",
                 "workspace/pipeline/fixtures", "workspace/pipeline/calibration", "workspace/pipeline/suites")

#: Label du motif d'URL à identifiants : son mot de passe peut être un
#: placeholder (`${DB_PASSWORD}`), filtré dans `scan_file`.
URL_CREDENTIALS = "URL avec identifiants"

#: Motifs à préfixe connu — sûrs, quasiment sans faux positif.
PREFIXED = {
    "clé OpenAI / Anthropic": r"sk-[A-Za-z0-9_-]{16,}",
    "clé Stripe": r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",
    "token Hugging Face": r"\bhf_[A-Za-z0-9]{30,}",
    "token Slack": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "token GitHub": r"gh[pousr]_[A-Za-z0-9]{20,}",
    "PAT GitHub": r"github_pat_[A-Za-z0-9_]{20,}",
    "clé AWS": r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    "clé Google": r"AIza[A-Za-z0-9_-]{30,}",
    "token GitLab": r"glpat-[A-Za-z0-9_-]{16,}",
    "clé Azure Storage / Service Bus": r"(?i)(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/]{30,}={0,2}",
    "JWT": r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}",
    "clé privée": r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
    # Mot de passe d'un caractère ou plus : `postgresql://app:pw12@db` passait
    # sous l'ancien minimum de six.
    URL_CREDENTIALS: r"[a-z][a-z0-9+.-]*://[^/\s:@]+:(?P<password>[^/\s:@]+)@",
}

#: Affectation d'une variable au nom sensible. Plus bruyant, donc restreint aux
#: valeurs qui ne ressemblent pas à un placeholder ou à un nom de variable.
#: Le guillemet ouvrant est capturé : il dit si la valeur est un LITTÉRAL.
#:
#: Le nom admet un PRÉFIXE (`AZURE_OPENAI_API_KEY`, `DB_PASSWORD`) : `\b` ne
#: coupe pas après `_`, si bien que la forme la plus courante d'un `.env` — un
#: nom de variable préfixé — n'était jamais reconnue.
ASSIGNMENT = re.compile(
    r"""(?i)(?<![A-Za-z0-9])(?P<name>(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|secret|client[_-]?secret|password|passwd|pwd"""
    r"""|token|credential|private[_-]?key|access[_-]?key|account[_-]?key))\b"""
    r"""\s*[:=]\s*(?P<quote>["']?)(?P<value>[^\s"',;)]{12,})["']?""")

_PLACEHOLDER = re.compile(
    r"^(\$\{?\w+\}?|<[^>]*>|x{3,}|\*{3,}|\.{3,}|change[_-]?me|todo|tbd|none|null|"
    r"votre[_-]|your[_-]|a[_-]completer|redacted|\[REDACTED\])", re.IGNORECASE)

#: Un NOM de variable en majuscules n'est pas une valeur : `key_env: CRM_API_KEY`
#: est exactement la forme que le framework EXIGE (cf. dataaccess/declared-sources).
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

#: Une EXPRESSION de code n'est pas une valeur : `api_key=settings.secret("llmApiKey")
#: .get_secret_value()` est la ligne que le squelette généré écrit pour NE PAS
#: porter le secret en clair — et le scan la comptait comme une fuite, quatre
#: fois par projet. Un identifiant suivi d'un appel, d'un indiçage ou d'un
#: attribut est du code ; la valeur qu'il produit n'est pas dans le fichier.
_CODE_EXPR = re.compile(r"^(?:[A-Za-z_][\w.]*\s*[\(\[]|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$)")

#: Extensions binaires ou dérivées : les scanner produit du bruit, pas des faits.
SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".whl", ".so", ".dll",
    ".pyc", ".parquet", ".xlsx", ".lock", ".ico", ".woff", ".woff2",
    # Binaires des autres écosystèmes (.NET, JVM, Node) : décodés en Latin-1 ils
    # ne rendraient que du bruit.
    ".exe", ".pdb", ".jar", ".class", ".nupkg", ".snupkg", ".dylib", ".bin", ".7z", ".tar",
})
#: `obj/` (.NET) et `.gradle/` (Kotlin) sont des caches de build régénérés.
#: `bin/`, `build/`, `dist/` restent scannés : c'est ce qui PART avec le livrable.
SKIP_PARTS = frozenset({"__pycache__", ".git", "node_modules", ".venv", "venv", "obj", ".gradle",
                        ".mypy_cache", ".pytest_cache", ".ruff_cache"})

#: Les fichiers d'exemple LIVRÉS avec l'application : ils partent dans le
#: dépôt, donc ils sont scannés — c'est le seul `.env*` qui doive l'être.
ENV_SHIPPED = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})

COMPILED = [(label, re.compile(pattern)) for label, pattern in PREFIXED.items()]


def is_env_file(path: Path) -> bool:
    """`.env`, `.env.local`, `.env.production`… — le fichier de secrets, PAS un exemple.

    `workspace/src/{App}/.env` est l'emplacement DÉSIGNÉ des valeurs de
    secrets (ARCHITECTURE §2.ter) : gitignoré, copié depuis `assets/.env`,
    interdit en lecture à tout agent. Le scanner le trouvait sous `src/` et
    rendait `[SECRET_LEAK]` sur tout projet correctement configuré — un scan
    qui rougit sur la configuration qu'il exige apprend à être ignoré.
    """
    name = path.name.lower()
    if name in ENV_SHIPPED:
        return False
    return name == ".env" or name.startswith(".env.")


def is_placeholder(value: str) -> bool:
    v = value.strip().strip("\"'")
    return bool(_PLACEHOLDER.match(v)) or bool(_ENV_NAME.match(v))


def is_code_expression(value: str) -> bool:
    """La valeur est une expression de code, pas un littéral (cf. `_CODE_EXPR`)."""
    return bool(_CODE_EXPR.match(value.strip()))


def is_literal_assignment(m: re.Match[str]) -> bool:
    """Une correspondance d'`ASSIGNMENT` porte-t-elle une VALEUR — ni placeholder, ni code ?

    Partagée avec `tracing.redact_text` : ce que la gate compte comme fuite et
    ce que la trace rédige doivent être la même chose. Une valeur entre
    guillemets est un littéral quoi qu'elle contienne ; sans guillemets, une
    expression de code ne porte pas le secret, elle le CHERCHE.
    """
    value = m.group("value")
    return not is_placeholder(value) and (bool(m.group("quote")) or not is_code_expression(value))


def read_scannable(path: Path) -> str | None:
    """Le texte d'un fichier, UTF-8 d'abord, Latin-1 ensuite ; None s'il est illisible.

    Un fichier non UTF-8 (un `.cs` en cp1252, un `.properties` en ISO-8859-1)
    était sauté en silence — et compté comme scanné. Latin-1 décode tout octet :
    les motifs, tous ASCII, s'y retrouvent à l'identique.
    """
    try:
        return markdown_io.read_text(path)
    except UnicodeDecodeError:
        try:
            return path.read_bytes().decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
        except OSError:
            return None
    except OSError:
        return None


def _is_placeholder_password(m: re.Match[str]) -> bool:
    pw = m.groupdict().get("password") or ""
    return pw.startswith(("$", "<", "{", "%", "*")) or is_placeholder(pw)


def is_env_style_name(name: str) -> bool:
    """`AZURE_OPENAI_API_KEY`, `DB_PASSWORD` : un nom de variable d'environnement."""
    return bool(_ENV_NAME.match(name)) and "_" in name


def scan_file(root: Path, path: Path, report: Report) -> int:
    text = read_scannable(path)
    if text is None:
        return -1
    loc = paths.rel(root, path)
    found = 0

    for number, line in enumerate(text.split("\n"), start=1):
        for label, pattern in COMPILED:
            m = pattern.search(line)
            if m and label == URL_CREDENTIALS and _is_placeholder_password(m):
                continue  # `postgres://app:${DB_PASSWORD}@db` : la forme exigée, pas une fuite
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
        if m and is_literal_assignment(m):
            found += 1
            # Erreur quand le nom est celui d'une variable d'environnement
            # (`DB_PASSWORD=…`) : c'est la ligne d'un `.env` recopiée hors de son
            # fichier désigné, pas un exemple de code. Sinon un avertissement :
            # `api_key = "…"` dans du code reste souvent une valeur de test.
            emit = report.error if is_env_style_name(m.group("name")) else report.warn
            emit(
                "SECRET_LEAK",
                f"{loc}:{number} — affectation `{m.group('name')}` avec une valeur littérale",
                fix="référencer un NOM de variable (`key_env: CRM_API_KEY`) plutôt qu'une valeur. "
                    "Si c'est un exemple, le rendre reconnaissable (`<à compléter>`, `${VAR}`)",
                location=loc,
            )
    return found


def run(root: Path, targets: list[str] | None = None) -> Report:
    report = Report(name="SECRETS", target=str(root))
    scanned = 0
    missing: list[str] = []
    unreadable: list[str] = []

    for rel in (targets or list(DEFAULT_PATHS)):
        base = (root / rel).resolve()
        if not base.exists():
            missing.append(rel)
            continue
        candidates = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        for path in candidates:
            # Parties RELATIVES à la cible : un projet rangé sous un répertoire
            # nommé `obj` ou `venv` ne doit pas voir tout son arbre sauté.
            inner = path.relative_to(base).parts if path != base else ()
            if SKIP_PARTS & set(inner) or path.suffix.lower() in SKIP_SUFFIXES or is_env_file(path):
                continue
            if scan_file(root, path, report) < 0:
                unreadable.append(paths.rel(root, path))
                continue
            scanned += 1

    report.data.update({"filesScanned": scanned, "pathsAbsent": missing, "filesUnreadable": unreadable[:20]})
    if unreadable:
        report.warn("SECRET_SCAN_PARTIAL", f"{len(unreadable)} fichier(s) illisible(s), non scanné(s) : {unreadable[:3]}",
                    fix="un fichier qu'on ne lit pas n'est pas un fichier propre : vérifier ses droits")
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

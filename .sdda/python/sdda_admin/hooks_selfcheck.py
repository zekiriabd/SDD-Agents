#!/usr/bin/env python3
"""Chaque hook câblé DÉMARRE et RÉPOND — exécuté pour de vrai, pas lu. 0 token.

`framework_smoke` vérifie que chaque hook est câblé dans `.claude/settings.json`.
Il ne vérifie pas qu'il s'exécute : un interpréteur absent du PATH, un import
cassé, une commande mal citée, et le hook rend un code ≠ 2 — que le harnais
traite comme une AUTORISATION. Les invariants disparaissent alors exactement
comme s'ils étaient verts. La seule preuve qu'un hook tient est de le lancer.

Pour chaque commande de `settings.json`, telle que le harnais la lancera (même
shell POSIX, même `$CLAUDE_PROJECT_DIR`) :

    payload inoffensif  -> doit répondre 0 (le hook démarre et laisse passer)
    payload à refuser   -> doit répondre 2 (le hook démarre ET juge), pour les
                           hooks d'ownership, de lecture et de shell

Les payloads ne font rien : un hook analyse, il n'exécute pas — le
`echo > …/datasets/…` du payload de refus n'est jamais lancé.

Usage :
    python .sdda/sdda.py hooks-selfcheck            # la CI le joue avec SDDA_HOOKS_STRICT=1
    python .sdda/sdda.py hooks-selfcheck --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

# À l'import : `--help` sort d'argparse avant `main()`, et rendait ses accents
# en mojibake sur une console cp1252.
ensure_utf8_stdout()

ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_S = 30

#: Codes qui disent « le hook n'a pas démarré » : commande introuvable (127),
#: non exécutable (126), alias du Microsoft Store (9009).
_NOT_STARTED = {126, 127, 9009}


def _payloads(matcher: str, root: Path) -> tuple[dict, dict | None]:
    """(payload inoffensif, payload à refuser | None) pour un matcher."""
    tools = set(matcher.split("|"))
    golden = str(root / "workspace" / "pipeline" / "datasets" / "golden" / "hooks-selfcheck.jsonl")
    if tools & {"Bash", "PowerShell"}:
        return ({"tool_name": "Bash", "tool_input": {"command": "echo hooks-selfcheck"}, "cwd": str(root)},
                {"tool_name": "Bash", "agent_type": "dev-agent", "cwd": str(root),
                 "tool_input": {"command": "echo x > workspace/pipeline/datasets/golden/hooks-selfcheck.jsonl"}})
    if tools & {"Write", "Edit"}:
        return ({"tool_name": "Write", "tool_input": {"file_path": str(root / "README.md"), "content": ""},
                 "cwd": str(root)},
                {"tool_name": "Write", "agent_type": "dev-agent", "cwd": str(root),
                 "tool_input": {"file_path": golden, "content": "{}"}})
    if tools & {"Read", "Grep", "Glob"}:
        return ({"tool_name": "Read", "tool_input": {"file_path": str(root / "README.md")}, "cwd": str(root)},
                {"tool_name": "Read", "agent_type": "dev-agent", "cwd": str(root),
                 "tool_input": {"file_path": str(root / "workspace" / "assets" / ".env")}})
    if tools & {"Task", "Agent"}:
        # Un agent hors de tout `applies_to` et hors matrice : chaque hook de
        # spawn doit démarrer, et laisser passer.
        return ({"tool_name": "Agent", "cwd": str(root),
                 "tool_input": {"subagent_type": "hooks-selfcheck", "prompt": "noop", "description": "noop"}}, None)
    return ({"agent_type": "hooks-selfcheck", "cwd": str(root)}, None)


def _shell() -> list[str] | None:
    """Le shell du harnais : Git Bash sous Windows (et non le `bash.exe` de WSL,
    que `System32` place souvent en tête du PATH), sinon bash ou sh."""
    if os.name == "nt":
        for candidate in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
                          Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return [str(candidate), "-c"]
    for name in ("bash", "sh"):
        found = shutil.which(name)
        if found:
            return [found, "-c"]
    return None


def run(root: Path, settings_path: Path) -> Report:
    report = Report(name="HOOKS-SELFCHECK", target=str(root))
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.error("HOOK_SETTINGS_UNREADABLE", f"{settings_path} illisible : {exc}",
                     fix="python .sdda/sdda.py harness-build --harness claude-code")
        return report
    shell = _shell()
    if shell is None:
        report.error("HOOK_SHELL_MISSING", "aucun shell POSIX (bash, sh) sur le PATH",
                     fix="le harnais lance les hooks dans un shell POSIX (Git Bash sous Windows) : l'installer")
        return report
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    results = []
    for event, entries in (settings.get("hooks") or {}).items():
        for entry in entries:
            matcher = str(entry.get("matcher") or "")
            benign, hostile = _payloads(matcher, root)
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command") or "")
                name = command.split("sdda_hooks/")[-1].split('"')[0] if "sdda_hooks/" in command else command
                is_judge = hostile is not None and any(
                    k in name for k in ("ownership", "forbidden_reads", "bash_ownership"))
                for kind, payload, expected in (("inoffensif", benign, 0),
                                                ("à refuser", hostile, 2) if is_judge else (None, None, None)):
                    if kind is None:
                        continue
                    try:
                        proc = subprocess.run([*shell, command], input=json.dumps(payload), text=True,
                                              capture_output=True, env=env, cwd=str(root), timeout=TIMEOUT_S,
                                              encoding="utf-8", errors="replace")
                        code = proc.returncode
                        lines = (proc.stderr or "").strip().splitlines()
                        err = [next((ln for ln in lines if "ne demarre pas" in ln), lines[-1] if lines else "")]
                    except subprocess.TimeoutExpired:
                        code, err = -1, [f"aucune réponse en {TIMEOUT_S} s"]
                    results.append({"event": event, "matcher": matcher, "hook": name, "payload": kind,
                                    "expected": expected, "code": code})
                    if code == expected:
                        continue
                    if code in _NOT_STARTED or "ne demarre pas" in "\n".join(err):
                        report.error("HOOK_INTERPRETER_MISSING",
                                     f"{name} ({event} {matcher}) ne démarre pas : code {code} — {err[0]}",
                                     fix="le harnais AUTORISE un hook qui ne démarre pas. Installer Python, ou "
                                         "fixer `SDDA_PYTHON` (`py -3`, chemin absolu) dans l'environnement du harnais")
                    else:
                        report.error("HOOK_UNRESPONSIVE",
                                     f"{name} ({event} {matcher}) : payload {kind}, code {code} attendu {expected} "
                                     f"— {err[0]}",
                                     fix="lancer la commande à la main avec le payload (cf. --json) ; un hook qui "
                                         "ne refuse pas ce qu'il doit refuser est un enforcer absent")
    report.data["checks"] = results
    if not results:
        report.error("HOOK_SETTINGS_EMPTY", f"{settings_path} ne câble aucun hook",
                     fix="python .sdda/sdda.py harness-build --harness claude-code")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exécute chaque hook câblé avec un payload inoffensif et un à refuser.")
    parser.add_argument("--root", default=str(ROOT), help="racine du projet (défaut : ce dépôt)")
    parser.add_argument("--settings", default=None, help="settings.json à vérifier (défaut : <root>/.claude/settings.json)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    settings = Path(args.settings) if args.settings else root / ".claude" / "settings.json"
    report = run(root, settings)
    if args.json:
        print(json.dumps({"ok": report.ok, "errors": [e.__dict__ for e in report.errors], **report.data},
                         ensure_ascii=False, indent=2, default=str))
    else:
        checks = report.data.get("checks") or []
        print(f"[HOOKS-SELFCHECK] {len(checks)} exécution(s), {len(report.errors)} échec(s)")
        for e in report.errors:
            print(f"ERROR: {e.message}\nCAUSE: [{e.cls}]\nFIX: {e.fix}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())

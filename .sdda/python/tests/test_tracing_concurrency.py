"""Append de spans entre PROCESSUS — une ligne écrite est une ligne entière.

Le runner d'eval exécute jusqu'à `EvalMaxParallel` (4) items en parallèle, et
plusieurs processus peuvent écrire la même trace. L'ancien `open("a").write`
laissait le tampon d'`io` découper une ligne longue en plusieurs appels
système ; sous Windows, le mode append du CRT positionne PUIS écrit. Deux spans
pouvaient donc s'entrelacer, et `read_spans` ignore une ligne illisible : le
span disparaissait sans bruit.

Le test lance de vrais processus (pas des threads : le GIL masquerait le
problème) qui écrivent des spans volontairement longs — plus longs que le
tampon d'`io` (8 Kio) — dans le MÊME fichier, puis exige que chaque ligne se
relise et qu'aucun span ne manque. Il couvre les deux écrivains : celui du
framework (`sdda_lib.tracing.TraceWriter`) et celui de l'application générée
(`templates/runtime/python/app/tracing.py`, chargé tel quel).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]
RUNTIME_TRACING = PYTHON_DIR.parent / "templates/runtime/python/app/tracing.py"

WORKERS = 6
SPANS_PER_WORKER = 60
#: Au-delà du tampon d'`io` (8192) : c'est la taille qui découpait une ligne.
PAYLOAD_CHARS = 12_000

FRAMEWORK_WORKER = """
import sys
sys.path.insert(0, {python_dir!r})
from pathlib import Path
from sdda_lib import tracing
writer = tracing.TraceWriter(Path({root!r}), "run-concurrent")
for i in range({n}):
    writer.emit("sdda.gate G5", span_id=f"w{{sys.argv[1]}}-{{i}}", parent_span_id="root",
                attributes={{"worker": sys.argv[1], "i": i, "blob": sys.argv[1] * {size}}})
"""

RUNTIME_WORKER = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("rt_tracing", {module!r})
mod = importlib.util.module_from_spec(spec)
sys.modules["rt_tracing"] = mod
spec.loader.exec_module(mod)
tracer = mod.Tracer(run_id="run-concurrent", path=Path({path!r}), redact_enabled=False)
for i in range({n}):
    tracer.emit("sdda.gate G5", span_id=f"w{{sys.argv[1]}}-{{i}}", parent_span_id="root",
                attributes={{"worker": sys.argv[1], "i": i, "blob": sys.argv[1] * {size}}})
"""


def _run_workers(script: str) -> None:
    procs = [subprocess.Popen([sys.executable, "-c", script, str(w)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for w in range(WORKERS)]
    for proc in procs:
        _, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, err.decode("utf-8", "replace")[-2000:]


def _assert_every_line_whole(path: Path) -> None:
    raw = path.read_bytes()
    assert b"\r\n" not in raw, "une ligne de trace se termine par \\n seul"
    lines = raw.decode("utf-8").split("\n")
    assert lines[-1] == ""
    seen: set[str] = set()
    for number, line in enumerate(lines[:-1], start=1):
        span = json.loads(line)          # une ligne entrelacée ne se relit pas
        worker = span["attributes"]["worker"]
        assert span["attributes"]["blob"] == worker * PAYLOAD_CHARS, f"ligne {number} tronquée"
        seen.add(span["span_id"])
    expected = {f"w{w}-{i}" for w in range(WORKERS) for i in range(SPANS_PER_WORKER)}
    assert seen == expected, f"{len(expected - seen)} span(s) perdu(s)"


def test_framework_trace_writer_appends_whole_lines_across_processes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "workspace").mkdir(parents=True)
    _run_workers(FRAMEWORK_WORKER.format(python_dir=str(PYTHON_DIR), root=str(root),
                                         n=SPANS_PER_WORKER, size=PAYLOAD_CHARS))
    from sdda_lib import tracing
    path = tracing.trace_path(root, "run-concurrent")
    _assert_every_line_whole(path)
    assert len(list(tracing.read_spans(path))) == WORKERS * SPANS_PER_WORKER


def test_runtime_tracer_appends_whole_lines_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "run-concurrent.jsonl"
    path.parent.mkdir(parents=True)
    _run_workers(RUNTIME_WORKER.format(module=str(RUNTIME_TRACING), path=str(path),
                                       n=SPANS_PER_WORKER, size=PAYLOAD_CHARS))
    _assert_every_line_whole(path)


def test_append_line_writes_one_lf_terminated_line(tmp_path: Path) -> None:
    from sdda_lib import tracing
    target = tmp_path / "t.jsonl"
    tracing.append_line(target, '{"a": 1}')
    tracing.append_line(target, '{"b": 2}\n')
    assert target.read_bytes() == b'{"a": 1}\n{"b": 2}\n'

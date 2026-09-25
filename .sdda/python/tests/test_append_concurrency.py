"""Les journaux append-only du pipeline s'écrivent sous verrou : jamais deux lignes entrelacées.

`runs.jsonl` (état des runs) et `bypasses.jsonl` (audit R5) étaient ouverts en
`open("a")`, non atomique entre écrivains sous Windows. Une ligne entrelacée
est un run que `--resume` ne reprend plus, ou un bypass audité qui n'apparaît
pas dans l'audit. Le rapport de gate, lui, est lu par les hooks pendant qu'un
validateur l'écrit : il doit être entier ou absent, jamais tronqué.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from sdda_lib import paths
from sdda_lib.errors import Report
from sdda_lib.gate_reports import append_bypass_audit, report_path, write_gate_report
from sdda_scripts import sdda_state

THREADS, PER_THREAD = 8, 40
LONG = "x" * 20_000   # bien au-delà du tampon d'`io` : un `write` naïf se découpe


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hammer(fn) -> None:
    threads = [threading.Thread(target=fn, args=(t,)) for t in range(THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()


def test_concurrent_bypass_audits_do_not_interleave(project: Path) -> None:
    def worker(t: int) -> None:
        for i in range(PER_THREAD):
            append_bypass_audit(project, f"G{t}", f"raison {i} " + LONG, operator=f"op-{t}")

    _hammer(worker)
    rows = _lines(paths.audit_dir(project) / "bypasses.jsonl")
    assert len(rows) == THREADS * PER_THREAD
    assert sorted({r["operator"] for r in rows}) == [f"op-{t}" for t in range(THREADS)]
    assert all(r["reason"].endswith(LONG) for r in rows)


def test_concurrent_run_journal_entries_do_not_interleave(project: Path) -> None:
    def worker(t: int) -> None:
        for i in range(PER_THREAD):
            sdda_state._append_journal(project, {"event": "test", "thread": t, "i": i, "pad": LONG})

    _hammer(worker)
    rows = _lines(sdda_state.journal_path(project))
    assert len(rows) == THREADS * PER_THREAD
    assert {(r["thread"], r["i"]) for r in rows} == {(t, i) for t in range(THREADS) for i in range(PER_THREAD)}


def test_gate_reports_are_written_atomically(project: Path) -> None:
    report = Report(name="G0", target="1-SupportAssistant")
    path = write_gate_report(project, "G0", "1-SupportAssistant", report, {"mission": "sha256:" + "0" * 64})
    assert path == report_path(project, "G0", "1-SupportAssistant")
    assert not path.with_name(path.name + ".tmp").exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["gate"] == "G0" and list(data) == sorted(data)

"""test_nightcycle.py — Tests for the Python nightcycle runner."""
import sys
import os
import json
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quilt_saddle_bridge import (
    QuiltSaddleBridge, NightcycleRunner, NightcycleReport, NightcycleAlignmentStats,
)
from quilt_saddle_bridge.nightcycle import (
    EARNED_KEEP_N, EARNED_KEEP_WILSON, WILSON_Z,
)


def make_ledger(path: Path, n_entries: int = 10, success_rate: float = 0.8,
                  alignment: str = "HERMES_405B"):
    """Create a synthetic saddle-format ledger."""
    bridge = QuiltSaddleBridge(ledger_path=str(path), frozens_dir=str(path.parent / "frozens"))
    import time
    for i in range(n_entries):
        success = (i / n_entries) < success_rate
        event = {
            "ts": time.time() + i,
            "kind": "cast.observed",
            "decision": {
                "model": alignment, "opener": "voice",
                "primitive": "Murmur", "rationale": f"test {i}",
            },
            "latency_ms": 1000 + i * 100,
            "success": success, "quality": 0.9 if success else 0.1,
            "cost": 0.001,
        }
        bridge.observe_casting_event(event, cell_id="c", run_id="r", alignment_id=alignment)
    return bridge


def test_runner_loads_ledger():
    """The runner reads a ledger."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        runner = NightcycleRunner(str(path))
        report = runner.run()
        assert report.n_entries == 5


def test_runner_handles_missing_file():
    """The runner handles a missing file gracefully."""
    with tempfile.TemporaryDirectory() as d:
        runner = NightcycleRunner(str(Path(d) / "missing.jsonl"))
        report = runner.run()
        assert report.n_entries == 0
        assert report.n_alignments == 0


def test_runner_aggregates_per_alignment():
    """Stats are aggregated per alignmentId."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        bridge = QuiltSaddleBridge(ledger_path=str(path),
                                     frozens_dir=str(Path(d) / "frozens"))
        import time
        # Mix of two alignments
        for i in range(5):
            bridge.observe_casting_event({
                "ts": time.time() + i, "kind": "cast.observed",
                "decision": {"model": "A", "opener": "voice", "primitive": "Murmur", "rationale": "r"},
                "latency_ms": 1000, "success": True, "quality": 0.9, "cost": 0.001,
            }, cell_id="c", run_id="r", alignment_id="A")
        for i in range(3):
            bridge.observe_casting_event({
                "ts": time.time() + i, "kind": "cast.observed",
                "decision": {"model": "B", "opener": "slate", "primitive": "Murmur", "rationale": "r"},
                "latency_ms": 1000, "success": False, "quality": 0.1, "cost": 0.001,
            }, cell_id="c", run_id="r", alignment_id="B")
        runner = NightcycleRunner(str(path))
        report = runner.run()
        assert report.n_alignments == 2
        a_stats = next(a for a in report.alignments if a.alignmentId == "A")
        b_stats = next(a for a in report.alignments if a.alignmentId == "B")
        assert a_stats.n == 5
        assert a_stats.successes == 5
        assert b_stats.n == 3
        assert b_stats.successes == 0


def test_earned_keep_requires_n_and_wilson():
    """An alignment needs n>=5 AND wilson>=0.5 to earn keep."""
    s = NightcycleAlignmentStats(alignmentId="X")
    s.n = 4
    s.successes = 4
    s.wilson_lower = 0.7
    s.finalize()
    assert s.earned_keep is False  # n too small
    s.n = 5
    s.successes = 1
    s.wilson_lower = 0.0
    s.finalize()
    assert s.earned_keep is False  # wilson too low
    s.n = 5
    s.successes = 5
    s.wilson_lower = 0.0
    s.finalize()
    # 5/5 = 0.566 wilson > 0.5, n >= 5
    assert s.earned_keep is True


def test_report_to_markdown():
    """The markdown report is well-formed."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=10, success_rate=0.9, alignment="HERMES_405B")
        runner = NightcycleRunner(str(path))
        report = runner.run()
        md = report.to_markdown()
        assert "# Nightcycle Report" in md
        assert "Per-alignment summary" in md
        assert "HERMES_405B" in md
        assert "Wilson LB" in md


def test_report_includes_recommendations():
    """The report includes retire/promote recommendations."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        bridge = QuiltSaddleBridge(ledger_path=str(path),
                                     frozens_dir=str(Path(d) / "frozens"))
        import time
        # 6 failures for A
        for i in range(6):
            bridge.observe_casting_event({
                "ts": time.time() + i, "kind": "cast.observed",
                "decision": {"model": "BAD", "opener": "voice", "primitive": "Murmur", "rationale": "r"},
                "latency_ms": 1000, "success": False, "quality": 0.1, "cost": 0.001,
            }, cell_id="c", run_id="r", alignment_id="BAD")
        # 6 successes for B
        for i in range(6):
            bridge.observe_casting_event({
                "ts": time.time() + i, "kind": "cast.observed",
                "decision": {"model": "GOOD", "opener": "voice", "primitive": "Murmur", "rationale": "r"},
                "latency_ms": 1000, "success": True, "quality": 0.9, "cost": 0.001,
            }, cell_id="c", run_id="r", alignment_id="GOOD")
        runner = NightcycleRunner(str(path))
        report = runner.run()
        md = report.to_markdown()
        assert "Retire these" in md or "Retire" in md
        assert "BAD" in md
        assert "GOOD" in md


def test_report_includes_escalations():
    """The report flags escalations."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        bridge = QuiltSaddleBridge(ledger_path=str(path),
                                     frozens_dir=str(Path(d) / "frozens"))
        import time
        for i in range(3):
            bridge.observe_casting_event({
                "ts": time.time() + i, "kind": "cast.observed",
                "decision": {"model": "X", "opener": "voice", "primitive": "Murmur", "rationale": "r"},
                "latency_ms": 1000, "success": False, "quality": 0.1, "cost": 0.001,
            }, cell_id="c", run_id="r", alignment_id="X")
        # Manually patch the entries to mark them as escalations
        lines = path.read_text().splitlines()
        new_lines = []
        for line in lines:
            entry = json.loads(line)
            entry["verdictKind"] = "escalated"
            entry["escalated"] = True
            new_lines.append(json.dumps(entry))
        path.write_text("\n".join(new_lines) + "\n")
        runner = NightcycleRunner(str(path))
        report = runner.run()
        md = report.to_markdown()
        assert "Escalations" in md
        assert "X" in md


def test_report_to_dict_serializable():
    """to_dict produces a JSON-serializable structure."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        runner = NightcycleRunner(str(path))
        report = runner.run()
        d = report.to_dict()
        # Should be JSON-serializable
        s = json.dumps(d, default=str)
        parsed = json.loads(s)
        assert "n_entries" in parsed


def test_cli_writes_report():
    """The CLI writes a markdown report to disk."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        out = Path(d) / "report.md"
        make_ledger(path, n_entries=5)
        # Run the CLI as a subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "quilt_saddle_bridge.nightcycle",
              str(path), "--out", str(out)],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")},
        )
        if result.returncode != 0:
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
        assert result.returncode == 0
        assert out.exists()
        with open(out) as f:
            content = f.read()
            assert "Nightcycle Report" in content


def test_cli_json_output():
    """The CLI can output JSON."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        out = Path(d) / "report.json"
        make_ledger(path, n_entries=3)
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "quilt_saddle_bridge.nightcycle",
              str(path), "--out", str(out), "--json"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")},
        )
        assert result.returncode == 0
        with open(out) as f:
            data = json.load(f)
        assert "n_entries" in data


def test_hash_chain_validated():
    """The runner validates the hash chain."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        runner = NightcycleRunner(str(path))
        report = runner.run()
        assert report.hash_chain_ok is True


def test_total_cost_aggregated():
    """Total cost is aggregated across all entries."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        runner = NightcycleRunner(str(path))
        report = runner.run()
        assert report.total_cost_usd > 0


if __name__ == "__main__":
    import inspect
    tests = [v for k, v in globals().items()
              if k.startswith("test_") and callable(v) and inspect.isfunction(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")

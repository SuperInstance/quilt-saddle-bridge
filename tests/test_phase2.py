"""test_phase2.py — Tests for Phase 2 (saddle → Quilt)."""
import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, "/workspace/quilt-substrate/src")

from quilt_saddle_bridge import (
    QuiltSaddleBridge, SaddleLedgerReader, AlignmentStats, wilson_lower_phase2,
)
from quilt_saddle_bridge.phase2 import wilson_lower
from quilt_substrate.substrate import Substrate, Cell
from quilt_substrate.plugins.casting import (
    QuiltCastingCallPlugin, Probes,
)


def make_ledger(ledger_path: Path, n_entries: int = 10, success_rate: float = 0.8):
    """Create a synthetic saddle-format ledger."""
    import datetime
    bridge = QuiltSaddleBridge(ledger_path=str(ledger_path), frozens_dir=str(ledger_path.parent / "frozens"))
    for i in range(n_entries):
        success = (i / n_entries) < success_rate
        event = {
            "ts": time.time() + i,
            "kind": "cast.observed",
            "decision": {
                "model": "HERMES_405B" if i % 2 == 0 else "SEED_MINI",
                "opener": "voice" if i % 3 == 0 else "slate",
                "primitive": "Murmur",
                "rationale": f"test {i}",
                "confidence": 0.8,
            },
            "latency_ms": 1000 + i * 100,
            "success": success,
            "quality": 0.9 if success else 0.1,
            "cost": 0.001,
        }
        bridge.observe_casting_event(event, cell_id="c", run_id="r",
                                       alignment_id=event["decision"]["model"])
    return bridge


import time


def test_reader_loads_ledger():
    """A reader loads a ledger file."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        reader = SaddleLedgerReader(str(path))
        assert len(reader.entries) == 5


def test_reader_handles_missing_file():
    """A reader on a missing file returns empty."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "nonexistent.jsonl"
        reader = SaddleLedgerReader(str(path))
        assert len(reader.entries) == 0


def test_reader_summarizes():
    """The summarize method aggregates by alignmentId."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=10, success_rate=0.7)
        reader = SaddleLedgerReader(str(path))
        summary = reader.summarize()
        assert summary["n_entries"] == 10
        # Two alignment IDs: HERMES_405B and SEED_MINI
        assert summary["n_alignments"] == 2
        # Each alignment should have stats
        for aid, stats in summary["alignments"].items():
            assert stats["n_total"] > 0
            assert "wilson_lower" in stats
            assert "p90_latency" in stats
            assert "earned_keep" in stats


def test_earned_keep_rule():
    """Earned-keep requires wilson_lower >= 0.5 AND n_total >= 3."""
    stats = AlignmentStats(alignment_id="X")
    stats.n_total = 2
    stats.n_worked = 2
    assert stats.earned_keep is False  # n too small
    stats.n_total = 3
    stats.n_worked = 1
    assert stats.earned_keep is False  # wilson too low
    stats.n_total = 5
    stats.n_worked = 5
    # 5/5 gives wilson_lower=0.566 > 0.5
    assert stats.earned_keep is True
    stats.n_total = 10
    stats.n_worked = 5
    # 5/10 gives wilson_lower < 0.5
    assert stats.earned_keep is False


def test_wilson_lower_function():
    """The wilson_lower function is correct."""
    assert wilson_lower(0, 0) == 0.0
    assert wilson_lower(5, 5) > 0.5
    assert wilson_lower(50, 100) < wilson_lower(80, 100)


def test_to_wilson_profiles():
    """Convert ledger to Wilson-profiles-shaped data."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=10)
        reader = SaddleLedgerReader(str(path))
        profiles = reader.to_wilson_profiles()
        # We have 4 (primitive, opener, model) combinations
        # Murmur is the only primitive; openers are voice/slate; models are HERMES/SEED
        # 2 model × 2 opener = 4 combinations
        assert len(profiles) >= 2
        for key, data in profiles.items():
            assert "lower_bound" in data
            assert "p90_latency" in data
            assert "n" in data
            assert data["n"] > 0


def test_apply_to_plugin():
    """Apply ledger data to a Quilt plugin's Wilson profiles."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=10, success_rate=0.8)
        reader = SaddleLedgerReader(str(path))
        # Set up a plugin
        substrate = Substrate()
        substrate.add(Cell(address="bathy:0", value=4.2, axes=("lat", "lon")))
        probes = Probes(user="casey", app="writers-room", hardware="laptop",
                        time_of_day="evening", weather="calm", crew_state="normal")
        plugin = QuiltCastingCallPlugin(substrate, probes=probes)
        # Apply ledger data
        n = reader.apply_to_plugin(plugin)
        assert n > 0
        # The plugin's Wilson profiles should now have entries
        assert len(plugin.wilson.obs) >= n


def test_verify_hash_chain_valid():
    """A correctly written ledger verifies."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        reader = SaddleLedgerReader(str(path))
        ok, msg = reader.verify_hash_chain()
        assert ok, msg


def test_verify_hash_chain_tampered():
    """A tampered ledger fails verification."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=5)
        # Tamper with one entry
        lines = path.read_text().splitlines()
        entry = json.loads(lines[2])
        entry["verdict"] = "failed"  # change the verdict
        lines[2] = json.dumps(entry)
        path.write_text("\n".join(lines) + "\n")
        reader = SaddleLedgerReader(str(path))
        ok, msg = reader.verify_hash_chain()
        assert not ok


def test_nightcycle_report():
    """The nightcycle report is generated as markdown."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        make_ledger(path, n_entries=10)
        reader = SaddleLedgerReader(str(path))
        report = reader.nightcycle_report()
        assert "# Nightcycle Report" in report
        assert "Hash chain" in report
        assert "Per-alignment summary" in report
        assert "Earned-keep summary" in report


def test_round_trip_quilt_to_saddle_to_quilt():
    """Quilt events → saddle ledger → Quilt Wilson profiles."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        substrate = Substrate()
        substrate.add(Cell(address="bathy:0", value=4.2, axes=("lat", "lon")))
        probes = Probes(user="reyes", app="F/V EILEEN", hardware="tablet",
                        time_of_day="0300", weather="gale", crew_state="tired")
        plugin = QuiltCastingCallPlugin(substrate, probes=probes)
        plugin.install()
        bridge = QuiltSaddleBridge(ledger_path=str(path),
                                     frozens_dir=str(Path(d) / "frozens"))
        # Render 5 times
        for i in range(5):
            substrate.render(opener="chart", role="creative_ideation")
            for event in plugin.witness[-2:]:
                if event.get("kind") == "cast.observed":
                    bridge.observe_casting_event(event, cell_id="bathy", run_id="r")
        # Now read back
        reader = SaddleLedgerReader(str(path))
        assert len(reader.entries) == 5
        # Apply to a new plugin
        new_plugin = QuiltCastingCallPlugin(Substrate(), probes=probes)
        n = reader.apply_to_plugin(new_plugin)
        assert n > 0


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

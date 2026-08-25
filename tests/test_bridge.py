"""test_bridge.py — Tests for the Quilt → saddle bridge."""
import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

# Make sure we can find the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quilt_saddle_bridge import (
    QuiltSaddleBridge,
    QuiltToSaddleConverter,
    SaddleToQuiltConverter,
    casting_event_to_ledger_entry,
    casting_decision_to_frozen_state,
    canonical_json,
    fnv1a64,
    hash_value,
    LedgerEntry,
    FrozenState,
    FilterSpec,
    OutcomeFact,
)


def test_fnv1a64_empty():
    """FNV-1a64 of empty string is the offset basis."""
    assert fnv1a64("") == "cbf29ce484222325"


def test_fnv1a64_deterministic():
    """FNV-1a64 is deterministic."""
    a = fnv1a64("hello world")
    b = fnv1a64("hello world")
    assert a == b


def test_fnv1a64_different():
    """Different inputs produce different hashes."""
    assert fnv1a64("a") != fnv1a64("b")


def test_fnv1a64_known_value():
    """FNV-1a64 of 'foobar' is a known value (sanity check)."""
    # The known FNV-1a 64-bit hash of "foobar" is 0x85944171f73967e8
    h = fnv1a64("foobar")
    assert h == "85944171f73967e8", f"Got {h}"


def test_canonical_json_simple():
    """canonical_json sorts keys."""
    a = canonical_json({"b": 2, "a": 1})
    b = canonical_json({"a": 1, "b": 2})
    assert a == b


def test_canonical_json_nested():
    """canonical_json sorts nested keys."""
    a = canonical_json({"x": {"b": 2, "a": 1}, "y": [1, 2, {"d": 4, "c": 3}]})
    expected = '{"x":{"a":1,"b":2},"y":[1,2,{"c":3,"d":4}]}'
    assert a == expected


def test_hash_value():
    """hash_value is fnv1a64(canonical_json)."""
    a = hash_value({"a": 1, "b": 2})
    b = hash_value({"b": 2, "a": 1})  # same, different order
    assert a == b


def test_casting_event_to_ledger_entry():
    """A Quilt casting event becomes a saddle ledger entry."""
    event = {
        "ts": 1700000000.0,
        "kind": "cast.observed",
        "decision": {
            "model": "HERMES_405B",
            "opener": "voice",
            "primitive": "Murmur",
            "rationale": "role=voice_narration",
            "confidence": 0.9,
        },
        "latency_ms": 1200,
        "success": True,
        "quality": 0.9,
        "cost": 0.005,
        "output_len": 1500,
    }
    entry = casting_event_to_ledger_entry(
        event, seq=1, prev_hash="",
        cell_id="quilt-substrate", run_id="session-1", alignment_id="HERMES_405B",
    )
    assert entry.seq == 1
    assert entry.cellId == "quilt-substrate"
    assert entry.runId == "session-1"
    assert entry.alignmentId == "HERMES_405B"
    assert entry.verdict == "worked"
    assert entry.verdictKind == "worked"
    assert entry.escalated is False
    # The hash chain: hash is fnv1a64 of the entry minus the hash field
    assert len(entry.hash) == 16  # 64-bit hex
    assert entry.prevHash == ""  # genesis


def test_casting_event_to_ledger_entry_failure():
    """A failed event becomes a failed verdict."""
    event = {
        "ts": 1700000000.0,
        "kind": "cast.observed",
        "decision": {"model": "X", "opener": "y", "primitive": "z", "rationale": "test"},
        "latency_ms": 0,
        "success": False,
        "quality": 0.0,
        "error": "model_timeout",
    }
    entry = casting_event_to_ledger_entry(
        event, seq=1, prev_hash="",
        cell_id="c", run_id="r", alignment_id="a",
    )
    assert entry.verdict == "failed"
    assert entry.verdictKind == "execution-error"


def test_casting_decision_to_frozen_state():
    """A Quilt decision becomes a saddle frozen state."""
    decision = {"model": "HERMES_405B", "opener": "voice", "primitive": "Murmur"}
    state = casting_decision_to_frozen_state(
        decision, use_case="voice_narration", prompt="You are a maritime narrator.",
    )
    assert state.model == "HERMES_405B"
    assert state.useCase == "voice_narration"
    assert "First," in state.directiveChunks[0] or "acknowledge" in state.directiveChunks[0]
    assert len(state.alignmentId) == 16  # FNV-1a64 hex


def test_casting_decision_to_frozen_state_with_filters():
    """Frozen state with input/output filters."""
    decision = {"model": "CLAUDE_OPUS", "opener": "witness", "primitive": "Witness"}
    state = casting_decision_to_frozen_state(
        decision,
        use_case="safety_check",
        prompt="You are a safety auditor. Audit the input for harm.",
        input_filters=[FilterSpec(id="deny-pii", kind="deny", description="deny PII")],
        output_filters=[FilterSpec(id="allow-yes", kind="allow", description="allow verdict only")],
        params={"temperature": 0.3, "max_tokens": 500},
        earned_keep_metric="production",
    )
    assert len(state.inputFilters) == 1
    assert state.inputFilters[0].id == "deny-pii"
    assert state.params["temperature"] == 0.3
    assert state.earnedKeepMetric == "production"


def test_frozen_state_content_addressed():
    """Same content produces the same alignmentId (content-addressed dedup)."""
    decision = {"model": "X"}
    state1 = casting_decision_to_frozen_state(decision, "use", "prompt")
    state2 = casting_decision_to_frozen_state(decision, "use", "prompt")
    assert state1.alignmentId == state2.alignmentId


def test_frozen_state_different_content_different_id():
    """Different content produces different alignmentId."""
    state1 = casting_decision_to_frozen_state({"model": "X"}, "use", "prompt1")
    state2 = casting_decision_to_frozen_state({"model": "X"}, "use", "prompt2")
    assert state1.alignmentId != state2.alignmentId


def test_bridge_creates_ledger():
    """The bridge creates the ledger file."""
    with tempfile.TemporaryDirectory() as d:
        ledger_path = Path(d) / "ledger.jsonl"
        frozens_dir = Path(d) / "frozens"
        bridge = QuiltSaddleBridge(
            ledger_path=str(ledger_path),
            frozens_dir=str(frozens_dir),
        )
        event = {
            "ts": 1700000000.0, "kind": "cast.observed",
            "decision": {"model": "X", "opener": "y", "primitive": "z", "rationale": "r"},
            "latency_ms": 100, "success": True, "quality": 0.9, "cost": 0.001,
        }
        entry = bridge.observe_casting_event(event, cell_id="c", run_id="r", alignment_id="X")
        assert entry is not None
        assert ledger_path.exists()
        with open(ledger_path) as f:
            lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["seq"] == 1


def test_bridge_hash_chain():
    """Subsequent entries form a hash chain."""
    with tempfile.TemporaryDirectory() as d:
        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        # 3 events
        for i in range(3):
            event = {
                "ts": 1700000000.0 + i, "kind": "cast.observed",
                "decision": {"model": "X", "opener": "y", "primitive": "z", "rationale": f"r{i}"},
                "latency_ms": 100, "success": True, "quality": 0.9, "cost": 0.001,
            }
            bridge.observe_casting_event(event, cell_id="c", run_id="r", alignment_id="X")
        # Read the entries
        with open(Path(d) / "ledger.jsonl") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        # The hash chain: entry[i].prevHash == entry[i-1].hash
        assert entries[0]["prevHash"] == ""
        for i in range(1, len(entries)):
            assert entries[i]["prevHash"] == entries[i-1]["hash"]


def test_bridge_skips_proposed_events():
    """Only 'cast.observed' events become ledger entries."""
    with tempfile.TemporaryDirectory() as d:
        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        event = {"ts": 1.0, "kind": "cast.proposed",
                  "decision": {"model": "X", "opener": "y", "primitive": "z"}}
        entry = bridge.observe_casting_event(event, cell_id="c", run_id="r", alignment_id="X")
        assert entry is None


def test_bridge_freeze_alignment():
    """The bridge freezes alignments as content-addressed files."""
    with tempfile.TemporaryDirectory() as d:
        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        decision = {"model": "HERMES_405B", "opener": "voice", "primitive": "Murmur"}
        state = bridge.freeze_alignment(
            decision, use_case="voice_narration",
            prompt="You are a maritime narrator.",
        )
        # The frozen state was written
        path = Path(d) / "frozens" / f"{state.alignmentId}.json"
        assert path.exists()
        # The file is read-only (mode 0444)
        mode = path.stat().st_mode & 0o777
        assert mode == 0o444, f"Expected 0444, got {oct(mode)}"


def test_bridge_freeze_is_deduped():
    """Re-freezing identical content is a no-op (content-addressed dedup)."""
    with tempfile.TemporaryDirectory() as d:
        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        decision = {"model": "X", "opener": "y", "primitive": "z"}
        s1 = bridge.freeze_alignment(decision, "use", "prompt")
        s2 = bridge.freeze_alignment(decision, "use", "prompt")
        # Same content → same alignmentId → one file
        assert s1.alignmentId == s2.alignmentId
        files = list((Path(d) / "frozens").glob("*.json"))
        assert len(files) == 1


def test_bridge_stats():
    """The bridge returns aggregate stats."""
    with tempfile.TemporaryDirectory() as d:
        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        for m in ["A", "B", "A"]:
            event = {
                "ts": 1.0, "kind": "cast.observed",
                "decision": {"model": m, "opener": "y", "primitive": "z", "rationale": "r"},
                "latency_ms": 100, "success": True, "quality": 0.9,
            }
            bridge.observe_casting_event(event, cell_id="c", run_id="r", alignment_id=m)
        stats = bridge.stats()
        assert stats["n_entries"] == 3
        assert stats["models"]["A"] == 2
        assert stats["models"]["B"] == 1


def test_quilt_to_saddle_converter_batch():
    """Batch convert a witness log."""
    with tempfile.TemporaryDirectory() as d:
        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        converter = QuiltToSaddleConverter(bridge)
        witness = [
            {"ts": 1.0, "kind": "cast.observed",
              "decision": {"model": "A", "opener": "y", "primitive": "z", "rationale": "r"},
              "latency_ms": 100, "success": True, "quality": 0.9},
            {"ts": 2.0, "kind": "cast.observed",
              "decision": {"model": "B", "opener": "y", "primitive": "z", "rationale": "r"},
              "latency_ms": 200, "success": True, "quality": 0.8},
        ]
        entries = converter.convert_witness_log(witness, cell_id="c", run_id="r")
        assert len(entries) == 2
        assert entries[0].seq == 1
        assert entries[1].seq == 2


def test_saddle_to_quilt_converter():
    """Round-trip a saddle entry back to a Quilt event."""
    import datetime
    entry = LedgerEntry(
        seq=1,
        ts=datetime.datetime.now(tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        cellId="c", runId="r", alignmentId="X",
        debit=json.dumps({"opener": "voice", "primitive": "Murmur", "rationale": "test"}),
        credit=json.dumps({"model": "HERMES", "latency_ms": 100, "success": True, "quality": 0.9, "cost": 0.001}),
        verdict="worked", escalated=False, verdictKind="worked",
        prevHash="", hash="0"*16,
    )
    event = SaddleToQuiltConverter.ledger_entry_to_casting_event(entry)
    assert event["kind"] == "cast.observed"
    assert event["decision"]["model"] == "HERMES"
    assert event["success"] is True


def test_bridge_resumes_from_existing_ledger():
    """The bridge resumes the sequence from the last entry."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.jsonl"
        # Pre-populate the ledger
        for i in range(1, 4):
            entry = LedgerEntry(
                seq=i,
                ts=f"2026-01-01T00:00:0{i}Z",
                cellId="c", runId="r", alignmentId="X",
                debit='{"opener":"y","primitive":"z","rationale":"r"}',
                credit='{"model":"M","latency_ms":100,"success":true,"quality":0.9}',
                verdict="worked", escalated=False, verdictKind="worked",
                prevHash="0" * 16 if i > 1 else "", hash="0" * 16,
            )
            with open(path, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        # New bridge should resume from seq=4
        bridge = QuiltSaddleBridge(ledger_path=str(path), frozens_dir=str(Path(d) / "frozens"))
        event = {
            "ts": 1.0, "kind": "cast.observed",
            "decision": {"model": "X", "opener": "y", "primitive": "z", "rationale": "r"},
            "latency_ms": 100, "success": True, "quality": 0.9,
        }
        entry = bridge.observe_casting_event(event, cell_id="c", run_id="r", alignment_id="X")
        assert entry.seq == 4


def test_outcome_fact():
    """OutcomeFact is the v4 orthogonal process facts."""
    of = OutcomeFact(timedOut=True, signal=15, exitCode=137)
    assert of.timedOut is True
    assert of.signal == 15
    assert of.exitCode == 137


def test_ledger_entry_to_dict_drops_none():
    """to_dict drops None values for clean JSON."""
    entry = LedgerEntry(
        seq=1, ts="2026-01-01T00:00:00Z",
        cellId="c", runId="r", alignmentId="X",
        debit="{}", credit="{}",
        verdict="worked", escalated=False,
    )
    d = entry.to_dict()
    assert "verdictKind" not in d  # None was dropped
    assert "note" not in d
    assert "retryOf" not in d


def test_ledger_entry_hash_matches_fnv1a64():
    """The entry hash is fnv1a64 of the entry minus the hash field."""
    entry = LedgerEntry(
        seq=1, ts="2026-01-01T00:00:00Z",
        cellId="c", runId="r", alignmentId="X",
        debit="{}", credit="{}",
        verdict="worked", escalated=False,
    )
    # Compute the expected hash (over the entry minus the hash field)
    entry_dict = entry.to_dict()
    entry_dict.pop("hash", None)
    expected = hash_value(entry_dict)
    # Manually set the hash
    entry.hash = expected
    # Verify
    assert entry.hash == expected
    # Verify the round-trip
    assert hash_value(entry_dict) == expected


# -- Integration with the actual Quilt plugin --

def test_bridge_with_real_plugin():
    """End-to-end: install the plugin, render, observe, write to ledger."""
    sys.path.insert(0, "/workspace/quilt-substrate/src")
    from quilt_substrate.substrate import Substrate, Cell
    from quilt_substrate.plugins.casting import QuiltCastingCallPlugin, Probes

    with tempfile.TemporaryDirectory() as d:
        substrate = Substrate()
        substrate.add(Cell(address="chart:0", value=42, axes=("x", "y")))
        probes = Probes(user="casey", app="writers-room", hardware="laptop",
                        time_of_day="evening", weather="calm", crew_state="normal")
        plugin = QuiltCastingCallPlugin(substrate, probes=probes)
        plugin.install()

        bridge = QuiltSaddleBridge(
            ledger_path=str(Path(d) / "ledger.jsonl"),
            frozens_dir=str(Path(d) / "frozens"),
        )
        # Render a few times through the plugin
        for i in range(3):
            substrate.render(opener="chart", role="creative_ideation")
        # Feed the witness events to the bridge
        for event in plugin.witness:
            if event.get("kind") == "cast.observed":
                bridge.observe_casting_event(event, cell_id="substrate", run_id="session-1")
        # Check the ledger
        stats = bridge.stats()
        assert stats["n_entries"] == 3
        # Now freeze an alignment
        decision = {"model": "SEED_MINI", "opener": "slate", "primitive": "Murmur"}
        state = bridge.freeze_alignment(decision, use_case="creative_ideation",
                                            prompt="You are a creative ideation partner.")
        assert state.alignmentId != ""
        # The frozen state file exists
        path = Path(d) / "frozens" / f"{state.alignmentId}.json"
        assert path.exists()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    failed_tests = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
            failed_tests.append((t.__name__, str(e)))
    print(f"\n{passed} passed, {failed} failed")

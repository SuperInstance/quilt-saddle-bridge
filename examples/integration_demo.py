"""integration_demo.py — End-to-end demo of the Quilt ↔ saddle bridge.

Shows the full loop:
1. Install the Quilt casting-call plugin on a substrate
2. Render 5 times through the plugin
3. Feed the witness events to the bridge
4. The bridge writes saddle-format ledger entries + frozen states
5. Show the output: ledger.jsonl + frozens/<hash>.json
6. Compare to the static casting-call (no plugin)

This is the proof: the Quilt + saddle work together. The Quilt picks the model;
saddle records what happened (double-entry) and freezes how to use the model.
The cowboy reads saddle's nightcycle report to see which combinations earned
their keep.
"""
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, "/workspace/quilt-substrate/src")
sys.path.insert(0, "/workspace/quilt-saddle-bridge/src")

from quilt_substrate.substrate import Substrate, Cell
from quilt_substrate.plugins.casting import QuiltCastingCallPlugin, Probes
from quilt_saddle_bridge import (
    QuiltSaddleBridge,
    FilterSpec,
)


def banner(text):
    print()
    print("=" * 60)
    print(f"  {text}")
    print("=" * 60)


def main():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ledger_path = d / "ledger.jsonl"
        frozens_dir = d / "frozens"

        banner("Quilt + saddle bridge — End-to-end demo")
        print(f"Working dir: {d}")

        # Step 1: Set up the substrate
        substrate = Substrate()
        substrate.add(Cell(address="bathy:0", value=4.2, axes=("lat", "lon")))
        substrate.add(Cell(address="bathy:1", value=5.8, axes=("lat", "lon")))
        substrate.add(Cell(address="tide:current", value="ebb", axes=("time",)))
        print(f"Substrate has {len(substrate)} cells")

        # Step 2: Install the plugin (F/V EILEEN in a 0300 gale)
        probes = Probes(
            user="reyes", app="F/V EILEEN navigation", hardware="tablet",
            time_of_day="0300", weather="gale", crew_state="tired",
        )
        plugin = QuiltCastingCallPlugin(substrate, probes=probes)
        plugin.install()
        print("Plugin installed on substrate")

        # Step 3: Set up the bridge
        bridge = QuiltSaddleBridge(
            ledger_path=str(ledger_path),
            frozens_dir=str(frozens_dir),
        )
        print(f"Bridge writing to {ledger_path}")
        print(f"Frozens going to {frozens_dir}")

        # Step 4: Render 5 times through the plugin
        banner("Step 4: Render 5 times through the plugin")
        for i in range(5):
            result = substrate.render(opener="tide", role="sensory_creative")
            print(f"  Render {i+1}: {type(result).__name__}, {len(str(result))} chars")
            # Feed the witness event to the bridge
            for event in plugin.witness[-2:]:  # proposed + observed
                if event.get("kind") == "cast.observed":
                    bridge.observe_casting_event(event, cell_id="bathy", run_id=f"session-{i}")
        print(f"Bridge has {bridge.stats()['n_entries']} ledger entries")

        # Step 5: Freeze an alignment
        banner("Step 5: Freeze a casting decision as a saddle FrozenState")
        decision = {"model": "DEEPSEEK_V4_FLASH", "opener": "tide", "primitive": "Murmur"}
        state = bridge.freeze_alignment(
            decision, use_case="sensory_creative",
            prompt="You are a maritime navigator's assistant. Report tidal current, "
                    "depth, and any hazards. Speak terse and clear.",
            input_filters=[
                FilterSpec(id="strip-noise", kind="transform", description="denoise radar input"),
            ],
            output_filters=[
                FilterSpec(id="voice-only", kind="allow", description="allow voice output only"),
            ],
            params={"temperature": 0.4, "max_tokens": 200, "voice": "maritime"},
            directive_chunks=[
                "Acknowledge the current depth and tide state.",
                "Flag any hazard within 100m.",
                "Speak the smallest, most useful sentence first.",
            ],
            earned_keep_metric="task-approval",
        )
        print(f"Frozen state ID (alignmentId): {state.alignmentId}")
        print(f"  Model: {state.model}")
        print(f"  Use case: {state.useCase}")
        print(f"  Filters: {len(state.inputFilters)} input, {len(state.outputFilters)} output")
        print(f"  Chunks: {len(state.directiveChunks)}")
        frozen_path = frozens_dir / f"{state.alignmentId}.json"
        print(f"  Written to: {frozen_path.name}")
        print(f"  File mode: {oct(frozen_path.stat().st_mode & 0o777)}")

        # Step 6: Show the ledger
        banner("Step 6: The ledger (saddle-format JSONL)")
        with open(ledger_path) as f:
            for i, line in enumerate(f, 1):
                entry = json.loads(line)
                credit = json.loads(entry["credit"])
                print(f"  Entry {i}: seq={entry['seq']}, model={credit['model']}, "
                       f"verdict={entry['verdict']}/{entry.get('verdictKind', '?')}, "
                       f"latency={credit['latency_ms']}ms")

        # Step 7: Hash chain verification
        banner("Step 7: Hash chain verification")
        with open(ledger_path) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        ok = True
        for i in range(1, len(entries)):
            if entries[i]["prevHash"] != entries[i-1]["hash"]:
                ok = False
                print(f"  ❌ Entry {i+1}: prevHash mismatch!")
        if ok:
            print(f"  ✓ Hash chain valid across all {len(entries)} entries")
            print(f"  Genesis: {entries[0]['prevHash'] or '(empty)'}")
            print(f"  Tip:    {entries[-1]['hash']}")

        # Step 8: Round-trip back to Quilt
        banner("Step 8: Round-trip (saddle entry → Quilt event)")
        from quilt_saddle_bridge import SaddleToQuiltConverter, LedgerEntry
        e = entries[0]
        le = LedgerEntry(
            seq=e["seq"], ts=e["ts"], cellId=e["cellId"], runId=e["runId"],
            alignmentId=e["alignmentId"], debit=e["debit"], credit=e["credit"],
            verdict=e["verdict"], escalated=e["escalated"],
            verdictKind=e.get("verdictKind"), note=e.get("note"),
            prevHash=e["prevHash"], hash=e["hash"],
        )
        event = SaddleToQuiltConverter.ledger_entry_to_casting_event(le)
        print(f"  Round-tripped event: {event['kind']}")
        print(f"  Model: {event['decision']['model']}")
        print(f"  Opener: {event['decision']['opener']}")
        print(f"  Latency: {event['latency_ms']}ms")

        # Step 9: Final stats
        banner("Step 9: Final stats")
        print(json.dumps(bridge.stats(), indent=2))

        print()
        print("=" * 60)
        print("  THE LOOP CLOSES")
        print("=" * 60)
        print()
        print("The Quilt knows WHICH model to use (the casting-call).")
        print("Saddle knows HOW to use it (frozen state: prompt, filters, params).")
        print("Saddle's ledger records EVERY interaction (debit/credit/verdict).")
        print("The nightcycle reads the ledger and refines the alignment.")
        print("The cowboy (rider) reads the morning report.")
        print()
        print("Together: the harness. The dog. The quilt. The animal, maturing.")


if __name__ == "__main__":
    main()

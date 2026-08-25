"""phase2_demo.py — Phase 2 demo: saddle ledger → Quilt Wilson profiles.

This shows the loop CLOSING:
1. Phase 1: Quilt plugin renders → bridge writes saddle ledger (5 entries)
2. Phase 2: Bridge reads the saddle ledger → applies to a NEW Quilt plugin
3. The new plugin now has the wisdom of the previous run

The Quilt's plugin doesn't have to learn from scratch. It can be primed
from saddle's ledger — the cowboy's nightcycle runs overnight, refines
the alignment, and the morning plugin has the lessons baked in.

This is the loop: Quilt → saddle → nightcycle → saddle → Quilt. Cowboy reads.
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
    QuiltSaddleBridge, SaddleLedgerReader, FilterSpec,
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

        banner("Phase 2: closing the loop")
        print("Quilt → saddle → nightcycle → Quilt")
        print()
        print(f"Working dir: {d}")

        # Step 1: Phase 1 — Quilt writes to saddle
        banner("Step 1: Phase 1 — Quilt renders, bridge writes saddle ledger")
        substrate1 = Substrate()
        substrate1.add(Cell(address="bathy:0", value=4.2, axes=("lat", "lon")))
        probes1 = Probes(user="reyes", app="F/V EILEEN", hardware="tablet",
                          time_of_day="0300", weather="gale", crew_state="tired")
        plugin1 = QuiltCastingCallPlugin(substrate1, probes=probes1)
        plugin1.install()
        bridge = QuiltSaddleBridge(
            ledger_path=str(ledger_path),
            frozens_dir=str(frozens_dir),
        )
        for i in range(5):
            substrate1.render(opener="chart", role="creative_ideation")
            for event in plugin1.witness[-2:]:
                if event.get("kind") == "cast.observed":
                    bridge.observe_casting_event(event, cell_id="bathy", run_id=f"s{i}")
        print(f"Phase 1 wrote {bridge.stats()['n_entries']} entries to {ledger_path.name}")

        # Step 2: Read back the saddle ledger
        banner("Step 2: Phase 2 — read the saddle ledger back")
        reader = SaddleLedgerReader(str(ledger_path))
        print(f"Read {len(reader.entries)} entries from {ledger_path.name}")

        # Step 3: Verify the hash chain
        banner("Step 3: Verify the hash chain")
        ok, msg = reader.verify_hash_chain()
        print(f"  {msg}")
        assert ok

        # Step 4: Summarize the ledger
        banner("Step 4: Summarize")
        summary = reader.summarize()
        print(f"  Total entries: {summary['n_entries']}")
        print(f"  Total alignments: {summary['n_alignments']}")
        for aid, s in summary["alignments"].items():
            print(f"    {aid}: {s['n_total']} entries, "
                   f"{s['success_rate']:.0%} success, "
                   f"wilson={s['wilson_lower']:.3f}, "
                   f"earned_keep={'✓' if s['earned_keep'] else '✗'}")

        # Step 5: Apply to a NEW Quilt plugin
        banner("Step 5: Apply to a new Quilt plugin")
        substrate2 = Substrate()
        substrate2.add(Cell(address="bathy:0", value=4.2, axes=("lat", "lon")))
        probes2 = Probes(user="reyes", app="F/V EILEEN", hardware="tablet",
                          time_of_day="0300", weather="gale", crew_state="tired")
        plugin2 = QuiltCastingCallPlugin(substrate2, probes=probes2)
        n_updated = reader.apply_to_plugin(plugin2)
        print(f"  Applied {n_updated} profiles to plugin2")
        print(f"  plugin2's Wilson profiles now have {len(plugin2.wilson.obs)} keys")

        # Step 6: Generate the nightcycle report
        banner("Step 6: Nightcycle report")
        report = reader.nightcycle_report()
        print(report)

        # Step 7: Show that the new plugin's decisions are now informed
        banner("Step 7: The new plugin is informed by Phase 1's history")
        for i in range(3):
            d_decision = plugin2.decide(opener="chart", kwargs={"role": "creative_ideation"})
            print(f"  Decision {i+1}: {d_decision.model} + {d_decision.opener} "
                   f"(rationale: {d_decision.rationale[:50]}...)")

        banner("The loop is closed")
        print()
        print("Quilt picked. Saddle recorded. Nightcycle read. Wisdom applied.")
        print("The cowboy doesn't have to wait for the plugin to learn from scratch —")
        print("the morning plugin has the lessons of yesterday's ledger baked in.")
        print()
        print("This is the harness: pincher (reflex) → Quilt (cast) → saddle (record) → cowboy (refine).")


if __name__ == "__main__":
    main()

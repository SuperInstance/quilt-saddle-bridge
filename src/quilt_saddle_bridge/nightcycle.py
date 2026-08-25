"""nightcycle.py — The Python nightcycle runner.

Saddle's nightcycle (TypeScript, in saddle/src/nightcycle.ts) is a scheduled
pass that:
1. Reads the ledger.jsonl
2. Aggregates per-(alignmentId) success/failure/escalation counts
3. Computes Wilson lower bound per alignment
4. Identifies "earned keep" (wilson >= 0.5, n >= 5)
5. Emits a markdown report

This is the Python equivalent. It uses Phase 2's SaddleLedgerReader but
adds the nightcycle-specific aggregation and reporting.

Usage:
    from quilt_saddle_bridge.nightcycle import NightcycleRunner
    runner = NightcycleRunner("data/ledger.jsonl")
    report = runner.run()
    print(report)

Or from CLI:
    python3 -m quilt_saddle_bridge.nightcycle data/ledger.jsonl --out report.md
"""
from __future__ import annotations
import argparse
import math
import sys
import datetime
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .phase2 import (
    SaddleLedgerReader, AlignmentStats, wilson_lower as wilson_lower_phase2,
    fnv1a64, canonical_json, hash_value,
)
from .saddle_types import LedgerEntry


WILSON_Z = 1.96  # 95% confidence
EARNED_KEEP_N = 5
EARNED_KEEP_WILSON = 0.5


@dataclass
class NightcycleAlignmentStats:
    """Per-alignment stats from the nightcycle."""
    alignmentId: str
    n: int = 0
    successes: int = 0
    escalations: int = 0
    failures: int = 0
    wilson_lower: float = 0.0
    earned_keep: bool = False
    last_seen: str = ""
    avg_latency_ms: float = 0.0
    total_cost_usd: float = 0.0

    def observe(self, e: Dict[str, Any], credit: Dict[str, Any]) -> None:
        self.n += 1
        verdict = e.get("verdict", "failed")
        if verdict == "worked":
            self.successes += 1
        else:
            self.failures += 1
        # Escalation
        if e.get("verdictKind") == "escalated":
            self.escalations += 1
        # Latency
        lat = credit.get("latency_ms", 0) or 0
        self.avg_latency_ms = (self.avg_latency_ms * (self.n - 1) + lat) / self.n
        # Cost
        cost = credit.get("cost", 0) or 0
        self.total_cost_usd += cost
        # Last seen
        self.last_seen = e.get("ts", "")

    def finalize(self) -> None:
        if self.n == 0:
            return
        p = self.successes / self.n
        z = WILSON_Z
        denom = 1 + (z * z) / self.n
        center = (p + (z * z) / (2 * self.n)) / denom
        spread = (z * math.sqrt(p * (1 - p) / self.n + (z * z) / (4 * self.n * self.n))) / denom
        self.wilson_lower = max(0.0, center - spread)
        self.earned_keep = (
            self.wilson_lower >= EARNED_KEEP_WILSON and self.n >= EARNED_KEEP_N
        )


@dataclass
class NightcycleReport:
    """The full nightcycle report — both the data and the rendered markdown."""
    generated_at: str
    ledger_path: str
    n_entries: int
    n_alignments: int
    hash_chain_ok: bool
    hash_chain_msg: str
    alignments: List[NightcycleAlignmentStats] = field(default_factory=list)
    earned_keep_count: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0

    def to_markdown(self) -> str:
        """Render the report as markdown."""
        lines = [
            "# Nightcycle Report",
            "",
            f"**Generated:** {self.generated_at}  ",
            f"**Ledger:** `{self.ledger_path}`  ",
            f"**Entries:** {self.n_entries}  ",
            f"**Alignments:** {self.n_alignments}  ",
            f"**Earned keep:** {self.earned_keep_count}  ",
            f"**Total cost:** ${self.total_cost_usd:.4f}  ",
            f"**Hash chain:** {'✓' if self.hash_chain_ok else '✗'} {self.hash_chain_msg}  ",
            "",
            "## Per-alignment summary",
            "",
            "Sorted by Wilson lower bound (descending).",
            "",
            "| Alignment | N | Worked | Failed | Wilson LB | Avg Lat | Total Cost | Earned Keep | Last Seen |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for a in sorted(self.alignments, key=lambda x: -x.wilson_lower):
            lines.append(
                f"| `{a.alignmentId}` | {a.n} | {a.successes} | {a.failures} | "
                f"{a.wilson_lower:.3f} | {a.avg_latency_ms:.0f}ms | "
                f"${a.total_cost_usd:.4f} | "
                f"{'✓' if a.earned_keep else '✗'} | "
                f"{a.last_seen[:19] if a.last_seen else '-'} |"
            )

        # Earned-keep section
        lines.extend([
            "",
            "## Earned-keep summary",
            "",
        ])
        kept = [a for a in self.alignments if a.earned_keep]
        if kept:
            lines.append(f"**{len(kept)} alignments earned their keep** "
                          f"(wilson_lower ≥ {EARNED_KEEP_WILSON} AND n ≥ {EARNED_KEEP_N}):")
            lines.append("")
            for a in sorted(kept, key=lambda x: -x.wilson_lower):
                lines.append(f"- `{a.alignmentId}` — {a.successes}/{a.n} success, "
                              f"wilson={a.wilson_lower:.3f}, cost=${a.total_cost_usd:.4f}")
        else:
            lines.append(f"No alignments earned their keep yet (need n≥{EARNED_KEEP_N} "
                          f"and wilson_lower≥{EARNED_KEEP_WILSON}).")

        # Escalations
        escalated = [a for a in self.alignments if a.escalations > 0]
        if escalated:
            lines.extend([
                "",
                "## Escalations",
                "",
                f"**{len(escalated)} alignments had escalations** "
                f"(execution errors exhausted maxAttempts):",
                "",
            ])
            for a in escalated:
                lines.append(f"- `{a.alignmentId}` — {a.escalations} escalations")

        # Recommendations
        lines.extend([
            "",
            "## Recommendations",
            "",
        ])
        # Recommend retiring alignments that consistently fail
        failing = [a for a in self.alignments
                    if a.n >= EARNED_KEEP_N and a.wilson_lower < 0.3]
        if failing:
            lines.append("**Retire these alignments** (low Wilson LB, n≥5):")
            for a in failing:
                lines.append(f"- `{a.alignmentId}` — {a.successes}/{a.n} success, "
                              f"wilson={a.wilson_lower:.3f}")
            lines.append("")

        # Recommend promoting earned-keep
        if kept:
            lines.append("**Pin these alignments** (use them more):")
            for a in kept:
                lines.append(f"- `{a.alignmentId}` — {a.successes}/{a.n} success, "
                              f"wilson={a.wilson_lower:.3f}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        return {
            "generated_at": self.generated_at,
            "ledger_path": self.ledger_path,
            "n_entries": self.n_entries,
            "n_alignments": self.n_alignments,
            "hash_chain_ok": self.hash_chain_ok,
            "hash_chain_msg": self.hash_chain_msg,
            "earned_keep_count": self.earned_keep_count,
            "total_cost_usd": self.total_cost_usd,
            "total_latency_ms": self.total_latency_ms,
            "alignments": [asdict(a) for a in self.alignments],
        }


class NightcycleRunner:
    """The Python nightcycle runner.

    Reads a saddle-format JSONL ledger, aggregates per-alignment stats, and
    produces a markdown report.
    """

    def __init__(self, ledger_path: str):
        self.ledger_path = ledger_path
        self.reader = SaddleLedgerReader(ledger_path)

    def run(self) -> NightcycleReport:
        """Run the nightcycle pass. Returns a NightcycleReport."""
        alignments: Dict[str, NightcycleAlignmentStats] = {}
        total_cost = 0.0
        total_latency = 0.0

        for entry in self.reader.entries:
            aid = entry.get("alignmentId", "unknown")
            if aid not in alignments:
                alignments[aid] = NightcycleAlignmentStats(alignmentId=aid)
            try:
                credit = __import__("json").loads(entry.get("credit", "{}"))
            except Exception:
                credit = {}
            alignments[aid].observe(entry, credit)
            # Add to totals
            cost = credit.get("cost", 0) or 0
            lat = credit.get("latency_ms", 0) or 0
            total_cost += cost
            total_latency += lat

        # Finalize Wilson and earned-keep
        for a in alignments.values():
            a.finalize()

        # Verify the hash chain
        hash_ok, hash_msg = self.reader.verify_hash_chain()

        earned_keep = sum(1 for a in alignments.values() if a.earned_keep)

        return NightcycleReport(
            generated_at=datetime.datetime.now(tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            ledger_path=self.ledger_path,
            n_entries=len(self.reader.entries),
            n_alignments=len(alignments),
            hash_chain_ok=hash_ok,
            hash_chain_msg=hash_msg,
            alignments=list(alignments.values()),
            earned_keep_count=earned_keep,
            total_cost_usd=total_cost,
            total_latency_ms=total_latency,
        )


# -- CLI --

def main():
    parser = argparse.ArgumentParser(description="Quilt saddle bridge nightcycle runner")
    parser.add_argument("ledger", help="Path to the saddle-format JSONL ledger")
    parser.add_argument("--out", "-o", default=None,
                          help="Output file for the markdown report (default: stdout)")
    parser.add_argument("--json", action="store_true",
                          help="Output as JSON instead of markdown")
    args = parser.parse_args()

    if not Path(args.ledger).exists():
        print(f"Error: ledger file not found: {args.ledger}", file=sys.stderr)
        sys.exit(1)

    runner = NightcycleRunner(args.ledger)
    report = runner.run()

    if args.json:
        import json
        output = json.dumps(report.to_dict(), indent=2, default=str)
    else:
        output = report.to_markdown()

    if args.out:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"Report written to {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()

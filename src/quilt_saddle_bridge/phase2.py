"""phase2.py — Read saddle's ledger back into the Quilt's Wilson profiles.

Phase 1 of the bridge was write-only: Quilt → saddle.
Phase 2 closes the loop: saddle → Quilt.

What this module does:
1. Read a saddle-format JSONL ledger
2. Aggregate entries by (alignmentId, verdict) to get success/failure counts
3. Compute per-(alignment, role) success rates
4. Emit a JSON document that can be loaded into the Quilt's WilsonProfiles
5. Optionally, run saddle's nightcycle-style report (a summary of which
   alignments earned their keep)

Why: the nightcycle reads the ledger, refines the alignment, and the cowboy
needs to see WHICH alignments worked. Phase 2 makes that data flow back into
the Quilt's casting decisions.

Usage:
    from quilt_saddle_bridge.phase2 import SaddleLedgerReader
    reader = SaddleLedgerReader("data/ledger.jsonl")
    summary = reader.summarize()
    wilson_data = reader.to_wilson_profiles()
    # Apply to a Quilt plugin
    plugin.wilson.import_data(wilson_data)
"""
from __future__ import annotations
import json
import time
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field


# -- Re-use FNV-1a64 and canonical_json from phase 1 --

from .bridge import fnv1a64, canonical_json, hash_value
from .saddle_types import LedgerEntry


# -- Wilson lower bound (re-impl here to avoid hard dep) --

def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    """Wilson lower bound — same as casting.py."""
    if n == 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - spread)


@dataclass
class AlignmentStats:
    """Stats for one alignment (one model+opener+role triple)."""
    alignment_id: str
    n_total: int = 0
    n_worked: int = 0
    n_failed: int = 0
    n_escalated: int = 0
    latencies_ms: List[int] = field(default_factory=list)
    last_seen: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.n_worked / self.n_total if self.n_total else 0.0

    @property
    def wilson_lower(self) -> float:
        return wilson_lower(self.n_worked, self.n_total)

    @property
    def p90_latency(self) -> float:
        if not self.latencies_ms:
            return float('inf')
        sorted_lats = sorted(self.latencies_ms)
        return sorted_lats[int(len(sorted_lats) * 0.9)]

    @property
    def earned_keep(self) -> bool:
        """A simple earned-keep rule: wilson_lower >= 0.5 AND n_total >= 3."""
        return self.wilson_lower >= 0.5 and self.n_total >= 3


class SaddleLedgerReader:
    """Reads a saddle-format JSONL ledger and produces Quilt-format data."""

    def __init__(self, ledger_path: str):
        self.ledger_path = Path(ledger_path)
        self.entries: List[Dict[str, Any]] = []
        self._read()

    def _read(self):
        """Read all entries from the ledger file."""
        if not self.ledger_path.exists():
            return
        with open(self.ledger_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def verify_hash_chain(self) -> Tuple[bool, str]:
        """Verify the FNV-1a64 hash chain. Returns (ok, message)."""
        prev_hash = ""
        for i, entry in enumerate(self.entries):
            if entry.get("prevHash", "") != prev_hash:
                return False, f"Entry {i+1}: prevHash mismatch (expected {prev_hash}, got {entry.get('prevHash', '')})"
            # Recompute the entry hash
            entry_dict = {k: v for k, v in entry.items() if k != "hash"}
            expected_hash = hash_value(entry_dict)
            actual_hash = entry.get("hash", "")
            if actual_hash != expected_hash:
                return False, f"Entry {i+1}: hash mismatch (expected {expected_hash}, got {actual_hash})"
            prev_hash = actual_hash
        return True, f"Hash chain valid across all {len(self.entries)} entries"

    def summarize(self) -> Dict[str, Any]:
        """Aggregate by alignmentId, model, opener, role."""
        alignment_stats: Dict[str, AlignmentStats] = {}
        for entry in self.entries:
            aid = entry.get("alignmentId", "unknown")
            if aid not in alignment_stats:
                alignment_stats[aid] = AlignmentStats(alignment_id=aid)
            stats = alignment_stats[aid]
            stats.n_total += 1
            verdict = entry.get("verdict", "failed")
            verdict_kind = entry.get("verdictKind", "")
            if verdict == "worked":
                stats.n_worked += 1
            elif verdict_kind == "escalated":
                stats.n_escalated += 1
            else:
                stats.n_failed += 1
            # Latency
            try:
                credit = json.loads(entry.get("credit", "{}"))
                lat = credit.get("latency_ms", 0)
                if lat:
                    stats.latencies_ms.append(lat)
                cost = credit.get("cost", 0.0)
                stats.total_cost_usd += cost
            except (json.JSONDecodeError, TypeError):
                pass
            # Timestamp
            ts_str = entry.get("ts", "")
            if ts_str:
                try:
                    import datetime
                    ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    stats.last_seen = max(stats.last_seen, ts)
                except (ValueError, AttributeError):
                    pass

        return {
            "n_entries": len(self.entries),
            "n_alignments": len(alignment_stats),
            "alignments": {
                aid: {
                    "n_total": s.n_total,
                    "n_worked": s.n_worked,
                    "n_failed": s.n_failed,
                    "n_escalated": s.n_escalated,
                    "success_rate": s.success_rate,
                    "wilson_lower": s.wilson_lower,
                    "p90_latency": s.p90_latency,
                    "earned_keep": s.earned_keep,
                    "last_seen": s.last_seen,
                    "total_cost_usd": s.total_cost_usd,
                }
                for aid, s in alignment_stats.items()
            },
        }

    def to_wilson_profiles(self) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        """Convert ledger data to a Wilson-profiles-shaped dict.

        Returns: {(primitive, opener, model): {"lower_bound": float, "p90_latency": float, "n": int}}
        """
        # Aggregate by (primitive, opener, model) instead of just alignmentId
        profiles: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for entry in self.entries:
            try:
                debit = json.loads(entry.get("debit", "{}"))
                credit = json.loads(entry.get("credit", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            primitive = debit.get("primitive", "Murmur")
            opener = debit.get("opener", "chart")
            model = credit.get("model", "unknown")
            key = (primitive, opener, model)
            if key not in profiles:
                profiles[key] = {
                    "lower_bound": 0.0,
                    "p90_latency": float('inf'),
                    "n": 0,
                    "n_worked": 0,
                    "n_failed": 0,
                    "latencies_ms": [],
                }
            prof = profiles[key]
            prof["n"] += 1
            verdict = entry.get("verdict", "failed")
            if verdict == "worked":
                prof["n_worked"] += 1
            else:
                prof["n_failed"] += 1
            lat = credit.get("latency_ms", 0)
            if lat:
                prof["latencies_ms"].append(lat)

        # Compute Wilson lower bounds
        for key, prof in profiles.items():
            prof["lower_bound"] = wilson_lower(prof["n_worked"], prof["n"])
            if prof["latencies_ms"]:
                sorted_lats = sorted(prof["latencies_ms"])
                prof["p90_latency"] = sorted_lats[int(len(sorted_lats) * 0.9)]
            # Don't keep latencies in the output (saves space)
            del prof["latencies_ms"]
        return profiles

    def apply_to_plugin(self, plugin) -> int:
        """Apply the ledger data to a Quilt plugin's Wilson profiles.

        Returns the number of profiles updated.
        """
        profiles = self.to_wilson_profiles()
        n = 0
        for (primitive, opener, model), data in profiles.items():
            # Inject observations into the plugin's Wilson profiles
            for _ in range(data["n_worked"]):
                plugin.wilson.observe(primitive, opener, model, 1000, True, 0.9)
            for _ in range(data["n_failed"]):
                plugin.wilson.observe(primitive, opener, model, 1000, False, 0.1)
            n += 1
        return n

    def nightcycle_report(self) -> str:
        """Generate a markdown report (saddle's nightcycle style)."""
        summary = self.summarize()
        ok, msg = self.verify_hash_chain()
        lines = [
            "# Nightcycle Report",
            "",
            f"**Ledger:** `{self.ledger_path}`  ",
            f"**Entries:** {summary['n_entries']}  ",
            f"**Alignments:** {summary['n_alignments']}  ",
            f"**Hash chain:** {msg}  ",
            "",
            "## Per-alignment summary",
            "",
            "| Alignment | N | Worked | Failed | Success% | Wilson LB | p90 Lat | Earned Keep |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for aid, s in sorted(summary["alignments"].items(),
                              key=lambda x: -x[1]["wilson_lower"]):
            lines.append(
                f"| `{aid}` | {s['n_total']} | {s['n_worked']} | {s['n_failed']} | "
                f"{s['success_rate']:.0%} | {s['wilson_lower']:.3f} | "
                f"{s['p90_latency']:.0f}ms | "
                f"{'✓' if s['earned_keep'] else '✗'} |"
            )
        lines.extend([
            "",
            "## Earned-keep summary",
            "",
        ])
        kept = [aid for aid, s in summary["alignments"].items() if s["earned_keep"]]
        if kept:
            lines.append(f"**{len(kept)} alignments earned their keep:**")
            for aid in kept:
                lines.append(f"- `{aid}`")
        else:
            lines.append("No alignments earned their keep yet (need n≥3 + wilson_lower≥0.5).")
        return "\n".join(lines)

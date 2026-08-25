"""quilt-saddle-bridge — Connect the Quilt's witness log to saddle's double-entry ledger.

The Quilt's casting-call plugin records `CastingEvent`s: what model was used,
when, with what outcome. Saddle records `LedgerEntry`s: what was the input
(debit), the output (credit), and the verdict.

This bridge converts between them. The Quilt's witness becomes saddle's ledger.
The Quilt's CastingDecision profiles become saddle's FrozenStates.

Phase 1 (write): Quilt → saddle
Phase 2 (read): saddle → Quilt (the nightcycle's report feeds back)

Why: the Quilt knows *which model* to use; saddle knows *how to use it* (the
prompt, filters, params). Together they close the harness loop.

The bridge writes JSONL to a file that saddle's `node src/nightcycle.ts` can read.
"""
from .bridge import (
    QuiltSaddleBridge,
    QuiltToSaddleConverter,
    SaddleToQuiltConverter,
    casting_event_to_ledger_entry,
    casting_decision_to_frozen_state,
    canonical_json,
    fnv1a64,
    hash_value,
)
from .phase2 import (
    SaddleLedgerReader,
    AlignmentStats,
    wilson_lower as wilson_lower_phase2,
)
from .saddle_types import (
    LedgerEntry,
    FrozenState,
    FilterSpec,
    Verdict,
    VerdictKind,
    OutcomeFact,
)

__version__ = "0.2.0"

__all__ = [
    "QuiltSaddleBridge",
    "QuiltToSaddleConverter",
    "SaddleToQuiltConverter",
    "SaddleLedgerReader",
    "AlignmentStats",
    "casting_event_to_ledger_entry",
    "casting_decision_to_frozen_state",
    "canonical_json",
    "fnv1a64",
    "hash_value",
    "wilson_lower_phase2",
    "LedgerEntry",
    "FrozenState",
    "FilterSpec",
    "Verdict",
    "VerdictKind",
    "OutcomeFact",
]

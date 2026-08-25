"""quilt-saddle-bridge — Connect the Quilt's witness log to saddle's double-entry ledger.

The Quilt's casting-call plugin records `CastingEvent`s: what model was used,
when, with what outcome. Saddle records `LedgerEntry`s: what was the input
(debit), the output (credit), and the verdict.

This bridge converts between them. The Quilt's witness becomes saddle's ledger.
The Quilt's CastingDecision profiles become saddle's FrozenStates.

Why: the Quilt knows *which model* to use; saddle knows *how to use it* (the
prompt, filters, params). Together they close the harness loop.

The Quilt says: "Use SEED_MINI for fable_compression at 0300."
Saddle says: "Use SEED_MINI with this prompt, these filters, these params."
The cowboy reads the ledger to see which combinations earned their keep.

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
from .saddle_types import (
    LedgerEntry,
    FrozenState,
    FilterSpec,
    Verdict,
    VerdictKind,
    OutcomeFact,
)

__version__ = "0.1.0"

__all__ = [
    "QuiltSaddleBridge",
    "QuiltToSaddleConverter",
    "SaddleToQuiltConverter",
    "casting_event_to_ledger_entry",
    "casting_decision_to_frozen_state",
    "canonical_json",
    "fnv1a64",
    "hash_value",
    "LedgerEntry",
    "FrozenState",
    "FilterSpec",
    "Verdict",
    "VerdictKind",
    "OutcomeFact",
]

"""saddle_types.py — Python types matching saddle's TypeScript interfaces.

The saddle source uses these shapes (ledger.ts, frozens.ts). We mirror them here
so the bridge can produce JSON that saddle's `nightcycle.ts` can read directly.

Reference (saddle/src/ledger.ts):
- LedgerEntry has: seq, ts, cellId, runId, alignmentId, debit, credit, verdict,
  escalated, verdictKind, note, retryOf, outcome, prevHash, hash

Reference (saddle/src/frozens.ts):
- FrozenState has: id, model, useCase, prompt, inputFilters, outputFilters, params,
  directiveChunks, earnedKeepMetric, grants, alignmentId, createdAt
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from typing_extensions import Literal


Verdict = Literal["worked", "failed"]
VerdictKind = Literal["worked", "judgment-fail", "execution-error", "escalated"]
FilterKind = Literal["deny", "allow", "transform"]
EarnedKeepMetric = Literal["production", "task-approval"]


@dataclass
class OutcomeFact:
    """v4 orthogonal process facts."""
    timedOut: Optional[bool] = None
    signal: Optional[int] = None
    exitCode: Optional[int] = None


@dataclass
class FilterSpec:
    """A filter applied before/after the model."""
    id: str
    kind: FilterKind
    description: str
    pattern: Optional[str] = None


@dataclass
class LedgerEntry:
    """An entry in saddle's double-entry ledger.

    Append-only, hash-chained. Each entry commits to the hash of the one before.
    """
    seq: int
    ts: str
    cellId: str
    runId: str
    alignmentId: str
    debit: str           # JSON-encoded string of the input
    credit: str          # JSON-encoded string of the output
    verdict: Verdict
    escalated: bool
    verdictKind: Optional[VerdictKind] = None
    note: Optional[str] = None
    retryOf: Optional[int] = None
    outcome: Optional[OutcomeFact] = None
    prevHash: str = ""   # '' for genesis
    hash: str = ""       # FNV-1a64 of entry minus the hash field

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dict, dropping None values for clean JSON.

        Note: prevHash is kept even when empty (for genesis entries).
        """
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


@dataclass
class FrozenState:
    """A content-addressed, immutable alignment bundle.

    The manifest hash IS the content address: frozens/<alignmentId>.json
    Written ONCE, mode 0444, never overwritten.
    """
    id: str
    model: str
    useCase: str
    prompt: str
    inputFilters: List[FilterSpec] = field(default_factory=list)
    outputFilters: List[FilterSpec] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    directiveChunks: List[str] = field(default_factory=list)
    earnedKeepMetric: Optional[EarnedKeepMetric] = None
    grants: Optional[List[str]] = None
    alignmentId: str = ""  # computed on freeze
    createdAt: str = ""    # ISO timestamp on freeze

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        d = asdict(self)
        d["inputFilters"] = [asdict(f) for f in self.inputFilters]
        d["outputFilters"] = [asdict(f) for f in self.outputFilters]
        return {k: v for k, v in d.items() if v is not None and v != ""}

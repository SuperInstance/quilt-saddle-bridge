"""bridge.py — The Quilt ↔ saddle bridge.

Converts between:
- Quilt `CastingEvent` → saddle `LedgerEntry`
- Quilt `CastingDecision` profile → saddle `FrozenState`

The hash chain is the same on both sides: FNV-1a64 of canonical JSON.

Why a bridge, not a rewrite? The Quilt is Python, saddle is TypeScript. The
bridge writes JSONL to disk that saddle's `nightcycle.ts` can read directly. We
don't have to touch saddle's core — the JSONL format is the contract.
"""
from __future__ import annotations
import json
import time
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import asdict

from .saddle_types import (
    LedgerEntry, FrozenState, FilterSpec, OutcomeFact,
)


# -- Hashing (FNV-1a 64-bit, matching saddle/src/hash.ts) -----------------

def fnv1a64(s: str) -> str:
    """FNV-1a 64-bit, hex-encoded. Matches saddle's TypeScript implementation."""
    h = 0xcbf29ce484222325
    for ch in s:
        h ^= ord(ch) & 0xff
        # Multiply by FNV prime (0x100000001b3) mod 2^64
        h = (h * 0x100000001b3) & 0xffffffffffffffff
    return format(h, '016x')


def canonical_json(value: Any) -> str:
    """Sort keys recursively so equivalent objects hash identically. Matches saddle."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(x) for x in value) + "]"
    if isinstance(value, dict):
        items = []
        for k in sorted(value.keys()):
            v = value[k]
            if v is not None:
                items.append(json.dumps(k) + ":" + canonical_json(v))
        return "{" + ",".join(items) + "}"
    return json.dumps(str(value))


def hash_value(value: Any) -> str:
    """FNV-1a64 over canonical JSON. Matches saddle's hashValue()."""
    return fnv1a64(canonical_json(value))


# -- Quilt → Saddle -------------------------------------------------------

def _verdict_kind(quality: Optional[float], success: bool) -> str:
    """Map Quilt's quality score to saddle's verdictKind.

    Saddle's verdictKind semantics:
    - worked: the cell produced a passing judgment/output
    - judgment-fail: the cell produced a real judgment and it was a fail
    - execution-error: the cell failed to produce anything (retryable)
    - escalated: execution errors exhausted maxAttempts

    Quilt gives us: success (bool) and quality (0.0-1.0, optional).
    - success=False → execution-error (the call didn't return a usable output)
    - success=True, quality=None or quality >= 0.5 → worked
    - success=True, quality < 0.5 → judgment-fail (it returned something but the
      output was judged poor)
    """
    if not success:
        return "execution-error"
    if quality is None:
        return "worked"  # no quality signal, assume success means worked
    if quality >= 0.5:
        return "worked"
    return "judgment-fail"


def _verdict(quality: Optional[float], success: bool) -> str:
    """Map to saddle's v3 verdict."""
    if not success:
        return "failed"
    return "worked"


def casting_event_to_ledger_entry(
    event: Dict[str, Any],
    seq: int,
    prev_hash: str,
    cell_id: str,
    run_id: str,
    alignment_id: str,
) -> LedgerEntry:
    """Convert a Quilt casting event to a saddle ledger entry.

    The Quilt's event is the in-memory dict from `plugin.witness`.
    Quilt schema (casting.py):
        {
            "ts": float, "kind": "cast.observed",
            "decision": {model, opener, primitive, rationale, confidence, ...},
            "latency_ms": int, "success": bool, "error": str|None,
            "quality": float|None, "cost": float|None,
        }

    Saddle schema (ledger.ts):
        LedgerEntry {
            seq, ts, cellId, runId, alignmentId,
            debit: str (JSON-encoded input),
            credit: str (JSON-encoded output),
            verdict, escalated, verdictKind, note,
            retryOf, outcome, prevHash, hash
        }
    """
    decision = event.get("decision", {})
    debit_obj = {
        "opener": decision.get("opener"),
        "primitive": decision.get("primitive"),
        "rationale": decision.get("rationale"),
        "confidence": decision.get("confidence"),
        "ts": event.get("ts"),
    }
    credit_obj = {
        "model": decision.get("model"),
        "latency_ms": event.get("latency_ms"),
        "success": event.get("success"),
        "quality": event.get("quality"),
        "cost": event.get("cost"),
        "output_len": event.get("output_len"),
        "error": event.get("error"),
    }
    success = event.get("success", False)
    quality = event.get("quality")  # may be None
    verdict = _verdict(quality, success)
    verdict_kind = _verdict_kind(quality, success)

    entry = LedgerEntry(
        seq=seq,
        ts=_iso_ts(event.get("ts", time.time())),
        cellId=cell_id,
        runId=run_id,
        alignmentId=alignment_id,
        debit=json.dumps(debit_obj),
        credit=json.dumps(credit_obj),
        verdict=verdict,
        escalated=False,  # not exposed in Quilt's v1
        verdictKind=verdict_kind,
        note=decision.get("rationale"),
        prevHash=prev_hash,
    )
    # Compute the entry hash
    entry_dict = entry.to_dict()
    entry_dict.pop("hash", None)
    entry.hash = hash_value(entry_dict)
    return entry


def _iso_ts(ts: float) -> str:
    """Convert a unix timestamp to ISO 8601 (matching saddle's format)."""
    import datetime
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def casting_decision_to_frozen_state(
    decision: Dict[str, Any],
    use_case: str,
    prompt: str,
    input_filters: Optional[List[FilterSpec]] = None,
    output_filters: Optional[List[FilterSpec]] = None,
    params: Optional[Dict[str, Any]] = None,
    directive_chunks: Optional[List[str]] = None,
    earned_keep_metric: Optional[str] = None,
) -> FrozenState:
    """Convert a Quilt casting decision profile to a saddle frozen state.

    A Quilt `CastingDecision` says: model=HERMES_405B, opener=voice, primitive=Murmur.
    A saddle `FrozenState` says: model=HERMES_405B, prompt="...", inputFilters=[...],
    outputFilters=[...], params={...}, directiveChunks=[...].

    Together they pin the HOW to the WHAT.
    """
    model = decision.get("model", "unknown")
    alignment_id = hash_value({
        "model": model,
        "useCase": use_case,
        "prompt": prompt,
        "inputFilters": [asdict(f) for f in (input_filters or [])],
        "outputFilters": [asdict(f) for f in (output_filters or [])],
        "params": params or {},
        "directiveChunks": directive_chunks or [],
    })

    state = FrozenState(
        id=use_case,
        model=model,
        useCase=use_case,
        prompt=prompt,
        inputFilters=input_filters or [],
        outputFilters=output_filters or [],
        params=params or {"temperature": 0.7, "max_tokens": 1500},
        directiveChunks=directive_chunks or [
            "First, acknowledge the situation.",
            "Then, propose a path forward.",
            "Finally, take the smallest irreversible step.",
        ],
        earnedKeepMetric=earned_keep_metric,
        alignmentId=alignment_id,
        createdAt=_iso_ts(time.time()),
    )
    return state


# -- The Bridge -----------------------------------------------------------

class QuiltSaddleBridge:
    """A persistent bridge that writes Quilt events to saddle's ledger.

    Usage:
        bridge = QuiltSaddleBridge(ledger_path="data/ledger.jsonl",
                                     frozens_dir="data/frozens")
        # Each time the Quilt's plugin observes an event:
        bridge.observe_casting_event(event, cell_id="...", run_id="...",
                                       alignment_id="...")
        # When a casting decision profile stabilizes, freeze it:
        bridge.freeze_alignment(decision, use_case="fable_compression",
                                  prompt="...", params={...})
    """

    def __init__(self, ledger_path: str = "data/ledger.jsonl",
                  frozens_dir: str = "data/frozens"):
        self.ledger_path = Path(ledger_path)
        self.frozens_dir = Path(frozens_dir)
        self.frozens_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = self._read_last_seq()
        self._prev_hash = self._read_last_hash()

    def _read_last_seq(self) -> int:
        """Read the last seq from the ledger file."""
        if not self.ledger_path.exists():
            return 0
        last_seq = 0
        with open(self.ledger_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    last_seq = max(last_seq, entry.get("seq", 0))
                except json.JSONDecodeError:
                    pass
        return last_seq

    def _read_last_hash(self) -> str:
        """Read the last hash from the ledger file."""
        if not self.ledger_path.exists():
            return ""
        last_hash = ""
        with open(self.ledger_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("hash"):
                        last_hash = entry["hash"]
                except json.JSONDecodeError:
                    pass
        return last_hash

    def observe_casting_event(self, event: Dict[str, Any],
                                cell_id: str = "quilt-substrate",
                                run_id: str = "default",
                                alignment_id: str = "") -> LedgerEntry:
        """Record a Quilt casting event as a saddle ledger entry."""
        if event.get("kind") != "cast.observed":
            return None  # only observed events become ledger entries

        self._seq += 1
        entry = casting_event_to_ledger_entry(
            event, seq=self._seq, prev_hash=self._prev_hash,
            cell_id=cell_id, run_id=run_id,
            alignment_id=alignment_id or event.get("decision", {}).get("model", "default"),
        )
        # Append to the ledger
        with open(self.ledger_path, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
        self._prev_hash = entry.hash
        return entry

    def freeze_alignment(self, decision: Dict[str, Any], use_case: str,
                            prompt: str,
                            input_filters: Optional[List[FilterSpec]] = None,
                            output_filters: Optional[List[FilterSpec]] = None,
                            params: Optional[Dict[str, Any]] = None,
                            directive_chunks: Optional[List[str]] = None,
                            earned_keep_metric: Optional[str] = None) -> FrozenState:
        """Freeze a casting decision as a content-addressed alignment bundle."""
        state = casting_decision_to_frozen_state(
            decision, use_case, prompt,
            input_filters=input_filters,
            output_filters=output_filters,
            params=params,
            directive_chunks=directive_chunks,
            earned_keep_metric=earned_keep_metric,
        )
        # Write to frozens/<alignmentId>.json (matching saddle's convention)
        path = self.frozens_dir / f"{state.alignmentId}.json"
        if path.exists():
            return state  # content-addressed dedup
        with open(path, "w") as f:
            json.dump(state.to_dict(), f, indent=2)
        # Make read-only (mode 0444) like saddle does
        os.chmod(path, 0o444)
        return state

    def stats(self) -> Dict[str, Any]:
        """Return aggregate stats about the bridge."""
        if not self.ledger_path.exists():
            return {"n_entries": 0, "n_frozens": 0, "ledger_path": str(self.ledger_path)}
        n_entries = 0
        models = {}
        with open(self.ledger_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    n_entries += 1
                    credit = json.loads(entry.get("credit", "{}"))
                    m = credit.get("model", "unknown")
                    models[m] = models.get(m, 0) + 1
                except (json.JSONDecodeError, KeyError):
                    pass
        n_frozens = len(list(self.frozens_dir.glob("*.json")))
        return {
            "n_entries": n_entries,
            "n_frozens": n_frozens,
            "models": models,
            "ledger_path": str(self.ledger_path),
            "frozens_dir": str(self.frozens_dir),
        }


# -- The Converters (for batch import/export) ------------------------------

class QuiltToSaddleConverter:
    """Batch-convert a Quilt witness log to saddle's ledger."""

    def __init__(self, bridge: QuiltSaddleBridge):
        self.bridge = bridge

    def convert_witness_log(self, witness: List[Dict[str, Any]],
                               cell_id: str = "quilt-substrate",
                               run_id: str = "default",
                               alignment_id: str = "") -> List[LedgerEntry]:
        """Convert a list of Quilt witness events to saddle ledger entries."""
        entries = []
        for event in witness:
            entry = self.bridge.observe_casting_event(
                event, cell_id=cell_id, run_id=run_id, alignment_id=alignment_id,
            )
            if entry:
                entries.append(entry)
        return entries


class SaddleToQuiltConverter:
    """Convert saddle ledger entries back to Quilt witness events.

    For round-trip tests and for the cowboy to see saddle's view of the world.
    """

    @staticmethod
    def ledger_entry_to_casting_event(entry: LedgerEntry) -> Dict[str, Any]:
        debit = json.loads(entry.debit)
        credit = json.loads(entry.credit)
        return {
            "ts": _parse_ts(entry.ts),
            "kind": "cast.observed",
            "decision": {
                "model": credit.get("model", "unknown"),
                "opener": debit.get("opener"),
                "primitive": debit.get("primitive"),
                "rationale": debit.get("rationale"),
                "confidence": debit.get("confidence"),
            },
            "latency_ms": credit.get("latency_ms"),
            "success": credit.get("success", False),
            "quality": credit.get("quality"),
            "cost": credit.get("cost"),
            "error": credit.get("error"),
        }


def _parse_ts(iso_ts: str) -> float:
    """Parse an ISO 8601 timestamp to unix."""
    import datetime
    return datetime.datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()

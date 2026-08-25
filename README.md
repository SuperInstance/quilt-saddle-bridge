# quilt-saddle-bridge

A bridge between the Quilt's casting-call witness log and saddle's double-entry ledger.

The Quilt's `CastingEvent` becomes a saddle `LedgerEntry`. The Quilt's
`CastingDecision` profile becomes a saddle `FrozenState`. Together: the Quilt
picks *which* model; saddle pins *how* to use it (prompt, filters, params).

```
┌─────────────────────────────────────────────────────────────┐
│  Quilt substrate (Python)                                   │
│  ┌─────────────────────────────────────────┐                │
│  │  casting-call plugin                    │                │
│  │  - witness events                       │                │
│  │  - Wilson profiles                      │                │
│  │  - gale-aware resources                 │                │
│  └────────────┬────────────────────────────┘                │
│               │                                              │
│               ▼                                              │
│  ┌─────────────────────────────────────────┐                │
│  │  quilt-saddle-bridge                    │                │
│  │  - convert event → ledger entry         │                │
│  │  - freeze decision → frozen state       │                │
│  │  - hash chain (FNV-1a64)                │                │
│  │  - content-addressed files              │                │
│  └────────────┬────────────────────────────┘                │
│               │                                              │
│               ▼                                              │
│  data/ledger.jsonl      data/frozens/<hash>.json            │
│  (append-only JSONL)    (read-only content-addressed)        │
└─────────────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  saddle (TypeScript)                                        │
│  - nightcycle.ts reads ledger.jsonl                         │
│  - reads frozens/*.json                                     │
│  - refines the quilt of cells                               │
└─────────────────────────────────────────────────────────────┘
```

## Why

The Quilt knows the *which*: which model to use for a given (app, user, hardware).
Saddle knows the *how*: the prompt, the filters, the params. Together they close
the harness loop. The cowboy reads saddle's morning report to see which
combinations earned their keep.

The bridge writes JSONL that saddle's `node src/nightcycle.ts` can read directly.
No need to touch saddle's TypeScript core.

## Install

```bash
git clone https://github.com/SuperInstance/quilt-saddle-bridge.git
cd quilt-saddle-bridge
pip install -e .
```

## Usage

```python
from quilt_substrate.substrate import Substrate, Cell
from quilt_substrate.plugins.casting import QuiltCastingCallPlugin, Probes
from quilt_saddle_bridge import QuiltSaddleBridge, FilterSpec

# Set up the Quilt
substrate = Substrate()
substrate.add(Cell(address="bathy:0", value=4.2, axes=("lat", "lon")))
probes = Probes(user="reyes", app="F/V EILEEN", hardware="tablet",
                  time_of_day="0300", weather="gale", crew_state="tired")
plugin = QuiltCastingCallPlugin(substrate, probes=probes)
plugin.install()

# Set up the bridge
bridge = QuiltSaddleBridge(
    ledger_path="data/ledger.jsonl",
    frozens_dir="data/frozens",
)

# Render through the plugin; the bridge auto-records
for i in range(5):
    substrate.render(opener="tide", role="sensory_creative")
    for event in plugin.witness[-2:]:
        if event.get("kind") == "cast.observed":
            bridge.observe_casting_event(event, cell_id="bathy", run_id=f"session-{i}")

# Freeze an alignment as a saddle FrozenState
decision = {"model": "DEEPSEEK_V4_FLASH", "opener": "tide", "primitive": "Murmur"}
state = bridge.freeze_alignment(
    decision, use_case="sensory_creative",
    prompt="You are a maritime navigator's assistant.",
    input_filters=[FilterSpec(id="strip-noise", kind="transform", description="denoise")],
    output_filters=[FilterSpec(id="voice-only", kind="allow", description="voice only")],
    params={"temperature": 0.4, "max_tokens": 200, "voice": "maritime"},
    directive_chunks=[
        "Acknowledge the current depth and tide state.",
        "Flag any hazard within 100m.",
        "Speak the smallest, most useful sentence first.",
    ],
    earned_keep_metric="task-approval",
)
print(f"Frozen: {state.alignmentId}.json")
```

## What the bridge produces

### Ledger entry (data/ledger.jsonl)

Each line is a saddle-format `LedgerEntry` (matching saddle/src/ledger.ts):

```json
{
  "seq": 1,
  "ts": "2026-08-24T20:30:00Z",
  "cellId": "bathy",
  "runId": "session-0",
  "alignmentId": "DEEPSEEK_V4_FLASH",
  "debit": "{\"opener\":\"tide\",\"primitive\":\"Murmur\",\"rationale\":\"...\"}",
  "credit": "{\"model\":\"DEEPSEEK_V4_FLASH\",\"latency_ms\":1200,\"success\":true,...}",
  "verdict": "worked",
  "escalated": false,
  "verdictKind": "worked",
  "note": "...",
  "prevHash": "",
  "hash": "4fdce028db174118"
}
```

Append-only, hash-chained (FNV-1a64). Tamper-evident, not tamper-proof.

### Frozen state (data/frozens/<hash>.json)

Content-addressed, read-only (mode 0444). The filename IS the manifest hash:

```json
{
  "id": "sensory_creative",
  "model": "DEEPSEEK_V4_FLASH",
  "useCase": "sensory_creative",
  "prompt": "You are a maritime navigator's assistant.",
  "inputFilters": [{"id": "strip-noise", "kind": "transform", "description": "denoise"}],
  "outputFilters": [{"id": "voice-only", "kind": "allow", "description": "voice only"}],
  "params": {"temperature": 0.4, "max_tokens": 200, "voice": "maritime"},
  "directiveChunks": [
    "Acknowledge the current depth and tide state.",
    "Flag any hazard within 100m.",
    "Speak the smallest, most useful sentence first."
  ],
  "earnedKeepMetric": "task-approval",
  "alignmentId": "bc62f6b12b1288d1",
  "createdAt": "2026-08-24T20:30:00Z"
}
```

## Tests

```bash
python3 tests/test_bridge.py
```

26 tests covering: FNV-1a64, canonical JSON, casting event → ledger entry,
casting decision → frozen state, hash chain, content-addressed dedup, round-trip
back to Quilt, and end-to-end integration with the actual Quilt plugin.

## Status

Phase 1: write-only. The bridge converts Quilt → saddle, not the reverse.
Phase 2 will read saddle's ledger back into the Quilt's substrate, so saddle's
nightcycle output can refine the casting decisions.

## The loop

The Quilt picks the model. Saddle records the outcome. The cowboy reads the
ledger. The nightcycle refines the alignment. The harness gets tighter. The dog
matures. The quilt grows.

The substrate is the soil. The casting-call is the gardener. The saddle is the
harness. The nightcycle is the moon. The cowboy is the rider. The kennel is the
home.

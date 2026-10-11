# MT5 Multi-Timeframe Market Analysis Engine - Next Steps

## Phase 1: Deterministic Core (COMPLETED ✅)

### What's Done
- Pluggable data providers: OANDA v20 REST (live), MT5 CSV (offline), synthetic (deterministic)
- Closed-candle filtering (forming candle excluded by the source or dropped)
- Swing detection (5L/5R) with confirmation delay
- Market structure (HH/HL/LH/LL, internal/external, protected levels)
- BOS/CHoCH with ATR-based breakout buffer (causal - no look-ahead)
- S/R zones with ATR-normalized clustering & lifecycle
- EMA(20/50/200) with price relationships + slope direction, RSI(14), ATR(14)
- Fibonacci (deterministic anchoring)
- Previous day/week levels (broker-time, causal), round numbers (JPY-aware)
- Trading sessions (Asia/London/NY, timezone-aware, overlaps)
- Candlestick features (doji, engulfing, hammer, etc.)
- Canonical market state JSON with full traceability
- Historical replay engine (causal, no future leaks)
- CLI: validate, replay, inspect, analyze (provider-aware)

---

## Phase 1.5: Data Pipeline & Core Hardening (COMPLETED ✅)

### Data pipeline
- `DataProvider` abstraction; engine is source-agnostic
- MT5 provider: reads candles from a running MetaTrader 5 terminal over IPC
  (official `MetaTrader5` package) - real broker data, no exports; auto-detects
  the broker UTC offset and drops the forming bar
- OANDA v20 REST provider: candles endpoint, pagination past the 5000 cap,
  `complete`-flag handling, symbol/granularity mapping, token via `OANDA_API_TOKEN`
  (note: v20 REST is not available for OANDA Global Markets)
- Corrected candle timestamp semantics: providers timestamp by bar **open**;
  `time_close = open + timeframe` is the authoritative causal timestamp
- CSV provider kept for offline back-compat
- Deterministic synthetic provider for tests/offline (H1 path aggregated to H4/D1)

### Correctness fixes
- Determinism: zone sources no longer stamp `datetime.now()`; all timestamps
  derive from the decision time (same input → byte-identical output)
- Zone lifecycle: fixed the enum comparison so `broken`/`flipped` actually occur
- Previous day/week: computed from closed D1 candles in broker time (were aliased)
- Structural events: causal, one event per broken level, no look-ahead
- EMA: fixed malformed `price_vs_emaema_20` keys; implemented slope direction
- `data_quality` in the state is now scoped to the decision window (no future leak)
- Packaging: `pip install -e .` works (setuptools package discovery)

### Tests
- Full-pipeline determinism (repeated runs identical)
- Future-data invariance (append candles → past state unchanged)
- Causal events (break strictly after reference swing confirms; no duplicate breaks)
- Zone lifecycle transitions; previous-day causality
- OANDA provider parsing/mapping (mocked HTTP)

---

## Phase 2: Pattern Geometry & LLM Integration

### 2.1 Pattern Feature Engine
- [ ] Double top/bottom detection (deterministic geometry)
- [ ] Head & shoulders / inverse H&S
- [ ] Flag/pennant patterns
- [ ] Wedge patterns (rising/falling)
- [ ] Triangle patterns (ascending/descending/symmetrical)
- [ ] Rectangle/channel patterns
- [ ] Breakout/retest detection
- [ ] Pattern state machine (forming → confirmed → failed)

### 2.2 LLM Integration
- [ ] System prompt engineering (top-down reading, evidence citation)
- [ ] Structured output schema (Pydantic models for AI response)
- [ ] Evidence ID referencing in AI output
- [ ] Scenario generation with typed conditions
- [ ] Validation layer:
  - Schema validation (Pydantic)
  - Semantic grounding (price/ID/timestamp validation)
  - Pattern evidence validation
  - Derived value traceability
- [ ] Retry logic with validation feedback
- [ ] Rejected analysis storage

### 2.3 AI Output Schema
```json
{
  "bias": "bullish|bearish|neutral|mixed|conditional",
  "market_phase": "trend|pullback|range|consolidation|breakout|retest|possible_reversal|transition|unknown",
  "summary": "...",
  "timeframe_read": {
    "D1": {"interpretation": "...", "references": ["D1_ZONE_001"]},
    "H4": {"interpretation": "...", "references": ["H4_EVENT_009"]},
    "H1": {"interpretation": "...", "references": ["H1_SWING_12"]}
  },
  "patterns": [],
  "key_levels": [],
  "scenarios": [],
  "what_changes_the_read": [],
  "unclear": []
}
```

---

## Phase 3: Evaluation Framework

### 3.1 Historical Evaluation
- [ ] Chronological evaluation (older → development, later → validation, recent → holdout)
- [ ] Scenario evaluation:
  - Activation occurred + timestamp
  - Invalidation occurred + timestamp
  - Time to activation/invalidation
  - MFE/MAE (max favorable/adverse excursion)
- [ ] Baseline comparisons:
  - Baseline A: Deterministic state only
  - Baseline B: Deterministic + recent candles
  - System C: Full AI interpretation
- [ ] LLM-specific metrics:
  - Grounding (references only supplied evidence)
  - Consistency (same state → similar interpretation)
  - Pattern discipline (avoids unsupported patterns)
  - Scenario quality (conditional & grounded)
  - Structural understanding (trend/pullback/breakout/range/reversal)
  - Hallucination rate (invented levels/events/indicators)

### 3.2 Reproducibility Tests (Expand)
- [ ] Same input → same deterministic state
- [ ] Future data test (append candles after T, state at T unchanged)
- [ ] Forming candle test (never enters closed-candle computations)
- [ ] Higher TF alignment test (later H4 doesn't leak into H1 state)
- [ ] Swing confirmation test (no swing before confirmation candle)
- [ ] Breakout test (wick-only ≠ confirmed break)
- [ ] Stable IDs test (reordering arrays doesn't change IDs)

---

## Phase 4: Production Hardening

### 4.1 Data Pipeline
- [x] Live broker data via OANDA v20 REST API (replaces manual exports)
- [ ] EA push mode (MQL5 Expert Advisor for live candles)
- [ ] Secret key authentication for EA endpoint
- [ ] VPS deployment guide
- [ ] Data quality monitoring/alerting
- [ ] Multiple symbol support (test on major, JPY pair, cross)

### 4.2 Storage & Versioning
- [ ] Database schema (PostgreSQL/SQLite)
- [ ] Immutable analysis snapshots
- [ ] Version tracking (engine, pattern, prompt, model)
- [ ] Audit trail (AI conclusion → evidence → market state → candles → MT5)

### 4.3 Cost Control
- [ ] Decision-timeframe candle close trigger only
- [ ] Significant structural event triggers (configurable)
- [ ] No continuous polling

---

## CSV Data Preparation (Required for Testing)

> **Note:** OANDA is now the primary data source (`--provider oanda`). The CSV
> path below is retained for offline/back-compat use.

### MT5 Export Format
Export from MT5: **File → Export → CSV** for each timeframe.

**Required columns:**
```
time,open,high,low,close,tick_volume,spread,real_volume
2026.01.15 10:00,1.0500,1.0520,1.0490,1.0510,1245,8,0
```

**File naming convention:**
```
{SYMBOL}_{TIMEFRAME}.csv
Examples:
EURUSD_D1.csv
EURUSD_H4.csv
EURUSD_H1.csv
USDJPY_D1.csv
USDJPY_H4.csv
USDJPY_H1.csv
```

### Broker Timezone Offset
Set in `config.yaml`:
```yaml
csv_import:
  broker_offset_hours: 2  # EET = UTC+2, EEST = UTC+3, UTC = 0
  drop_last_row: true     # Drop forming candle at end of export
```

**Common broker offsets:**
- IC Markets, Pepperstone, FP Markets: `2` (EET) / `3` (EEST)
- OANDA, Forex.com: `0` (UTC) or `-5` (EST) / `-4` (EDT)
- Check your broker's "Server Time" in MT5 Market Watch

### Data Requirements
| Timeframe | Min Bars | Recommended |
|-----------|----------|-------------|
| D1        | 200      | 500+        |
| H4        | 500      | 1000+       |
| H1        | 1000     | 2000+       |

**For swing style (D1→H4→H1):** Need at least 50 D1 bars for EMA200 warmup + swing detection.

### Generate Test Data
```bash
# Quick validation
python cli.py validate ./data EURUSD --broker-offset 2

# Full replay (generates market state JSON files)
python cli.py replay ./data EURUSD ./output --broker-offset 2 --max-snapshots 50

# Inspect a snapshot
python cli.py inspect ./output/EURUSD_swing_H1_20260115T100000Z_v2.0.0.json
```

### Multi-Symbol Testing
Test on at least 3 pairs:
1. **Major** - EURUSD, GBPUSD, AUDUSD (5 digits, 0.00001 point)
2. **JPY pair** - USDJPY, EURJPY, GBPJPY (3 digits, 0.001 point)
3. **Cross** - EURGBP, AUDCAD, NZDJPY

---

## GitHub Setup

```bash
cd C:\Users\AdomAmponsah\Documents\Repos\mt5_analysis_engine

# Initialize repo
git init
git add .
git commit -m "feat: deterministic core - swing detection, market structure, zones, indicators, replay engine"

# Create GitHub repo first at https://github.com/new (name: forex-adom)
# Then:
git branch -M main
git remote add origin https://github.com/Adom-Amponsah/forex-adom.git
git push -u origin main
```

---

## Configuration Reference

Key settings in `config.yaml`:
```yaml
engine:
  version: "2.0.0"
  schema_version: "2.0"

style: "swing"  # or "intraday"

timeframes:
  context: ["D1"]
  structure: ["H4"]
  decision: "H1"
  confirmation: []

swing:
  left_bars: 5
  right_bars: 5
  equal_policy: "strict"

indicators:
  ema:
    periods: [20, 50, 200]
  rsi:
    period: 14
    overbought: 70
    oversold: 30
  atr:
    period: 14

fibonacci:
  ratios: [0.382, 0.500, 0.618]
  min_leg_atr_multiple: 1.0

breakout:
  n_points: 0
  atr_fraction: 0.05

zones:
  atr_tolerance_min: 0.25
  atr_tolerance_max: 0.50

round_numbers:
  step_pips: 100
  levels_around_price: 5

csv_import:
  broker_offset_hours: 2
  drop_last_row: true

replay:
  recent_candles_per_timeframe: 30
  calculation_history_bars: 1000
```

---

## Project Structure
```
mt5_analysis_engine/
├── app/
│   ├── config/settings.py       # Configuration (loads config.yaml)
│   ├── data/                    # CSV loading, timeframe alignment, quality
│   ├── indicators/              # EMA, RSI, ATR, Fib, levels, sessions
│   ├── structure/               # Swings, HH/HL/LH/LL, BOS/CHoCH
│   ├── zones/engine.py          # S/R clustering & lifecycle
│   ├── patterns/candle_features.py  # Deterministic candle patterns
│   ├── market_state/builder.py  # Canonical market state JSON
│   └── replay/engine.py         # Historical replay
├── cli.py                       # Commands: validate, replay, analyze, inspect
├── config.yaml                  # All configuration
├── pyproject.toml               # Dependencies
├── tests/test_deterministic.py  # 12 determinism tests
├── README.md                    # Project documentation
└── NEXT_STEPS.md                # This file
```

---

## Acceptance Criteria for Phase 2

Before moving to Phase 3, verify:
- [ ] Pattern features generate evidence IDs that exist in market state
- [ ] LLM output validates against schema without errors (95%+)
- [ ] Semantic grounding catches hallucinated prices/levels
- [ ] Replay + AI produces stored analyses with full traceability
- [ ] Baseline comparison shows AI adds value over deterministic-only
- [ ] Tests pass for all new pattern detection code
# MT5 Multi-Timeframe Market Analysis Engine

Deterministic, replayable forex market analysis engine implementing the V2 Engineering Specification.

## Features (First Build - Deterministic Core)

- **CSV Upload**: Load MT5 exported D1/H4/H1 candles with broker timezone offset handling
- **Closed-Candle Filtering**: Automatically drops forming candles from CSV exports
- **Swing Detection**: 5-left/5-right symmetric pivot windows with explicit equal high/low policy
- **Market Structure**: HH/HL/LH/LL classification, internal/external structure, protected levels
- **Structural Events**: BOS/CHoCH detection with configurable ATR-based breakout buffer
- **Support/Resistance Zones**: Clustering with ATR-normalized tolerance, lifecycle states
- **Indicators**: EMA (20/50/200), RSI (14), ATR (14) with proper warmup
- **Fibonacci**: Deterministic anchoring to latest structurally valid swing leg
- **Previous Day/Week Levels**: From broker D1 candles
- **Round Numbers**: Generated from symbol metadata (handles JPY pairs correctly)
- **Trading Sessions**: Timezone-aware (Asia/London/New York) with overlap support
- **Candlestick Features**: Doji, engulfing, pin bars, inside bars, strong closes
- **Canonical Market State**: Immutable JSON output with full traceability
- **Historical Replay**: Causal replay engine with future-data leak prevention

## Project Structure

```
mt5_analysis_engine/
├── app/
│   ├── config/          # Configuration management
│   ├── data/            # Data loading, normalization, timeframe alignment
│   ├── indicators/      # EMA, RSI, ATR, Fibonacci, levels, sessions
│   ├── structure/       # Swing detection, market structure, BOS/CHoCH
│   ├── zones/           # Support/resistance zone engine
│   ├── patterns/        # Candlestick features
│   ├── market_state/    # Canonical market state builder
│   └── replay/          # Historical replay engine
├── cli.py               # Command-line interface
├── config.yaml          # Configuration file
├── pyproject.toml       # Project metadata and dependencies
└── tests/               # Determinism and reproducibility tests
```

## Installation

```bash
# Requires Python 3.11+
# Install from Microsoft Store or python.org

# Install dependencies
pip install -e .

# Or with dev dependencies
pip install -e ".[dev]"
```

## Configuration

Edit `config.yaml` to customize:
- Broker timezone offset (`csv_import.broker_offset_hours`)
- Swing detection parameters
- Indicator periods
- Breakout buffer
- Zone clustering tolerance
- Round number step
- Session definitions

## Usage

### Validate CSV Data
```bash
python cli.py validate ./data EURUSD --style swing --broker-offset 2
```

### Run Single Analysis
```bash
python cli.py analyze ./data EURUSD "2026-10-06T10:00:00Z" --style swing --broker-offset 2
```

### Run Historical Replay
```bash
python cli.py replay ./data EURUSD ./output --style swing --broker-offset 2 --max-snapshots 100
```

### Inspect Saved Snapshot
```bash
python cli.py inspect ./output/EURUSD_swing_H1_20261006T100000Z_v2.0.0.json
```

## CSV Format

Expected MT5 CSV export format (one file per timeframe):
```
time,open,high,low,close,tick_volume,spread,real_volume
2026.10.06 10:00,1.17510,1.17590,1.17480,1.17560,1245,8,0
```

Files should be named: `{SYMBOL}_{TIMEFRAME}.csv` (e.g., `EURUSD_H1.csv`)

## Deterministic Guarantees

The engine passes these reproducibility tests:
1. **Same Input → Same Output**: Identical candles + config + version = identical market state
2. **Future Data Test**: Appending candles after T doesn't change state at T
3. **Forming Candle Test**: Current forming candle never enters closed-candle computations
4. **Higher TF Alignment**: At H1 time T, later H4 candles never leak into state
5. **Swing Confirmation**: Swing never appears before its confirmation candle closes
6. **Breakout Validation**: Wick-only penetration never creates confirmed structural break
7. **Stable IDs**: Reordering arrays never changes object IDs

## Output: Canonical Market State

Each analysis produces a complete JSON document containing:

```json
{
  "schema_version": "2.0",
  "engine_version": "2.0.0",
  "symbol": "EURUSD",
  "style": "swing",
  "decision_timeframe": "H1",
  "decision_candle_close_time": "2026-10-06T10:00:00Z",
  "as_of": "2026-10-06T10:00:00Z",
  "data_source": {...},
  "price": 1.17550,
  "session": {"active": ["london", "new_york"], "primary": "new_york"},
  "previous_day": {"high": 1.17920, "low": 1.17310, ...},
  "previous_week": {"high": 1.18500, "low": 1.16850, ...},
  "round_numbers": [1.17500, 1.18000],
  "timeframes": {
    "D1": {...},
    "H4": {...},
    "H1": {...}
  },
  "structural_events": [...],
  "data_quality": {...}
}
```

## Next Steps (Phase 2+)

- Pattern geometry features (double top/bottom, flags, wedges)
- LLM interpretation with structured output schema
- Validation layer (structural + semantic grounding)
- Scenario evaluation framework
- Historical evaluation and baseline comparison

## Design Principles

1. **Code determines market facts, LLM interprets**
2. **Closed candles only** - no forming candle leakage
3. **Historical replay must be causal** - no future information
4. **No fake precision** - uncertainty exposed through alternatives
5. **Version everything** - full reproducibility
6. **Deterministic IDs** - based on immutable source data, not array position

## License

MIT
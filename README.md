# Multi-Timeframe Market Analysis Engine

Deterministic, replayable forex market analysis engine implementing the V2 Engineering Specification.

The engine turns raw candles into a **canonical market state** - a facts-only JSON
document with full traceability and no look-ahead. The long-term goal is for an
LLM to *interpret* that state (never to invent it); this repository currently
contains the deterministic core and the data pipeline that feeds it.

> **New here?** See [RUNNING.md](RUNNING.md) for a step-by-step guide to
> installing, connecting a data source, and running analyses.

## Features

- **Pluggable data providers**: OANDA v20 REST (live broker feed), MT5 CSV
  exports, and a deterministic synthetic generator for offline dev/tests
- **Causal closed-candle handling**: only bars that have actually closed at the
  decision time are ever used (forming bars are excluded by the source or dropped)
- **Swing detection**: 5-left/5-right symmetric pivot windows with explicit equal high/low policy
- **Market structure**: HH/HL/LH/LL classification, internal/external structure, protected levels
- **Structural events**: causal BOS/CHoCH detection with configurable ATR-based breakout buffer
- **Support/Resistance zones**: ATR-normalized clustering with a working lifecycle (active → tested → broken → flipped)
- **Indicators**: EMA (20/50/200) with price-relationship and slope direction, RSI (14), ATR (14)
- **Fibonacci**: deterministic anchoring to the latest structurally valid swing leg
- **Previous Day/Week levels**: computed from closed D1 candles in broker time
- **Round Numbers**: generated from symbol metadata (handles JPY pairs correctly)
- **Trading Sessions**: timezone-aware (Asia/London/New York) with overlap support
- **Candlestick Features**: doji, engulfing, pin bars, inside bars, strong closes
- **Pattern Geometry**: double top/bottom, head & shoulders (+ inverse),
  ascending/descending/symmetrical triangles, rising/falling wedges,
  rectangles, channels - each with a causal forming → confirmed → failed state
- **LLM Interpretation**: grounded, schema-validated reads of the market state,
  with a retry-with-feedback loop and rejected-attempt storage
- **Historical Replay**: causal replay engine with future-data leak prevention

## Data Sources

The engine consumes `list[Candle]` and is agnostic to where candles come from.
Pick a provider with `--provider` (or `data_provider` in `config.yaml`):

| Provider | Description | Auth |
|----------|-------------|------|
| `mt5` | Live data from a running MetaTrader 5 terminal via the official Python package. No exports. Windows-only. | logged-in terminal, or `MT5_LOGIN`/`MT5_PASSWORD`/`MT5_SERVER` |
| `oanda` | OANDA v20 REST API. Real broker feed, history to ~2005, native H1/H4/D/W/M. | `OANDA_API_TOKEN` env var |
| `csv` | Offline MT5 CSV exports (`{SYMBOL}_{TIMEFRAME}.csv`). | none |
| `synthetic` | Deterministic, seeded generator (trends/pullbacks/ranges). For tests and offline work only. | none |

### MetaTrader 5 setup

The `mt5` provider talks to a locally running MT5 terminal over IPC - real
broker candles with no manual exports.

1. Install the MetaTrader5 Python package: `pip install ".[mt5]"` (Windows only).
2. Open your MT5 terminal and log into the account you want data from.
3. Run with `--provider mt5` (or `data_provider: "mt5"` in `config.yaml`).

Notes:
- The terminal must be **logged in** and must not be blocked by a modal dialog.
  Running the terminal and the script at the same privilege level avoids IPC
  timeouts (don't run the terminal as administrator while the script isn't).
- **Keep the terminal up to date.** Brokers reject outdated clients with
  `authorization ... failed (Old version)`. If you see that in the terminal's
  Journal, let MT5 update and restart before logging in.
- **Symbol names are resolved automatically.** Brokers suffix symbols
  inconsistently (e.g. OANDA Global Markets exposes `EURUSD.sml`). Ask for
  `EURUSD`; the provider finds the matching terminal symbol (override with
  `mt5.symbol_suffix` if needed).
- **Broker offset is auto-detected** from a live tick, or - when the market is
  closed - inferred from the weekly gap (the FX week reopens 21:00 UTC Sunday),
  so it stays correct across DST. Pin it with `mt5.broker_offset_hours`.
- Bars requested beyond what the terminal holds are automatically reduced, and
  history is retried while it syncs.
- Credentials are read from `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` if you
  want the provider to log the terminal in itself; they are never stored.

### OANDA setup

> **Note:** the OANDA **v20 REST API is not available for OANDA Global Markets.**
> If your account is on a Global Markets server (e.g. `OANDA_Global-Demo-1`),
> use the `mt5` provider instead. For the REST provider you need an account in a
> supported division (OANDA Corporation / OANDA Europe / OANDA Asia Pacific).

1. Open a free **fxTrade Practice** account at OANDA (supported division).
2. In the Account Management Portal, generate a **personal access token**.
3. Export it as an environment variable (never commit it):

```bash
export OANDA_API_TOKEN="your-token-here"      # bash
$env:OANDA_API_TOKEN = "your-token-here"      # PowerShell
```

Then set `data_provider: "oanda"` in `config.yaml` (or pass `--provider oanda`).
Use `oanda.environment: "practice"` for the demo endpoint or `"live"` for production.

## Project Structure

```
forex-adom/
├── app/
│   ├── config/          # Configuration management
│   ├── data/            # Providers, CSV parsing, alignment, data quality
│   │   ├── provider.py          # DataProvider protocol + factory
│   │   ├── mt5_provider.py      # MetaTrader 5 terminal (IPC) provider
│   │   ├── oanda_provider.py    # OANDA v20 REST provider
│   │   ├── csv_provider.py      # MT5 CSV provider
│   │   ├── synthetic_provider.py# Deterministic offline provider
│   │   ├── csv_loader.py        # MT5 CSV parsing
│   │   ├── timeframe_alignment.py
│   │   └── models.py            # Candle, Timeframe, metadata, quality
│   ├── indicators/      # EMA, RSI, ATR, Fibonacci, levels, sessions
│   ├── structure/       # Swing detection, market structure, causal BOS/CHoCH
│   ├── zones/           # Support/resistance zone engine
│   ├── patterns/        # Candlestick features
│   ├── market_state/    # Canonical market state builder
│   └── replay/          # Historical replay engine
├── cli.py               # Command-line interface
├── config.yaml          # Configuration file
├── pyproject.toml       # Project metadata and dependencies
└── tests/               # Determinism, causality, provider and pipeline tests
```

## Installation

```bash
# Requires Python 3.11+
python -m venv .venv
.venv/Scripts/activate          # Windows; source .venv/bin/activate on Unix

# Install with dev dependencies
pip install -e ".[dev]"
```

## Configuration

Edit `config.yaml` to customize the data provider, broker timezone offset,
swing detection, indicator periods, breakout buffer, zone clustering tolerance,
round-number step, session definitions, and OANDA settings.

## Usage

### Validate data
```bash
python cli.py validate EURUSD --provider mt5
python cli.py validate EURUSD --provider oanda
python cli.py validate EURUSD --provider csv --data-dir ./data --broker-offset 2
python cli.py validate EURUSD --provider synthetic
```

### Run a single analysis
```bash
# decision_time is the CLOSE time of the decision candle (UTC)
python cli.py analyze EURUSD "2025-12-02T21:00:00Z" --provider mt5 --output state.json
```

### Run historical replay
```bash
python cli.py replay EURUSD ./output --provider mt5 --max-snapshots 100
python cli.py replay EURUSD ./output --provider synthetic --max-snapshots 10
```

### Inspect a saved snapshot
```bash
python cli.py inspect ./output/EURUSD_swing_H1_20251202T210000Z_v2.0.0.json
```

## CSV Format (offline `csv` provider only)

Expected MT5 CSV export format (one file per timeframe). The timestamp column is
the bar's **open** time:

```
time,open,high,low,close,tick_volume,spread,real_volume
2026.10.06 10:00,1.17510,1.17590,1.17480,1.17560,1245,8,0
```

Files should be named `{SYMBOL}_{TIMEFRAME}.csv` (e.g., `EURUSD_H1.csv`).

## Deterministic Guarantees

The engine passes these reproducibility tests (see `tests/`):

1. **Same Input → Same Output**: identical candles + config + version = identical market state
2. **Future Data Test**: appending candles after T doesn't change state at T
3. **Forming Candle Test**: the current forming candle never enters closed-candle computations
4. **Higher TF Alignment**: at H1 time T, later H4 candles never leak into state
5. **Swing Confirmation**: a swing never appears before its confirmation candle closes
6. **Causal Events**: a BOS/CHoCH break always occurs strictly after its reference swing confirmed
7. **Stable IDs**: object IDs derive from immutable source data, not array position
8. **No Wall-Clock Leak**: no output field depends on the current time

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

## LLM Interpretation (Phase 2)

The model interprets the canonical market state - it never invents facts.
Anything it claims must cite an evidence ID that exists in the state, and every
level it names must correspond to a real level. Ungrounded output is rejected
and retried with the validation errors fed back.

```bash
export LLM_API_KEY="..."        # any OpenAI-compatible endpoint
export LLM_MODEL="gpt-4o-mini"

# inspect the prompt without a key
python cli.py interpret state.json --show-prompt

# run the grounded interpretation
python cli.py interpret state.json --output analysis.json
```

Output follows a strict schema (`bias`, `market_phase`, `summary`,
`timeframe_read`, `patterns`, `key_levels`, `scenarios`,
`what_changes_the_read`, `unclear`). Failed attempts are written to
`rejected_analyses/`.

## Next Steps (Phase 3+)

- Evaluation framework: chronological splits, scenario activation/invalidation,
  MFE/MAE, baseline comparison, hallucination-rate metrics
- Production hardening: scheduled triggers, storage, audit trail
- More patterns: flags/pennants, breakout/retest as first-class features

## Design Principles

1. **Code determines market facts, LLM interprets**
2. **Closed candles only** - no forming candle leakage
3. **Historical replay must be causal** - no future information
4. **No fake precision** - uncertainty exposed through alternatives
5. **Version everything** - full reproducibility
6. **Deterministic IDs** - based on immutable source data, not array position

## License

MIT

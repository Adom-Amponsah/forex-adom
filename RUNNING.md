# How to Run This & Get Data

A practical, copy-paste guide to installing the engine, pulling real market data,
and running an analysis. Written for Windows (where the MT5 provider lives), but
the engine itself runs anywhere.

---

## TL;DR

```powershell
# 1. one-time setup
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev,mt5]"

# 2. make sure the MT5 terminal is open and logged in, then:
python cli.py validate EURUSD --provider mt5
python cli.py analyze EURUSD 2026-10-09T20:00:00Z --provider mt5 --output state.json
```

If `validate` prints candle counts and `Complete: True`, you're done.

---

## 1. Prerequisites

| Thing | Needed for |
|---|---|
| Python **3.11+** | everything |
| **MetaTrader 5** terminal, installed + running + logged in | the `mt5` provider only |
| An OANDA API token (supported division) | the `oanda` provider only |
| — nothing — | the `synthetic` and `csv` providers |

You only need **one** data source. If you just want to see the engine work,
use `synthetic` (no account, no terminal).

---

## 2. One-time setup

```powershell
cd C:\Users\Adom\Desktop\Adom\Repos\AdomForex\forex-adom

python -m venv .venv
.\.venv\Scripts\activate

# core + dev tools + the Windows-only MT5 bridge
pip install -e ".[dev,mt5]"
```

Check it works (no data source needed):

```powershell
python -c "import app; print('ok')"
python -m pytest tests/ -q          # 30 tests should pass
```

---

## 3. Get data

### Option A — MetaTrader 5 (live broker data, no exports)  ← recommended on Windows

**How it works:** the `MetaTrader5` Python package is a bridge to a **locally
running MT5 terminal**. The terminal is the data source; the package just asks
it for candles over IPC. So the terminal must be **open and logged in**.

**Steps:**

1. Install your broker's MT5 (e.g. *OANDA MetaTrader 5*).
2. Open it and log in: **File → Login to Trade Account** (enter login, password, server).
3. **Keep it up to date.** If the Journal shows
   `authorization ... failed (Old version)`, let MT5 update and restart it —
   brokers reject outdated clients.
4. **Do not run the terminal as administrator** (it blocks the IPC bridge).
   Dismiss any dialogs so the terminal is idle.
5. Leave the terminal running, then:

```powershell
python cli.py validate EURUSD --provider mt5
```

Expected output (real example, OANDA Global Markets demo):

```
--- D1 ---
  Candles: 2499
  Date range: 2017-02-20 21:00:00+00:00 to 2026-10-08 21:00:00+00:00
  Complete: True
--- H4 --- ... 4999 candles ...
--- H1 --- ... 4999 candles ...
[OK] All data quality checks passed!
```

**Good to know:**
- Symbol names are **resolved automatically** — ask for `EURUSD`, it finds
  `EURUSD.sml` (this broker suffixes majors). Override with `mt5.symbol_suffix`.
- The broker's UTC offset is **auto-detected** (from a live tick, or from the
  weekly gap when the market is closed). Pin it with `mt5.broker_offset_hours`.
- The terminal can be started by the script, but it must be installed and able
  to log in.

Config (`config.yaml`):

```yaml
data_provider: "mt5"
mt5:
  terminal_path: null        # null = auto-detect
  default_bars: 5000
  broker_offset_hours: null  # null = auto-detect
  symbol_suffix: ""          # e.g. ".sml" if auto-resolution picks the wrong one
```

Credentials are only read from env vars if you want the script to log in for you
(never stored in the repo):

```powershell
$env:MT5_LOGIN="1715563512"; $env:MT5_PASSWORD="..."; $env:MT5_SERVER="OANDA_Global-Demo-1"
```

---

### Option B — OANDA v20 REST (headless, works on any OS / VPS)

Pure HTTPS — no terminal, no GUI. Requires an OANDA account in a **supported
division** (OANDA Corporation / Europe / Asia Pacific).

> ⚠️ **OANDA Global Markets is NOT supported by the v20 REST API.** If your
> account is on a Global Markets server (e.g. `OANDA_Global-Demo-1`), use
> Option A instead.

1. Open a free **fxTrade Practice** account (supported division).
2. Generate a **personal access token** in the Account Management Portal.
3. Export it and run:

```powershell
$env:OANDA_API_TOKEN="your-token"
python cli.py validate EURUSD --provider oanda
```

```yaml
data_provider: "oanda"
oanda:
  environment: "practice"   # or "live"
  price: "M"                # M = mid, B = bid, A = ask
```

---

### Option C — CSV (offline, no account)

Export candles from MT5 (**File → Export → CSV**) into `./data` as
`{SYMBOL}_{TIMEFRAME}.csv`:

```
time,open,high,low,close,tick_volume,spread,real_volume
2026.10.06 10:00,1.17510,1.17590,1.17480,1.17560,1245,8,0
```

The timestamp column is the bar's **open** time.

```powershell
python cli.py validate EURUSD --provider csv --data-dir .\data --broker-offset 2
```

---

### Option D — Synthetic (no data at all; for testing)

```powershell
python cli.py validate EURUSD --provider synthetic
python cli.py replay   EURUSD .\output --provider synthetic --max-snapshots 20
```

---

## 4. Command reference

```
python cli.py validate SYMBOL [--provider mt5|oanda|csv|synthetic]
python cli.py analyze  SYMBOL DECISION_TIME [--provider ...] [--output FILE]
python cli.py replay   SYMBOL OUTPUT_DIR [--provider ...] [--start-idx N] [--max-snapshots N]
python cli.py inspect  SNAPSHOT_FILE.json
```

Common options:
- `--provider` — `mt5` (default config is `csv`), `oanda`, `csv`, `synthetic`
- `--style` — `swing` (D1→H4→H1) or `intraday` (H4→H1→M15)
- `--decision-tf` — default `H1`
- `--broker-offset`, `--data-dir`, `--keep-last-row` — `csv` provider only

**`DECISION_TIME` must be a candle CLOSE time in UTC**, e.g. `2026-10-09T20:00:00Z`.
To find a valid one, run `validate` and use a value from the `Date range` it prints.

Examples:

```powershell
# single analysis -> JSON on stdout
python cli.py analyze EURUSD 2026-10-09T20:00:00Z --provider mt5 --output state.json
python cli.py inspect state.json

# historical replay -> one JSON file per decision candle
python cli.py replay EURUSD .\output --provider mt5 --start-idx 200 --max-snapshots 50
```

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `-10005 IPC timeout` | modal dialog open, or terminal running as **admin** | close all dialogs; launch MT5 normally (not as admin) |
| `-6 Authorization failed` | terminal not logged in | log in via **File → Login to Trade Account** |
| `authorization ... failed (Old version)` in the MT5 Journal | terminal build too old | let MT5 update, restart it, log in |
| `symbol 'EURUSD' not found` | broker uses a suffix | set `mt5.symbol_suffix` (auto-resolution usually handles it) |
| `copy_rates_from_pos failed ... Call failed` | history still syncing, or asking for more bars than exist | handled automatically (retry + shrink); lower `mt5.default_bars` if it persists |
| Timestamps look shifted / wrong session | broker offset | set `mt5.broker_offset_hours` explicitly (e.g. `3` for EET) |
| `pip install -e .` fails with "Multiple top-level packages" | old packaging | already fixed in `pyproject.toml` |
| `No decision candle found at ...` | decision time isn't a bar close | use a close time from `validate` output |

---

## 6. Output

- `analyze` → a single canonical market-state JSON (or stdout).
- `replay` → `OUTPUT_DIR/SYMBOL_style_TF_TIMESTAMP_v2.0.0.json`, one per decision candle.
- Every run is **deterministic**: same data + config = byte-identical output.

Inspect any snapshot with `python cli.py inspect <file.json>`.

---

## 7. Running on another machine / server

The **engine** is portable Python. Only the **data source** has requirements:

| Provider | Needs the MT5 app? | OS | Headless / VPS |
|---|---|---|---|
| `mt5` | **Yes** — installed, running, logged in | **Windows only** | No |
| `oanda` | No — pure HTTPS | any | **Yes** |
| `synthetic` | No | any | Yes |
| `csv` | No | any | Yes |

So: **for a Linux VPS / cloud deployment, use `oanda` (or a data API), not `mt5`.**
Switching is a one-line change (`--provider oanda`) — no engine changes.

---

## 8. Security

- **Never commit credentials.** The repo reads `OANDA_API_TOKEN`,
  `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` from the environment only.
- `.venv/`, `output/` and logs are git-ignored.
- Rotate any password that has been shared in plaintext.

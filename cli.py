"""
Command-line interface for the MT5/OANDA Analysis Engine.
"""
import click
from pathlib import Path
from datetime import datetime
import json

# Load a local .env (git-ignored) if present, so secrets never need committing.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv is a transitive dep of pydantic-settings
    pass

from app.data.models import Timeframe
from app.data.provider import get_provider, DataProvider
from app.replay.engine import run_replay, run_single_analysis


PROVIDER_CHOICES = ["csv", "oanda", "mt5", "synthetic"]


def build_provider(
    provider_name: str,
    data_dir: str | None,
    broker_offset: float,
    drop_last_row: bool,
) -> DataProvider:
    """Construct the configured data provider."""
    if provider_name == "csv":
        if not data_dir:
            raise click.UsageError("--data-dir is required for the csv provider")
        return get_provider(
            "csv",
            data_dir=data_dir,
            broker_offset_hours=broker_offset,
            drop_last_row=drop_last_row,
        )
    return get_provider(provider_name)


def timeframes_for_style(style: str, decision_tf: str):
    if style == "swing":
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
        context_tfs = [Timeframe.D1]
        structure_tfs = [Timeframe.H4]
    else:
        timeframes = [Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]
        context_tfs = [Timeframe.H4]
        structure_tfs = [Timeframe.H1]
    return timeframes, context_tfs, structure_tfs, Timeframe(decision_tf)


@click.group()
@click.version_option(version="2.0.0")
def cli():
    """Multi-Timeframe Market Analysis Engine - Deterministic Core."""
    pass


@cli.command()
@click.argument("symbol", type=str)
@click.argument("output_dir", type=click.Path(file_okay=False, dir_okay=True))
@click.option("--provider", "provider_name", type=click.Choice(PROVIDER_CHOICES), default="csv")
@click.option("--data-dir", type=click.Path(exists=True, file_okay=False, dir_okay=True), default="./data")
@click.option("--style", type=click.Choice(["swing", "intraday"]), default="swing")
@click.option("--broker-offset", type=float, default=2.0, help="Broker server time offset from UTC (hours, csv only)")
@click.option("--keep-last-row/--drop-last-row", default=False, help="Keep last row (forming candle, csv only)")
@click.option("--start-idx", type=int, default=0, help="Start index in decision timeframe candles")
@click.option("--max-snapshots", type=int, default=None, help="Maximum snapshots to generate")
@click.option("--decision-tf", type=click.Choice([tf.value for tf in Timeframe]), default="H1")
def replay(
    symbol: str,
    output_dir: str,
    provider_name: str,
    data_dir: str,
    style: str,
    broker_offset: float,
    keep_last_row: bool,
    start_idx: int,
    max_snapshots: int,
    decision_tf: str,
):
    """Run historical replay and save market states."""
    timeframes, context_tfs, structure_tfs, decision_tf_enum = timeframes_for_style(style, decision_tf)

    click.echo(f"Provider: {provider_name} | Symbol: {symbol} | Style: {style}, Decision TF: {decision_tf_enum.value}")
    if provider_name == "csv":
        click.echo(f"Data dir: {data_dir}, Broker offset: {broker_offset}h, Drop last row: {not keep_last_row}")

    try:
        provider = build_provider(provider_name, data_dir, broker_offset, not keep_last_row)
        saved = run_replay(
            provider=provider,
            symbol=symbol,
            output_dir=output_dir,
            timeframes=timeframes,
            decision_timeframe=decision_tf_enum,
            context_timeframes=context_tfs,
            structure_timeframes=structure_tfs,
            start_idx=start_idx,
            max_snapshots=max_snapshots,
        )
        click.echo(f"\n[OK] Replay complete! Saved {len(saved)} market states to {output_dir}")
        for f in saved[:5]:
            click.echo(f"  - {Path(f).name}")
        if len(saved) > 5:
            click.echo(f"  ... and {len(saved) - 5} more")
    except Exception as e:
        click.echo(f"\n[ERROR] Error: {e}", err=True)
        raise click.Abort()


@cli.command()
@click.argument("symbol", type=str)
@click.argument("decision_time", type=str)
@click.option("--provider", "provider_name", type=click.Choice(PROVIDER_CHOICES), default="csv")
@click.option("--data-dir", type=click.Path(exists=True, file_okay=False, dir_okay=True), default="./data")
@click.option("--style", type=click.Choice(["swing", "intraday"]), default="swing")
@click.option("--broker-offset", type=float, default=2.0, help="Broker server time offset from UTC (hours, csv only)")
@click.option("--keep-last-row/--drop-last-row", default=False, help="Keep last row (forming candle, csv only)")
@click.option("--decision-tf", type=click.Choice([tf.value for tf in Timeframe]), default="H1")
@click.option("--output", type=click.Path(file_okay=True, dir_okay=False), default=None, help="Output file (stdout if omitted)")
def analyze(
    symbol: str,
    decision_time: str,
    provider_name: str,
    data_dir: str,
    style: str,
    broker_offset: float,
    keep_last_row: bool,
    decision_tf: str,
    output: str,
):
    """Run analysis for a single decision point and output market state JSON."""
    try:
        dt = datetime.fromisoformat(decision_time.replace("Z", "+00:00"))
    except ValueError:
        click.echo("Invalid time format. Use ISO format: 2026-10-06T10:00:00Z", err=True)
        raise click.Abort()

    timeframes, context_tfs, structure_tfs, decision_tf_enum = timeframes_for_style(style, decision_tf)

    click.echo(f"Analyzing {symbol} at {decision_time} (provider: {provider_name})...")
    try:
        provider = build_provider(provider_name, data_dir, broker_offset, not keep_last_row)
        result = run_single_analysis(
            provider=provider,
            symbol=symbol,
            decision_time=dt,
            timeframes=timeframes,
            decision_timeframe=decision_tf_enum,
            context_timeframes=context_tfs,
            structure_timeframes=structure_tfs,
        )
        if output:
            with open(output, "w") as f:
                f.write(result)
            click.echo(f"[OK] Saved to {output}")
        else:
            click.echo(result)
    except Exception as e:
        click.echo(f"\n[ERROR] Error: {e}", err=True)
        raise click.Abort()


@cli.command()
@click.argument("symbol", type=str)
@click.option("--provider", "provider_name", type=click.Choice(PROVIDER_CHOICES), default="csv")
@click.option("--data-dir", type=click.Path(exists=True, file_okay=False, dir_okay=True), default="./data")
@click.option("--style", type=click.Choice(["swing", "intraday"]), default="swing")
@click.option("--broker-offset", type=float, default=2.0, help="Broker server time offset from UTC (hours, csv only)")
@click.option("--keep-last-row/--drop-last-row", default=False, help="Keep last row (forming candle, csv only)")
def validate(
    symbol: str,
    provider_name: str,
    data_dir: str,
    style: str,
    broker_offset: float,
    keep_last_row: bool,
):
    """Validate data quality without running full analysis."""
    timeframes, _, _, _ = timeframes_for_style(style, "H1")

    click.echo(f"Validating data for {symbol} (provider: {provider_name})...")
    try:
        provider = build_provider(provider_name, data_dir, broker_offset, not keep_last_row)
        loaded = provider.load(symbol, timeframes)

        from app.data.data_quality import validate_ohlc

        all_good = True
        for tf, series in loaded.items():
            candles = series.candles
            click.echo(f"\n--- {tf.value} ---")
            click.echo(f"  Candles: {len(candles)}")
            if candles:
                click.echo(f"  Date range: {candles[0].time_close} to {candles[-1].time_close}")
            click.echo(f"  Symbol: {series.symbol_metadata.symbol}, Digits: {series.symbol_metadata.digits}, Point: {series.symbol_metadata.point}")
            click.echo(f"  Complete: {series.quality.complete}")
            click.echo(f"  Missing bars: {len(series.quality.missing_bars)}")
            click.echo(f"  Duplicate bars: {len(series.quality.duplicate_bars)}")
            click.echo(f"  Out of order: {len(series.quality.out_of_order_bars)}")
            ohlc_errors = validate_ohlc(candles)
            click.echo(f"  OHLC errors: {len(ohlc_errors)}")
            if not series.quality.complete or ohlc_errors:
                all_good = False

        click.echo("\n[OK] All data quality checks passed!" if all_good else "\n[WARN] Some data quality issues found.")
    except Exception as e:
        click.echo(f"\n[ERROR] Error: {e}", err=True)
        raise click.Abort()


@cli.command()
@click.argument("snapshot_file", type=click.Path(exists=True, file_okay=True, dir_okay=False))
def inspect(snapshot_file: str):
    """Inspect a saved market state snapshot."""
    with open(snapshot_file) as f:
        data = json.load(f)

    click.echo("=== Market State Snapshot ===")
    click.echo(f"Symbol: {data.get('symbol')}")
    click.echo(f"Style: {data.get('style')}")
    click.echo(f"Decision TF: {data.get('decision_timeframe')}")
    click.echo(f"As Of: {data.get('as_of')}")
    click.echo(f"Engine Version: {data.get('engine_version')}")
    click.echo(f"Price: {data.get('price')}")
    click.echo(f"Session: {data.get('session')}")
    click.echo(f"Previous Day: {data.get('previous_day')}")
    click.echo(f"Previous Week: {data.get('previous_week')}")

    click.echo("\n--- Timeframes ---")
    for tf_name, tf_data in data.get("timeframes", {}).items():
        click.echo(f"\n  {tf_name}:")
        struct = tf_data.get("structure")
        if struct:
            click.echo(f"    Direction: {struct.get('direction')}")
            ext = struct.get("external", {})
            click.echo(f"    External: H={ext.get('last_high')}, L={ext.get('last_low')}")
            click.echo(f"    Protected: H={ext.get('protected_high')}, L={ext.get('protected_low')}")
        zones = tf_data.get("zones", [])
        click.echo(f"    Zones: {len(zones)}")
        for z in zones[:3]:
            click.echo(f"      {z['role']}: {z['low']:.5f}-{z['high']:.5f} ({z['status']})")
        fib = tf_data.get("fib")
        if fib:
            click.echo(f"    Fib: {fib['direction']} {fib['anchor_low_price']:.5f}->{fib['anchor_high_price']:.5f}")
            for lvl in fib["levels"]:
                click.echo(f"      {lvl['ratio']}: {lvl['price']:.5f}")
        click.echo(f"    Indicators: EMA={list(tf_data.get('ema', {}).keys())}, RSI={tf_data.get('rsi')}, ATR={tf_data.get('atr')}")


@cli.command()
@click.argument("snapshot_file", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option("--max-retries", type=int, default=None, help="Retries with grounding feedback")
@click.option("--reject-dir", type=click.Path(file_okay=False, dir_okay=True), default=None,
              help="Where to store rejected attempts")
@click.option("--output", type=click.Path(file_okay=True, dir_okay=False), default=None,
              help="Write the analysis JSON here (stdout if omitted)")
@click.option("--show-prompt", is_flag=True, help="Print the prompt and exit (no API key needed)")
def interpret(snapshot_file: str, max_retries, reject_dir, output, show_prompt):
    """Interpret a saved market-state snapshot with the LLM (grounded)."""
    from app.market_state.builder import MarketState
    from app.llm.prompt import SYSTEM_PROMPT, build_user_prompt

    state = MarketState.model_validate_json(Path(snapshot_file).read_text())

    if show_prompt:
        click.echo(SYSTEM_PROMPT)
        click.echo("\n" + "=" * 70 + "\n")
        click.echo(build_user_prompt(state))
        return

    try:
        from app.llm.client import get_llm_client
        from app.llm.analysis import interpret_market_state

        client = get_llm_client()
        result = interpret_market_state(
            state, client, max_retries=max_retries, reject_dir=reject_dir
        )

        if result.analysis is not None:
            payload = result.analysis.model_dump(mode="json")
            text = json.dumps(payload, indent=2)
            if output:
                with open(output, "w") as f:
                    f.write(text)
                click.echo(f"[OK] Analysis saved to {output}")
            else:
                click.echo(text)

        if result.validation and result.validation.warnings:
            for w in result.validation.warnings:
                click.echo(f"[WARN] {w.code}: {w.message}")

        if result.ok:
            click.echo(f"[OK] Grounded analysis ({result.attempts} attempt(s))")
        else:
            click.echo(f"[FAIL] Not grounded after {result.attempts} attempt(s): {result.error}", err=True)
            if result.validation:
                for e in result.validation.errors:
                    click.echo(f"  [ERROR] {e.code}: {e.message}", err=True)
            for f in result.rejected_files:
                click.echo(f"  rejected: {f}", err=True)
            raise click.Abort()

    except click.Abort:
        raise
    except Exception as e:
        click.echo(f"\n[ERROR] {e}", err=True)
        raise click.Abort()


if __name__ == "__main__":
    cli()

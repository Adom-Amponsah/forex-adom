"""
Command-line interface for the MT5 Analysis Engine.
"""
import click
from pathlib import Path
from datetime import datetime
import json
from app.data.models import Timeframe
from app.replay.engine import run_replay_from_csv, run_single_analysis


@click.group()
@click.version_option(version="2.0.0")
def cli():
    """MT5 Multi-Timeframe Market Analysis Engine - Deterministic Core."""
    pass


@cli.command()
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.argument("symbol", type=str)
@click.argument("output_dir", type=click.Path(file_okay=False, dir_okay=True))
@click.option("--style", type=click.Choice(["swing", "intraday"]), default="swing", help="Analysis style")
@click.option("--broker-offset", type=float, default=2.0, help="Broker server time offset from UTC (hours)")
@click.option("--keep-last-row/--drop-last-row", default=False, help="Keep last row (forming candle)")
@click.option("--start-idx", type=int, default=0, help="Start index in decision timeframe candles")
@click.option("--max-snapshots", type=int, default=None, help="Maximum snapshots to generate")
@click.option("--decision-tf", type=click.Choice([tf.value for tf in Timeframe]), default="H1", help="Decision timeframe")
def replay(
    data_dir: str,
    symbol: str,
    output_dir: str,
    style: str,
    broker_offset: float,
    keep_last_row: bool,
    start_idx: int,
    max_snapshots: int,
    decision_tf: str,
):
    """Run historical replay from CSV files and save market states."""
    
    # Configure timeframes based on style
    if style == "swing":
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
        context_tfs = [Timeframe.D1]
        structure_tfs = [Timeframe.H4]
        decision_tf_enum = Timeframe(decision_tf)
    else:
        timeframes = [Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]
        context_tfs = [Timeframe.H4]
        structure_tfs = [Timeframe.H1]
        decision_tf_enum = Timeframe(decision_tf)
    
    click.echo(f"Loading data from {data_dir} for {symbol}...")
    click.echo(f"Style: {style}, Decision TF: {decision_tf_enum.value}")
    click.echo(f"Broker offset: {broker_offset}h, Drop last row: {not keep_last_row}")
    
    try:
        saved = run_replay_from_csv(
            data_dir=data_dir,
            symbol=symbol,
            output_dir=output_dir,
            timeframes=timeframes,
            decision_timeframe=decision_tf_enum,
            context_timeframes=context_tfs,
            structure_timeframes=structure_tfs,
            broker_offset_hours=broker_offset,
            drop_last_row=not keep_last_row,
            start_idx=start_idx,
            max_snapshots=max_snapshots,
        )
        
        click.echo(f"\n[OK] Replay complete! Saved {len(saved)} market states to {output_dir}")
        
        # Show first few files
        for f in saved[:5]:
            click.echo(f"  - {Path(f).name}")
        if len(saved) > 5:
            click.echo(f"  ... and {len(saved) - 5} more")
            
    except Exception as e:
        click.echo(f"\n[ERROR] Error: {e}", err=True)
        raise click.Abort()


@cli.command()
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.argument("symbol", type=str)
@click.argument("decision_time", type=str)
@click.option("--style", type=click.Choice(["swing", "intraday"]), default="swing", help="Analysis style")
@click.option("--broker-offset", type=float, default=2.0, help="Broker server time offset from UTC (hours)")
@click.option("--keep-last-row/--drop-last-row", default=False, help="Keep last row (forming candle)")
@click.option("--decision-tf", type=click.Choice([tf.value for tf in Timeframe]), default="H1", help="Decision timeframe")
@click.option("--output", type=click.Path(file_okay=True, dir_okay=False), default=None, help="Output file (stdout if not specified)")
def analyze(
    data_dir: str,
    symbol: str,
    decision_time: str,
    style: str,
    broker_offset: float,
    keep_last_row: bool,
    decision_tf: str,
    output: str,
):
    """Run analysis for a single decision point and output market state JSON."""
    
    # Parse decision time
    try:
        dt = datetime.fromisoformat(decision_time.replace("Z", "+00:00"))
    except ValueError:
        click.echo(f"Invalid time format. Use ISO format: 2026-10-06T10:00:00Z", err=True)
        raise click.Abort()
    
    # Configure timeframes
    if style == "swing":
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
        context_tfs = [Timeframe.D1]
        structure_tfs = [Timeframe.H4]
        decision_tf_enum = Timeframe(decision_tf)
    else:
        timeframes = [Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]
        context_tfs = [Timeframe.H4]
        structure_tfs = [Timeframe.H1]
        decision_tf_enum = Timeframe(decision_tf)
    
    click.echo(f"Analyzing {symbol} at {decision_time}...")
    
    try:
        result = run_single_analysis(
            data_dir=data_dir,
            symbol=symbol,
            decision_time=dt,
            timeframes=timeframes,
            decision_timeframe=decision_tf_enum,
            context_timeframes=context_tfs,
            structure_timeframes=structure_tfs,
            broker_offset_hours=broker_offset,
            drop_last_row=not keep_last_row,
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
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.argument("symbol", type=str)
@click.option("--style", type=click.Choice(["swing", "intraday"]), default="swing", help="Analysis style")
@click.option("--broker-offset", type=float, default=2.0, help="Broker server time offset from UTC (hours)")
@click.option("--keep-last-row/--drop-last-row", default=False, help="Keep last row (forming candle)")
def validate(
    data_dir: str,
    symbol: str,
    style: str,
    broker_offset: float,
    keep_last_row: bool,
):
    """Validate CSV data quality without running full analysis."""
    
    if style == "swing":
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
    else:
        timeframes = [Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]
    
    click.echo(f"Validating data for {symbol} from {data_dir}...")
    
    try:
        from app.data.csv_loader import load_multi_timeframe_csv
        from app.data.data_quality import check_candle_sequence, validate_ohlc
        
        loaded = load_multi_timeframe_csv(
            data_dir=data_dir,
            symbol=symbol,
            timeframes=timeframes,
            broker_offset_hours=broker_offset,
            drop_last_row=not keep_last_row,
        )
        
        all_good = True
        for tf, (candles, meta, source) in loaded.items():
            click.echo(f"\n--- {tf.value} ---")
            click.echo(f"  Candles: {len(candles)}")
            click.echo(f"  Date range: {candles[0].time_close} to {candles[-1].time_close}")
            click.echo(f"  Symbol: {meta.symbol}, Digits: {meta.digits}, Point: {meta.point}")
            
            # Quality checks
            quality = check_candle_sequence(candles, tf)
            click.echo(f"  Complete: {quality.complete}")
            click.echo(f"  Missing bars: {len(quality.missing_bars)}")
            click.echo(f"  Duplicate bars: {len(quality.duplicate_bars)}")
            click.echo(f"  Out of order: {len(quality.out_of_order_bars)}")
            
            ohlc_errors = validate_ohlc(candles)
            click.echo(f"  OHLC errors: {len(ohlc_errors)}")
            
            if not quality.complete or ohlc_errors:
                all_good = False
        
        if all_good:
            click.echo("\n[OK] All data quality checks passed!")
        else:
            click.echo("\n[WARN] Some data quality issues found.")
            
    except Exception as e:
        click.echo(f"\n[ERROR] Error: {e}", err=True)
        raise click.Abort()


@cli.command()
@click.argument("snapshot_file", type=click.Path(exists=True, file_okay=True, dir_okay=False))
def inspect(snapshot_file: str):
    """Inspect a saved market state snapshot."""
    
    with open(snapshot_file) as f:
        data = json.load(f)
    
    click.echo(f"=== Market State Snapshot ===")
    click.echo(f"Symbol: {data.get('symbol')}")
    click.echo(f"Style: {data.get('style')}")
    click.echo(f"Decision TF: {data.get('decision_timeframe')}")
    click.echo(f"As Of: {data.get('as_of')}")
    click.echo(f"Engine Version: {data.get('engine_version')}")
    click.echo(f"Price: {data.get('price')}")
    click.echo(f"Session: {data.get('session')}")
    
    # Timeframes
    click.echo(f"\n--- Timeframes ---")
    for tf_name, tf_data in data.get("timeframes", {}).items():
        click.echo(f"\n  {tf_name}:")
        if tf_data.get("structure"):
            struct = tf_data["structure"]
            click.echo(f"    Direction: {struct.get('direction')}")
            ext = struct.get("external", {})
            click.echo(f"    External: H={ext.get('last_high')}, L={ext.get('last_low')}")
            click.echo(f"    Protected: H={ext.get('protected_high')}, L={ext.get('protected_low')}")
        
        zones = tf_data.get("zones", [])
        click.echo(f"    Zones: {len(zones)}")
        for z in zones[:3]:
            click.echo(f"      {z['role']}: {z['low']:.5f}-{z['high']:.5f} ({z['status']})")
        
        if tf_data.get("fib"):
            fib = tf_data["fib"]
            click.echo(f"    Fib: {fib['direction']} {fib['anchor_low_price']:.5f}->{fib['anchor_high_price']:.5f}")
            for lvl in fib["levels"]:
                click.echo(f"      {lvl['ratio']}: {lvl['price']:.5f}")
        
        indicators = {k: v for k, v in tf_data.items() if k in ("ema", "rsi", "atr")}
        click.echo(f"    Indicators: EMA={list(tf_data.get('ema', {}).keys())}, RSI={tf_data.get('rsi')}, ATR={tf_data.get('atr')}")


if __name__ == "__main__":
    cli()
"""
Support/Resistance zone engine with clustering and lifecycle.

All timestamps are derived from the decision time and the source candles - never
from the wall clock - so identical inputs always produce identical zones.
"""
from datetime import datetime
from typing import Optional
from enum import Enum
from app.data.models import Candle, Timeframe, make_zone_id
from app.structure.swings import SwingType
from app.structure.structure import LabeledSwing, StructureLabel
from app.config.settings import config
from pydantic import BaseModel, ConfigDict


class ZoneRole(str, Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


class ZoneStatus(str, Enum):
    ACTIVE = "active"
    TESTED = "tested"
    BROKEN = "broken"
    FLIPPED = "flipped"
    INVALIDATED = "invalidated"


class Zone(BaseModel):
    """Support/Resistance zone with lifecycle."""
    model_config = ConfigDict(frozen=True)

    id: str
    timeframe: Timeframe
    role: ZoneRole
    low: float
    high: float
    status: ZoneStatus = ZoneStatus.ACTIVE
    touch_count: int = 0
    first_seen: datetime
    last_touched: Optional[datetime] = None
    origin_ids: list[str] = []  # Swing IDs that created this zone
    supporting_ids: list[str] = []  # Additional confirming touches
    # For flipped zones
    flipped_from_id: Optional[str] = None


class ZoneSource(BaseModel):
    """Source object for zone construction."""
    price: float
    role: ZoneRole
    origin_id: str
    time: datetime
    strength: float = 1.0  # Weight for clustering


def collect_zone_sources(
    labeled_swings: list[LabeledSwing],
    as_of: datetime,
    prev_day_high: Optional[float] = None,
    prev_day_low: Optional[float] = None,
    prev_week_high: Optional[float] = None,
    prev_week_low: Optional[float] = None,
    round_numbers: Optional[list[float]] = None,
) -> list[ZoneSource]:
    """
    Collect all potential zone sources.

    Sources: confirmed swings, previous day/week high/low, and round numbers.
    `as_of` (the decision time) is used as the source time for the non-swing
    levels so the output is fully deterministic.
    """
    sources = []

    for ls in labeled_swings:
        if ls.swing.swing_type == SwingType.HIGH:
            strength = 1.5 if ls.label in (StructureLabel.HH, StructureLabel.LH) else 1.0
            if ls.label == StructureLabel.EH:
                strength = 1.2
            sources.append(ZoneSource(
                price=ls.swing.price,
                role=ZoneRole.RESISTANCE,
                origin_id=ls.swing.id,
                time=ls.swing.pivot_time,
                strength=strength,
            ))
        else:
            strength = 1.5 if ls.label in (StructureLabel.HL, StructureLabel.LL) else 1.0
            if ls.label == StructureLabel.EL:
                strength = 1.2
            sources.append(ZoneSource(
                price=ls.swing.price,
                role=ZoneRole.SUPPORT,
                origin_id=ls.swing.id,
                time=ls.swing.pivot_time,
                strength=strength,
            ))

    for price, role, origin, strength in [
        (prev_day_high, ZoneRole.RESISTANCE, "PREV_DAY_HIGH", 1.3),
        (prev_day_low, ZoneRole.SUPPORT, "PREV_DAY_LOW", 1.3),
        (prev_week_high, ZoneRole.RESISTANCE, "PREV_WEEK_HIGH", 1.5),
        (prev_week_low, ZoneRole.SUPPORT, "PREV_WEEK_LOW", 1.5),
    ]:
        if price is not None:
            sources.append(ZoneSource(
                price=price, role=role, origin_id=origin, time=as_of, strength=strength,
            ))

    if round_numbers:
        for rn in round_numbers:
            for role in (ZoneRole.RESISTANCE, ZoneRole.SUPPORT):
                sources.append(ZoneSource(
                    price=rn,
                    role=role,
                    origin_id=f"ROUND_{rn}",
                    time=as_of,
                    strength=0.8,
                ))

    return sources


def cluster_sources(
    sources: list[ZoneSource],
    atr_value: float,
    tolerance_min: float = 0.25,
    tolerance_max: float = 0.50,
) -> list[list[ZoneSource]]:
    """
    Cluster zone sources by price proximity (ATR-normalized).

    Roles are processed support-then-resistance and each role sorted by price,
    so the resulting cluster order (and therefore zone IDs) is deterministic.
    """
    if not sources:
        return []

    support_sources = sorted(
        (s for s in sources if s.role == ZoneRole.SUPPORT), key=lambda s: s.price
    )
    resistance_sources = sorted(
        (s for s in sources if s.role == ZoneRole.RESISTANCE), key=lambda s: s.price
    )

    clusters = []
    cluster_tolerance = atr_value * ((tolerance_min + tolerance_max) / 2)

    for role_sources in (support_sources, resistance_sources):
        if not role_sources:
            continue
        current_cluster = [role_sources[0]]
        for source in role_sources[1:]:
            center = (
                sum(s.price * s.strength for s in current_cluster)
                / sum(s.strength for s in current_cluster)
            )
            if abs(source.price - center) <= cluster_tolerance:
                current_cluster.append(source)
            else:
                clusters.append(current_cluster)
                current_cluster = [source]
        if current_cluster:
            clusters.append(current_cluster)

    return clusters


def create_zones_from_clusters(
    clusters: list[list[ZoneSource]],
    timeframe: Timeframe,
    symbol: str,
    zone_counter: int = 0,
) -> tuple[list[Zone], int]:
    """Create Zone objects from clustered sources."""
    zones = []
    counter = zone_counter

    for cluster in clusters:
        if not cluster:
            continue
        role = cluster[0].role
        prices = [s.price for s in cluster]
        origin_ids = [s.origin_id for s in cluster]

        zones.append(Zone(
            id=make_zone_id(symbol, timeframe, role.value, counter),
            timeframe=timeframe,
            role=role,
            low=min(prices),
            high=max(prices),
            status=ZoneStatus.ACTIVE,
            touch_count=0,
            first_seen=min(s.time for s in cluster),
            origin_ids=origin_ids,
        ))
        counter += 1

    return zones, counter


def update_zone_status(
    zones: list[Zone],
    candles: list[Candle],
) -> list[Zone]:
    """
    Update zone lifecycle from price action, causally.

    Transitions: ACTIVE -> TESTED (price traded into the zone) ->
    BROKEN (a candle closed beyond the zone) -> FLIPPED (after the break, a
    candle closed back through the zone from the other side).

    Only candles that closed strictly after the zone's `first_seen` are
    considered, so a zone never reacts to data that predates it.
    """
    updated = []

    for zone in zones:
        status = zone.status
        touch_count = 0
        last_touched = None
        supporting: list[str] = []
        broken = False

        for candle in candles:
            if candle.time_close <= zone.first_seen:
                continue

            overlaps = candle.high >= zone.low and candle.low <= zone.high
            if overlaps:
                touch_count += 1
                last_touched = candle.time_close
                supporting.append(candle.id)
                if status == ZoneStatus.ACTIVE:
                    status = ZoneStatus.TESTED

            if zone.role == ZoneRole.SUPPORT:
                if candle.close < zone.low:
                    broken = True
                    status = ZoneStatus.BROKEN
                elif broken and candle.close > zone.high:
                    status = ZoneStatus.FLIPPED
            else:  # RESISTANCE
                if candle.close > zone.high:
                    broken = True
                    status = ZoneStatus.BROKEN
                elif broken and candle.close < zone.low:
                    status = ZoneStatus.FLIPPED

        updated.append(zone.model_copy(update={
            "status": status,
            "touch_count": touch_count,
            "last_touched": last_touched,
            "supporting_ids": supporting,
        }))

    return updated


def build_zones(
    labeled_swings: list[LabeledSwing],
    candles: list[Candle],
    atr_values: list[Optional[float]],
    timeframe: Timeframe,
    symbol: str,
    as_of: datetime,
    prev_day_high: Optional[float] = None,
    prev_day_low: Optional[float] = None,
    prev_week_high: Optional[float] = None,
    prev_week_low: Optional[float] = None,
    round_numbers: Optional[list[float]] = None,
) -> list[Zone]:
    """Build zones for a timeframe."""
    atr = next((v for v in reversed(atr_values) if v is not None), 0.001)

    sources = collect_zone_sources(
        labeled_swings,
        as_of,
        prev_day_high, prev_day_low,
        prev_week_high, prev_week_low,
        round_numbers,
    )

    clusters = cluster_sources(
        sources, atr,
        config.zones.atr_tolerance_min,
        config.zones.atr_tolerance_max,
    )

    zones, _ = create_zones_from_clusters(clusters, timeframe, symbol, 0)
    return update_zone_status(zones, candles)

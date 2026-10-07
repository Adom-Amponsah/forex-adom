"""
Support/Resistance zone engine with clustering and lifecycle.
"""
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
from app.data.models import Candle, Timeframe, make_zone_id
from app.structure.swings import Swing, SwingType
from app.structure.structure import LabeledSwing, StructureLabel
from app.config.settings import config
from app.indicators.engine import calculate_atr
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
    prev_day_high: Optional[float] = None,
    prev_day_low: Optional[float] = None,
    prev_week_high: Optional[float] = None,
    prev_week_low: Optional[float] = None,
    round_numbers: Optional[list[float]] = None,
) -> list[ZoneSource]:
    """
    Collect all potential zone sources.
    
    Sources:
    - Confirmed swing highs -> resistance
    - Confirmed swing lows -> support
    - Previous day/week high/low
    - Round numbers
    """
    sources = []
    
    # Swings
    for ls in labeled_swings:
        if ls.swing.swing_type == SwingType.HIGH:
            # Stronger if it's a structural high (HH/LH)
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
    
    # Previous day/week levels
    if prev_day_high:
        sources.append(ZoneSource(
            price=prev_day_high,
            role=ZoneRole.RESISTANCE,
            origin_id="PREV_DAY_HIGH",
            time=datetime.now(timezone.utc),
            strength=1.3,
        ))
    if prev_day_low:
        sources.append(ZoneSource(
            price=prev_day_low,
            role=ZoneRole.SUPPORT,
            origin_id="PREV_DAY_LOW",
            time=datetime.now(timezone.utc),
            strength=1.3,
        ))
    if prev_week_high:
        sources.append(ZoneSource(
            price=prev_week_high,
            role=ZoneRole.RESISTANCE,
            origin_id="PREV_WEEK_HIGH",
            time=datetime.now(timezone.utc),
            strength=1.5,
        ))
    if prev_week_low:
        sources.append(ZoneSource(
            price=prev_week_low,
            role=ZoneRole.SUPPORT,
            origin_id="PREV_WEEK_LOW",
            time=datetime.now(timezone.utc),
            strength=1.5,
        ))
    
    # Round numbers
    if round_numbers:
        for rn in round_numbers:
            sources.append(ZoneSource(
                price=rn,
                role=ZoneRole.RESISTANCE,  # Will be both, handled in clustering
                origin_id=f"ROUND_{rn}",
                time=datetime.now(timezone.utc),
                strength=0.8,
            ))
            sources.append(ZoneSource(
                price=rn,
                role=ZoneRole.SUPPORT,
                origin_id=f"ROUND_{rn}",
                time=datetime.now(timezone.utc),
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
    Cluster zone sources by price proximity.
    
    Uses ATR-normalized distance with configurable tolerance.
    Sources of same role within tolerance are clustered.
    """
    if not sources:
        return []
    
    # Separate by role
    support_sources = [s for s in sources if s.role == ZoneRole.SUPPORT]
    resistance_sources = [s for s in sources if s.role == ZoneRole.RESISTANCE]
    
    clusters = []
    
    for role_sources in [support_sources, resistance_sources]:
        if not role_sources:
            continue
        
        # Sort by price
        role_sources.sort(key=lambda s: s.price)
        
        current_cluster = [role_sources[0]]
        cluster_tolerance = atr_value * ((tolerance_min + tolerance_max) / 2)
        
        for source in role_sources[1:]:
            # Check if within tolerance of cluster center
            cluster_center = sum(s.price * s.strength for s in current_cluster) / sum(s.strength for s in current_cluster)
            
            if abs(source.price - cluster_center) <= cluster_tolerance:
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
    zone_counter: int,
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
        
        zone = Zone(
            id=make_zone_id(symbol, timeframe, role.value, counter),
            timeframe=timeframe,
            role=role,
            low=min(prices),
            high=max(prices),
            status=ZoneStatus.ACTIVE,
            touch_count=0,
            first_seen=min(s.time for s in cluster),
            origin_ids=origin_ids,
        )
        zones.append(zone)
        counter += 1
    
    return zones, counter


def update_zone_status(
    zones: list[Zone],
    candles: list[Candle],
    current_price: float,
) -> list[Zone]:
    """
    Update zone lifecycle based on price action.
    
    - Tested: price touched zone (wick or close)
    - Broken: candle closed beyond zone
    - Flipped: broken zone retested from other side
    """
    updated = []
    
    for zone in zones:
        new_status = zone.status
        new_touch_count = zone.touch_count
        new_last_touched = zone.last_touched
        new_supporting = list(zone.supporting_ids)
        
        for candle in candles:
            # Check touch (wick into zone)
            touched = False
            if zone.role == ZoneRole.RESISTANCE:
                if candle.high >= zone.low and candle.low <= zone.high:
                    touched = True
                # Check break (close above)
                if candle.close > zone.high:
                    if zone.status == ZoneRole.RESISTANCE:
                        new_status = ZoneStatus.BROKEN
                    elif zone.status == ZoneStatus.BROKEN:
                        new_status = ZoneStatus.FLIPPED
            else:  # SUPPORT
                if candle.high >= zone.low and candle.low <= zone.high:
                    touched = True
                if candle.close < zone.low:
                    if zone.status == ZoneRole.SUPPORT:
                        new_status = ZoneStatus.BROKEN
                    elif zone.status == ZoneStatus.BROKEN:
                        new_status = ZoneStatus.FLIPPED
            
            if touched:
                new_touch_count += 1
                new_last_touched = candle.time_close
                new_supporting.append(candle.id)
        
        updated.append(Zone(
            id=zone.id,
            timeframe=zone.timeframe,
            role=zone.role,
            low=zone.low,
            high=zone.high,
            status=new_status,
            touch_count=new_touch_count,
            first_seen=zone.first_seen,
            last_touched=new_last_touched,
            origin_ids=zone.origin_ids,
            supporting_ids=new_supporting,
            flipped_from_id=zone.flipped_from_id,
        ))
    
    return updated


def build_zones(
    labeled_swings: list[LabeledSwing],
    candles: list[Candle],
    atr_values: list[Optional[float]],
    timeframe: Timeframe,
    symbol: str,
    prev_day_high: Optional[float] = None,
    prev_day_low: Optional[float] = None,
    prev_week_high: Optional[float] = None,
    prev_week_low: Optional[float] = None,
    round_numbers: Optional[list[float]] = None,
) -> list[Zone]:
    """Build zones for a timeframe."""
    # Get ATR for clustering (use latest available)
    atr = next((v for v in reversed(atr_values) if v is not None), 0.001)
    
    # Collect sources
    sources = collect_zone_sources(
        labeled_swings,
        prev_day_high, prev_day_low,
        prev_week_high, prev_week_low,
        round_numbers,
    )
    
    # Cluster
    clusters = cluster_sources(
        sources, atr,
        config.zones.atr_tolerance_min,
        config.zones.atr_tolerance_max,
    )
    
    # Create zones
    zones, _ = create_zones_from_clusters(clusters, timeframe, symbol, 0)
    
    # Update with price action
    zones = update_zone_status(zones, candles, candles[-1].close if candles else 0)
    
    return zones
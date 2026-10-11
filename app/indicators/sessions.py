"""
Trading session calculations with timezone awareness.
"""
from datetime import datetime, time as dt_time
from typing import Optional
import pytz
from app.config.settings import config
from pydantic import BaseModel, ConfigDict


class SessionState(BaseModel):
    """Active session state at a specific time."""
    model_config = ConfigDict(frozen=True)
    
    active: list[str]  # List of active session names
    primary: Optional[str] = None  # Primary session (by volume/importance)


class SessionInfo(BaseModel):
    """Session definition."""
    model_config = ConfigDict(frozen=True)
    
    name: str
    start: dt_time
    end: dt_time
    timezone: str


def parse_session_time(time_str: str) -> dt_time:
    """Parse HH:MM time string."""
    return dt_time(*map(int, time_str.split(":")))


def get_active_sessions(
    timestamp: datetime,
    session_defs: dict[str, SessionInfo],
    timezone_str: str = "UTC",
) -> SessionState:
    """
    Determine active sessions at a given UTC timestamp.
    
    Sessions can overlap (e.g., London/New York overlap).
    """
    # Convert timestamp to session timezone
    tz = pytz.timezone(timezone_str)
    local_time = timestamp.astimezone(tz).time()
    
    active = []
    for name, info in session_defs.items():
        # Handle overnight sessions (e.g., 22:00 - 06:00)
        if info.start <= info.end:
            if info.start <= local_time < info.end:
                active.append(name)
        else:
            # Overnight session
            if local_time >= info.start or local_time < info.end:
                active.append(name)
    
    # Determine primary session (priority: new_york > london > asia)
    primary = None
    priority = ["new_york", "london", "asia"]
    for p in priority:
        if p in active:
            primary = p
            break
    
    return SessionState(active=active, primary=primary)


def build_session_state(
    timestamp: datetime,
    config_sessions: Optional[dict] = None,
) -> SessionState:
    """Build session state from config."""
    if config_sessions is None:
        session_defs = {}
        for name, defn in config.sessions.definitions.items():
            session_defs[name] = SessionInfo(
                name=name,
                start=parse_session_time(defn.start),
                end=parse_session_time(defn.end),
                timezone=config.sessions.timezone,
            )
    else:
        session_defs = {}
        for name, defn in config_sessions.items():
            session_defs[name] = SessionInfo(
                name=name,
                start=parse_session_time(defn["start"]),
                end=parse_session_time(defn["end"]),
                timezone=config.sessions.timezone,
            )
    
    return get_active_sessions(timestamp, session_defs, config.sessions.timezone)
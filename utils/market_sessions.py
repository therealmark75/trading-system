"""
Exchange session state calculator.
Returns open/closed/lunch-break status with time-until-next-change
for the three major sessions: London, Hong Kong, New York.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _dur(td: timedelta) -> str:
    total_secs = max(0, int(td.total_seconds()))
    total_mins = total_secs // 60
    h, m = divmod(total_mins, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


_EXCHANGES = [
    {
        "id": "london",
        "city": "London",
        "exchange": "LSE",
        "tz": "Europe/London",
        "sessions": [("08:00", "16:30")],
    },
    {
        "id": "hong_kong",
        "city": "Hong Kong",
        "exchange": "HKEX",
        "tz": "Asia/Hong_Kong",
        "sessions": [("09:30", "12:00"), ("13:00", "16:00")],
    },
    {
        "id": "new_york",
        "city": "New York",
        "exchange": "NYSE",
        "tz": "America/New_York",
        "sessions": [("09:30", "16:00")],
    },
]

_BY_ID = {ex["id"]: ex for ex in _EXCHANGES}


def _next_weekday_open(now: datetime, sessions: list) -> datetime:
    """Return the next session open datetime (skipping weekends) after now."""
    first_open = _t(sessions[0][0])
    # Check remaining sessions today
    for s_str, _ in sessions:
        candidate = now.replace(
            hour=_t(s_str).hour, minute=_t(s_str).minute, second=0, microsecond=0
        )
        if candidate > now:
            return candidate
    # Move to next weekday
    d = now + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.replace(
        hour=first_open.hour, minute=first_open.minute, second=0, microsecond=0
    )


def get_session_state(exchange_id: str) -> dict:
    ex = _BY_ID[exchange_id]
    tz = ZoneInfo(ex["tz"])
    now = datetime.now(tz)
    wd = now.weekday()          # 0=Mon … 6=Sun
    ct = now.time().replace(second=0, microsecond=0)
    sessions = ex["sessions"]

    def _result(status, color, next_label, until_dt):
        return {
            "city":             ex["city"],
            "exchange":         ex["exchange"],
            "local_time":       now.strftime("%H:%M"),
            "status":           status,
            "color":            color,
            "next_change_label": next_label,
            "until_next_change": _dur(until_dt - now),
        }

    # ── Weekend ──────────────────────────────────────────────────────────────
    if wd >= 5:
        days_to_mon = 7 - wd           # Sat→2, Sun→1
        first_open = _t(sessions[0][0])
        monday = (now + timedelta(days=days_to_mon)).replace(
            hour=first_open.hour, minute=first_open.minute, second=0, microsecond=0
        )
        return _result("CLOSED", "red", "opens", monday)

    # ── Weekday: check sessions ───────────────────────────────────────────────
    for s_str, e_str in sessions:
        s, e = _t(s_str), _t(e_str)
        if s <= ct < e:
            end_dt = now.replace(hour=e.hour, minute=e.minute, second=0, microsecond=0)
            return _result("OPEN", "green", "closes", end_dt)

    # ── Gap between sessions (lunch break) ───────────────────────────────────
    if len(sessions) > 1:
        for i in range(len(sessions) - 1):
            _, gap_start_str = sessions[i]
            gap_end_str, _ = sessions[i + 1]
            gs, ge = _t(gap_start_str), _t(gap_end_str)
            if gs <= ct < ge:
                reopen = now.replace(hour=ge.hour, minute=ge.minute, second=0, microsecond=0)
                return _result("LUNCH BREAK", "amber", "opens", reopen)

    # ── Before open ──────────────────────────────────────────────────────────
    if ct < _t(sessions[0][0]):
        first_open = _t(sessions[0][0])
        open_dt = now.replace(
            hour=first_open.hour, minute=first_open.minute, second=0, microsecond=0
        )
        return _result("CLOSED", "red", "opens", open_dt)

    # ── After close ──────────────────────────────────────────────────────────
    next_open = _next_weekday_open(now, sessions)
    return _result("CLOSED", "red", "opens", next_open)


def get_all_sessions() -> list:
    return [get_session_state(ex["id"]) for ex in _EXCHANGES]

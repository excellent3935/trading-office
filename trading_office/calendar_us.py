"""미국 주식시장(나스닥) 거래일·거래시간 달력.

출처: Nasdaq Trader 휴장 일정, NYSE 2027 일정(2026-11 확인).
매년 말 다음 해 일정을 아래 표에 추가해야 한다. 표에 없는 해는 주말만 휴장으로 보고 경고를 출력한다.
시간은 모두 미국 동부시간(ET, 서머타임 자동 반영).
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)

HOLIDAYS = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}
EARLY_CLOSES = {date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}
COVERED_YEARS = {2026, 2027}
_warned: set[int] = set()


def _check_year(d: date):
    if d.year not in COVERED_YEARS and d.year not in _warned:
        _warned.add(d.year)
        print(f"⚠ {d.year}년 휴장 일정이 calendar_us.py에 없습니다. 주말만 휴장으로 처리합니다 — 일정을 추가하세요.")


def is_trading_day(d: date) -> bool:
    _check_year(d)
    return d.weekday() < 5 and d not in HOLIDAYS


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def trading_day_on_or_after(d: date) -> date:
    return d if is_trading_day(d) else next_trading_day(d)


def session(d: date) -> tuple[datetime, datetime]:
    """(개장, 마감) 시각(ET)."""
    close = EARLY_CLOSE if d in EARLY_CLOSES else CLOSE
    return datetime.combine(d, OPEN, ET), datetime.combine(d, close, ET)


def is_week_last_session(d: date) -> bool:
    """이번 주 마지막 거래일인가. 보통 금요일, 금요일이 휴장이면 목요일 등."""
    return is_trading_day(d) and next_trading_day(d).isocalendar()[:2] != d.isocalendar()[:2]


def same_week(a: date, b: date) -> bool:
    return a.isocalendar()[:2] == b.isocalendar()[:2]


def today_et() -> date:
    return datetime.now(ET).date()

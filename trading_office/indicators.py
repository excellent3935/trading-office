"""기술적 지표 — 표준 라이브러리만 사용.

모든 함수는 '마감된 봉'만 받는다고 가정한다(마지막 원소 = 가장 최근에 마감된 봉).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Bar:
    t: str        # 봉 시작 시각 'YYYY-MM-DD HH:MM' (ET)
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def date(self) -> str:
        return self.t[:10]


# ---------------- 이동평균 ----------------
def sma_series(xs: list[float], n: int) -> list[float | None]:
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def wma_series(xs: list[float], n: int) -> list[float | None]:
    w = n * (n + 1) / 2
    out = []
    for i in range(len(xs)):
        if i < n - 1:
            out.append(None)
        else:
            out.append(sum(xs[i - n + 1 + k] * (k + 1) for k in range(n)) / w)
    return out


def hma_series(xs: list[float], n: int) -> list[float | None]:
    """Hull MA = WMA(2·WMA(n/2) − WMA(n), √n)."""
    half, root = max(1, n // 2), max(1, int(round(math.sqrt(n))))
    a, b = wma_series(xs, half), wma_series(xs, n)
    raw = [2 * x - y for x, y in zip(a, b) if x is not None and y is not None]
    pad = len(xs) - len(raw)
    return [None] * pad + wma_series(raw, root) if raw else [None] * len(xs)


def slope_pct(series: list[float | None], k: int = 5) -> float | None:
    """최근 k봉 동안의 변화율(%) — 양수면 상승 기울기."""
    if len(series) <= k or series[-1] is None or series[-1 - k] is None:
        return None
    return (series[-1] / series[-1 - k] - 1) * 100


# ---------------- RSI ----------------
def rsi_series(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder RSI."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


# ---------------- 봉 변환 ----------------
def to_4h(bars: list[Bar]) -> list[Bar]:
    """1시간봉 → 4시간봉. 정규장 기준 하루 2개(09:30~13:30, 13:30~마감)."""
    out: list[Bar] = []
    day, grp = None, []

    def flush():
        if grp:
            out.append(Bar(grp[0].t, grp[0].o, max(b.h for b in grp), min(b.l for b in grp),
                           grp[-1].c, sum(b.v for b in grp)))

    for b in bars:
        if b.date != day:
            flush()
            day, grp = b.date, []
        elif len(grp) == 4:
            flush()
            grp = []
        grp.append(b)
    flush()
    return out


# ---------------- 스윙·다이버전스 ----------------
def swing_points(vals: list[float], kind: str, left: int = 3, right: int = 3) -> list[int]:
    idx = []
    for i in range(left, len(vals) - right):
        win = vals[i - left:i + right + 1]
        if kind == "low" and vals[i] == min(win):
            idx.append(i)
        if kind == "high" and vals[i] == max(win):
            idx.append(i)
    return idx


def divergence(bars: list[Bar], rsi: list[float | None], kind: str, lookback: int = 40,
               recent: int = 8) -> bool:
    """강세(bull): 가격 저점은 낮아졌는데 RSI 저점은 높아짐. 약세(bear)는 반대.
    두 번째 스윙이 최근 recent봉 안에 있어야 유효(오래된 다이버전스 무시)."""
    if len(bars) < lookback:
        return False
    seg = bars[-lookback:]
    rs = rsi[-lookback:]
    if kind == "bull":
        pts = [i for i in swing_points([b.l for b in seg], "low", 3, 2) if rs[i] is not None]
        if len(pts) < 2:
            return False
        i1, i2 = pts[-2], pts[-1]
        return (lookback - 1 - i2) <= recent and seg[i2].l < seg[i1].l and rs[i2] > rs[i1] and rs[i1] < 40
    pts = [i for i in swing_points([b.h for b in seg], "high", 3, 2) if rs[i] is not None]
    if len(pts) < 2:
        return False
    i1, i2 = pts[-2], pts[-1]
    return (lookback - 1 - i2) <= recent and seg[i2].h > seg[i1].h and rs[i2] < rs[i1] and rs[i1] > 60


# ---------------- 캔들 반전 ----------------
def candle_reversal(bars: list[Bar], kind: str) -> str | None:
    """마지막 마감봉의 추세 전환 캔들 이름(없으면 None)."""
    if len(bars) < 3:
        return None
    a, p, b = bars[-3], bars[-2], bars[-1]
    rng = max(b.h - b.l, 1e-9)
    body = abs(b.c - b.o)
    up_w, lo_w = b.h - max(b.o, b.c), min(b.o, b.c) - b.l
    if kind == "bull":
        declining = p.c < p.o or a.c > p.c
        if p.c < p.o and b.c > b.o and b.c >= p.o and b.o <= p.c and body > abs(p.c - p.o) * 0.9:
            return "상승 장악형"
        if declining and lo_w >= 2 * body and up_w <= max(body, rng * 0.15) and body / rng <= 0.4:
            return "망치형"
        if p.c < p.o and b.o < p.c and b.c > (p.o + p.c) / 2 and b.c < p.o:
            return "관통형"
        if a.c < a.o and abs(p.c - p.o) <= (a.o - a.c) * 0.3 and b.c > b.o and b.c > (a.o + a.c) / 2:
            return "샛별형"
        return None
    rising = p.c > p.o or a.c < p.c
    if p.c > p.o and b.c < b.o and b.c <= p.o and b.o >= p.c and body > abs(p.c - p.o) * 0.9:
        return "하락 장악형"
    if rising and up_w >= 2 * body and lo_w <= max(body, rng * 0.15) and body / rng <= 0.4:
        return "유성형"
    if p.c > p.o and b.o > p.c and b.c < (p.o + p.c) / 2 and b.c > p.o:
        return "먹구름형"
    if a.c > a.o and abs(p.c - p.o) <= (a.c - a.o) * 0.3 and b.c < b.o and b.c < (a.o + a.c) / 2:
        return "석별형"
    return None


# ---------------- 매물대·지지/저항 ----------------
def volume_profile_levels(bars: list[Bar], bins: int = 40, top: int = 6) -> list[float]:
    """거래량이 몰린 가격대(매물대) 중심 가격들. 국소 최대 구간만, 거래량 큰 순으로 top개."""
    if len(bars) < 20:
        return []
    lo, hi = min(b.l for b in bars), max(b.h for b in bars)
    if hi <= lo:
        return []
    width = (hi - lo) / bins
    vol = [0.0] * bins
    for b in bars:  # 봉의 거래량을 고가~저가 구간에 고르게 배분
        i0 = min(bins - 1, int((b.l - lo) / width))
        i1 = min(bins - 1, int((b.h - lo) / width))
        share = b.v / (i1 - i0 + 1)
        for i in range(i0, i1 + 1):
            vol[i] += share
    peaks = [i for i in range(bins)
             if vol[i] >= (vol[i - 1] if i else 0) and vol[i] >= (vol[i + 1] if i < bins - 1 else 0)]
    peaks.sort(key=lambda i: vol[i], reverse=True)
    return sorted(lo + (i + 0.5) * width for i in peaks[:top])


def swing_levels(bars: list[Bar], lookback: int = 120) -> list[float]:
    seg = bars[-lookback:]
    lows = [seg[i].l for i in swing_points([b.l for b in seg], "low", 5, 5)]
    highs = [seg[i].h for i in swing_points([b.h for b in seg], "high", 5, 5)]
    return sorted(lows + highs)


def nearest_below(price: float, levels: list[float]) -> float | None:
    c = [x for x in levels if x < price]
    return max(c) if c else None


def nearest_above(price: float, levels: list[float]) -> float | None:
    c = [x for x in levels if x > price]
    return min(c) if c else None

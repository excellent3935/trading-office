"""시장 데이터 제공자 (1시간봉 기반).

  SampleMarket : 오프라인 테스트용 가상 시장. 5분 단위로 움직이고 1시간봉을 만든다.
                 레버리지·인버스 ETF는 기초지수와 연동(QLD=+2×나스닥, QID=−2×, SOXL=+3×반도체, SOXS=−3×).
                 실제 데이터가 아니다.
  YahooMarket  : Yahoo Finance 1시간봉(비공식 엔드포인트, 표준 라이브러리). --realtime 과 함께 사용.

인터페이스
  quote(s)          현재가·전일 대비
  bars(s)           마감된 1시간봉 목록(오래된 → 최근)
  advance_to(now)   시장 시계를 now(ET)까지 진행(샘플은 5분씩 시뮬레이션, Yahoo는 새로 조회)
  new_day(d)        거래일 시작(전일 종가 갱신)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import calendar_us as cal
from .indicators import Bar

STEP_MIN = 5


@dataclass
class Quote:
    symbol: str
    price: float
    change_pct: float    # 전일 종가 대비 %
    volume_ratio: float  # 최근 봉 거래량 / 평균


class MarketData:
    symbols: list[str]

    def quote(self, symbol: str) -> Quote: ...
    def bars(self, symbol: str) -> list[Bar]: ...
    def advance_to(self, now: datetime) -> None: ...
    def new_day(self, d: date) -> None: ...
    def headlines(self) -> list[str]: return []


def bar_starts(d: date, minutes: int = 60) -> list[datetime]:
    """정규장 기본 봉(minutes분) 시작 시각들(마지막 봉은 짧을 수 있음). 시세원의 bar_min 과 같아야 한다."""
    o, c = cal.session(d)
    out, t = [], o
    while t < c:
        out.append(t)
        t += timedelta(minutes=minutes)
    return out


# =====================================================================
class SampleMarket(MarketData):
    # 기초 요인: 나스닥100, 반도체
    FACTORS = {"NDX": 0.012, "SOX": 0.022}
    # 종목: (기준가, 요인, 베타, 고유 변동성)  — 가격은 가상
    STOCKS = {
        "AAPL": (230, "NDX", 0.9, 0.010), "NVDA": (180, "SOX", 1.1, 0.016),
        "TSLA": (350, "NDX", 1.4, 0.025), "SPCX": (150, "NDX", 1.2, 0.022),
        "ANT": (80, "NDX", 1.3, 0.030), "MSFT": (480, "NDX", 0.9, 0.009),
        "META": (700, "NDX", 1.1, 0.014), "AMZN": (220, "NDX", 1.0, 0.012),
        "LLY": (900, "NDX", 0.3, 0.012), "GOOGL": (280, "NDX", 1.0, 0.012),
        "UNH": (320, "NDX", 0.3, 0.013),
        # 추가 종목(가격은 가상)
        "PLTR": (170, "NDX", 1.6, 0.030), "ORCL": (220, "NDX", 1.0, 0.016), "IBM": (280, "NDX", 0.8, 0.011),
        "CRWD": (500, "NDX", 1.3, 0.022), "AMD": (190, "SOX", 1.3, 0.022), "MU": (650, "SOX", 1.3, 0.025),
        "AVGO": (300, "SOX", 1.2, 0.016), "INTC": (40, "SOX", 1.1, 0.020), "MRVL": (100, "SOX", 1.3, 0.025),
        "TSM": (300, "SOX", 1.1, 0.016), "SKHY": (190, "SOX", 1.3, 0.030), "SNDK": (120, "SOX", 1.4, 0.030),
        "WDC": (110, "SOX", 1.3, 0.025), "VRT": (160, "NDX", 1.4, 0.025), "COHR": (110, "SOX", 1.4, 0.028),
        "ABBV": (210, "NDX", 0.3, 0.012), "JNJ": (180, "NDX", 0.3, 0.009), "CVX": (155, "NDX", 0.3, 0.011),
        "XOM": (115, "NDX", 0.3, 0.011), "MSTR": (300, "NDX", 1.8, 0.040), "COIN": (320, "NDX", 1.6, 0.035),
    }
    # 레버리지·인버스 ETF: (기준가, 요인, 배수)
    ETFS = {"QLD": (120, "NDX", 2), "QID": (22.5, "NDX", -2), "SOXL": (40, "SOX", 3), "SOXS": (9, "SOX", -3)}
    LISTED = {"ANT": date(2026, 11, 19), "SPCX": date(2026, 6, 12), "SKHY": date(2026, 7, 10)}  # 상장일 이전 봉은 없음

    def __init__(self, seed: int | None = None, start: date | None = None, history_days: int = 45):
        self.rng = random.Random(seed)
        self.symbols = list(self.STOCKS) + list(self.ETFS)
        self.f = {k: {"x": 0.0, "trend": 0.0, "mu": 0.0, "left": 0} for k in self.FACTORS}
        self.s = {k: {"x": math.log(v[0]), "trend": math.log(v[0]), "mu": 0.0, "left": 0}
                  for k, v in self.STOCKS.items()}
        self.e = {k: math.log(v[0]) for k, v in self.ETFS.items()}
        self.hist: dict[str, list[Bar]] = {s: [] for s in self.symbols}
        self.cur: dict[str, Bar | None] = {s: None for s in self.symbols}
        self.prev_close: dict[str, float] = {}
        self.now: datetime | None = None
        self.day: date | None = None
        start = cal.trading_day_on_or_after(start or cal.today_et())
        # 과거 데이터 생성
        d = start
        days = []
        for _ in range(history_days):
            d = d - timedelta(days=1)
            while not cal.is_trading_day(d):
                d -= timedelta(days=1)
            days.append(d)
        for hd in reversed(days):
            self.new_day(hd)
            self.advance_to(cal.session(hd)[1])
        self._finish_day()

    # ---- 내부 ----
    def price(self, s: str) -> float:
        return math.exp(self.s[s]["x"]) if s in self.s else math.exp(self.e[s])

    def _regime(self, st: dict, scale: float):
        if st["left"] <= 0:
            st["mu"] = self.rng.choice([1, 1, 0, -1, -1]) * scale * self.rng.uniform(0.3, 1.0) / 78
            st["left"] = self.rng.randint(4, 12) * 78
        st["left"] -= 1

    def _step(self, t: datetime):
        """5분 한 칸."""
        k = 0.012  # 추세선으로의 복원 강도 → 과매수·과매도 구간이 생김
        fr = {}
        for name, vol in self.FACTORS.items():
            st = self.f[name]
            self._regime(st, vol * 0.35)
            st["trend"] += st["mu"]
            jump = 0.0
            if self.rng.random() < 0.002:
                jump = self.rng.choice([-1, -1, 1]) * self.rng.uniform(1.0, 2.5) * vol
            r = st["mu"] + vol / math.sqrt(78) * self.rng.gauss(0, 1) - k * (st["x"] - st["trend"]) + jump
            st["x"] += r
            fr[name] = r
        moves = {}
        for s, (_, fac, beta, ivol) in self.STOCKS.items():
            st = self.s[s]
            self._regime(st, ivol * 0.3)
            st["trend"] += st["mu"] + beta * fr[fac]
            jump = 0.0
            if self.rng.random() < 0.003:  # 가끔 뉴스성 급변(되돌림은 복원력이 만든다)
                jump = self.rng.choice([-1, -1, 1]) * self.rng.uniform(1.5, 3.5) * ivol
            r = beta * fr[fac] + st["mu"] + ivol / math.sqrt(78) * self.rng.gauss(0, 1) - k * (st["x"] - st["trend"]) + jump
            st["x"] += r
            moves[s] = r
        for s, (_, fac, lev) in self.ETFS.items():
            r = lev * fr[fac] - 0.0000005  # 미세한 비용
            self.e[s] += r
            moves[s] = r
        for s in self.symbols:
            if s in self.LISTED and self.day < self.LISTED[s]:
                continue
            p = self.price(s)
            b = self.cur[s]
            minute = t.hour * 60 + t.minute
            ushape = 1.6 if minute < 10 * 60 + 30 or minute >= 15 * 60 else 1.0
            v = 1e5 * ushape * self.rng.lognormvariate(0, 0.4) * (1 + 40 * abs(moves[s]))
            if b is None:
                o = self.cur_open.get(s, p)
                self.cur[s] = Bar(self.bar_t.strftime("%Y-%m-%d %H:%M"), o, max(o, p), min(o, p), p, v)
            else:
                b.h, b.l, b.c, b.v = max(b.h, p), min(b.l, p), p, b.v + v

    def _close_bar(self):
        for s in self.symbols:
            if self.cur[s] is not None:
                self.hist[s].append(self.cur[s])
                del self.hist[s][:-600]
            self.cur[s] = None
        self.cur_open = {s: self.price(s) for s in self.symbols}

    def _finish_day(self):
        if self.day is not None:
            self._close_bar()

    # ---- 인터페이스 ----
    def new_day(self, d: date) -> None:
        if self.day is not None and self.now is not None:
            self._finish_day()
        self.prev_close = {s: self.price(s) for s in self.symbols}
        # 밤사이 갭
        for name, vol in self.FACTORS.items():
            g = self.rng.gauss(0, vol * 0.35)
            self.f[name]["x"] += g
            self.f[name]["_gap"] = g
        for s, (_, fac, beta, ivol) in self.STOCKS.items():
            self.s[s]["x"] += beta * self.f[fac]["_gap"] + self.rng.gauss(0, ivol * 0.3)
        for s, (_, fac, lev) in self.ETFS.items():
            self.e[s] += lev * self.f[fac]["_gap"]
        self.day = d
        self.starts = bar_starts(d)
        self.now = self.starts[0]
        self.bar_t = self.now
        self.cur_open = {s: self.price(s) for s in self.symbols}
        self.cur = {s: None for s in self.symbols}

    def advance_to(self, now: datetime) -> None:
        close = cal.session(self.day)[1]
        now = min(now, close)
        while self.now < now:
            t = self.now + timedelta(minutes=STEP_MIN)
            self._step(t)
            self.now = t
            nxt = [b for b in self.starts if b > self.bar_t]
            if nxt and t >= nxt[0]:  # 봉 마감 → 다음 봉 시작
                self._close_bar()
                self.bar_t = nxt[0]
            elif self.now >= close:
                self._close_bar()
                self.bar_t = close

    def quote(self, s: str) -> Quote:
        p = self.price(s)
        pc = self.prev_close.get(s, p)
        hb = self.hist[s][-20:]
        vr = (hb[-1].v / (sum(b.v for b in hb) / len(hb))) if hb else 1.0
        return Quote(s, round(p, 4), round((p / pc - 1) * 100, 2), round(vr, 2))

    def bars(self, s: str) -> list[Bar]:
        return self.hist[s]

    def headlines(self) -> list[str]:
        return []


# =====================================================================
class YahooMarket(MarketData):
    """Yahoo Finance 1시간봉. 비공식 엔드포인트라 바뀔 수 있다. 실패한 종목은 주문 대상에서 빠진다."""
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{s}?interval=60m&range=60d"

    def __init__(self, symbols: list[str], timeout: int = 10, refresh_sec: int = 60):
        import json
        import urllib.request
        self._json, self._req = json, urllib.request
        self.symbols, self.timeout, self.refresh = symbols, timeout, refresh_sec
        self.data: dict[str, dict] = {}
        self.last_fetch = 0.0
        self.advance_to(datetime.now(cal.ET))

    def _fetch(self, s: str, now: datetime) -> dict:
        req = self._req.Request(self.URL.format(s=s), headers={"User-Agent": "Mozilla/5.0"})
        with self._req.urlopen(req, timeout=self.timeout) as r:
            res = self._json.load(r)["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        bars = []
        for i, ts in enumerate(res.get("timestamp") or []):
            o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
            if None in (o, h, l, c):
                continue
            t = datetime.fromtimestamp(ts, cal.ET)
            bars.append(Bar(t.strftime("%Y-%m-%d %H:%M"), o, h, l, c, v or 0))
        # 아직 안 끝난 마지막 봉 제외
        if bars:
            t0 = datetime.strptime(bars[-1].t, "%Y-%m-%d %H:%M").replace(tzinfo=cal.ET)
            end = min(t0 + timedelta(hours=1), cal.session(t0.date())[1])
            if end > now:
                bars = bars[:-1]
        price = float(res["meta"]["regularMarketPrice"])
        today = now.date().isoformat()
        prev = [b.c for b in bars if b.date < today]
        return {"bars": bars, "price": price, "prev": prev[-1] if prev else price}

    def advance_to(self, now: datetime) -> None:
        import time as _t
        if _t.time() - self.last_fetch < self.refresh and self.data:
            return
        for s in self.symbols:
            try:
                self.data[s] = self._fetch(s, now)
            except Exception as e:  # 실패 시 이전 값 유지
                print(f"  [시세 실패] {s}: {e}")
        self.last_fetch = _t.time()
        missing = [s for s in self.symbols if s not in self.data]
        if missing:
            print(f"  [시세 없음] {missing} — 해당 종목은 판단에서 제외")

    def new_day(self, d: date) -> None:
        self.last_fetch = 0.0

    def quote(self, s: str) -> Quote:
        d = self.data[s]
        hb = d["bars"][-20:]
        vr = (hb[-1].v / (sum(b.v for b in hb) / len(hb))) if hb and sum(b.v for b in hb) else 1.0
        return Quote(s, d["price"], round((d["price"] / d["prev"] - 1) * 100, 2), round(vr, 2))

    def bars(self, s: str) -> list[Bar]:
        return self.data.get(s, {}).get("bars", [])

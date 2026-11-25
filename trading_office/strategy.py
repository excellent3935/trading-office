"""매매 전략 — 사용자 규칙을 코드로 옮긴 것.

진입(롱)  뉴스·수급이 악재가 아님(필수) + 아래 3개 중 2개 이상(entry_min_other)
          a) 1h 또는 4h RSI ≤30(최근 N봉 안) 또는 강세 다이버전스
          b) 이평선(SMA20·50·100·200, HMA200, 1시간봉) 기울기 양수 다수 + 지지선 근처  c) 추세 전환 캔들
진입(숏)  SOXS·QID만. 기초 차트(SOXL·QLD)에서 위 조건의 반대(RSI ≥70 또는 약세 다이버전스, 기울기 음수,
          저항 근처, 하락 전환 캔들)를 확인한 뒤 인버스 ETF를 매수한다. 숏은 뉴스 필수 + 3개 모두(short_min_other). 숏은 추가매수하지 않는다.
물타기    롱만 1회. 과거 주요 매물대(거래량 프로파일) 라인에 닿고 + 확실한 전환 신호(전환 캔들 + RSI 반등 또는 다이버전스)
손절      1) 1차 추가매수 후 그 지지선(매물대) 아래로 1시간봉 종가 이탈  2) 장중 악재로 수급 이탈
          (숏은 추매가 없으므로 진입 때 저항선 위로 기초 차트가 마감하면 손절)
          + 안전망: 평균단가 대비 hard_stop(레버리지 배수 반영), 일일 −1%, 금요일 청산 (risk.py/office.py)
익절      가격 ≥ 수수료(왕복 0.5%) + 여유 이상일 때, 다음 중 하나: RSI ≥70 / 주요 저항 도착 / SMA 저항 도착
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import indicators as ind
from .config import StrategyConfig


@dataclass
class Analysis:
    symbol: str
    enough: bool
    note: str = ""
    price: float = 0.0
    rsi1h: float | None = None
    rsi1h_prev: float | None = None
    rsi4h: float | None = None
    rsi_min: float | None = None      # 최근 N봉 1h/4h RSI 최저
    rsi_max: float | None = None
    div_bull: bool = False
    div_bear: bool = False
    slopes: dict = field(default_factory=dict)
    up_votes: int = 0
    down_votes: int = 0
    candle_bull: str | None = None
    candle_bear: str | None = None
    mas: dict = field(default_factory=dict)
    vp: list = field(default_factory=list)       # 매물대
    support: float | None = None
    resistance: float | None = None
    last_close: float = 0.0

    def brief(self) -> dict:
        r = lambda x: None if x is None else round(x, 2)
        return {"enough": self.enough, "note": self.note, "price": r(self.price), "rsi1h": r(self.rsi1h),
                "rsi4h": r(self.rsi4h), "div_bull": self.div_bull, "div_bear": self.div_bear,
                "slope_up": self.up_votes, "slope_down": self.down_votes, "candle_bull": self.candle_bull,
                "candle_bear": self.candle_bear, "support": r(self.support), "resistance": r(self.resistance)}


def analyze(symbol: str, bars: list[ind.Bar], price: float, cfg: StrategyConfig) -> Analysis:
    need = max(max(cfg.sma_periods), cfg.hma_period) + cfg.slope_bars + int(cfg.hma_period ** 0.5) + 2
    if len(bars) < need:
        return Analysis(symbol, False, f"데이터 부족({len(bars)}/{need}봉) — 이평선 판단 불가", price=price)
    closes = [b.c for b in bars]
    r1 = ind.rsi_series(closes, cfg.rsi_period)
    b4 = ind.to_4h(bars)
    r4 = ind.rsi_series([b.c for b in b4], cfg.rsi_period)
    recent = [x for x in r1[-cfg.rsi_watch_bars:] if x is not None] + \
             [x for x in r4[-max(1, cfg.rsi_watch_bars // 4):] if x is not None]
    a = Analysis(symbol, True, price=price, last_close=closes[-1],
                 rsi1h=r1[-1], rsi1h_prev=r1[-2], rsi4h=r4[-1] if r4 else None,
                 rsi_min=min(recent) if recent else None, rsi_max=max(recent) if recent else None)
    a.div_bull = ind.divergence(bars, r1, "bull") or ind.divergence(b4, r4, "bull", lookback=30, recent=3)
    a.div_bear = ind.divergence(bars, r1, "bear") or ind.divergence(b4, r4, "bear", lookback=30, recent=3)
    for n in cfg.sma_periods:
        s = ind.sma_series(closes, n)
        a.mas[f"SMA{n}"] = s[-1]
        a.slopes[f"SMA{n}"] = ind.slope_pct(s, cfg.slope_bars)
    h = ind.hma_series(closes, cfg.hma_period)
    a.mas[f"HMA{cfg.hma_period}"] = h[-1]
    a.slopes[f"HMA{cfg.hma_period}"] = ind.slope_pct(h, cfg.slope_bars)
    a.up_votes = sum(1 for v in a.slopes.values() if v is not None and v > 0)
    a.down_votes = sum(1 for v in a.slopes.values() if v is not None and v < 0)
    a.candle_bull = ind.candle_reversal(bars, "bull")
    a.candle_bear = ind.candle_reversal(bars, "bear")
    a.vp = ind.volume_profile_levels(bars[-cfg.profile_bars:])
    levels = a.vp + ind.swing_levels(bars) + [v for v in a.mas.values() if v]
    a.support = ind.nearest_below(price, levels)
    a.resistance = ind.nearest_above(price, levels)
    return a


@dataclass
class Setup:
    instrument: str          # 실제로 사는 종목
    side: str                # long | short
    signal: str              # 신호를 본 차트
    checks: list             # [(이름, 통과여부, 설명)]
    key_level: float | None = None
    min_other: int = 3       # 뉴스(첫 항목)는 필수, 나머지 중 최소 통과 개수

    @property
    def ok(self) -> bool:
        if len(self.checks) != 4 or not self.checks[0][1]:
            return False
        return sum(1 for c in self.checks[1:] if c[1]) >= self.min_other

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c[1])

    def as_dict(self) -> dict:
        return {"instrument": self.instrument, "side": self.side, "signal": self.signal,
                "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in self.checks],
                "rule": f"뉴스 필수 + 나머지 3개 중 {self.min_other}개", "ok": self.ok,
                "key_level": None if self.key_level is None else round(self.key_level, 2)}


class Strategy:
    def __init__(self, cfg: StrategyConfig, fee_pct: float):
        self.c, self.fee = cfg, fee_pct

    def leverage(self, s: str) -> int:
        return self.c.leverage.get(s, 1)

    def size_pct(self, s: str) -> float:
        return self.c.entry_pct / (self.leverage(s) if self.c.scale_by_leverage else 1)

    def hard_stop_pct(self, s: str) -> float:
        return self.c.hard_stop_pct * (self.leverage(s) if self.c.scale_by_leverage else 1)

    # ---------- 진입 ----------
    def entry_long(self, a: Analysis, news: float) -> Setup:
        c = self.c
        if not a.enough:
            return Setup(a.symbol, "long", a.symbol, [("데이터", False, a.note)])
        near = a.support is not None and a.price <= a.support * (1 + c.level_near_pct / 100)
        checks = [
            ("뉴스·수급", news > c.news_block_score, f"감성 {news:+.2f}"),
            ("RSI·다이버전스", (a.rsi_min is not None and a.rsi_min <= c.rsi_oversold) or a.div_bull,
             f"최근 RSI 최저 {fmt(a.rsi_min)}" + (" · 강세 다이버전스" if a.div_bull else "")),
            ("이평선 기울기·지지", a.up_votes >= c.trend_min_votes and near,
             f"상승 기울기 {a.up_votes}/5 · 지지 {fmt(a.support)}" + (" 근처" if near else " 멀음")),
            ("전환 캔들", a.candle_bull is not None, a.candle_bull or "없음"),
        ]
        return Setup(a.symbol, "long", a.symbol, checks, a.support, c.entry_min_other)

    def entry_short(self, instrument: str, u: Analysis, news_under: float) -> Setup:
        """u = 기초 차트(SOXL/QLD) 분석. 조건 충족 시 인버스 ETF(SOXS/QID)를 매수."""
        c = self.c
        if not u.enough:
            return Setup(instrument, "short", u.symbol, [("데이터", False, u.note)])
        near = u.resistance is not None and u.price >= u.resistance * (1 - c.level_near_pct / 100)
        checks = [
            ("뉴스·수급", news_under < -c.news_block_score, f"{u.symbol} 감성 {news_under:+.2f}"),
            ("RSI·다이버전스", (u.rsi_max is not None and u.rsi_max >= c.rsi_overbought) or u.div_bear,
             f"{u.symbol} 최근 RSI 최고 {fmt(u.rsi_max)}" + (" · 약세 다이버전스" if u.div_bear else "")),
            ("이평선 기울기·저항", u.down_votes >= c.trend_min_votes and near,
             f"{u.symbol} 하락 기울기 {u.down_votes}/5 · 저항 {fmt(u.resistance)}" + (" 근처" if near else " 멀음")),
            ("전환 캔들", u.candle_bear is not None, u.candle_bear or "없음"),
        ]
        return Setup(instrument, "short", u.symbol, checks, u.resistance, c.short_min_other)

    # ---------- 물타기 (롱만) ----------
    def add_check(self, meta: dict, a: Analysis) -> tuple[bool, str, float | None]:
        c = self.c
        if meta.get("side") != "long":
            return False, "숏은 추가매수 안 함", None
        if meta.get("adds", 0) >= c.long_max_adds:
            return False, "추가매수 한도 도달", None
        if not a.enough:
            return False, a.note, None
        entry = meta.get("entry_price", a.price)
        if a.price > entry * (1 - c.add_min_drop_pct / 100):
            return False, "진입가 대비 하락 폭 부족", None
        touch = [lv for lv in a.vp if abs(a.price / lv - 1) * 100 <= c.add_touch_pct]
        if not touch:
            return False, "주요 매물대 라인 아님", None
        turning = a.candle_bull is not None and (
            a.div_bull or (a.rsi1h is not None and a.rsi1h_prev is not None and a.rsi1h > a.rsi1h_prev and a.rsi1h < 45))
        if not turning:
            return False, "확실한 전환 신호 없음", None
        lv = min(touch, key=lambda x: abs(x - a.price))
        why = f"매물대 {lv:.2f} 도달 + {a.candle_bull}" + (" + 강세 다이버전스" if a.div_bull else " + RSI 반등")
        return True, why, lv

    # ---------- 청산 ----------
    def exit_reason(self, meta: dict, inst_price: float, avg_cost: float, a_sig: Analysis,
                    news_sig: float) -> tuple[str | None, str]:
        """(사유, 종류: stop|news|tp). a_sig = 신호 차트 분석(롱은 자기 자신, 숏은 기초 차트)."""
        c = self.c
        side = meta.get("side", "long")
        # 장중 악재/호재로 수급 이탈
        if side == "long" and news_sig <= c.news_exit_score:
            return f"장중 악재로 수급 이탈(감성 {news_sig:+.2f})", "news"
        if side == "short" and news_sig >= -c.news_exit_score:
            return f"{a_sig.symbol} 장중 호재로 숏 근거 소멸(감성 {news_sig:+.2f})", "news"
        # 구조적 손절
        key = meta.get("key_level")
        if key and a_sig.enough is not None:
            if side == "long" and meta.get("adds", 0) >= 1 and a_sig.last_close < key * (1 - c.stop_buffer_pct / 100):
                return f"1차 추매 후 지지선 {key:.2f} 하방 이탈(종가 {a_sig.last_close:.2f})", "stop"
            if side == "short" and a_sig.last_close > key * (1 + c.stop_buffer_pct / 100):
                return f"{a_sig.symbol} 저항선 {key:.2f} 상방 돌파(종가 {a_sig.last_close:.2f})", "stop"
        # 익절: 수수료(매도분) + 여유를 넘은 가격에서만
        tp_line = avg_cost * (1 + self.fee / 100 + c.tp_extra_pct / 100)
        if inst_price < tp_line or not a_sig.enough:
            return None, ""
        p = a_sig.price
        if side == "long":
            sma_res = [k for k, v in a_sig.mas.items() if k.startswith("SMA") and v and v >= p and v <= p * (1 + c.tp_touch_pct / 100)]
            if a_sig.rsi1h is not None and a_sig.rsi1h >= c.rsi_overbought:
                return f"RSI {a_sig.rsi1h:.1f} 과매수권 익절", "tp"
            if a_sig.resistance and p >= a_sig.resistance * (1 - c.tp_touch_pct / 100):
                return f"주요 저항 {a_sig.resistance:.2f} 도착 익절", "tp"
            if sma_res:
                return f"{sma_res[0]} 저항 도착 익절", "tp"
        else:
            sma_sup = [k for k, v in a_sig.mas.items() if k.startswith("SMA") and v and v <= p and v >= p * (1 - c.tp_touch_pct / 100)]
            if a_sig.rsi1h is not None and a_sig.rsi1h <= c.rsi_oversold:
                return f"{a_sig.symbol} RSI {a_sig.rsi1h:.1f} 과매도권 익절", "tp"
            if a_sig.support and p <= a_sig.support * (1 + c.tp_touch_pct / 100):
                return f"{a_sig.symbol} 주요 지지 {a_sig.support:.2f} 도착 익절", "tp"
            if sma_sup:
                return f"{a_sig.symbol} {sma_sup[0]} 지지 도착 익절", "tp"
        return None, ""


def fmt(x):
    return "—" if x is None else f"{x:.2f}"

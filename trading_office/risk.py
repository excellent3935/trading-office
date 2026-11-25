"""리스크 매니저 — 에이전트 위의 '컴플라이언스 팀'.

에이전트가 어떤 결정을 내려도 이 검사를 통과하지 못하면 주문은 나가지 않는다.
LLM 출력은 신뢰하지 않는 입력으로 취급한다.
"""
from dataclasses import dataclass
from .config import RiskLimits


@dataclass
class Verdict:
    ok: bool
    qty: float = 0.0
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits, seed: float, allowed_symbols: set[str]):
        self.l = limits
        self.seed = seed
        self.allowed = allowed_symbols
        self.orders_today = 0
        self.halted = False       # 손실 한도 도달 → 매매 중단
        self.target_hit = False   # 목표 달성 → 신규 진입 중단
        self.entry_blocked = ""   # 비어 있지 않으면 신규 매수 금지(사유). 예: 주말 청산 시간대

    def new_day(self):
        """거래일이 바뀌면 일일 카운터 초기화(포지션은 유지)."""
        self.orders_today, self.halted, self.target_hit, self.entry_blocked = 0, False, False, ""

    def day_pnl_pct(self, equity: float, start_equity: float) -> float:
        return (equity / start_equity - 1) * 100

    def update_state(self, equity: float, start_equity: float) -> str | None:
        pnl = self.day_pnl_pct(equity, start_equity)
        if pnl <= -self.l.daily_loss_limit_pct and not self.halted:
            self.halted = True
            return f"일일 손실 한도 도달({pnl:.2f}%) → 전량 청산, 오늘 매매 중단"
        if pnl >= self.l.daily_target_pct and not self.target_hit:
            self.target_hit = True
            return f"일일 목표 달성({pnl:.2f}%) → 신규 진입 중단, 청산·축소만 허용"
        return None

    def check(self, symbol: str, side: str, notional: float, price: float,
              position_value: float, exposure: float, equity: float) -> Verdict:
        if symbol not in self.allowed:
            return Verdict(False, reason=f"허용되지 않은 종목 {symbol}")
        if side not in ("buy", "sell"):
            return Verdict(False, reason=f"잘못된 주문 방향 {side}")
        if price <= 0 or notional <= 0:
            return Verdict(False, reason="잘못된 수량/가격")
        if self.halted:
            return Verdict(False, reason="매매 중단 상태")
        if self.orders_today >= self.l.max_orders_per_day:
            return Verdict(False, reason="일일 주문 횟수 초과")

        if side == "sell":  # 공매도 없음: 보유분만 매도
            qty = min(notional, position_value) / price
            return Verdict(qty > 0, qty, "" if qty > 0 else "보유 수량 없음")

        if self.entry_blocked:
            return Verdict(False, reason=self.entry_blocked)
        if self.target_hit:
            return Verdict(False, reason="목표 달성 후 신규 매수 금지")
        cap = min(notional,
                  equity * self.l.max_order_pct / 100,
                  equity * self.l.max_position_pct / 100 - position_value,
                  equity * self.l.max_gross_exposure_pct / 100 - exposure)
        if cap <= 0:
            return Verdict(False, reason="비중/노출 한도 초과")
        reason = "한도에 맞게 축소" if cap < notional else ""
        return Verdict(True, cap / price, reason)

    def stop_losses(self, positions: dict, prices: dict, mult=lambda s: 1) -> list[str]:
        """안전망 손절: 평균단가 대비 stop_loss_pct × mult(s) 이상 하락한 종목."""
        return [s for s, p in positions.items()
                if p.qty and s in prices and prices[s] <= p.avg_price * (1 - self.l.stop_loss_pct * mult(s) / 100)]

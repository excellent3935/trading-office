"""브로커(주문 실행).

PaperBroker: 모의매매. 기본값.
LiveBroker : 실계좌 연결용 뼈대. 주문마다 터미널에서 사람이 'yes'를 입력해야 전송된다.
             API 키는 코드/로그에 넣지 말고 환경변수로만 읽을 것.
"""
from dataclasses import dataclass, field


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    by: str


class Broker:
    def submit(self, symbol: str, side: str, qty: float, price: float, by: str) -> Fill | None: ...


@dataclass
class PaperBroker(Broker):
    cash: float
    fee_pct: float = 0.05  # 수수료+슬리피지 가정(%)
    positions: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)

    def submit(self, symbol, side, qty, price, by):
        px = price * (1 + self.fee_pct / 100) if side == "buy" else price * (1 - self.fee_pct / 100)
        pos = self.positions.setdefault(symbol, Position(symbol))
        if side == "buy":
            cost = qty * px
            if cost > self.cash:
                return None
            pos.avg_price = (pos.avg_price * pos.qty + cost) / (pos.qty + qty)
            pos.qty += qty
            self.cash -= cost
        else:
            qty = min(qty, pos.qty)
            if qty <= 0:
                return None
            pos.qty -= qty
            self.cash += qty * px
            if pos.qty == 0:
                pos.avg_price = 0.0
        f = Fill(symbol, side, qty, round(px, 4), by)
        self.fills.append(f)
        return f

    def equity(self, prices: dict) -> float:
        return self.cash + sum(p.qty * prices[s] for s, p in self.positions.items() if p.qty)

    def exposure(self, prices: dict) -> float:
        return sum(p.qty * prices[s] for s, p in self.positions.items() if p.qty)


class LiveBroker(Broker):
    """실계좌용 뼈대. 실제 API 호출부는 직접 구현해야 하며, 사람 승인 없이는 절대 전송하지 않는다."""

    def __init__(self, paper_mirror: PaperBroker):
        self.mirror = paper_mirror  # 장부 계산용

    def submit(self, symbol, side, qty, price, by):
        ans = input(f"\n[실주문 승인] {by}: {side.upper()} {symbol} {qty} @ ~{price} → 전송할까요? (yes/no) ")
        if ans.strip().lower() != "yes":
            print("  → 취소됨")
            return None
        raise NotImplementedError("여기에 증권사/거래소 API 주문 코드를 구현하세요.")

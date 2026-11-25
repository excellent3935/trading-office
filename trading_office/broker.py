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
    opened: str = ""   # 최초 진입 거래일(YYYY-MM-DD). 주간 청산·보유기간 추적용
    meta: dict = field(default_factory=dict)  # 전략 정보: side(long/short), signal, entry_price, key_level, adds


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
    fee_pct: float = 0.25       # 매매 수수료(%) — 매수·매도 각각 거래대금에 부과
    slippage_pct: float = 0.0   # 체결 가격 불리 가정(%). 기본 0, 보수적으로 보려면 0.02~0.05
    fees_paid: float = 0.0      # 누적 수수료(USD)
    positions: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)

    today: str = ""  # Office가 거래일마다 설정

    def submit(self, symbol, side, qty, price, by):
        px = price * (1 + self.slippage_pct / 100) if side == "buy" else price * (1 - self.slippage_pct / 100)
        pos = self.positions.setdefault(symbol, Position(symbol))
        if side == "buy":
            fee = qty * px * self.fee_pct / 100
            cost = qty * px + fee            # 평균단가에 수수료 포함(손익분기 기준)
            if cost > self.cash:
                return None
            self.fees_paid += fee
            if pos.qty == 0:
                pos.opened = self.today
            pos.avg_price = (pos.avg_price * pos.qty + cost) / (pos.qty + qty)
            pos.qty += qty
            self.cash -= cost
        else:
            qty = min(qty, pos.qty)
            if qty <= 0:
                return None
            fee = qty * px * self.fee_pct / 100
            pos.qty -= qty
            self.cash += qty * px - fee
            self.fees_paid += fee
            if pos.qty <= 1e-9:
                pos.qty, pos.avg_price, pos.opened, pos.meta = 0.0, 0.0, "", {}
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

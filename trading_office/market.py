"""시장 데이터 제공자.

SampleMarket: 오프라인 테스트용 가상 시세(랜덤워크). 실제 데이터가 아님.
실데이터를 붙이려면 MarketData 인터페이스를 구현한 클래스를 추가한다.
(예: 나스닥은 yfinance/Alpaca, 코인은 ccxt/업비트·바이낸스 API)
"""
import random
from dataclasses import dataclass


@dataclass
class Quote:
    symbol: str
    price: float
    change_pct: float   # 전일 대비 %
    volume_ratio: float  # 평균 대비 거래량 배수


class MarketData:
    def quote(self, symbol: str) -> Quote: ...
    def step(self) -> None: ...
    def headlines(self) -> list[str]: return []


class SampleMarket(MarketData):
    BASE = {"QQQ": 520, "NVDA": 180, "AAPL": 230, "MSFT": 480, "TSLA": 350,
            "BTC-USD": 95_000, "ETH-USD": 3_400, "SOL-USD": 190}

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.prev_close = dict(self.BASE)
        self.price = {s: p * (1 + self.rng.uniform(-0.02, 0.02)) for s, p in self.BASE.items()}

    def _vol(self, s: str) -> float:
        return 0.012 if "-USD" in s else 0.006

    def step(self) -> None:
        for s in self.price:
            self.price[s] *= 1 + self.rng.gauss(0, self._vol(s))

    def quote(self, symbol: str) -> Quote:
        p = self.price[symbol]
        return Quote(symbol, round(p, 2),
                     round((p / self.prev_close[symbol] - 1) * 100, 2),
                     round(self.rng.uniform(0.6, 1.8), 2))

    def headlines(self) -> list[str]:
        return ["[샘플] 연준 위원 발언 대기, 금리 경로 관망세",
                "[샘플] 반도체 섹터 실적 시즌 진행 중",
                "[샘플] 비트코인 ETF 순유입 지속"]


class YahooMarket(MarketData):
    """Yahoo Finance 차트 엔드포인트로 실시세를 받는다(표준 라이브러리만 사용).

    나스닥 종목(QQQ, NVDA...)과 코인(BTC-USD...)을 같은 방식으로 조회한다.
    비공식 엔드포인트라 바뀔 수 있으며, 실패한 종목은 None 처리 후 주문 대상에서 빠진다.
    step()은 매 라운드 최신 시세로 갱신한다.
    """
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{s}?interval=5m&range=1d"

    def __init__(self, symbols: list[str], timeout: int = 10):
        import json, urllib.request
        self._json, self._req = json, urllib.request
        self.symbols, self.timeout = symbols, timeout
        self.cache: dict[str, Quote] = {}
        self.step()

    def _fetch(self, s: str) -> Quote | None:
        req = self._req.Request(self.URL.format(s=s), headers={"User-Agent": "Mozilla/5.0"})
        with self._req.urlopen(req, timeout=self.timeout) as r:
            res = self._json.load(r)["chart"]["result"][0]
        meta = res["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        vols = [v for v in (res.get("indicators", {}).get("quote", [{}])[0].get("volume") or []) if v]
        ratio = (vols[-1] / (sum(vols) / len(vols))) if vols else 1.0
        return Quote(s, round(price, 4), round((price / prev - 1) * 100, 2), round(ratio, 2))

    def step(self) -> None:
        for s in self.symbols:
            try:
                q = self._fetch(s)
                if q:
                    self.cache[s] = q
            except Exception as e:  # 네트워크 오류 시 이전 값 유지
                print(f"  [시세 실패] {s}: {e}")
        missing = [s for s in self.symbols if s not in self.cache]
        if missing:
            raise RuntimeError(f"시세를 받지 못한 종목: {missing}")

    def quote(self, symbol: str) -> Quote:
        return self.cache[symbol]

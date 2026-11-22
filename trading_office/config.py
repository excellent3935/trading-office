"""오피스 전체 설정.

리스크 한도는 여기서만 정의하고, risk.py가 코드로 강제한다.
LLM(에이전트)은 이 값을 바꿀 수 없다.
"""
from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    daily_target_pct: float = 0.5      # 하루 목표 수익률(%). 달성 시 신규 진입 중단
    daily_loss_limit_pct: float = 1.0  # 하루 최대 손실(%). 도달 시 전량 청산 + 매매 중단
    max_position_pct: float = 10.0     # 종목당 최대 비중(시드 대비 %)
    max_gross_exposure_pct: float = 50.0  # 총 노출 한도(%)
    max_order_pct: float = 5.0         # 주문 1건 최대 크기(%)
    stop_loss_pct: float = 1.5         # 포지션별 손절(진입가 대비 %)
    max_orders_per_day: int = 20       # 과매매 방지
    # 보유기간: 포지션은 다음 거래일로 넘길 수 있다(오버나이트 허용).
    # 단, 매주 마지막 거래일(보통 금요일)에는 장 마감 전에 전량 청산한다.
    weekly_flatten: bool = True
    no_entry_before_close_min: int = 30   # 주 마지막 거래일: 마감 N분 전부터 신규 매수 금지
    flatten_before_close_min: int = 15    # 주 마지막 거래일: 마감 N분 전 전량 청산


@dataclass
class TeamConfig:
    seniors: dict = field(default_factory=lambda: {
        "senior_bigtech": {"market": "nasdaq 빅테크", "symbols": ["NVDA", "AAPL", "MSFT", "AMZN", "META"]},
        "senior_growth": {"market": "nasdaq 성장주·지수", "symbols": ["QQQ", "TSLA", "AVGO", "AMD", "GOOGL"]},
    })
    # 주니어 -> 담당 시니어
    juniors: dict = field(default_factory=lambda: {
        "junior_1": "senior_bigtech",
        "junior_2": "senior_bigtech",
        "junior_3": "senior_growth",
    })


@dataclass
class OfficeConfig:
    seed: float = 10_000.0          # 전체 시드(USD)
    mode: str = "paper"             # "paper" | "live" (live는 매 주문 사람 승인 필요)
    llm: str = "mock"               # "mock" | "claude"
    claude_model: str = "sonnet"
    intraday_rounds: int = 3        # 하루 중 매매 라운드 수
    data_dir: str = "office_data"   # 로그/장부/포지션 상태(state.json) 저장 위치
    realtime: bool = False          # True면 실제 미국 동부시간에 맞춰 라운드를 기다렸다 실행
    risk: RiskLimits = field(default_factory=RiskLimits)
    team: TeamConfig = field(default_factory=TeamConfig)

"""오피스 전체 설정.

리스크 한도와 전략 기준은 여기서만 정의하고, risk.py·strategy.py가 코드로 강제한다.
LLM(에이전트)은 이 값을 바꿀 수 없다.
"""
from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    daily_target_pct: float = 0.5      # 하루 목표 수익률(%). 달성 시 신규 진입 중단(청산·익절은 계속)
    daily_loss_limit_pct: float = 1.0  # 하루 최대 손실(%). 도달 시 전량 청산 + 매매 중단
    max_position_pct: float = 10.0     # 종목당 최대 비중(시드 대비 %) — 첫 진입 5% + 추매 5%
    max_gross_exposure_pct: float = 50.0  # 총 노출 한도(%)
    max_order_pct: float = 5.0         # 주문 1건 최대 크기(%)
    stop_loss_pct: float = 3.0         # 안전망 손절: 평균단가 대비 %(레버리지 ETF는 배수만큼 넓힘). 구조적 손절과 별개
    max_orders_per_day: int = 20       # 과매매 방지
    # 보유기간: 포지션은 다음 거래일로 넘길 수 있다(오버나이트 허용).
    # 단, 매주 마지막 거래일(보통 금요일)에는 장 마감 전에 전량 청산한다.
    weekly_flatten: bool = True
    no_entry_before_close_min: int = 30   # 주 마지막 거래일: 마감 N분 전부터 신규 매수 금지
    flatten_before_close_min: int = 15    # 주 마지막 거래일: 마감 N분 전 전량 청산


@dataclass
class StrategyConfig:
    # 종목: 롱은 아래 목록, 숏은 인버스 ETF 매수로만(SOXS·QID). 값 = 신호를 볼 기초 차트
    long_symbols: list = field(default_factory=lambda: [
        "SOXL", "QLD", "AAPL", "NVDA", "TSLA", "SPCX", "ANT", "MSFT", "META", "AMZN", "LLY", "GOOGL", "UNH",
        # 추가 종목(2026-11-26)
        "PLTR", "ORCL", "IBM", "CRWD", "AMD", "MU", "AVGO", "INTC", "MRVL", "TSM", "SKHY", "SNDK", "WDC", "VRT", "COHR",
        "ABBV", "JNJ", "CVX", "XOM", "MSTR", "COIN"])
    short_map: dict = field(default_factory=lambda: {"SOXS": "SOXL", "QID": "QLD"})
    leverage: dict = field(default_factory=lambda: {"SOXL": 3, "SOXS": 3, "QLD": 2, "QID": 2})
    # 지표
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_watch_bars: int = 14           # 과매도/과매수 후 이 봉 수(약 2거래일) 동안 '관찰' — 그 사이 전환 캔들이 나오면 진입
    sma_periods: tuple = (20, 50, 100, 200)
    hma_period: int = 200
    slope_bars: int = 5                # 기울기 = 최근 5봉 변화율
    entry_min_other: int = 2           # 롱: 뉴스는 필수, 나머지 3개(RSI·기울기/지지·전환캔들) 중 최소 이만큼
    short_min_other: int = 3           # 숏(SOXS·QID): 더 엄격하게 — 뉴스 필수 + 나머지 3개 모두
    trend_min_votes: int = 3           # 5개 이평선(SMA20·50·100·200, HMA200) 중 같은 방향 기울기 최소 개수
    profile_bars: int = 280            # 매물대 계산 구간(약 40거래일)
    level_near_pct: float = 1.0        # 지지/저항 '근처' 판정
    # 물타기(롱만)
    long_max_adds: int = 1
    short_max_adds: int = 0            # 숏은 추매 안 함
    add_min_drop_pct: float = 1.0      # 첫 진입가보다 이만큼 이상 내려와야 추매 검토
    add_touch_pct: float = 0.5         # 매물대 라인 터치 판정
    # 손절·익절
    stop_buffer_pct: float = 0.3       # 지지/저항 이탈 판정 여유
    tp_extra_pct: float = 0.3          # 수수료 위 최소 순이익(왕복 0.5% + 0.3% = 진입가 대비 약 +0.8%부터 익절)
    tp_touch_pct: float = 0.3          # 저항·이평선 '도착' 판정
    news_block_score: float = -0.3     # 이보다 나쁜 뉴스면 롱 진입 보류(숏은 반대)
    news_exit_score: float = -0.6      # 보유 중 이보다 나쁜 뉴스면 수급 이탈로 청산
    # 크기
    entry_pct: float = 5.0             # 1회 진입 = 시드의 5%
    scale_by_leverage: bool = True     # 레버리지 ETF는 진입 크기 ÷ 배수, 안전망 손절 × 배수
    hard_stop_pct: float = 3.0


@dataclass
class TeamConfig:
    seniors: dict = field(default_factory=lambda: {
        "senior_bigtech": {"market": "빅테크·소프트웨어", "symbols": ["AAPL", "NVDA", "TSLA", "MSFT", "META", "AMZN", "GOOGL",
                                                               "PLTR", "ORCL", "IBM", "CRWD"]},
        "senior_etf": {"market": "반도체·레버리지 ETF", "symbols": ["SOXL", "SOXS", "QLD", "QID", "AMD", "MU", "AVGO", "INTC", "MRVL",
                                                             "TSM", "SKHY", "SNDK", "WDC", "VRT", "COHR"]},
        "senior_new": {"market": "신규상장·헬스·에너지·크립토관련", "symbols": ["SPCX", "ANT", "LLY", "UNH", "ABBV", "JNJ", "CVX", "XOM",
                                                                    "MSTR", "COIN"]},
    })
    # 주니어 -> 담당 시니어 (주니어가 스캔·뉴스 확인 후 선보고)
    juniors: dict = field(default_factory=lambda: {
        "junior_1": "senior_bigtech",
        "junior_2": "senior_etf",
        "junior_3": "senior_new",
    })


@dataclass
class OfficeConfig:
    seed: float = 10_000.0          # 전체 시드(USD)
    fee_pct: float = 0.25           # 매매 수수료(%) — 매수·매도 각각 부과 (왕복 0.5%)
    slippage_pct: float = 0.0       # 체결 가격 불리 가정(%)
    mode: str = "paper"             # "paper" | "live" (live는 매 주문 사람 승인 필요)
    llm: str = "mock"               # "mock" | "claude"
    claude_model: str = "sonnet"
    intraday_rounds: int = 7        # (참고용) 판단은 1시간봉 마감마다 — 정규장 하루 약 7회
    data_dir: str = "office_data"   # 로그/장부/포지션 상태(state.json) 저장 위치
    realtime: bool = False          # True면 실제 미국 동부시간에 맞춰 5분마다 진행
    risk: RiskLimits = field(default_factory=RiskLimits)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    team: TeamConfig = field(default_factory=TeamConfig)

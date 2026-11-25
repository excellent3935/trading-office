"""실행 예
  python main.py                                   # 오늘(미국 동부 기준) 하루, 가상 시세
  python main.py --date 2026-11-23 --days 5        # 5거래일 연속 시뮬레이션 (금요일 청산 확인)
  python main.py --reset                           # 저장된 포지션(state.json) 초기화 후 시작
  python main.py --market yahoo --realtime         # 실시세·실뉴스로, 실제 시각에 맞춰 5분마다 진행
옵션: [--llm mock|claude] [--market sample|yahoo] [--export docs/data/latest.json] [--live]
"""
import argparse
import json
from datetime import date
from pathlib import Path

from trading_office.config import OfficeConfig
from trading_office.market import SampleMarket, YahooMarket
from trading_office.news import SampleNews, YahooNews
from trading_office.office import Office


def main():
    ap = argparse.ArgumentParser(description="AI 트레이딩 오피스")
    ap.add_argument("--llm", choices=["mock", "claude"], default="mock")
    ap.add_argument("--market", choices=["sample", "yahoo"], default="sample",
                    help="sample=가상 시세, yahoo=실시세")
    ap.add_argument("--seed", type=float, default=10_000)
    ap.add_argument("--random-seed", type=int, default=None, help="샘플 시세 재현용")
    ap.add_argument("--export", default=None, help="웹사이트용 JSON 저장 경로")
    ap.add_argument("--date", default=None, help="시작 거래일 YYYY-MM-DD (기본: 오늘/다음 거래일, 미국 동부 기준)")
    ap.add_argument("--days", type=int, default=1, help="연속 실행할 거래일 수")
    ap.add_argument("--reset", action="store_true", help="저장된 포지션 상태를 지우고 시드로 시작")
    ap.add_argument("--realtime", action="store_true", help="실제 미국 동부시간에 맞춰 라운드 대기")
    ap.add_argument("--live", action="store_true", help="실계좌 모드(주문마다 사람 승인). 기본은 모의매매")
    a = ap.parse_args()

    cfg = OfficeConfig(seed=a.seed, llm=a.llm,
                       mode="live" if a.live else "paper", realtime=a.realtime)
    if a.realtime and a.days > 1:
        ap.error("--realtime은 하루씩 실행하세요 (--days 1)")
    if a.market == "yahoo" and not a.realtime:
        ap.error("실시세(yahoo)는 --realtime 과 함께 하루씩 실행하세요")
    if cfg.mode == "live":
        print("⚠ 실계좌 모드: 모든 주문은 터미널에서 'yes' 입력 시에만 전송됩니다.")

    from trading_office import calendar_us as cal
    start = date.fromisoformat(a.date) if a.date else cal.trading_day_on_or_after(cal.today_et())
    symbols = [s for v in cfg.team.seniors.values() for s in v["symbols"]]
    if a.market == "yahoo":
        market, news = YahooMarket(symbols), YahooNews()
    else:
        market, news = SampleMarket(a.random_seed, start=start), SampleNews(a.random_seed)
    office = Office(cfg, market, reset_state=a.reset, news=news)
    office.run_days(start, a.days)

    if a.export:
        events = [json.loads(l) for l in office.log_path.read_text(encoding="utf-8").splitlines()]
        out = {
            "market_source": a.market, "llm": a.llm, "mode": cfg.mode, "seed": cfg.seed,
            "limits": vars(cfg.risk),
            "team": {"seniors": cfg.team.seniors, "juniors": cfg.team.juniors},
            "events": events,
        }
        p = Path(a.export)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"웹사이트 데이터 저장: {p}")


if __name__ == "__main__":
    main()

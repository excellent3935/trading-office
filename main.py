"""실행: python main.py [--llm mock|claude] [--market sample|yahoo] [--export docs/data/latest.json]"""
import argparse
import json
from pathlib import Path

from trading_office.config import OfficeConfig
from trading_office.market import SampleMarket, YahooMarket
from trading_office.office import Office


def main():
    ap = argparse.ArgumentParser(description="AI 트레이딩 오피스")
    ap.add_argument("--llm", choices=["mock", "claude"], default="mock")
    ap.add_argument("--market", choices=["sample", "yahoo"], default="sample",
                    help="sample=가상 시세, yahoo=실시세")
    ap.add_argument("--seed", type=float, default=10_000)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--random-seed", type=int, default=None, help="샘플 시세 재현용")
    ap.add_argument("--export", default=None, help="웹사이트용 JSON 저장 경로")
    ap.add_argument("--live", action="store_true", help="실계좌 모드(주문마다 사람 승인). 기본은 모의매매")
    a = ap.parse_args()

    cfg = OfficeConfig(seed=a.seed, llm=a.llm, intraday_rounds=a.rounds,
                       mode="live" if a.live else "paper")
    if cfg.mode == "live":
        print("⚠ 실계좌 모드: 모든 주문은 터미널에서 'yes' 입력 시에만 전송됩니다.")

    symbols = [s for v in cfg.team.seniors.values() for s in v["symbols"]]
    market = YahooMarket(symbols) if a.market == "yahoo" else SampleMarket(a.random_seed)
    office = Office(cfg, market)
    office.run_day()

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

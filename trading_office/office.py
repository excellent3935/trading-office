"""하루 운영 사이클.

1) 시니어 → 팀장: 일간 시황 보고
2) 팀장 → 시니어: 하루 투자전략 지시 (종목군별 배분 %)
3) 라운드마다:
   a) 주니어 → 담당 시니어: 매매 전 선보고 → 시니어 승인/반려
   b) 시니어: 지시 기반 주문 계획
   c) 모든 주문은 RiskManager 검사 → Broker 체결
   d) 손절 / 목표 달성 / 손실 한도 상태 갱신
4) 장 마감 보고 + 장부(JSONL) 저장
"""
import json
import time
from pathlib import Path

from .agents import JuniorTrader, SeniorTrader, TeamLead
from .broker import LiveBroker, PaperBroker
from .config import OfficeConfig
from .llm import ClaudeCLI, MockLLM
from .market import MarketData
from .risk import RiskManager


class Office:
    def __init__(self, cfg: OfficeConfig, market: MarketData):
        self.cfg, self.market = cfg, market
        llm = ClaudeCLI(cfg.claude_model) if cfg.llm == "claude" else MockLLM()
        rules = {"target": cfg.risk.daily_target_pct, "loss": cfg.risk.daily_loss_limit_pct,
                 "maxpos": cfg.risk.max_position_pct}

        self.lead = TeamLead("team_lead", llm, rules)
        self.seniors = {n: SeniorTrader(n, llm, rules, v["market"], v["symbols"])
                        for n, v in cfg.team.seniors.items()}
        self.juniors = {n: (JuniorTrader(n, llm, rules), s) for n, s in cfg.team.juniors.items()}

        symbols = {s for v in cfg.team.seniors.values() for s in v["symbols"]}
        self.paper = PaperBroker(cash=cfg.seed)
        self.broker = LiveBroker(self.paper) if cfg.mode == "live" else self.paper
        self.risk = RiskManager(cfg.risk, cfg.seed, symbols)

        self.dir = Path(cfg.data_dir)
        self.dir.mkdir(exist_ok=True)
        self.log_path = self.dir / f"ledger_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    # ---------- 유틸 ----------
    def log(self, event: str, **data):
        rec = {"t": time.strftime("%H:%M:%S"), "event": event, **data}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{rec['t']}] {event}: {json.dumps(data, ensure_ascii=False)[:220]}")

    def prices(self) -> dict:
        return {s: self.market.quote(s).price for s in self.risk.allowed}

    def quotes(self, symbols) -> list[dict]:
        return [vars(self.market.quote(s)) for s in symbols]

    def positions_view(self) -> dict:
        return {s: {"qty": round(p.qty, 6), "avg": round(p.avg_price, 2)}
                for s, p in self.paper.positions.items() if p.qty}

    def equity(self) -> float:
        return self.paper.equity(self.prices())

    def execute(self, by: str, symbol: str, side: str, notional: float, reason: str = ""):
        try:
            notional = float(notional)
        except (TypeError, ValueError):
            return self.log("주문거부", by=by, reason="금액 형식 오류")
        px = self.prices()
        if symbol not in px:
            return self.log("주문거부", by=by, symbol=symbol, reason="허용되지 않은 종목")
        pos = self.paper.positions.get(symbol)
        pos_val = pos.qty * px[symbol] if pos else 0.0
        v = self.risk.check(symbol, side, notional, px[symbol], pos_val,
                            self.paper.exposure(px), self.paper.equity(px))
        if not v.ok:
            return self.log("주문거부", by=by, symbol=symbol, side=side, reason=v.reason)
        fill = self.broker.submit(symbol, side, round(v.qty, 6), px[symbol], by)
        if fill:
            self.risk.orders_today += 1
            self.log("체결", by=by, symbol=symbol, side=side, qty=fill.qty, price=fill.price,
                     note=v.reason, why=reason)

    def flatten(self, by: str, only: list[str] | None = None):
        px = self.prices()
        for s, p in list(self.paper.positions.items()):
            if p.qty and (only is None or s in only):
                fill = self.broker.submit(s, "sell", p.qty, px[s], by)
                if fill:
                    self.log("청산", by=by, symbol=s, qty=fill.qty, price=fill.price)

    def check_risk_state(self, start_equity: float):
        for s in self.risk.stop_losses(self.paper.positions, self.prices()):
            self.flatten("risk_stoploss", only=[s])
        msg = self.risk.update_state(self.equity(), start_equity)
        if msg:
            self.log("리스크", message=msg)
            if self.risk.halted:
                self.flatten("risk_halt")

    # ---------- 하루 ----------
    def run_day(self):
        start_eq = self.equity()
        self.log("장시작", equity=round(start_eq, 2), mode=self.cfg.mode, llm=self.cfg.llm,
                 target_usd=round(start_eq * self.cfg.risk.daily_target_pct / 100, 2))

        # 1) 시니어 시황 보고
        heads = self.market.headlines()
        reports = {n: s.market_report(self.quotes(s.symbols), heads) for n, s in self.seniors.items()}
        for n, r in reports.items():
            self.log("시황보고", by=n, report=r)

        # 2) 팀장 전략 지시
        directive = self.lead.daily_strategy(reports, start_eq,
                                             {"target_hit": self.risk.target_hit, "halted": self.risk.halted})
        self.log("전략지시", by="team_lead", directive=directive)
        no_trade = set(directive.get("no_trade") or [])
        alloc = directive.get("allocation_pct") or {}

        for rnd in range(1, self.cfg.intraday_rounds + 1):
            if self.risk.halted:
                break
            self.market.step()
            self.log("라운드", n=rnd, equity=round(self.equity(), 2))

            # 3a) 주니어 선보고 → 시니어 승인
            for jn, (junior, sn) in self.juniors.items():
                senior = self.seniors[sn]
                wl = [x for x in reports[sn].get("watchlist", []) if x in senior.symbols]
                prop = junior.propose(sn, {"stance": directive.get("stance"),
                                           "instruction": (directive.get("instructions") or {}).get(sn)},
                                      self.quotes(senior.symbols))
                if not prop.get("symbol"):
                    continue
                self.log("선보고", by=jn, to=sn, proposal=prop)
                if prop["symbol"] in no_trade or prop["symbol"] not in senior.symbols:
                    self.log("반려", by=sn, junior=jn, comment="담당 외 종목 또는 금지 종목")
                    continue
                rv = senior.review_junior(jn, prop, directive, wl)
                self.log("승인" if rv.get("approved") else "반려", by=sn, junior=jn, review=rv)
                if rv.get("approved"):
                    self.execute(jn, prop["symbol"], prop.get("side", "buy"),
                                 min(float(rv.get("notional") or 0), float(prop.get("notional") or 0)),
                                 prop.get("thesis", ""))

            # 3b) 시니어 본매매
            for sn, senior in self.seniors.items():
                wl = [x for x in reports[sn].get("watchlist", []) if x in senior.symbols]
                try:
                    pct = max(0.0, min(float(alloc.get(sn, 0)), 100.0))
                except (TypeError, ValueError):
                    pct = 0.0
                budget = start_eq * pct / 100 / self.cfg.intraday_rounds
                plan = senior.trade_plan(directive, self.quotes(senior.symbols),
                                         self.positions_view(), budget, wl)
                for o in (plan.get("orders") or [])[:5]:
                    if not isinstance(o, dict) or o.get("symbol") in no_trade \
                            or o.get("symbol") not in senior.symbols:
                        continue
                    self.execute(sn, o["symbol"], o.get("side", "buy"), o.get("notional", 0),
                                 o.get("reason", ""))

            # 3c) 리스크 상태
            self.check_risk_state(start_eq)

        # 4) 마감
        end_eq = self.equity()
        pnl = end_eq - start_eq
        self.log("장마감", equity=round(end_eq, 2), pnl_usd=round(pnl, 2),
                 pnl_pct=round(pnl / start_eq * 100, 3),
                 target_hit=self.risk.target_hit, halted=self.risk.halted,
                 orders=self.risk.orders_today, positions=self.positions_view())
        print(f"\n장부 저장: {self.log_path}")
        return pnl

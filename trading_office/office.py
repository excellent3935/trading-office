"""거래일 운영 사이클 (포지션 이월 + 주간 청산).

하루 흐름 (시각은 미국 동부시간, 나스닥 정규장 기준)
1) 시니어 → 팀장: 일간 시황 보고 (전일 이월 포지션 포함)
2) 팀장 → 시니어: 하루 투자전략 지시 (종목군별 배분 %)
3) 라운드마다 (09:45 ~ 마감 30분 전 사이에 균등 배치):
   a) 주니어 → 담당 시니어: 매매 전 선보고 → 시니어 승인/반려
   b) 시니어: 지시 기반 주문 계획 (매수·매도 모두 가능 — 보유 연장 또는 정리)
   c) 모든 주문은 RiskManager 검사 → Broker 체결
   d) 손절 / 목표 달성 / 손실 한도 상태 갱신
4) 주 마지막 거래일(보통 금요일): 마감 30분 전부터 신규 매수 금지, 마감 15분 전 전량 청산
5) 장 마감 보고 + 장부(JSONL) + 포지션 상태(state.json) 저장 → 다음 거래일로 이월

포지션은 다음 거래일로 넘어갈 수 있다. 지난주에 진입한 포지션이 남아 있으면
(예: 금요일에 프로그램을 못 돌림) 다음 거래일 첫 라운드 전에 먼저 청산한다.
"""
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import calendar_us as cal
from .agents import JuniorTrader, SeniorTrader, TeamLead
from .broker import LiveBroker, PaperBroker, Position
from .config import OfficeConfig
from .llm import ClaudeCLI, MockLLM
from .market import MarketData
from .risk import RiskManager

WEEKDAY_KO = "월화수목금토일"


class Office:
    def __init__(self, cfg: OfficeConfig, market: MarketData, reset_state: bool = False):
        self.cfg, self.market = cfg, market
        llm = ClaudeCLI(cfg.claude_model) if cfg.llm == "claude" else MockLLM()
        r = cfg.risk
        rules = {"target": r.daily_target_pct, "loss": r.daily_loss_limit_pct, "maxpos": r.max_position_pct,
                 "noentry": r.no_entry_before_close_min, "flat": r.flatten_before_close_min}

        self.lead = TeamLead("team_lead", llm, rules)
        self.seniors = {n: SeniorTrader(n, llm, rules, v["market"], v["symbols"])
                        for n, v in cfg.team.seniors.items()}
        self.juniors = {n: (JuniorTrader(n, llm, rules), s) for n, s in cfg.team.juniors.items()}

        symbols = {s for v in cfg.team.seniors.values() for s in v["symbols"]}
        self.dir = Path(cfg.data_dir)
        self.dir.mkdir(exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.paper = PaperBroker(cash=cfg.seed)
        self.last_session = ""
        if not reset_state:
            self._load_state()
        self.broker = LiveBroker(self.paper) if cfg.mode == "live" else self.paper
        self.risk = RiskManager(cfg.risk, cfg.seed, symbols)
        self.log_path = self.dir / f"ledger_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        self.clock: datetime | None = None   # 현재 라운드 시각(ET)

    # ---------- 상태 저장/복원 ----------
    def _load_state(self):
        if not self.state_path.exists():
            return
        s = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.paper.cash = s["cash"]
        self.paper.positions = {k: Position(k, v["qty"], v["avg"], v.get("opened", ""))
                                for k, v in s.get("positions", {}).items() if v["qty"] > 0}
        self.last_session = s.get("last_session", "")

    def _save_state(self):
        self.state_path.write_text(json.dumps({
            "cash": self.paper.cash, "last_session": self.last_session,
            "positions": {k: {"qty": p.qty, "avg": p.avg_price, "opened": p.opened}
                          for k, p in self.paper.positions.items() if p.qty > 0},
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 유틸 ----------
    def log(self, event: str, **data):
        t = self.clock.strftime("%m-%d %H:%M ET") if self.clock else time.strftime("%H:%M:%S")
        rec = {"t": t, "event": event, **data}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{t}] {event}: {json.dumps(data, ensure_ascii=False)[:220]}")

    def prices(self) -> dict:
        return {s: self.market.quote(s).price for s in self.risk.allowed}

    def quotes(self, symbols) -> list[dict]:
        return [vars(self.market.quote(s)) for s in symbols]

    def positions_view(self) -> dict:
        return {s: {"qty": round(p.qty, 6), "avg": round(p.avg_price, 2), "opened": p.opened}
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
                held = p.opened
                fill = self.broker.submit(s, "sell", p.qty, px[s], by)
                if fill:
                    self.log("청산", by=by, symbol=s, qty=fill.qty, price=fill.price, opened=held)

    def check_risk_state(self, start_equity: float):
        for s in self.risk.stop_losses(self.paper.positions, self.prices()):
            self.flatten("risk_stoploss", only=[s])
        msg = self.risk.update_state(self.equity(), start_equity)
        if msg:
            self.log("리스크", message=msg)
            if self.risk.halted:
                self.flatten("risk_halt")

    # ---------- 시각 ----------
    def _schedule(self, d: date) -> tuple[list[datetime], datetime | None, datetime, datetime]:
        """(라운드 시각들, 주간 청산 시각 또는 None, 신규매수 금지 시각, 마감 시각)."""
        r = self.cfg.risk
        open_dt, close_dt = cal.session(d)
        week_last = r.weekly_flatten and cal.is_week_last_session(d)
        start = open_dt + timedelta(minutes=15)
        end = close_dt - timedelta(minutes=r.no_entry_before_close_min)
        n = max(1, self.cfg.intraday_rounds)
        rounds = [start + (end - start) * (i / max(n - 1, 1)) for i in range(n)] if n > 1 else [start]
        flat = close_dt - timedelta(minutes=r.flatten_before_close_min) if week_last else None
        no_entry = close_dt - timedelta(minutes=r.no_entry_before_close_min)
        return rounds, flat, no_entry, close_dt

    def _wait_until(self, t: datetime):
        """realtime 모드면 실제 시각까지 대기, 아니면 시뮬레이션 시계만 이동."""
        if self.cfg.realtime:
            while (left := (t - datetime.now(cal.ET)).total_seconds()) > 0:
                time.sleep(min(left, 30))
            self.clock = max(t, datetime.now(cal.ET))
        else:
            self.clock = t

    # ---------- 거래일 ----------
    def run_day(self, d: date | None = None):
        d = d or cal.trading_day_on_or_after(cal.today_et())
        if not cal.is_trading_day(d):
            raise ValueError(f"{d}는 나스닥 휴장일입니다")
        self.paper.today = d.isoformat()
        if self.last_session and self.last_session != d.isoformat():
            self.market.new_day()
        self.risk.new_day()
        rounds, flat_at, no_entry_at, close_at = self._schedule(d)
        week_last = flat_at is not None
        self.clock = cal.session(d)[0]

        start_eq = self.equity()
        self.log("장시작", date=d.isoformat(), weekday=WEEKDAY_KO[d.weekday()],
                 close_et=close_at.strftime("%H:%M"), week_last_session=week_last,
                 equity=round(start_eq, 2), carried=self.positions_view(), mode=self.cfg.mode,
                 target_usd=round(start_eq * self.cfg.risk.daily_target_pct / 100, 2))
        if week_last:
            self.log("공지", message=f"오늘은 이번 주 마지막 거래일 — {no_entry_at:%H:%M} ET부터 신규 매수 금지, "
                                   f"{flat_at:%H:%M} ET 전량 청산" + (" (조기 폐장일)" if close_at.hour < 16 else ""))

        # 0) 지난주 포지션이 남아 있으면 먼저 정리 (금요일 청산을 놓친 경우)
        stale = [s for s, p in self.paper.positions.items()
                 if p.qty and p.opened and not cal.same_week(date.fromisoformat(p.opened), d)]
        if stale:
            self.log("리스크", message=f"지난주 포지션 발견 → 즉시 정리: {stale}")
            self.flatten("weekly_rule_overdue", only=stale)

        # 1) 시니어 시황 보고
        heads = self.market.headlines()
        reports = {n: s.market_report(self.quotes(s.symbols), heads) for n, s in self.seniors.items()}
        for n, r in reports.items():
            self.log("시황보고", by=n, report=r)

        # 2) 팀장 전략 지시
        directive = self.lead.daily_strategy(reports, start_eq, {
            "target_hit": self.risk.target_hit, "halted": self.risk.halted,
            "date": d.isoformat(), "weekday": WEEKDAY_KO[d.weekday()],
            "week_last_session": week_last, "carried_positions": self.positions_view(),
            "no_entry_after_et": no_entry_at.strftime("%H:%M") if week_last else None,
            "forced_flatten_at_et": flat_at.strftime("%H:%M") if week_last else None})
        self.log("전략지시", by="team_lead", directive=directive)
        no_trade = set(directive.get("no_trade") or [])
        alloc = directive.get("allocation_pct") or {}

        for rnd, t in enumerate(rounds, 1):
            if self.risk.halted:
                break
            if self.cfg.realtime and datetime.now(cal.ET) >= close_at:
                self.log("공지", message="이미 장 마감 이후라 남은 라운드를 건너뜀")
                break
            self._wait_until(t)
            self.market.step()
            if week_last and self.clock >= no_entry_at and not self.risk.entry_blocked:
                self.risk.entry_blocked = "주 마지막 거래일 마감 전 — 신규 매수 금지"
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
                budget = start_eq * pct / 100 / len(rounds)
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

        # 4) 주 마지막 거래일: 마감 전 전량 청산 (손실 한도로 이미 멈췄어도 실행)
        if week_last:
            self.risk.entry_blocked = "주간 청산 완료 — 신규 매수 금지"
            if self.cfg.realtime and datetime.now(cal.ET) >= close_at:
                self.log("리스크", message="⚠ 장 마감 이후라 주간 청산을 못 함 → 다음 거래일 첫 라운드 전에 정리됨")
            else:
                self._wait_until(flat_at)
                self.market.step()
                if any(p.qty for p in self.paper.positions.values()):
                    self.log("주간청산", message=f"{flat_at:%H:%M} ET — 주말 전 전 포지션 청산")
                    self.flatten("weekly_close")
                else:
                    self.log("주간청산", message="보유 포지션 없음")

        # 5) 마감
        self.clock = close_at
        end_eq = self.equity()
        pnl = end_eq - start_eq
        self.last_session = d.isoformat()
        self._save_state()
        self.log("장마감", date=d.isoformat(), equity=round(end_eq, 2), pnl_usd=round(pnl, 2),
                 pnl_pct=round(pnl / start_eq * 100, 3), target_hit=self.risk.target_hit,
                 halted=self.risk.halted, orders=self.risk.orders_today,
                 carry_over=self.positions_view())
        return pnl

    def run_days(self, start: date, n: int):
        """n 거래일 연속 실행(시뮬레이션). 주말·휴장일은 건너뜀."""
        d, total = cal.trading_day_on_or_after(start), 0.0
        for _ in range(n):
            total += self.run_day(d)
            d = cal.next_trading_day(d)
        print(f"\n{n}거래일 누적 손익: {total:+.2f} USD · 장부: {self.log_path} · 상태: {self.state_path}")
        return total

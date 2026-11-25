"""거래일 운영 사이클 — 1시간봉 기반 전략 + 포지션 이월 + 주간 청산.

하루 흐름 (미국 동부시간, 나스닥 정규장)
  장 시작   주니어가 담당 종목 뉴스 수급을 매기고 → 시니어가 지표 분석을 팀장에게 보고 → 팀장이 데스크별 한도 지시
  5분마다   시장 진행, 안전망 손절(평균단가 대비), 일일 손실 한도·목표, 금요일 매수 금지·전량 청산
  1시간봉 마감마다 (10:30, 11:30, … 마감)
     1) 주니어: 뉴스 새로 확인 → 감성 점수
     2) 보유 포지션: 손절(추매 후 지지 이탈 / 숏 저항 돌파 / 악재) · 익절(수수료 위 + RSI·저항·SMA) 판단 → 즉시 실행
     3) 롱 보유분: 물타기 조건(매물대 + 전환 신호) → 주니어 선보고 → 시니어 승인 → 실행
     4) 신규 진입: 체크리스트 4개 모두 통과한 후보만 → 주니어 선보고 → 시니어 승인 → 리스크 검사 → 실행
  장 마감   보고, 장부(JSONL), 포지션 상태(state.json) 저장 → 다음 거래일로 이월

지난주에 진입한 포지션이 남아 있으면(금요일 청산을 놓친 경우) 다음 거래일 시작 직후 먼저 청산한다.
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
from .market import STEP_MIN, MarketData, bar_starts
from .news import NewsFeed, SampleNews
from .risk import RiskManager
from .strategy import Analysis, Strategy, analyze

WEEKDAY_KO = "월화수목금토일"


class Office:
    def __init__(self, cfg: OfficeConfig, market: MarketData, reset_state: bool = False,
                 news: NewsFeed | None = None):
        self.cfg, self.market = cfg, market
        self._start_eq = None
        self.news = news or SampleNews()
        llm = ClaudeCLI(cfg.claude_model) if cfg.llm == "claude" else MockLLM()
        r = cfg.risk
        rules = {"target": r.daily_target_pct, "loss": r.daily_loss_limit_pct, "maxpos": r.max_position_pct,
                 "noentry": r.no_entry_before_close_min, "flat": r.flatten_before_close_min}
        self.lead = TeamLead("team_lead", llm, rules)
        self.seniors = {n: SeniorTrader(n, llm, rules, v["market"], v["symbols"])
                        for n, v in cfg.team.seniors.items()}
        self.juniors = {n: (JuniorTrader(n, llm, rules), s) for n, s in cfg.team.juniors.items()}
        self.strategy = Strategy(cfg.strategy, cfg.fee_pct)

        symbols = {s for v in cfg.team.seniors.values() for s in v["symbols"]}
        self.dir = Path(cfg.data_dir)
        self.dir.mkdir(exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.paper = PaperBroker(cash=cfg.seed, fee_pct=cfg.fee_pct, slippage_pct=cfg.slippage_pct)
        self.last_session = ""
        if not reset_state:
            self._load_state()
        self.broker = LiveBroker(self.paper) if cfg.mode == "live" else self.paper
        self.risk = RiskManager(cfg.risk, cfg.seed, symbols)
        self.log_path = self.dir / f"ledger_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        self.clock: datetime | None = None
        self.listeners: list = []
        self.scores: dict[str, float] = {}
        self.analyses: dict[str, Analysis] = {}
        self.directive: dict = {}

    # ---------- 상태 저장/복원 ----------
    def _load_state(self):
        if not self.state_path.exists():
            return
        s = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.paper.cash = s["cash"]
        self.paper.positions = {k: Position(k, v["qty"], v["avg"], v.get("opened", ""), v.get("meta", {}))
                                for k, v in s.get("positions", {}).items() if v["qty"] > 0}
        self.last_session = s.get("last_session", "")
        self.paper.fees_paid = s.get("fees_total", 0.0)

    def _save_state(self):
        self.state_path.write_text(json.dumps({
            "cash": self.paper.cash, "last_session": self.last_session, "fees_total": self.paper.fees_paid,
            "positions": {k: {"qty": p.qty, "avg": p.avg_price, "opened": p.opened, "meta": p.meta}
                          for k, p in self.paper.positions.items() if p.qty > 0},
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 이벤트 ----------
    def _t(self):
        return self.clock.strftime("%m-%d %H:%M ET") if self.clock else time.strftime("%H:%M:%S")

    def log(self, event: str, **data):
        rec = {"t": self._t(), "event": event, **data}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if not getattr(self, "quiet", False):
            print(f"[{rec['t']}] {event}: {json.dumps(data, ensure_ascii=False, default=str)[:220]}")
        self._notify(rec)

    def emit(self, event: str, **data):
        """장부에는 남기지 않고 구독자에게만 보내는 이벤트(시세 등)."""
        self._notify({"t": self._t(), "event": event, **data})

    def _notify(self, rec: dict):
        for fn in self.listeners:
            try:
                fn(rec)
            except Exception as e:
                print(f"  [listener 오류] {e}")

    def snapshot(self) -> dict:
        px = self.prices()
        return {"prices": {s: round(p, 2) for s, p in px.items()},
                "change": {s: self.market.quote(s).change_pct for s in px},
                "cash": round(self.paper.cash, 2), "equity": round(self.paper.equity(px), 2),
                "fees": round(self.paper.fees_paid, 2), "positions": self.positions_view(),
                "orders": self.risk.orders_today, "halted": self.risk.halted,
                "target_hit": self.risk.target_hit, "entry_blocked": self.risk.entry_blocked}

    # ---------- 시세·장부 ----------
    def prices(self) -> dict:
        out = {}
        for s in self.risk.allowed:
            try:
                out[s] = self.market.quote(s).price
            except KeyError:
                pass
        return out

    def positions_view(self) -> dict:
        return {s: {"qty": round(p.qty, 6), "avg": round(p.avg_price, 2), "opened": p.opened,
                    "side": p.meta.get("side", "long"), "adds": p.meta.get("adds", 0)}
                for s, p in self.paper.positions.items() if p.qty}

    def equity(self) -> float:
        return self.paper.equity(self.prices())

    def held(self, s: str) -> Position | None:
        p = self.paper.positions.get(s)
        return p if p and p.qty > 1e-9 else None

    def execute(self, by: str, symbol: str, side: str, notional: float, reason: str = "",
                meta: dict | None = None) -> bool:
        try:
            notional = float(notional)
        except (TypeError, ValueError):
            self.log("주문거부", by=by, reason="금액 형식 오류")
            return False
        px = self.prices()
        if symbol not in px:
            self.log("주문거부", by=by, symbol=symbol, reason="허용되지 않은 종목·시세 없음")
            return False
        pos = self.paper.positions.get(symbol)
        pos_val = pos.qty * px[symbol] if pos else 0.0
        v = self.risk.check(symbol, side, notional, px[symbol], pos_val,
                            self.paper.exposure(px), self.paper.equity(px))
        if not v.ok:
            self.log("주문거부", by=by, symbol=symbol, side=side, reason=v.reason)
            return False
        fill = self.broker.submit(symbol, side, round(v.qty, 6), px[symbol], by)
        if not fill:
            return False
        self.risk.orders_today += 1
        if meta is not None and side == "buy":
            self.paper.positions[symbol].meta.update(meta)
        self.log("체결", by=by, symbol=symbol, side=side, qty=fill.qty, price=fill.price, note=v.reason, why=reason)
        return True

    def flatten(self, by: str, only: list[str] | None = None, why: str = ""):
        px = self.prices()
        for s, p in list(self.paper.positions.items()):
            if p.qty and (only is None or s in only) and s in px:
                held = p.opened
                fill = self.broker.submit(s, "sell", p.qty, px[s], by)
                if fill:
                    self.risk.orders_today += 0  # 청산은 주문 한도와 무관
                    self.log("청산", by=by, symbol=s, qty=fill.qty, price=fill.price, opened=held, why=why)

    def check_risk_state(self, start_equity: float):
        mult = lambda s: self.strategy.leverage(s) if self.cfg.strategy.scale_by_leverage else 1
        for s in self.risk.stop_losses(self.paper.positions, self.prices(), mult):
            self.flatten("risk_stoploss", only=[s],
                         why=f"안전망 손절(평균단가 −{self.cfg.risk.stop_loss_pct * mult(s):.1f}%)")
        msg = self.risk.update_state(self.equity(), start_equity)
        if msg:
            self.log("리스크", message=msg)
            if self.risk.halted:
                self.flatten("risk_halt", why="일일 손실 한도")

    # ---------- 분석·뉴스 ----------
    def senior_of(self, symbol: str) -> str:
        for n, v in self.cfg.team.seniors.items():
            if symbol in v["symbols"]:
                return n
        return next(iter(self.seniors))

    def refresh_analyses(self):
        sc = self.cfg.strategy
        need = set(self.risk.allowed) | set(sc.short_map.values())
        for s in need:
            try:
                self.analyses[s] = analyze(s, self.market.bars(s), self.market.quote(s).price, sc)
            except KeyError:
                self.analyses[s] = Analysis(s, False, "시세 없음")

    def refresh_news(self):
        self.news.refresh(sorted(self.risk.allowed), self._t())
        for jn, (junior, sn) in self.juniors.items():
            syms = self.seniors[sn].symbols
            heads = {s: self.news.headlines(s) for s in syms}
            if not any(heads.values()):
                for s in syms:
                    self.scores[s] = 0.0
                continue
            res = junior.news_scores(heads)
            sc = res.get("scores") or {}
            changed = []
            for s in syms:
                try:
                    v = max(-1.0, min(1.0, float(sc.get(s, 0))))
                except (TypeError, ValueError):
                    v = 0.0
                if abs(v - self.scores.get(s, 0.0)) >= 0.3 and abs(v) >= 0.3:
                    changed.append(f"{s} {v:+.1f}")
                self.scores[s] = v
            if changed:
                self.log("뉴스", by=jn, to=sn, summary=res.get("summary", ""), changed=changed,
                         headlines={s: h[-1] for s, h in heads.items() if h})

    def group_exposure(self, senior: str) -> float:
        px = self.prices()
        return sum(p.qty * px.get(s, 0) for s, p in self.paper.positions.items()
                   if p.qty and s in self.seniors[senior].symbols)

    def watchlist(self, top: int = 7) -> list[dict]:
        """화면용: 미보유 종목의 진입 체크리스트(통과 개수 순)."""
        sc, out = self.cfg.strategy, []
        for s in sorted(self.risk.allowed):
            if self.held(s):
                continue
            if s in sc.short_map:
                st = self.strategy.entry_short(s, self.analyses[sc.short_map[s]], self.scores.get(sc.short_map[s], 0.0))
            elif s in sc.long_symbols and s in self.analyses:
                st = self.strategy.entry_long(self.analyses[s], self.scores.get(s, 0.0))
            else:
                continue
            if len(st.checks) == 4:
                out.append({**st.as_dict(), "passed": st.passed})
        return sorted(out, key=lambda x: (-x["ok"], -x["checks"][0]["ok"], -x["passed"]))[:top]

    # ---------- 1시간봉 마감 판단 ----------
    def decide(self, week_last: bool):
        self.refresh_news()
        self.refresh_analyses()
        st, sc = self.strategy, self.cfg.strategy
        px = self.prices()
        eq = self.paper.equity(px)
        self.emit("분석", setups=self.watchlist(), thin=[s for s, a in self.analyses.items() if not a.enough])

        for jn, (junior, sn) in self.juniors.items():
            senior = self.seniors[sn]
            # 1) 보유 포지션 청산 판단
            for s in senior.symbols:
                p = self.held(s)
                if not p or s not in px:
                    continue
                sig = p.meta.get("signal", s)
                a = self.analyses.get(sig)
                if not a:
                    continue
                reason, kind = st.exit_reason(p.meta, px[s], p.avg_price, a, self.scores.get(sig, 0.0))
                if reason:
                    self.log("청산판단", by=sn, symbol=s, kind=kind, reason=reason)
                    self.flatten(sn, only=[s], why=reason)
            if self.risk.halted:
                return
            # 2) 물타기 (롱만)
            for s in senior.symbols:
                p = self.held(s)
                if not p or p.meta.get("side") != "long" or s not in self.analyses:
                    continue
                ok, why, lv = st.add_check(p.meta, self.analyses[s])
                if not ok:
                    continue
                setup = {"kind": "add", "instrument": s, "side": "long", "why": why, "key_level": lv}
                self.log("선보고", by=jn, to=sn, proposal={"symbol": s, "side": "buy", "kind": "추가매수",
                                                         "notional": round(eq * st.size_pct(s) / 100, 2), "thesis": why})
                rv = senior.review_setup(jn, setup, self.directive, {"position": self.positions_view().get(s)})
                self.log("승인" if rv.get("approved") else "반려", by=sn, junior=jn, review=rv)
                if rv.get("approved"):
                    if self.execute(sn, s, "buy", eq * st.size_pct(s) / 100, f"물타기: {why}"):
                        p.meta["adds"] = p.meta.get("adds", 0) + 1
                        p.meta["key_level"] = lv
            # 3) 신규 진입
            no_trade = set(self.directive.get("no_trade") or [])
            try:
                cap = float((self.directive.get("allocation_pct") or {}).get(sn, 20))
            except (TypeError, ValueError):
                cap = 20.0
            watch, cands = [], []
            for s in senior.symbols:
                if self.held(s) or s not in px:
                    continue
                if s in sc.short_map:
                    under = sc.short_map[s]
                    if self.held(under):  # 같은 기초자산 롱 보유 중이면 숏 금지
                        continue
                    setup = st.entry_short(s, self.analyses[under], self.scores.get(under, 0.0))
                elif s in sc.long_symbols:
                    inv = [k for k, v in sc.short_map.items() if v == s]
                    if any(self.held(k) for k in inv):
                        continue
                    setup = st.entry_long(self.analyses[s], self.scores.get(s, 0.0))
                else:
                    continue
                if setup.ok:
                    cands.append(setup)
                elif len(setup.checks) == 4 and setup.checks[0][1] and setup.passed - 1 >= max(1, setup.min_other - 1):
                    watch.append(f"{s}({setup.passed}/4)")
            if watch or cands:
                self.log("스캔", by=jn, to=sn, watch=watch, candidates=[c.instrument for c in cands])
            for setup in cands:
                s = setup.instrument
                notional = eq * st.size_pct(s) / 100
                if s in no_trade:
                    self.log("반려", by=sn, junior=jn, review={"approved": False, "comment": "팀장 지시: 오늘 피할 종목"})
                    continue
                if self.group_exposure(sn) + notional > eq * cap / 100:
                    self.log("반려", by=sn, junior=jn, review={"approved": False, "comment": f"데스크 한도 {cap:.0f}% 초과"})
                    continue
                thesis = " · ".join(f"{n}: {d}" for n, _, d in setup.checks)
                self.log("선보고", by=jn, to=sn, proposal={"symbol": s, "side": "buy", "kind": "숏(인버스)" if setup.side == "short" else "롱",
                                                         "notional": round(notional, 2), "thesis": thesis},
                         setup=setup.as_dict())
                rv = senior.review_setup(jn, setup.as_dict(), self.directive, {"scores": {setup.signal: self.scores.get(setup.signal, 0)}})
                self.log("승인" if rv.get("approved") else "반려", by=sn, junior=jn, review=rv)
                if rv.get("approved"):
                    self.execute(jn, s, "buy", notional, thesis,
                                 meta={"side": setup.side, "signal": setup.signal, "entry_price": px[s],
                                       "key_level": setup.key_level, "adds": 0, "desk": sn})

    # ---------- 시각 ----------
    def _wait_until(self, t: datetime):
        if self.cfg.realtime:
            while (left := (t - datetime.now(cal.ET)).total_seconds()) > 0:
                if getattr(self.market, "live", False):      # 현재가를 실시간으로 받는 시세원이면 대기 중에도 화면·손절을 자주 점검
                    time.sleep(min(left, 3))
                    self._live_tick()
                else:
                    time.sleep(min(left, 30))
            self.clock = max(t, datetime.now(cal.ET))
        else:
            self.clock = t

    def _live_tick(self):
        """대기 중 약 3초마다: 화면에 현재가·평가금액을 보내고, 보유 종목 손절선만 점검한다(판단·신규 매수는 하지 않음)."""
        try:
            now = datetime.now(cal.ET)
            if self.clock is None or now > self.clock:        # 화면 시계가 실제 시각을 따라가도록
                self.clock = now
            if self._start_eq is not None and not self.risk.halted and self.paper.positions:
                self.check_risk_state(self._start_eq)
            self.emit("실시간시세", **self.snapshot())
        except Exception as e:
            print(f"  [실시간 점검 오류] {e}")

    # ---------- 거래일 ----------
    def run_day(self, d: date | None = None):
        d = d or cal.trading_day_on_or_after(cal.today_et())
        if not cal.is_trading_day(d):
            raise ValueError(f"{d}는 나스닥 휴장일입니다")
        r = self.cfg.risk
        self.paper.today = d.isoformat()
        if getattr(self.market, "day", None) != d:
            self.market.new_day(d)
        self.risk.new_day()
        open_at, close_at = cal.session(d)
        week_last = r.weekly_flatten and cal.is_week_last_session(d)
        no_entry_at = close_at - timedelta(minutes=r.no_entry_before_close_min)
        flat_at = close_at - timedelta(minutes=r.flatten_before_close_min) if week_last else None
        bar_ends = set(bar_starts(d, getattr(self.market, "bar_min", 60))[1:]) | {close_at}   # 기본 봉 마감 시각(키움 모드 15분)
        self.clock = open_at

        start_eq = self.equity()
        self._start_eq = start_eq
        fees_start = self.paper.fees_paid
        self.emit("시세", **self.snapshot())
        self.log("장시작", date=d.isoformat(), weekday=WEEKDAY_KO[d.weekday()],
                 close_et=close_at.strftime("%H:%M"), week_last_session=week_last,
                 equity=round(start_eq, 2), carried=self.positions_view(), mode=self.cfg.mode,
                 target_usd=round(start_eq * r.daily_target_pct / 100, 2))
        if week_last:
            self.log("공지", message=f"이번 주 마지막 거래일 — {no_entry_at:%H:%M} ET부터 신규 매수 금지, "
                                   f"{flat_at:%H:%M} ET 전량 청산" + (" (조기 폐장일)" if close_at.hour < 16 else ""))

        stale = [s for s, p in self.paper.positions.items()
                 if p.qty and p.opened and not cal.same_week(date.fromisoformat(p.opened), d)]
        if stale:
            self.log("리스크", message=f"지난주 포지션 발견 → 즉시 정리: {stale}")
            self.flatten("weekly_rule_overdue", only=stale, why="주간 청산 누락분")

        # 아침 회의
        self.refresh_news()
        self.refresh_analyses()
        reports = {}
        for n, s in self.seniors.items():
            rep = s.market_report({x: self.analyses[x].brief() for x in s.symbols if x in self.analyses},
                                  {x: self.scores.get(x, 0) for x in s.symbols})
            reports[n] = rep
            self.log("시황보고", by=n, report=rep)
        mkt_news = min(self.scores.get("QLD", 0), self.scores.get("SOXL", 0))
        self.directive = self.lead.daily_strategy(reports, start_eq, {
            "date": d.isoformat(), "weekday": WEEKDAY_KO[d.weekday()], "week_last_session": week_last,
            "carried_positions": self.positions_view(), "market_news": mkt_news})
        self.log("전략지시", by="team_lead", directive=self.directive)

        # 5분 단위 진행
        t = open_at
        rnd = 0
        flattened = False
        while t < close_at:
            t = t + timedelta(minutes=STEP_MIN)
            if self.cfg.realtime and datetime.now(cal.ET) >= close_at:
                self.log("공지", message="이미 장 마감 이후라 남은 시간을 건너뜀")
                break
            self._wait_until(t)
            self.market.advance_to(t)
            if week_last and t >= no_entry_at and not self.risk.entry_blocked:
                self.risk.entry_blocked = "주 마지막 거래일 마감 전 — 신규 매수 금지"
                self.log("공지", message=f"{t:%H:%M} ET — 신규 매수 금지 시작")
            if week_last and not flattened and t >= flat_at:
                flattened = True
                self.risk.entry_blocked = "주간 청산 완료 — 신규 매수 금지"
                has = any(p.qty for p in self.paper.positions.values())
                self.log("주간청산", message=f"{flat_at:%H:%M} ET — 주말 전 전 포지션 청산" if has else "보유 포지션 없음")
                self.flatten("weekly_close", why="주말 전 청산")
            if not self.risk.halted:
                self.check_risk_state(start_eq)
            if t in bar_ends and not self.risk.halted and t < close_at:
                rnd += 1
                self.emit("시세", **self.snapshot())
                self.log("라운드", n=rnd, equity=round(self.equity(), 2))
                self.decide(week_last)

        # 마감
        self._start_eq = None
        self.clock = close_at
        end_eq = self.equity()
        pnl = end_eq - start_eq
        self.last_session = d.isoformat()
        self._save_state()
        self.emit("시세", **self.snapshot())
        self.log("장마감", date=d.isoformat(), equity=round(end_eq, 2), pnl_usd=round(pnl, 2),
                 pnl_pct=round(pnl / start_eq * 100, 3),
                 fees_usd=round(self.paper.fees_paid - fees_start, 2), target_hit=self.risk.target_hit,
                 halted=self.risk.halted, orders=self.risk.orders_today, carry_over=self.positions_view())
        return pnl

    def run_days(self, start: date, n: int):
        d, total = cal.trading_day_on_or_after(start), 0.0
        for _ in range(n):
            total += self.run_day(d)
            d = cal.next_trading_day(d)
        print(f"\n{n}거래일 누적 손익(수수료 차감 후): {total:+.2f} USD · 누적 수수료 {self.paper.fees_paid:.2f} USD"
              f" · 장부: {self.log_path} · 상태: {self.state_path}")
        return total

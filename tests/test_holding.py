"""보유기간·주간 청산 테스트: python -m unittest discover tests"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date

from trading_office import calendar_us as cal
from trading_office.config import OfficeConfig
from trading_office.market import SampleMarket
from trading_office.office import Office


class TestCalendar(unittest.TestCase):
    def test_normal_friday_is_week_last(self):
        self.assertTrue(cal.is_week_last_session(date(2026, 11, 20)))
        self.assertFalse(cal.is_week_last_session(date(2026, 11, 19)))

    def test_black_friday_early_close(self):
        d = date(2026, 11, 27)
        self.assertTrue(cal.is_week_last_session(d))
        self.assertEqual(cal.session(d)[1].hour, 13)

    def test_friday_holiday_makes_thursday_last(self):
        for thu in (date(2026, 4, 2), date(2026, 6, 18), date(2026, 7, 2), date(2026, 12, 24)):
            self.assertFalse(cal.is_trading_day(date.fromordinal(thu.toordinal() + 1)))
            self.assertTrue(cal.is_week_last_session(thu), thu)

    def test_thanksgiving_skipped(self):
        self.assertEqual(cal.next_trading_day(date(2026, 11, 25)), date(2026, 11, 27))

    def test_dst_handled(self):  # 서머타임 여부와 무관하게 ET 16:00
        self.assertEqual(cal.session(date(2026, 7, 1))[1].strftime("%H:%M %Z"), "16:00 EDT")
        self.assertEqual(cal.session(date(2026, 12, 1))[1].strftime("%H:%M %Z"), "16:00 EST")


def office(tmp, start, seed=1, reset=False):
    cfg = OfficeConfig(data_dir=tmp)
    cfg.risk.stop_loss_pct = 50  # 안전망 손절이 테스트를 방해하지 않게
    o = Office(cfg, SampleMarket(seed, start=start), reset_state=reset)
    o.quiet = True
    o.strategy.exit_reason = lambda *a, **k: (None, "")   # 전략 청산 끔 → 보유/주간 규칙만 시험
    o.strategy.entry_long = lambda a, n: _never(a.symbol, "long")
    o.strategy.entry_short = lambda i, u, n: _never(i, "short")
    return o


def _never(sym, side):
    from trading_office.strategy import Setup
    return Setup(sym, side, sym, [("테스트", False, "")])


def hold(o, d, sym="AAPL"):
    """d 날짜 장 시작 직후 포지션 하나를 강제로 만든다."""
    o.market.new_day(d)
    o.paper.today = d.isoformat()
    px = o.market.quote(sym).price
    o.paper.submit(sym, "buy", 1, px, "test")
    o.paper.positions[sym].meta = {"side": "long", "signal": sym, "entry_price": px, "adds": 0}


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


class TestHolding(unittest.TestCase):
    def test_positions_carry_over_midweek(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = date(2026, 11, 16)  # 월요일
            o = office(tmp, d, reset=True)
            hold(o, d)
            quiet(o.run_day, d)
            self.assertTrue(o.held("AAPL"), "월요일 포지션은 다음 날로 이월돼야 함")
            o2 = office(tmp, d)  # 새 프로세스로 다시 열어도 복원
            self.assertTrue(o2.held("AAPL"))
            self.assertEqual(o2.paper.positions["AAPL"].meta.get("side"), "long")

    def test_week_last_session_ends_flat(self):
        for d in (date(2026, 11, 20), date(2026, 11, 27), date(2026, 4, 2)):  # 금, 블랙프라이데이, 성금요일 전 목
            with tempfile.TemporaryDirectory() as tmp:
                o = office(tmp, d, reset=True)
                hold(o, d)
                quiet(o.run_day, d)
                self.assertFalse(any(p.qty > 1e-9 for p in o.paper.positions.values()), d)
                state = json.load(open(f"{tmp}/state.json"))
                self.assertEqual(state["positions"], {})

    def test_no_buys_after_cutoff_on_friday(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = date(2026, 11, 20)
            o = office(tmp, d, reset=True)
            hold(o, d)
            quiet(o.run_day, d)
            self.assertIn("신규 매수 금지", o.risk.entry_blocked)
            v = o.risk.check("AAPL", "buy", 100, 100, 0, 0, 10_000)
            self.assertFalse(v.ok)

    def test_stale_positions_from_last_week_closed_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            thu = date(2026, 11, 19)
            o = office(tmp, thu, reset=True)
            hold(o, thu)
            quiet(o.run_day, thu)  # 금요일 실행 누락
            self.assertTrue(o.held("AAPL"))
            o2 = office(tmp, date(2026, 11, 23))
            quiet(o2.run_day, date(2026, 11, 23))  # 다음 주 월요일
            lines = [json.loads(l) for l in open(o2.log_path, encoding="utf-8")]
            overdue = [l for l in lines if l["event"] == "청산" and l["by"] == "weekly_rule_overdue"]
            self.assertTrue(overdue)


if __name__ == "__main__":
    unittest.main()


class TestFees(unittest.TestCase):
    def test_round_trip_costs_half_percent(self):
        from trading_office.broker import PaperBroker
        b = PaperBroker(cash=10_000)            # 기본 수수료 0.25%
        b.submit("QQQ", "buy", 10, 500, "t")     # 5,000 + 12.5
        b.submit("QQQ", "sell", 10, 500, "t")    # 5,000 - 12.5
        self.assertAlmostEqual(b.cash, 10_000 - 25.0)
        self.assertAlmostEqual(b.fees_paid, 25.0)

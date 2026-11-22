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


def office(tmp, seed=1, reset=False):
    cfg = OfficeConfig(data_dir=tmp, intraday_rounds=4)
    return Office(cfg, SampleMarket(seed), reset_state=reset)


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


class TestHolding(unittest.TestCase):
    def test_positions_carry_over_midweek(self):
        with tempfile.TemporaryDirectory() as tmp:
            o = office(tmp, reset=True)
            quiet(o.run_day, date(2026, 11, 16))  # 월요일
            held = {s for s, p in o.paper.positions.items() if p.qty}
            self.assertTrue(held, "모의 에이전트는 매수하므로 이월 포지션이 있어야 함")
            # 새 프로세스로 다시 열어도 포지션이 복원되는지
            o2 = office(tmp)
            self.assertEqual({s for s, p in o2.paper.positions.items() if p.qty}, held)

    def test_week_last_session_ends_flat(self):
        for start, n in ((date(2026, 11, 16), 5), (date(2026, 11, 23), 4), (date(2026, 3, 30), 4)):
            with tempfile.TemporaryDirectory() as tmp:
                o = office(tmp, reset=True)
                quiet(o.run_days, start, n)
                self.assertFalse(any(p.qty > 1e-9 for p in o.paper.positions.values()), start)
                state = json.load(open(f"{tmp}/state.json"))
                self.assertEqual(state["positions"], {})

    def test_no_buys_after_cutoff_on_friday(self):
        with tempfile.TemporaryDirectory() as tmp:
            o = office(tmp, reset=True)
            quiet(o.run_day, date(2026, 11, 20))
            lines = [json.loads(l) for l in open(o.log_path, encoding="utf-8")]
            buys = [l for l in lines if l["event"] == "체결" and l["side"] == "buy"]
            self.assertTrue(all(l["t"] < "11-20 15:30 ET" for l in buys))

    def test_stale_positions_from_last_week_closed_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            o = office(tmp, reset=True)
            quiet(o.run_day, date(2026, 11, 19))  # 목요일까지만 실행 (금요일 실행 누락)
            self.assertTrue(any(p.qty for p in o.paper.positions.values()))
            o2 = office(tmp)
            quiet(o2.run_day, date(2026, 11, 23))  # 다음 주 월요일
            lines = [json.loads(l) for l in open(o2.log_path, encoding="utf-8")]
            overdue = [l for l in lines if l["event"] == "청산" and l["by"] == "weekly_rule_overdue"]
            self.assertTrue(overdue)
            self.assertTrue(all(l["opened"] <= "2026-11-19" for l in overdue))


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

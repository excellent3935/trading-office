"""사용자 매매 규칙 테스트: python -m unittest discover tests"""
import unittest

from trading_office import indicators as ind
from trading_office.config import StrategyConfig
from trading_office.indicators import Bar
from trading_office.strategy import Analysis, Strategy


def A(sym="AAPL", **kw):
    base = dict(enough=True, price=100.0, last_close=100.0, rsi1h=45, rsi1h_prev=44, rsi4h=45, rsi_min=45,
                rsi_max=55, up_votes=2, down_votes=2, candle_bull=None, candle_bear=None, mas={}, vp=[],
                support=99.5, resistance=103.0)
    base.update(kw)
    return Analysis(sym, **base)


LONG_OK = dict(rsi_min=28, up_votes=4, support=99.5, candle_bull="망치형")
SHORT_OK = dict(rsi_max=74, down_votes=4, resistance=100.5, candle_bear="유성형")


class TestIndicators(unittest.TestCase):
    def test_rsi_extremes(self):
        up = [100 + i for i in range(40)]
        self.assertAlmostEqual(ind.rsi_series(up)[-1], 100)
        dn = [100 - i * .5 for i in range(40)]
        self.assertLess(ind.rsi_series(dn)[-1], 1)

    def test_sma_hma(self):
        xs = list(range(1, 301))
        self.assertEqual(ind.sma_series(xs, 20)[-1], sum(range(281, 301)) / 20)
        # 직선에서 HMA200 지연 ≈ 4봉, SMA200 지연 = 99.5봉 → HMA가 훨씬 빠르게 따라감
        self.assertAlmostEqual(ind.hma_series(xs, 200)[-1], 296, delta=0.5)
        self.assertAlmostEqual(ind.sma_series(xs, 200)[-1], 200.5)
        self.assertGreater(ind.slope_pct(ind.sma_series(xs, 20)), 0)

    def test_4h_groups_two_per_day(self):
        bars = [Bar(f"2026-11-2{d} {h}", 1, 1, 1, 1, 1) for d in (3, 4) for h in
                ("09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30")]
        self.assertEqual(len(ind.to_4h(bars)), 4)

    def test_candles(self):
        down = [Bar("t", 105, 106, 101, 102, 1), Bar("t", 102, 102.5, 99, 99.5, 1)]
        engulf = down + [Bar("t", 99.3, 103.5, 99.0, 103.2, 1)]
        self.assertEqual(ind.candle_reversal(engulf, "bull"), "상승 장악형")
        hammer = down + [Bar("t", 99.4, 99.7, 96.5, 99.6, 1)]
        self.assertEqual(ind.candle_reversal(hammer, "bull"), "망치형")
        up = [Bar("t", 95, 99, 94, 98, 1), Bar("t", 98, 101.5, 97.5, 101, 1)]
        star = up + [Bar("t", 101.2, 104.5, 100.9, 101.0, 1)]
        self.assertEqual(ind.candle_reversal(star, "bear"), "유성형")

    def test_volume_profile_finds_heavy_level(self):
        bars = [Bar("t", 100, 100.5, 99.5, 100, 1e6 if 40 <= i < 60 else 1e4) for i in range(100)]
        bars += [Bar("t", 110, 110.5, 109.5, 110, 1e4) for _ in range(30)]
        lv = ind.volume_profile_levels(bars, top=1)
        self.assertAlmostEqual(lv[0], 100, delta=1.0)


class TestEntries(unittest.TestCase):
    def setUp(self):
        self.s = Strategy(StrategyConfig(), fee_pct=0.25)

    def test_long_needs_all_four(self):
        self.assertTrue(self.s.entry_long(A(**LONG_OK), 0.0).ok)
        for k, bad in (("rsi_min", 40), ("up_votes", 2), ("support", 95.0), ("candle_bull", None)):
            self.assertFalse(self.s.entry_long(A(**{**LONG_OK, k: bad}), 0.0).ok, k)
        self.assertFalse(self.s.entry_long(A(**LONG_OK), -0.5).ok, "악재면 진입 보류")

    def test_divergence_substitutes_for_oversold(self):
        self.assertTrue(self.s.entry_long(A(**{**LONG_OK, "rsi_min": 38, "div_bull": True}), 0.0).ok)

    def test_short_only_via_inverse_on_underlying_signal(self):
        st = self.s.entry_short("SOXS", A("SOXL", **SHORT_OK), 0.0)
        self.assertTrue(st.ok)
        self.assertEqual((st.instrument, st.signal, st.side), ("SOXS", "SOXL", "short"))
        self.assertFalse(self.s.entry_short("QID", A("QLD", **{**SHORT_OK, "rsi_max": 60}), 0.0).ok)
        self.assertFalse(self.s.entry_short("QID", A("QLD", **SHORT_OK), 0.5).ok, "호재면 숏 보류")

    def test_thin_data_blocks_entry(self):
        st = self.s.entry_long(Analysis("ANT", False, "데이터 부족(39/221봉)"), 0.0)
        self.assertFalse(st.ok)

    def test_leverage_scaling(self):
        self.assertAlmostEqual(self.s.size_pct("AAPL"), 5.0)
        self.assertAlmostEqual(self.s.size_pct("SOXL"), 5.0 / 3)
        self.assertAlmostEqual(self.s.hard_stop_pct("QID"), 6.0)


class TestAdds(unittest.TestCase):
    def setUp(self):
        self.s = Strategy(StrategyConfig(), fee_pct=0.25)
        self.meta = {"side": "long", "entry_price": 100.0, "adds": 0}

    def test_add_at_volume_level_with_reversal(self):
        a = A(price=97.0, vp=[96.9, 105], candle_bull="상승 장악형", rsi1h=33, rsi1h_prev=29)
        ok, why, lv = self.s.add_check(self.meta, a)
        self.assertTrue(ok, why)
        self.assertAlmostEqual(lv, 96.9)

    def test_no_add_without_level_or_signal(self):
        self.assertFalse(self.s.add_check(self.meta, A(price=97.0, vp=[94.0], candle_bull="망치형", rsi1h=33, rsi1h_prev=29))[0])
        self.assertFalse(self.s.add_check(self.meta, A(price=97.0, vp=[96.9], candle_bull=None))[0])
        self.assertFalse(self.s.add_check(self.meta, A(price=99.5, vp=[99.5], candle_bull="망치형", rsi1h=33, rsi1h_prev=29))[0],
                         "하락 폭 부족")

    def test_only_one_add_and_never_for_shorts(self):
        a = A(price=97.0, vp=[96.9], candle_bull="망치형", rsi1h=33, rsi1h_prev=29)
        self.assertFalse(self.s.add_check({**self.meta, "adds": 1}, a)[0])
        self.assertFalse(self.s.add_check({**self.meta, "side": "short"}, a)[0])


class TestExits(unittest.TestCase):
    def setUp(self):
        self.s = Strategy(StrategyConfig(), fee_pct=0.25)

    def test_stop_after_add_on_support_break(self):
        meta = {"side": "long", "adds": 1, "key_level": 96.9}
        r, kind = self.s.exit_reason(meta, 96.0, 98.5, A(price=96.0, last_close=96.0), 0.0)
        self.assertEqual(kind, "stop")
        r, kind = self.s.exit_reason({**meta, "adds": 0}, 96.0, 98.5, A(price=96.0, last_close=96.0), 0.0)
        self.assertIsNone(r, "추매 전에는 구조적 손절 없음(안전망 손절만)")

    def test_news_exit(self):
        r, kind = self.s.exit_reason({"side": "long"}, 100, 100, A(), -0.8)
        self.assertEqual(kind, "news")
        r, kind = self.s.exit_reason({"side": "short", "key_level": 105}, 10, 10, A("QLD"), 0.8)
        self.assertEqual(kind, "news")

    def test_take_profit_only_above_fees(self):
        hot = A(price=101.0, rsi1h=72)
        self.assertIsNone(self.s.exit_reason({"side": "long"}, 100.4, 100.25, hot, 0)[0], "수수료 못 넘으면 보유")
        r, kind = self.s.exit_reason({"side": "long"}, 101.0, 100.25, hot, 0)
        self.assertEqual(kind, "tp")

    def test_take_profit_at_resistance_or_sma(self):
        r, k = self.s.exit_reason({"side": "long"}, 101.0, 100.0, A(price=101.0, resistance=101.2), 0)
        self.assertIn("저항", r)
        r, k = self.s.exit_reason({"side": "long"}, 101.0, 100.0, A(price=101.0, resistance=110, mas={"SMA100": 101.2}), 0)
        self.assertIn("SMA100", r)

    def test_short_stop_and_tp_use_underlying(self):
        meta = {"side": "short", "key_level": 100.5}
        r, k = self.s.exit_reason(meta, 9.5, 10.0, A("SOXL", price=101.2, last_close=101.2), 0)
        self.assertEqual(k, "stop")
        r, k = self.s.exit_reason(meta, 10.2, 10.0, A("SOXL", price=97, last_close=97, rsi1h=28, support=90), 0)
        self.assertEqual(k, "tp")


if __name__ == "__main__":
    unittest.main()

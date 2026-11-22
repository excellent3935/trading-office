"""리스크 한도 테스트: python -m unittest discover tests"""
import unittest
from trading_office.config import RiskLimits
from trading_office.risk import RiskManager


class TestRisk(unittest.TestCase):
    def setUp(self):
        self.r = RiskManager(RiskLimits(), 10_000, {"QQQ", "NVDA"})

    def test_unknown_symbol_rejected(self):
        self.assertFalse(self.r.check("GME", "buy", 100, 10, 0, 0, 10_000).ok)

    def test_order_capped_to_max_order(self):
        v = self.r.check("QQQ", "buy", 5_000, 500, 0, 0, 10_000)
        self.assertTrue(v.ok)
        self.assertAlmostEqual(v.qty * 500, 500)  # 5% of 10,000

    def test_position_limit(self):
        self.assertFalse(self.r.check("QQQ", "buy", 100, 500, 1_000, 1_000, 10_000).ok)

    def test_target_blocks_new_buys_but_allows_sells(self):
        self.r.update_state(10_060, 10_000)  # +0.6%
        self.assertTrue(self.r.target_hit)
        self.assertFalse(self.r.check("QQQ", "buy", 100, 500, 0, 0, 10_060).ok)
        self.assertTrue(self.r.check("QQQ", "sell", 100, 500, 300, 300, 10_060).ok)

    def test_loss_limit_halts(self):
        self.r.update_state(9_890, 10_000)  # -1.1%
        self.assertTrue(self.r.halted)
        self.assertFalse(self.r.check("QQQ", "sell", 100, 500, 300, 300, 9_890).ok)

    def test_no_short_selling(self):
        self.assertFalse(self.r.check("QQQ", "sell", 100, 500, 0, 0, 10_000).ok)


if __name__ == "__main__":
    unittest.main()

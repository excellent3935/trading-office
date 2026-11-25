import json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from trading_office import macro


def mk(d, **kw):
    now = datetime(2026, 11, 26, 3, 0, tzinfo=timezone(timedelta(hours=9)))
    base = {"asof": now.isoformat(), "symbols": {}, "macro": {}}
    base.update(kw)
    p = Path(tempfile.mkdtemp()) / "n.json"
    p.write_text(json.dumps(base, ensure_ascii=False))
    return macro.load(p, {"NVDA", "XOM", "JNJ"}, now + timedelta(hours=d))


class T(unittest.TestCase):
    def test_missing_stale_and_bad(self):
        self.assertEqual(macro.load(Path("/nonexistent/x.json"))[1], "파일 없음")
        self.assertTrue(mk(13)[1].startswith("오래됨"))                       # 기본 12시간
        p = Path(tempfile.mkdtemp()) / "n.json"; p.write_text("{깨짐")
        self.assertTrue(macro.load(p)[1].startswith("읽기 실패"))
        p.write_text(json.dumps({"asof": "2026-11-26T03:00:00"}))
        self.assertIn("시간대", macro.load(p)[1])

    def test_adjustment_rules(self):
        d, _ = mk(1, macro={"war": {"score": -0.6, "energy": 0.4}, "oil": {"score": -0.4, "energy": 0.6},
                            "rates": {"score": -0.2}})
        self.assertAlmostEqual(macro.adjustment("NVDA", d), -0.6)              # (−1.2)×0.5 → 하한 −0.6
        self.assertAlmostEqual(macro.adjustment("JNJ", d), -0.3)               # 방어주는 절반
        self.assertAlmostEqual(macro.adjustment("XOM", d), 0.3)                # 에너지: (0.4+0.6+(-0.2))×0.5=0.4 → 상한 0.3
        g, _ = mk(1, macro={"bonds": {"score": 1.0}, "indicators": {"score": 1.0}})
        self.assertAlmostEqual(macro.adjustment("NVDA", g), 0.3)               # 호재는 상한 +0.3

    def test_sanitizing(self):
        d, _ = mk(1, symbols={"NVDA": {"score": 99, "why": "x" * 500}, "ZZZ": {"score": 1}},
                  macro={"war": {"score": "abc"}, "oil": "무시", "이상한": {"score": 1}}, entry_halt=1)
        self.assertEqual(d["symbols"]["NVDA"]["score"], 1.0)                   # 범위 제한
        self.assertEqual(len(d["symbols"]["NVDA"]["why"]), 120)                # 길이 제한
        self.assertNotIn("ZZZ", d["symbols"])                                  # 허용 종목만
        self.assertEqual(list(d["macro"]), ["war"])                            # 알려진 요인만, 숫자 아니면 0
        self.assertEqual(d["macro"]["war"]["score"], 0.0)
        self.assertTrue(d["halt"])


if __name__ == "__main__":
    unittest.main()

"""외부 뉴스·거시 반영 파일(news_override.json) 읽기.

세이브티커 같은 사이트의 뉴스를 사람이(또는 Claude가) 읽고 정리한 결과를 오피스가 읽어 들인다.
오피스는 사이트에 접속하지 않고, 이 파일만 읽는다. 파일은 내 PC 안에서만 쓴다(공개 페이지로 내보내지 않음).

형식(모든 점수는 −1 악재 ~ +1 호재):
{
  "asof": "2026-11-26T02:40:00+09:00",       # 정리한 시각(필수). ttl_hours 지나면 무시
  "ttl_hours": 12,
  "symbols": {"NVDA": {"score": 0.4, "why": "CEO 복귀 확정"}},     # 종목별 추가 점수
  "macro": {                                  # 전쟁·유가·금리·경제지표·채권
    "war":        {"score": -0.2, "energy": 0.3, "why": "이란 협상"},
    "oil":        {"score": -0.1, "energy": 0.4, "why": ""},
    "rates":      {"score": -0.2, "why": ""},
    "indicators": {"score":  0.1, "why": ""},
    "bonds":      {"score": -0.1, "why": ""}
  },
  "entry_halt": false, "halt_why": ""         # true 면 신규 매수 금지(CPI·FOMC 같은 이벤트 전후)
}
적용: 최종 뉴스 점수 = 기본(야후 뉴스) + 종목 점수 + 거시 보정 (−1~+1로 제한)
거시 보정 = 5개 요인 점수 합 × 0.5, 범위 −0.6 ~ +0.3 (호재가 악재를 덮지 못하게 비대칭).
  에너지주(CVX·XOM)는 요인의 'energy' 값을 쓰고(없으면 score), 헬스케어 방어주는 보정을 절반만 받는다.
파일 내용은 외부 텍스트이므로 점수만 숫자로 읽고, why 는 화면 표시용 글자로만 쓴다(명령으로 해석하지 않음).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FACTORS = ("war", "oil", "rates", "indicators", "bonds")
FACTOR_KO = {"war": "전쟁·지정학", "oil": "유가", "rates": "금리", "indicators": "경제지표", "bonds": "채권"}
ENERGY = {"CVX", "XOM"}
DEFENSIVE = {"LLY", "UNH", "ABBV", "JNJ"}
MACRO_MIN, MACRO_MAX, MACRO_W = -0.6, 0.3, 0.5


def _num(x, lo=-1.0, hi=1.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, v)) if v == v else 0.0


def _txt(x, n=120) -> str:
    return str(x or "").replace("\n", " ")[:n]


def load(path: Path, allowed: set[str] | None = None, now: datetime | None = None) -> tuple[dict | None, str]:
    """(정리된 데이터, 상태 문구). 파일이 없거나 오래됐거나 깨졌으면 (None, 이유)."""
    now = now or datetime.now(timezone.utc)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "파일 없음"
    except (OSError, ValueError) as e:
        return None, f"읽기 실패({type(e).__name__})"
    if not isinstance(raw, dict):
        return None, "형식 오류"
    try:
        asof = datetime.fromisoformat(str(raw["asof"]))
        if asof.tzinfo is None:
            return None, "asof 에 시간대(+09:00 등)가 없음"
    except (KeyError, ValueError):
        return None, "asof 없음/형식 오류"
    ttl = _num(raw.get("ttl_hours", 12), 0.5, 48)
    age_h = (now - asof).total_seconds() / 3600
    if age_h > ttl:
        return None, f"오래됨({age_h:.1f}시간 전, 유효 {ttl:g}시간)"
    syms = {}
    for s, v in (raw.get("symbols") or {}).items():
        if isinstance(v, dict) and (allowed is None or s in allowed):
            syms[str(s)] = {"score": _num(v.get("score")), "why": _txt(v.get("why"))}
    macro = {}
    for f in FACTORS:
        v = (raw.get("macro") or {}).get(f)
        if isinstance(v, dict):
            macro[f] = {"score": _num(v.get("score")), "energy": _num(v["energy"]) if "energy" in v else None,
                        "why": _txt(v.get("why"))}
    return {"asof": asof.isoformat(), "age_h": round(age_h, 1), "symbols": syms, "macro": macro,
            "halt": bool(raw.get("entry_halt")), "halt_why": _txt(raw.get("halt_why"))}, "적용 중"


def adjustment(symbol: str, data: dict | None) -> float:
    """거시 5요인이 이 종목 뉴스 점수에 더하는 값."""
    if not data or not data["macro"]:
        return 0.0
    if symbol in ENERGY:
        tot = sum((m["energy"] if m["energy"] is not None else m["score"]) for m in data["macro"].values())
        mult = 1.0
    else:
        tot = sum(m["score"] for m in data["macro"].values())
        mult = 0.5 if symbol in DEFENSIVE else 1.0
    return max(MACRO_MIN, min(MACRO_MAX, tot * MACRO_W)) * mult


def summary(data: dict) -> str:
    parts = [f"{FACTOR_KO[f]} {m['score']:+.1f}" for f, m in data["macro"].items()]
    return ", ".join(parts) or "거시 항목 없음"

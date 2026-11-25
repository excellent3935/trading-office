"""LLM 백엔드.

ClaudeCLI: 내 PC의 Claude Code(`claude -p`)를 '생각만 하는' 용도로 호출한다.
  - --tools ""             : 도구 0개. 파일 읽기/쓰기, Bash, 웹 접근 전부 불가
  - --permission-prompts none : 혹시 권한 요청이 생겨도 자동 거부
  - 최소 환경변수만 전달, 빈 임시 폴더에서 실행
  → 에이전트는 JSON 텍스트만 돌려줄 수 있고, 실제 주문은 오직 파이썬 코드(risk→broker)가 한다.

MockLLM: 오프라인 테스트용 규칙 기반 응답. 설치/로그인 없이 전체 흐름을 돌려볼 수 있다.
"""
import json
import os
import re
import subprocess
import tempfile


class LLM:
    def ask(self, role: str, system: str, user: str) -> str: ...


class ClaudeCLI(LLM):
    def __init__(self, model: str = "sonnet", timeout: int = 180):
        self.model = model
        self.timeout = timeout

    def ask(self, role, system, user):
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "USER") if k in os.environ}
        cmd = ["claude", "-p", user,
               "--system-prompt", system,
               "--model", self.model,
               "--tools", "",
               "--permission-prompts", "none",
               "--output-format", "json"]
        with tempfile.TemporaryDirectory() as cwd:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                               text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"[{role}] claude 실행 실패: {r.stderr[:300]}")
        return json.loads(r.stdout).get("result", "")


class MockLLM(LLM):
    """역할별로 그럴듯한 JSON을 돌려주는 가짜 LLM. 매매 판단 품질은 의미 없음."""

    def ask(self, role, system, user):
        data = extract_json(user) or {}
        if role.startswith("senior") and "market_report" in system:
            quotes = data.get("quotes", [])
            up = [q["symbol"] for q in quotes if q["change_pct"] > 0]
            return json.dumps({"summary": f"상승 {len(up)}/{len(quotes)}종목",
                               "bias": "bullish" if len(up) * 2 >= len(quotes) else "bearish",
                               "watchlist": up[:2]}, ensure_ascii=False)
        if role == "team_lead":
            reports = data.get("reports", {})
            alloc = {k: (30 if v.get("bias") == "bullish" else 10) for k, v in reports.items()}
            return json.dumps({"stance": "선별적 롱, 목표 도달 시 즉시 정리",
                               "allocation_pct": alloc,
                               "instructions": {k: "워치리스트 중심 분할 진입, 손절 엄수" for k in reports}},
                              ensure_ascii=False)
        if role.startswith("junior"):
            q = max(data.get("quotes", [{}]), key=lambda x: x.get("change_pct", -99))
            return json.dumps({"symbol": q.get("symbol"), "side": "buy", "notional": 300,
                               "thesis": f"당일 강세 {q.get('change_pct')}%, 모멘텀 추종"},
                              ensure_ascii=False)
        if "junior_review" in system:
            p = data.get("proposal", {})
            ok = p.get("symbol") in data.get("watchlist", [])
            return json.dumps({"approved": ok, "notional": p.get("notional", 0) if ok else 0,
                               "comment": "워치리스트 일치, 승인" if ok else "워치리스트 밖, 반려"},
                              ensure_ascii=False)
        if "trade_plan" in system:
            wl = data.get("watchlist", [])
            budget = data.get("budget_usd", 0)
            px = {q["symbol"]: q["price"] for q in data.get("quotes", [])}
            # 보유 종목 중 +1% 이상 수익이면 익절(전량 매도), 아니면 계속 보유
            orders = [{"symbol": s, "side": "sell", "notional": round(p["qty"] * px[s], 2),
                       "reason": f"익절 {(px[s] / p['avg'] - 1) * 100:+.2f}%"}
                      for s, p in data.get("positions", {}).items()
                      if s in px and p.get("avg") and px[s] >= p["avg"] * 1.01]
            orders += [{"symbol": s, "side": "buy", "notional": round(budget / max(len(wl), 1) / 3, 2),
                        "reason": "지시 기반 분할 진입"} for s in wl]
            return json.dumps({"orders": orders}, ensure_ascii=False)
        return "{}"


def extract_json(text: str) -> dict | None:
    """LLM 응답에서 첫 JSON 객체만 추출. 실패하면 None (→ 주문 없음)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None

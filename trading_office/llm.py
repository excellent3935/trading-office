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
    """오프라인 테스트용 규칙 기반 '두뇌'. 판단 품질은 의미 없고, 흐름 확인용이다.
    - news_score: 키워드 사전으로 감성 점수
    - market_report: 분석 요약을 문장으로
    - daily_strategy: 데스크별 20% 한도(시장 뉴스가 나쁘면 롱 데스크 10%)
    - junior_review: 체크리스트가 모두 통과면 승인
    """

    def ask(self, role, system, user):
        from .news import lexicon_score
        data = extract_json(user) or {}
        task = re.search(r"\[task:(\w+)\]", system)
        task = task.group(1) if task else ""
        if task == "news_score":
            hl = data.get("headlines", {})
            scores = {s: round(lexicon_score(h), 2) for s, h in hl.items()}
            bad = [s for s, v in scores.items() if v <= -0.3]
            return json.dumps({"scores": scores, "summary": f"악재 {', '.join(bad)}" if bad else "특이 악재 없음"},
                              ensure_ascii=False)
        if task == "market_report":
            an = data.get("analyses", {})
            ok = {s: a for s, a in an.items() if a.get("enough")}
            up = [s for s, a in ok.items() if a.get("slope_up", 0) >= 3]
            down = [s for s, a in ok.items() if a.get("slope_down", 0) >= 3]
            watch = [s for s, a in ok.items() if (a.get("rsi1h") or 50) <= 35 or (a.get("rsi1h") or 50) >= 65]
            thin = [s for s, a in an.items() if not a.get("enough")]
            bias = "bullish" if len(up) > len(down) else "bearish" if len(down) > len(up) else "neutral"
            summary = f"상승 추세 {len(up)} · 하락 추세 {len(down)}" + (f" · 관찰 {', '.join(watch[:3])}" if watch else "") \
                + (f" · 데이터 부족 {', '.join(thin)}" if thin else "")
            return json.dumps({"summary": summary, "bias": bias, "watch": watch[:3], "risks": ""}, ensure_ascii=False)
        if task == "daily_strategy":
            reports = data.get("reports", {})
            mkt = data.get("risk_state", {}).get("market_news", 0)
            alloc = {k: (10 if mkt <= -0.3 and k != "senior_etf" else 20) for k in reports}
            stance = "악재 경계 — 롱 축소, 숏 관찰" if mkt <= -0.3 else "규칙 신호만 따라 선별 진입"
            return json.dumps({"stance": stance, "allocation_pct": alloc,
                               "instructions": {k: "체크리스트 4개 모두 통과한 건만" for k in reports}, "no_trade": []},
                              ensure_ascii=False)
        if task == "junior_review":
            st = data.get("setup", {})
            ok = all(c.get("ok") for c in st.get("checks", [])) if st.get("checks") else st.get("kind") == "add"
            return json.dumps({"approved": bool(ok), "comment": "체크리스트 전부 통과, 승인" if ok else "조건 미충족, 반려"},
                              ensure_ascii=False)
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

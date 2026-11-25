"""사무실 화면 로컬 서버 — 내 PC의 트레이딩 오피스를 브라우저에서 실시간으로 본다.

  python serve_office.py                         # 오늘 하루, 모의 에이전트 + 가상 시세
  python serve_office.py --days 5 --date 2026-11-23
  python serve_office.py --llm claude --market yahoo --realtime   # 실제 시각에 맞춰 진행
  → 브라우저에서 http://127.0.0.1:8765 열기

보안
  - 127.0.0.1에만 열림(같은 PC에서만 접속). Host 헤더도 검사해 DNS 리바인딩을 막는다.
  - 화면은 읽기 전용이다. 주문 승인(--live)은 지금처럼 터미널에서만 받는다.
  - docs/ 폴더의 정적 파일과 /events(실시간 이벤트), /api/hello 만 제공한다.
  - 표준 라이브러리만 사용(외부 패키지 없음).
"""
import argparse
import json
import queue
import threading
import time
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trading_office import calendar_us as cal
from trading_office.config import OfficeConfig
from trading_office.market import SampleMarket, YahooMarket
from trading_office.news import SampleNews, YahooNews
from trading_office.office import Office

DOCS = Path(__file__).parent / "docs"
HISTORY_MAX = 4000


class Hub:
    """이벤트를 모아 두고 접속한 브라우저마다 흘려보낸다."""

    def __init__(self):
        self.history: list[dict] = []
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.done = False

    def publish(self, rec: dict):
        with self.lock:
            self.history.append(rec)
            del self.history[:-HISTORY_MAX]
            for q in self.subs:
                q.put(rec)

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subs.append(q)
            return q, list(self.history)

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


def make_handler(hub: Hub, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):  # 콘솔을 매매 로그용으로 비워 둠
            pass

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def do_GET(self):
            if self.headers.get("Host", "") not in allowed_hosts:
                self.send_error(403, "Host not allowed")
                return
            if self.path == "/api/hello":
                body = json.dumps({"office": True, "done": hub.done}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/events":
                return self.stream()
            return super().do_GET()

        def do_POST(self):
            self.send_error(405)

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q, past = hub.subscribe()
            try:
                for rec in past:
                    self.wfile.write(f"data: {json.dumps(rec, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        rec = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(rec, ensure_ascii=False)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                hub.unsubscribe(q)

    return partial(Handler, directory=str(DOCS))


def main():
    ap = argparse.ArgumentParser(description="트레이딩 오피스 사무실 화면 서버")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--llm", choices=["mock", "claude"], default="mock")
    ap.add_argument("--market", choices=["sample", "yahoo"], default="sample")
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--live", action="store_true", help="실계좌 모드(주문마다 터미널 승인)")
    ap.add_argument("--start-delay", type=float, default=3.0, help="브라우저를 열 시간을 주고 시작(초)")
    a = ap.parse_args()

    cfg = OfficeConfig(llm=a.llm, realtime=a.realtime,
                       mode="live" if a.live else "paper")
    symbols = sorted({s for v in cfg.team.seniors.values() for s in v["symbols"]})
    start = date.fromisoformat(a.date) if a.date else cal.trading_day_on_or_after(cal.today_et())
    if a.market == "yahoo":
        if not a.realtime:
            ap.error("실시세(yahoo)는 --realtime 과 함께 실행하세요")
        market, news = YahooMarket(symbols), YahooNews()
    else:
        market, news = SampleMarket(start=start), SampleNews()
    office = Office(cfg, market, reset_state=a.reset, news=news)
    hub = Hub()
    office.listeners.append(hub.publish)

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(hub, a.port))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"사무실 화면: http://127.0.0.1:{a.port}  (Ctrl+C로 종료)")

    time.sleep(a.start_delay)
    try:
        office.run_days(start, a.days)
        hub.done = True
        hub.publish({"t": "", "event": "종료", "message": "오늘 운영이 끝났습니다. 화면은 계속 볼 수 있어요."})
        print("운영 종료 — 화면은 계속 열려 있습니다. Ctrl+C로 서버를 끄세요.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()

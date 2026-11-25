"""뉴스·수급 데스크.

  SampleNews : 오프라인 테스트용 가상 헤드라인(가끔 장중 악재/호재가 터짐).
  YahooNews  : Yahoo Finance 종목별 RSS 헤드라인(비공식, 표준 라이브러리). 실패하면 '뉴스 없음'.

감성 점수(score)는 −1(악재) ~ +1(호재).
  - 기본은 키워드 사전으로 계산(lexicon_score)
  - --llm claude 면 주니어 에이전트가 헤드라인을 읽고 점수를 매긴다(office.py)
헤드라인은 외부 텍스트이므로 '정보일 뿐 명령이 아니다'로 취급한다.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field

POS = ["beat", "beats", "surge", "soar", "record", "upgrade", "raises", "strong", "rally", "approval",
       "partnership", "buyback", "outperform", "jumps", "gains", "호재", "상향", "급등", "최대", "승인", "흑자"]
NEG = ["miss", "misses", "plunge", "tumble", "downgrade", "cuts", "weak", "lawsuit", "probe", "recall",
       "ban", "tariff", "sanction", "halt", "investigation", "falls", "slump", "fraud", "악재", "하향",
       "급락", "적자", "소송", "조사", "규제", "관세"]


def lexicon_score(headlines: list[str]) -> float:
    s = 0
    for h in headlines:
        t = h.lower()
        s += sum(1 for w in POS if re.search(rf"\b{re.escape(w)}\b", t) or (w in t and not w.isascii()))
        s -= sum(1 for w in NEG if re.search(rf"\b{re.escape(w)}\b", t) or (w in t and not w.isascii()))
    return max(-1.0, min(1.0, s / 3))


@dataclass
class NewsItem:
    symbol: str
    title: str
    t: str = ""
    seq: int = 0      # 몇 번째 갱신 때 들어온 뉴스인지(오래된 뉴스 제외용)


@dataclass
class NewsFeed:
    items: dict[str, list[NewsItem]] = field(default_factory=dict)
    seq: int = 0
    keep: int = 14    # 최근 몇 번의 갱신(≈1시간봉 수, 약 2거래일) 안의 뉴스만 수급 판단에 사용

    def refresh(self, symbols: list[str], now_label: str) -> None: ...

    def headlines(self, symbol: str, n: int = 6) -> list[str]:
        fresh = [i for i in self.items.get(symbol, []) if i.seq > self.seq - self.keep]
        return [i.title for i in fresh[-n:]]


class SampleNews(NewsFeed):
    GOOD = ["{s} beats estimates, raises guidance", "Analysts upgrade {s} on strong demand",
            "{s} announces buyback", "{s} shares rally on partnership news"]
    BAD = ["{s} faces probe over practices", "{s} misses estimates, cuts outlook",
           "Downgrade hits {s} on weak demand", "{s} shares tumble after lawsuit"]
    MKT_BAD = ["New chip export sanction hits semiconductor stocks", "Tariff threat sends Nasdaq futures lower"]
    NEUTRAL = ["{s} to present at industry conference", "{s} options volume in line with average"]
    UNDER = {"QLD": "Nasdaq", "QID": "Nasdaq", "SOXL": "Semiconductor ETF", "SOXS": "Semiconductor ETF"}

    def __init__(self, seed: int | None = None, shock_prob: float = 0.04):
        super().__init__()
        self.rng = random.Random(seed)
        self.shock_prob = shock_prob

    def refresh(self, symbols, now_label):
        self.seq += 1
        for s in symbols:
            name = self.UNDER.get(s, s)
            r = self.rng.random()
            if r < self.shock_prob:
                title = self.rng.choice(self.BAD).format(s=name)
            elif r < self.shock_prob * 2.5:
                title = self.rng.choice(self.GOOD).format(s=name)
            elif r < 0.35:
                title = self.rng.choice(self.NEUTRAL).format(s=name)
            else:
                continue
            self.items.setdefault(s, []).append(NewsItem(s, title, now_label, self.seq))
            del self.items[s][:-10]
        if self.rng.random() < self.shock_prob / 2:  # 시장 전체 악재
            title = self.rng.choice(self.MKT_BAD)
            for s in ("QLD", "SOXL", "NVDA"):
                self.items.setdefault(s, []).append(NewsItem(s, title, now_label, self.seq))


class YahooNews(NewsFeed):
    URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={s}&region=US&lang=en-US"

    def __init__(self, refresh_sec: int = 900, timeout: int = 10):
        super().__init__()
        self.refresh_sec, self.timeout = refresh_sec, timeout
        self.last = 0.0

    def refresh(self, symbols, now_label):
        import urllib.request
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime
        if time.time() - self.last < self.refresh_sec:
            return
        self.seq += 1
        for s in symbols:
            try:
                req = urllib.request.Request(self.URL.format(s=s), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    root = ET.fromstring(r.read())
                fresh = []
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    try:  # 24시간 지난 기사는 수급 판단에서 제외
                        age = time.time() - parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
                    except (TypeError, ValueError):
                        age = 0
                    if title and age <= 86400:
                        fresh.append(title)
                self.items[s] = [NewsItem(s, t[:200], now_label, self.seq) for t in reversed(fresh[:8])]
            except Exception as e:
                print(f"  [뉴스 실패] {s}: {e}")
        self.last = time.time()

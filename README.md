# AI 트레이딩 오피스 (초안)

**웹 시뮬레이터:** https://excellent3935.github.io/trading-office/

팀장 1명, 시니어 여러 명, 주니어 2~3명으로 구성된 트레이딩 팀을 AI 에이전트로 구현한 프로젝트입니다.
나스닥과 코인을 다루고, 하루 목표 수익은 시드의 0.5%입니다. 기본은 **모의매매**입니다.

## 실행
```bash
python main.py                      # 오프라인 테스트(MockLLM + 샘플 시세)
python main.py --llm claude         # 내 PC의 Claude Code로 에이전트 판단
python main.py --seed 20000 --rounds 5 --random-seed 7
python -m unittest discover tests   # 리스크 한도 테스트
```
외부 패키지 없이 Python 3.10 이상에서 동작합니다.

## 하루 흐름
1. **시니어 → 팀장**: 담당 시장(나스닥/코인)의 일간 시황 보고 (bias, watchlist)
2. **팀장 → 시니어**: 하루 투자전략 지시 (시니어별 배분 %, 지시사항, 금지 종목)
3. **라운드마다**
   - 주니어가 담당 시니어에게 **선보고** → 시니어가 승인/반려/금액 축소
   - 시니어가 팀장 지시를 바탕으로 본매매 계획
   - 모든 주문은 **RiskManager → Broker** 순서로만 실행
4. **장 마감**: 손익, 포지션, 주문 수를 `office_data/ledger_*.jsonl`에 기록

## 안전 설계
| 원칙 | 구현 |
|---|---|
| AI는 의견만, 주문은 코드가 | 에이전트는 JSON만 반환. 체결은 `office.execute()`만 가능 |
| 리스크 한도는 코드로 강제 | `risk.py`: 종목당 10%, 주문당 5%, 총 노출 50%, 손절 1.5%, 일 20건 |
| 0.5% 목표는 상한 | 달성 시 신규 매수 금지(청산만 허용). 무리하게 채우지 않음 |
| 일일 손실 한도 | -1% 도달 시 전량 청산하고 그날 매매 중단 |
| Claude 실행 격리 | `--tools ""`(도구 0개), `--permission-prompts none`, 최소 환경변수, 빈 임시 폴더 |
| 실계좌는 사람 승인 | `--live`일 때 주문마다 터미널에 `yes` 입력 필요 |
| LLM 출력 불신 | JSON 파싱 실패, 허용 외 종목, 잘못된 금액 → 주문 없음 |
| 공매도·레버리지 없음 | 보유분만 매도 |

## 파일
```
main.py                     CLI
trading_office/config.py    시드, 목표, 리스크 한도, 팀 구성
trading_office/agents.py    팀장/시니어/주니어 프롬프트와 역할
trading_office/office.py    하루 운영 사이클(보고 → 지시 → 선보고 → 매매 → 마감)
trading_office/risk.py      리스크 매니저
trading_office/broker.py    PaperBroker(모의), LiveBroker(뼈대)
trading_office/market.py    SampleMarket(가상 시세), 실데이터 인터페이스
trading_office/llm.py       ClaudeCLI, MockLLM
tests/test_risk.py          한도 테스트
```

## 다음 단계
- `market.py`에 실데이터 연결 (나스닥: yfinance/Alpaca, 코인: ccxt/업비트)
- 백테스트 모드 (과거 데이터로 여러 날 돌려서 전략 검증)
- 웹 대시보드 (팀원별 보고·지시·체결 타임라인)
- 실계좌는 모의매매로 충분히 검증한 뒤에, 소액·사람 승인 모드로만

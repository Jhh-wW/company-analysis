# 뉴스 본문 HTTP 기본 동시 폭 3

2026-09-23 · 무료 회귀 통과 · 운영 시간 단축 실측 없음

운영 뉴스 본문 adapter의 기본 동시 요청 수를 2에서 3으로 바꿨다. `NEWS_BODY_FETCH_CONCURRENCY`가 없을 때 적용되며, 명시한 `1`·`2`·`3`은 기존대로 우선한다. 잘못된 설정은 기존 순차 실행으로 돌아간다.

변경 제품 파일은 `app/src/features/pipeline/news_parallel_constants.py` 하나다. 일반 `BodyFetchConcurrency()`의 기본값은 그대로이고, 운영 경로가 명시한 폭을 전달한다. `pipeline/real.py`와 `news_intake/collection.py`의 동작은 이 변경에서 수정하지 않았다.

## 보존하는 계약

- 같은 호스트는 동시에 한 요청만 실행한다. `www` 변형도 같은 호스트 슬롯에 속한다.
- HTTP 요청·기사·글자·기간·마감 예산, 분석 묶음 경계와 AI 호출 상한을 유지한다.
- 완료 순서와 무관하게 원래 후보 순서로 소비하고 원문·법인·날짜·근거 검증을 그대로 적용한다.
- 진행 중 요청이 실패하면 이미 시작한 요청은 합류한 뒤 원래 중단 예외를 전파하고, 다음 후보와 분석은 시작하지 않는다.
- 검증된 운영 callback에만 병렬 실행을 적용한다. 알 수 없는 주입 callback은 순차를 유지한다.

## 확인한 회귀

`pipeline/tests/test_news_parallel_runtime.py`를 다음과 같이 보강했다. 시계에 따른 속도 비율 대신 `Barrier`와 `Event`로 실제 중첩·완료 순서·합류를 확인한다.

| 검증 | 확인 내용 |
|---|---|
| 새 기본값과 override | 환경변수 없음은 세 요청이 같은 barrier에서 만남; 명시한 2·3도 해당 폭으로 만남; 명시한 1과 잘못된 값은 호출 스레드에서 순차 실행 |
| 부모 문맥 격리 | 세 작업의 DNS/robots/진단 객체가 서로 다르고 부모와도 다름; 부모 마감과 취소 문맥은 유지 |
| 폭 2↔3 동등성 | 8개 기사, 2개 분석 묶음에서 첫 두 기사의 완료 순서를 뒤집어도 근거 조각 전체·분석 prompt/schema/max_tokens·동시성 항목 외 진단·실제 transport 요청 목록이 동일 |
| HTTP 실패 동등성 | 한 기사의 원주소와 www 변형을 모두 403으로 반환해도 폭 2↔3의 실패 사유·살아남은 7개 근거·분석 입력·요청 수가 동일 |
| 동일 호스트 | 세 기사와 www 변형이 있어도 실제 진입은 1개; 폭 2·3 모두 대기 중 취소를 따름; 완료 robots 캐시와 개별 마감도 보존 |
| 실패 합류 | 기본 폭 3으로 세 기사가 시작한 상태에서 첫 기사가 중단돼도 나머지 둘이 끝나기 전 반환하지 않음; 네 번째 기사/분석 호출 없이 원래 예외 객체 전파 |

기존 수집기·요청 원장 회귀를 함께 실행해 **122개 통과**했다. 본문 호출 상한, 마지막 호출 몫, 글자 예산, 마감, 분석 중단, 선행미사용 정산도 이 묶음에 포함한다.

## 무료 재현

root `.venv`를 사용하고 실제 네트워크 및 SQLite 연결을 실패하도록 차단한다. pytest 공통 fixture와 캐시를 끄므로 실DB나 임시 DB도 필요 없으며 제품 파일의 bytecode도 생성하지 않는다.

```powershell
Set-Location -LiteralPath 'C:/Users/jh-wo/orca/workspaces/기업분석2/fix-live-report-followup-20260923/app'
@'
import sys, socket, sqlite3
sys.dont_write_bytecode = True
sys.path.insert(0, '.')
def denied(*args, **kwargs):
    raise AssertionError('network/SQLite forbidden by task')
socket.socket.connect = denied
socket.create_connection = denied
sqlite3.connect = denied
import pytest
raise SystemExit(pytest.main([
    '--noconftest', '-p', 'no:cacheprovider', '-q',
    'src/features/pipeline/tests/test_news_parallel_runtime.py',
    'src/features/news_intake/tests/test_collection_body_concurrency.py',
    'src/features/news_intake/tests/test_body_prefetch.py',
]))
'@ | & 'C:/Users/jh-wo/.claude/workspace/기업분석2/.venv/Scripts/python.exe' -B -
```

확인 결과: `122 passed in 3.31s`. 첫 회귀 실행에서는 실패 fixture가 원주소만 403으로 만들어 www 변형으로 정상 복구됐다. 모든 표기 변형이 403인 fixture로 수정한 뒤 실패 사유 보존과 결과 동등성을 확인했다.

## 효과의 범위

서로 다른 호스트의 세 번째 본문 요청이 같은 분석 묶음 안에서 기다리던 경우 그 대기를 겹칠 수 있다. 같은 호스트뿐이거나 균일한 지연의 4개 묶음에서는 폭 2와 3 모두 두 차례가 필요해 단축되지 않을 수 있다. AI 모델·작성/검수 절차·분석 호출 상한은 변경하지 않았다.

동시 폭이 넓어져 조기 중단 전에 시작한 무료 HTTP가 더 있을 수 있으며, 기존 선행미사용 정산과 총 요청 상한으로 관리한다. 실제 마감에 가까운 환경에서는 도착 시각에 따라 확보 자료가 달라질 수 있으므로 무료 고정 입력의 동등성을 운영 모든 실행의 바이트 동일성으로 확대 해석하지 않는다. 운영 절감 초수, 시간 반감, 2분대 도달은 실측하지 않았고 주장하지 않는다. 배포 환경에 `NEWS_BODY_FETCH_CONCURRENCY=2`가 명시돼 있으면 기본값 변경만으로는 실행 폭이 바뀌지 않는다.

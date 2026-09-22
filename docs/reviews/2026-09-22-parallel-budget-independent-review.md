# 병렬 공급자 정산 독립 검토

2026-09-22 · 구현 담당과 별도 검토 · 외부 유료 호출 없음

**검토 결론: 발견한 P2 결함 1건은 구현 담당의 수정 후 독립 재검증을 통과했다. 현재 검토 범위에서 열린 차단 결함은 없다.** 소스·시험 파일은 직접 수정하지 않았고, 이 보고서만 작성했다. 운영 성능·실제 공급자 지연·비용 절감은 측정하지 않았다.

검토 대상은 `pipeline/real.py`, `core/provider_gateway/concurrency.py`와 `attempt_context.py`, `budget/state_machine.py`와 `parallel_constants.py`, `web/paid_runtime.py`, 신규 병렬 시험이다. 검토 중 추가된 `budget/provider_budget.py`의 예약 대기 변경도 포함했다. 실제 도달성 확인을 위해 `composer/parallel_sections.py`, `composer/logic.py`, `composer/pipeline.py`, `web/generation_singleflight.py`의 연결 경계도 읽었다. 같은 작업 폴더에서 구현이 진행 중인 상태를 검토했으며, 아래 결과는 마지막 표적 시험 시점의 코드 기준이다.

**발견하고 수정 확인한 결함**

P2 — 초과 정산 뒤 형제 호출 보호 예약이 새 호출 잔액으로 남았다.

초기 `_update_phase_after_attempt()`는 phase 잔액을 `max(진행 중 시도 예약 합, 기존 phase 잔액 - 이번 정산액, 0)`으로 갱신했다. 큰 초과 정산에서 이미 전송한 형제의 노출액을 보호하려던 값이 이후 정산에서 실제로 쓸 수 있는 잔액으로 취급됐다.

실제 SQLite 메모리 DB와 새 병렬 시험의 입력으로 다음을 재현했다.

| 순서 | 최초 phase 예약 | 확정 비용 누계 | 변경 전 phase 잔액 |
|---|---:|---:|---:|
| 100원 예상 시도 3개 전송 | 300원 | 0원 | 300원 |
| 첫 시도 실제 비용 400원 정산 | 300원 | 400원 | 200원 |
| 둘째 실제 비용 10원 정산 | 300원 | 410원 | 190원 |
| 셋째 실제 비용 10원 정산 | 300원 | 420원 | 180원 |

이후 `begin_attempt(estimated_krw=100)`이 성공했다. 살아 있는 동일 요청의 `ProviderBudget`은 원래 300원 한도로 계속 차단하므로 정상 단일 요청에서 유료 호출 우회까지 확인한 것은 아니다. 다만 영속 원장 자체의 “기존 예약 안에서만 새 호출 허용” 계약은 깨졌고, 로컬 문맥과 DB의 판정이 달라졌다.

구현 담당과 총괄에 Orca 메시지로 원인·재현값·회귀시험 제안을 전달했다. 구현 담당은 다음과 같이 수정했다.

- phase의 실제 입장 잔액은 `max(기존 잔액 - 정산액, 0)`으로만 줄인다.
- 이미 전송한 형제의 예약은 `_PHASE_RESERVATION_EXPOSURE_SQL`로 노출 집계에 별도 포함한다.
- 실행별/통장별 노출 및 통장 전체 기간 예약 집계에서 `max(phase 입장 잔액, 진행 시도 예약 합)`을 사용한다.
- 마지막 형제까지 정산하면 확정 비용 420원·예약 0원이 되고, 이후 1원 신규 시도도 거부하는 회귀시험을 추가했다.

수정 후 `test_actual_overrun_does_not_release_sibling_reservations`를 포함한 관련 50개 시험이 통과했다. 이 결함은 **수정 확인 완료**로 기록한다.

**확인한 계약과 근거**

| 검토 항목 | 확인 결과 |
|---|---|
| 부모 문맥 준비 | `ask.prepare_parallel()`이 유효한 장별 입력 확인 후 부모에서 유료 phase와 지연 client를 먼저 연다. 장부 wrapper도 준비 함수와 갱신된 병렬 능력을 전달한다. |
| 자식 문맥 격리 | 각 작업에 서로 다른 `copy_context()`를 전달한다. `_ProviderCallContext`는 불변 값이며 stage·캐시 여부·남겨 둘 호출 수의 임시 변경이 형제나 부모로 새지 않는다. |
| 공유 예산·사용량 | 복사되는 Context 안의 `ProviderBudget`은 같은 객체여서 잠금 아래 예약·정산한다. 요청 전체 호출 수와 사용량 목록도 잠금으로 보호하며, 이미 예약한 호출의 정산은 호출 시작 때 잡은 예산 객체에 수행한다. |
| 임시 금액 부족 | 병렬 작성만 형제의 임시 예약이 풀리기를 기다릴 수 있다. 대기는 Condition 잠금을 놓고 0.25초마다 취소·lease를 재확인한다. 실제 지출과 다음 요청만으로 한도를 넘으면 즉시 거부하고, 미확정 호출은 알림으로 대기를 중단한다. 한 번에 1개만 예약할 예산에서도 정산 후 같은 입력 3개를 순차 처리하는 시험을 확인했다. |
| SQLite 동시 입장 | 별도 연결 4개가 경쟁해 정확히 3개만 들어가고, 나머지는 거절된다. 세 호출은 동일한 phase 잔액을 함께 사용하며 합계 예상액을 넘길 수 없다. |
| 미확정 실패 뒤 형제 정산 | 첫 부채가 발생하면 종료 요청을 append-only 사건으로 남겨 새 입장과 아직 안 보낸 시도의 전송을 막는다. 이미 전송한 형제의 소유권은 마지막 정산까지 유지하고, 확정 사용량·부채를 모두 기록한 뒤 phase를 닫는다. |
| 대기 중 취소 | 전역 공급자 자리를 기다리는 동안 취소를 재확인한다. 아직 입장하지 않은 호출은 예약·전송 없이 끝나고 이미 전송한 호출은 실제 결과를 정산한다. |
| 실행기 실패 처리 | 장 실행기는 오류 뒤 추가 작업 배정을 중단하고, 이미 실행 중인 작업들이 끝날 때까지 합류한다. 외곽 원장 마감도 남아 있는 시도들을 모두 순회한다. |
| lease 만료·다른 소유자 | 현재 소유자와 유효시간 검사를 유지한다. 만료 시 전송 의도 시도들은 각각 부채로, 미전송 시도는 각각 0원으로 회수한다. 다른 소유자의 결과 기록과 만료 뒤 새 정산은 거부한다. |
| 요청 호출 상한 | 동시에 예약하더라도 요청 호출 수와 뒤 필수 단계 몫을 잠금 안에서 검사한다. 병렬화가 요청별 호출 한도를 늘리지 않는다. |
| 프로세스 전체 상한 | 공유 FIFO limiter가 서로 다른 보고서 엔진에도 적용된다. `PROVIDER_MAX_CONCURRENT_CALLS`는 현재 1~5 범위이며 잘못된 설정은 1로 제한한다. |
| 순차 동작 유지 | 병렬 안전 능력·부모 예산·attempt callback·검증된 계량 client가 모두 갖춰져야 작성자 능력이 3으로 올라간다. 과거 장 문장에 의존하는 flat 경로는 순차로 남고, 프로세스 공급자 상한 1은 실제 전송을 순차화한다. |

`max_parallel_calls`는 영속 원장 callback의 명시적 능력으로 전달된다. 기존 callback의 기본값은 1이고, 실제 paid runtime은 본조사 phase에서만 3을 제공한다. OCR 등 다른 phase를 이 변경으로 병렬화하지 않는다.

추가로 공급자 차단기가 전송 의도 단계에서 이미 `LOCAL_FAILURE/KNOWN_ZERO`로 닫은 시도는 공통 취소 callback에서 다시 정산하지 않는 멱등 처리를 확인했다. 전송 의도가 남아 있거나 비용이 미확정인 시도를 이 조건으로 0원 처리하지 않는다.

**FULL 작성 연결 확인**

네트워크만 가짜로 둔 별도 확인에서 다음 생산 함수를 실제로 연결했다.

`_v2_ask_via_provider → _DeferredMeteredClient → _MeteredClient → _CallLedgerRecorder.wrap → compose_sections(typed packet)`

처음에는 부모 예산·callback이 없어 병렬 능력이 꺼져 있었다. 준비 함수가 부모에서 예산·callback을 한 번 열고 지연 client를 해소한 뒤, 아홉 장을 실제 작성 경계로 보냈다. 결과는 부모 준비 1회, 공급자 최대 동시 3개, 가짜 전송 9개, 사용량 9개, 장부 작성 9개였으며 장부의 장 순서도 목차와 일치했다.

이는 FULL용 장별 작성 경로가 생산 공급자 factory와 장부 wrapper를 거쳐 병렬 분기에 도달함을 확인한다. 이 확인은 빈 문장 모형 응답을 사용했으므로 웹 요청부터 최종 품질 통과·저장·PDF 출고까지의 공개 경로 전체 성공을 검증한 것은 아니다. 공개 출고 전체 회귀는 해당 경계를 소유하는 통합시험과 함께 판정해야 한다.

**실행한 표적 시험**

모두 프로젝트 `.venv/Scripts/python.exe`로 실행했다. 아래 시험 집합은 서로 겹치므로 개수를 합산하지 않는다.

1. 초기 신규 병렬 시험 27개 통과:

```powershell
.venv/Scripts/python.exe -m pytest app/src/features/budget/tests/test_parallel_attempts.py app/src/features/pipeline/tests/test_provider_parallel.py -q -p no:cacheprovider
```

2. 기존 원장·paid runtime·공급자 차단기·문맥 회귀 45개 통과:

```powershell
.venv/Scripts/python.exe -m pytest app/src/features/budget/tests/test_state_machine.py app/src/web/tests/test_budget_attempt_runtime.py app/src/web/tests/test_provider_health_paid_runtime.py app/src/core/provider_gateway/tests/test_attempt_context.py -q -p no:cacheprovider
```

3. 결함 수정 후 재검증 50개 통과:

```powershell
.venv/Scripts/python.exe -m pytest app/src/features/budget/tests/test_parallel_attempts.py app/src/features/pipeline/tests/test_provider_parallel.py app/src/features/budget/tests/test_state_machine.py -q -p no:cacheprovider
```

4. 이후 추가된 임시 예약 대기·취소·미확정 대기 중단·실제 예산 소진 거부와 기존 예산/차단기 회귀 61개 통과:

```powershell
.venv/Scripts/python.exe -m pytest app/src/features/budget/tests/test_parallel_attempts.py app/src/features/budget/tests/test_provider_budget.py app/src/features/pipeline/tests/test_provider_parallel.py app/src/web/tests/test_provider_health_paid_runtime.py -q -p no:cacheprovider
```

별도로 실제 SQLite 반례와 앞의 FULL 작성 연결을 직접 실행했다. 수정된 추적 파일에 대한 `git diff --check`도 통과했다. FastAPI 시험에서는 기존 Starlette/httpx 지원 중단 예고가 1건 나왔고 시험 실패는 없었다.

검토 이후 구현 담당이 추가 변경한 부분은 별도 회귀 대상이다. 이번 검토는 유료 공급자의 실제 응답 지연·청구·SDK 연결 풀 부하·다중 프로세스 운영 처리량을 측정하지 않았으며, 속도나 비용 절감률의 실측 증거로 사용하지 않는다. 외부 유료 호출·배포·커밋·푸시는 수행하지 않았다.

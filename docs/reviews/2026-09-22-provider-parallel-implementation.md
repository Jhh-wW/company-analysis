# 장별 동시 작성을 위한 공급자·정산 경계 구현

2026-09-22 · 공급자 경계 담당 · 외부 유료 API 호출·배포·커밋·푸시 없음

장별 작성 최대 3개를 실제로 겹쳐 실행할 수 있도록 호출 문맥과 예약·정산 경계를 보강했다. 모델, 출력 상한, 캐시 요율, 검수·인용 계약, 요청당 AI 20회 상한, 본조사 단계 예약 상한 2,000원은 변경하지 않았다. 실제 서비스의 시간·총비용 절감률을 측정한 결과는 아니다.

## 구현과 연결 계약

- `real.py`의 단계·프롬프트 캐시·후속 호출 보호 몫을 불변 객체와 요청별 `ContextVar`로 분리했다. 사용량 목록과 비용 미확정 표식, 호출 횟수 갱신은 짧은 잠금으로 보호한다.
- 작성 함수는 `prepare_parallel()`을 제공한다. 총괄의 composer가 장별 입력을 검증한 뒤 부모 스레드에서 호출하면 지연 예산·정산 문맥과 지연 client를 초기화한다. 실제 계량 client, 부모 예산 문맥, 다중 정산 지원 callback을 확인해야 `parallel_safe=True`와 최대 3개 실행 속성을 제공한다. 검증되지 않은 callback·가짜 경계는 순차로 남는다.
- 각 composer 작업은 별도 `copy_context()`로 예산·정산·실행 취소 문맥을 전달해야 하며, 실패 후 추가 작업을 멈추고 이미 시작한 호출의 종료까지 기다려야 한다. 이 스케줄러·장부 연결은 총괄 소유 코드에서 수행했다.
- 프로세스 내 pipeline 공급자 호출은 FIFO 대기열로 제한한다. 대기열 잠금은 자리 배정에만 사용하며 토큰 계수·네트워크 통신·결과 대기 전체를 잠그지 않는다. 기다리는 요청도 취소·lease·비용 미확정을 확인한다.
- 호출별 금액 예약은 원자적으로 검사한다. 안전한 작성 경로에서 부족분이 형제 호출의 임시 예약일 때만 정산을 기다려 다시 평가한다. 실제 지출과 단일 호출 예상액부터 한도를 넘으면 기존 예산 초과로 차단한다. 비용 미확정·취소·lease 상실·일반 예외 밖의 실행 중단은 대기를 깨운다.

| 설정 | 기본값 | 복귀·검증 규칙 |
|---|---:|---|
| `REPORT_WRITER_MAX_PARALLEL_CALLS` | 3 | 허용 1~3, 1이면 장 스케줄러도 순차, 잘못된 값은 1 |
| `PROVIDER_MAX_CONCURRENT_CALLS` | 5 | 프로세스 내 pipeline 공급자 자리 합계, 허용 1~5, 잘못된 값은 1 |

## 영속 원장의 병렬 안전성

기존 원장은 단계당 진행 중 시도 하나만 허용했고, 첫 비용 미확정 사건이 단계를 즉시 닫아 이미 전송한 형제의 정산을 거절했다. 총괄 승인으로 예산 상태기계와 웹 정산 callback까지 소유 범위를 넓혀 수정했다.

- `begin_attempt`의 기본은 여전히 1개다. 실제 pipeline callback만 명시적으로 최대 3개를 요청하며, SQLite 쓰기 거래에서 진행 중 시도의 예상액 합과 단계 잔액을 비교한다.
- 단계 종료 요청은 불변 사건으로 남긴다. 이후 새 시도·전송은 차단하고, 기존 형제 호출의 소유권·정산은 마지막 시도까지 보존한 뒤 단계를 닫는다. 잘못된 소유자나 만료된 lease는 계속 거부한다.
- lease 만료 및 바깥 정산 회수는 진행 중 시도를 모두 처리한다. 전송 전 시도는 0원, 전송 의도 뒤 미확정 시도는 해당 예상액을 부채로 남긴다.
- 실제 비용 초과 뒤에도 이미 진행 중인 형제 예약을 비용 노출에 포함하되, 그 보호액을 새 호출의 가용 잔액으로 돌리지 않는다. 300원 예약에서 세 호출을 보낸 뒤 실제 400/10/10원 정산한 사례는 최종 확정액 420원·잔액 0원이고 새 시도를 거절한다.
- 동시에 수행하는 heartbeat의 조회·연장도 같은 쓰기 거래로 처리한다. 전송 전 취소 callback은 이미 공급자 차단기가 0원으로 닫은 시도를 다시 정산하지 않는다.

## 검증

모든 명령은 루트 `.venv/Scripts/python.exe`를 사용했다. 신규 공급자 시험은 실제 스레드와 `Barrier(3)`을 사용했고 네트워크만 가짜로 대체했다. 실제 웹 유료 실행 callback·공급자 차단기·임시 SQLite를 통과하는 시험도 포함했다.

```text
.venv/Scripts/python.exe -m pytest app/src/features/budget/tests app/src/features/pipeline/tests app/src/core/provider_gateway/tests -q -p no:cacheprovider
1416 passed, 10 subtests passed

.venv/Scripts/python.exe -m pytest app/src/web/tests/test_provider_health_paid_runtime.py app/src/web/tests/test_paid_stage_guards.py app/src/web/tests/test_paid_phase_context.py app/src/web/tests/test_budget_settle.py app/src/web/tests/test_budget_recheck.py app/src/web/tests/test_budget_attempt_runtime.py app/src/web/tests/test_link_revoke_cancels_run.py app/src/web/tests/test_generation_singleflight_integration.py -q -p no:cacheprovider
152 passed

# 마지막 순차 복귀 설정·취소 보강 뒤 실행
.venv/Scripts/python.exe -m pytest app/src/features/pipeline/tests/test_provider_parallel.py app/src/features/budget/tests/test_parallel_attempts.py app/src/web/tests/test_provider_health_paid_runtime.py -q -p no:cacheprovider
49 passed

git diff --check
오류 없음
```

신규 시험은 세 호출의 단계·캐시·후속 호출 보호 몫 격리, 사용량·금액 정산, 정상/확정 비용 실패/미확정 실패, 대기 취소·전송 뒤 취소·전송 전 예약 취소, 실제 금액·횟수 초과 차단, 임시 예약 대기 후 성공, 실행 중단 후 대기 해제, 서로 다른 보고서의 전역 자리 공유, 지연 client의 부모 초기화, SQLite 다중 예약·종료·lease 회수·초과 정산을 검증한다.

처음 확대 회귀에서 기존 시험이 `_MeteredEngine`을 함수로 치환하는 사례 6개가 실패했다. capability 판정이 이런 대체 경계를 보수적으로 순차 처리하도록 고친 뒤 전체 1,416개가 통과했다. 웹 시험의 기존 Starlette 사용 중단 예정 경고 1개는 남아 있다. 독립 검토자는 초과 정산 후 잔액과 실제 FULL writer→지연 client→장부→장별 입력 연결을 추가 확인했다.

## 변경 파일

- `app/src/features/pipeline/real.py`
- `app/src/features/pipeline/provider_parallel_constants.py`
- `app/src/features/pipeline/tests/test_provider_parallel.py`
- `app/src/core/provider_gateway/attempt_context.py`
- `app/src/core/provider_gateway/concurrency.py`
- `app/src/features/budget/provider_budget.py`
- `app/src/features/budget/state_machine.py`
- `app/src/features/budget/parallel_constants.py`
- `app/src/features/budget/tests/test_parallel_attempts.py`
- `app/src/web/paid_runtime.py`
- 이 보고서

## 남은 운영 확인

실제 호출 입력과 이미 사용한 단계 예산에 따라 세 예상액이 동시에 들어가지 않을 수 있다. 이때 모델·상한을 낮추지 않고 실행 폭이 자연히 줄어든다. 실 API의 지연·처리량 제한·캐시 적중·건별 총비용·보고서 품질 비교는 수행하지 않았다.

전역 자리 제한은 현재 프로세스의 pipeline 계량 Anthropic 경계에 적용된다. 별도 프로세스나 OCR·후보 검색 등 다른 공급자 경계까지 공유하는 분산 제한은 아니다. 장기 네트워크 응답은 기존 SDK 마감에 의존하며, 이미 전송한 호출은 취소로 비용을 0원으로 지우지 않는다. 새 원장 스키마나 운영 데이터 마이그레이션은 추가하지 않았다.

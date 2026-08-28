# 비용 상태기계·provider gateway 통합 지침

이 문서는 이번 단계에서 일부러 고치지 않은 `app/src/web`을 다음 단계에서 옮길 때의
계약이다. 지금 코드는 새 표와 API만 준비한다. **`prepare_cutover()`를 호출하기 전에는
기존 웹 동작이 바뀌지 않는다.**

## 먼저 알아둘 결론

- 900원·3,000원은 실제 청구 절대 상한이 아니라 **새 호출 입장 기준**이다.
- 입장 합계는 `확정 비용 + 보수부채 + ACTIVE phase 예약`이다.
- 기존 정책상 제한이 없는 MEMBER 통장이나 일반 운영 run cap은 `None`으로 넘긴다.
  임의의 거대 숫자로 바꾸지 않으며, 합계 기록은 그대로 하고 해당 검사만 생략한다.
- 보수부채가 있어도 `_UNRESOLVED_BUCKETS`처럼 통장 전체를 별도로 잠그지 않는다.
- 작업이 살아 있는지는 메모리 tuple이 아니라 `budget_phase_accounts`의 DB lease로
  판단한다.
- SDK와 이 gateway의 재시도는 모두 0회다. Anthropic client는 계속
  `max_retries=0`으로 만들어야 한다.
- prompt, 응답 본문, API key, 예외 메시지는 attempt 원장에 저장하지 않는다.

## 전환 순서

전환은 기존 웹이 새 API를 부르는 코드와 **같은 배포 단위**에서만 한다. 새 schema만
미리 배포하는 것은 안전하지만, 웹을 그대로 둔 채 실제 cutover를 실행하면 구 장부
write barrier가 기존 유료 호출을 의도적으로 실패시킨다.

1. 새 유료 조사 입장을 maintenance 상태로 막는다.
2. 정상 실행 중인 기존 유료 호출을 끝까지 기다린다. 끝나지 않은 legacy inflight는
   실제 전환 시 `UNKNOWN_LEGACY` 보수부채가 된다는 점을 운영자가 확인한다.
3. SQLite 백업을 만든다.
4. 같은 DB에서 `spend_store.ensure_schema(conn)`을 실행한다.
5. `state_machine.prepare_cutover(conn, migrated_at=..., dry_run=True)` 결과의 phase,
   확정 attempt, 미확정 attempt 수를 기록하고 commit하지 않아도 DB가 안 바뀌는지
   확인한다.
6. 명시적 transaction에서 `prepare_cutover(..., dry_run=False)`를 실행하고 결과를
   확인한 뒤 commit한다. 실패하면 rollback한다.
7. 동일한 startup에서 새 web 호출부를 켠 뒤 maintenance를 해제한다.

전환 표식은 `attempt-ledger-v1`이며 호출은 멱등이다. 구판의 0원 inflight는 0원으로
확정하지 않는다. 해당 phase의 승인된 예약 기준(후보 50원, 식별 100원, OCR 100원,
본조사 900원)을 `UNKNOWN_LEGACY` 보수부채로 보존한다. 구 표는 삭제하지 않는다.

## 기존 `paid_runtime` 교체표

| 기존 경로 | 새 경로 |
|---|---|
| `begin_inflight()` + `_ACTIVE_PAID_PHASES` | `state_machine.begin_phase()` |
| provider 직전 메모리 `ProviderBudget.reserve_call()`만 사용 | `begin_attempt()` 후 DB commit |
| 실제 네트워크 호출 | `provider_gateway.gateway.call_once()` |
| active tuple 등록·해제 | DB lease + `heartbeat_phase()` + `complete_phase()` |
| `finish_inflight()` | `record_attempt_outcome(KNOWN_COST)` 후 `complete_phase()` |
| `keep_inflight_with_known_spend()` | 알려진 호출과 모르는 호출을 서로 다른 attempt로 기록 |
| `_UNRESOLVED_BUCKETS` 전역 봉쇄 | `load_exposure()`의 liability를 입장 합계에 포함 |
| `settle_inflight_as_reserved()` | 아래 세 가지 `resolve_liability()` 명시 행동 |
| 정산 뒤 `_seed_ledger()` | 호출하지 않음. 새 SQLite attempt 원장이 정본 |

기존 `settle_inflight_as_reserved()`는 cutover 전 legacy 관리자 화면에만 남긴다. cutover
표식 뒤에는 DB write barrier가 구 표의 INSERT/UPDATE/DELETE를 거절한다.

## 호출 한 번의 순서

각 DB 콜백은 짧은 연결에서 transaction을 commit한 뒤 다음 단계로 간다.

1. `begin_phase(...) -> PhaseAccount`
2. 호출 하나마다 `begin_attempt(...) -> AttemptAccount`
3. provider request를 로컬에서 완전히 검증한다. 이때 실패했고 전송하지 않았음이
   확실하면 `record_pre_dispatch_failure(..., close_phase=True)`로 known-zero를 남긴다.
4. `gateway.call_once(...)`를 실행한다.
   - `before_dispatch`: `heartbeat_phase()`로 lease를 연장한 뒤
     `mark_dispatch_intent()`를 **commit**한다.
   - `send`: provider를 정확히 한 번 부른다.
   - `record_observation`: 아래 명시 매핑으로 `record_attempt_outcome()`을 commit한다.
5. 다음 호출이 있으면 2번으로 돌아가고, 모두 끝나면 `complete_phase()`로 남은 예약을
   반환한다.

`before_dispatch`가 실패하면 gateway는 provider를 부르지 않는다. provider 전송 뒤
결과 DB 기록이 실패하면 dispatch intent와 lease가 남는다. lease 만료 뒤
`expire_phase_lease()`가 그 attempt 예상액을 보수부채로 옮긴다. 이것은 “실제로
전송됐다”는 주장이 아니라, 전송 여부를 확정할 수 없다는 안전한 기록이다.

`heartbeat_phase()`의 만료 간격은 provider timeout보다 길어야 한다. 정확한 초 값은
이번 승인에 포함되지 않았으므로 기존 timeout·최장 정상 단계 실측 뒤 운영 상수로
결정한다. 임의의 TTL로 부채를 지우면 안 된다.

startup과 readiness는 `list_active_phases(expired_at_or_before=...)`로 DB lease를
조회한다. 만료된 각 행을 `expire_phase_lease()`로 CAS 처리하며, 다른 정상 ACTIVE
행을 한꺼번에 비우지 않는다.

저수준 경계에는 web·budget을 직접 import하지 않는 다음 callback 묶음을 주입한다.

```python
ProviderAttemptCallbacks(
    begin_attempt=lambda provider, operation, reserved_krw: attempt_token,
    heartbeat=lambda attempt_token: None,
    mark_dispatch_intent=lambda attempt_token: None,
    record_observation=lambda attempt_token, observation: None,
)
```

실행 순서는 `begin_attempt → heartbeat → mark_dispatch_intent → send 1회 →
record_observation`이다. `heartbeat`나 `mark_dispatch_intent`가 실패하면 send는 0회다.

## observation 매핑

provider gateway는 feature 경계를 지키기 위해 budget을 import하지 않는다. 웹
orchestrator가 다음처럼 값이 같은 enum을 명시적으로 바꾼다.

```python
state_machine.record_attempt_outcome(
    conn,
    attempt_id=attempt_id,
    transport_state=state_machine.TransportState(observation.transport_state.value),
    billing_state=state_machine.BillingState(
        observation.billing_disposition.value
    ),
    known_cost_krw=observation.known_cost_krw,
    liability_krw=observation.liability_krw,
    status_code=observation.status_code,
    error_type=observation.error_type,
    request_id=observation.request_id,
    lease_owner_id=worker_id,
    close_phase=should_close_phase,
    phase_succeeded=phase_succeeded,
    recorded_at=clock.now_kst().isoformat(),
)
```

보수부채 결과는 phase를 함께 닫아야 한다. 성공 응답이어도 usage 비용을 확인하지
못하면 `CONSERVATIVE_LIABILITY`다. HTTP 400·429 같은 status만으로 known-zero를
만들지 않는다.

재시작 이력 복구는 구 `load_run_history()`가 아니라
`load_run_exposure(conn, run_id=...)`를 써야 새 attempt의 확정 비용을 0원으로 잃지
않는다. startup·관리 화면은 `load_day_exposures()`로 날짜 전체와 통장별 확정 비용,
보수부채, 활성 예약을 함께 읽는다.

Anthropic adapter에는 응답의 실제 usage를 원화 비용으로 바꾸는 순수 함수를
`AnthropicAdapter(cost_resolver)`로 주입한다. Google Places 성공은 현재 승인된 고정
회계비용 49원을 `GooglePlacesAdapter(accounting_cost_krw=49.0)`에 넘긴다. adapter는
기존 pipeline이나 business-candidate feature를 직접 import하지 않는다.

Anthropic의 `_MeteredMessages` 경계는 원래 SDK 예외를 gateway wrapper의 `__cause__`로
되살려 상위의 기존 예외 분류를 보존한다. Google Places도 raw transport 안에서 안전한
기존 예외로 바꾸되 `status_code`를 별도 비민감 필드로 보존하고, gateway 기록 뒤
`GooglePlacesRateLimited`·`GooglePlacesTimedOut`·`GooglePlacesUnavailable` 원래 종류를
그대로 다시 발생시킨다. API key·응답 본문·예외 메시지는 관측 DTO에 들어갈 칸이 없다.

## 관리자 복구

`list_reconcilable()`은 날짜를 생략하면 오늘뿐 아니라 전 날짜를 모두 보여 준다.
`resolve_liability()`는 DB phase가 ACTIVE면 무조건 거절하며 다음 셋을 섞지 않는다.

- `CONFIRM_ACTUAL`: provider 자료로 실제비용을 확인했고 금액을 확정한다.
- `CONFIRM_CONSERVATIVE_LIABILITY`: 실제비용은 모르지만 보수부채를 입장 합계에 계속
  유지한다. 이 금액을 확정비용이나 JSONL 관측액으로 가장하지 않는다.
- `CONFIRM_ZERO`: provider 자료로 청구 0원을 확인했다.

세 행동은 모두 `actor_id`, `reason_code`, `resolved_at`을 새 append-only 사건으로
남긴다. 다른 ACTIVE phase나 lease를 지우지 않으며 `_seed_ledger()`를 부르지 않는다.

## 아직 통합 전에 결정하거나 확인할 것

- 운영 DB 현재 고아 수와 실제 Anthropic 청구액은 확인하지 못했다.
- subtype별 Anthropic 오류가 진짜 0원이라는 보장은 확인하지 못했다.
- lease 길이와 heartbeat 간격은 실측 뒤 결정해야 한다.
- provider 자체 spend limit이 없으므로 현재 금액은 실제 지출 절대 상한이 아니다.

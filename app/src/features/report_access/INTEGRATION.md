# PUBLIC 보고서 접근권한 통합 계약

이 기능의 PUBLIC grant는 브라우저를 증명하는 난수이고, URL의 run/report ID는
권한이 아니다. 한 브라우저가 만든 여러 보고서를 계속 열어야 하므로 grant 하나에
여러 run을 둘 수 있지만, **같은 run은 첫 report에서 다른 report로 바꿀 수 없다.**

## 작업 시작

스케줄러에 넘기기 전에 `issue_and_bind()`를 같은 DB 쓰기 거래에서 호출한다.
기존 token이 아직 유효하면 writer lock 안에서 같은 grant의 만료를 갱신한 뒤 새
run을 붙인다. 만료 직전 두 탭이 같은 옛 cookie로 동시에 시작해도 서로 다른
후속 token을 만들지 않으므로 어느 응답이 마지막에 와도 두 run과 기존 보고서를
함께 연다. 만료·철회된 token만 새 grant로 대체하며 옛 결속은 부활시키지 않는다.
`PUBLIC_GRANT_MAX_AGE_SEC`은 보고서 60일에 최대 실행시간과 commit 여유를 더한
값이며, `/run` 응답 쿠키의 `Max-Age`도 이 값을 사용한다.
실제 서버 권한의 정본은 언제나 `IssuedGrant.expires_at`과 DB 행이다.

## 보고서 저장

`bind_report()`는 PUBLIC grant의 `revoked_at`, `created_at`, `expires_at`을 쓰기
잠금이 잡힌 같은 거래 안에서 다시 확인한다. PUBLIC 호출은 현재 시각 추정이 아니라
실제 Delivery 영수증에 적힐 `delivery_expires_at`을 반드시 넘긴다. 저장소가 여기에
commit 여유를 더해 grant가 **보고서의 60일 전체보다 오래 사는지** 검증한다.
호출자는 다음 네 규칙을 지킨다.

1. 보고서 저장 거래의 마지막 쓰기 경계 가까이에서 호출하고 곧바로 commit한다.
2. PUBLIC 작업에서는 `PublicGrantBindingUnavailable`과 `False`를 모두 저장 실패로
   취급해 거래를 rollback한다. `False`는 만료 정리로 binding 자체가 이미 사라진
   경우까지 닫기 위해 필요하다.
3. `ReportBindingConflict`는 같은 run을 다른 report로 바꾸려는 불변식 위반이므로
   재시도하거나 덮어쓰지 않는다.
4. 최종 Delivery 거래에서도 실제 저장된 `Delivery.expires_at`으로 한 번 더 결속한
   뒤 명시적으로 commit한다. new·cache·single-flight waiter가 모두 이 경계를 쓰며,
   실패하면 Delivery·자동출고 승인·고객 청구가 함께 rollback된다.

`job_runtime.stage_report_storage(conn, job)`은 report·dashboard snapshot·권한 결속을
caller 소유 연결에 쓰되 commit/rollback하지 않는다. 최종 출고 통합자는 이 staging을
Delivery·cache·charge와 같은 transaction에서 호출한다. `_save_report(job)`은 아직
독립 저장을 요구하는 기존 호출자를 위한 호환 wrapper이며 그 wrapper만 직접 commit한다.

같은 run이 PUBLIC과 MEMBER 표에 동시에 있으면
`MixedReportOwnershipConflict`로 어느 행도 바꾸기 전에 거절한다. 호출자가 한쪽을
임의로 우선해 고치면 다른 소유자에게 보고서를 노출할 수 있으므로 자동 복구하지
않는다.

MEMBER는 PUBLIC 시간 정책을 사용하지 않는다. 기존 `bind_member_run()`의 OAuth
subject 결속과 `bind_report(delivery_expires_at=None)`의 MEMBER 갱신 흐름은 그대로
유지한다. LINK·ADMIN처럼 report_access 표가 소유하지 않는 저장에서 나오는
`False`도 정상이다.

`job_runtime.Job.requires_public_report_grant`는 작업 입장 때 한 번 확정한다. 완료
시점의 비용 통장·share key·로그인 상태로 PUBLIC 여부를 다시 추측하지 않는다.
같은 입장 응답에 실은 cookie의 서버 기준 만료(`public_grant_expires_at`)도 Job에
동결하고, 고정한 Delivery 만료+commit 여유보다 먼저 끝나면 DB 쓰기 전에 닫는다.

# PUBLIC 보고서 접근권한 통합 계약

이 기능의 PUBLIC grant는 브라우저를 증명하는 난수이고, URL의 run/report ID는
권한이 아니다. 한 브라우저가 만든 여러 보고서를 계속 열어야 하므로 grant 하나에
여러 run을 둘 수 있지만, **같은 run은 첫 report에서 다른 report로 바꿀 수 없다.**

## 작업 시작

스케줄러에 넘기기 전에 `issue_and_bind()`를 같은 DB 쓰기 거래에서 호출한다.
`issue_and_bind()`는 production 시계를 읽기 전에 SQLite writer lock부터 잡는다.
기존 token이 유효하거나, 철회되지 않았고 서버 만료 뒤
`PUBLIC_GRANT_STALE_RENEWAL_GRACE_SEC` 안이면 같은 grant의 만료를 갱신한 뒤 새
run을 붙인다. 이 유한 유예는 DB 만료와 브라우저가 응답을 받은 뒤 세기 시작한
cookie 만료의 짧은 차이만 흡수한다. 만료 직후 두 탭의 lock 순서와 응답 순서가
서로 뒤집혀도 어느 응답이 마지막에 와도 두 run과 기존 보고서를 함께 연다.
유예 경계부터는 새 grant로 교체하고 옛 결속을 넘기지 않으며, 철회 token은 유예
안이어도 절대 부활시키지 않는다.
`PUBLIC_GRANT_MAX_AGE_SEC`은 보고서 60일에 최대 실행시간, 발급→scheduler 인계
여유(`PUBLIC_GRANT_ADMISSION_MARGIN_SEC`), provider 완료 후 저장·PDF·원장 확정 상한
(`PUBLIC_GRANT_POSTPROCESS_MAX_SEC`), 최종 commit 여유를 각각 더한 값이다.
`/run` 응답 쿠키의 `Max-Age`도 이 값을 사용한다.
실제 서버 권한의 정본은 언제나 `IssuedGrant.expires_at`과 DB 행이다.

## 보고서 저장

`bind_report()`는 `ReportAudience` 중 예상 audience를 필수로 받고 PUBLIC grant의
`revoked_at`, `created_at`, `expires_at`을 쓰기
잠금이 잡힌 같은 거래 안에서 다시 확인한다. PUBLIC 호출은 현재 시각 추정이 아니라
실제 Delivery 영수증에 적힐 `delivery_expires_at`을 반드시 넘긴다. 저장소가 여기에
commit 여유를 더해 grant가 **보고서의 60일 전체보다 오래 사는지** 검증한다.
호출자는 다음 네 규칙을 지킨다.

1. 보고서 저장 거래의 마지막 쓰기 경계 가까이에서 호출하고 곧바로 commit한다.
2. `ReportBindingResult.audience`는 예상 audience와 exact identity여야 하고,
   PUBLIC·MEMBER는 `bound=True`, LINK·ADMIN은 `bound=False`여야 한다. 결과를
   bool로 축약하면 예외로 닫힌다.
3. `ReportBindingConflict`는 같은 run을 다른 report로 바꾸려는 불변식 위반이므로
   재시도하거나 덮어쓰지 않는다.
4. 최종 Delivery 거래에서도 실제 저장된 `Delivery.expires_at`으로 한 번 더 결속한
   뒤 명시적으로 commit한다. new·cache·single-flight waiter가 모두 이 경계를 쓰며,
   실패하면 Delivery·자동출고 승인·고객 청구가 함께 rollback된다.

`job_runtime.stage_report_storage(conn, job)`은 report·dashboard snapshot·권한 결속을
caller 소유 연결에 쓰되 commit/rollback하지 않는다. 최종 출고 통합자는 새 보고서마다
이 staging을 **정확히 한 번**, Delivery·ReleaseAuthority·artifact metadata·cache·charge와
같은 transaction에서 호출하고 한 번만 commit해야 한다. `_save_report(job)`의 독립
commit을 먼저 호출한 뒤 finalizer를 호출하면 안 된다. 이 단일 finalizer 배선은
Delivery·ReleaseAuthority 소유 통합 commit에서 완료한다.

같은 run이 PUBLIC과 MEMBER 표에 동시에 있으면
`MixedReportOwnershipConflict`로 어느 행도 바꾸기 전에 거절한다. 호출자가 한쪽을
임의로 우선해 고치면 다른 소유자에게 보고서를 노출할 수 있으므로 자동 복구하지
않는다.

MEMBER는 PUBLIC 시간 정책을 사용하지 않는다. 기존 `bind_member_run()`의 OAuth
subject 결속과 MEMBER typed result의 갱신 흐름을 유지한다. PUBLIC 선언에 MEMBER
행만 있거나 그 반대인 경우, 또는 LINK·ADMIN에 report_access 행이 있으면
`ReportAudienceConflict`로 어느 행도 바꾸지 않고 rollback한다.

`job_runtime.Job.report_audience`는 작업 입장 때 `ReportAudience` 네 값 중 하나로
한 번 확정한다. 완료 시점의 비용 통장·share key·`member_email` 빈 값으로
소유권을 다시 추측하지 않는다. `requires_public_report_grant`는 이 typed audience의
계산 property일 뿐 독립 상태가 아니다.
입장 응답에 실은 cookie의 서버 기준 만료(`public_grant_expires_at`)는 관측용
복사본일 뿐이다. 같은 token을 다른 탭이 연장할 수 있으므로 최종 판정은
`stage_report_storage()`와 같은 writer transaction에서 `bind_report()`가 다시 읽은
DB grant 행만 정본으로 삼는다.

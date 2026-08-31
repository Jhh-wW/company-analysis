# PUBLIC 보고서 접근권한 통합 계약

이 기능의 PUBLIC grant는 브라우저를 증명하는 난수이고, URL의 run/report ID는
권한이 아니다. 한 브라우저가 만든 여러 보고서를 계속 열어야 하므로 grant 하나에
여러 run을 둘 수 있지만, **같은 run은 첫 report에서 다른 report로 바꿀 수 없다.**

## 작업 시작

스케줄러에 넘기기 전에 `issue_and_bind()`를 같은 DB 쓰기 거래에서 호출한다.
기존 token은 보고서 최대 실행시간과 마지막 commit 여유가 모두 남았을 때만
재사용되고, 새 run의 완료 후 60일까지 열 수 있게 만료시각을 갱신한다. 여유가
부족하면 새 token으로 회전하되 과거 결속을 복제해 같은 브라우저의 보고서를
갑자기 닫지 않는다. `PUBLIC_GRANT_MAX_AGE_SEC`은 보고서 60일에 최대 실행시간과
commit 여유를 더한 값이며, `/run` 응답 쿠키의 `Max-Age`도 이 값을 사용한다.
실제 서버 권한의 정본은 언제나 `IssuedGrant.expires_at`과 DB 행이다.

## 보고서 저장

`bind_report()`는 PUBLIC grant의 `revoked_at`, `created_at`, `expires_at`을 쓰기
잠금이 잡힌 같은 거래 안에서 다시 확인한다. 호출자는 다음 세 규칙을 지킨다.

1. 보고서 저장 거래의 마지막 쓰기 경계 가까이에서 호출하고 곧바로 commit한다.
2. PUBLIC 작업에서는 `PublicGrantBindingUnavailable`과 `False`를 모두 저장 실패로
   취급해 거래를 rollback한다. `False`는 만료 정리로 binding 자체가 이미 사라진
   경우까지 닫기 위해 필요하다.
3. `ReportBindingConflict`는 같은 run을 다른 report로 바꾸려는 불변식 위반이므로
   재시도하거나 덮어쓰지 않는다.

같은 run이 PUBLIC과 MEMBER 표에 동시에 있으면
`MixedReportOwnershipConflict`로 어느 행도 바꾸기 전에 거절한다. 호출자가 한쪽을
임의로 우선해 고치면 다른 소유자에게 보고서를 노출할 수 있으므로 자동 복구하지
않는다.

MEMBER는 PUBLIC 시간 정책을 사용하지 않는다. 기존 `bind_member_run()`의 OAuth
subject 결속과 `bind_report()`의 MEMBER 갱신 흐름은 그대로 유지한다.

현재 `job_runtime`은 별도 통합 작업의 충돌을 피하려고 이 변경에 포함하지 않았다.
그 호출자는 PUBLIC 여부를 이미 알고 있으므로 위의 `False` 처리를 그 경계에서
연결해야 한다.

"""무봉인 v2 빈 장의 표시 전용 안내."""

from typing import Final

#: ★ 2026-09-16 사용자 결정: 독자에게는 «결과»만 말한다. 처리 과정(수집·검사
#:   문제 같은 우리 사정)은 보고서에 싣지 않는다. 옛 글자는 원인을 모른다는
#:   사정을 그대로 적어, 같은 화면에서 본문 안내문(composer)과 «세 번째 문구»로
#:   보였다(실측 — 메디라인 2026-09-18 보고서 3쪽 9장).
#: ★ `composer/constants.py`의 `NOTICE_INSUFFICIENT_EVIDENCE`와 «같은 글자»다.
#:   두 계층이 각자 만든 값이라 상수를 가져다 쓰지 않고 글자를 맞춰 둔다
#:   (report_standard는 composer에 의존하지 않는다 — 계층 경계 유지).
#:   글자가 갈라지면 report_standard/tests/test_empty_section.py 가 알려 준다.
EMPTY_SECTION_NOTICE: Final[str] = "확인된 자료가 부족해 이 장은 비어 있습니다."

"""뉴스의 보상 주체·수령자 반전을 확인하는 좁은 문법."""

import re

COMPENSATION_ACTION_RE = re.compile(r"보상|배상")
COMPENSATION_UNIT_RE = re.compile(r"(?<=[.!?。])\s+")
COMPENSATION_ACTOR = r"(?<![가-힣A-Za-z0-9])(?P<actor>[가-힣A-Za-z0-9][가-힣A-Za-z0-9·&.-]*?)"
COMPENSATION_METHOD = (
    r"\s*(?P<method>현금성\s*상품권|상품권|쿠폰|현금|포인트)"
    r"(?:으로|로|을|를)?(?=\s|[,.;:!?\"'“”‘’]|$)"
)
COMPENSATION_ISSUER_RE = re.compile(COMPENSATION_ACTOR + r"(?:은|는|이|가)" + COMPENSATION_METHOD)
COMPENSATION_RECIPIENT_RE = re.compile(COMPENSATION_ACTOR + r"(?:에게는|에는|에게|에)" + COMPENSATION_METHOD)

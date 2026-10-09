"""교육 예시를 실제 회사 실행으로 옮기는 후보의 닫힌 경계."""

import re

EDUCATION_PRACTICE_PROBLEM = "source_practice_unbound"
EDUCATION_PRACTICE_SLOTS = frozenset({"past_changes:completed_execution"})
PRACTICE_WRITER_GUIDE = " · 이 구간은 교육용 예시·안내다. 회사가 실제로 실행한 일로 서술하지 않는다."
SUMMARY_ACTUAL_EXECUTION_RE = re.compile(
    r"(?:도입|시행|운영|적용|개선|달성|검수|진행|구현|완료|출시|설정|정비|마련|포함)"
    r"[^.!?。]{0,70}(?:했다|하였(?:다|습니다)|했습니다|한다|합니다|하고\s*있(?:다|습니다)|"
    r"되어\s*있다|되었(?:다|습니다))|"
    r"(?:절차|원칙)[^.!?。]{0,70}두고\s*있다"
)
EXPLICIT_PROMPT_RE = re.compile(
    r"(?:^|\n)\s*#\s*(?:Project\s+Rules|프로젝트\s*(?:규칙|원칙))\b|"
    r"(?:A\s*안\s*:)[\s\S]*?(?:B\s*안\s*:)[\s\S]*?(?:조건|제약)\s*:|"
    r"(?:프롬프트|요청|프로젝트\s*규칙)의?\s*예시\s*[:：]"
    , re.I
)

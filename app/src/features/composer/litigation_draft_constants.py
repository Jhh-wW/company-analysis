"""검수 전 소송 사실과 자료 부재 설명을 분리하는 닫힌 문형."""
import re

LITIGATION_DRAFT_SECTION = "current_challenges"
LITIGATION_DRAFT_SLOT = "current_challenges:issue"
LITIGATION_DRAFT_REASON = "litigation_snapshot_before_absence_commentary"
LITIGATION_DRAFT_CITATION_COUNT = 1
LITIGATION_DRAFT_SUBJECT_MAX_CHARS = 100
LITIGATION_DRAFT_FACT_RE = re.compile(
    r"\s*(?P<fact>(?P<subject>[가-힣A-Za-z0-9㈜().&'· -]{1,"
    + str(LITIGATION_DRAFT_SUBJECT_MAX_CHARS)
    + r"}?)(?:가|는)\s*(?P<role>원고|피고)로\s*계류\s*중인\s*"
    r"소송사건은\s*(?P<count>\d+)\s*건"
    r"(?:(?:이며|이고|으로)\s*소송금액은\s*(?P<amount>(?:약\s*)?[\d,]+(?:\.\d+)?"
    r"\s*(?:천|만|억|조)?\s*원)(?:\s*규모)?)?)"
    r"(?:이나|이며|이고|이다|입니다)\s*[,.;]\s*(?P<comment>.+)\Z",
    re.DOTALL,
)
LITIGATION_DRAFT_SOURCE_END_RE = re.compile(
    r"(?:으로\s*소송금액은\s*(?P<amount>[\d,]+(?:\.\d+)?\s*(?:천|만|억|조)?\s*원))?"
    r"\s*(?:입니다|이다)\s*\Z"
)
LITIGATION_DRAFT_NONACTUAL_RE = re.compile(
    r"만약|가정|가상|예시|(?:인|하는|되는)\s*경우|(?:한다|된다|있다)면|"
    r"추정|예상|잠정|예정|현재도|지금도|현재까지|지금까지|이후에도|아직도"
)

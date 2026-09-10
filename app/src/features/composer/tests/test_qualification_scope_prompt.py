"""대상별 자격·인정 조건을 섞지 않는다는 규칙이 실제로 적혀 있는지만 보는
최소 정적 계약.

배경: SM 채용 FAQ 원문 동일 해시 복원 검토(.local-artifacts/
resume-20260910-SM-faq-current-reconstruction)에서, 원문이 한 대상(예:
프리랜서)에만 건 인정 조건을 문화 문단이 나열된 다른 대상(파트타임·인턴)
까지 넓혀 쓴 사례를 발견했다. 이 시험은 그 사례 자체를 재현하지 않는다
(회사·URL·고용형태 이름을 프롬프트에 박아 넣으면 안 되므로) — 대신
검수/작성 계약에 «대상마다 원문 조건을 따로 본다»는 일반 규칙 문구가
실제로 들어갔는지, 그 문구가 사례 고유 단어를 하드코딩하지 않았는지만
확인한다. 모델이 실제로 이 규칙을 지키는지는 이 시험이 증명하지 못한다
— 그건 실제 평가에서 별도로 확인해야 한다.
"""

from src.features.composer.body_review_constants import BODY_REVIEW_COMPARISON_GUIDE
from src.features.composer.constants import SECTION_GUIDES

#: 대상마다 원문 조건을 따로 확인하고, 한 대상의 조건을 나머지로 넓히지
#: 않는다는 규칙이 실제로 요구하는 최소 낱말들. 문장 형태·순서는 보지 않는다.
_SCOPE_SEPARATION_TOKENS = ("인정", "불인정", "예외", "넓혀 쓰지")

#: 이번에 발견된 사례 고유의 단어 — 일반 규칙 문구에 하드코딩되면 안 된다.
_CASE_SPECIFIC_WORDS = (
    "SM", "에스엠", "recruit.smentertainment", "파트타임", "파트타이머",
    "인턴", "프리랜서", "채용", "FAQ",
)


def _missing(text: str, required: tuple[str, ...]) -> list[str]:
    return [token for token in required if token not in text]


def test_body_review_guide_separates_qualification_conditions_per_target() -> None:
    assert not _missing(BODY_REVIEW_COMPARISON_GUIDE, _SCOPE_SEPARATION_TOKENS)


def test_culture_section_guide_separates_qualification_conditions_per_target() -> None:
    assert not _missing(SECTION_GUIDES["culture"], _SCOPE_SEPARATION_TOKENS)


def test_new_rule_does_not_hardcode_the_case_that_found_it() -> None:
    for guide in (BODY_REVIEW_COMPARISON_GUIDE, SECTION_GUIDES["culture"]):
        for word in _CASE_SPECIFIC_WORDS:
            assert word not in guide, (word, guide[:60])

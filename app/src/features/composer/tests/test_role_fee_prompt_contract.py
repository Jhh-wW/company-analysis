"""역할·과금·반복수익 안내가 세 검수 경로에 «같이» 실리는지만 보는 정적 계약.

이 파일은 모델이 그 안내를 실제로 지키는지 증명하지 않는다. 지키는 것은 하나다 —
작성 안내와 검수 안내가 같은 것을 요구하고, 그 문구가 flat·grouped·standalone
세 경로의 프롬프트에 모두 들어가는가. 회사·업종·상품 이름은 쓰지 않는다.
"""

from src.features.composer.body_review_constants import BODY_REVIEW_COMPARISON_GUIDE
from src.features.composer.constants import (
    BUSINESS_FLOW_GUIDE, FLOW_RELATION_REVIEW_GUIDE, OPERATIONS_FLOW_GUIDE,
)
from src.features.composer.diagram_check import _review_prompt
from src.features.composer.port import CollectedFragment, ComposedSentence, FlowRow
from src.features.composer.verify import (
    _GroupedReviewItem, _ReviewItem, _build_grouped_review_prompt, _build_review_prompt,
)


# ══════════════════════════════════════════════════════════
# ① 검수 안내가 네 가지 구별을 실제로 요구하는가
# ══════════════════════════════════════════════════════════

#: 이름·목차 조각은 «존재»만 보인다는 구별.
_EXISTENCE_ONLY = ("이름", "목차", "존재")
#: 대가 설명은 그 대상을 주어로 삼은 절에서만 가져온다는 구별.
_OWN_CLAUSE_ONLY = ("주어로 삼은", "다른 상품")
#: 계약·매출은 관계이지 과금 방식이 아니라는 구별.
_CONTRACT_IS_NOT_FEE = ("계약", "매출", "과금 방식")
#: 반복·확장 수익은 원문이 그 대상에 대해 밝힌 경우만이라는 구별.
_REPEAT_NEEDS_SOURCE = ("반복", "재예치", "신규 고객")


def _missing(guide: str, required: tuple[str, ...]) -> list[str]:
    """안내에서 빠진 요구 낱말만 돌려준다. 순서·문장 형태는 보지 않는다."""

    return [token for token in required if token not in guide]


def test_body_review_guide_states_the_four_distinctions() -> None:
    for required in (_EXISTENCE_ONLY, _OWN_CLAUSE_ONLY, _CONTRACT_IS_NOT_FEE):
        assert not _missing(BODY_REVIEW_COMPARISON_GUIDE, required), required


def test_flow_review_guide_states_the_four_distinctions() -> None:
    for required in (_EXISTENCE_ONLY, _OWN_CLAUSE_ONLY, _CONTRACT_IS_NOT_FEE,
                     _REPEAT_NEEDS_SOURCE):
        assert not _missing(FLOW_RELATION_REVIEW_GUIDE, required), required


def test_writing_guides_ask_for_the_same_thing_as_the_review_guides() -> None:
    """작성 쪽이 요구하지 않는 것을 검수 쪽만 막으면 매번 재작성으로 돈이 샌다."""

    assert not _missing(BUSINESS_FLOW_GUIDE, _OWN_CLAUSE_ONLY)
    assert not _missing(BUSINESS_FLOW_GUIDE, _REPEAT_NEEDS_SOURCE)
    assert not _missing(OPERATIONS_FLOW_GUIDE, ("주어로 삼은 원문",))


def test_guides_do_not_force_verbatim_copying_or_empty_cells() -> None:
    """근거가 있으면 바꿔쓰기를 그대로 두고, 빈 칸을 거짓 판정의 이유로 삼지 않는다."""

    assert "바꿔쓰기" in BODY_REVIEW_COMPARISON_GUIDE
    assert "그대로 베끼라는 뜻이 아니" in BODY_REVIEW_COMPARISON_GUIDE
    assert "빈 칸은 주장하지 않은 내용이므로 거짓 판정의 이유가 아니다" in FLOW_RELATION_REVIEW_GUIDE


# ══════════════════════════════════════════════════════════
# ② 세 경로가 그 안내를 모두 싣는가
# ══════════════════════════════════════════════════════════

def _fragment() -> CollectedFragment:
    return CollectedFragment("1", "사업내용", "회사는 해당 서비스를 제공한다고 밝혔다.")


def _sentence() -> ComposedSentence:
    return ComposedSentence("회사는 해당 서비스를 제공한다.", ("1",), "확인",
                            planned_claim_slot="operations_partners:operating_role")


def test_flat_body_prompt_carries_the_body_review_guide() -> None:
    prompt = _build_review_prompt(
        (_ReviewItem(1, _sentence(), "operations_partners"),), {"1": _fragment()}, "")

    assert BODY_REVIEW_COMPARISON_GUIDE in prompt


def test_grouped_prompt_carries_both_body_and_flow_guides() -> None:
    prompt = _build_grouped_review_prompt(
        (_GroupedReviewItem(1, "operations_partners", "문장", ("1",), sentence=_sentence()),),
        {"1": _fragment()}, None)

    assert BODY_REVIEW_COMPARISON_GUIDE in prompt
    assert FLOW_RELATION_REVIEW_GUIDE in prompt


def test_standalone_diagram_prompt_carries_the_flow_guide() -> None:
    row = FlowRow(cells=("자산", "서비스", "고객"), citations=("1",))
    prompt = _review_prompt(((1, "operations_partners", row),),
                            {"1": _fragment().text})

    assert FLOW_RELATION_REVIEW_GUIDE in prompt


def test_the_three_paths_share_one_flow_guide_text() -> None:
    """세 경로가 서로 다른 문구를 쓰면 한 곳만 고쳐도 조용히 어긋난다."""

    row = FlowRow(cells=("자산", "서비스", "고객"), citations=("1",))
    grouped = _build_grouped_review_prompt(
        (_GroupedReviewItem(1, "operations_partners", "문장", ("1",), sentence=_sentence()),),
        {"1": _fragment()}, None)
    standalone = _review_prompt(((1, "operations_partners", row),), {"1": _fragment().text})

    assert grouped.count(FLOW_RELATION_REVIEW_GUIDE) == 1
    assert standalone.count(FLOW_RELATION_REVIEW_GUIDE) == 1


def test_guides_name_no_company_industry_or_product() -> None:
    """닫힌 회사·업종·상품 이름을 안내에 박아 넣지 않았는지 지킨다."""

    banned = ("우리은행", "에스엠", "SM", "신탁업무운용수익", "퇴직연금", "방카슈랑스",
              "원비즈", "MVNO", "여행사업부문")
    for guide in (BODY_REVIEW_COMPARISON_GUIDE, FLOW_RELATION_REVIEW_GUIDE,
                  BUSINESS_FLOW_GUIDE, OPERATIONS_FLOW_GUIDE):
        for name in banned:
            assert name not in guide, (name, guide[:40])


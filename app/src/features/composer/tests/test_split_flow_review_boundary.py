"""D-1: 도식이 원인·결과를 «칸 경계로 나눠» 적을 때도 인과 가드가 그대로 지켜지는가.

★ 소유·범위
  - 이 파일 하나만 새로 만든다. ``test_split_flow_causal_support.py``(Opus 소유)는
    읽지도 참조하지도 않는다.
  - ``check_diagrams``· ``verify_report`` 공개 API만 실제로 호출한다. ``_split_role_span``
    같은 private 헬퍼는 이름조차 쓰지 않는다 — 자기 인용 조건(정상 보존·차용 거절·
    반대방향 거절·부정 거절·둘째 미증명 미봉인)에 따른 «기대 결과»만 선언한다.
  - 사유 코드(``CAUSE_*``)는 진단·문서가 함께 참조하는 안정된 계약이라 그대로
    임포트한다. 검수 응답 JSON의 키(``"관계"``·``"근거"``·``"원문"`` 등)는 기존
    통합 시험의 관례를 따라 리터럴 문자열로 쓴다.

★ 왜 3장(portfolio)인가 — «정상 전체 출력 양성» fixture가 되려면
  - 제품·서비스명 칸(``constants.PORTFOLIO_TABLE_HEADERS`` 첫 칸)은 이미 별도
    기계 검사(``diagram_check.portfolio_name_is_grounded``)가 «원문에 있는
    이름인가»만 표면 부분문자열로 본다 — 이 시험은 그 기계 검사를 다시 구현하지
    않고, 그 검사도 함께 통과하는 이름을 그대로 재사용해 «제품명 가드»와
    «인과 가드»를 한 fixture에서 동시에 만족시킨다. (한계: 이 기계 검사는 표면
    부분문자열 일치일 뿐이라 동음이의·줄임말은 못 잡는다 — 이 시험이 그 한계를
    넓히지 않는다.)
  - 칸 구성은 4칸(제품·서비스명 / 범위 / 추진 근거 / 사업적 역할)이며, «원인 칸 →
    바로 다음 칸이 결과의 주요 원인»이라는 분할 인과 계약은 «칸 배열에서 값이
    있는 두 칸이 실제로 이웃해 있을 때»만 성립한다(나머지 칸은 비워 둔다).

★ 두 가지 입력 모양
  - 분할양성(shape a): 제품명 칸 = 원인 전체, «바로 다음» 칸 = "결과의 주요 원인"
    전체.
  - 대조양성(shape b): 제품명 칸은 그대로 두고, 다음 칸에는 원인·결과를 나누지
    않은 완결 문장 하나를 통째로 둔다.

★ 실행 여부 — 이 파일은 이번 배정에서 **한 번도 실행되지 않았다**(X모드). 프로덕션이
  방금 동결됐다는 통지를 받았지만, pytest는 여전히 돌리지 않고 그대로 넘긴다는
  지시를 따랐다. 각 기대의 근거는
  ``.local-artifacts/resume-20260910-split-flow-independent-contract/``에 남겼다.
"""

from __future__ import annotations

import json

from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.direct_support_constants import (
    CAUSE_CLAIM_UNCOVERED,
    CAUSE_DIRECTION_REVERSED,
    CAUSE_NEGATED_IN_SOURCE,
    CAUSE_RELATION_NOT_IN_SOURCE,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, FlowRow,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


PRODUCT_NAME = "저비용 서비스"
EFFECT_NAME = "이익 증가"
#: 분할양성 — 제품명 칸(원인)과 «바로 다음» 칸(결과의 주요 원인)만 채우고 나머지는 비운다.
SPLIT_ROW_CELLS = (PRODUCT_NAME, "", "", f"{EFFECT_NAME}의 주요 원인")
#: 대조양성 — 제품명 칸은 그대로 두고, 다음 칸에 나누지 않은 완결 문장 하나를 둔다.
SINGLE_SENTENCE_ROW_CELLS = (
    PRODUCT_NAME, "", "", f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다.",
)
#: 두 문장을 다 담아 제품명 가드(문장1)와 관계 원문 대조(문장2)를 함께 만족하는 자기 인용.
SELF_CITATION_QUOTE = f"회사는 {PRODUCT_NAME}를 제공한다. {PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다."


def _relation(*, source_id: str, quote: str, cause: str = PRODUCT_NAME, effect: str = EFFECT_NAME) -> dict:
    """검수 응답의 «관계» 항목 하나. 키는 검수 응답 JSON 계약 그대로 리터럴로 쓴다."""

    return {"유형": "인과", "근거": source_id, "원인": cause, "결과": effect, "원문": quote}


def _portfolio_report(rows: tuple[FlowRow, ...]) -> ComposedReport:
    return ComposedReport((ComposedSection(PORTFOLIO_TABLE_SECTION_ID, (), flow_rows=rows),))


def _standalone_ask(entries: list[dict]):
    def ask(prompt):
        return json.dumps({"판정": entries}, ensure_ascii=False)
    return ask


# ══════════════════════════════════════════════════════════
# 독립 검수(check_diagrams) — 자기 인용 조건에 따른 핵심 사례
# ══════════════════════════════════════════════════════════


def test_split_form_self_citation_relation_is_kept():
    """분할양성: 제품명 칸(원인) + 바로 다음 칸(결과의 주요 원인)이 자기 인용과 맞으면 보존된다."""

    fragments = (CollectedFragment("own1", "사업내용", SELF_CITATION_QUOTE),)
    row = FlowRow(SPLIT_ROW_CELLS, ("own1",))
    relation = _relation(source_id="own1", quote=f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다.")

    checked, problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )
    assert checked.sections[0].flow_rows == (row,)
    assert problems == ()


def test_single_sentence_form_self_citation_relation_is_kept():
    """대조양성: 제품명 칸은 유지하고 다음 칸에 완결 문장 하나를 둔 경우도 자기 인용이면 보존된다."""

    fragments = (CollectedFragment("own1", "사업내용", SELF_CITATION_QUOTE),)
    row = FlowRow(SINGLE_SENTENCE_ROW_CELLS, ("own1",))
    relation = _relation(source_id="own1", quote=f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다.")

    checked, problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )
    assert checked.sections[0].flow_rows == (row,)
    assert problems == ()


def test_relation_borrowing_another_citation_is_rejected():
    """분할양성이어도 이 줄이 인용하지 않은 다른 조각의 원문을 근거로 대면 거절된다.

    row는 ``own1``만 인용한다(제품명 가드를 통과시키려고 own1에도 제품명은 넣지만
    관계 원문은 담지 않는다). 관계 항목이 ``근거``로 이 줄의 citations 밖인
    ``other1``을 대면 — 원문 글자가 맞아도 이 줄의 인용 목록 밖이라 거절된다.
    """
    fragments = (
        CollectedFragment("own1", "사업내용", f"회사는 {PRODUCT_NAME}를 제공한다."),
        CollectedFragment("other1", "사업내용", SELF_CITATION_QUOTE),
    )
    row = FlowRow(SPLIT_ROW_CELLS, ("own1",))
    relation = _relation(source_id="other1", quote=f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다.")

    checked, problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )
    assert checked.sections[0].flow_rows == ()
    assert len(problems) == 1 and CAUSE_RELATION_NOT_IN_SOURCE in problems[0]


def test_source_direction_reversed_is_rejected():
    """원문이 원인·결과를 반대로 적었으면 분할양성 구조여도 방향 오류로 거절된다."""

    quote = f"회사는 {PRODUCT_NAME}를 제공한다. {EFFECT_NAME}는 {PRODUCT_NAME}의 주요 원인이다."
    fragments = (CollectedFragment("own1", "사업내용", quote),)
    row = FlowRow(SPLIT_ROW_CELLS, ("own1",))
    relation = _relation(source_id="own1", quote=f"{EFFECT_NAME}는 {PRODUCT_NAME}의 주요 원인이다.")

    checked, problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )
    assert checked.sections[0].flow_rows == ()
    assert len(problems) == 1 and CAUSE_DIRECTION_REVERSED in problems[0]


def test_source_negates_the_relation_is_rejected():
    """원문이 그 인과를 부정하면 분할양성 구조여도 부정 원문 사유로 거절된다."""

    quote = f"회사는 {PRODUCT_NAME}를 제공한다. {PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인은 아니다."
    fragments = (CollectedFragment("own1", "사업내용", quote),)
    row = FlowRow(SPLIT_ROW_CELLS, ("own1",))
    relation = _relation(source_id="own1", quote=f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인은 아니다.")

    checked, problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )
    assert checked.sections[0].flow_rows == ()
    assert len(problems) == 1 and CAUSE_NEGATED_IN_SOURCE in problems[0]


def test_second_causal_claim_without_its_own_relation_is_not_smuggled_in():
    """같은 줄 안에 인과 단언이 «둘»이면, 하나만 근거를 대도 줄 전체가 거절된다(부분 승인 없음).

    제품명(원인1) → 이익 증가의 주요 원인(결과1) 뒤에, 남은 두 칸을 또 하나의 분할
    인과(원인2 → 결과2)로 채운다. 관계 항목은 첫 쌍만 뒷받침한다 — 첫 쌍이 실제로는
    정상이어도 둘째 쌍의 근거 결여가 «같은 응답 안»에서 함께 묻히면 안 된다.
    """
    cells = (PRODUCT_NAME, f"{EFFECT_NAME}의 주요 원인", "자재비 절감", "배송비 감소의 주요 원인")
    fragments = (CollectedFragment("own1", "사업내용", SELF_CITATION_QUOTE),)
    row = FlowRow(cells, ("own1",))
    relation = _relation(source_id="own1", quote=f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다.")

    checked, problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )
    assert checked.sections[0].flow_rows == ()
    assert len(problems) == 1 and CAUSE_CLAIM_UNCOVERED in problems[0]


# ══════════════════════════════════════════════════════════
# 묶음(verify_report) 대 독립(check_diagrams) 최소 비교
# ══════════════════════════════════════════════════════════


def test_grouped_and_standalone_paths_apply_the_same_split_form_guard():
    """같은 분할양성 후보를 독립 검수(check_diagrams)와 묶음 검수(verify_report)에 각각 태워도

    같은 판정(보존)이 나와야 한다 — 두 경로의 프롬프트 조립기는 다르지만 줄을
    남기고 지우는 가드(``constrain_verdicts`` → ``direct_support_problem``)는 한 벌이다.
    """
    fragments = (CollectedFragment("own1", "사업내용", SELF_CITATION_QUOTE),)
    row = FlowRow(SPLIT_ROW_CELLS, ("own1",))
    relation = _relation(source_id="own1", quote=f"{PRODUCT_NAME}가 {EFFECT_NAME}의 주요 원인이다.")

    standalone_checked, standalone_problems = check_diagrams(
        _portfolio_report((row,)), fragments,
        _standalone_ask([{"번호": 1, "결과": "참", "검증근거": {"관계": [relation]}}]),
    )

    def grouped_ask(prompt):
        items = review_items(prompt)
        assert items, "묶음 검수 입력에서 후보를 읽지 못했습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations),
             "결과": "참", "검증근거": {"관계": [relation]}}
            for item in items
        ]}, ensure_ascii=False)

    grouped_checked = verify_report(
        _portfolio_report((row,)), fragments, None, grouped_ask,
        allowed_fragment_ids_by_section={PORTFOLIO_TABLE_SECTION_ID: frozenset(("own1",))},
    )

    assert standalone_checked.sections[0].flow_rows == (row,)
    assert standalone_problems == ()
    assert grouped_checked.sections[0].flow_rows == (row,)

"""3장 «제품·서비스 카드»를 지키는 시험 — 1단계.

★ 왜 이 파일이 있나 (2026-09-05 하이브 실측)
  같은 보고서에서 흐름표 «6개»가 전부 화면에 나왔는데 3장만 0건이었다.
  3장 본문에는 제품 이름과 인용이 다 붙어 있었으니 자료 부족이 아니다.
  그런데 저장소 어디에도 「3장 표가 실제로 렌더된다」를 지키는 시험이
  없어서, 0건이 되어도 아무 시험이 빨간불이 되지 않았다.

★ 여기서 지키는 것 네 가지
  ① 작가 안내문 — 스위치 ON에서 3장에만 있던 «이중 조건»이 사라지고
     「근거 하나 이상」 기준과 「칸에 숫자 금지」 규칙이 들어 있다.
  ② 스위치 OFF — 옛 문구가 «한 글자도» 안 바뀌었다(되돌리기 보장).
  ③ 도식 검수 — 카드 장은 「카드」라는 말로 판정을 요구하고, 화살표
     장의 문구는 그대로다.
  ④ FULL 의미 칸 결속 — `portfolio:product_role`을 지원하는 조각이
     있으면 카드가 «파싱→검수→렌더»를 지나 화면 표까지 간다. 없으면
     전멸한다(이게 2단계가 필요한 이유다 — 여기서 잠가 둔다).

  SHADOW 전 사슬(real.py 수집부터 PDF까지)은
  `test_e2e_offline.py::test_이음매_3장_제품_카드도_같은_사슬을_지나_화면과_PDF까지_간다`
  가 따로 지킨다. 이 파일은 그 시험이 못 지나는 FULL 갈래를 맡는다.
"""

from __future__ import annotations

import json
import re
from typing import Iterator

import pytest

from src.core import revenue_table_switch as switch
from src.features.composer import constants as composer_constants
from src.features.composer.constants import (
    FLOW_ARROW_SECTION_IDS,
    FLOW_HEADERS_BY_SECTION,
    FLOW_PROMPT_BY_SECTION,
    GRADE_CONFIRMED,
    PORTFOLIO_TABLE_CAPTION,
    PORTFOLIO_TABLE_GUIDE,
    PORTFOLIO_TABLE_HEADERS,
    PORTFOLIO_TABLE_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.diagram_check import (
    FLOW_REVIEW_ARROW_ROW_NOUN,
    FLOW_REVIEW_CARD_ROW_NOUN,
    FLOW_REVIEW_ROW_NUMBER_PATTERN,
    VERDICT_TRUE,
    check_diagrams,
)
from src.features.composer.logic import compose_selected_sections
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.features.composer.render import render_report

# ★ 골든 리허설 입력(가짜 작가·검수·조각)을 다시 만들지 않고 그대로 쓴다 —
#   run_v2를 끝까지(출고 검증까지) 통과시키려면 아홉 장이 다 채워져야 하고,
#   그 입력은 이미 검증된 한 벌이 있다. 시험끼리 입력을 복제하면 한쪽만
#   고쳐 조용히 갈라진다(test_e2e_offline도 같은 방식으로 재사용한다).
from src.features.composer.tests.test_verify_boundaries import (
    COMPANY_NAME as _GOLDEN_COMPANY,
    _GoldenWriter,
    _ScriptedReviewer,
    _fixture_fragments,
    _golden_sections,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.policy import required_slots_for

_PRODUCT_ROLE_SLOT = "portfolio:product_role"
_REVENUE_LINK_SLOT = "portfolio:revenue_link"
_LIFECYCLE_SLOT = "portfolio:lifecycle_stage"
_COMPANY_ID = "00123456"
_GENERATION = "a" * 64

#: 3장 조각 원문 — 칸 값이 전부 이 안의 말이어야 도식 검수를 지난다.
_PORTFOLIO_TEXT = (
    "주력 그룹 Stray Kids는 북미·유럽·중남미에서 대형 투어를 전개하고, "
    "신보 출시와 구보 판매가 이어지고 있다."
)

#: 카드 한 줄 — 칸에 숫자·퍼센트·연도를 하나도 쓰지 않는다(새 안내문 규칙).
_CARD_CELLS = (
    "Stray Kids",
    "북미·유럽·중남미 대형 투어",
    "신보 출시와 구보 판매 확인",
    "주력 그룹",
)

_FLOW_ROW_NUMBER_RE = re.compile(FLOW_REVIEW_ROW_NUMBER_PATTERN, re.MULTILINE)


# ══════════════════════════════════════════════════════════
# 공통 준비
# ══════════════════════════════════════════════════════════


@pytest.fixture
def 스위치_켬() -> Iterator[None]:
    """이 시험 동안만 1단계 스위치를 «켠 채로» 동결한다.

    스위치는 프로세스 수명 동안 동결되므로, 앞뒤로 초기화하지 않으면
    같은 pytest 프로세스의 다른 시험이 이 값을 물려받는다.
    """

    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    switch.freeze_process_revenue_table_switch(switch.RevenueTableSwitch.ON)
    try:
        yield
    finally:
        switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


@pytest.fixture
def 스위치_끔() -> Iterator[None]:
    """옛 경로(스위치 미선언 = off)를 명시적으로 동결한다."""

    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    switch.freeze_process_revenue_table_switch(switch.RevenueTableSwitch.OFF)
    try:
        yield
    finally:
        switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


def _packet_set(portfolio_slots: tuple[str, ...]) -> SectionEvidencePacketSet:
    """장마다 필수 의미 칸을 채운 packet — 3장 조각의 칸만 시험이 정한다."""

    packets: list[SectionEvidencePacket] = []
    for index, section_id in enumerate(SECTION_IDS, start=1):
        if section_id == PORTFOLIO_TABLE_SECTION_ID:
            slots = portfolio_slots
            text = _PORTFOLIO_TEXT
        else:
            slots = required_slots_for(section_id) or (
                CLAIM_SLOTS_BY_SECTION[section_id][0],
            )
            text = f"테스트 회사의 {section_id} 공식 원문이다."
        packets.append(
            SectionEvidencePacket(
                company_id=_COMPANY_ID,
                evidence_generation_sha256=_GENERATION,
                section_id=section_id,
                fragments=(
                    CollectedFragment(
                        fragment_id=str(index),
                        kind="typed-evidence-v1:test",
                        text=text,
                        source_url=f"https://example.com/documents/{index}",
                        document_identity=f"document:example.com:doc-{index}",
                        document_content_sha256=f"{index:064x}",
                        supported_claim_slots=slots,
                    ),
                ),
            )
        )
    return SectionEvidencePacketSet(
        company_id=_COMPANY_ID,
        evidence_generation_sha256=_GENERATION,
        packets=tuple(packets),
    )


def _portfolio_writer_response(fragment_id: str) -> str:
    """3장 작가 응답 — 문장 1개 + 카드 1줄."""

    return json.dumps(
        {
            "문장들": [
                {
                    "글": "회사는 주력 그룹의 대형 투어를 이어 가고 있다.",
                    "인용": [fragment_id],
                    "등급": GRADE_CONFIRMED,
                    "주장슬롯": _PRODUCT_ROLE_SLOT,
                }
            ],
            "경로표": [{"칸": list(_CARD_CELLS), "인용": [fragment_id]}],
        },
        ensure_ascii=False,
    )


def _compose_portfolio(portfolio_slots: tuple[str, ...]) -> ComposedReport:
    """FULL packet 경로로 3장만 한 번 쓴다 (AI 1회, 네트워크 0회)."""

    fragment_id = str(SECTION_IDS.index(PORTFOLIO_TABLE_SECTION_ID) + 1)
    return compose_selected_sections(
        "테스트 회사",
        None,
        lambda _prompt: _portfolio_writer_response(fragment_id),
        section_evidence_packets=_packet_set(portfolio_slots),
        section_ids=(PORTFOLIO_TABLE_SECTION_ID,),
    )


def _portfolio_fragment_objs() -> tuple[CollectedFragment, ...]:
    fragment_id = str(SECTION_IDS.index(PORTFOLIO_TABLE_SECTION_ID) + 1)
    return (
        CollectedFragment(
            fragment_id=fragment_id, kind="사업내용", text=_PORTFOLIO_TEXT
        ),
    )


def _all_true_reviewer(prompt: str) -> str:
    numbers = [int(value) for value in _FLOW_ROW_NUMBER_RE.findall(prompt)]
    return json.dumps(
        {"판정": [{"번호": number, "결과": VERDICT_TRUE} for number in numbers]},
        ensure_ascii=False,
    )


# ══════════════════════════════════════════════════════════
# ① 작가 안내문 — 이중 조건이 사라졌다 (스위치 ON)
# ══════════════════════════════════════════════════════════


def test_스위치가_켜지면_3장_안내문에_우선_신호_2개_요구가_없다(
    스위치_켬: None,
) -> None:
    """★ 이게 3장만 0건이던 «1순위» 원인이다.

    신호를 2개 세는 코드는 v2 어디에도 없다(grep 0건). 작가에게만 바를
    높여 두고 아무도 안 지켜 주는 문장이었다.
    """
    지침 = FLOW_PROMPT_BY_SECTION[PORTFOLIO_TABLE_SECTION_ID]

    assert "우선 신호를 2개" not in 지침
    assert "우선 신호 2개" not in 지침
    assert "실행 신호가 없으면" not in 지침
    # 대신 «하나 이상» 기준이 그 자리에 있어야 한다 — 문장을 통째로
    # 지워도 통과하는 시험은 소용없다.
    assert "하나 이상 있으면" in 지침
    assert "매출 기여 확인" in 지침


def test_스위치가_켜지면_3장_안내문이_칸에_숫자를_쓰지_말라고_말한다(
    스위치_켬: None,
) -> None:
    """★ 수치 검사를 «완화»하는 대신 작가가 수를 안 쓰게 한다.

    도식 수치 검사는 칸의 수가 인용 원문에 글자 그대로 없으면 그 줄을
    통째로 버린다. 「중점 추진 근거」는 연도·퍼센트를 부르기 딱 좋은 칸이라
    3장이 조용히 전멸할 수 있는 자리였다.
    """
    지침 = FLOW_PROMPT_BY_SECTION[PORTFOLIO_TABLE_SECTION_ID]

    assert "숫자·퍼센트·연도를 쓰지 않는다" in 지침
    assert "2장·4장" in 지침


def test_스위치가_켜지면_3장_안내문이_수익을_만드는_제품이라고_말한다(
    스위치_켬: None,
) -> None:
    """★ 「지금 미는 제품」은 회사의 «의지»를 묻는 말이라 근거가 잘 안 붙는다.

    3장이 실제로 답해야 하는 것은 「지금 무엇을 팔아 돈을 버나」다.
    """
    지침 = FLOW_PROMPT_BY_SECTION[PORTFOLIO_TABLE_SECTION_ID]

    assert "수익을 실제로 만드는" in 지침


def test_새_안내문에도_남의_칸_이름이_새지_않는다(스위치_켬: None) -> None:
    """★ 안내문을 고칠 때 다른 장의 칸 이름이 새면 작가가 엉뚱한 칸을 채운다.

    기존 `test_section_tables.py`의 같은 검사는 스위치 OFF 문구만 본다
    (기본값이 off라서다) — 새 문구도 같은 잣대로 잰다.
    """
    지침 = FLOW_PROMPT_BY_SECTION[PORTFOLIO_TABLE_SECTION_ID]
    남의칸 = {
        name
        for other, headers in FLOW_HEADERS_BY_SECTION.items()
        if other != PORTFOLIO_TABLE_SECTION_ID
        for name in headers
    } - set(PORTFOLIO_TABLE_HEADERS)
    # 남의 칸이 «내 칸 이름의 부분»이면 leak이 아니다(2장 「제품·서비스」는
    # 3장 「제품·서비스명」의 부분 문자열이다).
    남의칸 = {
        name
        for name in 남의칸
        if not any(name in own for own in PORTFOLIO_TABLE_HEADERS)
    }

    샌_것 = [name for name in 남의칸 if name in 지침]
    assert not 샌_것, f"3장 새 안내문에 남의 칸 이름이 샜습니다: {샌_것}"


def test_새_안내문에도_내_칸_이름_네_개가_그대로_있다(스위치_켬: None) -> None:
    지침 = FLOW_PROMPT_BY_SECTION[PORTFOLIO_TABLE_SECTION_ID]

    for name in PORTFOLIO_TABLE_HEADERS:
        assert name in 지침, f"3장 새 안내문에 「{name}」이 없습니다"


# ══════════════════════════════════════════════════════════
# ② 스위치 OFF — 옛 문구가 한 글자도 안 바뀐다 (되돌리기 보장)
# ══════════════════════════════════════════════════════════


def test_스위치가_꺼지면_옛_안내문_문구를_글자_그대로_쓴다(
    스위치_끔: None,
) -> None:
    """★ 롤백은 «스위치를 안 켜는 것»뿐이어야 한다 — 코드 되감기 없이.

    옛 문구가 조금이라도 달라지면 되돌려도 예전 동작이 아니다.
    """
    지침 = FLOW_PROMPT_BY_SECTION[PORTFOLIO_TABLE_SECTION_ID]

    assert 지침.startswith(PORTFOLIO_TABLE_GUIDE)
    # 옛 문구의 이중 조건이 «그대로» 남아 있다.
    assert "«서로 다른» 실제 우선 신호를 2개 이상 쓴다" in 지침
    assert "실행 신호가 없으면 그 제품은 줄에 넣지 않는다" in 지침
    assert composer_constants.portfolio_table_guide() is PORTFOLIO_TABLE_GUIDE


def test_캡션과_머리글은_스위치와_무관하게_같은_글자다(
    스위치_켬: None,
) -> None:
    """★ 문서·렌더러·카드 판정이 이 글자를 그대로 참조한다 — 안 건드린다."""

    assert PORTFOLIO_TABLE_CAPTION == "지금 무엇을 미는가 — 핵심 제품·서비스와 역할"
    assert PORTFOLIO_TABLE_HEADERS == (
        "제품·서비스명",
        "제품·서비스 범위",
        "중점 추진 근거",
        "사업적 역할",
    )


# ══════════════════════════════════════════════════════════
# ③ 도식 검수 — 카드 장은 「카드」로 묻는다
# ══════════════════════════════════════════════════════════


def _card_section(rows: tuple[FlowRow, ...]) -> ComposedReport:
    fragment_id = str(SECTION_IDS.index(PORTFOLIO_TABLE_SECTION_ID) + 1)
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id=PORTFOLIO_TABLE_SECTION_ID,
                sentences=(
                    ComposedSentence(
                        text="회사는 주력 그룹의 대형 투어를 이어 가고 있다.",
                        citations=(fragment_id,),
                        grade=GRADE_CONFIRMED,
                    ),
                ),
                flow_rows=rows,
            ),
        )
    )


def _기록하는_검수():
    기록: list[str] = []

    def ask(prompt: str) -> str:
        기록.append(prompt)
        return _all_true_reviewer(prompt)

    ask.기록 = 기록  # type: ignore[attr-defined]
    return ask


def test_3장은_카드_장이라_검수_프롬프트가_카드로_묻는다() -> None:
    """★ 카드에는 이을 상대가 없다 — 화살표를 찾으라고 하면 오심이 난다."""
    assert PORTFOLIO_TABLE_SECTION_ID not in FLOW_ARROW_SECTION_IDS

    ask = _기록하는_검수()
    보고서, 사유 = check_diagrams(
        _card_section((FlowRow(cells=_CARD_CELLS, citations=("3",)),)),
        _portfolio_fragment_objs(),
        ask,
    )

    프롬프트 = ask.기록[0]
    assert f"] {FLOW_REVIEW_CARD_ROW_NOUN}(JSON 배열):" in 프롬프트
    assert f"] {FLOW_REVIEW_ARROW_ROW_NOUN}(JSON 배열):" not in 프롬프트
    assert "화살표가 없다" in 프롬프트
    # 판정이 «참»이면 카드가 남는다.
    제품장 = next(s for s in 보고서.sections if s.section_id == PORTFOLIO_TABLE_SECTION_ID)
    assert len(제품장.flow_rows) == 1, f"카드가 사라졌습니다: {사유}"


def test_화살표_장만_있으면_검수_문구가_예전과_같다() -> None:
    """★ 6개 흐름표는 지금 잘 나온다 — 카드 안내를 섞어 흔들지 않는다."""
    ask = _기록하는_검수()
    운영장 = ComposedReport(
        sections=(
            ComposedSection(
                section_id="operations_partners",
                sentences=(
                    ComposedSentence(
                        text="음반 유통은 파트너와 협력한다.",
                        citations=("3",),
                        grade=GRADE_CONFIRMED,
                    ),
                ),
                flow_rows=(
                    FlowRow(
                        cells=("연습생", "캐스팅·트레이닝", "데뷔 아티스트"),
                        citations=("3",),
                    ),
                ),
            ),
        )
    )

    check_diagrams(운영장, _portfolio_fragment_objs(), ask)

    프롬프트 = ask.기록[0]
    assert f"] {FLOW_REVIEW_ARROW_ROW_NOUN}(JSON 배열):" in 프롬프트
    assert FLOW_REVIEW_CARD_ROW_NOUN + "(JSON 배열)" not in 프롬프트
    assert "화살표가 없다" not in 프롬프트


def test_카드_칸에_근거_없는_수를_쓰면_그_줄이_빠진다() -> None:
    """★ 수치 검사는 «완화하지 않는다» — 새 안내문이 수를 막는 쪽이다."""
    수가_든_카드 = FlowRow(
        cells=("Stray Kids", "북미·유럽 투어", "신보 12장 출시", "주력 그룹"),
        citations=("3",),
    )

    보고서, 사유 = check_diagrams(
        _card_section((수가_든_카드,)),
        _portfolio_fragment_objs(),
        _all_true_reviewer,
    )

    제품장 = next(s for s in 보고서.sections if s.section_id == PORTFOLIO_TABLE_SECTION_ID)
    assert 제품장.flow_rows == ()
    assert any("인용 원문에 없는 수" in 이유 for 이유 in 사유), 사유


# ══════════════════════════════════════════════════════════
# ④ FULL 의미 칸 결속 — product_role이 있으면 화면 표까지 간다
# ══════════════════════════════════════════════════════════


def test_FULL에서_product_role_근거가_있으면_3장_카드가_화면_표까지_간다() -> None:
    """★★ FULL 갈래의 전 사슬 — 프롬프트→파싱→의미결속→검수→렌더.

    (SHADOW 전 사슬은 `test_e2e_offline.py`의 3장 이음매 시험이 맡는다.
     FULL은 real.py의 정식 수집기가 있어야 돌아가므로 여기서는 composer
     계층의 FULL 계약만 끝까지 지난다.)
    """
    composed = _compose_portfolio(
        (_PRODUCT_ROLE_SLOT, _REVENUE_LINK_SLOT, _LIFECYCLE_SLOT)
    )
    제품장 = next(
        s for s in composed.sections if s.section_id == PORTFOLIO_TABLE_SECTION_ID
    )
    assert len(제품장.flow_rows) == 1, (
        "FULL 의미 칸 결속이 카드를 버렸습니다 — logic.py의 3장 칸별 "
        "지원 슬롯 규칙을 확인하세요."
    )

    검수된, 사유 = check_diagrams(
        composed, _portfolio_fragment_objs(), _all_true_reviewer
    )
    검수후 = next(
        s for s in 검수된.sections if s.section_id == PORTFOLIO_TABLE_SECTION_ID
    )
    assert len(검수후.flow_rows) == 1, f"도식 검증이 카드를 버렸습니다: {사유}"

    fragment_id = str(SECTION_IDS.index(PORTFOLIO_TABLE_SECTION_ID) + 1)
    rendered = render_report(
        "테스트 회사",
        검수된,
        {int(fragment_id): {"종류": "사업내용", "원문": _PORTFOLIO_TEXT}},
        None,
    )
    화면장 = next(
        s for s in rendered.sections if s.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    assert 화면장.tables, "렌더가 3장 표를 만들지 않았습니다"
    assert 화면장.tables[0].caption == PORTFOLIO_TABLE_CAPTION
    assert 화면장.tables[0].headers == list(PORTFOLIO_TABLE_HEADERS)
    assert list(화면장.tables[0].rows[0]) == list(_CARD_CELLS)


def test_FULL에서_product_role_근거가_없으면_3장_카드가_전멸한다() -> None:
    """★ 2단계가 필요한 이유를 «시험으로» 남긴다 (1단계 범위 밖).

    3장 표 1번 칸 「제품·서비스명」은 `portfolio:product_role` 하나만
    지원 슬롯으로 받는다. 하이브 원문에 그 어휘가 0회라 FULL로 올리면
    이 줄이 반드시 전멸한다 — 어휘·정책 변경은 2단계 과제다.
    """
    composed = _compose_portfolio((_REVENUE_LINK_SLOT, _LIFECYCLE_SLOT))
    제품장 = next(
        s for s in composed.sections if s.section_id == PORTFOLIO_TABLE_SECTION_ID
    )

    assert 제품장.flow_rows == ()


# ══════════════════════════════════════════════════════════
# ⑤ 도식 버림 사유가 결과에도 남는다
# ══════════════════════════════════════════════════════════


def test_도식_버림_사유가_로그뿐_아니라_결과에도_실린다() -> None:
    """★ 9월 실측에서 사유가 로그에만 있어 원인을 못 갈랐다.

    「작가가 안 냈다」와 「우리가 걸렀다」를 가르는 유일한 표식이 이 목록
    이었는데 저장된 실행 기록에는 없었다. 골든 리허설 입력을 그대로 쓰고
    3장 응답에만 «근거에 없는 수»가 든 카드를 심는다.
    """
    sections = _golden_sections()
    sections[PORTFOLIO_TABLE_SECTION_ID]["경로표"] = [
        {
            # 조각 10 원문에 「5개 신보」·「42만 장」은 있어도 12는 없다.
            "칸": ["Stray Kids", "북미·유럽 투어", "신보 12장 출시", "주력 그룹"],
            "인용": ["10"],
        }
    ]

    output = run_v2(
        _GOLDEN_COMPANY,
        _fixture_fragments(),
        None,
        writer_ask=_GoldenWriter(sections),
        reviewer_ask=_ScriptedReviewer(),
    )

    assert output.diagram_drop_reasons, (
        "도식 버림 사유가 결과에 없습니다 — 다음 진단도 서버 로그가 있어야만 "
        "가능해집니다."
    )
    assert any(
        PORTFOLIO_TABLE_SECTION_ID in 이유 and "인용 원문에 없는 수" in 이유
        for 이유 in output.diagram_drop_reasons
    ), output.diagram_drop_reasons


def test_도식을_하나도_안_버리면_사유가_비어_있다() -> None:
    """★ 「비어 있음」이 「기록을 안 했음」과 구별돼야 진단에 쓸 수 있다."""
    output = run_v2(
        _GOLDEN_COMPANY,
        _fixture_fragments(),
        None,
        writer_ask=_GoldenWriter(),
        reviewer_ask=_ScriptedReviewer(),
    )

    assert output.diagram_drop_reasons == ()


def test_수가_없는_3장_카드는_run_v2를_지나_보고서_표까지_간다() -> None:
    """★ 새 안내문대로 «수를 안 쓴» 카드는 같은 사슬을 통과한다.

    바로 위 시험과 짝이다 — 떨어지는 이유가 「3장이라서」가 아니라
    「근거에 없는 수를 썼기 때문」임을 두 시험이 함께 못 박는다.
    """
    sections = _golden_sections()
    sections[PORTFOLIO_TABLE_SECTION_ID]["경로표"] = [
        {"칸": list(_CARD_CELLS), "인용": ["10"]}
    ]

    output = run_v2(
        _GOLDEN_COMPANY,
        _fixture_fragments(),
        None,
        writer_ask=_GoldenWriter(sections),
        reviewer_ask=_ScriptedReviewer(),
    )

    제품장 = next(
        s for s in output.report.sections if s.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    assert 제품장.tables, (
        f"3장 표가 사라졌습니다 — 버림 사유: {output.diagram_drop_reasons}"
    )
    assert 제품장.tables[0].caption == PORTFOLIO_TABLE_CAPTION
    assert len(제품장.tables[0].rows) == 1
    assert output.diagram_drop_reasons == ()

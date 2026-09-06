"""3장 카드가 packet의 «대표 이름»을 실제로 썼는지 본다 — 업종을 가리지 않는다.

★ 이 시험이 지키는 것 (2026-09-06 실측):
  ① 조각 줄에 «원문위치»가 실린다 — 안내문이 그 표기를 보고 고르라고
     지시하므로, 안 실리면 지킬 수 없는 지시가 된다.
  ② 3장 안내문이 이름 카드를 «반드시»로 요구하고, 줄 편입 기준에서 빼 주며,
     부문명만 쓰고 끝내지 말라고 말한다.
  ③ 이름을 하한 이상 줬는데 카드가 하나도 안 쓰면 그 사실이 기록된다.
  ④ ①~③이 엔터·제조·금융 어디서 온 이름이든 같게 동작한다.

★ 픽스처의 이름은 «가공 이름»이다. 실존 회사·그룹·상품 이름을 시험에 박으면
  지워도 되돌아온다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.features.composer.constants import (
    PORTFOLIO_TABLE_GUIDE_V2,
    PORTFOLIO_TABLE_HEADERS,
    PORTFOLIO_TABLE_SECTION_ID,
    PROMPT_FRAGMENT_LOCATION_LABEL,
)
from src.features.composer.diagram_check import (
    FLOW_REVIEW_PROMPT_HEADER,
    FLOW_REVIEW_ROW_NUMBER_PATTERN,
)
from src.features.composer.logic import build_section_prompt
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.portfolio_names import (
    MIN_REPRESENTATIVE_NAMES_FOR_CARD,
    portfolio_name_usage,
    representative_names,
)
from src.shared.name_fragments import (
    NAME_KIND_LABELS,
    NAME_KIND_SEGMENT,
    NAME_LABEL_SEPARATOR,
    NAME_VALUE_SEPARATOR,
    REPRESENTATIVE_NAME_LABELS,
    representative_label_and_name as label_and_name,
)


IP_LABEL, PRODUCT_LABEL, BRAND_LABEL = REPRESENTATIVE_NAME_LABELS
SEGMENT_LABEL = NAME_KIND_LABELS[NAME_KIND_SEGMENT]

#: 업종 세 가지의 «가공» 이름 표. (표 제목, 종류 라벨, 행 원문, 이름)
_ENTERTAINMENT = (
    "나. 주요 아티스트 전속계약",
    IP_LABEL,
    ("㈜가나다뮤직 | 하늘소년단", "㈜가나다뮤직 | 바다소녀들", "㈜라마바뮤직 | 별무리"),
    ("하늘소년단", "바다소녀들", "별무리"),
)
_MANUFACTURING = (
    "가. 주요 제품 및 서비스의 현황",
    PRODUCT_LABEL,
    (
        "메모리 | 가람메모리 D9, 나래메모리 D7 | 1,204 | 41.2%",
        "메모리 | 가람메모리 D9, 나래메모리 D7 | 1,204 | 41.2%",
        "파운드리 | 다솜공정 4나노 | 902 | 30.9%",
    ),
    ("가람메모리 D9", "나래메모리 D7", "다솜공정 4나노"),
)
_CARD_ISSUER = (
    "가. 주요 상품의 현황",
    PRODUCT_LABEL,
    (
        "신용카드 | 라온카드 포인트형 | 320 | 18.4%",
        "신용카드 | 마루카드 캐시백형 | 285 | 16.1%",
        "체크카드 | 바람체크카드 | 141 | 8.0%",
    ),
    ("라온카드 포인트형", "마루카드 캐시백형", "바람체크카드"),
)
_INDUSTRIES = {
    "엔터": _ENTERTAINMENT,
    "제조": _MANUFACTURING,
    "카드사": _CARD_ISSUER,
}


def _location(title: str, row: int, label: str, name: str) -> str:
    """`product_names/fragments.py`가 만드는 원문위치 모양 그대로."""

    return (
        f"{title} · {row}행"
        f"{NAME_LABEL_SEPARATOR}{label}{NAME_VALUE_SEPARATOR}{name}"
    )


def _name_fragment(
    fragment_id: str, title: str, label: str, text: str, name: str, *, row: int
) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="dart_business_report",
        text=text,
        location=_location(title, row, label, name),
        supported_claim_slots=("portfolio:product_role",),
    )


def _industry_fragments(industry: str, count: int | None = None):
    title, label, texts, names = _INDUSTRIES[industry]
    limit = len(names) if count is None else count
    return tuple(
        _name_fragment(
            str(index + 1), title, label, texts[index], names[index], row=index * 3 + 2
        )
        for index in range(limit)
    )


def _report(*rows: FlowRow) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id=PORTFOLIO_TABLE_SECTION_ID,
                sentences=(
                    ComposedSentence(
                        text="회사는 공식 제품을 운영한다.",
                        citations=("1",),
                        grade="확인",
                    ),
                ),
                flow_rows=rows,
            ),
        )
    )


# ══════════════════════════════════════════════════════════
# ① 조각 줄에 원문위치가 실린다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_작가_프롬프트의_조각줄이_원문위치와_이름을_보여_준다(industry: str) -> None:
    fragments = _industry_fragments(industry)

    prompt = build_section_prompt(
        "가나다회사", PORTFOLIO_TABLE_SECTION_ID, fragments, None
    )

    assert f"{PROMPT_FRAGMENT_LOCATION_LABEL}: " in prompt
    for fragment in fragments:
        assert fragment.location in prompt
        # ★ 표기의 «: 뒤»가 이름이라고 안내문이 말하므로 이름도 프롬프트에 있다.
        assert label_and_name(fragment.location)[1] in prompt


def test_원문위치가_없는_조각은_이름표를_붙이지_않는다() -> None:
    fragment = CollectedFragment(
        fragment_id="1", kind="홈페이지", text="회사 소개 문장이다."
    )

    prompt = build_section_prompt(
        "가나다회사", PORTFOLIO_TABLE_SECTION_ID, (fragment,), None
    )

    assert f"{PROMPT_FRAGMENT_LOCATION_LABEL}: " not in prompt


def test_원문위치는_지원_주장슬롯_앞에_온다() -> None:
    """뒤에 붙이면 조각 줄을 괄호 끝까지 읽는 기존 시험이 슬롯으로 오인한다."""

    prompt = build_section_prompt(
        "가나다회사",
        PORTFOLIO_TABLE_SECTION_ID,
        _industry_fragments("엔터", 1),
        None,
        show_supported_claim_slots=True,
    )
    line = next(line for line in prompt.splitlines() if "[조각 1]" in line)

    assert line.index(PROMPT_FRAGMENT_LOCATION_LABEL) < line.index("지원 주장슬롯")


# ══════════════════════════════════════════════════════════
# ② 안내문이 이름 카드를 강제한다 — 업종 이름은 넣지 않는다
# ══════════════════════════════════════════════════════════


def test_3장_안내문은_세_종류를_열거하고_이름_카드를_반드시로_요구한다() -> None:
    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "「대표 IP」·「제품」·「브랜드」 표기가 있는 조각이 있으면" in guide
    assert "카드 하나는 «반드시» 그 이름들을 담는다" in guide
    assert "«원문 그대로, 3~6개, 원문에 나온 순서대로» 나열한다" in guide
    assert "이름을 줄이거나 번역하거나 새로 짓지 않는다" in guide
    for label in REPRESENTATIVE_NAME_LABELS:
        assert label in guide


def test_3장_안내문은_부문명만_쓰고_끝내지_말라고_말한다() -> None:
    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "사업부문 이름«만» 쓰고 끝내지 않는다" in guide


def test_안내문은_이름_안의_수를_예외로_두고_전부_인용하라고_말한다() -> None:
    """★ 「어느 칸에도 수를 쓰지 마라」와 「이름을 원문 그대로 써라」는 모델명에
    모델 번호가 든 업종에서 정면으로 부딪친다. 예외를 명시하지 않으면 작가가
    이름에서 수를 빼거나(원문 위반) 줄이 통째로 빠진다(실측 재현)."""

    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "예외는 «이름 안에 든 수»뿐이다" in guide
    assert "이름에서 수를 빼거나 고치지 않는다" in guide
    assert "그 이름들이 실린 조각을 «모두» 인용한다" in guide


def test_이름_카드는_줄편입_기준에서_빠진다() -> None:
    """이름뿐인 조각은 「매출 기여·실행 근거」를 만족할 수 없다."""

    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "그 이름 카드는 아래 «줄에 넣을 기준»을 적용받지 않는다" in guide
    assert "카드는 최대 3개 그대로이며, 이름 카드가 그중 하나를 차지한다" in guide


def test_카드_제목은_인용_조각에_있는_이름으로만_쓰라고_말한다() -> None:
    """★ 지어낸 제목은 `diagram_check`가 «조용히» 비운다."""

    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "«이 카드가 인용한 조각에 글자 그대로 있는» 이름만 쓴다" in guide
    assert "인용 조각에 없는 제목을 지어내면 그 칸은 «비워진다»" in guide


# ══════════════════════════════════════════════════════════
# ③ 이름 읽기와 사용 판정
# ══════════════════════════════════════════════════════════


def test_원문위치_표기에서_종류와_이름을_읽는다() -> None:
    location = _location("가. 주요 제품 및 서비스의 현황", 3, PRODUCT_LABEL, "가람메모리 D9")

    assert label_and_name(location) == (PRODUCT_LABEL, "가람메모리 D9")


def test_대표_이름이_아닌_표기는_읽지_않는다() -> None:
    """사업부문·종속회사·주요 계약은 이 규칙의 충족 근거가 아니다."""

    assert label_and_name(
        _location("가. 주요 제품 및 서비스의 현황", 3, SEGMENT_LABEL, "메모리")
    ) == ("", "")
    assert label_and_name("사업의 내용") == ("", "")
    assert label_and_name("") == ("", "")


def test_이름_안에_구분자가_또_있어도_마지막_표기를_기준으로_자른다() -> None:
    location = _location("가. 표", 2, BRAND_LABEL, f"사슴{NAME_LABEL_SEPARATOR}달")

    assert label_and_name(location) == (BRAND_LABEL, f"사슴{NAME_LABEL_SEPARATOR}달")


def test_같은_이름이_여러_행에_있어도_한_번만_센다() -> None:
    title, label, texts, names = _MANUFACTURING
    duplicated = (
        _name_fragment("1", title, label, texts[0], names[0], row=2),
        _name_fragment("2", title, label, texts[0], names[0], row=5),
        _name_fragment("3", title, label, texts[1], names[1], row=5),
    )

    assert representative_names(duplicated) == (
        (label, names[0]),
        (label, names[1]),
    )


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_카드가_이름을_쓰면_미사용이_아니다(industry: str) -> None:
    fragments = _industry_fragments(industry)
    _title, _label, _texts, names = _INDUSTRIES[industry]
    row = FlowRow(
        cells=(names[0], ", ".join(names), "이름 표에 실린 대상이다", "주력"),
        citations=("1", "2", "3"),
    )

    usage = portfolio_name_usage(_report(row), fragments)

    assert usage.name_count == len(names)
    assert usage.should_have_used is True
    assert usage.used_names == names
    assert usage.unused is False


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_부문명만_쓴_카드는_미사용이다(industry: str) -> None:
    """★ 사업부문은 충족 근거로 세지 않는다 — 그래서 이 카드는 미충족이다."""

    fragments = _industry_fragments(industry)
    row = FlowRow(
        cells=("사업부문 하나", "부문 설명", "운영 확대", "주력"), citations=("1",)
    )

    usage = portfolio_name_usage(_report(row), fragments)

    assert usage.used_names == ()
    assert usage.unused is True
    assert usage.name_count == len(_INDUSTRIES[industry][3])
    assert sum(usage.counts_by_label.values()) == usage.name_count


def test_종류별_이름수가_라벨로_갈린다() -> None:
    fragments = _industry_fragments("엔터", 2) + tuple(
        _name_fragment(
            str(index + 10),
            _MANUFACTURING[0],
            PRODUCT_LABEL,
            _MANUFACTURING[2][index],
            _MANUFACTURING[3][index],
            row=index * 3 + 2,
        )
        for index in range(2)
    )

    usage = portfolio_name_usage(_report(), fragments)

    assert usage.counts_by_label == {IP_LABEL: 2, PRODUCT_LABEL: 2}
    assert usage.unused is True


def test_카드가_아예_없어도_미사용이다() -> None:
    usage = portfolio_name_usage(_report(), _industry_fragments("엔터"))

    assert usage.unused is True


@pytest.mark.parametrize("count", range(MIN_REPRESENTATIVE_NAMES_FOR_CARD))
def test_하한보다_적게_왔으면_안_써도_미사용이_아니다(count: int) -> None:
    """이름이 하나뿐이면 그 하나가 회사를 대표한다고 단정하지 않는다."""

    row = FlowRow(
        cells=("사업부문 하나", "부문 설명", "운영 확대", "주력"), citations=("1",)
    )

    usage = portfolio_name_usage(_report(row), _industry_fragments("엔터", count))

    assert usage.name_count == count
    assert usage.should_have_used is False
    assert usage.unused is False


def test_두_칸_경계에_걸친_글자는_이름을_쓴_것이_아니다() -> None:
    row = FlowRow(
        cells=("하늘", "소년단 관련 사업", "운영 확대", "주력"), citations=("1",)
    )

    usage = portfolio_name_usage(_report(row), _industry_fragments("엔터"))

    assert usage.used_names == ()
    assert usage.unused is True


def test_다른_장의_표는_3장_판정에_끼지_않는다() -> None:
    report = ComposedReport(
        sections=(
            ComposedSection(
                section_id=PORTFOLIO_TABLE_SECTION_ID,
                sentences=(),
                flow_rows=(
                    FlowRow(
                        cells=("사업부문 하나", "부문 설명", "운영 확대", "주력"),
                        citations=("1",),
                    ),
                ),
            ),
            ComposedSection(
                section_id="operations_partners",
                sentences=(),
                flow_rows=(
                    FlowRow(cells=("하늘소년단", "공연 운영", "고객"), citations=("1",)),
                ),
            ),
        )
    )

    usage = portfolio_name_usage(report, _industry_fragments("엔터"))

    assert usage.used_names == ()
    assert usage.unused is True


# ══════════════════════════════════════════════════════════
# ④ run_v2 배선 — 세 업종 모두 같은 사슬을 지난다
# ══════════════════════════════════════════════════════════


def _run_fragments(industry: str) -> dict[int, dict[str, str]]:
    """flat(SHADOW) 조각 — 1·2는 일반 근거, 3부터는 이름 표."""

    title, label, texts, names = _INDUSTRIES[industry]
    frags: dict[int, dict[str, str]] = {
        1: {"종류": "사업내용", "원문": "가나다회사는 사업부문 하나를 운영한다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객 존중을 핵심 가치로 삼는다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
    }
    for index, name in enumerate(names):
        frags[index + 3] = {
            "종류": "사업내용",
            "원문": texts[index],
            "원문위치": _location(title, index * 3 + 2, label, name),
        }
    return frags


class _PortfolioWriter:
    """3장에만 카드를 내는 가짜 작가. 카드 첫 두 칸은 인자로 받는다."""

    def __init__(
        self, name_cell: str, scope_cell: str, citations: tuple[str, ...]
    ) -> None:
        self._name_cell = name_cell
        self._scope_cell = scope_cell
        self._citations = citations
        self.portfolio_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if "핵심 요약" in prompt:
            return json.dumps(
                {
                    "문장들": [
                        {"글": f"요약 {mark} 문장이다.", "인용": ["1"], "등급": "확인"}
                        for mark in ("가", "나", "다")
                    ]
                },
                ensure_ascii=False,
            )
        payload: dict[str, object] = {
            "문장들": [
                {
                    "글": "가나다회사는 사업부문 하나를 운영한다.",
                    "인용": ["1"],
                    "등급": "확인",
                }
            ]
        }
        if PORTFOLIO_TABLE_HEADERS[0] in prompt:
            self.portfolio_prompts.append(prompt)
            payload["경로표"] = [
                {
                    "칸": [
                        self._name_cell,
                        self._scope_cell,
                        "이름 표에 실린 대상이다",
                        "주력",
                    ],
                    "인용": list(self._citations),
                }
            ]
        return json.dumps(payload, ensure_ascii=False)


class _AlwaysTrueReviewer:
    """문장 검수·도식 검수 프롬프트의 번호를 전부 «참»으로 돌려준다."""

    def __call__(self, prompt: str) -> str:
        if FLOW_REVIEW_PROMPT_HEADER in prompt:
            numbers = re.findall(
                FLOW_REVIEW_ROW_NUMBER_PATTERN, prompt, flags=re.MULTILINE
            )
        else:
            numbers = re.findall(r"\[(\d+)\] \(등급: [^,\n]+, 인용:", prompt)
        return json.dumps(
            {"판정": [{"번호": int(value), "결과": "참"} for value in numbers]},
            ensure_ascii=False,
        )


def _run(industry: str, writer: _PortfolioWriter):
    return run_v2(
        "가나다회사",
        _run_fragments(industry),
        None,
        writer_ask=writer,
        reviewer_ask=_AlwaysTrueReviewer(),
        corp_type="상장사",
        as_of_date="2026-09-06",
    )


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_이름을_안_쓴_카드는_실행결과에_이름수와_종류별이_실린다(industry: str) -> None:
    _title, label, _texts, names = _INDUSTRIES[industry]
    writer = _PortfolioWriter("사업부문 하나", "부문 설명", ("1",))

    output = _run(industry, writer)

    assert writer.portfolio_prompts, "3장 프롬프트가 한 번도 안 왔다"
    assert output.unused_portfolio_name_count == len(names)
    assert output.unused_portfolio_name_counts_by_label == ((label, len(names)),)


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_이름을_쓴_카드는_실행결과가_0이다(industry: str) -> None:
    _title, _label, _texts, names = _INDUSTRIES[industry]
    # ★ 이름을 여럿 나열하면 그 이름들이 실린 조각을 «모두» 인용해야 한다.
    #   안 그러면 이름 안의 수(모델명 등)가 「근거에 없는 수」로 잡혀 줄이
    #   통째로 빠진다 — 안내문이 그렇게 지시하는 이유가 이것이다.
    citations = tuple(str(index + 3) for index in range(len(names)))
    writer = _PortfolioWriter(names[0], ", ".join(names), citations)

    output = _run(industry, writer)

    assert writer.portfolio_prompts
    # ★ 「카드가 통째로 버려져서 0」이 아님을 못 박는다 — 그러면 위 시험과
    #   짝이 성립하지 않고, 두 시험이 같은 이유로 초록이 된다.
    제품장 = next(
        section
        for section in output.report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    assert 제품장.tables, (
        f"3장 카드가 사라졌습니다 — 버림 사유: {output.diagram_drop_reasons}"
    )
    assert any(names[0] in cell for cell in 제품장.tables[0].rows[0]), (
        제품장.tables[0].rows
    )
    assert output.unused_portfolio_name_count == 0
    assert output.unused_portfolio_name_counts_by_label == ()

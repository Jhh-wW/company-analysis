"""ComposedReport → 기존 렌더 파이프 입력(pipeline Report) 변환 (엔진 v2 소단계 3-4a).

★ 목표(04장 3-4절 1항): 검증까지 끝난 ComposedReport를 웹(result.html)·PDF
  (export_pdf/logic.py)·Notion이 «이미 소비하는» 공용 구조로 바꾼다.
  - 본문: 산문 단락 — 문장 끝에 `[n]` 인용 번호, «해석» 문장은 " — 해석" 표지.
  - 4장(past_changes): 프로그램이 만든 실적표를 기존 ReportTable로 그대로 태운다.
  - 출처 부록: 실제 인용된 조각만으로 만들며, 번호는 본문 `[n]`과 1:1이다.
★ import 방향: composer → pipeline.port / provenance.sources는 «데이터 계약
  재사용»만이다(생성 함수·게이트 호출 없음). report_standard·publish는 import
  하지 않는다. report_standard의 SectionContentBlock은 FactRecord 원장 투영
  전용이라 v2 산문에는 구조적으로 맞지 않아 쓰지 않는다 — v2 본문은 기존
  prose_lines 경로(웹·PDF 공통)를 그대로 탄다.
★ 여기는 «변환»만 한다. 거짓 검증은 3-2 verify.py, 출고 검증은 validate.py 몫.
  닫힌 정규식 게이트 없음 — 문장 내용을 거르는 검사를 하지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Optional

from src.core.citations import citation_number
from src.features.composer.constants import (
    CITATION_STYLE_MERGED,
    PARAGRAPH_MAX_SENTENCES,
    FLOW_ARROW_SECTION_IDS,
    FLOW_PRESENTATION,
    FLOW_UNCONFIRMED_CELL,
    OPERATIONS_FLOW_CAPTION,
    FLOW_CAPTION_BY_SECTION,
    FLOW_HEADERS_BY_SECTION,
    OPERATIONS_FLOW_HEADERS,
    OPERATIONS_FLOW_SECTION_ID,
    DART_DOCUMENT_HOST,
    DART_DOCUMENT_URL_TEMPLATE,
    DEFAULT_CITATION_STYLE,
    GRADE_INTERPRETED,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.logic import FragmentsInput
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
)
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SummaryItem,
)
from src.features.provenance.sources import Source, SourceKind

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
# 값 — 전부 이 파일(3-4a 소유) 상수
# ══════════════════════════════════════════════════════════

#: v2 보고서의 schema_version. canonical(v4)과 다른 값을 명시해
#: v1 게이트·저장 경로가 v2 보고서를 canonical로 착각하지 않게 한다.
#: (웹 result.html은 canonical 버전만 표시하므로, v2 화면 연결은 3-4b가
#:  라우트·템플릿 쪽에서 이 상수를 인정하도록 처리해야 한다 — 보고서에 명시.)
ENGINE_V2_SCHEMA_VERSION: Final[str] = "company-report-v2-composer"

#: «해석» 문장 뒤에 붙는 표지 (기준문서 5절 — 회사가 말한 것과 분석을 구분)
INTERPRETATION_MARKER: Final[str] = f" — {GRADE_INTERPRETED}"

#: 실적표가 실리는 장 — 04장 3-4절: 「4장은 기존 실적표·차트 재사용」
PERFORMANCE_TABLE_SECTION_ID: Final[str] = "past_changes"

#: 매출 구성표가 붙는 장. v1이 `real.py`에서 business_model에 붙이는 것과
#: 같은 자리다 — 두 경로가 다른 장에 붙으면 같은 회사 보고서가 채널마다
#: 달라진다.
COMPOSITION_TABLE_SECTION_ID: Final[str] = "business_model"

#: 구성표의 기본 표시 방식. `report_standard/visualization.py`가 이 값과
#: 표 모양을 함께 보고 100% 누적 막대를 그릴지 정한다.
COMPOSITION_PRESENTATION: Final[str] = "composition"

#: 시간 장 표시 태그 — report_standard SECTION_SPECS와 같은 값을 «복사»했다.
#: (composer→report_standard import 금지 규칙. 정본이 바뀌면 같이 바꾼다.)
SECTION_TAGS: Final[dict[str, str]] = {
    "past_changes": "#과거",
    "current_challenges": "#현재",
    "future_strategy": "#미래",
}

#: 장 표시 번호 — v3 정본 순서(1~9)를 장 id에 결속한다.
SECTION_DISPLAY_NUMBERS: Final[dict[str, str]] = {
    section_id: str(index + 1) for index, section_id in enumerate(SECTION_IDS)
}

#: 부록 Source.source_id 접두어 — canonical source_id와 절대 겹치지 않게 한다.
V2_SOURCE_ID_PREFIX: Final[str] = "v2-frag-"

#: 문서명도 종류도 없는 조각의 부록 표시 이름 (빈 라벨은 렌더가 깨진다)
FALLBACK_SOURCE_LABEL: Final[str] = "수집 자료"

#: URL 없는 조각(전자공시 절)의 부록 라벨 접두어
FILING_LABEL_PREFIX: Final[str] = "전자공시"


# ══════════════════════════════════════════════════════════
# 조각 메타 — 부록에 실을 문서명·출처·날짜·URL
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _FragmentMeta:
    """부록 한 줄을 만들 조각 메타. 원문 자체는 부록에 싣지 않는다."""

    fragment_id: str
    kind: str
    source_url: str = ""
    document_title: str = ""
    location: str = ""
    #: 홈페이지 조각의 «문서일» — CollectedFragment 어댑터에는 없는 필드라
    #: 원시 dict를 받았을 때만 채워진다 (port.py는 3-1 소유라 손대지 않는다).
    document_date: str = ""
    #: 전자공시 절(사업내용·MD&A 등)에서 떠 온 조각인가.
    #: ★ 조각 자체에 «출처»가 비어 있다는 모양 하나로만 정한다 — 종류 이름을
    #:   목록으로 검사하지 않는다(닫힌 목록 게이트 금지, 01_원칙과_금지.md).
    #:   이 값은 공시 원문 주소를 붙이기 «전»에 확정해야 한다.
    from_filing: bool = False


def _fragment_metas(fragments: FragmentsInput) -> tuple[_FragmentMeta, ...]:
    """원시 dict든 어댑터 튜플이든 부록용 메타로 맞춘다.

    ★ 원문이 빈 조각 제외 규칙은 port.fragments_from_raw와 «같아야» 한다 —
      compose·verify가 본 조각 집합과 부록의 조각 집합이 어긋나면
      본문 [n]과 부록의 1:1이 깨진다.
    """
    if isinstance(fragments, Mapping):
        out: list[_FragmentMeta] = []
        for number in sorted(fragments):
            item = fragments[number]
            text = str(item.get("원문") or "").strip()
            if not text:
                continue
            source_url = str(item.get("출처") or "").strip()
            out.append(
                _FragmentMeta(
                    fragment_id=str(number),
                    kind=str(item.get("종류") or "").strip(),
                    source_url=source_url,
                    document_title=str(item.get("문서명") or "").strip(),
                    location=str(item.get("원문위치") or "").strip(),
                    document_date=str(item.get("문서일") or "").strip(),
                    from_filing=not source_url,
                )
            )
        return tuple(out)
    return tuple(
        _FragmentMeta(
            fragment_id=str(fragment.fragment_id),
            kind=str(getattr(fragment, "kind", "") or ""),
            source_url=str(getattr(fragment, "source_url", "") or ""),
            document_title=str(getattr(fragment, "document_title", "") or ""),
            location=str(getattr(fragment, "location", "") or ""),
            from_filing=not str(getattr(fragment, "source_url", "") or ""),
        )
        for fragment in fragments
    )


def _citation_numbers(metas: Sequence[_FragmentMeta]) -> dict[str, int]:
    """조각 id → 부록 표시 번호.

    ★ 원칙(provenance.Source 계약과 동일): 조각 번호를 «그대로» 쓴다 —
      새로 매기면 본문·부록·검증이 서로 다른 번호를 보게 된다.
      숫자가 아닌 id(계약상 없지만 방어)는 기존 최대 번호 뒤에 이어 붙인다.
    """
    numbers: dict[str, int] = {}
    pending: list[str] = []
    for meta in metas:
        if meta.fragment_id.isdigit() and int(meta.fragment_id) > 0:
            numbers[meta.fragment_id] = int(meta.fragment_id)
        else:
            pending.append(meta.fragment_id)
    next_number = max(numbers.values(), default=0) + 1
    for fragment_id in pending:
        numbers[fragment_id] = next_number
        next_number += 1
    return numbers


# ══════════════════════════════════════════════════════════
# 문장 → 화면 글 (인용 번호 + 해석 표지)
# ══════════════════════════════════════════════════════════


def _sentence_citation_numbers(
    sentence: ComposedSentence, numbers: Mapping[str, int]
) -> tuple[int, ...]:
    """문장의 인용 id를 표시 번호로 바꾼다. 실존하지 않는 id는 버린다.

    (3-2 검증이 이미 깨진 인용 문장을 제거했으므로 여기 걸리면 결함 신호다 —
    조용히 틀린 번호를 내보내는 대신 표기만 빼고 경고를 남긴다.)
    """
    out: list[int] = []
    for citation in sentence.citations:
        number = numbers.get(str(citation).strip())
        if number is None:
            logger.warning(
                "렌더 단계에서 실존하지 않는 인용 id를 만나 표기를 뺐다: %s",
                citation,
            )
            continue
        if number not in out:
            out.append(number)
    return tuple(out)


def sentence_display_text(
    sentence: ComposedSentence,
    numbers: Mapping[str, int],
    *,
    show_markers: bool = True,
) -> str:
    """문장 하나를 «글 [n][m] — 해석» 모양으로 만든다 (04장 3-4절 1항).

    Args:
        show_markers: False면 인용 번호를 붙이지 않는다. «해석» 표지는 그대로
            남는다 — 그건 번호가 아니라 이 문장이 어떤 성격인지 알리는 표지다.
    """
    text = " ".join(sentence.text.split())
    if show_markers:
        markers = "".join(
            f"[{number}]" for number in _sentence_citation_numbers(sentence, numbers)
        )
        if markers:
            text = f"{text} {markers}"
    if sentence.grade == GRADE_INTERPRETED:
        text = f"{text}{INTERPRETATION_MARKER}"
    return text


def _marker_visibility(
    sentences: Sequence[ComposedSentence],
    numbers: Mapping[str, int],
    style: str,
) -> tuple[bool, ...]:
    """문장마다 인용 번호를 «보일지» 정한다.

    ★ 왜 문장 하나가 아니라 묶음을 보나 — 절충안의 핵심이 「같은 출처를
      잇달아 인용하는 문장은 묶음의 마지막에만 번호를 단다」이기 때문이다.
      앞뒤를 봐야 그 판단이 선다.

    규칙(CITATION_STYLE_MERGED):
      ① «해석» 문장은 번호를 뺀다 — 종합 판단이라 특정 출처를 가리키지 않고,
         이미 « — 해석» 표지가 성격을 말해 준다.
      ② «확인» 문장은 다음 문장이 «같은 출처 집합»을 인용하는 확인 문장이면
         번호를 미룬다. 그 묶음의 마지막 문장이 대표로 번호를 단다.

    ★ 부록과의 1:1은 깨지지 않는다 — 묶음의 마지막 문장이 그 출처 번호를
      «반드시» 표시하므로, 본문에 한 번도 안 나오는 부록 번호가 생기지 않는다.
      (validate_v2의 인용-부록 상호 검사 계약)
    """
    if style != CITATION_STYLE_MERGED:
        return tuple(True for _ in sentences)

    keys = [
        frozenset(_sentence_citation_numbers(sentence, numbers))
        for sentence in sentences
    ]
    visible: list[bool] = []
    for index, sentence in enumerate(sentences):
        if sentence.grade == GRADE_INTERPRETED:
            visible.append(False)
            continue
        if not keys[index]:
            visible.append(False)
            continue
        # 다음 «확인» 문장이 같은 출처 묶음이면 번호를 그쪽으로 미룬다.
        following = next(
            (
                position
                for position in range(index + 1, len(sentences))
                if sentences[position].grade != GRADE_INTERPRETED
            ),
            None,
        )
        defer = following is not None and keys[following] == keys[index]
        visible.append(not defer)
    return tuple(visible)


# ══════════════════════════════════════════════════════════
# 실적표 변환 (4장)
# ══════════════════════════════════════════════════════════


def _paragraph_breaks(
    sentences: Sequence[ComposedSentence], numbers: Mapping[str, int]
) -> tuple[int, ...]:
    """문단이 시작되는 문장 위치들.

    ★ 왜 나누나 (실측) — 화면도 PDF도 한 장의 문장을 «전부 이어 붙여» 한
      문단으로 냈다. 진영 2장은 8문장이 줄바꿈 없이 한 덩어리였다.
    ★ 기준은 «같은 출처를 인용하는 묶음». 인용이 바뀌면 이야기가 바뀐 것이고,
      절충안이 번호를 다는 자리(묶음의 끝)와도 정확히 맞는다.
      해석 문장은 앞 문장의 뜻풀이라 묶음을 끊지 않는다.
    ★ 출처가 안 바뀌어도 상한에서 끊는다 — 안 그러면 문단이 다시 벽이 된다.
    """
    if not sentences:
        return ()
    starts = [0]
    current_key: Optional[frozenset[int]] = None
    length = 0
    for index, sentence in enumerate(sentences):
        key = frozenset(_sentence_citation_numbers(sentence, numbers))
        is_interpretation = sentence.grade == GRADE_INTERPRETED
        if index == 0:
            current_key = key
            length = 1
            continue
        changed = bool(key) and not is_interpretation and key != current_key
        if changed or length >= PARAGRAPH_MAX_SENTENCES:
            starts.append(index)
            length = 1
            if key:
                current_key = key
            continue
        if key and not is_interpretation:
            current_key = key
        length += 1
    return tuple(starts)


def _summary_source_section(
    sentence: ComposedSentence,
    numbers: Mapping[str, int],
    section_citations: Mapping[str, frozenset[int]],
) -> str:
    """요약 문장이 «어느 장에서 온 이야기인가»를 인용으로 되짚는다.

    ★ 왜 필요한가 (실측) — v2는 SummaryItem에 section_id를 안 채웠다. 그래서
      요약 카드의 주제 라벨이 전부 「핵심결론」으로 나오고, 「→ 4장」 링크 칸이
      통째로 비었다. 독자가 「이 요약이 어느 장 이야기인지」를 알 수 없다.
    ★ 요약은 검증된 본문을 재료로 «새로» 쓰므로 어느 장에서 왔는지가 기록되지
      않는다. 대신 «같은 근거를 가장 많이 공유하는 장»을 찾는다 — 근거가
      겹친다는 것은 같은 이야기를 한다는 뜻이다.
    ★ 겹치는 장이 없으면 빈 문자열을 돌려준다. 억지로 붙이지 않는다 —
      틀린 장을 가리키는 것이 라벨이 없는 것보다 나쁘다.
    """
    cited = frozenset(_sentence_citation_numbers(sentence, numbers))
    if not cited:
        return ""
    best_id = ""
    best_score = 0
    for section_id in SECTION_IDS:
        score = len(cited & section_citations.get(section_id, frozenset()))
        if score > best_score:
            best_id, best_score = section_id, score
    return best_id


def _ensure_no_orphan_markers(
    groups: Sequence[tuple[Sequence[ComposedSentence], list[bool]]],
    numbers: Mapping[str, int],
) -> None:
    """부록에 실릴 번호가 «본문 어디에도» 안 보이는 일을 막는다 (제자리 수정).

    ★ 왜 필요한가 (골든 fixture가 잡은 결함) — 절충안 규칙 ①은 해석 문장의
      번호를 뺀다. 그런데 어떤 조각이 «해석 문장에서만» 인용되면 그 번호가
      본문에 한 번도 안 나온다. 부록은 인용된 조각으로 만들어지므로 그 줄이
      고아가 되고, 출고 검증(validate_v2)이 「부록에 있는 번호를 본문
      어디에서도 인용하지 않았습니다」로 보고서를 통째로 막는다.
    ★ 그래서 규칙을 적용한 «뒤»에 한 번 더 훑어, 어디에도 안 보이는 번호는
      그 번호를 인용한 «마지막» 문장에서 되살린다. 번호는 줄이되 추적은
      끊지 않는다 — 둘 중 하나를 고르는 문제가 아니다.
    """
    visible: set[int] = set()
    for sentences, shows in groups:
        for index, sentence in enumerate(sentences):
            if shows[index]:
                visible.update(_sentence_citation_numbers(sentence, numbers))
    # 어디에 마지막으로 나왔는지 기억해 두었다가, 안 보이는 번호만 되살린다.
    last_seen: dict[int, tuple[int, int]] = {}
    for group_index, (sentences, _shows) in enumerate(groups):
        for index, sentence in enumerate(sentences):
            for number in _sentence_citation_numbers(sentence, numbers):
                last_seen[number] = (group_index, index)
    for number, (group_index, index) in last_seen.items():
        if number not in visible:
            groups[group_index][1][index] = True


def _performance_report_table(
    table: PerformanceTable, presentation: str
) -> ReportTable:
    """composer 어댑터 실적표를 기존 렌더의 ReportTable로 되돌린다."""
    caption = table.caption
    if table.unit and "단위" not in caption:
        caption = f"{caption} (단위: {table.unit})"
    return ReportTable(
        caption=caption,
        headers=list(table.headers),
        rows=[list(row) for row in table.rows],
        cite=table.cite,
        numeric=True,
        display_unit=table.unit,
        presentation=presentation or "table",
    )


# ══════════════════════════════════════════════════════════
# 출처 부록
# ══════════════════════════════════════════════════════════


def _source_label(meta: _FragmentMeta, filing_meta: Optional[FilingMeta]) -> str:
    """부록에 보일 문서명 — 문서명 > 공시 보고서명·절 > (전자공시) 종류 순."""
    if meta.document_title:
        return meta.document_title
    if not meta.kind:
        return FALLBACK_SOURCE_LABEL
    if not meta.from_filing:
        return meta.kind
    # 전자공시 절(사업내용·MD&A 등)은 절 이름만 덜렁 내보내면 독자가 어느
    # 문서인지 알 수 없다. 보고서명을 알면 「반기보고서 · 사업내용」처럼
    # 문서와 절을 함께 보여 주고, 모르면 발행 채널만 앞에 붙인다.
    if filing_meta is not None and filing_meta.title:
        return f"{filing_meta.title} · {meta.kind}"
    return f"{FILING_LABEL_PREFIX} {meta.kind}"


def _flow_report_table(
    section: ComposedSection, numbers: Mapping[str, int]
) -> Optional[ReportTable]:
    """7장 경로표를 흐름도용 ReportTable로 바꾼다. 실을 줄이 없으면 None.

    ★ 「한 행 = 한 경로」가 이 표의 계약이다. 웹(`.flow-row`)과 PDF
      (`_FlowGraphic`)가 한 행을 왼쪽→오른쪽 한 흐름으로 그린다. 경로를
      행으로 나누면 도식 결함 세 가지가 구조적으로 막힌다:
        · 주 경로(회사가 고객에 직접 닿는 길) 누락 — 첫 줄에 오게 지침이 요구
        · 고객 혼동 — 고객이 다르면 다른 줄이라 한 상자로 합쳐질 수 없다
        · 지원 관계를 판매 경로에 놓기 — 고객에 안 닿는 관계는 표에 못 들어온다
    ★ 근거 없는 줄은 파싱에서 이미 버려졌다. 여기서는 «실존하는 조각을
      가리키는가»만 한 번 더 본다 — 없는 번호를 캡션에 인쇄하지 않기 위해서다.
    """
    if not section.flow_rows:
        return None
    rows: list[list[str]] = []
    cited: list[int] = []
    # ★ 「미확인」 채우기는 «화살표로 그려지는 장»(2·5·7장)에만 건다.
    #   카드로 그려지는 장(1·3·6·8장)은 빈 칸을 그대로 둔다 — 이유는
    #   constants.FLOW_ARROW_SECTION_IDS 주석(카드는 빈 칸을 «빼는» 렌더러다).
    fills_unconfirmed = section.section_id in FLOW_ARROW_SECTION_IDS
    for row in section.flow_rows:
        row_numbers = [
            numbers[str(citation).strip()]
            for citation in row.citations
            if str(citation).strip() in numbers
        ]
        if not row_numbers:
            continue
        # ★ 화살표 장에서는 회사가 안 밝힌 칸을 «빈 칸»이 아니라 「미확인」으로
        #   채운다. 빈 문자열이면 흐름도에 «라벨만 있고 속이 빈 76px 상자»가
        #   화살표와 함께 그려져 고장처럼 보인다
        #   (constants.FLOW_UNCONFIRMED_CELL 주석에 실측 근거).
        #   ★ 여기(데이터 층)에서 채우는 이유 — 웹(result.html)과 PDF(_FlowGraphic)가
        #     각자 채우면 한쪽만 고쳐져 갈린다. 2026-08-25에 문단 번호에서 같은
        #     사고가 있었다. 두 렌더러가 같은 값을 받게 한 곳에서 정한다.
        #   ★ 카드 장에서는 채우지 않는다 — 카드 렌더러가 빈 칸을 «빼도록»
        #     설계돼 있어서, 채우면 「확인된 사례: 미확인」·제목이 「미확인」인
        #     카드가 인쇄된다(FLOW_ARROW_SECTION_IDS 주석의 실측 2건).
        rows.append(
            [
                (str(cell).strip() or FLOW_UNCONFIRMED_CELL)
                if fills_unconfirmed
                else str(cell).strip()
                for cell in row.cells
            ]
        )
        cited.extend(row_numbers)
    if not rows:
        return None
    # 캡션 근거는 «표 전체»를 대표하는 첫 조각 하나만 단다. 행마다 번호를
    # 흩뿌리면 정본 §7(기준일·출처 반복 방지)에 어긋난다.
    return ReportTable(
        # 장마다 머리말·캡션이 다르다(5장 과제→대응, 7장 투입→하는 일→도달).
        # 그 대응은 constants 한 곳에서만 정한다.
        caption=FLOW_CAPTION_BY_SECTION[section.section_id],
        headers=list(FLOW_HEADERS_BY_SECTION[section.section_id]),
        rows=rows,
        cite=f"[{min(cited)}]",
        numeric=False,
        presentation=FLOW_PRESENTATION,
    )


def _build_source(
    meta: _FragmentMeta,
    number: int,
    company_name: str,
    used_in: Sequence[str],
    filing_meta: Optional[FilingMeta] = None,
) -> Source:
    """인용된 조각 하나를 부록 Source 한 줄로 만든다.

    kind 구분은 «URL이 있는가»라는 모양만 본다(내용 목록 검사 아님) —
    전자공시 절 조각은 출처 URL이 없고, 홈페이지·공식 IR 조각만 URL을 갖는다.

    ★ 전자공시 조각의 원문 주소는 «조각»이 아니라 «그 조각을 떠 온 문서»가
      가지고 있다. filing_meta를 받으면 접수번호로 원문 주소를 만들어 실어,
      독자가 부록에서 원문을 바로 열 수 있게 한다. filing_meta가 없으면
      예전처럼 주소 없이 나가며 그 사실이 화면에 그대로 보인다.
    """
    if not meta.from_filing:
        return Source(
            number=number,
            kind=SourceKind.OTHER,
            label=_source_label(meta, filing_meta),
            collected_at=meta.document_date,
            source_id=f"{V2_SOURCE_ID_PREFIX}{meta.fragment_id}",
            title=meta.document_title,
            publisher=company_name,
            url=meta.source_url,
            location=meta.location,
            used_in=list(used_in),
        )
    document_id = filing_meta.document_id if filing_meta is not None else ""
    return Source(
        number=number,
        kind=SourceKind.FILING,
        label=_source_label(meta, filing_meta),
        disclosed_at=filing_meta.disclosed_at if filing_meta is not None else "",
        collected_at=meta.document_date,
        source_id=f"{V2_SOURCE_ID_PREFIX}{meta.fragment_id}",
        title=filing_meta.title if filing_meta is not None else "",
        publisher=company_name,
        host=DART_DOCUMENT_HOST if document_id else "",
        url=(
            DART_DOCUMENT_URL_TEMPLATE.format(document_id=document_id)
            if document_id
            else ""
        ),
        document_id=document_id,
        # 어느 절에서 떠 왔는지가 원문 안에서 찾아갈 위치다.
        location=meta.location or meta.kind,
        used_in=list(used_in),
    )


# ══════════════════════════════════════════════════════════
# 진입 함수
# ══════════════════════════════════════════════════════════


def render_report(
    company_name: str,
    report: ComposedReport,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    corp_type: str = "",
    grade: Grade = Grade.COMPLETE,
    generated_at: str = "",
    as_of_date: str = "",
    analysis_period: str = "",
    latest_performance_period: str = "",
    table_presentation: str = "table",
    filing_meta: Optional[FilingMeta] = None,
    composition_tables: tuple[PerformanceTable, ...] = (),
    citation_style: str = DEFAULT_CITATION_STYLE,
) -> Report:
    """검증 끝난 ComposedReport를 웹·PDF 공용 pipeline Report로 바꾼다.

    Args:
        company_name: 분석 대상 법인 이름.
        report: verify_report까지 통과한 v2 보고서 (summary 포함).
        fragments: 수집 조각 — real.py 원시 dict를 주면 홈페이지 «문서일»까지
            부록에 실린다. CollectedFragment 시퀀스도 받는다.
        performance_table: 4장에 실을 프로그램 실적표. 없으면 None.
        corp_type / generated_at / as_of_date / analysis_period /
            latest_performance_period: 표지·머리말 메타 — real.py 연결부(3-4b)가
            기존 파이프라인 값 그대로 넘긴다. 없으면 표기 생략(거짓 없음).
        grade: 표지 등급. 기본 완성 — 완성 여부 실측은 06장 몫이다.
        table_presentation: 원본 pipeline ReportTable.presentation을 넘기면
            기존 차트(trend·composition)가 그대로 재사용된다. 기본은 일반 표.
        composition_tables: 2장에 실을 매출 구성표들(제품별·지역별 등). v1은 이
            표들을 이미 만들어 business_model 장에 «전부» 붙이는데 v2 호출부가
            넘기지 않아 «표도 도식도» 사라져 있었다(실측 결함 — 9장 중 4장
            하나만 표를 받았다). 표는 여러 개일 수 있고 각각 도식이 함께 나간다
            — 첫 표만 쓰지 않는다(2026-08-25 설계 변경). 비어 있으면 표 없이
            간다(억지로 만들지 않는다).
        citation_style: 본문 인용 번호 표기 방식. `inline`은 문장마다 번호를
            붙이고(기존), `merged`는 해석 문장의 번호를 빼고 같은 출처가 이어지는
            확인 문장 묶음은 마지막 문장에만 번호를 단다. 부록과의 1:1은 두 방식
            모두에서 유지된다.
        filing_meta: 이번 조사가 내려받은 공시의 신원(접수번호·보고서명·공시일).
            주면 전자공시 조각의 부록 줄에 «원문 주소»가 실린다. 없으면 주소
            없이 나가며, 그 사실이 화면에 그대로 보인다(빈 값을 지어내지 않는다).

    Returns:
        pipeline `Report` — 9개 장 전부(prose_lines: 문장 + [n] + 해석 표지,
        자료 부족 장은 안내문), 4장 실적표, 인용된 조각만으로 만든 부록
        (번호는 본문 [n]과 1:1), 핵심 요약. schema_version은
        ENGINE_V2_SCHEMA_VERSION — canonical(v4) 게이트 대상이 아니다.
    """
    metas = _fragment_metas(fragments)
    numbers = _citation_numbers(metas)
    meta_by_number = {numbers[meta.fragment_id]: meta for meta in metas}

    #: 부록 번호 → 그 번호를 인용한 장 id들 (v3 순서 유지)
    used_sections: dict[int, list[str]] = {}
    #: 부록 번호(문자열) → 그 번호를 근거로 쓴 문장들의 등급 («확인»/«해석»).
    #: 부록의 「사실 검증」 칸이 읽는다 — 자세한 이유는 `pipeline.port.Report`
    #: 의 `source_grades` 주석에 있다.
    source_grades: dict[str, list[str]] = {}

    sections: list[ReportSection] = []
    # 표기 방식을 적용한 가시성을 먼저 전부 계산한다 — 고아 번호를 되살리려면
    # 본문과 요약을 «함께» 봐야 한다.
    visibility_groups: list[tuple[Sequence[ComposedSentence], list[bool]]] = [
        (section.sentences, list(_marker_visibility(section.sentences, numbers, citation_style)))
        for section in report.sections
    ]
    visibility_groups.append(
        (report.summary, list(_marker_visibility(report.summary, numbers, citation_style)))
    )
    _ensure_no_orphan_markers(visibility_groups, numbers)
    section_shows = {
        section.section_id: visibility_groups[index][1]
        for index, section in enumerate(report.sections)
    }
    summary_shows = visibility_groups[-1][1]

    for section in report.sections:
        prose_lines: list[tuple[str, str]] = []
        notice_paragraph = ""
        # 자료 부족·생성 실패의 정직한 안내문을 본문 «앞»에 둔다
        # (기준문서 3절: 안내 1~2문장 + 찾은 만큼의 내용).
        if section.notice:
            prose_lines.append((section.notice, ""))
            notice_paragraph = section.notice
        shows = section_shows[section.section_id]
        breaks = set(_paragraph_breaks(section.sentences, numbers))
        prose_paragraphs: list[str] = []
        buffer: list[str] = []
        for index, sentence in enumerate(section.sentences):
            display = sentence_display_text(
                sentence, numbers, show_markers=shows[index]
            )
            # prose_lines는 «문장» 단위 그대로 둔다 — 출고 검증과 저장이 이
            # 단위를 쓴다. 문단은 화면·PDF 표시용으로 «따로» 모은다.
            prose_lines.append((display, ""))
            if index in breaks and buffer:
                prose_paragraphs.append(" ".join(buffer))
                buffer = []
            buffer.append(display)
        if buffer:
            prose_paragraphs.append(" ".join(buffer))
        for index, sentence in enumerate(section.sentences):
            # ★ 부록 사용 장 기록은 «번호를 보였는지»와 무관하다 — 근거를
            #   실제로 쓴 장은 표기 방식과 상관없이 그 장이다.
            for cited in _sentence_citation_numbers(sentence, numbers):
                owners = used_sections.setdefault(cited, [])
                if section.section_id not in owners:
                    owners.append(section.section_id)
                # ★ 등급도 «번호를 보였는지»와 무관하게 싣는다 (같은 이유).
                #   부록의 「사실 검증」 칸이 이 값을 쓴다 — 화면 글자에서
                #   되짚으면 숨겨진 «해석» 인용을 못 봐서 「사실 검증 완료」로
                #   잘못 표시된다(2026-08-25 적대 검수가 재현).
                grades = source_grades.setdefault(str(cited), [])
                if sentence.grade not in grades:
                    grades.append(sentence.grade)

        tables: list[ReportTable] = []
        # ★ 설계 변경(2026-08-25) — 「한 장에 표는 하나」라는 암묵적 단수
        #   가정을 걷어냈다. slots는 이 장에 실릴 «프로그램표»(실적/구성) 목록
        #   이다 — 4장은 실적표 하나, 2장은 구성표가 여러 개(제품별·지역별)일
        #   수 있다. 여기 흐름표(경로표)까지 «같은 장에 함께» 실릴 수 있다
        #   (예: 2장 = 흐름표 + 구성표 2개).
        slots: list[tuple[PerformanceTable, str]] = []
        if (
            section.section_id == PERFORMANCE_TABLE_SECTION_ID
            and performance_table is not None
            and performance_table.rows
        ):
            slots.append((performance_table, table_presentation))
        elif section.section_id == COMPOSITION_TABLE_SECTION_ID:
            slots.extend(
                (table, COMPOSITION_PRESENTATION)
                for table in composition_tables
                if table.rows
            )
        # 흐름표를 내는 장(1·2·5·6·7·8장). 예전에는 「프로그램표 자리를 이미
        # 쓴 장은 건너뛴다」(slot is None)는 배타 조건이 있었는데, 2장처럼
        # 구성표와 흐름표가 «함께» 실리는 장이 생겨 그 조건을 없앴다 — 흐름표를
        # 먼저 넣고 프로그램표를 뒤에 붙인다(목업이 요구하는 「흐름 → 구성」
        # 순서와도 맞는다).
        if section.section_id in FLOW_HEADERS_BY_SECTION:
            flow_table = _flow_report_table(section, numbers)
            if flow_table is not None:
                flow_cite = citation_number(flow_table.cite)
                if flow_cite and int(flow_cite) in meta_by_number:
                    owners = used_sections.setdefault(int(flow_cite), [])
                    if section.section_id not in owners:
                        owners.append(section.section_id)
                tables.append(flow_table)
        for table, presentation in slots:
            converted = _performance_report_table(table, presentation)
            cite_number_text = citation_number(converted.cite)
            if cite_number_text and int(cite_number_text) in meta_by_number:
                # 표 캡션의 〔n〕도 본문 인용이다 — 부록과 1:1을 지키려고
                # 그 조각을 부록 사용 목록에 넣는다.
                owners = used_sections.setdefault(int(cite_number_text), [])
                if section.section_id not in owners:
                    owners.append(section.section_id)
            elif cite_number_text:
                # 번호가 가리킬 조각이 없으면 틀린 번호를 인쇄하는 대신 표기를 뺀다.
                logger.warning(
                    "실적표 cite 번호 %s가 수집 조각에 없어 표기를 뺐다",
                    cite_number_text,
                )
                converted = ReportTable(
                    caption=converted.caption,
                    headers=list(converted.headers),
                    rows=[list(row) for row in converted.rows],
                    cite="",
                    numeric=converted.numeric,
                    display_unit=converted.display_unit,
                    presentation=converted.presentation,
                )
            tables.append(converted)

        sections.append(
            ReportSection(
                cell=section.section_id,
                title=SECTION_TITLES.get(section.section_id, section.section_id),
                # lines는 내부 감사용이지만 is_filled 판정에도 쓰인다 —
                # 안내문만 있는 장도 «장 삭제 금지» 원칙대로 렌더돼야 한다.
                lines=list(prose_lines),
                tables=tables,
                prose_lines=prose_lines,
                # 화면·PDF가 문단을 만드는 단위. 비면 소비하는 쪽이 예전처럼
                # prose_lines를 이어 붙인다 (뒤로 호환).
                prose_paragraphs=(
                    ([notice_paragraph] if notice_paragraph else []) + prose_paragraphs
                ),
                display_number=SECTION_DISPLAY_NUMBERS.get(
                    section.section_id, ""
                ),
                tag=SECTION_TAGS.get(section.section_id, ""),
            )
        )

    # 요약 문장이 어느 장 이야기인지 되짚기 위한 장별 인용 묶음.
    section_citations = {
        section.section_id: frozenset(
            number
            for sentence in section.sentences
            for number in _sentence_citation_numbers(sentence, numbers)
        )
        for section in report.sections
    }
    summary_items: list[SummaryItem] = []
    for index, sentence in enumerate(report.summary):
        summary_items.append(
            SummaryItem(
                text=sentence_display_text(
                    sentence, numbers, show_markers=summary_shows[index]
                ),
                section_id=_summary_source_section(
                    sentence, numbers, section_citations
                ),
            )
        )
        for cited in _sentence_citation_numbers(sentence, numbers):
            # 요약 전용 인용도 부록에는 실려야 한다 (장 목록에는 안 더한다 —
            # used_in은 본문 장 표시 전용이라 요약은 대응하는 장이 없다).
            used_sections.setdefault(cited, [])
            # ★ 등급은 본문과 «똑같이» 싣는다 (2026-08-25 적대 검수가 재현).
            #   요약 문장도 본문과 같은 verify_sentences를 타므로 «해석»으로
            #   강등될 수 있고, 그때 인용은 그대로 남는다. 여기서 등급을
            #   빠뜨리면 그 해석이 부록에 안 보여 「사실 검증 완료」로 잘못
            #   적힌다 — 실제로는 「부분 검증」이 맞다.
            grades = source_grades.setdefault(str(cited), [])
            if sentence.grade not in grades:
                grades.append(sentence.grade)

    citations: list[Source] = [
        _build_source(
            meta_by_number[number],
            number,
            company_name,
            used_sections[number],
            filing_meta,
        )
        for number in sorted(used_sections)
        if number in meta_by_number
    ]

    return Report(
        company=company_name,
        job="",
        corp_type=corp_type,
        grade=grade,
        sections=sections,
        citations=list(citations),
        summary_items=summary_items,
        generated_at=generated_at,
        schema_version=ENGINE_V2_SCHEMA_VERSION,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
        source_grades=source_grades,
    )

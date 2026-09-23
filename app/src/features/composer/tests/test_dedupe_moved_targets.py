"""안내문 최종 대조의 «이동 근거 문장 단위 재검»을 못 박는다 (P19).

★ 왜 이 시험이 있나 (실측 — 저장 실측 7·8장) — 「다른 장에서 더 자세히
  다뤄져 그쪽으로 모았습니다」가 이동 대상 장을 밝히지 않았고, 대상 장에
  «그 사실»이 남아 있는지 아무도 다시 재지 않았다. 또 「아래 표」라고 적고
  실제로는 다음 쪽의 «도식»이 실렸다.
★ 여기서 지키는 것:
  ① 넘긴 그 사실이 대상 장에 살아 있을 때만 이동을 말하고, 대상 장의
     번호·제목을 함께 적는다.
  ② 대상 장에 «다른» 문장이 있다는 것만으로 이동 성공을 단정하지 않는다.
  ③ 남은 자료는 «표/도식/보도 목록» 실제 종류로 부르고 지면 위치를 말하지
     않는다.
  ④ 대조는 멱등이다. 장부를 안 주면 종전 판정 그대로다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.constants import (
    NOTICE_DUPLICATE_MOVED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.dedupe import (
    MovedFactRecord,
    drop_cross_section_duplicates,
    moved_owner_fingerprint,
    reconcile_section_notices,
)
from src.features.composer.dedupe_notice_constants import (
    NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE,
    NOTICE_MOVED_TARGETS_TEMPLATE,
    NOTICE_ONLY_MATERIALS_TEMPLATE,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    NewsRow,
)

_문장 = (
    "회사는 Sony Music, TME, Republic Records 등 글로벌 유수의 음반·음원 "
    "유통 전문사와 파트너십을 체결하여 글로벌 유통 범위를 확대하고 있다."
)
#: 원래 의역 짝 — 짝(비교 후보)은 되지만 삭제 증명이 없어 이제 옮기지 않는다
#: (2026-09-23 총괄 확정). 보존 기대 음성으로 남긴다.
_문장_변형 = (
    "회사는 Sony Music, TME, Republic Records 등 글로벌 유통 전문사와의 "
    "파트너십을 통해 음반·음원의 글로벌 유통 범위를 확대하고 있다."
)
#: 소유 장이 남기는 같은 주장 문장. 인용이 하나 더 있어 지운 후보와 «지문»이
#: 다르다 — 장부가 삭제 후보가 아니라 실제 소유 후보에 결속됐는지 가를 수 있다.
_소유_인용 = ("n1", "n2")
_무관한_문장 = (
    "회사의 신인개발 부문은 캐스팅팀과 트레이닝팀으로 구성되어 연습생을 "
    "모집하고 체계적인 트레이닝을 제공한다."
)


def _sentence(text: str, citations: tuple[str, ...]) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade="확인")


def _report(**by_section: tuple[ComposedSentence, ...]) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id, sentences=by_section.get(section_id, ())
            )
            for section_id in SECTION_IDS
        )
    )


def _section(report: ComposedReport, section_id: str) -> ComposedSection:
    return next(
        section for section in report.sections
        if section.section_id == section_id
    )


def _dropped_report_and_ledger() -> tuple[ComposedReport, list[MovedFactRecord]]:
    """중복 제거를 실제로 돌려 «비워진 장 + 장부»를 만든다 (재현 경로 그대로)."""

    sink: list[MovedFactRecord] = []
    report = _report(
        culture=(_sentence(_문장, ("n1",)),),
        operations_partners=(
            _sentence(_문장, _소유_인용),
            _sentence("회사는 파트너 협력 조직을 별도로 운영한다.", ("n1",)),
        ),
    )
    result, dropped = drop_cross_section_duplicates(
        report, fragments=None, moved_facts_sink=sink
    )
    assert dropped == 1 and sink, "전제: culture 문장이 7장으로 넘어가야 한다"
    return result, sink


_7장_표시 = "7장 «사업 운영과 파트너 구조»"


def test_원래_의역_짝은_옮기지_않아_이동_장부도_안내문도_없다() -> None:
    """기대 변경 (2026-09-23 총괄 확정) — 종전 재현 경로는 이 의역 짝으로 8장을 비웠다.

    「유수의」가 빠지고 「체결하여」가 「통해」로 바뀌어 삭제 증명이 없다 → 8장 문장이
    남고, 남은 장에는 이동 안내문도 장부도 생기지 않는다.
    """
    sink: list[MovedFactRecord] = []
    report = _report(
        culture=(_sentence(_문장, ("n1",)),),
        operations_partners=(
            _sentence(_문장_변형, ("n1",)),
            _sentence("회사는 파트너 협력 조직을 별도로 운영한다.", ("n1",)),
        ),
    )
    result, dropped = drop_cross_section_duplicates(report, moved_facts_sink=sink)
    assert dropped == 0 and sink == []
    assert [sentence.text for sentence in _section(result, "culture").sentences] == [_문장]
    assert _section(result, "culture").notice == ""


def test_이동_근거가_살아_있으면_대상_장_번호와_제목을_적는다() -> None:
    result, sink = _dropped_report_and_ledger()
    reconciled = reconcile_section_notices(
        result, frozenset(), moved_facts=sink
    )
    notice = _section(reconciled, "culture").notice
    assert notice == NOTICE_MOVED_TARGETS_TEMPLATE.format(targets=_7장_표시)
    assert "아래" not in notice


def test_대상_장에_다른_문장만_있으면_이동을_단언하지_않는다() -> None:
    result, sink = _dropped_report_and_ledger()
    # 뒤 단계가 소유 장의 «그 문장»을 지웠다고 치자 — 다른 문장 하나는 남는다.
    swapped = ComposedReport(
        sections=tuple(
            replace(section, sentences=(_sentence(_무관한_문장, ("n9",)),))
            if section.section_id == "operations_partners"
            else section
            for section in result.sections
        ),
        summary=result.summary,
    )
    reconciled = reconcile_section_notices(
        swapped, frozenset(), moved_facts=sink
    )
    assert _section(reconciled, "culture").notice == NOTICE_INSUFFICIENT_EVIDENCE
    # 장부를 안 주면 종전 판정 그대로 — 대상 장에 문장이 있으니 이동 문구 유지.
    legacy = reconcile_section_notices(swapped, frozenset())
    assert _section(legacy, "culture").notice == NOTICE_DUPLICATE_MOVED


def test_남은_자료는_실제_종류로_부른다() -> None:
    result, sink = _dropped_report_and_ledger()
    with_flow = ComposedReport(
        sections=tuple(
            replace(
                section,
                flow_rows=(FlowRow(cells=("가", "나", "다"), citations=("n1",)),),
            )
            if section.section_id == "culture"
            else section
            for section in result.sections
        ),
        summary=result.summary,
    )
    reconciled = reconcile_section_notices(
        with_flow, frozenset(), moved_facts=sink
    )
    notice = _section(reconciled, "culture").notice
    assert notice.endswith(
        NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE.format(materials="도식")
    )
    assert "아래" not in notice and "표" not in notice


def test_근거_없이_자료만_남으면_자료_부족_문구에_종류를_적는다() -> None:
    section = ComposedSection(
        section_id="culture",
        sentences=(),
        notice=NOTICE_DUPLICATE_MOVED,
        news_rows=(
            NewsRow(cells=("2026-09-01", "가나다경제", "본문"), citations=("n1",)),
        ),
    )
    report = ComposedReport(
        sections=tuple(
            section if section_id == "culture"
            else ComposedSection(section_id=section_id, sentences=())
            for section_id in SECTION_IDS
        )
    )
    reconciled = reconcile_section_notices(report, frozenset(), moved_facts=())
    assert _section(reconciled, "culture").notice == (
        NOTICE_ONLY_MATERIALS_TEMPLATE.format(materials="보도 목록")
    )


def test_강화_대조는_멱등이다() -> None:
    result, sink = _dropped_report_and_ledger()
    once = reconcile_section_notices(result, frozenset(), moved_facts=sink)
    twice = reconcile_section_notices(once, frozenset(), moved_facts=sink)
    assert [section.notice for section in twice.sections] == [
        section.notice for section in once.sections
    ]


def test_장부는_삭제후보가_아닌_실제_소유후보만_기록한다() -> None:
    result, sink = _dropped_report_and_ledger()
    owner = _section(result, "operations_partners")
    assert sink[0].owner_fingerprints == (moved_owner_fingerprint(owner.sentences[0]),)
    assert moved_owner_fingerprint(owner.sentences[1]) not in sink[0].owner_fingerprints
    assert moved_owner_fingerprint(_sentence(_문장, ("n1",))) not in sink[0].owner_fingerprints


@pytest.mark.parametrize(
    "mutation",
    ("text", "whitespace", "number", "citations", "citation_order", "slot", "grade"),
)
def test_소유후보의_본문과_주장결속이_바뀌면_이동안내를_내린다(mutation: str) -> None:
    text = (
        "가나다전자는 2025년 국내 기업 고객을 대상으로 보안 진단 솔루션을 "
        "공급해 계약 기업 수가 10개사로 늘었다."
    )
    original = ComposedSentence(
        text=text, citations=("a", "b"), grade="확인",
        planned_claim_slot="past_changes:historical_performance",
        verification_state="verified",
    )
    extra = _sentence("가나다전자는 생산 시설 운영 조직을 별도로 관리하고 있다.", ("a",))
    ledger: list[MovedFactRecord] = []
    result, dropped = drop_cross_section_duplicates(
        _report(business_model=(original,), past_changes=(original, extra)),
        moved_facts_sink=ledger,
    )
    assert dropped == 1 and ledger[0].owner_section_id == "past_changes"
    changed = {
        "text": replace(original, text=text.replace("국내", "해외")),
        "whitespace": replace(original, text=text + " "),
        "number": replace(original, text=text.replace("2025년", "2026년").replace("10개사", "20개사")),
        "citations": replace(original, citations=("a", "c")),
        "citation_order": replace(original, citations=("b", "a")),
        "slot": replace(original, planned_claim_slot="past_changes:change_context"),
        "grade": replace(original, grade="해석"),
    }[mutation]
    swapped = replace(result, sections=tuple(
        replace(section, sentences=(changed, extra))
        if section.section_id == "past_changes" else section
        for section in result.sections
    ))
    final = reconcile_section_notices(swapped, frozenset(), moved_facts=ledger)
    assert _section(final, "business_model").notice == NOTICE_INSUFFICIENT_EVIDENCE


def test_소유지문_없는_옛_장부는_유사도로_복구하지_않는다() -> None:
    result, sink = _dropped_report_and_ledger()
    legacy = [replace(record, owner_fingerprints=()) for record in sink]
    final = reconcile_section_notices(result, frozenset(), moved_facts=legacy)
    assert _section(final, "culture").notice == NOTICE_INSUFFICIENT_EVIDENCE


def test_기록한_소유후보가_다른_장에만_있으면_이동성공이_아니다() -> None:
    result, sink = _dropped_report_and_ledger()
    owner = _section(result, "operations_partners")
    changed = replace(result, sections=tuple(
        replace(section, sentences=()) if section.section_id == "operations_partners"
        else replace(section, sentences=owner.sentences) if section.section_id == "identity"
        else section for section in result.sections
    ))
    final = reconcile_section_notices(changed, frozenset(), moved_facts=sink)
    assert _section(final, "culture").notice == NOTICE_INSUFFICIENT_EVIDENCE


def test_같은_무리에_실제로_남긴_소유후보_중_하나가_살면_이동안내를_유지한다() -> None:
    first = _sentence("가나다전자는 보안 진단 서비스의 개발과 유지보수를 맡아 고객의 시스템 운영을 지원한다.", ("f",))
    second = replace(first, text=first.text + " ")
    sink: list[MovedFactRecord] = []
    result, dropped = drop_cross_section_duplicates(
        _report(identity=(first,), operations_partners=(first, second)),
        moved_facts_sink=sink,
    )
    assert dropped == 1 and len(sink[0].owner_fingerprints) == 2
    changed = replace(result, sections=tuple(
        replace(section, sentences=(second,)) if section.section_id == "operations_partners"
        else section for section in result.sections
    ))
    final = reconcile_section_notices(changed, frozenset(), moved_facts=sink)
    assert _section(final, "identity").notice == NOTICE_MOVED_TARGETS_TEMPLATE.format(targets=_7장_표시)

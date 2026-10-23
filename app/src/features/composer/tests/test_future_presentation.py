"""실제 계획 발언의 확인 한계가 독자 표시에서 빠지는 회귀를 막는다."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.future_presentation import section_guidance_lines
from src.features.composer.future_presentation_constants import PLAN_SCOPE_NOTICE
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.public_manifest import _expected_public_content_projection
from src.features.composer.render import render_report


@pytest.fixture
def actual():
    return json.loads((Path(__file__).parent / "fixtures" /
        "fourteenth_future_presentation.json").read_text(encoding="utf-8"))


def _sentence(actual, **changes):
    return replace(ComposedSentence(
        actual["claim"], ("93",), "확인",
        planned_claim_slot=actual["claim_slot"], verification_state="verified",
    ), **changes)


def test_실제14_발언과_원문은_변경하지_않고_계획의_확인범위를_표시한다(actual):
    sentence = _sentence(actual)
    section = ComposedSection("future_strategy", (sentence,))
    assert hashlib.sha256(actual["source"].encode()).hexdigest() == actual["source_sha256"]
    assert section_guidance_lines(section) == [PLAN_SCOPE_NOTICE]
    assert section.sentences == (sentence,)
    assert sentence.text == actual["claim"]
    assert section.notice == ""
    assert "취소" not in PLAN_SCOPE_NOTICE and "미실행" not in PLAN_SCOPE_NOTICE


@pytest.mark.parametrize("changes", [
    {"verification_state": "unverified"},
    {"verification_state": "rejected"},
    {"planned_claim_slot": ""},
    {"planned_claim_slot": "operations_partners:relationship"},
    {"grade": "해석"},
    {"citations": ()},
])
def test_검수된_명시계획_외의_문장으로_안내를_만들지_않는다(actual, changes):
    section = ComposedSection("future_strategy", (_sentence(actual, **changes),))
    assert section_guidance_lines(section) == []


def test_다른장과_빈장은_기존안내만_보존한다(actual):
    for section_id in SECTION_IDS:
        if section_id != "future_strategy":
            section = ComposedSection(section_id, (_sentence(actual),), notice="기존 안내")
            assert section_guidance_lines(section) == ["기존 안내"]
    assert section_guidance_lines(ComposedSection("future_strategy", ())) == []


def test_본문이나_안내에_이미_같은한계가_있으면_중복하지_않는다(actual):
    annotated = _sentence(actual, text=actual["claim"] +
        "(이 자료의 확인 범위: 현재 이행·유지 상태 미확인)")
    section = ComposedSection("future_strategy", (annotated,))
    assert section_guidance_lines(section) == []
    assert section_guidance_lines(replace(section, sentences=(_sentence(actual),),
        notice=PLAN_SCOPE_NOTICE)) == [PLAN_SCOPE_NOTICE]
    assert section_guidance_lines(replace(section, sentences=(annotated, _sentence(actual)))) == [PLAN_SCOPE_NOTICE]


def test_현재진행_상태가_함께_있어도_계획발언만의_한계로_표시한다(actual):
    plan = _sentence(actual)
    status = _sentence(actual, text="회사는 새 서비스의 시범 운영을 진행 중이라고 밝혔다.",
        planned_claim_slot="future_strategy:plan_status", citations=("94",))
    section = ComposedSection("future_strategy", (plan, status))
    assert section_guidance_lines(section) == [PLAN_SCOPE_NOTICE]
    assert "해당 발언만으로" in PLAN_SCOPE_NOTICE
    assert "현재 이행·유지 상태 미확인" not in PLAN_SCOPE_NOTICE
    assert section.sentences == (plan, status)
    assert section_guidance_lines(replace(section, sentences=(status,))) == []
    assert section_guidance_lines(replace(section, notice=PLAN_SCOPE_NOTICE)) == [PLAN_SCOPE_NOTICE]


def test_실제14_공개기대구조와_렌더는_같은안내를_표시하고_발언은_유지한다(actual):
    sentence = _sentence(actual)
    report = ComposedReport(tuple(ComposedSection(
        sid, (sentence,) if sid == "future_strategy" else (),
    ) for sid in SECTION_IDS))
    fragments = (CollectedFragment(
        fragment_id="93", kind="언론 보도", text=actual["source"],
        source_url=actual["source_url"], document_date=actual["source_date"],
    ),)
    kwargs = dict(company_name="토스페이먼츠 주식회사", company_id="00123456",
        corp_type="기업", generated_at="2026-10-23", as_of_date="2026-10-23",
        analysis_period="2024~2025", latest_performance_period="2025",
        citation_style="inline", filing_meta=None)
    expected = _expected_public_content_projection(report, fragments, [],
        **kwargs, program_registry_sources=(), industry_anchors=(), industry_problems=())
    rendered = render_report(report=report, fragments=fragments,
        performance_table=None, **kwargs)
    section = next(s for s in rendered.sections if s.cell == "future_strategy")
    projected = next(s for s in expected["sections"] if s["cell"] == "future_strategy")
    assert section.guidance_lines == projected["guidance_lines"] == [PLAN_SCOPE_NOTICE]
    assert actual["claim"] in section.prose_paragraphs[0]
    assert PLAN_SCOPE_NOTICE not in section.prose_paragraphs[0]
    assert report.sections[5].sentences[0] is sentence

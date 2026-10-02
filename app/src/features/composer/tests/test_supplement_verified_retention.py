"""보충 작성이 모두 탈락해도 이전 검증 사실을 잃거나 안전 검사를 우회하지 않는다."""

import json
from dataclasses import replace
import pytest

from src.features.composer import pipeline
from src.features.composer.constants import SECTION_IDS
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.structured_claims import enforce_public_numeric_safety
from src.features.composer.tests.test_full_official_numeric_precull import _fragment
from src.features.composer.tests.test_section_public_manifest import (
    _RecoveringPacketWriter, _BoundGroupedReviewer, _NoDiagram,
    _numeric_table_and_fragment, _packets,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.models import VerificationState


def _sentence(text, *, verified=True):
    return ComposedSentence(
        text, ("1",), "확인", planned_claim_slot="identity:corporate_identity",
        verification_state=VerificationState.VERIFIED.value if verified else "unverified",
    )


def _base(*sentences):
    return ComposedReport(tuple(
        ComposedSection(section_id, tuple(sentences) if section_id == "identity" else ())
        for section_id in SECTION_IDS
    ), summary=tuple(sentences[:1]))


def test_보충이_비면_기존_검증본문과_새_검증도식을_유지하고_비대상장은_그대로다():
    old = _sentence("가나다전자는 설립된 법인이다.")
    pending = _sentence("검수하지 않은 설명이다.", verified=False)
    base = _base(old, pending)
    row = FlowRow(("공식 설립 기록", "법인 등록 확인"), ("1",))
    replacement = ComposedReport((ComposedSection("identity", (), flow_rows=(row,)),))
    result = pipeline._merge_selected_sections(base, replacement, ("identity",))
    assert result.sections[0].sentences == (old,)
    assert result.sections[0].flow_rows == (row,)
    assert all(a is b for a, b in zip(base.sections[1:], result.sections[1:]))
    assert result.summary == ()


def test_보충의_새_검증본문이_있으면_기존_교체계약을_유지한다():
    old = _sentence("가나다전자는 설립된 법인이다.")
    newer = _sentence("가나다전자는 공식 명칭을 변경했다.")
    replacement = ComposedReport((ComposedSection("identity", (newer,)),))
    result = pipeline._merge_selected_sections(_base(old), replacement, ("identity",))
    assert result.sections[0].sentences == (newer,)


def test_기존_미검증본문만_있으면_빈_보충에서_복구하지_않는다():
    pending = _sentence("검수하지 않은 설명이다.", verified=False)
    replacement = ComposedReport((ComposedSection("identity", ()),))
    result = pipeline._merge_selected_sections(_base(pending), replacement, ("identity",))
    assert result.sections[0].sentences == ()


def test_새_문장이_다른_의미칸을_채워도_기존_검증_의미칸은_보존한다():
    old = _sentence("가나다전자는 설립된 법인이다.")
    newer = replace(_sentence("가나다전자는 계측 장비를 제조한다."),
                    planned_claim_slot="identity:business_definition")
    replacement = ComposedReport((ComposedSection("identity", (newer,)),))
    diagnostics = []
    result = pipeline._merge_selected_sections(
        _base(old), replacement, ("identity",), retention_diagnostics=diagnostics,
    )
    assert result.sections[0].sentences == (newer, old)
    assert len(diagnostics) == 1 and diagnostics[0]["section_id"] == "identity"


def test_같은_문장을_다른_의미칸으로_복제하거나_미등록칸을_복원하지_않는다():
    old = _sentence("가나다전자는 계측 장비를 제조한다.")
    newer = replace(old, planned_claim_slot="identity:business_definition")
    wrong_section = replace(_sentence("운영 협력의 설명이다."),
                            planned_claim_slot="operations_partners:value_chain")
    unknown = replace(_sentence("미등록 질문의 설명이다."),
                      planned_claim_slot="identity:unknown")
    replacement = ComposedReport((ComposedSection("identity", (newer,)),))
    diagnostics = []
    result = pipeline._merge_selected_sections(
        _base(old, wrong_section, unknown), replacement, ("identity",),
        retention_diagnostics=diagnostics,
    )
    assert result.sections[0].sentences == (newer,)
    assert diagnostics == []


def test_복구한_이전본문도_병합후_같은_공식숫자검사를_다시_거친다():
    invalid = _sentence("가나다전자는 2029년에 설립됐다.")
    replacement = ComposedReport((ComposedSection("identity", ()),))
    merged = pipeline._merge_selected_sections(_base(invalid), replacement, ("identity",))
    filtered, observation = enforce_public_numeric_safety(
        merged, strict_cited_fragments={"1": _fragment("1", "가나다전자는 설립된 법인이다.")},
    )
    assert filtered.sections[0].sentences == ()
    assert observation.removed_total == 1


@pytest.mark.parametrize("new_slot", ("identity:corporate_identity", "identity:business_definition"))
def test_새_미검증_동일본문이_기존_검증본문을_밀어내거나_중복시키지_않는다(new_slot):
    old = _sentence("가나다전자는 계측 장비를 제조한다.")
    pending = replace(old, planned_claim_slot=new_slot, grade="해석",
                      verification_state="unverified")
    replacement = ComposedReport((ComposedSection("identity", (pending,)),))
    result = pipeline._merge_selected_sections(_base(old), replacement, ("identity",))
    assert result.sections[0].sentences == (old,)


def test_FULL_보충이_전부탈락해도_부분공개에서_기존검증사실은_남고_하한은_유지된다():
    class EmptyAfterReviewWriter(_RecoveringPacketWriter):
        def __init__(self):
            super().__init__(("identity",))
            self.original = ""

        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            if "1" in payload["문장들"][0]["인용"]:
                if self.section_calls["identity"] == 1:
                    self.original = payload["문장들"][0]["글"]
                else:
                    for item in payload["문장들"]:
                        item["글"] = "2029년에 " + item["글"]
            return json.dumps(payload, ensure_ascii=False)

    writer = EmptyAfterReviewWriter()
    reviewer = _BoundGroupedReviewer()
    output = pipeline.run_v2(
        "가나다전자", (), _numeric_table_and_fragment()[0],
        writer_ask=writer, reviewer_ask=reviewer, diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL, section_evidence_packets=_packets(),
        company_id="00123456", build_identity_sha256="b" * 64,
        evidence_available_fallback=True,
    )
    public_texts = [fact.claim for fact in output.report.fact_records]
    assert writer.original in public_texts
    assert all("2029년" not in text for text in public_texts)
    assert output.degraded_reason == "quality_floor"
    assert output.effective_release_mode != ReleaseMode.FULL.value
    assert len(writer.prompts) == 10
    assert len(reviewer.prompts) == 2

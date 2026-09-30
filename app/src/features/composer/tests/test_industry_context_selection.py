"""산업 앵커 정상 선택 탈락과 원문 변조를 구분하는 독립 반례."""
from dataclasses import replace

import pytest

from src.features.composer.industry_context import select_industry_context_for_fragments
from src.features.composer.tests.test_industry_context import _materials


def _inputs():
    _, fragment, anchor, problem, _ = _materials()
    anchor = replace(anchor, anchor_id=fragment.fragment_id)
    problem = replace(problem, business_anchor_id=anchor.anchor_id)
    return fragment, anchor, problem


def _select(fragment, anchor, problem, *, original=None, selected=None, **changes):
    arguments = dict(anchors=(anchor,), problems=(problem,),
                     original_fragments=(fragment,) if original is None else original,
                     selected_fragments=(fragment,) if selected is None else selected,
                     company_id=anchor.company_id)
    arguments.update(changes)
    return select_industry_context_for_fragments(**arguments)


def test_같은_원공식근거가_선택되면_산업관계를_보존한다():
    fragment, anchor, problem = _inputs()
    anchors, problems, dropped = _select(fragment, anchor, problem)
    assert anchors == (anchor,)
    assert problems == (problem,)
    assert dropped == 0


def test_정상_미선정이면_연결_산업문제만_제외한다():
    fragment, anchor, problem = _inputs()
    anchors, problems, dropped = _select(fragment, anchor, problem, selected=())
    assert anchors == () and problems == ()
    assert dropped == 1


def test_원근거가_없으면_선택탈락으로_숨기지_않는다():
    fragment, anchor, problem = _inputs()
    with pytest.raises(ValueError):
        _select(fragment, anchor, problem, original=(), selected=())


@pytest.mark.parametrize('field,value', [
    ('document_content_sha256', 'f' * 64),
    ('identity_binding', '다른 법인의 공식 근거'),
    ('source_publisher', '다른 발행처'),
    ('document_date', '2026-09-02'),
    ('location', '다른 위치'),
])
def test_선택된_같은번호의_근거변조는_조용히_제외하지_않는다(field, value):
    fragment, anchor, problem = _inputs()
    altered = replace(fragment, **{field: value})
    with pytest.raises(ValueError):
        _select(fragment, anchor, problem, selected=(altered,))


@pytest.mark.parametrize('field,value', [
    ('document_content_sha256', 'f' * 64),
    ('identity_binding', '다른 법인의 공식 근거'),
    ('company_id', '00000002'),
])
def test_미선정이라도_원앵커_변조는_먼저_차단한다(field, value):
    fragment, anchor, problem = _inputs()
    altered = replace(anchor, **{field: value})
    with pytest.raises(ValueError):
        _select(fragment, altered, problem, selected=(), company_id=anchor.company_id)


def test_없는_앵커를_참조한_산업문제도_차단한다():
    fragment, anchor, problem = _inputs()
    with pytest.raises(ValueError):
        _select(fragment, anchor, replace(problem, business_anchor_id='missing'), selected=())


@pytest.mark.parametrize('altered_first', [False, True])
def test_선택원문_번호충돌을_정상선택으로_보지_않는다(altered_first):
    fragment, anchor, problem = _inputs()
    altered = replace(fragment, source_publisher='다른 발행처')
    selected = (altered, fragment) if altered_first else (fragment, altered)
    with pytest.raises(ValueError):
        _select(fragment, anchor, problem, selected=selected)


def test_공개상한_뒤의_원앵커_변조도_차단한다():
    fragment, anchor, problem = _inputs()
    bad_anchor = replace(anchor, anchor_id='second', document_content_sha256='f' * 64)
    bad_problem = replace(problem, evidence_id='second-problem', business_anchor_id='second')
    with pytest.raises(ValueError):
        _select(fragment, anchor, problem, selected=(),
                anchors=(anchor, bad_anchor), problems=(problem, bad_problem))


def test_FULL_실제입력선택_뒤의_산업탈락은_보고서전체를_실패시키지_않는다():
    from src.features.composer.pipeline import run_v2, _prepare_section_evidence_packets
    from src.features.composer.tests.test_evidence_available_report import _StrictThinWriter
    from src.features.composer.tests.test_pipeline import _strict_packet_set, _FakeReviewer
    from src.features.composer.tests.injected_program_fixture import make_numeric_performance_evidence
    from src.shared.report_evidence.constants import ReleaseMode

    fragment, anchor, problem = _inputs()
    company_id = '00123456'
    binding = '회사코드 00123456의 공식 사업보고서'
    fragment = replace(fragment, identity_binding=binding)
    anchor = replace(anchor, company_id=company_id, identity_binding=binding)
    packets = _strict_packet_set()
    selected = _prepare_section_evidence_packets(packets).flat_union
    performance, _, filing_meta = make_numeric_performance_evidence(fragment_number=9)
    diagnostics = []
    writer = _StrictThinWriter()
    output = run_v2('가나다전자', (*selected, fragment), performance,
                    writer_ask=writer, reviewer_ask=_FakeReviewer(),
                    release_mode=ReleaseMode.FULL, section_evidence_packets=packets,
                    filing_meta=filing_meta, company_id=company_id,
                    build_identity_sha256='b' * 64, evidence_available_fallback=True,
                    industry_anchors=(anchor,), industry_problems=(problem,),
                    composition_diagnostics_sink=diagnostics)
    selection = [row for row in diagnostics if '공식사업근거_선택탈락' in row]
    assert len(selection) == 1
    assert selection[0]['수집산업문제'] == 1
    assert selection[0]['선택산업문제'] == 0
    assert selection[0]['공식사업근거_선택탈락'] == 1
    assert not any(section.industry_contexts for section in output.report.sections)
    assert output.downgraded_from_release_mode == ReleaseMode.FULL.value
    assert len(writer.prompts) == 9

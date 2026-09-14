# -*- coding: utf-8 -*-
"""인용이 아닌 대괄호 숫자가 보고서 전체를 차단하지 못하게 하는 반례.

실측 사고(2026-09-14 운영 실행): 기사 원문이 회사 이름 뒤에 종목코드를
「회사이름[숫자여섯자리]」로 달고 들어왔고, 그 원문을 «글자 그대로» 옮긴
본문 문장이 출고 검사에서 「본문이 인용한 번호가 부록에 없습니다」로 읽혀
검수까지 통과한 보고서 전체가 버려졌다.
"""

import json

import pytest

from src.core import news_intake_switch
from src.features.composer import pipeline as pipeline_module
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.stray_citation_marker import sanitize_stray_citation_markers
from src.features.composer.stray_citation_marker_constants import (
    CITATION_MARKER_ITEM,
    CITATION_MARKER_NOT_IN_CITATIONS,
)
from src.features.composer.tests.test_news_alternatives import _full
from src.features.composer.tests.test_news_block_channels import _news_fragment
from src.shared.report_quality.output_validation import (
    CITATION_MARKER_RE,
    V2ValidationError,
)
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


#: 합성 종목코드 — 특정 회사 값을 시험에 적지 않는다.
STRAY_CODE = "[123456]"
NEWS_ID = "881"
NEWS_TEXT = f"가나다전자{STRAY_CODE}는 신규 설비 공급 계약을 체결했다고 밝혔다."


@pytest.fixture(autouse=True)
def _enabled_news(monkeypatch):
    monkeypatch.setenv("NEWS_INTAKE", "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _fragment(fragment_id: str = "3", text: str = "가나다전자는 설비를 공급한다."):
    return CollectedFragment(fragment_id=fragment_id, kind="dart", text=text)


def _report(*sentences: ComposedSentence, notice: str = ""):
    return ComposedReport(
        (ComposedSection("business_model", tuple(sentences), notice=notice),)
    )


# ══════════════════════════════════════════════════════════
# ① 일반 산문 — 글자로 남기고 «자기 인용 번호»는 건드리지 않는다
# ══════════════════════════════════════════════════════════


def test_인용이아닌_대괄호숫자는_전각글자로_남고_내용이_보존된다():
    fragment = _fragment()
    sentence = ComposedSentence(
        f"가나다전자{STRAY_CODE}는 설비를 공급한다.", ("3",), "확인",
    )
    result = sanitize_stray_citation_markers(_report(sentence), (fragment,))

    kept = result.sections[0].sentences[0].text
    assert kept == "가나다전자［123456］는 설비를 공급한다."
    # 내용(숫자·낱말)은 한 글자도 잃지 않는다.
    assert "123456" in kept
    # 출고 검사가 읽는 «그» 정규식으로 더 이상 인용이 아니다.
    assert CITATION_MARKER_RE.findall(kept) == []
    # 문장은 빠지지 않았고, 뺀 기록도 없다.
    assert len(result.sections[0].sentences) == 1


def test_문장이_실제로_인용한_번호와_같은_대괄호숫자는_그대로_둔다():
    fragment = _fragment()
    sentence = ComposedSentence("가나다전자는 설비를 공급한다. [3]", ("3",), "확인")
    result = sanitize_stray_citation_markers(_report(sentence), (fragment,))

    assert result.sections[0].sentences[0].text.endswith("[3]")
    assert result == _report(sentence)


def test_같은_문장에서_자기번호는_남기고_아닌번호만_바꾼다():
    fragment = _fragment()
    sentence = ComposedSentence(
        f"가나다전자{STRAY_CODE}는 설비를 공급한다. [3]", ("3",), "확인",
    )
    result = sanitize_stray_citation_markers(_report(sentence), (fragment,))

    text = result.sections[0].sentences[0].text
    assert text == "가나다전자［123456］는 설비를 공급한다. [3]"
    assert CITATION_MARKER_RE.findall(text) == ["3"]


def test_다른_문장의_인용번호는_이_문장의_인용이_아니다():
    fragments = (_fragment("3"), _fragment("7", "다른 조각 원문."))
    sentence = ComposedSentence("가나다전자는 [7] 설비를 공급한다.", ("3",), "확인")
    result = sanitize_stray_citation_markers(_report(sentence), fragments)

    assert result.sections[0].sentences[0].text == "가나다전자는 ［7］ 설비를 공급한다."


def test_고쳐야_할_글자가_없으면_입력을_그대로_돌려준다():
    fragment = _fragment()
    report = _report(ComposedSentence("가나다전자는 설비를 공급한다.", ("3",), "확인"))
    assert sanitize_stray_citation_markers(report, (fragment,)) == report


def test_전각으로_바꾼_뒤_다시_불러도_더_바뀌지_않는다():
    fragment = _fragment()
    report = _report(
        ComposedSentence(f"가나다전자{STRAY_CODE}는 설비를 공급한다.", ("3",), "확인")
    )
    once = sanitize_stray_citation_markers(report, (fragment,))
    assert sanitize_stray_citation_markers(once, (fragment,)) == once


def test_자릿수가_아주_긴_대괄호숫자도_정수변환없이_글자로_남긴다():
    # CPython의 문자열→정수 자릿수 상한에 걸려 죽지 않아야 한다.
    fragment = _fragment()
    huge = "1" * 5000
    sentence = ComposedSentence(f"가나다전자[{huge}]는 설비를 공급한다.", ("3",), "확인")
    result = sanitize_stray_citation_markers(_report(sentence), (fragment,))

    assert result.sections[0].sentences[0].text.startswith("가나다전자［1")
    assert CITATION_MARKER_RE.findall(result.sections[0].sentences[0].text) == []


def test_장_안내문의_대괄호숫자도_같은_함수가_글자로_굳힌다():
    fragment = _fragment()
    report = _report(notice=f"자료{STRAY_CODE}를 찾지 못했습니다.")
    result = sanitize_stray_citation_markers(report, (fragment,))

    assert result.sections[0].notice == "자료［123456］를 찾지 못했습니다."


# ══════════════════════════════════════════════════════════
# ② 원문을 글자 그대로 옮긴 문장 — 고치지 않고 빼며 사유를 남긴다
# ══════════════════════════════════════════════════════════


def test_축자_문장은_글자를_고치지_않고_빼고_닫힌_사유코드를_남긴다():
    fragment = _news_fragment(NEWS_ID, "2026-09-01", NEWS_TEXT, section_id="business_model")
    sentence = ComposedSentence(
        f"(2026-09-01 가나다일보) {NEWS_TEXT}", (NEWS_ID,), "확인",
    )
    diagnostics: list[dict] = []
    result = sanitize_stray_citation_markers(
        _report(sentence), (fragment,), diagnostics=diagnostics,
    )

    assert result.sections[0].sentences == ()
    assert len(diagnostics) == 1
    assert diagnostics[0]["reason_code"] == CITATION_MARKER_NOT_IN_CITATIONS
    assert diagnostics[0]["section_id"] == "business_model"
    assert diagnostics[0]["kind"] == "본문"
    assert diagnostics[0]["verification_items"] == (CITATION_MARKER_ITEM,)
    # 원문·문장 글자는 진단에 남기지 않는다.
    assert NEWS_TEXT not in json.dumps(diagnostics, ensure_ascii=False)
    # 전송 계약이 이 진단을 실제로 통과시킨다(닫힌 목록 등록 확인).
    assert len(observed_review_outcomes(diagnostics)) == 1


def test_축자_문장이어도_고칠_글자가_없으면_그대로_싣는다():
    clean = "가나다전자는 신규 설비 공급 계약을 체결했다고 밝혔다."
    fragment = _news_fragment(NEWS_ID, "2026-09-01", clean, section_id="business_model")
    sentence = ComposedSentence(f"(2026-09-01 가나다일보) {clean}", (NEWS_ID,), "확인")
    diagnostics: list[dict] = []
    result = sanitize_stray_citation_markers(
        _report(sentence), (fragment,), diagnostics=diagnostics,
    )

    assert result.sections[0].sentences == (sentence,)
    assert diagnostics == []


def test_사유코드가_공유_전송계약의_항목이름과_같은_값이다():
    assert REVIEW_SCOPE_ITEMS[CITATION_MARKER_NOT_IN_CITATIONS] == CITATION_MARKER_ITEM


# ══════════════════════════════════════════════════════════
# ③ 배선 — 실제 파이프라인이 이 함수를 부르고 렌더 입력에 반영한다
# ══════════════════════════════════════════════════════════


def _news_run(monkeypatch):
    """운영 run_v2(FULL)를 그대로 돌리되 이 함수의 «실제 호출 인자»를 받아 둔다."""

    calls: list[tuple] = []
    original = pipeline_module.sanitize_stray_citation_markers

    def recording(report, fragments, **kwargs):
        calls.append((report, fragments, kwargs))
        return original(report, fragments, **kwargs)

    monkeypatch.setattr(pipeline_module, "sanitize_stray_citation_markers", recording)
    fragment = _news_fragment(
        NEWS_ID, "2026-09-01", NEWS_TEXT, section_id="business_model",
    )
    output, _writer, _reviewer = _full((fragment,))
    return output, calls


def test_운영경로가_축자_뉴스문장의_대괄호숫자를_출고전에_처리한다(monkeypatch):
    output, calls = _news_run(monkeypatch)

    # 실제 호출 인자 — 정리 «전» 보고서에 그 글자가 실제로 들어 있었다.
    assert calls, "파이프라인이 정리 함수를 부르지 않았습니다"
    before = [
        sentence.text
        for report, _fragments, _kwargs in calls
        for section in report.sections
        for sentence in section.sentences
    ]
    assert any(STRAY_CODE in text for text in before)
    assert any(
        NEWS_ID in {str(fragment.fragment_id) for fragment in fragments}
        for _report, fragments, _kwargs in calls
    )

    # 렌더 결과 — 그 글자는 더 이상 인용으로 읽히지 않는다.
    body = " ".join(
        text for section in output.report.sections for text, _cite in section.prose_lines
    )
    assert STRAY_CODE not in body
    assert any(
        diagnostic["reason_code"] == CITATION_MARKER_NOT_IN_CITATIONS
        for diagnostic in output.review_diagnostics
    )


def test_정리를_빼면_같은_실행이_출고검증에서_통째로_차단된다(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "sanitize_stray_citation_markers",
        lambda report, fragments, **kwargs: report,
    )
    fragment = _news_fragment(
        NEWS_ID, "2026-09-01", NEWS_TEXT, section_id="business_model",
    )
    with pytest.raises(V2ValidationError) as error:
        _full((fragment,))
    assert any("부록에 없습니다" in problem for problem in error.value.problems)

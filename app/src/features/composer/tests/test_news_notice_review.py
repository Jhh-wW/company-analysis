"""뉴스 확인 범위·대체 후보 안내의 독립 계약 회귀."""

from dataclasses import replace

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.news_usage import (
    append_research_notice,
    retain_verified_news,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.pipeline.news_research_context import public_news_research_status


def _fragment(fragment_id: str = "woori-news-1", *, text: str = "우리은행은 기업금융 서비스를 확대했다고 보도됐다."):
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="typed-evidence-v1:news",
        formal_source_kind="news",
        text=text,
        source_url="https://media.example/woori-news",
        document_date="2026-09-01",
        document_identity="article:woori-news",
        source_publisher="가나다경제",
        news_grounded=True,
    )


def _report(*sections: ComposedSection) -> ComposedReport:
    return ComposedReport(sections=sections)


def _sentence(fragment, *, alternative: bool = False, text: str | None = None):
    return ComposedSentence(
        text or f"{fragment.document_date} {fragment.source_publisher} 보도에 따르면, {fragment.text}",
        (fragment.fragment_id,),
        GRADE_CONFIRMED,
        verification_state="verified",
        news_source_alternative=alternative,
    )


def test_woori_diagnostic_separates_search_access_analysis_verification_and_limit():
    step = {
        "step": "5b_뉴스_수집",
        "독립기사": 0,
        "검색상태": "success",
        "실패": "fetch_robots",
        "실패사유": ("fetch_robots", "grounded_response_incomplete"),
        "검증미완료": ("source_review_pending",),
        "상한사유": ("window_body_budget",),
    }

    status = public_news_research_status([step], enabled=True)

    assert status["검색완료"] is True
    assert status["관측문제"] == ("접속", "분석", "검증")
    assert status["상한도달"] is True
    assert status["독립기사"] == 0
    assert status["상태"] == "failed"

    report = _report(ComposedSection("identity", ()))
    notice = append_research_notice(report, status).sections[0].notice
    assert "뉴스 검색 요청은 완료했습니다." in notice
    assert "일부 기사에 접속하거나 본문을 읽지 못했습니다." in notice
    assert "일부 기사 본문 분석을 완료하지 못했습니다." in notice
    assert "일부 후보의 법인·원문·시점 등 근거 확인을 마치지 못했습니다." in notice
    assert "조사 상한에 도달해 일부 후보를 끝까지 확인하지 못했습니다." in notice
    assert "관련 보도가 없다는 뜻은 아닙니다." in notice


def test_noop_news_diagnostic_cannot_claim_successful_supplement():
    status = public_news_research_status(
        [{"step": "5b_뉴스_수집", "독립기사": 0, "완전성": "sufficient"}],
        enabled=True,
    )

    assert status["상태"] == "insufficient"
    notice = append_research_notice(
        _report(ComposedSection("identity", ())), status
    ).sections[0].notice
    assert "보강했습니다" not in notice
    assert "보강 근거가 부족했습니다" in notice

    # 상위 진단이 낡은 상태값을 보내더라도 공개 문구는 성공 보강을 주장하지 않는다.
    stale_ok_notice = append_research_notice(
        _report(ComposedSection("identity", ())),
        {"상태": "ok", "독립기사": 0},
    ).sections[0].notice
    assert "보강했습니다" not in stale_ok_notice


def test_normal_writer_survives_without_overstated_alternative_failure_notice():
    writer_fragment = _fragment("writer")
    alternative_fragment = replace(writer_fragment, fragment_id="alternative")
    report = _report(
        ComposedSection(
            "business_model",
            (_sentence(writer_fragment), _sentence(alternative_fragment, alternative=True)),
        )
    )
    diagnostics = []

    retained = retain_verified_news(
        report,
        (writer_fragment, alternative_fragment),
        review_input=report,
        diagnostics=diagnostics,
    )

    assert [sentence.citations for sentence in retained.sections[0].sentences] == [("writer",)]
    assert [row["사유코드"] for row in diagnostics] == ["duplicate_source"]
    final = append_research_notice(retained, None)
    assert final.sections[0].notice == ""


def test_review_rejection_leaves_no_notice_and_preserves_other_sections_exactly():
    """검수 탈락은 처리 과정이라 장 안내문에 남기지 않는다(2026-09-16 사용자 결정).

    탈락한 장은 문장만 빠지고 안내문은 빈 채로 두며, 사유는 진단 목록에만 남는다.
    다른 장(identity)의 문장·기존 안내는 글자 하나 바뀌지 않는다.
    """
    fragment = _fragment()
    identity_sentence = ComposedSentence("공식 원문 exact 보존", (), GRADE_CONFIRMED)
    base = _report(
        ComposedSection("identity", (identity_sentence,), notice="identity 기존 안내"),
        ComposedSection(
            "business_model",
            (replace(_sentence(fragment), verification_state="unverified"),),
        ),
    )
    diagnostics = []

    retained = retain_verified_news(base, (fragment,), review_input=base, diagnostics=diagnostics)
    final = append_research_notice(retained, None)

    assert [row["사유코드"] for row in diagnostics] == ["not_verified"]
    assert final.sections[0] == base.sections[0]
    assert final.sections[1].sentences == ()
    assert final.sections[1].notice == ""


def test_stale_process_notice_from_research_status_is_not_reintroduced():
    """정상 완료(ok)는 안내문이 없고, 장애 안내는 identity 장에서만 갱신된다."""
    base = _report(
        ComposedSection("identity", (), notice="identity 기존 안내"),
        ComposedSection("business_model", (), notice="다른 장 안내"),
    )

    ok = append_research_notice(base, {"상태": "ok", "독립기사": 3})
    assert ok == base

    failed = append_research_notice(base, {"상태": "failed", "독립기사": 0})
    assert failed.sections[0].notice.startswith("identity 기존 안내\n\n확인 범위:")
    assert failed.sections[1] == base.sections[1]
    assert append_research_notice(failed, {"상태": "failed", "독립기사": 0}) == failed

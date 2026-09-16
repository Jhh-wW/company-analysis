"""뉴스 대체 후보·검수 탈락 처리의 독립 계약 회귀 — 독자용 안내문은 남기지 않는다."""

from dataclasses import replace

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.news_usage import retain_verified_news
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)


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
    assert retained.sections[0].notice == ""


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

    final = retain_verified_news(base, (fragment,), review_input=base, diagnostics=diagnostics)

    assert [row["사유코드"] for row in diagnostics] == ["not_verified"]
    assert final.sections[0] == base.sections[0]
    assert final.sections[1].sentences == ()
    assert final.sections[1].notice == ""



"""외국어 원문 보강과 한국어 뉴스 본문 공개의 경계."""

from dataclasses import replace

from src.features.composer.news_usage import (
    attribution_prefix, news_usage_diagnostics, retain_verified_news,
    supplement_news_candidates,
)
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.test_news_block_channels import _news_fragment


def _source(text):
    return _news_fragment("81", "2026-09-01", text, section_id="business_model")


def _report(*sentences):
    return ComposedReport(sections=(ComposedSection("business_model", sentences),))


def test_foreign_source_is_not_copied_as_raw_fallback():
    source = _source("Kiwoom Securities launched a customer event.")
    original = _report()
    output, added = supplement_news_candidates(original, (source,))
    assert output == original
    assert added == ()
    assert source.text == "Kiwoom Securities launched a customer event."
    details = news_usage_diagnostics(output, (source,))["근거별판정"]
    assert details[0]["사유"] == "한국어본문미확인"


def test_korean_source_still_enters_normal_review():
    source = _source("키움증권은 고객 대상 ISA 가입 행사를 진행한다고 밝혔다.")
    output, added = supplement_news_candidates(_report(), (source,))
    assert added == ("81",)
    sentence = output.sections[0].sentences[0]
    assert sentence.text == attribution_prefix(source) + source.text
    assert sentence.verification_state != "verified"


def test_verified_korean_translation_can_use_foreign_source():
    source = _source("Kiwoom Securities launched a customer event.")
    sentence = ComposedSentence(
        attribution_prefix(source) + "키움증권은 고객 대상 행사를 시작했다.",
        ("81",), "확인", verification_state="verified",
    )
    report = _report(sentence)
    assert retain_verified_news(report, (source,)) == report
    assert not retain_verified_news(
        _report(replace(sentence, verification_state="unverified")), (source,),
    ).sections[0].sentences


def test_korean_attribution_does_not_disguise_english_body():
    source = _source("They were followed by Kiwoom Securities.")
    sentence = ComposedSentence(
        attribution_prefix(source) + source.text,
        ("81",), "확인", verification_state="verified",
    )
    diagnostics = []
    output = retain_verified_news(_report(sentence), (source,), diagnostics=diagnostics)
    assert not output.sections[0].sentences
    assert diagnostics[0]["사유코드"] == "korean_body_unverified"

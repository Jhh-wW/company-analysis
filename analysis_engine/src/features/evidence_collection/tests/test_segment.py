"""전문(全文) 분할 — 목차·상투 문구 제외, 고정 표제 밖 문단 포착 시험."""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection.models import CollectedDocument, DocumentTextRange
from features.evidence_collection.segment import (
    segment_document,
    segment_document_with_status,
    segment_sections,
    segment_short_observation_candidates,
    segment_short_observation_candidates_with_status,
    usable_ranges_from_candidates,
)
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    LISTED_BUSINESS_REPORT_TEXT,
)


def test_목차_구간의_본문은_후보에서_빠진다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)

    assert not any("회사의 개요, 사업의 내용, 재무에 관한 사항 순서" in c.text for c in candidates)


def test_반복되는_면책_문구는_상투_문구로_빠진다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)

    assert not any("외부감사인의 감사의견과 별도로 작성" in c.text for c in candidates)


def test_고정_표제_밖의_문단도_후보로_잡힌다() -> None:
    """옛 방식(종류당 첫 1,200자)은 앞부분만 봤다 — 뒤쪽 장도 잡히는지 확인."""
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    texts = [c.text for c in candidates]

    # V~VIII장(임원·계약·향후계획·시장현황)은 문서 뒤쪽에 있다 — 첫 1,200자
    # 방식이면 절대 잡히지 않는다.
    assert any("경영진 리더십 원칙" in t for t in texts)
    assert any("장기 공급계약" in t for t in texts)
    assert any("해외 생산라인 투자" in t for t in texts)
    assert any("동종업계는 소수 기업이 과점" in t for t in texts)


def test_문단_수는_9개_이상이다() -> None:
    """실측 커버리지 2.4%였던 옛 방식과 달리 문서 전체에서 여러 문단을 뽑는지."""
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    assert len(candidates) >= 9


def test_usable_ranges는_CollectedDocument에_그대로_들어간다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    ranges = usable_ranges_from_candidates(candidates)

    document = CollectedDocument(
        company_id="00126380",
        document_id="dart_business_report:20250315000001",
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250315000001",
        source_tier="TIER_1_OFFICIAL",
        source_kind="dart_business_report",
        publisher="금융감독원 전자공시시스템(DART)",
        title="사업보고서",
        published_on="2025-03-15",
        collected_at="2026-08-31T00:00:00+09:00",
        content_sha256="a" * 64,
        identity_binding="corp_code=00126380;rcept_no=20250315000001",
        usable_ranges=ranges,
        collector_version="evidence_collection/1.0",
        parser_version="evidence_collection_segment/1.0",
        requirement="REQUIRED",
    )
    assert len(document.usable_ranges) == len(candidates)


def test_빈_문서는_후보가_없다() -> None:
    assert segment_document("") == []
    assert segment_document("   \n\n  ") == []


def test_짧은_잔여물은_MIN_FRAGMENT_CHARS_미만이면_버린다() -> None:
    text = "I. 회사의 개요\n짧음\n"
    candidates = segment_document(text)
    assert candidates == []


def test_P2_공백_섞인_목_차_마커도_제외된다() -> None:
    text = "1. 목 차\n이 사업보고서는 회사의 개요, 사업의 내용 순서로 구성되어 있다.\n\nI. 회사의 개요\n당사는 전자부품을 제조하는 주식회사이며 법인이다.\n"
    candidates = segment_document(text)

    assert not any("회사의 개요, 사업의 내용 순서로 구성" in c.text for c in candidates)
    assert any("당사는 전자부품을 제조하는 주식회사이며 법인이다" in c.text for c in candidates)


def test_P2_점_leader와_쪽번호로_끝나는_목차_항목_줄은_표제로_오인되지_않는다() -> None:
    text = (
        "1. 목차\n"
        "I. 회사의 개요 ............ 3\n"
        "II. 사업의 내용 ............ 5\n"
        "\n"
        "I. 회사의 개요\n"
        "당사는 전자부품을 제조하는 주식회사이며 법인이다.\n"
        "\n"
        "II. 사업의 내용\n"
        "당사의 매출은 판매에서 발생한다.\n"
    )
    candidates = segment_document(text)

    # 목차 항목 줄 자체가 조각으로 새지 않는다(점 leader·쪽번호 포함 원문 그대로는 없어야 함).
    assert not any("............ 3" in c.text or "............ 5" in c.text for c in candidates)
    # 진짜 본문 표제 아래 문단은 그대로 잡힌다.
    assert any("당사는 전자부품을 제조하는 주식회사이며 법인이다" in c.text for c in candidates)
    assert any("당사의 매출은 판매에서 발생한다" in c.text for c in candidates)


def test_각_후보의_start_end는_실제_원문과_일치한다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    for candidate in candidates:
        assert LISTED_BUSINESS_REPORT_TEXT[candidate.start:candidate.end] == candidate.text
        assert DocumentTextRange(candidate.start, candidate.end).end > candidate.start


def test_짧은_경쟁문장은_writer와_분리된_bounded_관측차선에만_남는다() -> None:
    sentence = "가나다전자는 베타전자와 경쟁합니다."
    text = f"I. 사업의 내용\n\n{sentence}\n\n잡음\n"

    assert not any(sentence in item.text for item in segment_document(text))
    short = segment_short_observation_candidates(text)
    assert [item.text for item in short if sentence in item.text] == [sentence]
    assert text[short[0].start : short[0].end] == short[0].text


def test_짧은_줄이_많아도_관측_후보와_제목_객체수는_상한이_있다() -> None:
    noisy_paragraphs = "\n\n".join(
        f"잡음{index:05d}" for index in range(20_000)
    )
    candidates = segment_short_observation_candidates(noisy_paragraphs)

    assert len(candidates) <= c.MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT
    assert (
        sum(len(item.text.strip()) for item in candidates)
        <= c.MAX_SHORT_OBSERVATION_CHARS_PER_DOCUMENT
    )

    heading_flood = "\n".join(
        f"{index % 999 + 1}. x" for index in range(20_000)
    )
    assert len(segment_sections(heading_flood)) <= c.MAX_TEXT_SEGMENTS_PER_DOCUMENT


def test_주입된_짧은후보_filter는_앞쪽_잡음예산과_무관하게_전문을_찾는다() -> None:
    target = "가나다전자는 베타전자와 경쟁합니다."
    noise = "\n\n".join(f"상품{index:05d}" for index in range(500))
    text = f"{noise}\n\n{target}\n"

    result = segment_short_observation_candidates_with_status(
        text,
        candidate_filter=lambda candidate: "경쟁" in candidate,
    )

    assert [candidate.text for candidate in result.candidates] == [target]
    assert result.truncation_reason == ""
    assert text[result.candidates[0].start : result.candidates[0].end] == target


def test_주입된_짧은후보가_cap을_넘으면_전문완료로_위장하지_않는다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(c, "MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT", 2)
    text = "\n\n".join(
        (
            "가나다는 나다라와 경쟁합니다.",
            "마바사는 사아자와 경쟁합니다.",
            "차카타는 파하가와 경쟁합니다.",
        )
    )

    result = segment_short_observation_candidates_with_status(
        text,
        candidate_filter=lambda candidate: "경쟁" in candidate,
    )

    assert len(result.candidates) == 2
    assert result.truncation_reason == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED


def test_목차_leader는_짧은_관측_차선에도_들어가지_않는다() -> None:
    text = (
        "I. 회사의 개요 ............ 3\n"
        "II. 사업의 내용 ............ 5\n"
        "\n"
        "I. 사업의 내용\n\n"
        "가나다전자는 베타전자와 경쟁합니다.\n"
    )

    candidates = segment_short_observation_candidates(text)
    assert not any("............" in item.text for item in candidates)


def test_장문_문단폭탄은_후보수_상한과_잘림사유를_함께_남긴다(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT", 3)
    text = "\n\n".join(
        f"서로 다른 장문 문단 {index} " + "가" * 30 for index in range(10)
    )

    result = segment_document_with_status(text)

    assert len(result.candidates) == 3
    assert result.truncation_reason == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED


def test_장문_후보의_총문자_상한도_잘림사유를_남긴다(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT", 100)
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT", 70)
    text = "\n\n".join(("가" * 40, "나" * 40, "다" * 40))

    result = segment_document_with_status(text)

    assert len(result.candidates) == 1
    assert result.truncation_reason == c.REASON_DOCUMENT_FRAGMENT_CHARS_EXCEEDED


def test_서로다른_줄폭탄은_상투문구색인을_무한히_키우지_않는다(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_BOILERPLATE_DISTINCT_LINES_PER_DOCUMENT", 2)
    text = "\n".join(
        (
            "첫 번째 서로 다른 충분히 긴 문장입니다.",
            "두 번째 서로 다른 충분히 긴 문장입니다.",
            "세 번째 서로 다른 충분히 긴 문장입니다.",
        )
    )

    result = segment_document_with_status(text)

    assert result.truncation_reason == c.REASON_DOCUMENT_LINE_INDEX_EXCEEDED


def test_제목구간_상한도_OK가_아닌_잘림으로_관측한다(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_TEXT_SEGMENTS_PER_DOCUMENT", 2)
    text = "\n".join(
        (
            "I. 첫 장",
            "첫 장에 있는 충분히 긴 실제 본문 문장입니다.",
            "II. 둘째 장",
            "둘째 장에 있는 충분히 긴 실제 본문 문장입니다.",
            "III. 셋째 장",
            "셋째 장에 있는 충분히 긴 실제 본문 문장입니다.",
        )
    )

    result = segment_document_with_status(text)

    assert len(result.candidates) <= 2
    assert result.truncation_reason == c.REASON_DOCUMENT_SECTION_COUNT_EXCEEDED

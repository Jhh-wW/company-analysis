"""계약 Mapping 변환기(`to_evidence_mappings`) 회귀시험 — 왕복 보장이 핵심."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.features.homepage.wide_evidence_mapping import (
    canonical_text_of,
    range_offsets_of,
    to_evidence_mappings,
)
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import (
    WideCollectionAttempt,
    WideCollectionResult,
    WideDocumentIdentity,
)

_SHA = "a" * 64


def _document(usable_ranges: tuple[str, ...], **overrides: object) -> WideDocumentIdentity:
    content_sha256 = hashlib.sha256("\n".join(usable_ranges).encode("utf-8")).hexdigest()
    fields = dict(
        company_id="c1",
        document_id="d1",
        canonical_url="https://company.example/careers",
        source_kind="official_recruit_page",
        publisher="company.example",
        title="채용",
        published_on="",
        collected_at="2026-08-31T00:00:00+00:00",
        content_sha256=content_sha256,
        identity_binding="root",
        usable_ranges=usable_ranges,
        collector_version="v1",
        parser_version="v1",
        requirement="REQUIRED",
        source_tier="TIER_1_OFFICIAL",
    )
    fields.update(overrides)
    return WideDocumentIdentity(**fields)


def _attempt(**overrides: object) -> WideCollectionAttempt:
    fields = dict(
        company_id="c1",
        attempt_id="page-0001",
        source_kind="official_web_page",
        requirement="REQUIRED",
        state="OK",
        slot_ids=("culture:work_principle",),
        reason_code="page_ok",
        elapsed_ms=10,
        bytes_downloaded=100,
        documents_seen=1,
    )
    fields.update(overrides)
    return WideCollectionAttempt(**fields)


def _result(
    *,
    company_id: str = "c1",
    documents: tuple[WideDocumentIdentity, ...] = (),
    attempts: tuple[WideCollectionAttempt, ...] = (),
) -> WideCollectionResult:
    return WideCollectionResult(company_id=company_id, documents=documents, attempts=attempts)


def test_offset으로_슬라이스하면_원_구간_텍스트가_정확히_복원된다():
    ranges = ("첫 번째 구간입니다.", "두 번째는 조금 더 긴 구간의 본문입니다.", "셋째.")
    document = _document(ranges)

    canonical = canonical_text_of(document)
    offsets = range_offsets_of(document)

    assert len(offsets) == len(ranges)
    for offset, original in zip(offsets, ranges):
        assert canonical[offset["start"] : offset["end"]] == original


def test_canonical_text는_content_sha256과_일관된다():
    ranges = ("구간 하나.", "구간 둘.")
    document = _document(ranges)
    assert hashlib.sha256(canonical_text_of(document).encode("utf-8")).hexdigest() == (
        document.content_sha256
    )


def test_offset은_정렬되고_겹치지_않는다():
    ranges = ("a" * 10, "b" * 5, "c" * 20)
    document = _document(ranges)
    offsets = range_offsets_of(document)
    for previous, current in zip(offsets, offsets[1:]):
        assert previous["end"] < current["start"]  # "\n" 한 글자만큼 벌어져 있다
        assert previous["start"] < previous["end"]


def test_document_mapping은_계약_필드_이름을_그대로_쓴다():
    document = _document(("핵심가치에 따른 채용 관련 본문입니다.",))
    fragments = build_fragments(document, company_id="c1")
    assert fragments  # 이 시험이 exact_evidence_hashes를 검증하려면 fragment가 있어야 한다
    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    assert len(mapped["documents"]) == 1
    mapping = mapped["documents"][0]
    assert set(mapping.keys()) == {
        "company_id", "document_id", "canonical_url", "source_kind", "publisher",
        "title", "published_on", "collected_at", "content_sha256", "identity_binding",
        "usable_ranges", "collector_version", "parser_version", "requirement", "source_tier",
        "exact_evidence_hashes",
    }
    assert mapping["document_id"] == "d1"
    assert isinstance(mapping["usable_ranges"], list)
    assert mapping["usable_ranges"][0] == {
        "start": 0, "end": len("핵심가치에 따른 채용 관련 본문입니다.")
    }
    assert mapping["exact_evidence_hashes"] == sorted({f.text_sha256 for f in fragments})


def test_fragment_mapping은_평범한_dict_list다():
    document = _document(("핵심가치 채용 문구입니다.",))
    fragments = build_fragments(document, company_id="c1")
    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    assert len(mapped["fragments"]) == len(fragments)
    for mapping in mapped["fragments"]:
        assert isinstance(mapping, dict)
        assert isinstance(mapping["reason_codes"], list)
        assert isinstance(mapping["score_millis"], int)
        assert set(mapping.keys()) == {
            "company_id", "fragment_id", "document_id", "location", "text_sha256", "text",
            "section_id", "slot_id", "covered_slot_ids", "score_millis", "reason_codes",
        }


def test_attempt_mapping은_slot_ids를_list로_바꾼다():
    mapped = to_evidence_mappings(result=_result(attempts=(_attempt(),)), fragments=())
    mapping = mapped["attempts"][0]
    assert isinstance(mapping["slot_ids"], list)
    assert mapping["slot_ids"] == ["culture:work_principle"]
    assert mapping["company_id"] == "c1"


def test_출력값_안에_tuple가_없다():
    document = _document(("본문 구간 하나입니다.",))
    fragments = build_fragments(document, company_id="c1")
    mapped = to_evidence_mappings(
        result=_result(documents=(document,), attempts=(_attempt(),)), fragments=fragments
    )

    def _assert_no_tuple(value: object) -> None:
        assert not isinstance(value, tuple)
        if isinstance(value, dict):
            for item in value.values():
                _assert_no_tuple(item)
        elif isinstance(value, list):
            for item in value:
                _assert_no_tuple(item)

    _assert_no_tuple(mapped)


# ── (계약 generation=7): exact_evidence_hashes ──────────


def test_exact_evidence_hashes는_문서의_fragment_해시_전체를_정렬해_담는다():
    """왕복 시험 — 목록에 있는 해시는 정확히 이 문서의 fragment에서 나온 것들이다."""
    ranges = (
        "핵심가치 중심의 채용 문화입니다.",
        "합격자 인터뷰에서 개선 프로젝트를 완료한 사례를 소개합니다.",
    )
    document = _document(ranges)
    fragments = build_fragments(document, company_id="c1")
    assert len(fragments) == 2  # 구간마다 신호 키워드가 달라 서로 다른 슬롯 1개씩만 매긴다

    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    mapping = mapped["documents"][0]
    fragment_hashes = {fragment.text_sha256 for fragment in fragments}
    assert len(fragment_hashes) == 2  # 두 구간의 실제 텍스트가 달라 해시도 다르다
    assert mapping["exact_evidence_hashes"] == sorted(fragment_hashes)


def test_exact_evidence_hashes는_중복_해시를_하나로_합친다():
    """한 원문이 두 슬롯을 채워도 fragment와 해시는 각각 한 번만 나간다."""
    document = _document(
        ("핵심가치를 적용해 고객 불편을 개선한 프로젝트 사례를 완료했습니다.",)
    )
    fragments = build_fragments(document, company_id="c1")
    assert len(fragments) == 1
    assert set(fragments[0].covered_slot_ids) == {
        "culture:work_principle", "culture:verified_case"
    }
    assert len({fragment.text_sha256 for fragment in fragments}) == 1

    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    mapping = mapped["documents"][0]
    assert mapping["exact_evidence_hashes"] == [fragments[0].text_sha256]


def test_fragment가_없는_문서는_documents에서_빠진다():
    """: 페이지 유형을 못 알아내 scored fragment가 0개인 문서는
    exact_evidence_hashes가 빈 값으로 나가는 대신(앱 계약이 거절) 애초에
    documents 출력에서 빠진다 — 조회 사실 자체는 attempt로 따로 남는다."""
    document = _document(
        ("아무 키워드도 없는 본문입니다.",),
        document_id="d-unmatched",
        canonical_url="https://company.example/xyz-unrelated",
    )
    fragments = build_fragments(document, company_id="c1")
    assert fragments == ()  # 알 수 없는 페이지 유형 — 조각을 만들지 않는다

    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    assert mapped["documents"] == []


def test_일부_문서만_fragment가_있으면_그_문서만_남는다():
    matched = _document(("핵심가치 채용 관련 본문입니다.",), document_id="d-matched")
    unmatched = _document(
        ("전혀 관련 없는 본문입니다.",),
        document_id="d-unmatched",
        canonical_url="https://company.example/xyz-unrelated",
    )
    fragments = build_fragments(matched, company_id="c1") + build_fragments(unmatched, company_id="c1")

    mapped = to_evidence_mappings(result=_result(documents=(matched, unmatched)), fragments=fragments)

    document_ids = {mapping["document_id"] for mapping in mapped["documents"]}
    assert document_ids == {"d-matched"}


def test_exact_evidence_hashes_형식은_소문자_64자리_16진수():
    document = _document(("핵심가치 채용 관련 본문입니다.",))
    fragments = build_fragments(document, company_id="c1")
    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    for value in mapped["documents"][0]["exact_evidence_hashes"]:
        assert isinstance(value, str)
        assert len(value) == 64
        assert value == value.lower()
        int(value, 16)  # 16진수가 아니면 ValueError


# ── 계약 generation=8: fragments·attempts의 company_id ────────


def test_fragment_mapping의_company_id는_fragment_자신의_값을_그대로_담는다():
    document = _document(("핵심가치 채용 문구입니다.",))
    fragments = build_fragments(document, company_id="target-co")
    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    assert mapped["fragments"]
    assert all(mapping["company_id"] == "target-co" for mapping in mapped["fragments"])


def test_attempt_mapping의_company_id는_attempt_자신의_값을_그대로_담는다():
    attempt = _attempt(company_id="target-co")
    mapped = to_evidence_mappings(
        result=_result(company_id="target-co", attempts=(attempt,)), fragments=()
    )

    assert mapped["attempts"][0]["company_id"] == "target-co"


def test_변환_단계는_document의_company_id로_fragment_company_id를_채우지_않는다():
    """공격 시험 — document.company_id("c1")와 fragment.company_id("other-co")가
    서로 다를 때, to_evidence_mappings가 document 값으로 몰래 덮어쓰면 안 된다.
    변환 단계는 pass-through만 해야 소유권 검증이 의미를 가진다. fragments는
    WideCollectionResult에 속하지 않는(별도 인자) 값이라 result의 company_id
    일관성 검증과 무관하게 여전히 독립적으로 다를 수 있다."""
    document = _document(("핵심가치 채용 문구입니다.",), company_id="c1")
    fragments = build_fragments(document, company_id="other-co")

    mapped = to_evidence_mappings(result=_result(documents=(document,)), fragments=fragments)

    assert mapped["documents"][0]["company_id"] == "c1"
    assert all(mapping["company_id"] == "other-co" for mapping in mapped["fragments"])


def test_document와_attempt의_company_id_불일치는_결과_생성_시점에_이미_막힌다():
    """설계 변경 반영 — document.company_id와 attempt.company_id가 다르면
    이제 WideCollectionResult 생성 시점에 이미 ValueError로 막힌다
    (wide_types.py 참조, 계약 gen=8 마지막 고리). to_evidence_mappings는
    항상 이미 내부 일관성이 확인된 result만 받으므로, 변환 단계에서
    document·attempt를 서로 다시 대조하지 않는다."""
    document = _document(("핵심가치 채용 문구입니다.",), company_id="c1")
    attempt = _attempt(company_id="other-co")
    with pytest.raises(ValueError):
        WideCollectionResult(company_id="c1", documents=(document,), attempts=(attempt,))


# ── 최상위 company_id ───────────────────────────


def test_최상위_company_id는_result에서만_나온다():
    document = _document(("핵심가치 채용 문구입니다.",), company_id="target-co")
    fragments = build_fragments(document, company_id="target-co")
    mapped = to_evidence_mappings(
        result=_result(company_id="target-co", documents=(document,)), fragments=fragments
    )
    assert mapped["company_id"] == "target-co"


def test_최상위_company_id는_documents가_0건이어도_보존된다():
    """robots 전면 차단 등으로 문서가 하나도 없어도, 최상위 company_id는
    남아야 한다(수집 주체를 잃지 않는다)."""
    attempt = _attempt(company_id="target-co")
    mapped = to_evidence_mappings(
        result=_result(company_id="target-co", attempts=(attempt,)), fragments=()
    )
    assert mapped["company_id"] == "target-co"
    assert mapped["documents"] == []


def test_최상위_company_id는_attempts가_0건이어도_보존된다():
    document = _document(("핵심가치 채용 문구입니다.",), company_id="target-co")
    fragments = build_fragments(document, company_id="target-co")
    mapped = to_evidence_mappings(
        result=_result(company_id="target-co", documents=(document,)), fragments=fragments
    )
    assert mapped["company_id"] == "target-co"
    assert mapped["attempts"] == []


def test_최상위_company_id는_documents_attempts_모두_0건이어도_보존된다():
    mapped = to_evidence_mappings(result=_result(company_id="target-co"), fragments=())
    assert mapped["company_id"] == "target-co"
    assert mapped["documents"] == []
    assert mapped["attempts"] == []


def test_최상위_키는_넷뿐이다():
    mapped = to_evidence_mappings(result=_result(), fragments=())
    assert set(mapped.keys()) == {"company_id", "documents", "fragments", "attempts"}


def test_옛_시그니처로_부르면_TypeError():
    """documents·fragments·attempts 개별 인자로 부르던 옛 시그니처는 더 이상
    없다 — 기본값으로 몰래 호환시키지 않았으므로 TypeError가 나야 한다."""
    with pytest.raises(TypeError):
        to_evidence_mappings(documents=(), fragments=(), attempts=())  # type: ignore[call-arg]


def test_report_evidence는_import하지_않는다():
    """앱 공용 계약 스키마 직접 사용은 chapter_evidence만의 몫이다."""
    module_path = Path(__file__).resolve().parents[1] / "wide_evidence_mapping.py"
    source = module_path.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import src.shared.report_evidence")
        assert not stripped.startswith("from src.shared.report_evidence")

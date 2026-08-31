"""계약 Mapping 변환기(`to_evidence_mappings`) 회귀시험 — 왕복 보장이 핵심."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.features.homepage.wide_evidence_mapping import (
    canonical_text_of,
    range_offsets_of,
    to_evidence_mappings,
)
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import (
    WideCollectionAttempt,
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
    document = _document(("채용 관련 본문입니다.",))  # canonical_url이 /careers라 슬롯이 잡힌다
    fragments = build_fragments(document, company_id="c1")
    assert fragments  # 이 시험이 exact_evidence_hashes를 검증하려면 fragment가 있어야 한다
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    assert len(result["documents"]) == 1
    mapping = result["documents"][0]
    assert set(mapping.keys()) == {
        "company_id", "document_id", "canonical_url", "source_kind", "publisher",
        "title", "published_on", "collected_at", "content_sha256", "identity_binding",
        "usable_ranges", "collector_version", "parser_version", "requirement", "source_tier",
        "exact_evidence_hashes",
    }
    assert mapping["document_id"] == "d1"
    assert isinstance(mapping["usable_ranges"], list)
    assert mapping["usable_ranges"][0] == {"start": 0, "end": len("채용 관련 본문입니다.")}
    assert mapping["exact_evidence_hashes"] == sorted({f.text_sha256 for f in fragments})


def test_fragment_mapping은_평범한_dict_list다():
    document = _document(("채용 문구입니다.",))
    fragments = build_fragments(document, company_id="c1")
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    assert len(result["fragments"]) == len(fragments)
    for mapping in result["fragments"]:
        assert isinstance(mapping, dict)
        assert isinstance(mapping["reason_codes"], list)
        assert isinstance(mapping["score_millis"], int)
        assert set(mapping.keys()) == {
            "company_id", "fragment_id", "document_id", "location", "text_sha256", "text",
            "section_id", "slot_id", "score_millis", "reason_codes",
        }


def test_attempt_mapping은_slot_ids를_list로_바꾼다():
    result = to_evidence_mappings(documents=(), fragments=(), attempts=(_attempt(),))
    mapping = result["attempts"][0]
    assert isinstance(mapping["slot_ids"], list)
    assert mapping["slot_ids"] == ["culture:work_principle"]
    assert mapping["company_id"] == "c1"


def test_출력값_안에_tuple가_없다():
    document = _document(("본문 구간 하나입니다.",))
    fragments = build_fragments(document, company_id="c1")
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=(_attempt(),))

    def _assert_no_tuple(value: object) -> None:
        assert not isinstance(value, tuple)
        if isinstance(value, dict):
            for item in value.values():
                _assert_no_tuple(item)
        elif isinstance(value, list):
            for item in value:
                _assert_no_tuple(item)

    _assert_no_tuple(result)


# ── P0-3(계약 generation=7): exact_evidence_hashes ──────────


def test_exact_evidence_hashes는_문서의_fragment_해시_전체를_정렬해_담는다():
    """왕복 시험 — 목록에 있는 해시는 정확히 이 문서의 fragment에서 나온 것들이다."""
    ranges = ("핵심가치 중심의 채용 문화입니다.", "합격자 인터뷰 사례를 소개합니다.")
    document = _document(ranges)
    fragments = build_fragments(document, company_id="c1")
    assert len(fragments) == 2  # 구간마다 신호 키워드가 달라 서로 다른 슬롯 1개씩만 매긴다

    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    mapping = result["documents"][0]
    fragment_hashes = {fragment.text_sha256 for fragment in fragments}
    assert len(fragment_hashes) == 2  # 두 구간의 실제 텍스트가 달라 해시도 다르다
    assert mapping["exact_evidence_hashes"] == sorted(fragment_hashes)


def test_exact_evidence_hashes는_중복_해시를_하나로_합친다():
    """본문에 슬롯별 신호 키워드가 없으면 build_fragments가 같은 텍스트를 슬롯
    개수만큼 복제한다(culture:work_principle·culture:verified_case 둘 다) —
    내용이 같으니 exact_evidence_hashes는 1개로 합쳐져야 한다."""
    document = _document(("평범한 채용 안내 문단입니다.",))
    fragments = build_fragments(document, company_id="c1")
    assert len(fragments) == 2  # 같은 텍스트, slot_id만 다른 fragment 2개
    assert len({fragment.text_sha256 for fragment in fragments}) == 1

    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    mapping = result["documents"][0]
    assert mapping["exact_evidence_hashes"] == [fragments[0].text_sha256]


def test_fragment가_없는_문서는_documents에서_빠진다():
    """P0-3: 페이지 유형을 못 알아내 scored fragment가 0개인 문서는
    exact_evidence_hashes가 빈 값으로 나가는 대신(앱 계약이 거절) 애초에
    documents 출력에서 빠진다 — 조회 사실 자체는 attempt로 따로 남는다."""
    document = _document(
        ("아무 키워드도 없는 본문입니다.",),
        document_id="d-unmatched",
        canonical_url="https://company.example/xyz-unrelated",
    )
    fragments = build_fragments(document, company_id="c1")
    assert fragments == ()  # 알 수 없는 페이지 유형 — 조각을 만들지 않는다

    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    assert result["documents"] == []


def test_일부_문서만_fragment가_있으면_그_문서만_남는다():
    matched = _document(("채용 관련 본문입니다.",), document_id="d-matched")
    unmatched = _document(
        ("전혀 관련 없는 본문입니다.",),
        document_id="d-unmatched",
        canonical_url="https://company.example/xyz-unrelated",
    )
    fragments = build_fragments(matched, company_id="c1") + build_fragments(unmatched, company_id="c1")

    result = to_evidence_mappings(documents=(matched, unmatched), fragments=fragments, attempts=())

    document_ids = {mapping["document_id"] for mapping in result["documents"]}
    assert document_ids == {"d-matched"}


def test_exact_evidence_hashes_형식은_소문자_64자리_16진수():
    document = _document(("채용 관련 본문입니다.",))
    fragments = build_fragments(document, company_id="c1")
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    for value in result["documents"][0]["exact_evidence_hashes"]:
        assert isinstance(value, str)
        assert len(value) == 64
        assert value == value.lower()
        int(value, 16)  # 16진수가 아니면 ValueError


# ── 계약 generation=8: fragments·attempts의 company_id ────────


def test_fragment_mapping의_company_id는_fragment_자신의_값을_그대로_담는다():
    document = _document(("채용 문구입니다.",))
    fragments = build_fragments(document, company_id="target-co")
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    assert result["fragments"]
    assert all(mapping["company_id"] == "target-co" for mapping in result["fragments"])


def test_attempt_mapping의_company_id는_attempt_자신의_값을_그대로_담는다():
    attempt = _attempt(company_id="target-co")
    result = to_evidence_mappings(documents=(), fragments=(), attempts=(attempt,))

    assert result["attempts"][0]["company_id"] == "target-co"


def test_변환_단계는_document의_company_id로_fragment_company_id를_채우지_않는다():
    """공격 시험 — document.company_id("c1")와 fragment.company_id("other-co")가
    서로 다를 때, to_evidence_mappings가 document 값으로 몰래 덮어쓰면 안 된다.
    변환 단계는 pass-through만 해야 소유권 검증이 의미를 가진다."""
    document = _document(("채용 문구입니다.",), company_id="c1")
    fragments = build_fragments(document, company_id="other-co")

    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    assert result["documents"][0]["company_id"] == "c1"
    assert all(mapping["company_id"] == "other-co" for mapping in result["fragments"])


def test_변환_단계는_document의_company_id로_attempt_company_id를_채우지_않는다():
    document = _document(("채용 문구입니다.",), company_id="c1")
    fragments = build_fragments(document, company_id="c1")
    attempt = _attempt(company_id="other-co")

    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=(attempt,))

    assert result["documents"][0]["company_id"] == "c1"
    assert result["attempts"][0]["company_id"] == "other-co"


def test_report_evidence는_import하지_않는다():
    """실행계획 §4-2 — 앱 공용 계약 스키마 직접 사용은 chapter_evidence만의 몫이다."""
    module_path = Path(__file__).resolve().parents[1] / "wide_evidence_mapping.py"
    source = module_path.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import src.shared.report_evidence")
        assert not stripped.startswith("from src.shared.report_evidence")

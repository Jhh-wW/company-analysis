"""usable_ranges → 슬롯 태그 조각(fragment) 변환 회귀시험."""

from __future__ import annotations

from src.features.homepage.constants import WIDE_REQUIRED_SLOT_IDS
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import WideDocumentIdentity

_SHA = "a" * 64
_FORBIDDEN_SLOT_PREFIXES = ("comparison_", "limitation", "historical_performance")


def _document(canonical_url: str, usable_ranges: tuple[str, ...]) -> WideDocumentIdentity:
    return WideDocumentIdentity(
        company_id="c1",
        document_id="d1",
        canonical_url=canonical_url,
        source_kind="official_web_page",
        publisher="company.example",
        title="제목",
        published_on="",
        collected_at="2026-08-31T00:00:00+00:00",
        content_sha256=_SHA,
        identity_binding="root",
        usable_ranges=usable_ranges,
        collector_version="v1",
        parser_version="v1",
        requirement="REQUIRED",
        source_tier="TIER_1_OFFICIAL",
    )


def test_채용_페이지는_culture_슬롯만_받는다():
    document = _document(
        "https://company.example/careers", ("우리 팀에 합류하세요.",)
    )
    fragments = build_fragments(document)
    assert fragments
    assert {f.slot_id for f in fragments} == {"culture:work_principle", "culture:verified_case"}


def test_회사소개_페이지는_identity와_competitive_position_슬롯을_받는다():
    document = _document("https://company.example/about", ("회사 소개 문구입니다.",))
    fragments = build_fragments(document)
    slot_ids = {f.slot_id for f in fragments}
    assert slot_ids == {
        "identity:corporate_identity",
        "identity:business_definition",
        "competitive_position:self_context",
    }


def test_페이지_유형을_모르면_조각을_만들지_않는다():
    document = _document("https://company.example/random-page", ("아무 내용.",))
    assert build_fragments(document) == ()


def test_본문_키워드가_있으면_그_슬롯만_매긴다():
    # about 페이지 후보는 identity 2개 + competitive_position 1개인데,
    # "강점"이라는 몸통 신호는 competitive_position:self_context에만 있다.
    document = _document(
        "https://company.example/about",
        ("우리 회사의 강점은 빠른 기술력입니다.",),
    )
    fragments = build_fragments(document)
    assert {f.slot_id for f in fragments} == {"competitive_position:self_context"}
    assert fragments[0].score_millis == 700
    assert "body_keyword_match" in fragments[0].reason_codes


def test_본문_키워드가_없으면_후보_전체로_매긴다():
    document = _document(
        "https://company.example/about",
        ("특별한 신호 키워드 없이 그냥 소개하는 문장입니다.",),
    )
    fragments = build_fragments(document)
    assert {f.slot_id for f in fragments} == {
        "identity:corporate_identity",
        "identity:business_definition",
        "competitive_position:self_context",
    }
    assert all(f.score_millis == 400 for f in fragments)
    assert all(f.reason_codes == ("page_type_signal",) for f in fragments)


def test_location은_문서_canonical_url과_구간_index를_가리킨다():
    document = _document(
        "https://company.example/careers",
        ("첫 번째 구간입니다.", "두 번째 구간입니다."),
    )
    fragments = build_fragments(document)
    locations = {f.location for f in fragments}
    assert "https://company.example/careers#0" in locations
    assert "https://company.example/careers#1" in locations


def test_fragment_text는_원본_usable_range와_같다():
    document = _document("https://company.example/careers", ("정확히 이 텍스트입니다.",))
    fragments = build_fragments(document)
    assert all(f.text == "정확히 이 텍스트입니다." for f in fragments)


def test_comparison_limitation_historical_performance_슬롯은_0건이다():
    """여러 페이지 유형을 섞어도 금지 슬롯은 절대 나오지 않는다(허용 어휘에 없음)."""
    documents = [
        _document("https://company.example/careers", ("채용 문구입니다.",)),
        _document("https://company.example/about", ("회사 소개입니다.",)),
        _document("https://company.example/products", ("제품 소개입니다.",)),
        _document("https://company.example/news", ("뉴스룸 소식입니다.",)),
        _document("https://company.example/partners", ("파트너사 소개입니다.",)),
    ]
    all_fragments = [frag for doc in documents for frag in build_fragments(doc)]
    assert all_fragments  # 실제로 조각이 만들어졌는지 확인(공허한 통과 방지)
    for fragment in all_fragments:
        assert fragment.slot_id in WIDE_REQUIRED_SLOT_IDS
        assert not any(
            fragment.slot_id.startswith(prefix) for prefix in _FORBIDDEN_SLOT_PREFIXES
        )


def test_fragment_id는_결정론적이다():
    document = _document("https://company.example/careers", ("같은 입력입니다.",))
    first = build_fragments(document)
    second = build_fragments(document)
    assert [f.fragment_id for f in first] == [f.fragment_id for f in second]

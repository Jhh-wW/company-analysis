"""usable_ranges → 슬롯 태그 조각(fragment) 변환 회귀시험."""

from __future__ import annotations

import pytest

from src.features.homepage.constants import WIDE_REQUIRED_SLOT_IDS
from src.features.homepage.wide_domain import classify_official_page_url
from src.features.homepage.wide_fragments import build_fragments, build_fragments_for_collection
from src.features.homepage.wide_types import WideCollectionResult, WideDocumentIdentity

_SHA = "a" * 64
_FORBIDDEN_SLOT_PREFIXES = ("comparison_", "limitation", "historical_performance")
_COMPANY_ID = "c1"


def _document(
    canonical_url: str, usable_ranges: tuple[str, ...], **overrides: object
) -> WideDocumentIdentity:
    fields = dict(
        company_id="c1",
        document_id="d1",
        canonical_url=canonical_url,
        source_kind=classify_official_page_url(canonical_url).source_kind,
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
    fields.update(overrides)
    return WideDocumentIdentity(**fields)


def test_채용_페이지는_culture_슬롯만_받는다():
    document = _document(
        "https://company.example/careers",
        ("핵심가치를 적용해 고객 불편을 개선한 프로젝트 사례를 완료했습니다.",),
    )
    fragments = build_fragments(document, company_id=_COMPANY_ID)
    assert fragments
    assert {
        slot_id for fragment in fragments for slot_id in fragment.covered_slot_ids
    } == {"culture:work_principle", "culture:verified_case"}
    assert len(fragments) == 1  # 같은 원문 범위는 슬롯 수와 무관하게 한 번만 싣는다.


def test_회사소개_페이지는_identity와_competitive_position_슬롯을_받는다():
    document = _document(
        "https://company.example/about",
        ("2010년 설립해 주요 사업을 운영하며 차별화된 경쟁력을 갖췄습니다.",),
    )
    fragments = build_fragments(document, company_id=_COMPANY_ID)
    slot_ids = {
        slot_id for fragment in fragments for slot_id in fragment.covered_slot_ids
    }
    assert slot_ids == {
        "identity:corporate_identity",
        "identity:business_definition",
        "competitive_position:self_context",
    }


def test_페이지_유형을_모르면_조각을_만들지_않는다():
    document = _document("https://company.example/random-page", ("아무 내용.",))
    assert build_fragments(document, company_id=_COMPANY_ID) == ()


def test_단일_루트_홈페이지는_URL이_아니라_본문_신호로_분류한다():
    document = _document(
        "https://company.example/",
        (
            "2010년에 설립한 법인이며 주요 사업은 식품 제조 및 판매입니다. "
            "주요 고객사에 서비스를 제공하고 구독료로 수익을 얻습니다.",
        ),
    )

    fragments = build_fragments(document, company_id=_COMPANY_ID)
    covered = {
        slot_id for fragment in fragments for slot_id in fragment.covered_slot_ids
    }

    assert "identity:corporate_identity" in covered
    assert "identity:business_definition" in covered
    assert "business_model:customer_type" in covered
    assert "business_model:revenue_model" in covered


def test_감사보고서형_작은회사의_숫자표는_사업모델을_지어내지_않는다():
    document = _document(
        "https://company.example/",
        (
            "감사보고서 재무제표의 매출액은 2024년 10억원이며 자산과 부채는 "
            "아래 부속명세서와 같습니다.",
        ),
    )

    assert build_fragments(document, company_id=_COMPANY_ID) == ()


@pytest.mark.parametrize(
    "text",
    (
        "고객의 문제를 해결하는 맞춤형 솔루션을 제공합니다.",
        "리스크 관리 솔루션을 제공합니다.",
        "고객 경험을 개선한 프로젝트 사례입니다.",
    ),
)
def test_고객용_광고문구는_회사의_당면과제나_대응이_아니다(text):
    document = _document("https://company.example/", (text,))

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert "current_challenges:issue" not in covered
    assert "current_challenges:response" not in covered


def test_구체적_부정영향은_issue만_채운다():
    document = _document(
        "https://company.example/",
        ("원재료 가격 상승으로 제조원가 부담이 커졌습니다.",),
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert "current_challenges:issue" in covered
    assert "current_challenges:response" not in covered


def test_회사행동만_있고_문제와_연결되지_않으면_response를_채우지_않는다():
    document = _document(
        "https://company.example/", ("공급처를 다변화했습니다.",)
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert "current_challenges:response" not in covered


def test_한_문단에서_문제_연결어_회사행동이_함께_있으면_issue와_response를_채운다():
    document = _document(
        "https://company.example/",
        (
            "원재료 가격 상승으로 부담이 커졌고, 이에 대응해 공급처를 "
            "다변화했습니다.",
        ),
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert {"current_challenges:issue", "current_challenges:response"} <= covered


def test_바로_앞_문단의_문제와_명시적으로_연결된_회사행동은_response를_채운다():
    document = _document(
        "https://company.example/",
        (
            "원재료 가격 상승으로 제조원가 부담이 커졌습니다.",
            "이에 대응해 공급처를 다변화했습니다.",
        ),
    )

    fragments = build_fragments(document, company_id=_COMPANY_ID)
    covered = {
        slot_id for fragment in fragments for slot_id in fragment.covered_slot_ids
    }

    assert {"current_challenges:issue", "current_challenges:response"} <= covered
    response = next(
        fragment
        for fragment in fragments
        if "current_challenges:response" in fragment.covered_slot_ids
    )
    assert response.location.endswith("#1")


@pytest.mark.parametrize(
    "text",
    (
        "고객사의 매출 하락이 이어졌습니다. 분석 플랫폼을 자동화했습니다.",
        "원재료 가격이 상승했습니다. 한편 사옥 보안을 강화했습니다.",
        "협력사 공급에 차질이 생겼습니다. 협력사가 설비에 투자했습니다.",
        "금리 하락으로 금융비용 부담이 감소했습니다. 회사가 설비에 투자했습니다.",
    ),
)
def test_같은_범위의_무관한_부정어와_행동어는_당면과제_관계를_만들지_않는다(text):
    document = _document("https://company.example/", (text,))

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert "current_challenges:response" not in covered


def test_같은_범위에서도_회사_부정영향과_인과_대응행동이_있으면_둘_다_채운다():
    document = _document(
        "https://company.example/",
        (
            "원재료 가격이 급등해 원가율이 높아졌습니다. "
            "이에 대응해 신규 공급업체를 발굴했습니다.",
        ),
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert {"current_challenges:issue", "current_challenges:response"} <= covered


def test_연결어가_있어도_행동_주체가_협력사면_response를_채우지_않는다():
    document = _document(
        "https://company.example/",
        (
            "원재료 가격 상승으로 제조원가 부담이 커졌습니다. "
            "이에 대응해 협력사가 설비에 투자했습니다.",
        ),
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }

    assert "current_challenges:issue" in covered
    assert "current_challenges:response" not in covered


def test_검증사례는_사례_표지만으로_채우지_않고_행동이나_결과도_요구한다():
    title_only = _document(
        "https://company.example/careers", ("구성원 인터뷰 사례와 스토리",)
    )
    concrete = _document(
        "https://company.example/careers",
        ("구성원 인터뷰에서 개선 프로젝트를 적용해 목표를 달성한 사례",),
    )

    assert build_fragments(title_only, company_id=_COMPANY_ID) == ()
    assert {
        slot_id
        for fragment in build_fragments(concrete, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    } == {"culture:verified_case"}


def test_본문_키워드가_있으면_그_슬롯만_매긴다():
    # about 페이지 후보는 identity 2개 + competitive_position 1개인데,
    # "강점"이라는 몸통 신호는 competitive_position:self_context에만 있다.
    document = _document(
        "https://company.example/about",
        ("우리 회사의 강점은 빠른 기술력입니다.",),
    )
    fragments = build_fragments(document, company_id=_COMPANY_ID)
    assert {f.slot_id for f in fragments} == {"competitive_position:self_context"}
    assert fragments[0].score_millis == 700
    assert "body_keyword_match" in fragments[0].reason_codes


def test_본문_키워드가_없으면_URL_후보만으로_슬롯을_만들지_않는다():
    document = _document(
        "https://company.example/about",
        ("특별한 신호 키워드 없이 그냥 소개하는 문장입니다.",),
    )
    assert build_fragments(document, company_id=_COMPANY_ID) == ()


def test_location은_문서_canonical_url과_구간_index를_가리킨다():
    document = _document(
        "https://company.example/careers",
        ("첫 번째 핵심가치 원칙입니다.", "두 번째 핵심가치 원칙입니다."),
    )
    fragments = build_fragments(document, company_id=_COMPANY_ID)
    locations = {f.location for f in fragments}
    assert "https://company.example/careers#0" in locations
    assert "https://company.example/careers#1" in locations


def test_fragment_text는_원본_usable_range와_같다():
    document = _document("https://company.example/careers", ("핵심가치는 정확히 이 텍스트입니다.",))
    fragments = build_fragments(document, company_id=_COMPANY_ID)
    assert all(f.text == "핵심가치는 정확히 이 텍스트입니다." for f in fragments)


def test_comparison_limitation_historical_performance_슬롯은_0건이다():
    """여러 페이지 유형을 섞어도 금지 슬롯은 절대 나오지 않는다(허용 어휘에 없음)."""
    documents = [
        _document("https://company.example/careers", ("핵심가치와 일하는 방식입니다.",)),
        _document("https://company.example/about", ("2010년에 설립한 법인입니다.",)),
        _document("https://company.example/products", ("주력 제품은 알파 솔루션입니다.",)),
        _document("https://company.example/news", ("신제품 출시를 완료한 성과입니다.",)),
        _document("https://company.example/partners", ("협력사 공급망에서 직접 운영합니다.",)),
    ]
    all_fragments = [frag for doc in documents for frag in build_fragments(doc, company_id=_COMPANY_ID)]
    assert all_fragments  # 실제로 조각이 만들어졌는지 확인(공허한 통과 방지)
    for fragment in all_fragments:
        assert fragment.slot_id in WIDE_REQUIRED_SLOT_IDS
        assert not any(
            fragment.slot_id.startswith(prefix) for prefix in _FORBIDDEN_SLOT_PREFIXES
        )


def test_fragment_id는_결정론적이다():
    document = _document("https://company.example/careers", ("핵심가치라는 같은 입력입니다.",))
    first = build_fragments(document, company_id=_COMPANY_ID)
    second = build_fragments(document, company_id=_COMPANY_ID)
    assert [f.fragment_id for f in first] == [f.fragment_id for f in second]


# ── 계약 generation=8: company_id ────────────────────────


def test_fragment는_인자로_받은_company_id를_그대로_싣는다():
    document = _document("https://company.example/careers", ("핵심가치 채용 문구입니다.",))
    fragments = build_fragments(document, company_id="target-co")
    assert fragments
    assert all(f.company_id == "target-co" for f in fragments)


def test_company_id는_document의_값으로_자동_대체되지_않는다():
    """공격 시험 — document.company_id("c1")와 다른 값을 넘기면, build_fragments가
    조용히 document 값으로 바꿔치기하지 않고 «넘긴 값 그대로»를 실어야 한다.
    이게 성립하지 않으면 다른 회사 조회 결과가 섞여도 겉으로는 안 들킨다."""
    document = _document("https://company.example/careers", ("핵심가치 채용 문구입니다.",))
    assert document.company_id == "c1"

    fragments = build_fragments(document, company_id="other-co")

    assert fragments
    assert all(f.company_id == "other-co" for f in fragments)
    assert not any(f.company_id == document.company_id for f in fragments)


def test_company_id를_생략하면_TypeError():
    """필수 키워드 인자다 — 빠뜨리면 즉시 TypeError로 걸린다(호출자가
    깜빡해도 빈 값이 조용히 흘러가지 않는다)."""
    document = _document("https://company.example/careers", ("핵심가치 채용 문구입니다.",))
    with pytest.raises(TypeError):
        build_fragments(document)  # type: ignore[call-arg]


# ── build_fragments_for_collection: 수집 결과 전용 편의 함수 ──────


def test_수집결과의_모든_문서에서_조각을_만든다():
    """권장 결합 경로 — 문서마다 build_fragments를 손으로 부르지 않아도
    수집 결과 하나로 모든 문서의 fragment를 한 번에 얻는다."""
    careers = _document(
        "https://company.example/careers", ("핵심가치 채용 문구입니다.",), document_id="d-careers"
    )
    about = _document(
        "https://company.example/about", ("2010년에 설립한 법인입니다.",), document_id="d-about"
    )
    result = WideCollectionResult(company_id="c1", documents=(careers, about), attempts=())

    fragments = build_fragments_for_collection(result)

    assert fragments
    assert {f.document_id for f in fragments} == {"d-careers", "d-about"}


def test_수집결과_company_id를_모든_fragment에_싣는다():
    """공격 시험 — document.company_id를 역산하는 게 아니라 result.company_id
    (호출 인자로 받은 정본)를 싣는지 확인한다. 문서도 같은 값을 가져야
    WideCollectionResult 생성이 통과하므로 값 자체는 같지만, 출처가
    documents가 아니라 result.company_id임은 아래 «결과 자신에서 역산하지
    않는다» 계열 시험(wide_types.py)에서 결과 생성 시점 검증으로 이미
    증명된다 — 여기서는 그 정본이 fragment까지 그대로 흐르는지 확인한다."""
    document = _document(
        "https://company.example/careers", ("핵심가치 채용 문구입니다.",), company_id="target-co"
    )
    result = WideCollectionResult(company_id="target-co", documents=(document,), attempts=())

    fragments = build_fragments_for_collection(result)

    assert fragments
    assert all(f.company_id == "target-co" for f in fragments)


def test_문서가_0건이면_빈_튜플():
    """robots 차단 등으로 문서가 하나도 안 만들어진 수집 결과도 예외 없이
    빈 튜플을 돌려줘야 한다. company_id는 documents가 아니라 결과 자신이
    정본으로 들고 있으므로, 문서가 0건이어도 정본을 잃지 않는다(그래서
    company_id 없이도 문제없이 빈 튜플을 낸다)."""
    result = WideCollectionResult(company_id="c1", documents=(), attempts=())
    assert build_fragments_for_collection(result) == ()


def test_문서_company_id가_결과와_다르면_결과_생성_시점에_ValueError():
    """공격 시험 — 한 번의 수집 실행은 회사 하나만 대상으로 해야 정상이다.
    내부 불일치(document.company_id가 result.company_id와 다름)는
    ``build_fragments_for_collection`` 호출 이전, ``WideCollectionResult``
    «생성 시점»에 이미 막힌다(정본이 result 자신이라 documents끼리
    서로 일치하는지가 아니라 정본과 일치하는지를 본다 — 그래서 문서가
    1개뿐이라 «서로 일치»해도 정본과 다르면 걸린다)."""
    document = _document(
        "https://company.example/careers", ("핵심가치 채용 문구입니다.",), company_id="other-co"
    )
    with pytest.raises(ValueError):
        WideCollectionResult(company_id="target-co", documents=(document,), attempts=())


def test_수집결과_경로와_저수준_경로는_같은_fragment를_만든다():
    """왕복 — build_fragments_for_collection이 문서마다 build_fragments를
    같은 company_id로 부르는 것과 동일한 결과를 내는지 확인한다."""
    document = _document("https://company.example/careers", ("핵심가치 채용 문구입니다.",))
    result = WideCollectionResult(company_id="c1", documents=(document,), attempts=())

    via_collection = build_fragments_for_collection(result)
    via_low_level = build_fragments(document, company_id=document.company_id)

    assert via_collection == via_low_level

from pathlib import Path

import pytest

from src.features.product_names.constants import MAX_NAME_CANDIDATES
from src.features.product_names.logic import (
    collect_name_candidates,
    parse_major_contracts,
    parse_named_service_table,
    parse_product_service_table,
    parse_subsidiary_table,
)
from src.features.product_names.models import NameCandidate
from src.shared.report_generation.models import exact_text_sha256


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _by_name(candidates):
    return {candidate.name: candidate for candidate in candidates}


def test_카카오_금액없는_제품열과_부문을_읽는다() -> None:
    candidates = collect_name_candidates(
        _fixture("kakao_product_services.txt"), source_kind="사업보고서"
    )
    by_name = _by_name(candidates)

    assert {
        "카카오톡",
        "선물하기",
        "다음(Daum)",
        "픽코마",
        "카카오페이지",
        "카카오웹툰",
    } <= {name for name, item in by_name.items() if item.subject_kind == "product"}
    assert {"플랫폼 부문", "콘텐츠 부문"} <= {
        name for name, item in by_name.items() if item.subject_kind == "segment"
    }
    assert all(candidate.source_kind == "사업보고서" for candidate in candidates)


def test_우리은행_상품명과_주요내용을_함께_읽는다() -> None:
    candidates = parse_named_service_table(_fixture("woori_named_services.txt"))
    by_name = _by_name(candidates)

    assert by_name["우리 SUPER주거래 통장"].subject_kind == "product"
    assert "입출금 통장" in by_name["우리 SUPER주거래 통장"].description
    assert by_name["WON 플러스 예금"].description.startswith("가입 기간")


def test_하이브_종속회사와_주요사업을_읽는다() -> None:
    candidates = parse_subsidiary_table(_fixture("hybe_subsidiaries.txt"))
    by_name = _by_name(candidates)

    assert by_name["㈜수퍼톤"].subject_kind == "subsidiary"
    assert by_name["㈜수퍼톤"].description == "소프트웨어 개발업 · AI 솔루션 개발"


def test_특수관계자_표에서는_종속기업_행만_읽는다() -> None:
    text = """특수관계자 현황
구분 | 특수관계자명 | 거래내용
종속기업 | ㈜우아한청년들 | 영업 거래
관계기업 | ㈜예시관계사 | 영업 거래
2. 담보
"""

    candidates = parse_subsidiary_table(text)

    assert tuple(candidate.name for candidate in candidates) == ("㈜우아한청년들",)


def test_인이지_중요계약과_기간_진행률을_읽는다() -> None:
    candidates = parse_major_contracts(_fixture("ineeji_contracts.txt"))
    by_name = _by_name(candidates)

    assert {"AI예측모델 구축", "K-스마트등대공장"} == set(by_name)
    assert by_name["AI예측모델 구축"].subject_kind == "contract"
    assert "계약기간: 2024-07-01~2025-06-30" in by_name[
        "AI예측모델 구축"
    ].description
    assert "진행률: 91%" in by_name["AI예측모델 구축"].description


def test_삼성전자_부문과_품목을_구분해_읽는다() -> None:
    candidates = parse_product_service_table(
        _fixture("samsung_product_services.txt")
    )
    by_name = _by_name(candidates)

    assert by_name["DX 부문"].subject_kind == "segment"
    assert by_name["DS 부문"].subject_kind == "segment"
    assert by_name["TV"].subject_kind == "product"
    assert by_name["모니터"].subject_kind == "product"
    assert by_name["DRAM"].subject_kind == "product"
    assert by_name["NAND Flash"].subject_kind == "product"


@pytest.mark.parametrize(
    "parser",
    (
        parse_product_service_table,
        parse_named_service_table,
        parse_subsidiary_table,
        parse_major_contracts,
    ),
)
def test_표가_없거나_입력이_비면_예외없이_빈_tuple이다(parser) -> None:
    assert parser("관련 표가 없는 일반 본문입니다.") == ()
    assert parser("") == ()
    assert parser(None) == ()


def test_같은_이름은_공백과_기호를_정규화해_먼저_나온_하나만_남긴다() -> None:
    text = """주요 제품 및 서비스
부문 | 제품명
플랫폼 | 이름 상품

주요 상품 및 서비스의 내용
상품명 | 주요 내용
이름-상품 | 뒤 규칙의 설명
"""

    candidates = collect_name_candidates(text, source_kind="분기보고서")

    assert sum(candidate.name in {"이름 상품", "이름-상품"} for candidate in candidates) == 1
    assert _by_name(candidates)["이름 상품"].location == "주요 제품 및 서비스"


def test_후보_상한을_넘기지_않는다() -> None:
    rows = "\n".join(f"부문{i} | 제품{i}" for i in range(MAX_NAME_CANDIDATES + 10))
    text = f"주요 제품 및 서비스\n부문 | 제품명\n{rows}\n2. 다음 절"

    candidates = collect_name_candidates(text, source_kind="사업보고서")

    assert len(candidates) == MAX_NAME_CANDIDATES


def test_모든_후보는_원문행과_공용_해시를_보존한다() -> None:
    candidates = collect_name_candidates(
        "\n".join(
            (
                "주요 제품 및 서비스",
                "부문 | 제품명",
                "  플랫폼 부문 | 다음(Daum)  ",
                "2. 다음 절",
            )
        ),
        source_kind="사업보고서",
    )

    assert candidates
    for candidate in candidates:
        assert candidate.name in candidate.excerpt
        assert candidate.excerpt.startswith("  ")
        assert candidate.excerpt_sha256 == exact_text_sha256(candidate.excerpt)


def test_열_수가_맞지_않는_행과_잡음_이름을_버린다() -> None:
    text = """주요 제품 및 서비스
부문 | 제품명 | 설명
플랫폼 | 정상 제품 | 설명
콘텐츠 | 열 부족
금융 | 합계 | 설명
제조 | 100백만원 | 설명
기타 | 미확인 | 설명
3. 다음 절
"""

    candidates = collect_name_candidates(text, source_kind="사업보고서")
    names = {candidate.name for candidate in candidates}

    assert "정상 제품" in names
    assert "열 부족" not in names
    assert "합계" not in names
    assert "100백만원" not in names
    assert "미확인" not in names


def test_subject_kind는_닫힌_목록이다() -> None:
    with pytest.raises(ValueError, match="허용하지 않는"):
        NameCandidate(
            name="제품",
            subject_kind="unknown",
            description="",
            source_kind="사업보고서",
            location="표",
            excerpt="제품",
            excerpt_sha256=exact_text_sha256("제품"),
        )

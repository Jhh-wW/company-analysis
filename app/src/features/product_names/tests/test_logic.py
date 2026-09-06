from pathlib import Path

import pytest

from src.features.product_names.constants import (
    MAX_NAME_CANDIDATES,
    SUBJECT_IP,
)
from src.features.product_names.logic import (
    collect_name_candidates,
    collect_name_candidates_from_tables,
    parse_artist_contract_tables,
    parse_ip_tables,
    parse_major_contract_tables,
    parse_major_contracts,
    parse_named_service_table,
    parse_named_service_tables,
    parse_product_service_table,
    parse_product_service_tables,
    parse_subsidiary_table,
    parse_subsidiary_tables,
)
from src.features.product_names.models import NameCandidate
from src.features.product_names.tables import parse_filing_tables, read_filing_tables
from src.shared.report_generation.models import exact_text_sha256


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fixture_tables(name: str):
    return read_filing_tables(FIXTURES / name)


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


# ─────────────────────────────────────────────────────────────────────
# 표 구조 입력 규칙 — 실제 공시는 «공백 접힌 평문»이 아니라 HTML 표다.
# ─────────────────────────────────────────────────────────────────────

HYBE_ARTIST_FIXTURE = "hybe_artist_contracts.xml"
KAKAO_TABLE_FIXTURE = "kakao_product_services.xml"
WOORI_TABLE_FIXTURE = "woori_named_services.xml"
# 브리프가 요구한 하이브 그룹 이름. 시험은 그룹명만 쓰고 멤버 이름은 쓰지 않는다.
HYBE_EXPECTED_GROUPS = frozenset(
    {
        "방탄소년단",
        "투모로우바이투게더",
        "세븐틴",
        "뉴진스",
        "르세라핌",
        "엔하이픈",
        "아일릿",
        "보이넥스트도어",
    }
)


def test_하이브_아티스트표에서_그룹만_대표IP로_읽는다() -> None:
    candidates = parse_artist_contract_tables(
        _fixture_tables(HYBE_ARTIST_FIXTURE)
    )
    names = {candidate.name for candidate in candidates}

    assert HYBE_EXPECTED_GROUPS <= names
    assert all(candidate.subject_kind == SUBJECT_IP for candidate in candidates)
    assert _by_name(candidates)["뉴진스"].description == "㈜어도어 소속"
    assert _by_name(candidates)["방탄소년단"].description == "㈜빅히트뮤직 소속"


def test_아티스트_칸의_사람_이름은_후보가_되지_않는다() -> None:
    """멤버 이름은 물론, 그룹 칸이 본명과 겹치는 솔로 표기도 빼야 한다."""

    tables = _fixture_tables(HYBE_ARTIST_FIXTURE)
    names = {candidate.name for candidate in parse_artist_contract_tables(tables)}
    members = {
        row[2]
        for row in tables[0].rows[1:]
        if len(row) > 2 and row[2].strip()
    }

    assert members  # 픽스처에 아티스트 칸이 실제로 있다
    assert not (names & members)


def test_대표IP는_ROWSPAN이_겹쳐도_그룹당_한_건이다() -> None:
    candidates = parse_artist_contract_tables(
        _fixture_tables(HYBE_ARTIST_FIXTURE)
    )
    names = [candidate.name for candidate in candidates]

    assert len(names) == len(set(names))


def test_브랜드_게임_파이프라인_머리행도_대표IP로_읽는다() -> None:
    candidates = parse_ip_tables(_fixture_tables(WOORI_TABLE_FIXTURE))
    by_name = _by_name(candidates)

    assert {"오딘: 발할라 라이징", "아키에이지 워"} <= set(by_name)
    assert by_name["아키에이지 워"].subject_kind == SUBJECT_IP
    assert by_name["아키에이지 워"].description == "모바일 MMORPG"


def test_개발단계_칸이_함께_있으면_제품명을_대표IP로_읽는다() -> None:
    markup = """
    <P>가. 연구개발 진행 현황</P>
    <TABLE>
      <TR><TH>제품명</TH><TH>개발단계</TH><TH>적응증</TH></TR>
      <TR><TD>PRF-001</TD><TD>품목제조허가</TD><TD>관절 건강 보호</TD></TR>
    </TABLE>
    """
    tables = parse_filing_tables(markup)

    assert [item.name for item in parse_ip_tables(tables)] == ["PRF-001"]
    # 같은 표를 제품 규칙이 다시 읽어 종류가 갈리면 안 된다.
    assert parse_product_service_tables(tables) == ()


def test_표에서_부문과_제품을_구분해_읽는다() -> None:
    candidates = parse_product_service_tables(
        _fixture_tables(KAKAO_TABLE_FIXTURE)
    )
    by_name = _by_name(candidates)

    assert {"플랫폼 부문", "콘텐츠 부문"} <= {
        name for name, item in by_name.items() if item.subject_kind == "segment"
    }
    assert {"카카오톡", "선물하기", "다음(Daum)", "픽코마", "카카오웹툰"} <= {
        name for name, item in by_name.items() if item.subject_kind == "product"
    }


def test_표의_합계행과_괄호속_슬래시는_이름이_되지_않는다() -> None:
    names = {
        candidate.name
        for candidate in parse_product_service_tables(
            _fixture_tables(KAKAO_TABLE_FIXTURE)
        )
    }

    assert "합계" not in names
    assert "기타(A/S) 등" not in names
    assert not any(name.startswith("S)") for name in names)


def test_재고자산_원재료_명세는_제품_이름표가_아니다() -> None:
    markup = """
    <P>다. 재고자산 현황 등</P>
    <TABLE>
      <TR><TH>사업부문</TH><TH>품목</TH><TH>금액</TH></TR>
      <TR><TD>음악사업</TD><TD>재공품</TD><TD>1,234</TD></TR>
      <TR><TD>음악사업</TD><TD>재고자산회전율(회수)</TD><TD>3.1</TD></TR>
    </TABLE>
    """

    assert parse_product_service_tables(parse_filing_tables(markup)) == ()


def test_사람_이름_열은_이름_칸으로_쓰지_않는다() -> None:
    tables = _fixture_tables(WOORI_TABLE_FIXTURE)
    names = {
        candidate.name
        for candidate in collect_name_candidates_from_tables(
            tables, source_kind="사업보고서"
        )
    }

    assert "홍길동" not in names
    assert "대표이사" not in names


def test_표의_상품구분과_상품내용을_함께_읽는다() -> None:
    # 이름이 겹치면 먼저 나온 행만 남는 것은 합치는 단계의 몫이라 여기서 확인한다.
    candidates = collect_name_candidates_from_tables(
        _fixture_tables(WOORI_TABLE_FIXTURE), source_kind="사업보고서"
    )
    by_name = _by_name(candidates)

    assert by_name["신용카드"].description == "일반적인 범용 신용카드"
    assert by_name["단기카드대출 (현금서비스)"].description.startswith("사전에 부여된")
    assert by_name["신용카드"].subject_kind == "product"


def test_표에서_종속회사와_주요사업을_읽는다() -> None:
    by_name = _by_name(
        parse_subsidiary_tables(_fixture_tables(KAKAO_TABLE_FIXTURE))
    )

    assert by_name["㈜카카오엔터테인먼트"].subject_kind == "subsidiary"
    assert (
        by_name["㈜카카오엔터테인먼트"].description
        == "콘텐츠 제작업 · 웹툰·웹소설 제작"
    )


def test_표에서_계약명과_기간_진행률을_읽는다() -> None:
    by_name = _by_name(
        parse_major_contract_tables(_fixture_tables(KAKAO_TABLE_FIXTURE))
    )

    assert set(by_name) == {"공공 클라우드 전환 구축"}
    description = by_name["공공 클라우드 전환 구축"].description
    assert "계약기간: 2024-07-01~2025-06-30" in description
    assert "진행률: 91%" in description


def test_세로로_세운_계약표의_항목_이름은_계약명이_아니다() -> None:
    markup = """
    <P>다. 옵션계약</P>
    <TABLE>
      <TR><TD>계약명</TD><TD>주주간 약정</TD></TR>
      <TR><TD>거래상대방</TD><TD>비지배주주</TD></TR>
      <TR><TD>계약일</TD><TD>2025-01-02</TD></TR>
    </TABLE>
    """
    names = {
        candidate.name
        for candidate in parse_major_contract_tables(parse_filing_tables(markup))
    }

    assert "거래상대방" not in names
    assert "계약일" not in names


def test_표_후보의_원문위치와_원문행은_그_행_그대로다() -> None:
    candidates = collect_name_candidates_from_tables(
        _fixture_tables(HYBE_ARTIST_FIXTURE), source_kind="사업보고서"
    )
    candidate = _by_name(candidates)["뉴진스"]

    assert candidate.location.startswith("나. 주요 아티스트 전속계약 · ")
    assert candidate.location.endswith("행")
    assert candidate.name in candidate.excerpt
    assert " | " in candidate.excerpt
    assert candidate.excerpt_sha256 == exact_text_sha256(candidate.excerpt)
    assert candidate.source_kind == "사업보고서"


def test_표_후보도_같은_이름은_먼저_나온_하나만_남긴다() -> None:
    candidates = collect_name_candidates_from_tables(
        _fixture_tables(KAKAO_TABLE_FIXTURE), source_kind="분기보고서"
    )
    names = [candidate.name for candidate in candidates]

    assert len(names) == len(set(names))
    assert all(candidate.source_kind == "분기보고서" for candidate in candidates)


def test_표_후보_상한을_넘기지_않는다() -> None:
    rows = "".join(
        f"<TR><TD>부문{index}</TD><TD>제품{index}</TD></TR>"
        for index in range(MAX_NAME_CANDIDATES + 10)
    )
    markup = (
        "<P>가. 주요 제품 및 서비스의 현황</P>"
        f"<TABLE><TR><TH>구분</TH><TH>제품명</TH></TR>{rows}</TABLE>"
    )

    candidates = collect_name_candidates_from_tables(
        parse_filing_tables(markup), source_kind="사업보고서"
    )

    assert len(candidates) == MAX_NAME_CANDIDATES


def test_표가_없으면_예외없이_빈_tuple이다() -> None:
    assert collect_name_candidates_from_tables((), source_kind="사업보고서") == ()
    assert (
        collect_name_candidates_from_tables(
            parse_filing_tables("<P>표 없는 본문</P>"), source_kind="사업보고서"
        )
        == ()
    )


def test_그룹칸이_본명에_들어_있으면_사람_이름으로_본다() -> None:
    """솔로 표기는 그룹 칸에 활동명, 아티스트 칸에 본명이 들어간다."""

    markup = """
    <P>나. 주요 아티스트 전속계약</P>
    <TABLE>
      <TR><TD>회 사 명</TD><TD>그 룹</TD><TD>아 티 스 트</TD></TR>
      <TR><TD>㈜예시레이블</TD><TD>민현</TD><TD>황민현</TD></TR>
      <TR><TD>㈜예시레이블</TD><TD>이 현</TD><TD>이 현</TD></TR>
      <TR><TD>㈜예시레이블</TD><TD>지코</TD><TD>우지호</TD></TR>
      <TR><TD>㈜예시레이블</TD><TD>다운(Dvwn)</TD><TD>정다운</TD></TR>
    </TABLE>
    """
    names = [
        candidate.name
        for candidate in parse_artist_contract_tables(parse_filing_tables(markup))
    ]

    assert names == ["지코", "다운(Dvwn)"]


def test_계약명_칸만_있는_두칸_표는_계약표로_보지_않는다() -> None:
    markup = """
    <P>다. 옵션계약</P>
    <TABLE>
      <TR><TD>계약명</TD><TD>주주간 약정</TD></TR>
      <TR><TD>만기일</TD><TD>2030-12-31</TD></TR>
      <TR><TD>계약 체결 목적</TD><TD>지분 확보</TD></TR>
    </TABLE>
    """

    assert parse_major_contract_tables(parse_filing_tables(markup)) == ()


def test_원문발췌에는_멤버_본명이_들어가지_않는다() -> None:
    """발췌는 작가 프롬프트와 부록 근거 원문에 그대로 실린다."""

    tables = _fixture_tables(HYBE_ARTIST_FIXTURE)
    members = {
        row[2]
        for row in tables[0].rows[1:]
        if len(row) > 2 and row[2].strip()
    }
    candidates = parse_artist_contract_tables(tables)

    assert members and candidates
    for candidate in candidates:
        assert not any(member in candidate.excerpt for member in members)
        # 결속 검사(P3-3)를 위해 이름은 여전히 발췌 안에 있어야 한다.
        assert candidate.name in candidate.excerpt
    by_name = _by_name(candidates)
    assert by_name["방탄소년단"].excerpt == "㈜빅히트뮤직 | 방탄소년단"


def test_사람_이름_열은_발췌에서도_빠진다() -> None:
    markup = """
    <P>가. 주요 아티스트 전속계약</P>
    <TABLE>
      <TR><TD>회사명</TD><TD>그룹</TD><TD>아티스트</TD><TD>계약 종료</TD></TR>
      <TR><TD>㈜예시레이블</TD><TD>예시그룹</TD><TD>홍길동</TD><TD>2030-01-01</TD></TR>
    </TABLE>
    """
    candidates = parse_artist_contract_tables(parse_filing_tables(markup))

    assert len(candidates) == 1
    assert candidates[0].excerpt == "㈜예시레이블 | 예시그룹 | 2030-01-01"


def test_제품_표가_상한에_닿아도_뒤_규칙의_대표IP는_살아남는다() -> None:
    """상한을 전체 합계로 끊으면 제품 표가 큰 회사에서 IP가 0건이 된다."""

    product_rows = "".join(
        f"<TR><TD>부문{index}</TD><TD>제품{index}</TD></TR>"
        for index in range(MAX_NAME_CANDIDATES + 5)
    )
    markup = (
        "<P>가. 주요 제품 및 서비스의 현황</P>"
        f"<TABLE><TR><TH>구분</TH><TH>제품명</TH></TR>{product_rows}</TABLE>"
        "<P>나. 주요 아티스트 전속계약</P>"
        "<TABLE>"
        "<TR><TH>회사명</TH><TH>그룹</TH><TH>아티스트</TH></TR>"
        "<TR><TD>㈜예시레이블</TD><TD>예시그룹</TD><TD>홍길동</TD></TR>"
        "</TABLE>"
    )

    candidates = collect_name_candidates_from_tables(
        parse_filing_tables(markup), source_kind="사업보고서"
    )
    ip_names = [
        candidate.name
        for candidate in candidates
        if candidate.subject_kind == SUBJECT_IP
    ]
    product_like = [
        candidate
        for candidate in candidates
        if candidate.subject_kind != SUBJECT_IP
    ]

    assert ip_names == ["예시그룹"]
    # 규칙 하나가 가져가는 양은 여전히 상한 안이다.
    assert len(product_like) == MAX_NAME_CANDIDATES

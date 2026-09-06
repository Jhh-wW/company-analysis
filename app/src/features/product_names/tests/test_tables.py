"""공시 원문(HTML/XML)의 표를 행·칸 구조로 읽는 규칙 시험."""

from pathlib import Path

from src.features.product_names.constants import (
    MAX_TABLE_CELL_CHARS,
    MAX_TABLE_ROWS,
    MAX_TABLE_SPAN,
)
from src.features.product_names.tables import (
    FilingTable,
    parse_filing_tables,
    read_filing_tables,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_rowspan과_colspan은_아래와_오른쪽으로_채워진다() -> None:
    markup = """
    <P>가. 예시 표</P>
    <TABLE>
      <TR><TD ROWSPAN="2">회사</TD><TD COLSPAN="2">묶음 머리</TD></TR>
      <TR><TD>왼쪽</TD><TD>오른쪽</TD></TR>
      <TR><TD>다른 회사</TD><TD>가</TD><TD>나</TD></TR>
    </TABLE>
    """

    tables = parse_filing_tables(markup)

    assert len(tables) == 1
    assert tables[0].rows == (
        ("회사", "묶음 머리", "묶음 머리"),
        ("회사", "왼쪽", "오른쪽"),
        ("다른 회사", "가", "나"),
    )


def test_표_제목은_바로_앞_줄에서_잡고_붙은_표는_물려받는다() -> None:
    markup = """
    <P>나. 주요 아티스트 전속계약</P>
    <TABLE><TR><TD>(단위 : 백만원)</TD></TR></TABLE>
    <TABLE><TR><TD>회사명</TD><TD>그룹</TD></TR></TABLE>
    """

    titles = [table.title for table in parse_filing_tables(markup)]

    assert titles == ["나. 주요 아티스트 전속계약", "나. 주요 아티스트 전속계약"]


def test_앞줄이_문단이면_위쪽의_절_표제를_제목으로_쓴다() -> None:
    markup = (
        "<P>라. 주요 사업의 내용</P>"
        "<P>" + "당사는 본사를 거점으로 여러 지역총괄의 생산·판매 체제를 갖추고 있습니다. " * 3 + "</P>"
        "<TABLE><TR><TD>구분</TD><TD>품목</TD></TR></TABLE>"
    )

    assert parse_filing_tables(markup)[0].title == "라. 주요 사업의 내용"


def test_칸_안의_여러_문단은_한_칸의_이어진_글자가_된다() -> None:
    markup = "<TABLE><TR><TD><P>2025년 제21기</P><P>(당 기)</P></TD></TR></TABLE>"

    assert parse_filing_tables(markup)[0].rows == (("2025년 제21기 (당 기)",),)


def test_해제되지_않은_ampersand도_글자_그대로_읽는다() -> None:
    """공시 원문은 XML 선언을 달고도 ``&팀`` 같은 날 ``&``를 그대로 담는다."""

    markup = "<TABLE><TR><TD>&팀</TD><TD>&amp;더블유</TD></TR></TABLE>"

    assert parse_filing_tables(markup)[0].rows == (("&팀", "&더블유"),)


def test_이상한_span값은_한_칸으로_본다() -> None:
    markup = '<TABLE><TR><TD ROWSPAN="0" COLSPAN="없음">값</TD><TD>옆</TD></TR></TABLE>'

    assert parse_filing_tables(markup)[0].rows == (("값", "옆"),)


def test_표가_상한을_넘으면_앞부분만_남긴다() -> None:
    rows = "".join(f"<TR><TD>행{index}</TD></TR>" for index in range(MAX_TABLE_ROWS + 5))

    table = parse_filing_tables(f"<TABLE>{rows}</TABLE>")[0]

    assert len(table.rows) == MAX_TABLE_ROWS


def test_아주_긴_칸은_잘라서_담는다() -> None:
    long_cell = "가" * (MAX_TABLE_CELL_CHARS + 50)

    table = parse_filing_tables(f"<TABLE><TR><TD>{long_cell}</TD></TR></TABLE>")[0]

    assert len(table.rows[0][0]) == MAX_TABLE_CELL_CHARS


def test_닫히지_않은_표도_읽은_만큼_남긴다() -> None:
    markup = "<TABLE><TR><TD>남는 값</TD></TR>"

    assert parse_filing_tables(markup)[0].rows == (("남는 값",),)


def test_표가_없거나_입력이_비면_빈_tuple이다() -> None:
    assert parse_filing_tables("<P>표가 없는 본문입니다.</P>") == ()
    assert parse_filing_tables("") == ()
    assert parse_filing_tables(None) == ()


def test_없는_파일은_예외없이_빈_tuple이다(tmp_path: Path) -> None:
    assert read_filing_tables(tmp_path / "없는파일.xml") == ()
    assert read_filing_tables("") == ()


def test_cp949로_저장된_원문도_읽는다(tmp_path: Path) -> None:
    """``read_filing_text``와 같은 순서(utf-8 → cp949 → euc-kr)로 해독한다."""

    path = tmp_path / "cp949.xml"
    path.write_bytes("<TABLE><TR><TD>한글 칸</TD></TR></TABLE>".encode("cp949"))

    assert read_filing_tables(path)[0].rows == (("한글 칸",),)


def test_하이브_아티스트_표_픽스처는_세_칸_구조로_읽힌다() -> None:
    tables = read_filing_tables(FIXTURES / "hybe_artist_contracts.xml")

    assert len(tables) == 1
    table = tables[0]
    assert isinstance(table, FilingTable)
    assert table.title == "나. 주요 아티스트 전속계약"
    assert table.rows[0] == ("회 사 명", "그 룹", "아 티 스 트")
    # ROWSPAN 채움 덕분에 회사명·그룹 칸이 아래 행에서도 비지 않는다.
    assert table.rows[1][:2] == ("㈜빅히트뮤직", "방탄소년단")
    assert table.rows[2][:2] == ("㈜빅히트뮤직", "방탄소년단")
    assert all(len(row) == 3 for row in table.rows)


def test_경로가_아예_잘못돼도_예외없이_빈_tuple이다() -> None:
    """이름 표는 «있으면 좋은» 추가물이라 여기서 조사를 멈추면 안 된다."""

    assert read_filing_tables("널\x00문자가 섞인 경로.xml") == ()
    assert read_filing_tables("<MagicMock id='0'>") == ()


def test_아주_큰_span은_상한에서_끊는다() -> None:
    """어긋난 마크업 한 칸이 표 전체를 채우며 메모리를 먹지 못하게 한다."""

    rows = "".join(
        f"<TR><TD>행{index}</TD></TR>" for index in range(MAX_TABLE_SPAN + 50)
    )
    markup = (
        f'<TABLE><TR><TD ROWSPAN="99999">채움</TD><TD>첫 행</TD></TR>{rows}</TABLE>'
    )

    table = parse_filing_tables(markup)[0]
    filled = [row for row in table.rows if row[0] == "채움"]

    assert len(filled) == MAX_TABLE_SPAN
    assert table.rows[MAX_TABLE_SPAN][0] != "채움"


def test_아주_큰_colspan도_상한에서_끊는다() -> None:
    markup = '<TABLE><TR><TD COLSPAN="99999">넓은 칸</TD></TR></TABLE>'

    table = parse_filing_tables(markup)[0]

    assert len(table.rows[0]) == MAX_TABLE_SPAN
    assert set(table.rows[0]) == {"넓은 칸"}

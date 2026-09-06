"""제품·서비스 이름 표 파서의 닫힌 규칙."""

from __future__ import annotations

import re
from typing import Final

SUBJECT_PRODUCT: Final[str] = "product"
SUBJECT_BRAND: Final[str] = "brand"
SUBJECT_SEGMENT: Final[str] = "segment"
SUBJECT_SUBSIDIARY: Final[str] = "subsidiary"
SUBJECT_CONTRACT: Final[str] = "contract"
SUBJECT_IP: Final[str] = "ip"
SUBJECT_KINDS: Final[frozenset[str]] = frozenset(
    {
        SUBJECT_PRODUCT,
        SUBJECT_BRAND,
        SUBJECT_SEGMENT,
        SUBJECT_SUBSIDIARY,
        SUBJECT_CONTRACT,
        SUBJECT_IP,
    }
)

MAX_NAME_CANDIDATES: Final[int] = 40
# 조립기 프롬프트 길이는 곧 비용이므로 건당 300~400원 목표 안에서 제한한다.
MAX_NAME_FRAGMENTS_PER_FILING: Final[int] = 16
# 한 종류가 예산을 다 먹으면 다른 종류가 한 건도 못 들어간다. 실측(하이브)에서
# 제품 후보가 11건이라, 종류별 상한이 없으면 대표 IP가 0건이 된다.
MAX_NAME_FRAGMENTS_PER_KIND: Final[int] = 10
# 조각 자리를 나눠 주는 순서. 사업부문 → 대표 IP → 제품 순으로,
# 「그 회사가 무엇으로 불리는가」에 가까운 이름부터 자리를 준다.
NAME_FRAGMENT_KIND_ORDER: Final[tuple[str, ...]] = (
    SUBJECT_SEGMENT,
    SUBJECT_IP,
    SUBJECT_PRODUCT,
    SUBJECT_BRAND,
    SUBJECT_SUBSIDIARY,
    SUBJECT_CONTRACT,
)
MIN_NAME_CHARS: Final[int] = 2
MAX_NAME_CHARS: Final[int] = 100
UNSPECIFIED_SOURCE_KIND: Final[str] = ""

# ── 공시 원문 표 읽기 ────────────────────────────────────────────────
FILING_TEXT_ENCODINGS: Final[tuple[str, ...]] = ("utf-8", "cp949", "euc-kr")
TABLE_TAG: Final[str] = "table"
TABLE_ROW_TAG: Final[str] = "tr"
TABLE_CELL_TAGS: Final[frozenset[str]] = frozenset({"td", "th"})
# 표 밖에서는 줄 경계, 칸 안에서는 낱말 경계로 쓴다.
TABLE_LINE_BREAK_TAGS: Final[frozenset[str]] = frozenset(
    {"p", "br", "div", "li", "title", "h1", "h2", "h3", "h4", "h5", "h6"}
)
# 표 하나가 통째로 메모리를 먹지 않도록 자른다(공시 재무제표는 수천 행이다).
MAX_TABLE_ROWS: Final[int] = 5_000
MAX_TABLE_COLUMNS: Final[int] = 200
# ROWSPAN/COLSPAN 한 칸이 표를 통째로 부풀리지 못하게 막는다 — 어긋난
# 마크업 하나가 5,000행×200열을 채우면 그 공시 하나에 메모리·시간이 쏠린다.
MAX_TABLE_SPAN: Final[int] = 100
MAX_TABLE_CELL_CHARS: Final[int] = 500
MAX_TABLE_TITLE_CHARS: Final[int] = 120
# 머리행은 표 맨 위 몇 줄만 본다 — 그 아래는 자료 행으로 읽는다.
MAX_HEADER_ROWS: Final[int] = 4
WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
TABLE_CELL_JOINER: Final[str] = " | "
UNTITLED_TABLE_LOCATION: Final[str] = "공시 표"
TABLE_ROW_LOCATION_SUFFIX: Final[str] = "행"

PRODUCT_SERVICE_SECTION_TITLES: Final[tuple[str, ...]] = (
    "주요 제품 및 서비스 현황",
    "주요 제품 및 서비스",
    "주요 제품 등의 현황",
    "주요 제품·서비스",
)
NAMED_SERVICE_SECTION_TITLES: Final[tuple[str, ...]] = (
    "주요 상품 및 서비스의 내용",
    "주요 상품 및 서비스 내용",
)
SUBSIDIARY_SECTION_TITLES: Final[tuple[str, ...]] = (
    "주요 종속회사의 업종 및 주요 사업",
    "연결대상 종속회사 개황",
    "연결대상 종속회사 현황",
    "연결대상 종속회사",
    "종속기업 현황",
    "특수관계자 현황",
    "특수관계자",
)
MAJOR_CONTRACT_SECTION_TITLES: Final[tuple[str, ...]] = (
    "총 계약금액이 직전 회계연도 매출액의 5% 이상인 계약",
    "총 계약금액이 매출액의 5% 이상인 계약",
    "매출액의 5% 이상인 계약",
)

# 업종별 이름 칸 어휘. 회사·업종을 가리지 않고 머리행 글자만 본다
# (제조·플랫폼·금융·바이오 공시가 같은 자리에 쓰는 말을 모았다).
PRODUCT_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "주요제품서비스",
        "주요제품및서비스",
        "주요제품및서비스현황",
        "품목",
        "주요품목",
        "주요제품",
        "제품명",
        "제품",
        "모델",
        "모델명",
        "서비스",
        "주요서비스",
    }
)
SEGMENT_HEADERS: Final[frozenset[str]] = frozenset(
    {"사업부문", "부문", "사업부", "구분", "부문명", "영업부문"}
)
NAMED_SERVICE_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "상품명",
        "서비스명",
        "상품",
        "주요상품",
        "주요상품및서비스",
        "상품구분",
        "서비스구분",
        "상품종류",
        "카드명",
        "카드",
        "펀드명",
        "플랫폼",
        "플랫폼명",
        "앱",
        "앱명",
    }
)
DESCRIPTION_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "주요내용",
        "내용",
        "상품내용",
        "서비스내용",
        "설명",
        "특징",
        "주요특징",
        "비고",
    }
)
# 「대표 IP」 이름 칸. 브랜드·게임 타이틀·개발 파이프라인처럼 회사가 내세우는
# 고유명사가 들어가는 자리다.
IP_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "브랜드",
        "브랜드명",
        "주요브랜드",
        "타이틀",
        "타이틀명",
        "게임명",
        "주요게임",
        "ip",
        "주요ip",
        "파이프라인",
        "파이프라인명",
    }
)
# 「제품명 + 개발단계」처럼 단계 칸이 함께 있으면 그 제품명은 대표 IP로 본다.
IP_STAGE_HEADERS: Final[frozenset[str]] = frozenset(
    {"개발단계", "임상단계", "진행단계", "단계", "적응증"}
)
# 아티스트 전속계약 표(엔터)의 세 칸. 「그 룹」처럼 글자 사이 공백은 정규화된다.
# 「팀」은 넣지 않는다 — 하이브의 실제 그룹명 「&팀」이 머리행 글자와 같아져
# 이름 후보에서 통째로 떨어진다.
ARTIST_GROUP_HEADERS: Final[frozenset[str]] = frozenset({"그룹", "그룹명"})
ARTIST_MEMBER_HEADERS: Final[frozenset[str]] = frozenset(
    {"아티스트", "소속아티스트", "멤버", "구성원"}
)
# 사람 이름이 들어가는 칸. 이 아래 값은 이름 후보로 쓰지 않는다.
PERSON_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {"성명", "이름", "대표자", "대표자명", "임원", "직원", "담당자", "본인"}
)
COMPANY_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "회사명",
        "종속회사명",
        "종속기업명",
        "특수관계자명",
        "상호",
        "법인명",
        "기업명",
    }
)
BUSINESS_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "업종",
        "주요사업",
        "주요사업내용",
        "영위사업",
        "영업내용",
        "사업내용",
    }
)
RELATION_HEADERS: Final[frozenset[str]] = frozenset({"구분", "관계"})
CONTRACT_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {"계약명", "계약의명칭", "공사명", "프로젝트명"}
)
CONTRACT_PERIOD_HEADERS: Final[frozenset[str]] = frozenset(
    {"계약기간", "기간", "공사기간"}
)
CONTRACT_PROGRESS_HEADERS: Final[frozenset[str]] = frozenset(
    {"진행률", "진행율", "공사진행률"}
)
# 세로로 세운 계약 표(머리말이 왼쪽 열)에서 항목 이름이 계약명으로 새는 것을 막는다.
CONTRACT_FIELD_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "거래상대방",
        "계약상대방",
        "계약일",
        "계약금액",
        "계약조건",
        "계약내용",
        "계약목적",
        "계약체결일",
    }
)

ALL_HEADER_KEYS: Final[frozenset[str]] = frozenset().union(
    PRODUCT_NAME_HEADERS,
    SEGMENT_HEADERS,
    NAMED_SERVICE_NAME_HEADERS,
    DESCRIPTION_HEADERS,
    COMPANY_NAME_HEADERS,
    BUSINESS_HEADERS,
    RELATION_HEADERS,
    CONTRACT_NAME_HEADERS,
    CONTRACT_PERIOD_HEADERS,
    CONTRACT_PROGRESS_HEADERS,
    CONTRACT_FIELD_HEADERS,
    IP_NAME_HEADERS,
    IP_STAGE_HEADERS,
    ARTIST_GROUP_HEADERS,
    ARTIST_MEMBER_HEADERS,
    PERSON_NAME_HEADERS,
)

REJECTED_NAME_KEYS: Final[frozenset[str]] = frozenset(
    {
        "백만원",
        "합계",
        "소계",
        "계",
        "총계",
        "총합계",
        "기타",
        "기타등",
        "미확인",
        "해당사항없음",
        "해당사항없습니다",
        "해당없음",
        "없음",
        "주",
        "주1",
        "주2",
        "주3",
        # 재무제표·재고 명세의 줄 이름은 회사가 파는 것이 아니다.
        "매출액",
        "매출",
        "영업수익",
        "영업이익",
        "내부매출액",
        "당기순이익",
        "자산총계",
        "부채총계",
        "자본총계",
        "재공품",
        "원재료",
        "부재료",
        "미착품",
        "저장품",
        "반제품",
        "재고자산평가충당금",
    }
)
# 이 말로 시작하는 값은 항목 이름이 아니라 묶음·잔여 줄이다.
REJECTED_NAME_PREFIXES: Final[tuple[str, ...]] = (
    "기타",
    "합계",
    "소계",
    "총계",
    "총합계",
    "부문계",
    "내부거래",
    "부문간내부거래",
    "연결기준합계",
    "해당사항",
)
# 표 제목이 이 말을 담으면 대표 IP 표로 본다(머리행 어휘와 함께 쓴다).
IP_TABLE_TITLE_KEYWORDS: Final[tuple[str, ...]] = (
    "주요 아티스트",
    "브랜드",
    "주요 게임",
    "파이프라인",
)
# 이 말이 제목에 있으면 제품·서비스 이름 표가 아니다 — 재고·원재료·설비 명세다.
# (실측: 「재고자산회전율(회수) [연환산 매출원가÷…]」 같은 줄이 제품으로 샜다.)
EXCLUDED_TABLE_TITLE_KEYWORDS: Final[tuple[str, ...]] = (
    "재고자산",
    "원재료",
    "생산능력",
    "생산실적",
    "가격변동",
)
# 표 제목은 표 바로 앞 줄에서 잡되, 그 줄이 이보다 길면 문단으로 보고
# 위쪽에서 절 표제 모양의 짧은 줄을 찾는다.
MAX_TABLE_HEADING_CHARS: Final[int] = 60
MAX_TABLE_TITLE_LOOKBACK_LINES: Final[int] = 12
TABLE_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[가-힣]\.|[IVXLC]{1,4}\.|\d{1,2}[.)]|\(|\[|<|[①-⑮])"
)
NAME_EDGE_CHARS: Final[str] = " \t\r\n-–—•ㆍ·,;:"
# 괄호 안의 ``/``·``,``는 이름의 일부다(「기타(A/S) 등」).
NAME_BRACKET_OPENERS: Final[str] = "([{<（［｛"
NAME_BRACKET_CLOSERS: Final[str] = ")]}>）］｝"
COMPANY_MARKERS: Final[tuple[str, ...]] = (
    "㈜",
    "(주)",
    "주식회사",
    "Co., Ltd.",
    "Co.,Ltd.",
    "Inc.",
)

# 공시 평문은 줄바꿈은 남고 열 경계만 눌리는 경우가 있어 세 모양만 인정한다.
COLUMN_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"(?:\t+| {2,}|\|)")
NAME_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"[,·/]")
HEADER_KEY_NOISE_RE: Final[re.Pattern[str]] = re.compile(r"[^0-9A-Za-z가-힣]")

# 다음 절 표제만 경계로 쓴다. 행 안의 ``1.0``이나 회사명의 ``Inc.``는 잡지 않는다.
SECTION_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^[ \t]*(?:"
    r"(?:[IVXLC]+|\d{1,2}|[가-힣])\.[ \t]+\S"
    r"|\(\d{1,2}\)[ \t]+\S"
    r"|제[ \t]*\d+[ \t]*(?:장|절)[ \t]*\S"
    r")"
)

NUMERIC_OR_UNIT_ONLY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[\s\d,.+\-△()%￦₩원천백만억조금액]+$"
)


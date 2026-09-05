"""제품·서비스 이름 표 파서의 닫힌 규칙."""

from __future__ import annotations

import re
from typing import Final

SUBJECT_PRODUCT: Final[str] = "product"
SUBJECT_BRAND: Final[str] = "brand"
SUBJECT_SEGMENT: Final[str] = "segment"
SUBJECT_SUBSIDIARY: Final[str] = "subsidiary"
SUBJECT_CONTRACT: Final[str] = "contract"
SUBJECT_KINDS: Final[frozenset[str]] = frozenset(
    {
        SUBJECT_PRODUCT,
        SUBJECT_BRAND,
        SUBJECT_SEGMENT,
        SUBJECT_SUBSIDIARY,
        SUBJECT_CONTRACT,
    }
)

MAX_NAME_CANDIDATES: Final[int] = 40
MIN_NAME_CHARS: Final[int] = 2
MAX_NAME_CHARS: Final[int] = 100
UNSPECIFIED_SOURCE_KIND: Final[str] = ""

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

PRODUCT_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "주요제품서비스",
        "주요제품및서비스",
        "품목",
        "주요제품",
        "제품명",
        "서비스",
    }
)
SEGMENT_HEADERS: Final[frozenset[str]] = frozenset(
    {"사업부문", "부문", "사업부", "구분"}
)
NAMED_SERVICE_NAME_HEADERS: Final[frozenset[str]] = frozenset(
    {"상품명", "서비스명", "상품"}
)
DESCRIPTION_HEADERS: Final[frozenset[str]] = frozenset(
    {"주요내용", "내용", "상품내용", "서비스내용"}
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
)

REJECTED_NAME_KEYS: Final[frozenset[str]] = frozenset(
    {"백만원", "합계", "소계", "계", "기타", "미확인"}
)
NAME_EDGE_CHARS: Final[str] = " \t\r\n-–—•ㆍ·,;:"
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


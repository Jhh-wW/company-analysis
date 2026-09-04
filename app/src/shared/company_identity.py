"""식별번호로 결속된 공식 회사명 비교의 공통 계약."""

from __future__ import annotations

import re
import unicodedata
from typing import Final


DART_CORP_CODE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
STOCK_CODE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{6}")
KOREAN_REGISTRATION_NUMBER_LENGTHS: Final[frozenset[int]] = frozenset({10, 13})
KOREAN_CORPORATE_MARKERS: Final[tuple[str, ...]] = (
    "(주)",
    "(유)",
    "(사)",
    "(재)",
    "㈜",
)
KOREAN_CORPORATE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "주식회사",
        "유한회사",
        "유한책임회사",
        "합자회사",
        "합명회사",
    }
)
SUPPORTED_NAME_PUNCTUATION: Final[frozenset[str]] = frozenset(
    ".,&'\"’·()[]{}-_/+"
)

_TOKEN_RE = re.compile(r"[0-9a-zA-Z가-힣]+")
_LATIN_ACRONYM_RE = re.compile(r"[A-Za-z]{2,5}")
_DOTTED_LATIN_ACRONYM_RE = re.compile(r"[A-Za-z](?:\.[A-Za-z]){1,4}\.?")
_OFFICIAL_MIXED_NAME_ACRONYM_RE = re.compile(r"^([A-Z]{2,5})(?=[가-힣])")
_LATIN_LETTER_NAMES: Final[dict[str, str]] = {
    "A": "에이",
    "B": "비",
    "C": "씨",
    "D": "디",
    "E": "이",
    "F": "에프",
    "G": "지",
    "H": "에이치",
    "I": "아이",
    "J": "제이",
    "K": "케이",
    "L": "엘",
    "M": "엠",
    "N": "엔",
    "O": "오",
    "P": "피",
    "Q": "큐",
    "R": "알",
    "S": "에스",
    "T": "티",
    "U": "유",
    "V": "브이",
    "W": "더블유",
    "X": "엑스",
    "Y": "와이",
    "Z": "지",
}


def _name_tokens(value: object) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for marker in KOREAN_CORPORATE_MARKERS:
        text = text.replace(marker, " ")
    return tuple(
        token
        for token in _TOKEN_RE.findall(text)
        if token and token not in KOREAN_CORPORATE_TOKENS
    )


def _is_supported_company_name(value: object) -> bool:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return False
    for character in text:
        if (
            ("A" <= character <= "Z")
            or ("a" <= character <= "z")
            or ("0" <= character <= "9")
            or ("가" <= character <= "힣")
            or character.isspace()
            or character in SUPPORTED_NAME_PUNCTUATION
        ):
            continue
        return False
    return True


def exact_company_name_key(value: object) -> str:
    """법인 표지와 구두점만 무시하고 단어 경계를 보존한 키를 만든다."""

    return "\x1f".join(_name_tokens(value))


def normalize_korean_registration_number(value: object) -> str:
    """공개 사업자/법인번호의 ASCII 숫자·공백·하이픈 표기만 정규화한다.

    임의 글자를 모두 지운 뒤 우연히 10/13자리가 된 값을 받으면 서로 다른
    입력이 같은 안정 식별번호로 합쳐진다. DART가 주는 일반적인 연속 숫자,
    ``123-45-67890`` 및 공백 표기만 허용하고 나머지는 추측하지 않는다.
    """

    raw = str(value or "")
    if not raw or any(
        not ("0" <= character <= "9") and character not in {" ", "-"}
        for character in raw
    ):
        return ""
    digits = "".join(character for character in raw if "0" <= character <= "9")
    return digits if len(digits) in KOREAN_REGISTRATION_NUMBER_LENGTHS else ""


def exact_company_names_equivalent(left: object, right: object) -> bool:
    """별칭이나 유사도 추론 없이 두 공식 회사명 표기를 비교한다."""

    if not _is_supported_company_name(left) or not _is_supported_company_name(right):
        return False
    left_key = exact_company_name_key(left)
    right_key = exact_company_name_key(right)
    return bool(left_key and right_key and left_key == right_key)


def normalized_latin_acronym(value: object) -> str:
    """2~5자 라틴 약어를 대문자 비구두점 표기로 정규화한다."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    if _LATIN_ACRONYM_RE.fullmatch(normalized):
        return normalized.upper()
    if _DOTTED_LATIN_ACRONYM_RE.fullmatch(normalized):
        return normalized.replace(".", "").upper()
    return ""


def latin_acronym_korean(value: object) -> str:
    """검증된 짧은 라틴 약어를 한글 자모 이름 독음으로 바꾼다."""

    acronym = normalized_latin_acronym(value)
    if not acronym:
        return ""
    return "".join(_LATIN_LETTER_NAMES[letter] for letter in acronym)


def _official_acronym_name_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not _is_supported_company_name(text):
        return ""
    for marker in KOREAN_CORPORATE_MARKERS:
        text = text.replace(marker, " ")
    converted: list[str] = []
    for raw_token in _TOKEN_RE.findall(text):
        folded = raw_token.casefold()
        if folded in KOREAN_CORPORATE_TOKENS:
            continue
        match = _OFFICIAL_MIXED_NAME_ACRONYM_RE.match(raw_token)
        if match is not None:
            acronym = match.group(1)
            raw_token = latin_acronym_korean(acronym) + raw_token[match.end() :]
        converted.append(raw_token.casefold())
    return "\x1f".join(converted)


def verified_official_company_names_equivalent(
    left: object,
    right: object,
    *,
    observed_corp_code: object = "",
    expected_corp_code: object = "",
    observed_stock_code: object = "",
    expected_stock_code: object = "",
) -> bool:
    """안정 식별자가 같은 경우에만 좁은 공식명 표기 등가를 허용한다.

    동일한 DART 고유번호 또는 종목코드가 먼저 법인 동일성을 증명해야 한다.
    그 뒤에만 ``SK하이닉스``와 ``에스케이하이닉스``처럼 한글 회사명 앞에
    붙은 2~5자 대문자 약어를 결정적인 한글 독음으로 바꾼다. 오타·토큰·유사도
    기반 비교는 수행하지 않는다.
    """

    observed_corp = str(observed_corp_code or "").strip()
    expected_corp = str(expected_corp_code or "").strip()
    observed_stock = str(observed_stock_code or "").strip()
    expected_stock = str(expected_stock_code or "").strip()

    corp_supplied = bool(observed_corp or expected_corp)
    stock_supplied = bool(observed_stock or expected_stock)
    corp_verified = bool(
        DART_CORP_CODE_RE.fullmatch(observed_corp)
        and observed_corp == expected_corp
    )
    stock_verified = bool(
        STOCK_CODE_RE.fullmatch(observed_stock)
        and observed_stock == expected_stock
    )
    if (corp_supplied and not corp_verified) or (
        stock_supplied and not stock_verified
    ):
        return False
    if not (corp_verified or stock_verified):
        return False
    if exact_company_names_equivalent(left, right):
        return True

    left_key = _official_acronym_name_key(left)
    right_key = _official_acronym_name_key(right)
    return bool(left_key and right_key and left_key == right_key)

"""서로 다른 등록 도메인의 회사 공식 페이지를 확인하는 신원 계약.

공식 홈페이지가 외부 링크를 걸었다는 사실만으로 그 도메인을 회사 소유로
보면 안 된다. 협력사·언론·결제사 링크도 똑같이 걸리기 때문이다. 이 모듈은
DART 기업개황에서 받은 정확한 법인명과 안정 식별번호(사업자등록번호 또는
법인등록번호)가 대상 HTML에 함께 있을 때만 교차 도메인 후보를 승인한다.

도메인명·회사명을 손으로 적은 allowlist는 사용하지 않는다. 입력 URL은 검색
결과 snippet이 아니라 상위의 출처 있는 발견 경로가 건넨 exact URL이어야 하며,
여기서는 그 URL의 실제 본문을 다시 검증한다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

from src.shared.company_identity import (
    exact_company_name_key,
    normalize_korean_registration_number,
)

_BUSINESS_NUMBER_LENGTH = 10
_CORPORATE_NUMBER_LENGTH = 13
_BUSINESS_NUMBER_MARKERS = (
    "사업자등록번호",
    "사업자 등록번호",
    "사업자번호",
    "사업자 번호",
)
_CORPORATE_NUMBER_MARKERS = (
    "법인등록번호",
    "법인 등록번호",
    "법인번호",
    "법인 번호",
)
_NUMBER_CONTEXT_CHARS = 48


@dataclass(frozen=True)
class OfficialCompanyIdentity:
    """DART가 확인한 회사명과 공개 안정 식별번호의 최소 집합."""

    legal_name: str
    aliases: tuple[str, ...] = ()
    registration_numbers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        legal_name = str(self.legal_name or "").strip()
        if not legal_name or not exact_company_name_key(legal_name):
            raise ValueError("교차 도메인 검증에 쓸 DART 법인명이 필요합니다")
        aliases = tuple(
            dict.fromkeys(
                clean
                for value in self.aliases
                if (clean := str(value or "").strip())
            )
        )
        numbers = tuple(
            dict.fromkeys(
                normalized
                for value in self.registration_numbers
                if (normalized := normalize_registration_number(value))
            )
        )
        object.__setattr__(self, "legal_name", legal_name)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "registration_numbers", numbers)

    @property
    def can_verify_cross_domain(self) -> bool:
        """안정 식별번호가 없으면 다른 등록 도메인을 승인하지 않는다."""

        return bool(self.registration_numbers)


@dataclass(frozen=True)
class OfficialIdentityMatch:
    """원문 식별정보를 노출하지 않고 결속에 남길 검증 영수증."""

    evidence_sha256: str
    matched_name_sha256: str
    registration_number_sha256: str


class _IdentityTextExtractor(HTMLParser):
    """footer를 포함한 사람이 읽는 글자와 JSON-LD만 모은다.

    본문 조각 추출기는 화면 잡음을 줄이려고 footer를 뺀다. 하지만 국내 회사
    페이지의 사업자등록번호는 대개 footer에 있으므로, 신원 확인에는 footer를
    버리면 안 된다. 일반 script/style은 제외하고 구조화 조직정보인 JSON-LD만
    포함한다.
    """

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._json_ld_depth = 0
        self._parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        lowered = tag.casefold()
        if lowered == "script":
            values = {key.casefold(): (value or "") for key, value in attrs}
            if values.get("type", "").strip().casefold() == "application/ld+json":
                self._json_ld_depth += 1
            else:
                self._skip_depth += 1
        elif lowered in {"style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "script":
            if self._json_ld_depth:
                self._json_ld_depth -= 1
            elif self._skip_depth:
                self._skip_depth -= 1
        elif lowered in {"style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def normalize_registration_number(value: object) -> str:
    """DART 번호의 하이픈·공백만 없애고 10/13자리 숫자만 받는다."""

    return normalize_korean_registration_number(value)


def _name_token_rows(value: object) -> tuple[str, ...]:
    key = exact_company_name_key(value)
    return tuple(part for part in key.split("\x1f") if part)


def _contains_name_tokens(page_tokens: tuple[str, ...], name: object) -> bool:
    expected = _name_token_rows(name)
    if not expected or len(expected) > len(page_tokens):
        return False
    width = len(expected)
    return any(
        page_tokens[index : index + width] == expected
        for index in range(len(page_tokens) - width + 1)
    )


def _registration_pattern(number: str) -> re.Pattern[str]:
    # 홈페이지 표기는 123-45-67890·123 45 67890·연속 숫자를 모두 쓴다.
    # 숫자 사이의 구분자는 ASCII 공백·하이픈 0~2개만 허용한다. ``[^0-9]``를
    # 쓰면 ``1a2b3...``처럼 글자가 낀 일반 문장도 등록번호로 합쳐져, 공개된
    # 회사명과 표지만 복사한 제3자 페이지가 우연한 숫자열로 신원 검증을
    # 통과할 수 있다. HTML 줄바꿈은 extractor가 공백 하나로 정규화한다.
    body = r"[ -]{0,2}".join(re.escape(digit) for digit in number)
    return re.compile(rf"(?<![0-9]){body}(?![0-9])")


def _number_has_registry_marker(text: str, number: str) -> bool:
    markers = (
        _BUSINESS_NUMBER_MARKERS
        if len(number) == _BUSINESS_NUMBER_LENGTH
        else _CORPORATE_NUMBER_MARKERS
    )
    lowered = text.casefold()
    for match in _registration_pattern(number).finditer(text):
        start = max(0, match.start() - _NUMBER_CONTEXT_CHARS)
        end = min(len(text), match.end() + _NUMBER_CONTEXT_CHARS)
        context = lowered[start:end]
        if any(marker in context for marker in markers):
            return True
    return False


def verify_official_company_identity(
    raw_html: str,
    identity: OfficialCompanyIdentity,
) -> OfficialIdentityMatch | None:
    """대상 HTML에서 exact 회사명과 등록번호 표기가 함께 있는지 확인한다.

    회사명만 맞거나 번호만 맞으면 승인하지 않는다. 등록번호는 주변에
    ``사업자등록번호``/``법인등록번호`` 표지가 있어야 한다. 따라서 공식
    페이지가 단순히 링크한 협력사·광고 페이지나 회사 이름을 언급한 기사로
    도메인 경계를 넓힐 수 없다.
    """

    return verify_official_company_identity_pages((raw_html,), identity)


def verify_official_company_identity_pages(
    raw_pages: tuple[str, ...],
    identity: OfficialCompanyIdentity,
) -> OfficialIdentityMatch | None:
    """같은 origin의 닫힌 페이지 묶음에서 이름과 번호를 결속한다.

    작은 회사 홈페이지는 법인명을 첫 화면에, 사업자번호를 개인정보처리방침
    footer에 따로 두기도 한다. 페이지별 HTML을 따로 파싱한 뒤 «회사명은 어느
    한 페이지», «표지와 번호는 어느 한 페이지»에서 각각 확인한다. 원문을
    단순 연결하지 않으므로 한 페이지 끝의 ``사업자등록번호``와 다른 페이지
    첫 숫자가 우연히 이어져 번호가 되는 일은 없다. 호출자는 같은 origin·
    robots·페이지/바이트 상한을 먼저 검증해야 한다.
    """

    if not identity.can_verify_cross_domain or not isinstance(raw_pages, tuple):
        return None
    texts: list[str] = []
    for raw_html in raw_pages:
        if not isinstance(raw_html, str):
            return None
        parser = _IdentityTextExtractor()
        try:
            parser.feed(raw_html)
        except (TypeError, ValueError):
            return None
        texts.append(unicodedata.normalize("NFKC", parser.text))
    if not texts:
        return None

    matched_name = ""
    for text in texts:
        page_tokens = _name_token_rows(text)
        matched_name = next(
            (
                name
                for name in (identity.legal_name, *identity.aliases)
                if _contains_name_tokens(page_tokens, name)
            ),
            "",
        )
        if matched_name:
            break
    if not matched_name:
        return None

    matched_number = next(
        (
            number
            for number in identity.registration_numbers
            if any(_number_has_registry_marker(text, number) for text in texts)
        ),
        "",
    )
    if not matched_number:
        return None

    name_key = exact_company_name_key(matched_name)
    material = "\0".join(
        (name_key, matched_number, "\0\0".join(texts))
    ).encode("utf-8")
    return OfficialIdentityMatch(
        evidence_sha256=hashlib.sha256(material).hexdigest(),
        matched_name_sha256=hashlib.sha256(name_key.encode("utf-8")).hexdigest(),
        registration_number_sha256=hashlib.sha256(
            matched_number.encode("ascii")
        ).hexdigest(),
    )

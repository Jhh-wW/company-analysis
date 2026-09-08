"""공식 이름 전체를 보존하는 표기 정규화. 짧은 접두나 추측 별칭은 만들지 않는다."""

from __future__ import annotations

import itertools
import re
import unicodedata

from src.features.news_intake import constants as c
from src.features.news_intake.models import NewsCompanyContext


def _clean_name(raw: str) -> str:
    clean = unicodedata.normalize("NFKC", raw)
    for designator in c.CORPORATE_DESIGNATORS:
        clean = clean.replace(unicodedata.normalize("NFKC", designator), "")
    clean = re.sub(r'[\s"\r\n]+', " ", clean).strip()
    return re.sub(c.ENGLISH_CORPORATE_SUFFIX_PATTERN, "", clean, flags=re.I).strip()


def _name_key(name: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", name).casefold() if char.isalnum())


def _official_names(company: NewsCompanyContext) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for raw in (company.company_name, *company.aliases):
        clean = _clean_name(raw)
        key = _name_key(clean)
        if key and key not in seen:
            names.append(clean)
            seen.add(key)
    return tuple(names)


def derived_company_names(company: NewsCompanyContext) -> tuple[str, ...]:
    """두 공식 표기의 같은 알파벳 독음을 치환하고 상호 나머지는 그대로 둔다.

    예를 들어 공식 한글 상호가 '에이비씨정밀'이고 공식 영문 이름에 독립
    약자 'ABC'가 있을 때 'ABC정밀'만 파생한다. 'ABC'나 '정밀'은 만들지 않는다.
    기사·사용자 입력·사업 문맥은 이 증명의 입력이 아니다.
    """
    names = _official_names(company)
    acronyms: set[str] = set()
    for name in names:
        match = re.match(
            rf"([A-Z]{{{c.NAME_ACRONYM_MIN_CHARS},{c.NAME_ACRONYM_MAX_CHARS}}})(?=$|[\s.,])",
            name,
        )
        if match:
            acronyms.add(match.group(1))
    derived: list[str] = []
    seen = {_name_key(name) for name in names}
    for acronym in sorted(acronyms):
        readings = itertools.islice(
            itertools.product(*(c.LATIN_LETTER_KOREAN_READINGS[letter] for letter in acronym)),
            c.NAME_READING_VARIANT_BUDGET,
        )
        for letters in readings:
            spoken = "".join(letters)
            for name in names:
                compact = re.sub(r"\s+", "", name)
                if not compact.startswith(spoken):
                    continue
                suffix = compact[len(spoken):]
                if len(_name_key(suffix)) < c.NAME_RETAINED_SUFFIX_MIN_CHARS:
                    continue
                variant = acronym + suffix
                key = _name_key(variant)
                if key and key not in seen:
                    derived.append(variant)
                    seen.add(key)
                    if len(derived) >= c.NAME_DERIVED_VARIANT_BUDGET:
                        return tuple(derived)
    return tuple(derived)


def company_query_names(company: NewsCompanyContext) -> tuple[str, ...]:
    """공식 상호를 우선하고 검증된 혼합 표기를 별칭 검색 예산 안에 포함한다."""
    names = _official_names(company)
    if not names:
        return ()
    return names[:1] + derived_company_names(company) + names[1:]


def mentions_target(text: str, company: NewsCompanyContext) -> bool:
    """검증한 전체 이름의 경계를 비교해 이름 일부가 같은 타법인을 거절한다."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for designator in c.CORPORATE_DESIGNATORS:
        normalized = normalized.replace(unicodedata.normalize("NFKC", designator).casefold(), " ")
    boundary = r"[\W_]"
    for raw_name in company_query_names(company):
        key = _name_key(raw_name)
        if not key:
            continue
        pattern = r"(?<![^\W_])" + c.NAME_SEPARATOR_PATTERN.join(re.escape(char) for char in key)
        pattern += rf"(?=$|{boundary}|{c.NAME_PARTICLE_PATTERN}(?=$|{boundary}))"
        if re.search(pattern, normalized):
            return True
    return False

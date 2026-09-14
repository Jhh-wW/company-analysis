"""Offline DART company identity index and deterministic candidate retrieval.

``corp_code`` is the identity. Names and the remaining CORPCODE fields are
search aliases or ranking evidence only; no match produced here confirms a
company without the existing human confirmation step.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Iterable, Mapping

from src.shared.company_identity import (
    DART_CORP_CODE_RE as _CORP_CODE_RE,
    ENGLISH_CORPORATE_TOKENS as _ENGLISH_CORPORATE_TOKENS,
    KOREAN_CORPORATE_MARKERS as _CORPORATE_MARKERS,
    KOREAN_CORPORATE_TOKENS as _KOREAN_CORPORATE_TOKENS,
    STOCK_CODE_RE as _STOCK_CODE_RE,
    SUPPORTED_NAME_PUNCTUATION as _SHORT_QUERY_ALLOWED_PUNCTUATION,
    exact_company_name_key as _exact_company_name_key,
    latin_acronym_korean as _latin_acronym_korean,
    normalized_latin_acronym as _normalized_latin_acronym,
    official_uppercase_acronyms as _official_uppercase_acronyms,
)


# 한글과 영문/숫자가 공백 없이 붙으면("삼성SDS") 옛 정규식
# [0-9a-zA-Z가-힣]+ 은 둘을 토큰 1개로 묶어버려, "삼성"과 "SDS"가 각각
# 걸려야 할 약어·부분일치 경로를 전부 놓쳤다("삼성 SDS"는 걸리고
# "삼성SDS"는 빈 결과인 비대칭이 실측으로 확인됨). 한글 런과 영문/숫자
# 런을 별도 대안으로 두면 re.findall 이 스크립트 경계에서 토큰을 자연히
# 나눈다. 영문과 숫자는 계속 한 토큰으로 묶인다("이마트24"는 그대로
# 유지) — 이번에 고치는 건 한글↔영문 경계뿐이다.
_TOKEN_RE = re.compile(r"[가-힣]+|[0-9a-zA-Z]+")
_MODIFY_DATE_RE = re.compile(r"[0-9]{8}")

# DART 정식 상호는 "회사이름 + 공백 + 업종형 접미사"로 등록된 경우가 많아
# 색인용 별칭 토큰은 대개 이미 분리돼 있다. 그런데 사용자가 입력을 붙여
# 쓰면(예: "제이와이피엔터테인먼트") `_TOKEN_RE`가 공백 없는 한글 전체를
# 토큰 1개로 묶어버려서, 별칭 쪽엔 있는 "제이와이피"/"엔터테인먼트" 개별
# 토큰과 전혀 만나지 못하고 exact/derived/token/trigram 전 단계가 빗나간다
# (trigram은 길이차 가드에 걸려 조용히 기권한다). 여기서만 좁게 쪼갠다 —
# 임의의 단어를 다 쪼개면 무관한 회사가 토큰 경로로 섞여 들어올 수 있어서,
# 잘 알려진 업종형 접미사 목록으로만 한정한다.
_GLUED_COMPANY_TYPE_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        {"엔터테인먼트", "엔터", "홀딩스", "인터내셔널", "테크놀로지"},
        key=len,
        reverse=True,
    )
)
# 접미사만 남고 본체가 없는 경우(예: 접미사 자체가 상호인 극단값)는 쪼개지
# 않는다. 최소 2자 이상 남아야 "본체 이름"으로 본다.
_GLUED_SUFFIX_MIN_BASE_LENGTH = 2

# Trigram is an abstaining typo block, not a general fuzzy-name fallback.
TRIGRAM_MIN_CHARS = 6
TRIGRAM_MIN_SIMILARITY = 0.78
TRIGRAM_MAX_LENGTH_RATIO_GAP = 0.25

# 종류별 우선순위. 예전엔 spacing/legal_suffix/acronym_* 다섯 종류가
# 전부 4점으로 묶여 있어서, 동점일 때 "종목코드 유무·최종수정일·이름"
# 같은 약한 기준으로 승부가 갈렸다. 실측(rank_depth.json)에서 "SK 하이닉스"는
# spacing 일치가 진짜 정답인데 같은 4점인 "SK" 약어 일치 143건에 묻혀
# 22위로 밀려 상위 15개에서 잘렸다. 그래서 동점을 없애고 강한 증거부터
# 순서를 매긴다: 식별번호 > 완전일치 > 공백차이 > 법인접미사만 다름 >
# 앞부분만 침(prefix) > 안쪽만 침(substring) > 영문약어 원문일치 >
# 약어 독음 일치 > 원문에 약어가 그대로 있는 교차표기 > 다중토큰 부분집합
# 일치 > 오타 유사도(trigram) 순. token/trigram 은 기존 상대 순서(자기
# 위·아래 종류에 대한 승패) 그대로 유지했다.
MATCH_KIND_PRIORITY: Mapping[str, int] = MappingProxyType(
    {
        "exact_id": 11,
        "exact_name": 10,
        "spacing": 9,
        "legal_suffix": 8,
        "prefix": 7,
        "substring": 6,
        "acronym_token": 5,
        "acronym_reading": 4,
        "acronym_cross_script": 3,
        "token": 2,
        "trigram": 1,
    }
)

# prefix/substring 은 정의상 같은 우선순위 안에 여러 후보가 묶이기 쉽다
# ("삼성화재"가 "삼성화재해상보험"도, 있다면 더 긴 계열사명도 함께 침).
# 이 두 종류에서만 등록명이 짧은(=질의에 더 가까운) 쪽을 앞세운다. 다른
# 종류는 아래 정렬 키에서 이 자리에 항상 0 을 반환해 기존 동점 처리
# 순서를 그대로 유지한다.
LENGTH_TIEBREAK_KINDS: frozenset[str] = frozenset({"prefix", "substring"})

# substring("등록명 «안쪽» 아무 데나 질의가 들어 있다")은 짧은 질의에서 무관한
# 법인을 확신 있게 노출한다. 2026-09-10 로컬 CORPCODE 119,102건 실측:
#   · "토스"(2자) -> 와토스코리아·비스토스·미래오토스 (전부 상장사, 전부 무관)
#   · "노션"(2자) -> 이노션·이노션에스·이노션테크   ("노션"은 DART 미등록)
#   · "리디"(2자) -> 쓰리디넷·쓰리디월드 · "기아"(2자) -> 세기아케마·전기아이피
#   · "컬리"(2자) -> 파라뷰더컬리넌·파머수티컬리서치어소시에이츠코리아
# 같은 실측에서 substring 이 «정답»을 찾아 준 유일한 질의는 "올리브영"(4자)->
# 씨제이올리브영이었고, 3자 이상 질의(네이버·현대차·두나무)의 substring 후보는
# 같은 질의의 prefix 후보에 밀려 화면 상위 3장에 오르지 않았다. 그래서 문턱을
# 3자로 둔다 — 2자 질의의 우연한 겹침만 닫고, 실제로 쓰이던 경로는 건드리지 않는다.
# prefix 는 이 문턱을 쓰지 않는다(질의로 «시작»하는 이름은 우연이 훨씬 적다).
SUBSTRING_MIN_QUERY_CHARS: Final[int] = 3


def _match_kind_family(match_kind: str) -> str:
    """독식 판정용 묶음. prefix/substring 은 같은 헬퍼(_best_partial_alias)
    에서 나오는 한 가족이라 한쪽만 보면 독식 검사를 피해 갈 수 있다."""
    return "partial" if match_kind in LENGTH_TIEBREAK_KINDS else match_kind


@dataclass(frozen=True)
class DartCompanyRecord:
    """The five official identity/search fields from one CORPCODE XML row."""

    corp_code: str
    corp_name: str
    corp_eng_name: str = ""
    stock_code: str = ""
    modify_date: str = ""


@dataclass(frozen=True)
class DartNameAlias:
    corp_code: str
    raw: str
    field: str
    exact_key: str
    derived_exact_key: str
    normalized: str
    tokens: tuple[str, ...]
    trigrams: frozenset[str]


@dataclass(frozen=True)
class DartCompanyIndex:
    records: tuple[DartCompanyRecord, ...]
    aliases_by_code: Mapping[str, tuple[DartNameAlias, ...]]
    by_corp_code: Mapping[str, DartCompanyRecord]
    by_stock_code: Mapping[str, tuple[str, ...]]
    by_exact_name: Mapping[str, tuple[str, ...]]
    by_compact_name: Mapping[str, tuple[str, ...]]
    by_derived_name: Mapping[str, tuple[str, ...]]
    by_token: Mapping[str, tuple[str, ...]]
    by_official_acronym: Mapping[str, tuple[str, ...]]
    by_acronym_reading: Mapping[str, tuple[str, ...]]
    by_trigram: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class DartCompanyMatch:
    record: DartCompanyRecord
    match_kind: str
    similarity: float
    matched_name: str
    matched_field: str


def _split_glued_company_type_suffix(token: str) -> tuple[str, ...]:
    """공백 없이 붙은 업종형 접미사를 본체와 분리한다.

    가장 긴 접미사부터 검사해 접미사끼리 겹치는 경우를 피한다(현재 목록엔
    없지만 향후 추가를 대비). 매칭되는 접미사가 없으면 원래 토큰 그대로
    1개짜리 튜플을 돌려준다.
    """
    for suffix in _GLUED_COMPANY_TYPE_SUFFIXES:
        if token.endswith(suffix):
            base = token[: -len(suffix)]
            if len(base) >= _GLUED_SUFFIX_MIN_BASE_LENGTH:
                return (base, suffix)
    return (token,)


def _name_tokens(value: object, *, drop_english_suffixes: bool) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for marker in _CORPORATE_MARKERS:
        text = text.replace(marker, " ")
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text):
        if (
            not token
            or token in _KOREAN_CORPORATE_TOKENS
            or (drop_english_suffixes and token in _ENGLISH_CORPORATE_TOKENS)
        ):
            continue
        tokens.extend(_split_glued_company_type_suffix(token))
    return tuple(tokens)


def _normalized_parts(value: object) -> tuple[str, tuple[str, ...]]:
    tokens = _name_tokens(value, drop_english_suffixes=True)
    return "".join(tokens), tokens


def derived_company_name_key(value: object) -> str:
    """A lower-evidence key that additionally removes English legal suffixes."""
    return "\x1f".join(_name_tokens(value, drop_english_suffixes=True))


def normalize_company_name(value: object) -> str:
    """NFKC+casefold company-name derivative; the raw value remains untouched."""
    return _normalized_parts(value)[0]


def company_name_tokens(value: object) -> tuple[str, ...]:
    return _normalized_parts(value)[1]


def _has_disallowed_short_latin_mix(value: object) -> bool:
    """Reject short Latin fragments mixed with another lookalike script.

    Name tokenization deliberately supports ASCII Latin and Korean. Without
    this preflight, an input such as ``ҮG`` (Cyrillic U + Latin G) loses the
    unsupported letter and becomes an exact search for the unrelated name
    ``G``. Keep ordinary Korean/English names and common legal-name punctuation
    valid, including NFKC-normalized full-width Latin text.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    ascii_latin_count = sum(
        ("A" <= character <= "Z") or ("a" <= character <= "z")
        for character in text
    )
    if not 1 <= ascii_latin_count <= 5:
        return False
    for character in text:
        if (
            ("A" <= character <= "Z")
            or ("a" <= character <= "z")
            or ("0" <= character <= "9")
            or ("가" <= character <= "힣")
            or ("\u1100" <= character <= "\u11ff")
            or ("\u3130" <= character <= "\u318f")
            or character.isspace()
            or character in _SHORT_QUERY_ALLOWED_PUNCTUATION
        ):
            continue
        return True
    return False


def name_trigrams(normalized: str) -> frozenset[str]:
    if len(normalized) < 3:
        return frozenset()
    padded = f"  {normalized} "
    return frozenset(padded[index : index + 3] for index in range(len(padded) - 2))


def trigram_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return (2.0 * len(left & right)) / (len(left) + len(right))


def parse_dart_company_records(xml_path: str | Path) -> tuple[DartCompanyRecord, ...]:
    """Parse the official five CORPCODE fields without dropping English names."""
    root = ET.parse(xml_path).getroot()
    records: list[DartCompanyRecord] = []
    seen_codes: set[str] = set()
    for node in root.iter("list"):
        corp_code = (node.findtext("corp_code") or "").strip()
        corp_name = (node.findtext("corp_name") or "").strip()
        if (
            _CORP_CODE_RE.fullmatch(corp_code) is None
            or not corp_name
            or corp_code in seen_codes
        ):
            continue
        stock_code = (node.findtext("stock_code") or "").strip()
        modify_date = (node.findtext("modify_date") or "").strip()
        records.append(
            DartCompanyRecord(
                corp_code=corp_code,
                corp_name=corp_name,
                corp_eng_name=(node.findtext("corp_eng_name") or "").strip(),
                stock_code=(
                    stock_code if _STOCK_CODE_RE.fullmatch(stock_code) else ""
                ),
                modify_date=(
                    modify_date if _MODIFY_DATE_RE.fullmatch(modify_date) else ""
                ),
            )
        )
        seen_codes.add(corp_code)
    return tuple(records)


def _freeze_postings(values: dict[str, list[str]]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType(
        {key: tuple(dict.fromkeys(codes)) for key, codes in values.items()}
    )


def build_dart_company_index(records: Iterable[DartCompanyRecord]) -> DartCompanyIndex:
    """Build cached-ready exact/token/trigram postings from raw official records."""
    clean_records: list[DartCompanyRecord] = []
    aliases_by_code: dict[str, tuple[DartNameAlias, ...]] = {}
    by_code: dict[str, DartCompanyRecord] = {}
    stock_postings: dict[str, list[str]] = {}
    exact_postings: dict[str, list[str]] = {}
    compact_postings: dict[str, list[str]] = {}
    derived_postings: dict[str, list[str]] = {}
    token_postings: dict[str, list[str]] = {}
    acronym_postings: dict[str, list[str]] = {}
    acronym_reading_postings: dict[str, list[str]] = {}
    trigram_postings: dict[str, list[str]] = {}

    for record in records:
        if (
            not isinstance(record, DartCompanyRecord)
            or _CORP_CODE_RE.fullmatch(record.corp_code) is None
            or not record.corp_name
            or record.corp_code in by_code
        ):
            continue
        aliases: list[DartNameAlias] = []
        seen_aliases: set[tuple[str, str]] = set()
        for field, raw in (
            ("corp_name", record.corp_name),
            ("corp_eng_name", record.corp_eng_name),
        ):
            normalized, tokens = _normalized_parts(raw)
            exact_key = _exact_company_name_key(raw)
            derived_exact_key = derived_company_name_key(raw)
            if not normalized or (field, exact_key) in seen_aliases:
                continue
            seen_aliases.add((field, exact_key))
            grams = name_trigrams(normalized)
            alias = DartNameAlias(
                corp_code=record.corp_code,
                raw=raw,
                field=field,
                exact_key=exact_key,
                derived_exact_key=derived_exact_key,
                normalized=normalized,
                tokens=tokens,
                trigrams=grams,
            )
            aliases.append(alias)
            exact_postings.setdefault(exact_key, []).append(record.corp_code)
            compact_postings.setdefault(exact_key.replace("\x1f", ""), []).append(record.corp_code)
            if derived_exact_key and derived_exact_key != exact_key:
                derived_postings.setdefault(derived_exact_key, []).append(
                    record.corp_code
                )
            for token in set(tokens):
                token_postings.setdefault(token, []).append(record.corp_code)
            for acronym in _official_uppercase_acronyms(raw):
                acronym_postings.setdefault(acronym.casefold(), []).append(
                    record.corp_code
                )
                acronym_reading_postings.setdefault(
                    normalize_company_name(_latin_acronym_korean(acronym)), []
                ).append(record.corp_code)
            for gram in grams:
                trigram_postings.setdefault(gram, []).append(record.corp_code)

        clean_records.append(record)
        by_code[record.corp_code] = record
        aliases_by_code[record.corp_code] = tuple(aliases)
        if _STOCK_CODE_RE.fullmatch(record.stock_code):
            stock_postings.setdefault(record.stock_code, []).append(record.corp_code)

    return DartCompanyIndex(
        records=tuple(clean_records),
        aliases_by_code=MappingProxyType(aliases_by_code),
        by_corp_code=MappingProxyType(by_code),
        by_stock_code=_freeze_postings(stock_postings),
        by_exact_name=_freeze_postings(exact_postings),
        by_compact_name=_freeze_postings(compact_postings),
        by_derived_name=_freeze_postings(derived_postings),
        by_token=_freeze_postings(token_postings),
        by_official_acronym=_freeze_postings(acronym_postings),
        by_acronym_reading=_freeze_postings(acronym_reading_postings),
        by_trigram=_freeze_postings(trigram_postings),
    )


def _best_alias(
    index: DartCompanyIndex,
    corp_code: str,
    *,
    predicate,
) -> DartNameAlias | None:
    return next(
        (
            alias
            for alias in index.aliases_by_code.get(corp_code, ())
            if predicate(alias)
        ),
        None,
    )


def _prefer_match(
    matches: dict[str, DartCompanyMatch], candidate: DartCompanyMatch
) -> None:
    previous = matches.get(candidate.record.corp_code)
    if previous is None or (
        MATCH_KIND_PRIORITY[candidate.match_kind],
        candidate.similarity,
        candidate.matched_field == "corp_name",
    ) > (
        MATCH_KIND_PRIORITY[previous.match_kind],
        previous.similarity,
        previous.matched_field == "corp_name",
    ):
        matches[candidate.record.corp_code] = candidate


def _add_codes(
    matches: dict[str, DartCompanyMatch],
    index: DartCompanyIndex,
    codes: Iterable[str],
    *,
    kind: str,
    similarity: float,
    alias_predicate,
) -> None:
    for corp_code in codes:
        record = index.by_corp_code.get(corp_code)
        if record is None:
            continue
        alias = _best_alias(index, corp_code, predicate=alias_predicate)
        _prefer_match(
            matches,
            DartCompanyMatch(
                record=record,
                match_kind=kind,
                similarity=similarity,
                matched_name=(alias.raw if alias is not None else record.corp_name),
                matched_field=(alias.field if alias is not None else "corp_code"),
            ),
        )


def _token_alias_match(query_tokens: tuple[str, ...], alias: DartNameAlias) -> bool:
    query_set = set(query_tokens)
    alias_set = set(alias.tokens)
    overlap = query_set & alias_set
    if not overlap:
        return False
    # A candidate may contain extra legal-name tokens, but a one-token alias may
    # not discard a qualifier supplied by the user (for example, the stale
    # ``XYZ Corporation`` alias must not consume ``XYZ Entertainment``).
    if not query_set <= alias_set:
        return False
    if len(query_set) == 1:
        token = next(iter(overlap))
        # 한글 토큰은 2음절이면 이미 노이즈가 적다(자모가 조합돼 음절당
        # 정보량이 크다). 라틴 문자는 2~3자가 흔한 약어라 기존처럼 4자
        # 문턱을 유지해 무관한 단어와의 우연한 일치를 막는다.
        if re.search(r"[가-힣]", token):
            return len(token) >= 2
        return len(token) >= 4
    return True


def _best_partial_alias(
    aliases: Iterable[DartNameAlias], normalized_query: str
) -> tuple[DartNameAlias | None, str]:
    """정규화한 질의가 별칭의 앞부분(prefix)이거나 안쪽(substring)에 있는지 찾는다.

    "당근"→"당근마켓"처럼 법인 접미사를 생략한 입력은 prefix, "올리브영"→
    "씨제이올리브영"처럼 모회사 이름을 생략한 입력은 substring으로 잡는다.
    같은 법인 안에서 두 종류가 동시에 걸리면 prefix를 우선하고(더 이른
    위치의 일치가 더 강한 증거), 후보가 여럿이면 등록명이 더 짧은(=질의에
    더 가까운) 별칭을 고른다.

    substring 은 ``SUBSTRING_MIN_QUERY_CHARS`` 미만의 짧은 질의에서는 아예
    열지 않는다(아래 상수의 실측 근거 참조).
    """
    substring_allowed = len(normalized_query) >= SUBSTRING_MIN_QUERY_CHARS
    best_prefix: DartNameAlias | None = None
    best_substring: DartNameAlias | None = None
    for alias in aliases:
        candidate = alias.normalized
        if len(candidate) <= len(normalized_query):
            continue
        if candidate.startswith(normalized_query):
            if best_prefix is None or len(candidate) < len(best_prefix.normalized):
                best_prefix = alias
        elif substring_allowed and normalized_query in candidate:
            if best_substring is None or len(candidate) < len(best_substring.normalized):
                best_substring = alias
    if best_prefix is not None:
        return best_prefix, "prefix"
    if best_substring is not None:
        return best_substring, "substring"
    return None, ""


def _match_sort_key(
    item: DartCompanyMatch,
) -> tuple[int, float, bool, int, int, str, str]:
    return (
        -MATCH_KIND_PRIORITY[item.match_kind],
        -item.similarity,
        not bool(_STOCK_CODE_RE.fullmatch(item.record.stock_code)),
        -(
            int(item.record.modify_date)
            if _MODIFY_DATE_RE.fullmatch(item.record.modify_date)
            else 0
        ),
        (
            len(item.record.corp_name)
            if item.match_kind in LENGTH_TIEBREAK_KINDS
            else 0
        ),
        item.record.corp_name,
        item.record.corp_code,
    )


def generate_dart_company_matches(
    index: DartCompanyIndex, query: object, *, limit: int = 15
) -> tuple[DartCompanyMatch, ...]:
    """Union deterministic blocks and rank them; an empty result means abstain."""
    raw_query = unicodedata.normalize("NFKC", str(query or "")).strip()
    if _has_disallowed_short_latin_mix(raw_query):
        return ()
    normalized_query, query_tokens = _normalized_parts(raw_query)
    exact_query = _exact_company_name_key(raw_query)
    derived_query = derived_company_name_key(raw_query)
    if not normalized_query:
        return ()
    # legal_suffix 게이트에도 재사용한다(§아래 legal_suffix 블록 참고).
    acronym = _normalized_latin_acronym(raw_query)

    matches: dict[str, DartCompanyMatch] = {}

    # Stable official identifiers are accepted as search inputs, but still lead
    # to a human confirmation card rather than automatic confirmation.
    identifier_codes: tuple[str, ...] = ()
    if _CORP_CODE_RE.fullmatch(raw_query):
        identifier_codes = (raw_query,) if raw_query in index.by_corp_code else ()
    elif _STOCK_CODE_RE.fullmatch(raw_query):
        identifier_codes = index.by_stock_code.get(raw_query, ())
    _add_codes(
        matches,
        index,
        identifier_codes,
        kind="exact_id",
        similarity=1.0,
        alias_predicate=lambda _alias: False,
    )

    exact_codes = index.by_exact_name.get(exact_query, ())
    _add_codes(
        matches,
        index,
        exact_codes,
        kind="exact_name",
        similarity=1.0,
        alias_predicate=lambda alias: alias.exact_key == exact_query,
    )

    # 검색에서만 공백 차이를 흡수한다. 법인 확정용 공통 이름 비교는 넓히지 않는다.
    # 짧은 영문 조각을 합쳐 다른 약어를 만들지는 않는다.
    compact_query = exact_query.replace("\x1f", "")
    if re.search(r"[가-힣]", raw_query) or len(compact_query) >= TRIGRAM_MIN_CHARS:
        _add_codes(
            matches, index, index.by_compact_name.get(compact_query, ()),
            kind="spacing", similarity=1.0,
            alias_predicate=lambda alias: alias.exact_key.replace("\x1f", "") == compact_query,
        )

    # 질의 전체가 순수 라틴 약어(공백 없음)이면 derived_query 는 언제나
    # 토큰 1개뿐이라, by_derived_name 에서 걸리는 후보도 전부 "영문명이
    # 약어+일반 법인 단어(Corporation/Inc 등)뿐이라 접미사를 떼면 약어
    # 하나만 남는" 경우로 한정된다("YG Corporation"→"yg", "JYP
    # Corporation"→"jyp"). 이건 "LG CNS"→"LG씨엔에스"처럼 정식명 전체가
    # 접미사 하나 차이인 진짜 legal_suffix 증거가 아니라 acronym_token과
    # 세기가 같은 증거인데, 실측(dart_yg_full_catalog_slice.json)에서
    # "YG Corporation"(서류상 법인, 종목코드 없음)이 이 자리 때문에
    # legal_suffix 최상위로 올라가 실제 상장된 와이지엔터테인먼트(종목
    # 122870)보다 앞서는 역전이 나왔다. 아래 acronym_token/acronym_reading
    # 블록이 by_token 색인(접미사 이미 제거됨)으로 이 후보들을 그대로
    # 다시 찾아내므로, 여기서 빼도 사라지지 않고 정당한 tier로 내려갈
    # 뿐이다. 공백이 있는 다중 토큰 질의("LG CNS")는 acronym 이 빈
    # 문자열이라 이 게이트에 걸리지 않는다.
    derived_codes = index.by_derived_name.get(derived_query, ()) if not acronym else ()
    _add_codes(
        matches,
        index,
        derived_codes,
        kind="legal_suffix",
        similarity=1.0,
        alias_predicate=lambda alias: alias.derived_exact_key == derived_query,
    )

    # 정식명의 앞부분만 치거나("당근"→당근마켓, 법인 접미사 생략) 뒷부분만
    # 쳐서 모회사 이름을 생략한 경우("올리브영"→씨제이올리브영)를 찾는다.
    # 순수 영문 질의는 위 약어 경로가 이미 맡고 있어 빼고(한글이 전혀
    # 없으면 이 블록을 열지 않는다), 1음절 질의는 무관한 후보를 너무 많이
    # 끌어들여 뺀다.
    if re.search(r"[가-힣]", raw_query) and len(normalized_query) >= 2:
        for corp_code, aliases in index.aliases_by_code.items():
            alias, kind = _best_partial_alias(aliases, normalized_query)
            if alias is None:
                continue
            record = index.by_corp_code.get(corp_code)
            if record is None:
                continue
            _prefer_match(
                matches,
                DartCompanyMatch(record, kind, 1.0, alias.raw, alias.field),
            )

    if acronym:
        acronym_token = acronym.casefold()
        token_codes = index.by_token.get(acronym_token, ())
        _add_codes(
            matches,
            index,
            token_codes,
            kind="acronym_token",
            similarity=1.0,
            alias_predicate=lambda alias: acronym_token in alias.tokens,
        )
        expanded = _exact_company_name_key(_latin_acronym_korean(acronym))
        reading_codes = index.by_exact_name.get(expanded, ())
        _add_codes(
            matches,
            index,
            reading_codes,
            kind="acronym_reading",
            similarity=1.0,
            alias_predicate=lambda alias: alias.exact_key == expanded,
        )

    # Reverse cross-script aliases come only from acronym tokens literally
    # present in an official raw DART name.  This lets a qualified Korean/mixed
    # query retrieve the same legal entities without inventing transliterations.
    # A pure spaced Latin input such as ``JY P`` never enters this block.
    query_has_korean = any(re.search(r"[가-힣]", token) for token in query_tokens)
    if not acronym:
        for token in dict.fromkeys(query_tokens):
            token_acronym = (
                _normalized_latin_acronym(token) if query_has_korean else ""
            )
            if token_acronym:
                acronym_key = token_acronym.casefold()
                _add_codes(
                    matches,
                    index,
                    index.by_official_acronym.get(acronym_key, ()),
                    kind="acronym_cross_script",
                    similarity=1.0,
                    alias_predicate=lambda alias, key=token_acronym: key
                    in _official_uppercase_acronyms(alias.raw),
                )
            reading_key = normalize_company_name(token)
            reading_codes = index.by_acronym_reading.get(reading_key, ())
            if reading_codes:
                _add_codes(
                    matches,
                    index,
                    reading_codes,
                    kind="acronym_cross_script",
                    similarity=1.0,
                    alias_predicate=lambda alias, key=reading_key: any(
                        normalize_company_name(_latin_acronym_korean(item)) == key
                        for item in _official_uppercase_acronyms(alias.raw)
                    ),
                )

    token_code_sets = [
        set(index.by_token.get(token, ()))
        for token in set(query_tokens)
        if len(token) >= 2
    ]
    token_codes = set().union(*token_code_sets) if token_code_sets else set()
    for corp_code in token_codes:
        alias = _best_alias(
            index,
            corp_code,
            predicate=lambda item: _token_alias_match(query_tokens, item),
        )
        record = index.by_corp_code.get(corp_code)
        if alias is None or record is None:
            continue
        overlap = len(set(query_tokens) & set(alias.tokens))
        similarity = (2.0 * overlap) / (len(set(query_tokens)) + len(set(alias.tokens)))
        _prefer_match(
            matches,
            DartCompanyMatch(record, "token", similarity, alias.raw, alias.field),
        )

    # Short names and acronym-like inputs never enter typo similarity. This is
    # the main abstention guard against SM -> Smart Media style false positives.
    if len(normalized_query) >= TRIGRAM_MIN_CHARS and not acronym:
        query_grams = name_trigrams(normalized_query)
        trigram_codes: set[str] = set()
        for gram in query_grams:
            trigram_codes.update(index.by_trigram.get(gram, ()))
        typo_matches: list[DartCompanyMatch] = []
        for corp_code in trigram_codes:
            record = index.by_corp_code.get(corp_code)
            if record is None:
                continue
            best: tuple[float, DartNameAlias] | None = None
            for alias in index.aliases_by_code.get(corp_code, ()):
                longest = max(len(normalized_query), len(alias.normalized))
                length_gap = abs(len(normalized_query) - len(alias.normalized))
                if (
                    not longest
                    or length_gap / longest > TRIGRAM_MAX_LENGTH_RATIO_GAP
                ):
                    continue
                similarity = trigram_similarity(query_grams, alias.trigrams)
                if similarity < TRIGRAM_MIN_SIMILARITY:
                    continue
                if best is None or similarity > best[0]:
                    best = (similarity, alias)
            if best is not None:
                typo_matches.append(
                    DartCompanyMatch(
                        record,
                        "trigram",
                        best[0],
                        best[1].raw,
                        best[1].field,
                    )
                )
        typo_matches.sort(
            key=lambda item: (-item.similarity, item.record.corp_code)
        )
        for match in typo_matches[: max(10, max(1, int(limit)) * 3)]:
            _prefer_match(matches, match)

    ranked = sorted(matches.values(), key=_match_sort_key)
    limit_n = max(1, int(limit))
    top = list(ranked[:limit_n])

    # 짧은 한글 조각 하나가 흔한 접두(2음절 "엔씨"가 "엔씨아이디에스"류
    # 15건과 우연히 겹침)로 limit 을 통째로 독식하면, 실제로는 더 정확할
    # 수 있는 다른 종류의 후보(같은 "엔씨"의 약어 독음으로 찾는 "NC")가
    # 통째로 밀려 사라진다(실측: alias-recall-probe 재측정에서 "엔씨"가
    # top3->miss 로 퇴행). 순위 자체(더 나은 증거가 항상 앞선다는 원칙)는
    # 그대로 두고, **limit 안이 단일 종류로만 채워졌을 때만** 맨 뒷자리
    # 하나를 다른 종류의 최상위 후보로 구제한다. 여러 종류가 이미 섞여
    # 있으면(정상 케이스, 예: "SK 하이닉스" 수정 후 spacing 1건이 최상위에
    # 오고 나머지가 acronym_cross_script인 경우) 아무것도 바꾸지 않는다 —
    # 그런 경우는 이미 원하는 종류가 1위이므로 구제가 필요 없다.
    if len(ranked) > limit_n and top:
        monopoly_family = _match_kind_family(top[0].match_kind)
        is_monopoly = all(
            _match_kind_family(item.match_kind) == monopoly_family for item in top
        )
        if is_monopoly:
            # 독식 가족이 limit 을 다 채우면 다른 가족 후보가 통째로 사라진다
            # ("엔씨" 실측 회귀: prefix 15건이 acronym_cross_script "NC"를
            # 밀어냄 — 원래는 상위 3위였다). 상위 20%(최소 1자리)만 다른
            # 가족의 최상위 후보들로 구제한다. 순위 원칙(강한 증거가 앞선다)
            # 은 그대로 두고 "아예 안 보이는" 사고만 막는다 — 여러 가족이
            # 이미 섞여 있으면(정상 케이스, 예: 수정 후 "SK 하이닉스"는
            # spacing 1위 + acronym_cross_script 나머지) is_monopoly 가
            # False 라 이 블록은 아무것도 바꾸지 않는다.
            reserve_n = max(1, limit_n // 5)
            rescues = [
                item
                for item in ranked[limit_n:]
                if _match_kind_family(item.match_kind) != monopoly_family
            ][:reserve_n]
            if rescues:
                top[len(top) - len(rescues) :] = rescues
                top.sort(key=_match_sort_key)

    return tuple(top)

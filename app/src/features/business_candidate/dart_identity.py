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
from typing import Iterable, Mapping


_TOKEN_RE = re.compile(r"[0-9a-zA-Z가-힣]+")
_CORP_CODE_RE = re.compile(r"[0-9]{8}")
_STOCK_CODE_RE = re.compile(r"[0-9]{6}")
_MODIFY_DATE_RE = re.compile(r"[0-9]{8}")
_LATIN_ACRONYM_RE = re.compile(r"[A-Za-z]{2,5}")
_DOTTED_LATIN_ACRONYM_RE = re.compile(
    r"[A-Za-z](?:\.[A-Za-z]){1,4}\.?"
)
_OFFICIAL_UPPER_ACRONYM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z](?:\.[A-Z]){1,4}\.?|[A-Z]{2,5})(?![A-Za-z0-9])"
)
_SHORT_QUERY_ALLOWED_PUNCTUATION = frozenset(
    ".,&'\"’·()[]{}-_/+"
)
_CORPORATE_MARKERS = (
    "(주)",
    "(유)",
    "(사)",
    "(재)",
    "㈜",
)
_KOREAN_CORPORATE_TOKENS = frozenset(
    {
        "주식회사",
        "유한회사",
        "유한책임회사",
        "합자회사",
        "합명회사",
    }
)
_ENGLISH_CORPORATE_TOKENS = frozenset(
    {
        "corporation",
        "corp",
        "incorporated",
        "inc",
        "company",
        "co",
        "limited",
        "ltd",
        "llc",
    }
)
_LATIN_LETTER_NAMES = {
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

# Trigram is an abstaining typo block, not a general fuzzy-name fallback.
TRIGRAM_MIN_CHARS = 6
TRIGRAM_MIN_SIMILARITY = 0.78
TRIGRAM_MAX_LENGTH_RATIO_GAP = 0.25

MATCH_KIND_PRIORITY: Mapping[str, int] = MappingProxyType(
    {
        "exact_id": 6,
        "exact_name": 5,
        "legal_suffix": 4,
        "acronym_token": 4,
        "acronym_reading": 4,
        "acronym_cross_script": 4,
        "token": 3,
        "trigram": 2,
    }
)


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


def _name_tokens(value: object, *, drop_english_suffixes: bool) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for marker in _CORPORATE_MARKERS:
        text = text.replace(marker, " ")
    return tuple(
        token
        for token in _TOKEN_RE.findall(text)
        if token
        and token not in _KOREAN_CORPORATE_TOKENS
        and (not drop_english_suffixes or token not in _ENGLISH_CORPORATE_TOKENS)
    )


def _normalized_parts(value: object) -> tuple[str, tuple[str, ...]]:
    tokens = _name_tokens(value, drop_english_suffixes=True)
    return "".join(tokens), tokens


def exact_company_name_key(value: object) -> str:
    """Preserve word boundaries while ignoring only legal wrappers/punctuation."""
    return "\x1f".join(_name_tokens(value, drop_english_suffixes=False))


def exact_company_names_equivalent(left: object, right: object) -> bool:
    """Compare two official-name spellings without fuzzy or alias inference.

    DART and a confirmation card can place Korean legal wrappers differently
    (for example ``삼성전자`` and ``삼성전자(주)``).  The existing exact key is
    the narrow contract for that comparison: it removes Korean legal wrappers
    and punctuation while preserving token boundaries and English words.

    Tokenization must never silently discard an unsupported lookalike script.
    Validate the complete NFKC text first so ``ΑG``, ``JҮP`` or
    ``삼성전자Α`` cannot collapse to an unrelated supported-script key.
    """

    def supported(value: object) -> bool:
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
                or character in _SHORT_QUERY_ALLOWED_PUNCTUATION
            ):
                continue
            return False
        return True

    if not supported(left) or not supported(right):
        return False
    left_key = exact_company_name_key(left)
    right_key = exact_company_name_key(right)
    return bool(left_key and right_key and left_key == right_key)


def derived_company_name_key(value: object) -> str:
    """A lower-evidence key that additionally removes English legal suffixes."""
    return "\x1f".join(_name_tokens(value, drop_english_suffixes=True))


def normalize_company_name(value: object) -> str:
    """NFKC+casefold company-name derivative; the raw value remains untouched."""
    return _normalized_parts(value)[0]


def company_name_tokens(value: object) -> tuple[str, ...]:
    return _normalized_parts(value)[1]


def normalized_latin_acronym(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    if _LATIN_ACRONYM_RE.fullmatch(normalized):
        return normalized.upper()
    if _DOTTED_LATIN_ACRONYM_RE.fullmatch(normalized):
        return normalized.replace(".", "").upper()
    return ""


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


def latin_acronym_korean(value: object) -> str:
    acronym = normalized_latin_acronym(value)
    if not acronym:
        return ""
    return "".join(_LATIN_LETTER_NAMES[letter] for letter in acronym)


def _official_uppercase_acronyms(value: object) -> tuple[str, ...]:
    """Return only acronym tokens literally present in an official raw name.

    Casefolded aliases cannot prove that a short token was an acronym.  Keep this
    narrow: an independent all-uppercase 2--5 letter (optionally dotted) token,
    excluding ordinary English corporate suffixes.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    found: list[str] = []
    for match in _OFFICIAL_UPPER_ACRONYM_TOKEN_RE.finditer(text):
        acronym = normalized_latin_acronym(match.group(0))
        if (
            acronym
            and acronym.casefold() not in _ENGLISH_CORPORATE_TOKENS
            and acronym not in found
        ):
            found.append(acronym)
    return tuple(found)


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
            exact_key = exact_company_name_key(raw)
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
                    normalize_company_name(latin_acronym_korean(acronym)), []
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
    # ``JYP Corporation`` alias must not consume ``JYP Entertainment``).
    if not query_set <= alias_set:
        return False
    if len(query_set) == 1:
        token = next(iter(overlap))
        return len(token) >= 4
    return True


def generate_dart_company_matches(
    index: DartCompanyIndex, query: object, *, limit: int = 15
) -> tuple[DartCompanyMatch, ...]:
    """Union deterministic blocks and rank them; an empty result means abstain."""
    raw_query = unicodedata.normalize("NFKC", str(query or "")).strip()
    if _has_disallowed_short_latin_mix(raw_query):
        return ()
    normalized_query, query_tokens = _normalized_parts(raw_query)
    exact_query = exact_company_name_key(raw_query)
    derived_query = derived_company_name_key(raw_query)
    if not normalized_query:
        return ()

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

    derived_codes = index.by_derived_name.get(derived_query, ())
    _add_codes(
        matches,
        index,
        derived_codes,
        kind="legal_suffix",
        similarity=1.0,
        alias_predicate=lambda alias: alias.derived_exact_key == derived_query,
    )

    acronym = normalized_latin_acronym(raw_query)
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
        expanded = exact_company_name_key(latin_acronym_korean(acronym))
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
            token_acronym = normalized_latin_acronym(token) if query_has_korean else ""
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
                        normalize_company_name(latin_acronym_korean(item)) == key
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

    ranked = sorted(
        matches.values(),
        key=lambda item: (
            -MATCH_KIND_PRIORITY[item.match_kind],
            -item.similarity,
            not bool(_STOCK_CODE_RE.fullmatch(item.record.stock_code)),
            -(
                int(item.record.modify_date)
                if _MODIFY_DATE_RE.fullmatch(item.record.modify_date)
                else 0
            ),
            item.record.corp_name,
            item.record.corp_code,
        ),
    )
    return tuple(ranked[: max(1, int(limit))])

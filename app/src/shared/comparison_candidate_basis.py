"""경쟁사 후보 근거 v1의 feature 중립 순수 계약.

9장 조립기와 출고 게이트가 서로 직접 import하지 않고도 같은 닫힌 JSON,
법인 별칭 경계, 경쟁 표지, 문장 SHA-256을 재계산하도록 한다. 원문 문장 자체는
v1에 복제하지 않으며 ``candidate_fact_id``가 가리키는 1~8장 사실에서 읽는다.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse
from datetime import date
from typing import Iterable, Mapping


COMPARISON_BASIS_VERSION = "company-comparison-basis-v1"
COMPARISON_SOURCE_BASIS_VERSION = "company-comparison-source-basis-v2"
COMPARISON_OVERLAP_DIMENSIONS = frozenset(
    {"고객 겹침", "제품·서비스 겹침", "시장 겹침"}
)
COMPARISON_SOURCE_OVERLAP_DIMENSIONS = frozenset(
    {*COMPARISON_OVERLAP_DIMENSIONS, "경쟁 관계 명시"}
)

_COMPETITION_MARKERS = (
    "경쟁사",
    "경쟁업체",
    "경쟁 관계",
    "경쟁관계",
    "동종업체",
    "동종 업체",
    "경합",
    "시장점유율",
    "시장 점유율",
)
_SOURCE_COMPETITION_MARKERS = tuple(
    marker
    for marker in _COMPETITION_MARKERS
    if marker not in {"시장점유율", "시장 점유율"}
)
_ENGLISH_CURRENT_COMPETITOR_MODIFIER = (
    r"(?:(?:principal|primary|direct|major)\s+)?"
)
_ENGLISH_SOURCE_COMPETITION = re.compile(
    rf"\b{_ENGLISH_CURRENT_COMPETITOR_MODIFIER}competitors?\b|"
    r"\b(?:compete|competes|competing)\s+(?:directly\s+)?(?:with|against)\b|"
    r"\bin\s+(?:direct\s+)?competition\s+with\b",
    re.IGNORECASE,
)
_ENGLISH_COMPETE_RELATION = re.compile(
    r"\b(?:"
    r"(?:compete|competes|competing)\s+(?:directly\s+)?(?:with|against)|"
    r"(?:is|are)\s+(?:actively\s+)?competing\s+"
    r"(?:directly\s+)?(?:with|against)|"
    r"(?:is|are)\s+in\s+(?:direct\s+)?competition\s+with"
    r")\s+",
    re.IGNORECASE,
)
_ENGLISH_TARGET_STOP = re.compile(
    r"[.;:!?]|"
    r",\s*(?:while|whereas|although|but|because|whose|which|that)\b|"
    r",\s*(?:an?|the)\s+(?:subsidiary|affiliate|customer|client|supplier|"
    r"vendor|partner|distributor|reseller)\b|"
    r"\s+(?:for|to|using|after|before|through|via|because|when|where|"
    r"which|whose|in|on|at|across|within|during|under|over)\b|"
    r"\s+(?:and|but)\s+(?=[^,.;:!?]{0,80}\b(?:"
    r"supplies|supplied|supply|serves|served|serve|provides|provided|provide|"
    r"sells|sold|sell|licenses|licensed|license|acquires|acquired|acquire|"
    r"uses|used|use|is|are|was|were|has|have|had|does|do|did)\b)",
    re.IGNORECASE,
)
_ENGLISH_CLAUSE_BOUNDARY = re.compile(
    r"[.;:!?]|,\s*(?:while|whereas|although|but|because)\s+",
    re.IGNORECASE,
)
_ENGLISH_SUBJECT_ADVERB = re.compile(
    r"(?:\s+(?:currently|actively|primarily|also|now|directly)){0,3}\s*$",
    re.IGNORECASE,
)
_ENGLISH_NOMINAL_PREDICATE = re.compile(
    r"\b(?:is|are|remains?)\s+(?:one\s+of\s+)?{owner}\s+"
    rf"{_ENGLISH_CURRENT_COMPETITOR_MODIFIER}competitors?\b"
    r"(?=\s*(?:[.;:!?]|$))",
    re.IGNORECASE,
)
_ENGLISH_NOMINAL_LIST = re.compile(
    rf"{{owner}}\s+{_ENGLISH_CURRENT_COMPETITOR_MODIFIER}competitors?\s+"
    r"(?:include|includes|are|remain|remains|consist(?:s)?\s+of|is)\s+",
    re.IGNORECASE,
)
_KOREAN_SOURCE_COMPETITION = re.compile(
    r"(?:와|과)\s*(?:직접\s*)?경쟁(?:한다|합니다|\s*중이다|\s*중입니다)"
)
_KOREAN_TERMINAL_COMPETITION = re.compile(
    r"(?:직접\s*)?경쟁(?:"
    r"한다|합니다|\s*중이다|\s*중입니다|"
    r"\s*관계(?:에)?\s*(?:있다|있습니다|이다|입니다)"
    r")(?=\s*[.!?]?$)"
)
_KOREAN_ATTRIBUTIVE_COMPETITION = re.compile(
    r"(?:직접\s*)?경쟁\s*관계(?:인|에\s+있는)\s+"
    r"(?P<description>[^.;!?]{1,160}?(?:기업|회사|업체|법인))"
    r"(?:이다|입니다|다)(?=\s*[.!?]?$)"
)
_KOREAN_DESCRIPTION_SUBJECT = re.compile(
    r"(?:^|\s)[0-9a-z가-힣·()/-]{1,80}(?:는|은|이|가)\s+"
)
_KOREAN_DESCRIPTION_RELATION = re.compile(
    r"(?:경쟁|공급|납품|인수|계약|거래|협력|제휴|투자|"
    r"공동개발|공동연구|공동사업|체결|지원|고객)"
)
_ENGLISH_COMPETITION_NEGATION = re.compile(
    r"\b(?:not|never|neither|no\s+longer|do\s+not|does\s+not|did\s+not|"
    r"cannot|can['’]t|isn['’]t|aren['’]t)\b.{0,48}"
    r"(?:\bcompetitors?\b|\bcompet(?:e|es|ed|ing)\b|\bcompetition\b)|"
    r"\bno\b.{0,24}\bcompetitors?\b",
    re.IGNORECASE,
)
_ENGLISH_COMPETITION_EXCLUSION = re.compile(
    r"(?:\bcompetitors?\b|\bcompetition\b).{0,64}"
    r"(?:\b(?:do|does|did)\s+not\s+include\b|"
    r"\b(?:exclude|excludes|excluded|excluding|except|excepted|excepting)\b)|"
    r"(?:\bnot\s+included\b|"
    r"\b(?:exclude|excludes|excluded|excluding|except|excepted|excepting)\b)"
    r".{0,64}(?:\bcompetitors?\b|\bcompetition\b)",
    re.IGNORECASE,
)
_ENGLISH_RELATION_EXCLUSION = re.compile(
    r"\b(?:other\s+than|but\s+not|apart\s+from|rather\s+than|"
    r"unlike|instead\s+of)\b",
    re.IGNORECASE,
)
_ENGLISH_NONCURRENT_COMPETITOR = re.compile(
    r"\b(?:former|past|historical|potential|prospective|possible|alleged|"
    r"candidate|likely|purported|putative|suspected|future|would-be)\s+"
    r"(?:[a-z-]+\s+){0,2}competitors?\b|"
    r"\bcompetitors?\s+(?:candidate|prospect|possibility|allegation)\b",
    re.IGNORECASE,
)
_ENGLISH_PAST_COMPETITION = re.compile(
    r"\b(?:was|were|became|had\s+been|has\s+been)\b.{0,64}\bcompetitors?\b|"
    r"\bcompetitors?\b.{0,48}\b(?:included|were)\b|"
    r"\b(?:competed|had\s+competed)\s+(?:directly\s+)?(?:with|against)\b|"
    r"\b(?:used\s+to|would\s+formerly)\s+(?:directly\s+)?compete\s+"
    r"(?:with|against)\b",
    re.IGNORECASE,
)
_KOREAN_PAST_COMPETITION = re.compile(
    r"(?:경쟁사|경쟁업체|동종\s*업체)(?:였|이었|이었다|였습니다|이었습니다)|"
    r"(?:경쟁사|경쟁업체|동종\s*업체).{0,48}(?:였다|였습니다|이었다|이었습니다)|"
    r"경쟁(?:하였다|했습니다|했었다|해왔다|해왔습니다|하였었다)"
)
_COMPETITION_DISJUNCTION = re.compile(r"\b(?:either\s+)?or\b|(?:또는|혹은)", re.IGNORECASE)
_ENGLISH_RESERVED_TARGET_ALIASES = frozenset(
    {
        "i",
        "me",
        "my",
        "mine",
        "we",
        "us",
        "our",
        "ours",
        "you",
        "your",
        "yours",
        "he",
        "him",
        "his",
        "she",
        "her",
        "hers",
        "it",
        "its",
        "they",
        "them",
        "their",
        "theirs",
        "this",
        "that",
        "these",
        "those",
        "in",
        "on",
        "at",
        "of",
        "to",
        "for",
        "with",
        "against",
        "and",
        "or",
        "as",
        "by",
        "from",
        "is",
        "are",
        "be",
    }
)
_KOREAN_RELATION_EXCLUSION = re.compile(r"(?:아니라|대신)")
_KOREAN_COMPETITION_NEGATION = re.compile(
    r"(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합).{0,16}"
    r"(?:아니|없|않)|경쟁.{0,12}(?:하지|않|아니|없)"
)
_KOREAN_COMPETITION_EXCLUSION = re.compile(
    r"(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합).{0,64}"
    r"(?:제외|포함하지|빼고)|"
    r"(?:제외|포함하지|빼고).{0,64}"
    r"(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합)"
)
_THIRD_PARTY_ATTRIBUTION = re.compile(
    r"\b(?:according\s+to|said|says|stated|states|told|claimed|claims|"
    r"quoted|reported|reports|noted|notes|remarked)\b|"
    r"(?:말했다|말했으며|밝혔다|밝혔으며|전했다|전했으며|언급했다|"
    r"설명했다|주장했다|에\s*따르면|라고\s*했다|이라고\s*했다)",
    re.IGNORECASE,
)
_COOPERATIVE_COMPETITION_CONTEXT = re.compile(
    r"\b(?:help|helps|helped|helping|enable|enables|enabled|enabling|"
    r"allow|allows|allowed|allowing|support|supports|supported|supporting|"
    r"assist|assists|assisted|assisting)\b.{0,80}"
    r"\b(?:compete|competes|competed|competing)\b|"
    r"\b(?:together\s+with|alongside|in\s+(?:collaboration|partnership)\s+with|"
    r"collaborat(?:e|es|ed|ing)\s+with|partner(?:s|ed|ing)?\s+with)\b|"
    r"(?:와|과)\s*함께|(?:협력|제휴)(?:하여|해|해서|하며|하고)|공동으로",
    re.IGNORECASE,
)
_KOREAN_EMBEDDED_COMPETITION = re.compile(
    r"경쟁(?:한다고|한다고\s*|하는\s*것을|하는\s*데|하도록).{0,40}"
    r"(?:판단|생각|지원|도움|허용|권고)"
)
_QUOTATION_MARKS = frozenset('"“”‘’「」『』《》〈〉')
_ENGLISH_SELF_COMPETITION = re.compile(
    r"\bwe\s+(?:(?:do|currently|actively|primarily|also|now)\s+){0,3}"
    r"(?:compete\s+(?:directly\s+)?|are\s+(?:actively\s+)?competing\s+)"
    r"(?:with|against)\b|"
    r"\b(?:is|are|remains?)\s+(?:not\s+)?our\s+"
    rf"{_ENGLISH_CURRENT_COMPETITOR_MODIFIER}competitors?\b|"
    rf"\bour\s+{_ENGLISH_CURRENT_COMPETITOR_MODIFIER}competitors?\s+"
    r"(?:include|includes|are|remain|remains|consist)\b",
    re.IGNORECASE,
)
_ENGLISH_SELF_OBJECT_COMPETITION = re.compile(
    r"(?i:\b(?:compete|competes|competing)\s+"
    r"(?:directly\s+)?(?:with|against)\s+)us\b"
)
_SELF_MODIFIES_THIRD_PARTY = re.compile(
    r"\bour\s+(?:customers?|clients?|suppliers?|vendors?|partners?|"
    r"distributors?|resellers?|subsidiar(?:y|ies)|affiliates?|investees?)\b|"
    r"당사(?:의)?\s*(?:고객|고객사|수요처|협력사|공급사|공급업체|파트너|유통사|"
    r"자회사|계열사|투자사|피투자사)",
    re.IGNORECASE,
)
_PRODUCT_MARKERS = ("제품", "서비스", "품목", "브랜드", "기술")
_CUSTOMER_MARKERS = ("고객", "수요처", "납품처", "발주처")
_LEGAL_MARKERS = (
    "주식회사",
    "유한회사",
    "(주)",
    "㈜",
    "corp.",
    "co.,ltd.",
    "co., ltd.",
)
_ALIAS_TRAILING_PARTICLES = (
    "였습니다",
    "입니다",
    "였다",
    "이다",
    "에서",
    "보다",
    "와",
    "과",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "등",
)
_ALIAS_BOUNDARY = r"[0-9a-z가-힣]"
_ALIAS_SEPARATOR = r"[\s,.;:!?()\[\]{}\"'·/]"
_EVIDENCE_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._:-]{0,199}")
_EXPECTED_KEYS = frozenset(
    {
        "version",
        "candidate_fact_id",
        "candidate_source_id",
        "candidate_corp_code",
        "candidate_name",
        "filing_document_id",
        "evidence_sha256",
        "overlap_dimension",
    }
)
_SOURCE_EXPECTED_KEYS = frozenset(
    {
        "version",
        "candidate_source_id",
        "self_corp_code",
        "self_attestation_source_id",
        "self_attestation_evidence",
        "candidate_corp_code",
        "candidate_name",
        "source_kind",
        "source_type",
        "source_publisher",
        "source_host",
        "source_url",
        "source_document_id",
        "source_location",
        "source_date",
        "evidence_text",
        "evidence_sha256",
        "evidence_exact_sha256",
        "overlap_dimension",
    }
)


def _normalized(value: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _case_preserving(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def comparison_evidence_sha256(text: str) -> str:
    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def comparison_evidence_exact_sha256(text: str) -> str:
    """문장 추출 뒤 strip된 원문의 UTF-8 byte-exact SHA-256."""

    raw = str(text or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def comparison_evidence_sentences(text: str) -> tuple[str, ...]:
    return tuple(
        sentence
        for match in _EVIDENCE_SENTENCE.finditer(str(text or ""))
        if (sentence := match.group(0).strip())
    )


def comparison_corporate_core(value: str) -> str:
    core = _normalized(value)
    for marker in _LEGAL_MARKERS:
        core = core.replace(_normalized(marker), " ")
    return re.sub(r"[^0-9a-z가-힣]", "", core)


def comparison_candidate_aliases(name: str) -> tuple[str, ...]:
    raw = _normalized(name)
    stripped = raw
    for marker in _LEGAL_MARKERS:
        stripped = stripped.replace(_normalized(marker), " ")
    stripped = " ".join(stripped.split()).strip(" ,.-")
    return tuple(
        dict.fromkeys(
            alias
            for alias in (raw, stripped)
            if alias and (alias == raw or len(alias) >= 2)
        )
    )


def comparison_alias_is_in_sentence(alias: str, sentence: str) -> bool:
    normalized_alias = _normalized(alias)
    normalized_sentence = _normalized(sentence)
    if not normalized_alias:
        return False
    particle = "|".join(
        re.escape(value)
        for value in sorted(_ALIAS_TRAILING_PARTICLES, key=len, reverse=True)
    )
    boundary = rf"(?:$|{_ALIAS_SEPARATOR}|(?:{particle})(?=$|{_ALIAS_SEPARATOR}))"
    return re.search(
        rf"(?<!{_ALIAS_BOUNDARY}){re.escape(normalized_alias)}(?={boundary})",
        normalized_sentence,
    ) is not None


def _trim_english_target(value: str) -> str:
    match = _ENGLISH_TARGET_STOP.search(value)
    return value[: match.start()].strip(" ,") if match else value.strip(" ,")


def _english_subject_before(value: str, end: int) -> str:
    prefix = value[:end]
    boundaries = tuple(_ENGLISH_CLAUSE_BOUNDARY.finditer(prefix))
    if boundaries:
        prefix = prefix[boundaries[-1].end() :]
    return _ENGLISH_SUBJECT_ADVERB.sub("", prefix).strip(" ,")


def _alias_is_english_list_item(alias: str, value: str) -> bool:
    """영문 target slot의 독립 목록 항목일 때만 별칭을 인정한다."""

    normalized_alias = _normalized(alias)
    normalized_value = _normalized(value)
    if not normalized_alias or not normalized_value:
        return False
    escaped = re.escape(normalized_alias)
    return re.search(
        rf"(?:^|,\s*|\b(?:and|or)\s+|[&/·]\s*)"
        rf"(?:the\s+)?(?<!{_ALIAS_BOUNDARY}){escaped}"
        rf"(?=$|\s*(?:,|\b(?:and|or)\b|[&/·]))",
        normalized_value,
        re.IGNORECASE,
    ) is not None


def _english_list_is_known(
    value: str,
    aliases: Iterable[str],
    *,
    allow_lowercase_us: bool = False,
) -> bool:
    """목록 전체가 catalog 별칭과 닫힌 구분자로만 소진되는지 검사한다."""

    normalized = _normalized(value)
    if re.search(r"\bor\b", normalized):
        return False
    known = sorted(
        {
            normalized_alias
            for alias in aliases
            if (normalized_alias := _normalized(alias))
        },
        key=len,
        reverse=True,
    )
    if allow_lowercase_us and _lowercase_us_is_english_list_item(value):
        known.append("us")
    if not normalized or not known:
        return False
    position = 0
    expect_item = True
    item_count = 0
    length = len(normalized)
    while position < length:
        while position < length and normalized[position].isspace():
            position += 1
        if position >= length:
            break
        if expect_item:
            matched = next(
                (
                    candidate
                    for candidate in known
                    if normalized.startswith(candidate, position)
                    and (
                        position + len(candidate) == length
                        or not normalized[position + len(candidate)].isalnum()
                    )
                ),
                "",
            )
            if not matched:
                return False
            position += len(matched)
            item_count += 1
            expect_item = False
            continue
        comma = re.match(r"\s*(?:,|&|/|·)\s*", normalized[position:])
        if comma:
            position += comma.end()
            connector = re.match(r"(?:and|or)\b\s*", normalized[position:])
            if connector:
                position += connector.end()
            expect_item = True
            continue
        connector = re.match(r"(?:and|or)\b\s*", normalized[position:])
        if connector:
            position += connector.end()
            expect_item = True
            continue
        return False
    return item_count > 0 and not expect_item


def _alias_is_english_subject_item(
    alias: str,
    value: str,
    known_aliases: Iterable[str],
) -> bool:
    return _english_list_is_known(value, known_aliases) and _alias_is_english_list_item(
        alias, value
    )


def _lowercase_us_is_english_list_item(value: str) -> bool:
    """국가 약어 ``US``와 목적격 대명사 ``us``를 원문 대소문자로 구분한다."""

    return re.search(
        r"(?:^|,\s*|\b(?:and|or)\s+|[&/·]\s*)us"
        r"(?=$|\s*(?:,|\b(?:and|or)\b|[&/·]))",
        value,
    ) is not None


def _korean_list_is_known(
    value: str,
    aliases: Iterable[str],
    *,
    require_final_relation_particle: bool,
) -> bool:
    """한국어 후보 목록 전체를 catalog alias와 닫힌 조사로만 소진한다."""

    normalized = _normalized(value).strip(" ,·&/")
    if "또는" in normalized or "혹은" in normalized:
        return False
    known = sorted(
        {
            normalized_alias
            for alias in aliases
            if (normalized_alias := _normalized(alias))
        },
        key=len,
        reverse=True,
    )
    if not normalized or not known:
        return False
    position = 0
    item_count = 0
    ended_with_relation_particle = False
    length = len(normalized)
    while position < length:
        while position < length and normalized[position].isspace():
            position += 1
        matched = next(
            (
                candidate
                for candidate in known
                if normalized.startswith(candidate, position)
                and (
                    position + len(candidate) == length
                    or normalized[position + len(candidate)] in " 와과,·&/"
                )
            ),
            "",
        )
        if not matched:
            return False
        position += len(matched)
        item_count += 1
        while position < length and normalized[position].isspace():
            position += 1
        if position >= length:
            ended_with_relation_particle = False
            break
        if normalized[position] in "와과":
            position += 1
            ended_with_relation_particle = True
            while position < length and normalized[position].isspace():
                position += 1
            if position >= length:
                break
            # 뒤에 catalog alias가 바로 오면 ``베타와 감마와`` 목록이다.
            if any(normalized.startswith(candidate, position) for candidate in known):
                continue
            return False
        separator = re.match(
            r"(?:,|·|&|/)\s*(?:(?:및|또는)\s*)?|(?:및|또는)\s+",
            normalized[position:],
        )
        if not separator:
            return False
        position += separator.end()
        ended_with_relation_particle = False
    return bool(
        item_count
        and (
            ended_with_relation_particle
            if require_final_relation_particle
            else True
        )
    )


def _korean_alias_appears(value: str, aliases: Iterable[str]) -> bool:
    return any(
        comparison_alias_is_in_sentence(alias, value)
        for company in aliases
        for alias in comparison_candidate_aliases(company)
    )


def _korean_generic_description_is_safe(
    value: str,
    aliases: Iterable[str],
) -> bool:
    """관계절 없는 짧은 업종 명사구만 legacy 설명부로 허용한다."""

    normalized = _normalized(value).strip()
    return bool(
        normalized
        and len(normalized) <= 160
        and re.fullmatch(
            r"[0-9a-z가-힣·()&/+\-]+(?:\s+[0-9a-z가-힣·()&/+\-]+){0,19}",
            normalized,
        )
        and re.search(r"(?:기업|회사|업체|법인)$", normalized)
        and not _korean_alias_appears(normalized, aliases)
        and not _KOREAN_DESCRIPTION_SUBJECT.search(normalized)
        and not _KOREAN_DESCRIPTION_RELATION.search(normalized)
    )


def _korean_verbal_target_list(
    value: str,
    aliases: Iterable[str],
) -> str:
    """경쟁 술어 바로 앞의 catalog alias 목록만 허용한다."""

    return (
        value
        if _korean_list_is_known(
            value,
            aliases,
            require_final_relation_particle=True,
        )
        else ""
    )


def _korean_nominal_target_list(
    value: str,
    aliases: Iterable[str],
) -> str:
    """명시 목록과 legacy ``후보와 같은 동종 회사`` 목록 prefix만 허용."""

    stripped = re.sub(
        r"(?:이다|입니다|다)$",
        "",
        _normalized(value),
    ).strip()
    if _korean_list_is_known(
        stripped,
        aliases,
        require_final_relation_particle=False,
    ):
        return stripped
    descriptor = re.fullmatch(
        r"(?P<targets>.+?)(?:와|과)\s+같은\s+(?P<context>[^.;!?]{1,160})",
        stripped,
    )
    if descriptor is None or not _korean_generic_description_is_safe(
        descriptor.group("context"), aliases
    ):
        return ""
    targets = descriptor.group("targets").strip()
    return (
        targets
        if _korean_list_is_known(
            targets,
            aliases,
            require_final_relation_particle=False,
        )
        else ""
    )


def _english_self_owner_patterns(self_company: str) -> tuple[str, ...]:
    patterns = [r"\bour\b"]
    for alias in comparison_candidate_aliases(self_company):
        normalized_alias = _normalized(alias)
        if normalized_alias:
            patterns.append(
                rf"(?<!{_ALIAS_BOUNDARY}){re.escape(normalized_alias)}(?:'s|’s)"
            )
    return tuple(dict.fromkeys(patterns))


def _english_subject_is_self(subject: str, self_company: str) -> bool:
    normalized = _normalized(subject)
    if re.fullmatch(r"we", normalized, re.IGNORECASE):
        return True
    return any(
        _alias_is_english_list_item(alias, normalized)
        and _normalized(alias) == normalized
        for alias in comparison_candidate_aliases(self_company)
    )


def _english_object_is_self(value: str, self_company: str) -> bool:
    if _lowercase_us_is_english_list_item(value):
        return True
    return any(
        _alias_is_english_list_item(alias, value)
        for alias in comparison_candidate_aliases(self_company)
    )


def _korean_self_alias_patterns(self_company: str) -> tuple[str, ...]:
    aliases = ["당사", "자사", *comparison_candidate_aliases(self_company)]
    return tuple(
        dict.fromkeys(
            re.escape(normalized)
            for alias in aliases
            if (normalized := _normalized(alias))
        )
    )


def _korean_verbal_relation_matches(
    *,
    alias: str,
    sentence: str,
    self_company: str,
    known_aliases: Iterable[str],
) -> bool:
    normalized = _normalized(sentence)
    candidate = re.escape(_normalized(alias))
    if not candidate:
        return False
    competition = _KOREAN_TERMINAL_COMPETITION.search(normalized)
    if competition is None:
        competition = _KOREAN_ATTRIBUTIVE_COMPETITION.search(normalized)
        if competition is None or not _korean_generic_description_is_safe(
            competition.group("description"), known_aliases
        ):
            return False
    prefix = normalized[: competition.start()].strip()
    for self_alias in _korean_self_alias_patterns(self_company):
        self_subject = re.fullmatch(
            rf"(?<!{_ALIAS_BOUNDARY}){self_alias}(?:는|은|이|가)\s*"
            rf"(?P<targets>.+)",
            prefix,
        )
        if self_subject is not None:
            targets = _korean_verbal_target_list(
                self_subject.group("targets"), known_aliases
            )
            if targets and re.search(
                rf"(?<!{_ALIAS_BOUNDARY}){candidate}(?:와|과)", targets
            ):
                return True

        # 역방향은 모호한 복수 주어를 열지 않고 단일 후보↔자사만 허용한다.
        if re.fullmatch(
            rf"(?<!{_ALIAS_BOUNDARY}){candidate}(?:는|은|이|가)\s*"
            rf"(?<!{_ALIAS_BOUNDARY}){self_alias}(?:와|과)",
            prefix,
        ):
            return True
    return False


def _korean_nominal_relation_matches(
    *,
    alias: str,
    sentence: str,
    self_company: str,
    known_aliases: Iterable[str],
) -> bool:
    normalized = _normalized(sentence)
    candidate = re.escape(_normalized(alias))
    if not candidate:
        return False
    competitor = r"(?:주요\s*|직접\s*)?(?:경쟁사|경쟁업체|동종\s*업체)"
    for self_alias in _korean_self_alias_patterns(self_company):
        # ``당사의 경쟁사는 베타, 감마``처럼 명시된 목록의 우측만 본다.
        owner_list = re.fullmatch(
            rf"(?<!{_ALIAS_BOUNDARY}){self_alias}의\s*{competitor}"
            rf"(?:는|은|이|가|에는|로는|:)?\s*(?P<targets>[^.;!?]{{1,220}})"
            rf"\s*[.;!?]?",
            normalized,
        )
        if owner_list is not None:
            targets = _korean_nominal_target_list(
                owner_list.group("targets"), known_aliases
            )
            if targets and comparison_alias_is_in_sentence(alias, targets):
                return True

        # ``베타는 당사의 경쟁사``처럼 후보가 self-owned 경쟁사 술어의 주어다.
        candidate_subject = re.fullmatch(
            rf"(?<!{_ALIAS_BOUNDARY}){candidate}(?:는|은|이|가)\s*"
            rf"(?<!{_ALIAS_BOUNDARY}){self_alias}의\s*{competitor}"
            rf"(?:이다|입니다|다)?\s*[.;!?]?",
            normalized,
        )
        if candidate_subject is not None:
            return True
    return False


def comparison_alias_is_competition_target(
    alias: str,
    sentence: str,
    *,
    self_company: str,
    known_company_aliases: Iterable[str],
) -> bool:
    """별칭이 자사 경쟁 술어의 실제 argument/list slot인지 판정한다.

    문장 어딘가에 법인명이 있다는 이유만으로 후보로 승격하지 않는다. 영문은
    compete 양측과 self-owned competitor 목록, 한국어는 자사 주어-``와/과``
    경쟁 구조와 self-owned 경쟁사 목록만 허용한다. 해석이 필요한 문법은
    fail-closed 한다.
    """

    normalized_alias = _normalized(alias)
    if (
        not alias
        or not self_company
        or (
            re.fullmatch(r"[a-z]+", normalized_alias) is not None
            and normalized_alias in _ENGLISH_RESERVED_TARGET_ALIASES
        )
        or not comparison_alias_is_in_sentence(alias, sentence)
    ):
        return False
    normalized = _normalized(sentence)
    if (
        _KOREAN_COMPETITION_NEGATION.search(normalized)
        or _ENGLISH_COMPETITION_NEGATION.search(normalized)
        or _ENGLISH_COMPETITION_EXCLUSION.search(normalized)
        or _ENGLISH_RELATION_EXCLUSION.search(normalized)
        or _ENGLISH_NONCURRENT_COMPETITOR.search(normalized)
        or _ENGLISH_PAST_COMPETITION.search(normalized)
        or _KOREAN_PAST_COMPETITION.search(normalized)
        or _COMPETITION_DISJUNCTION.search(normalized)
        or _KOREAN_RELATION_EXCLUSION.search(normalized)
        or _KOREAN_COMPETITION_EXCLUSION.search(normalized)
        or _THIRD_PARTY_ATTRIBUTION.search(normalized)
        or _COOPERATIVE_COMPETITION_CONTEXT.search(normalized)
        or _SELF_MODIFIES_THIRD_PARTY.search(normalized)
        or _KOREAN_EMBEDDED_COMPETITION.search(normalized)
        or any(mark in sentence for mark in _QUOTATION_MARKS)
    ):
        return False

    case_preserved = _case_preserving(sentence)
    compact_sentence = re.sub(r"[^0-9a-z가-힣]", "", normalized)
    present_known_companies = tuple(
        company
        for company in known_company_aliases
        if (
            (normalized_company := _normalized(company))
            and (
                normalized_company in normalized
                or (
                    len(core := comparison_corporate_core(company)) >= 2
                    and core in compact_sentence
                )
            )
        )
    )
    known_aliases = tuple(
        dict.fromkeys(
            [
                *(
                    expanded
                    for company in present_known_companies
                    for expanded in comparison_candidate_aliases(company)
                ),
                alias,
                *comparison_candidate_aliases(self_company),
            ]
        )
    )
    for relation in _ENGLISH_COMPETE_RELATION.finditer(case_preserved):
        subject = _english_subject_before(case_preserved, relation.start())
        target = _trim_english_target(case_preserved[relation.end() :])
        if (
            _english_subject_is_self(subject, self_company)
            and _english_list_is_known(target, known_aliases)
            and _alias_is_english_list_item(alias, target)
        ) or (
            _alias_is_english_subject_item(alias, subject, known_aliases)
            and _english_list_is_known(
                target,
                known_aliases,
                allow_lowercase_us=True,
            )
            and _english_object_is_self(target, self_company)
        ):
            return True

    for owner in _english_self_owner_patterns(self_company):
        list_pattern = re.compile(
            _ENGLISH_NOMINAL_LIST.pattern.replace("{owner}", owner),
            re.IGNORECASE,
        )
        for match in list_pattern.finditer(case_preserved):
            targets = _trim_english_target(case_preserved[match.end() :])
            if _english_list_is_known(
                targets, known_aliases
            ) and _alias_is_english_list_item(alias, targets):
                return True

        predicate_pattern = re.compile(
            _ENGLISH_NOMINAL_PREDICATE.pattern.replace("{owner}", owner),
            re.IGNORECASE,
        )
        for match in predicate_pattern.finditer(case_preserved):
            if _alias_is_english_subject_item(
                alias,
                _english_subject_before(case_preserved, match.start()),
                known_aliases,
            ):
                return True

    return _korean_verbal_relation_matches(
        alias=alias,
        sentence=sentence,
        self_company=self_company,
        known_aliases=known_aliases,
    ) or _korean_nominal_relation_matches(
        alias=alias,
        sentence=sentence,
        self_company=self_company,
        known_aliases=known_aliases,
    )


def comparison_sentence_has_marker(sentence: str) -> bool:
    normalized = _normalized(sentence)
    return any(_normalized(marker) in normalized for marker in _COMPETITION_MARKERS)


def comparison_source_sentence_has_marker(sentence: str) -> bool:
    """v2 후보는 부정되지 않은 명시적 경쟁 관계만 요구한다.

    영문 ``competitor``/``competes with``는 허용하지만 성질만 나타내는
    ``competitive``와 부정문은 후보 표지로 보지 않는다.
    """

    normalized = _normalized(sentence)
    if (
        _KOREAN_COMPETITION_NEGATION.search(normalized)
        or _ENGLISH_COMPETITION_NEGATION.search(normalized)
        or _ENGLISH_COMPETITION_EXCLUSION.search(normalized)
        or _ENGLISH_RELATION_EXCLUSION.search(normalized)
        or _ENGLISH_NONCURRENT_COMPETITOR.search(normalized)
        or _ENGLISH_PAST_COMPETITION.search(normalized)
        or _KOREAN_PAST_COMPETITION.search(normalized)
        or _COMPETITION_DISJUNCTION.search(normalized)
        or _KOREAN_RELATION_EXCLUSION.search(normalized)
        or _KOREAN_COMPETITION_EXCLUSION.search(normalized)
        or _THIRD_PARTY_ATTRIBUTION.search(normalized)
        or _COOPERATIVE_COMPETITION_CONTEXT.search(normalized)
        or any(mark in sentence for mark in _QUOTATION_MARKS)
    ):
        return False
    return bool(
        any(
            _normalized(marker) in normalized
            for marker in _SOURCE_COMPETITION_MARKERS
        )
        or _ENGLISH_SOURCE_COMPETITION.search(normalized)
        or _KOREAN_SOURCE_COMPETITION.search(normalized)
    )


def comparison_source_candidate_support_terms(
    sentence: str,
    candidate_name: str,
) -> tuple[str, ...]:
    """후보 판별기가 이미 확인한 대상 alias와 경쟁 관계 표현을 보존한다.

    일반 낱말 빈도나 조사 제거로 새 근거어를 추측하지 않는다. 닫힌 후보
    판별기가 실제로 허용한 문장에서, 같은 닫힌 alias/관계 패턴이 확인한 두
    문자열만 비교 맥락 Fact로 운반한다.
    """

    if not comparison_source_sentence_has_marker(sentence):
        return ()
    normalized = _normalized(sentence)
    aliases = tuple(
        alias
        for alias in comparison_candidate_aliases(candidate_name)
        if comparison_alias_is_in_sentence(alias, sentence)
    )
    if not aliases:
        return ()
    relation = next(
        (
            _normalized(marker)
            for marker in sorted(_SOURCE_COMPETITION_MARKERS, key=len, reverse=True)
            if _normalized(marker) in normalized
        ),
        "",
    )
    if not relation:
        for pattern in (_ENGLISH_SOURCE_COMPETITION, _KOREAN_SOURCE_COMPETITION):
            match = pattern.search(normalized)
            if match is not None:
                relation = _normalized(match.group(0))
                break
    alias = aliases[0]
    if not relation or relation == alias:
        return ()
    return alias, relation


def comparison_source_sentence_has_self_subject(
    sentence: str,
    self_company: str,
) -> bool:
    """같은 문장에 자사 법인 별칭 또는 self-published 대명사가 있는가."""

    normalized = _normalized(sentence)
    if (
        _SELF_MODIFIES_THIRD_PARTY.search(normalized)
        or _THIRD_PARTY_ATTRIBUTION.search(normalized)
        or _COOPERATIVE_COMPETITION_CONTEXT.search(normalized)
        or _ENGLISH_NONCURRENT_COMPETITOR.search(normalized)
        or _ENGLISH_PAST_COMPETITION.search(normalized)
        or _KOREAN_PAST_COMPETITION.search(normalized)
        or _COMPETITION_DISJUNCTION.search(normalized)
        or any(mark in sentence for mark in _QUOTATION_MARKS)
    ):
        return False
    if _ENGLISH_SELF_COMPETITION.search(
        normalized
    ) or _ENGLISH_SELF_OBJECT_COMPETITION.search(_case_preserving(sentence)):
        return True
    if re.search(
        r"당사(?:는|가|도|와|과|의)?\b.{0,80}"
        r"(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합|경쟁(?:한다|합니다|하고|하며|하는))",
        normalized,
    ) or re.search(
        r"(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합)"
        r".{0,80}당사(?:와|과|의|를|을)?\b",
        normalized,
    ):
        return True

    for alias in comparison_candidate_aliases(self_company):
        if not comparison_alias_is_in_sentence(alias, normalized):
            continue
        escaped = re.escape(_normalized(alias))
        alias_start = rf"(?<!{_ALIAS_BOUNDARY}){escaped}"
        alias_exact = rf"{alias_start}(?!{_ALIAS_BOUNDARY})"
        alias_korean = (
            rf"{alias_start}(?:는|은|이|가|와|과|의|를|을|도)?"
            rf"(?!{_ALIAS_BOUNDARY})"
        )
        third_party_role = (
            r"(?:customers?|clients?|suppliers?|vendors?|partners?|"
            r"distributors?|resellers?|고객|고객사|수요처|협력사|공급사|"
            r"공급업체|파트너|유통사)"
        )
        if re.search(
            alias_start
            + r"(?:'s|의)?\s*(?:customers?|clients?|suppliers?|vendors?|partners?|"
            r"distributors?|resellers?|고객|고객사|수요처|협력사|공급사|"
            r"공급업체|파트너|유통사)",
            normalized,
            re.IGNORECASE,
        ) or re.search(
            rf"{third_party_role}[\s,:-]{{1,8}}{alias_exact}",
            normalized,
            re.IGNORECASE,
        ):
            return False
        if re.search(
            alias_korean
            + r".{0,80}(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합|"
            r"경쟁(?:한다|합니다|하고|하며|하는))",
            normalized,
        ) or re.search(
            alias_exact
            + r"\s+(?:directly\s+)?(?:compete|competes|competed|competing)\s+"
            r"(?:with|against)\b",
            normalized,
            re.IGNORECASE,
        ) or re.search(
            alias_start
            + r"'s\s+(?:[a-z-]+\s+){0,3}competitors?\b",
            normalized,
            re.IGNORECASE,
        ) or re.search(
            r"(?:경쟁사|경쟁업체|경쟁\s*관계|동종\s*업체|경합)"
            + rf".{{0,80}}{alias_korean}",
            normalized,
        ) or re.search(
            r"\b(?:compete|competes|competed|competing)\s+"
            r"(?:directly\s+)?(?:with|against)\s+"
            + alias_exact,
            normalized,
            re.IGNORECASE,
        ) or re.search(
            r"\bcompetitors?\s+(?:of|for)\s+" + alias_exact,
            normalized,
            re.IGNORECASE,
        ):
            return True
    return False


def comparison_overlap_dimension(sentence: str) -> str:
    if any(marker in sentence for marker in _CUSTOMER_MARKERS):
        return "고객 겹침"
    if any(marker in sentence for marker in _PRODUCT_MARKERS):
        return "제품·서비스 겹침"
    return "시장 겹침"


def comparison_source_overlap_dimension(sentence: str) -> str:
    """순수 경쟁 문장에는 고객·제품·시장 겹침을 추정해 붙이지 않는다."""

    if any(marker in sentence for marker in _CUSTOMER_MARKERS):
        return "고객 겹침"
    if any(marker in sentence for marker in _PRODUCT_MARKERS):
        return "제품·서비스 겹침"
    if "시장" in sentence:
        return "시장 겹침"
    return "경쟁 관계 명시"


def encode_comparison_basis_v1(payload: Mapping[str, object]) -> str:
    """닫힌 필드가 모두 유효할 때만 결정론적 v1 JSON을 만든다."""

    encoded = json.dumps(
        {key: payload.get(key, "") for key in _EXPECTED_KEYS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded if parse_comparison_basis_v1(encoded) is not None else ""


def parse_comparison_basis_v1(value: object) -> dict[str, str] | None:
    try:
        payload = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != _EXPECTED_KEYS:
        return None
    clean = {key: str(payload.get(key) or "").strip() for key in _EXPECTED_KEYS}
    if (
        clean["version"] != COMPARISON_BASIS_VERSION
        or not _ID.fullmatch(clean["candidate_fact_id"])
        or not _ID.fullmatch(clean["candidate_source_id"])
        or not re.fullmatch(r"[0-9]{8}", clean["candidate_corp_code"])
        or not re.fullmatch(r"[0-9a-f]{64}", clean["evidence_sha256"])
        or clean["overlap_dimension"] not in COMPARISON_OVERLAP_DIMENSIONS
        or not all(clean.values())
    ):
        return None
    return clean


def encode_comparison_source_basis_v2(payload: Mapping[str, object]) -> str:
    """1~8장 의미 분류와 독립된 공식 원문 후보 근거를 닫힌 JSON으로 만든다.

    v2는 순수 경쟁 문장을 1~8장 사실로 위장하지 않는다. 대신 봉인된 공식
    Source의 신원·날짜·원문 위치와 정확한 한 문장을 함께 보존해 출고 게이트가
    Source 등록부와 SHA-256을 다시 계산할 수 있게 한다.
    """

    encoded = json.dumps(
        {key: payload.get(key, "") for key in _SOURCE_EXPECTED_KEYS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded if parse_comparison_source_basis_v2(encoded) is not None else ""


def parse_comparison_source_basis_v2(value: object) -> dict[str, str] | None:
    raw = str(value or "")
    if len(raw) > 25_000:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) != _SOURCE_EXPECTED_KEYS
        or not all(isinstance(payload.get(key), str) for key in _SOURCE_EXPECTED_KEYS)
    ):
        return None
    clean = {key: str(payload.get(key) or "").strip() for key in _SOURCE_EXPECTED_KEYS}
    if (
        clean["version"] != COMPARISON_SOURCE_BASIS_VERSION
        or not _ID.fullmatch(clean["candidate_source_id"])
        or not _ID.fullmatch(clean["self_attestation_source_id"])
        or not re.fullmatch(r"[0-9]{8}", clean["self_corp_code"])
        or not re.fullmatch(r"[0-9]{8}", clean["candidate_corp_code"])
        or clean["source_kind"] not in {"공시", "기타"}
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", clean["source_date"])
        or not re.fullmatch(r"[0-9a-f]{64}", clean["evidence_sha256"])
        or not re.fullmatch(r"[0-9a-f]{64}", clean["evidence_exact_sha256"])
        or clean["overlap_dimension"] not in COMPARISON_SOURCE_OVERLAP_DIMENSIONS
        or not all(clean.values())
    ):
        return None
    try:
        date.fromisoformat(clean["source_date"])
    except ValueError:
        return None
    if any(
        len(value) > (4000 if key == "evidence_text" else 1000)
        or any(ord(character) < 32 for character in value)
        for key, value in clean.items()
    ):
        return None
    if comparison_evidence_sha256(clean["evidence_text"]) != clean["evidence_sha256"]:
        return None
    if (
        comparison_evidence_exact_sha256(clean["evidence_text"])
        != clean["evidence_exact_sha256"]
    ):
        return None
    return clean


def parse_comparison_basis(value: object) -> dict[str, str] | None:
    """현재 v1 사실 결속과 v2 공식 원문 결속 중 정확히 하나를 읽는다."""

    return parse_comparison_basis_v1(value) or parse_comparison_source_basis_v2(value)


def comparison_source_basis_is_allowed(basis: Mapping[str, str]) -> bool:
    """v2가 DART 공시 또는 공식 HTML IR·웹의 닫힌 출처인지 검사한다."""

    if str(basis.get("version") or "") != COMPARISON_SOURCE_BASIS_VERSION:
        return False
    kind = str(basis.get("source_kind") or "")
    source_type = " ".join(str(basis.get("source_type") or "").split()).casefold()
    host = str(basis.get("source_host") or "").casefold().rstrip(".")
    try:
        parsed = urllib.parse.urlsplit(str(basis.get("source_url") or ""))
        parsed_port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.hostname.casefold().rstrip(".") != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port not in (None, 443)
        or bool(parsed.fragment)
    ):
        return False
    if kind == "공시":
        return source_type == "공식 공시" and host in {
            "dart.fss.or.kr",
            "opendart.fss.or.kr",
            "kind.krx.co.kr",
        }
    if kind != "기타" or source_type not in {
        "회사 공식 ir",
        "공식 ir",
        "회사 공식 웹",
        "공식 웹",
    }:
        return False
    # 회사가 직접 낸 공식 보도자료·뉴스룸은 공식 웹 원문일 수 있다. 검색 결과와
    # 개인·운영 블로그만 후보로 승격하지 않는다. percent encoding으로 이 경계를
    # 우회하지 못하도록 제한 횟수만큼 먼저 푼 뒤 variant/prefix를 검사한다.
    def decoded(value: str) -> str:
        current = value
        for _attempt in range(3):
            candidate = urllib.parse.unquote(current)
            if candidate == current:
                break
            current = candidate
        return unicodedata.normalize("NFKC", current).casefold()

    blocked_prefixes = (
        "blog",
        "search",
        "site-search",
        "site_search",
        "snippet",
    )
    path_segments = {
        decoded(segment).strip()
        for segment in decoded(parsed.path).split("/")
        if segment.strip()
    }
    host_labels = {
        decoded(label).strip() for label in parsed.hostname.split(".") if label
    }
    query_keys = {
        decoded(key).strip()
        for key, _value in urllib.parse.parse_qsl(
            decoded(parsed.query), keep_blank_values=True
        )
    }
    search_query_keys = {
        "s",
        "q",
        "query",
        "keyword",
        "keywords",
        "search",
        "search_query",
        "searchquery",
        "searchword",
        "term",
    }
    if query_keys & search_query_keys:
        return False
    return not any(
        token.startswith(blocked_prefixes)
        for token in path_segments | host_labels
    )


def comparison_dart_profile_attestation_is_valid(
    *,
    source_kind: str,
    source_type: str,
    source_publisher: str,
    source_host: str,
    source_url: str,
    source_document_id: str,
    evidence: str,
    self_corp_code: str,
    self_company: str,
) -> bool:
    """v2 자사 신원을 증명하는 OpenDART 기업개황의 닫힌 계약.

    인증키가 필요한 API 응답을 일반 사용자 citation으로 가장하지 않는다. 이
    함수는 내부 attester의 정확한 endpoint·법인코드와 봉인한 최소 응답 필드만
    검사하며, Source 역할과 HMAC은 각 provenance 등록부가 별도로 검사한다.
    """

    code = str(self_corp_code or "").strip()
    company = _normalized(self_company)
    if (
        source_kind != "공시"
        or _normalized(source_type)
        not in {"규제기관 공식 자료", "공식 규제기관 자료"}
        or _normalized(source_publisher) != company
        or str(source_host or "").casefold().rstrip(".")
        != "opendart.fss.or.kr"
        or not re.fullmatch(r"[0-9]{8}", code)
        or str(source_document_id or "").strip() != code
    ):
        return False
    try:
        parsed_url = urllib.parse.urlsplit(str(source_url or "").strip())
        parsed_port = parsed_url.port
    except (TypeError, ValueError):
        return False
    if (
        parsed_url.scheme.casefold() != "https"
        or (parsed_url.hostname or "").casefold().rstrip(".")
        != "opendart.fss.or.kr"
        or parsed_url.path != "/api/company.json"
        or parsed_url.fragment
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_port not in (None, 443)
        or urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True)
        != [("corp_code", code)]
    ):
        return False
    try:
        payload = json.loads(str(evidence or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    expected_profile_keys = {"corp_code", "corp_name", "hm_url"}
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_profile_keys
        or not all(isinstance(payload.get(key), str) for key in expected_profile_keys)
    ):
        return False
    values = payload
    return bool(
        values["corp_code"] == code
        and _normalized(values["corp_name"]) == company
        and values["corp_name"].strip()
        and all(len(value) <= 1000 for value in values.values())
        and all(
            not any(ord(character) < 32 for character in value)
            for value in values.values()
        )
    )


def comparison_candidate_sentence_matches(
    basis: Mapping[str, str],
    *,
    comparison_target: str,
    evidence_text: str,
    self_company: str,
    known_company_aliases: Iterable[str],
) -> bool:
    """한 문장에서 후보가 자사 경쟁 술어의 target인지까지 역검산한다."""

    if comparison_corporate_core(str(basis.get("candidate_name") or "")) != (
        comparison_corporate_core(comparison_target)
    ):
        return False
    sentences = comparison_evidence_sentences(evidence_text)
    aliases = comparison_candidate_aliases(comparison_target)
    if len(sentences) != 1 or not aliases:
        return False
    sentence = sentences[0]
    marker_matches = (
        comparison_source_sentence_has_marker(sentence)
        if str(basis.get("version") or "") == COMPARISON_SOURCE_BASIS_VERSION
        else comparison_sentence_has_marker(sentence)
    )
    return (
        marker_matches
        and any(
            comparison_alias_is_competition_target(
                alias,
                sentence,
                self_company=self_company,
                known_company_aliases=known_company_aliases,
            )
            for alias in aliases
        )
        and comparison_evidence_sha256(sentence)
        == str(basis.get("evidence_sha256") or "")
        and (
            str(basis.get("version") or "") != COMPARISON_SOURCE_BASIS_VERSION
            or comparison_evidence_exact_sha256(sentence)
            == str(basis.get("evidence_exact_sha256") or "")
        )
    )


def comparison_comparator_source_id(
    *, corp_code: str, comparison_period: str, comparison_scope: str
) -> str:
    """후보 DART 고유번호와 실제 비교사 재무 Source ID의 결정론적 결속."""

    scope = str(comparison_scope or "").upper()
    scope_code = "CFS" if "CFS" in scope else "OFS" if "OFS" in scope else ""
    period = str(comparison_period or "")
    end = period.split("~", 1)[-1]
    year = end[:4] if re.match(r"^20\d{2}-", end) else ""
    code = str(corp_code or "").strip()
    if not re.fullmatch(r"[0-9]{8}", code) or not year or not scope_code:
        return ""
    digest = hashlib.sha256(
        _normalized(code + year + scope_code).encode("utf-8")
    ).hexdigest()[:16]
    return f"comparison-comparator-{digest}"

"""보고서 후보가 실제로 그 회사의 이야기인지 코드로 검사한다.

이 모듈은 새 사실을 만들지 않는다. 수집된 원문을 그대로 통과시키거나 보류할
뿐이다. AI의 "알맹이 있음" 판정만 믿으면 실행마다 일반론이 섞일 수 있으므로,
항목별 최소 증거를 결정론적으로 한 번 더 확인한다.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


_NUMBER_RE = re.compile(r"(?<![A-Za-z가-힣])(?:20\d{2}|\d[\d,]*(?:\.\d+)?)\s*(?:년|월|일|분기|%|％|원|억|만|명|개|건|회|배|곳|개국|도시|석)?")
_LATIN_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9&.+:-]{1,}(?![A-Za-z0-9])")
_LATIN_PHRASE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9&.+:-]*)(?:\s+[A-Z][A-Za-z0-9&.+:-]*)+(?![A-Za-z0-9])"
)
_QUOTED_RE = re.compile(r"[\"'‘’“”「」『』](.{2,30}?)[\"'‘’“”「」『』]")
_PERSON_ROLE_RE = re.compile(r"([가-힣]{2,4})\s*(?:대표|회장|사장|CEO|PD|프로듀서)")
_NAMED_SUFFIX_RE = re.compile(
    r"[가-힣A-Za-z0-9&.+:-]{2,}(?:파트너스|플랫폼|펀드|레이블|스튜디오|센터|아카데미|"
    r"엔터테인먼트|뮤직|레코드|레코딩|샵|SHOP|법인|그룹)"
)

_LATIN_STOP = {
    "ai", "api", "b2b", "b2c", "business", "cd", "ceo", "company", "content",
    "contents", "dart", "digital", "entertainment", "esg", "etf", "global", "group",
    "ir", "it", "kpi", "md", "media", "music", "oem", "odm", "online", "offline",
    "partner", "partners", "partnership", "pd", "platform", "r&d", "record", "records",
    "service", "services", "sns", "strategic", "technology", "tv", "url",
    "advanced", "alliance", "consortium", "creative", "distribution", "innovative",
    "integrated", "international", "leading", "network", "solutions", "universal",
    "worldwide", "premier", "commerce", "collective",
}
_GENERIC_ANCHOR_CORES = {
    "글로벌플랫폼", "글로벌음악플랫폼", "글로벌파트너", "글로벌파트너스",
    "다양한플랫폼", "디지털플랫폼", "온라인플랫폼", "음악플랫폼",
}
_GENERIC_KOREAN_ORG_RE = re.compile(r"[가-힣]{2,}(?:조직|팀|센터|본부|부서|실)$")
_GENERIC_KOREAN_NAMED_RE = re.compile(
    r"(?:글로벌|디지털|온라인|콘텐츠|음악|아티스트|신인|인재|사업|전략|연구|개발|"
    r"마케팅|유통|공연|제작|운영|해외|국내)+(?:스튜디오|레이블|플랫폼|파트너스|"
    r"펀드|뮤직|레코드|레코딩|샵|법인|그룹)$"
)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})\s*년?")
_CLAUSE_SPLIT_RE = re.compile(
    r"[.!?;]|\s*[+,]\s*|\s+(?:그리고|그러나|반면|한편|또한)\s+"
)
_RECENT_YEAR_WINDOW = 3

_METRIC_MARKERS = (
    "매출", "영업이익", "순이익", "손실", "이익률", "원가율", "매출총이익",
    "수익", "판매량", "수출", "관객", "모객", "가입자", "점유율", "영업손익",
)
_PERIOD_MARKERS = ("전년", "전분기", "당기", "전기", "연간", "상반기", "하반기", "분기")
_BUSINESS_MARKERS = (
    "판매", "판다", "제공", "제작", "제조", "만들", "유통", "서비스", "제품", "상품", "라이선스", "로열티",
    "구독", "광고", "공연", "음반", "음원", "매출", "수익", "수수료", "플랫폼",
)
_MECHANISM_MARKERS = (
    "계약", "설비", "기술", "특허", "라이선스", "법인", "지분", "인프라", "시스템",
    "플랫폼", "레이블", "조직", "파트너", "제휴", "유통", "공급", "제작", "운영",
)
_RESULT_MARKERS = (
    "증가", "감소", "확대", "축소", "개선", "하락", "상승", "달성", "기여", "전환",
    "출시", "체결", "선정", "결성", "개설", "진출", "수주", "판매", "반영",
)
_RISK_MARKERS = (
    "위험", "리스크", "과제", "의존", "집중", "부담", "둔화", "감소", "하락", "손실",
    "변동", "불확실", "경쟁", "원가", "비용", "재계약", "환율", "규제", "차질",
)
_ACTION_MARKERS = (
    "출시", "체결", "결성", "선정", "개편", "전환", "도입", "운영", "개최", "공연",
    "투어", "데뷔", "협업", "제휴", "투자", "인수", "설립", "확대", "판매", "공개",
)
_PLAN_MARKERS = (
    "계획", "예정", "추진", "목표", "방향", "로드맵", "확대할", "강화할", "진출할",
    "구축할", "도입할", "개발할", "출시할", "성장 전략", "중장기", "향후",
    "방침", "하려고", "하고자", "하기로", "검토 중", "검토",
)
_RELATION_MARKERS = (
    "계약", "제휴", "협업", "파트너십", "파트너사", "파트너 관계", "유통", "공급", "고객", "거래처", "발주",
    "판매처", "라이선스", "합작", "공동", "대행", "배급",
)
_RELATION_COREFERENCE = (
    "양사", "이번 파트너십", "해당 파트너십", "동 파트너십", "해당 계약", "동 계약",
)
_GENERIC_MARKERS = (
    "글로벌화 트렌드", "시스템을 강화", "경쟁력을 강화", "성장동력", "시너지 창출",
    "시장 변화에 대응", "지속적인 성장", "역량을 강화", "미래 성장", "고객 만족",
)
_FORECAST_MARKERS = (
    "것으로 보인다", "것으로 예상", "것으로 전망", "전망된다", "추정된다", "예상된다",
    "기대된다", "가능성이 있다",
)
_FORECAST_RE = re.compile(
    r"(?:할\s+것으로\s*(?:보|예상|전망)[가-힣]*|가능성이\s*(?:있|높)[가-힣]*)"
)
_EVIDENCE_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)")

_IDENTITY_MARKERS = (
    "정체성", "미션", "비전", "존재 목적", "설립 목적", "핵심 가치", "기업 이념",
    "전문기업", "기업이다", "회사다", "사업을 영위",
)
_CULTURE_MARKERS = (
    "인재상", "핵심가치", "핵심 가치", "조직문화", "기업문화", "일하는 방식",
    "존중", "신뢰", "협업", "소통", "책임", "정직", "겸손", "창의",
)
_CURRENT_MARKERS = (
    "현재", "진행 중", "대응", "개선 중", "추진 중", "협의 중", "준비 중",
)

_SEMANTIC_CELL_ALIASES: dict[str, str] = {
    "business_model": "1",
    "current_challenges": "4-1",
    "future_strategy": "4-3",
    "operations_partners": "9",
}


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    score: int
    reason: str = ""


def _plain(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _contains(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _company_tokens(company: str) -> set[str]:
    normalized = _plain(company).casefold()
    stripped = re.sub(r"(?:주식회사|\(주\)|㈜|엔터테인먼트|entertainment|corp(?:oration)?|inc)", " ", normalized)
    return {token for token in re.split(r"[^0-9a-z가-힣]+", stripped) if len(token) >= 2}


def _identity_core(value: str) -> str:
    normalized = _plain(value).casefold()
    normalized = re.sub(
        r"(?:주식회사|\(주\)|㈜|엔터테인먼트|entertainment|corporation|corp|inc)",
        "",
        normalized,
    )
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def _looks_like_strong_latin(raw: str) -> bool:
    """약어·혼합대소문자·숫자처럼 문자열 자체로 강한 영문 실명만 인정한다."""

    token = raw.strip(".")
    folded = token.casefold()
    if folded in _LATIN_STOP or len(token) < 2:
        return False
    return (
        token.isupper()
        or any(char.isupper() for char in token[1:])
        or any(char.isdigit() for char in token)
    )


def verified_latin_names(texts: Iterable[str]) -> set[str]:
    """서로 다른 근거 문장에 두 번 이상 나온 명칭만 보강 실명으로 둔다."""

    rows = [
        _plain(match.group(0))
        for text in texts
        for match in _EVIDENCE_SENTENCE_RE.finditer(_plain(text))
        if _plain(match.group(0))
    ]
    display_by_core: dict[str, str] = {}
    for row in rows:
        for match in _LATIN_PHRASE_RE.finditer(row):
            phrase = match.group(0).strip()
            words = [word.casefold().strip(".") for word in phrase.split()]
            if words and all(word in _LATIN_STOP for word in words):
                continue
            core = _identity_core(phrase)
            if not core:
                continue
            display_by_core.setdefault(core, phrase)
        for phrase in _NAMED_SUFFIX_RE.findall(row):
            core = _identity_core(phrase)
            if not core:
                continue
            display_by_core.setdefault(core, phrase)
    return {
        display
        for display in display_by_core.values()
        if _distinct_claim_mentions(display, rows) >= 2
    }


def _distinct_claim_mentions(value: str, rows: Iterable[str]) -> int:
    needle = _plain(value).casefold()
    cores: list[str] = []
    for row in rows:
        folded = row.casefold()
        index = folded.find(needle)
        if index < 0:
            continue
        core = _identity_core(row[index:])
        if not core:
            continue
        if any(core in prior or prior in core for prior in cores):
            continue
        cores.append(core)
    return len(cores)


def _recent_clause_with(
    text: str,
    *,
    cutoff_year: int,
    markers: Iterable[str],
    company: str,
    verified_names: Iterable[str] = (),
    executed_only: bool = False,
    allow_coreference: bool = False,
) -> bool:
    """최근 시점·사건·실명이 같은 절에 있는지 보수적으로 확인한다."""

    prior_named: set[str] = set()
    prior_recent = False
    for clause in _claim_clauses(text):
        clause_years = [int(year) for year in _YEAR_RE.findall(clause)]
        recent = any(year >= cutoff_year for year in clause_years) or _contains(
            clause, ("최근", "현재", "당기", "이번", "올해", "금년")
        )
        named = {
            anchor
            for anchor in anchor_tokens(clause, company, verified_names=verified_names)
            if not _NUMBER_RE.fullmatch(_plain(anchor))
        }
        if allow_coreference and _contains(clause, _RELATION_COREFERENCE) and prior_named:
            named.update(prior_named)
            recent = recent or prior_recent
        marker_ok = _has_executed_action(clause) if executed_only else _contains(clause, markers)
        if recent and named and marker_ok:
            return True
        prior_named = named
        prior_recent = recent
    return False


def _claim_clauses(text: str) -> list[str]:
    """문장 경계와 두 번째 이후 연도부터 절을 나눈다.

    첫 연도 앞의 주어·실명은 같은 절에 보존하되, 과거 사건 뒤에 붙인 최근
    숫자가 앞의 실명을 빌리지 못하게 한다.
    """

    out: list[str] = []
    for base in _CLAUSE_SPLIT_RE.split(text):
        year_matches = list(_YEAR_RE.finditer(base))
        starts = [match.start() for match in year_matches[1:]]
        if not starts:
            if base.strip():
                out.append(base)
            continue
        start = 0
        for end in starts:
            if base[start:end].strip():
                out.append(base[start:end])
            start = end
        if base[start:].strip():
            out.append(base[start:])
    return out


def _has_executed_action(text: str) -> bool:
    """`확대할 계획`처럼 미래형인 표지를 이미 실행한 사건으로 세지 않는다."""

    for marker in _ACTION_MARKERS:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            tail = text[index + len(marker):index + len(marker) + 20]
            if not _FORECAST_RE.search(tail) and not re.match(
                r"\s*(?:를|을)?\s*(?:"
                r"(?:할|하는|한다는)\s*(?:계획|예정|방침)|"
                r"할\s+것으로\s*(?:보인다|예상된다|전망된다)|"
                r"하기로|검토\s*(?:중|할|하고)|"
                r"예정|계획|방침|하려고|하고자)",
                tail,
            ):
                return True
            start = index + len(marker)
    return False


def anchor_tokens(
    text: str,
    company: str = "",
    *,
    verified_names: Iterable[str] = (),
) -> set[str]:
    """문장 안에서 그대로 재확인할 수 있는 회사 고유 단서를 뽑는다."""

    clean = _plain(text)
    company_tokens = _company_tokens(company)
    company_cores = {_identity_core(token) for token in company_tokens}
    verified_by_core = {_identity_core(name): _plain(name) for name in verified_names}
    anchors: set[str] = set()
    for raw in _LATIN_TOKEN_RE.findall(clean):
        token = raw.casefold().strip(".")
        if token in company_tokens or not _looks_like_strong_latin(raw):
            continue
        anchors.add(raw)
    anchors.update(
        display
        for core, display in verified_by_core.items()
        if core
        and re.search(
            rf"(?<![A-Za-z0-9]){re.escape(display)}(?![A-Za-z0-9])",
            clean,
            flags=re.IGNORECASE,
        )
    )
    anchors.update(match.group(1) for match in _QUOTED_RE.finditer(clean))
    anchors.update(match.group(1) for match in _PERSON_ROLE_RE.finditer(clean))
    anchors.update(
        match
        for match in _NAMED_SUFFIX_RE.findall(clean)
        if re.search(r"[A-Za-z0-9]", match)
        or _identity_core(match) in verified_by_core
    )
    anchors.update(match.group(0) for match in _NUMBER_RE.finditer(clean) if match.group(0).strip())
    return {
        anchor.strip()
        for anchor in anchors
        if anchor.strip()
        and _identity_core(anchor) not in company_cores
        and _identity_core(anchor) not in _GENERIC_ANCHOR_CORES
        and not _GENERIC_KOREAN_ORG_RE.fullmatch(_identity_core(anchor))
        and not _GENERIC_KOREAN_NAMED_RE.fullmatch(_identity_core(anchor))
    }


def source_kind_matches_sentence(kind: str, text: str) -> bool:
    """조각 라벨과 문장 의미가 명백히 충돌하면 AI에게 주기 전에 막는다.

    특히 사업보고서의 목차·교차참조에서 잡힌 ``재무`` 조각이 뒤쪽 연구개발
    문장을 품는 사례를 차단한다. 재무 후보는 숫자와 재무 지표가 함께 있어야 한다.
    """

    clean = _plain(text)
    if not clean:
        return False
    if kind == "재무":
        if clean.startswith("주요계정(DART API):"):
            return True
        return bool(_NUMBER_RE.search(clean)) and _contains(clean, _METRIC_MARKERS)
    return True


def assess_claim(
    cell: str,
    text: str,
    *,
    source_kind: str = "",
    company: str = "",
    as_of_year: int | None = None,
    verified_names: Iterable[str] = (),
) -> GateDecision:
    """항목별 최소 증거를 검사한다. 통과해도 사실 여부는 별도 근거 대조가 맡는다."""

    clean = _plain(text)
    if not source_kind_matches_sentence(source_kind, clean):
        return GateDecision(False, 0, "출처 종류와 문장 내용 불일치")

    anchors = anchor_tokens(clean, company, verified_names=verified_names)
    named_anchors = {
        anchor for anchor in anchors if not _NUMBER_RE.fullmatch(_plain(anchor))
    }
    has_number = bool(_NUMBER_RE.search(clean))
    has_period = has_number or _contains(clean, _PERIOD_MARKERS)
    years = [int(year) for year in _YEAR_RE.findall(clean)]
    cutoff_year = (as_of_year or dt.date.today().year) - _RECENT_YEAR_WINDOW
    explicitly_stale = bool(years) and max(years) < cutoff_year
    recent_action_context = _recent_clause_with(
        clean,
        cutoff_year=cutoff_year,
        markers=_ACTION_MARKERS,
        company=company,
        verified_names=verified_names,
        executed_only=True,
    )
    recent_plan_context = _recent_clause_with(
        clean,
        cutoff_year=cutoff_year,
        markers=_PLAN_MARKERS,
        company=company,
        verified_names=verified_names,
        allow_coreference=True,
    )
    generic = _contains(clean, _GENERIC_MARKERS)
    score = min(len(anchors), 3) + int(has_number) + int(has_period)

    if cell == "identity":
        official_source = source_kind not in {"뉴스", "재무"}
        passed = official_source and _contains(clean, _IDENTITY_MARKERS)
        return GateDecision(
            passed,
            score + int(passed),
            "공식 자기정의 또는 산업 내 역할이 없음" if not passed else "",
        )

    if cell == "portfolio":
        executed = _has_executed_action(clean)
        passed = (
            _contains(clean, _BUSINESS_MARKERS)
            and (executed or _contains(clean, ("운영 중", "판매 중", "출시", "공급")))
            and (bool(anchors) or has_number)
            and not _contains(clean, _FORECAST_MARKERS)
        )
        return GateDecision(
            passed,
            score + int(executed),
            "현재 실행 근거가 있는 제품·서비스가 아님" if not passed else "",
        )

    if cell == "past_changes":
        if explicitly_stale:
            return GateDecision(False, score, "최근 36개월보다 오래된 실행 근거")
        actual_metric = has_number and _contains(clean, _METRIC_MARKERS)
        executed = recent_action_context
        passed = (actual_metric or executed) and not _contains(clean, _FORECAST_MARKERS)
        return GateDecision(
            passed,
            score + int(executed) + int(actual_metric),
            "최근 36개월의 완료 실행 또는 실제 실적이 아님" if not passed else "",
        )

    if cell == "culture":
        official_source = source_kind == "홈페이지"
        passed = official_source and _contains(clean, _CULTURE_MARKERS)
        return GateDecision(
            passed,
            score + int(passed),
            "공식 채용·문화 자료의 가치 또는 범위가 있는 사례가 아님" if not passed else "",
        )

    cell = _SEMANTIC_CELL_ALIASES.get(cell, cell)

    if cell == "1":
        passed = _contains(clean, _BUSINESS_MARKERS) and (
            "주요사업" in clean or "수익" in clean or "매출" in clean or "판매" in clean
            or "판다" in clean or "제공" in clean
        )
        return GateDecision(passed, score + int(passed), "사업·수익 방식이 없음" if not passed else "")

    if cell == "2":
        quantified_result = (
            has_number
            and _contains(clean, _METRIC_MARKERS)
            and _contains(clean, _RESULT_MARKERS)
        )
        passed = (
            _contains(clean, _MECHANISM_MARKERS)
            and (bool(named_anchors) or quantified_result)
            and not (generic and len(named_anchors) < 2)
        )
        return GateDecision(passed, score + int(_contains(clean, _RESULT_MARKERS)), "고유 단서와 경쟁력의 실체가 함께 있지 않음" if not passed else "")

    if cell == "3":
        passed = has_number and has_period and _contains(clean, _METRIC_MARKERS)
        return GateDecision(passed, score + 3 if passed else score, "기간·지표·숫자가 함께 있지 않음" if not passed else "")

    if cell == "4-1":
        passed = _contains(clean, _RISK_MARKERS) and (bool(anchors) or has_number)
        if source_kind and source_kind != "뉴스":
            passed = passed and (
                _contains(clean, _CURRENT_MARKERS)
                or _has_executed_action(clean)
                or _contains(clean, ("과제", "위험", "손실", "부담", "의존"))
            )
        return GateDecision(passed, score + int(passed), "회사 고유 위험·변화 근거가 없음" if not passed else "")

    if cell == "4-2":
        if explicitly_stale:
            return GateDecision(False, score, "최근 3년보다 오래된 실행 근거")
        passed = (
            _contains(clean, _ACTION_MARKERS)
            and bool(named_anchors)
            and recent_action_context
            and not (generic and len(named_anchors) < 2)
        )
        return GateDecision(passed, score + int(_contains(clean, _RESULT_MARKERS)), "최근 실행과 고유 단서가 함께 있지 않음" if not passed else "")

    if cell == "4-3":
        if explicitly_stale:
            return GateDecision(False, score, "최근 3년보다 오래된 계획 근거")
        if _contains(clean, _FORECAST_MARKERS) or _FORECAST_RE.search(clean):
            return GateDecision(False, score, "회사 계획이 아닌 외부 전망 표현")
        passed = recent_plan_context and _contains(clean, _PLAN_MARKERS) and _contains(
            clean, _MECHANISM_MARKERS + _BUSINESS_MARKERS
        ) and bool(named_anchors)
        return GateDecision(passed, score + int(passed), "공식 방향·실행수단·고유 단서가 함께 있지 않음" if not passed else "")

    if cell == "9":
        passed = _contains(clean, _RELATION_MARKERS) and bool(named_anchors)
        return GateDecision(passed, score + int(passed), "실명 파트너와 관계가 함께 있지 않음" if not passed else "")

    return GateDecision(True, score)


def filter_items(
    items: list[Any],
    frags: dict[int, dict[str, str]],
    *,
    company: str,
    allowed_sources: dict[str, set[str]] | None = None,
) -> tuple[list[Any], list[tuple[Any, GateDecision]], int]:
    """AI가 고른 원문 중 항목별 최소 증거를 통과한 것만 남긴다."""

    kept: list[Any] = []
    rejected: list[tuple[Any, GateDecision]] = []
    total_score = 0
    verified_names = verified_latin_names(
        str(frag.get("원문", "")) for frag in frags.values()
    )
    for item in items:
        frag = frags.get(getattr(item, "fragment_id", None), {})
        block = str(getattr(item, "block", ""))
        source_kind = str(frag.get("종류", ""))
        if allowed_sources is not None and source_kind not in allowed_sources.get(block, set()):
            rejected.append((item, GateDecision(False, 0, "이 항목에서 허용하지 않는 출처 종류")))
            continue
        decision = assess_claim(
            block,
            str(getattr(item, "sentence", "")),
            source_kind=source_kind,
            company=company,
            verified_names=verified_names,
        )
        if decision.passed:
            kept.append(item)
            total_score += decision.score
        else:
            rejected.append((item, decision))
    return kept, rejected, total_score


def filter_prose_lines(
    cell: str,
    lines: list[tuple[str, str]],
    evidence: list[tuple[str, str]],
    *,
    company: str,
) -> list[tuple[str, str]]:
    """작가가 고유 단서를 지워 일반론으로 만든 문장은 표시용 글에서 제외한다."""

    verified_names = verified_latin_names(source_text for source_text, _cite in evidence)
    by_cite: dict[str, set[str]] = {}
    for source_text, cite in evidence:
        by_cite.setdefault(cite, set()).update(
            anchor_tokens(source_text, company, verified_names=verified_names)
        )
    out: list[tuple[str, str]] = []
    for text, cite in lines:
        source_anchors = by_cite.get(cite, set())
        decision = assess_claim(
            cell,
            text,
            source_kind="",
            company=company,
            verified_names=verified_names,
        )
        if decision.passed and (not source_anchors or any(anchor in text for anchor in source_anchors)):
            out.append((text, cite))
    return out

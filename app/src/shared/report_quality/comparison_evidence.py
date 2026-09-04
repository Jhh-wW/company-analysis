"""공식 양사 비교의 구조 필드와 DART 원문 행을 잇는 단일 정본."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import date


_INTEGER_AMOUNT = re.compile(r"^-?[0-9]+$")
_CONTEXT_MARKERS: Mapping[str, tuple[str, ...]] = {
    "customer": ("고객", "수요처", "납품처", "발주처"),
    "product": ("제품", "서비스", "품목", "브랜드", "장비", "소재"),
    "market": ("시장", "산업", "지역"),
}
_CONTEXT_STOP = frozenset(
    {
        "회사는", "회사의", "회사", "공식", "사업보고서", "연결재무제표",
        "별도재무제표", "대상", "대상으로", "기반", "고객", "수요처",
        "납품처", "발주처", "제품", "서비스", "품목", "브랜드", "장비",
        "소재", "시장", "산업", "지역", "공급", "공급한다", "판매",
        "판매한다", "제공", "제공한다", "공시", "공시한다",
    }
)


def _normalized(value: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _canonical_period(value: object) -> str:
    matches = re.findall(r"20\d{2}|\d{1,2}", str(value or ""))
    if len(matches) < 6:
        return ""
    try:
        start = date(int(matches[0]), int(matches[1]), int(matches[2]))
        end = date(int(matches[3]), int(matches[4]), int(matches[5]))
    except ValueError:
        return ""
    return f"{start.isoformat()}~{end.isoformat()}"


def _context_lexeme(token: str) -> str:
    clean = token.casefold()
    for suffix in (
        "으로", "에서", "에게", "부터", "까지", "과", "와", "을", "를",
        "은", "는", "이", "가", "의", "에",
    ):
        if len(clean) >= len(suffix) + 2 and clean.endswith(suffix):
            return clean[: -len(suffix)]
    return clean


def _axis_terms(text: str, markers: tuple[str, ...]) -> set[str]:
    clauses = re.split(r"[.!?\n]", _normalized(text))
    relevant = (
        clause for clause in clauses if any(marker in clause for marker in markers)
    )
    terms: set[str] = set()
    for clause in relevant:
        for token in re.findall(r"[가-힣A-Za-z]{2,}", clause):
            lexeme = _context_lexeme(token)
            if lexeme and lexeme not in _CONTEXT_STOP:
                terms.add(lexeme)
    return terms


def comparison_official_text(evidence: str) -> str:
    """비교 JSON payload의 공식 서술 원문만 꺼내고 구형 원문은 그대로 둔다."""

    raw = str(evidence or "").strip()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("official_text") or "").strip()


def comparison_shared_context(
    *,
    self_company: str,
    self_text: str,
    comparator_company: str,
    comparator_text: str,
) -> dict[str, str]:
    """양사 원문에 모두 직접 나타나는 고객·제품·시장 범위를 재계산한다."""

    context: dict[str, str] = {}
    company_terms = {
        token
        for name in (self_company, comparator_company)
        for token in re.findall(r"[가-힣A-Za-z]{2,}", _normalized(name))
    }
    for axis, markers in _CONTEXT_MARKERS.items():
        common = (
            _axis_terms(self_text, markers) & _axis_terms(comparator_text, markers)
        ) - company_terms
        chosen = sorted(term for term in common if len(term) >= 2)
        if len(chosen) < 2:
            return {}
        context[axis] = "·".join(chosen[:6])
    return context


def comparison_evidence_rows(
    *,
    evidence: str,
    period: str,
    definition: str,
    scope: str,
) -> tuple[Mapping[str, object], ...] | None:
    """선언한 계정·기간·범위와 정확히 맞는 DART 행을 정의 순서로 돌려준다."""

    try:
        payload = json.loads(str(evidence or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("financials"), dict):
        return None
    financials = payload["financials"]
    if financials.get("status") != "000" or str(
        financials.get("reprt_code") or ""
    ).strip() != "11011":
        return None
    raw_rows = financials.get("list")
    if not isinstance(raw_rows, list):
        return None

    canonical_period = _canonical_period(period)
    scope_match = re.search(r"\b(CFS|OFS)\b", scope, re.IGNORECASE)
    definitions = tuple(
        row.strip() for row in str(definition or "").split(";") if row.strip()
    )
    if not canonical_period or scope_match is None or not definitions:
        return None
    scope_code = scope_match.group(1).upper()

    selected: list[Mapping[str, object]] = []
    for raw_definition in definitions:
        fields = tuple(field.strip() for field in raw_definition.split("|"))
        if len(fields) != 5 or any(not field for field in fields):
            return None
        metric_id, account_name, statement_kind, report_code, currency = fields
        matches = tuple(
            row
            for row in raw_rows
            if isinstance(row, dict)
            and str(row.get("account_id") or "").strip() == metric_id
            and _normalized(row.get("account_nm")) == _normalized(account_name)
            and str(row.get("sj_div") or "").strip().upper()
            == statement_kind.upper()
            and str(row.get("reprt_code") or "").strip() == report_code
            and str(row.get("currency") or "").strip().upper() == currency.upper()
            and str(row.get("fs_div") or "").strip().upper() == scope_code
            and _canonical_period(row.get("thstrm_dt")) == canonical_period
        )
        if len(matches) != 1:
            return None
        selected.append(matches[0])
    return tuple(selected)


def comparison_evidence_amounts(
    *,
    evidence: str,
    period: str,
    definition: str,
    scope: str,
) -> tuple[int, ...] | None:
    """정확히 선택한 DART 행의 당기금액만 정의 순서로 읽는다."""

    rows = comparison_evidence_rows(
        evidence=evidence,
        period=period,
        definition=definition,
        scope=scope,
    )
    if rows is None:
        return None
    amounts: list[int] = []
    for row in rows:
        raw = str(row.get("thstrm_amount") or "").strip().replace(",", "")
        if _INTEGER_AMOUNT.fullmatch(raw) is None:
            return None
        amounts.append(int(raw))
    return tuple(amounts)


__all__ = [
    "comparison_evidence_amounts",
    "comparison_evidence_rows",
    "comparison_official_text",
    "comparison_shared_context",
]

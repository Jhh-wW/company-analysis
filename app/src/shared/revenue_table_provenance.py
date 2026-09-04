"""매출 구성표의 원문 행과 공개 행을 잇는 정본 근거 계약.

``revenuemix`` 생산자와 ``composer`` 출고 검증기가 같은 계약을 쓰도록 이
작은 모듈에 모았다. 공개 숫자가 JSON 안에 한 번 더 적혀 있다는 사실만으로는
근거가 되지 않는다. 반드시 인용 조각 안의 실제 원문 행·범위·해시까지 다시
맞아야 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Final, Literal, TypeAlias


RevenueAxis: TypeAlias = Literal["product", "region"]

REVENUE_AXIS_PRODUCT: Final[RevenueAxis] = "product"
REVENUE_AXIS_REGION: Final[RevenueAxis] = "region"
REVENUE_AXES: Final[frozenset[str]] = frozenset(
    {REVENUE_AXIS_PRODUCT, REVENUE_AXIS_REGION}
)

# 생산자와 AI 전 결속기가 표제·캡션에서 같은 축을 읽는다. 이 목록이 둘로
# 갈라지면 새 표제 하나를 추가할 때 추출기는 제품표로 읽고 검증기는 지역표로
# 읽는 식의 또 다른 배선 오류가 생긴다.
REVENUE_HEADS_BY_AXIS: Final[dict[RevenueAxis, tuple[str, ...]]] = {
    REVENUE_AXIS_PRODUCT: (
        "제품별 매출액",
        "품목별 매출액",
        "제품별 매출 실적",
        "사업부문별 매출액",
        "주요 제품 및 서비스의 현황",
    ),
    REVENUE_AXIS_REGION: (
        "지역별 매출액",
        "지역별 매출 실적",
        "매출지역별",
    ),
}
REVENUE_CAPTION_BY_AXIS: Final[dict[RevenueAxis, str]] = {
    REVENUE_AXIS_PRODUCT: "무엇을 팔아 번 돈인가 — 제품·서비스별 매출 비중",
    REVENUE_AXIS_REGION: "어디서 번 돈인가 — 지역별 매출 비중",
}

REVENUE_ROW_PROVENANCE_SCHEMA: Final[str] = "revenue-table-row-provenance-v2"
REVENUE_EXTRACTOR_NAME: Final[str] = "revenuemix.regex"
REVENUE_EXTRACTOR_VERSION: Final[str] = "3"
REVENUE_MAX_ROWS: Final[int] = 12
REVENUE_HEADERS: Final[tuple[str, str, str]] = (
    "구분",
    "매출액 (백만원)",
    "비중",
)
REVENUE_NAME_NOISE: Final[tuple[str, ...]] = (
    "매 출 액",
    "매출액",
    "비 중",
    "비중",
    "구 분",
    "구분",
    "품 목",
    "품목",
    "(단위 : 백만원)",
    "(단위: 백만원)",
    "단위 : 백만원",
    "연결재무제표 기준",
    "매 출 지 역",
    "매출지역",
    "고객과의 계약에서 생기는 수익",
)
REVENUE_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"([^\d%]{2,40}?)\s+(\d{1,3}(?:,\d{3})+|\d{4,})\s+"
    r"(\d{1,3}\.\d{1,2})\s*%"
)

_HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_TOTAL_NAMES: Final[frozenset[str]] = frozenset({"합계", "총계", "계"})


def canonical_json(value: object) -> str:
    """근거 행의 바이트 정본(JSON)을 만든다."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_revenue_name(raw: str) -> str:
    """생산자와 검증기가 공유하는 매출 행 이름 정규화."""

    name = str(raw)
    for noise in REVENUE_NAME_NOISE:
        name = name.replace(noise, " ")
    name = re.sub(r"\(주\s*\d+\)", " ", name)
    name = re.sub(
        r"20\d{2}\s*년|제\s*\d+\s*기|\(\s*(?:당|전|전 전)\s*기\s*\)",
        " ",
        name,
    )
    name = re.sub(r"[·\-–—]+\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" ,.()")


def is_revenue_total_name(value: object) -> bool:
    """합계 표지인지 *전체 이름*으로 판정한다.

    부분문자열 ``계``를 쓰면 기계·회계·설계를 합계로 오인하므로 공백을
    접은 뒤 닫힌 목록과 정확히 비교한다.
    """

    return re.sub(r"\s+", "", str(value)) in _TOTAL_NAMES


def displayed_percent_total_is_complete(values: Iterable[object]) -> bool:
    """표시 자릿수의 반올림만으로 합계 100이 가능한지 판정한다."""

    parsed: list[Decimal] = []
    tolerance = Decimal(0)
    for value in values:
        raw = str(value).strip().replace(",", "").removesuffix("%")
        try:
            number = Decimal(raw)
        except (InvalidOperation, ValueError):
            return False
        if not number.is_finite() or number < 0 or number > 100:
            return False
        parsed.append(number)
        places = len(raw.partition(".")[2]) if "." in raw else 0
        tolerance += Decimal(1).scaleb(-places) / Decimal(2)
    if not parsed:
        return False
    difference = abs(sum(parsed, Decimal(0)) - Decimal(100))
    return difference == 0 or difference < tolerance


def revenue_table_evidence_identity(evidence: str) -> str:
    """한 표의 모든 행이 공유해야 하는 source+total 정본 신원."""

    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, Mapping) or evidence != canonical_json(payload):
        return ""
    if not all(key in payload for key in ("schema", "extractor", "source", "table")):
        return ""
    return sha256_text(
        canonical_json(
            {
                "schema": payload["schema"],
                "extractor": payload["extractor"],
                "source": payload["source"],
                "table": payload["table"],
            }
        )
    )


def revenue_table_source_excerpt(evidence_rows: Sequence[str]) -> str:
    """동일한 한 표의 근거 행들에서 전용 cite용 최소 원문을 꺼낸다."""

    if not evidence_rows:
        return ""
    identities = {revenue_table_evidence_identity(value) for value in evidence_rows}
    if "" in identities or len(identities) != 1:
        return ""
    excerpts: set[str] = set()
    for evidence in evidence_rows:
        try:
            payload = json.loads(evidence)
            excerpt = payload["source"]["excerpt"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return ""
        if not isinstance(excerpt, str) or not excerpt:
            return ""
        excerpts.add(excerpt)
    return excerpts.pop() if len(excerpts) == 1 else ""


def _caption_matches_axis(axis: RevenueAxis, caption: str) -> bool:
    expected = REVENUE_CAPTION_BY_AXIS[axis]
    return re.fullmatch(
        rf"{re.escape(expected)}(?: \(20\d{{2}}년\))?",
        str(caption).strip(),
    ) is not None


def _text_axis(value: str) -> RevenueAxis | None:
    """원문 시작의 알려진 표제 하나에서 축을 읽는다.

    부분문자열 전체를 훑어 축을 정하면 목차나 다음 표제가 뒤에 있는 경우가
    다시 섞인다. 생산자가 보존한 header/excerpt는 현재 표제에서 정확히
    시작해야 하므로, 여기서도 오직 시작 위치의 표제만 인정한다.
    """

    matches = tuple(
        axis
        for axis, heads in REVENUE_HEADS_BY_AXIS.items()
        if any(value.startswith(head) for head in heads)
    )
    return matches[0] if len(matches) == 1 else None


def revenue_table_axis_matches(
    *,
    axis: object,
    caption: object,
    evidence_rows: Sequence[str],
    cited_source_text: str = "",
) -> bool:
    """typed 축·캡션·머리말·인용 원문의 축이 모두 같은지 확인한다.

    ``revenuemix``가 만든 ``axis`` 문자열만 믿으면 누군가 캡션만 제품으로
    바꾼 지역 표가 통과한다. 반대로 캡션만 읽으면 지금 잡은 것처럼 추출 행이
    다른 축이어도 알 수 없다. 따라서 서로 독립인 네 자리를 AI 호출 전에
    맞춰 본다: raw transport의 축, 공개 캡션, evidence의 봉인 축·머리말,
    그리고 실제 cite에 들어갈 excerpt.
    """

    if type(axis) is not str or axis not in REVENUE_AXES:
        return False
    typed_axis: RevenueAxis = axis  # type: ignore[assignment]
    if type(caption) is not str or not _caption_matches_axis(typed_axis, caption):
        return False
    excerpt = revenue_table_source_excerpt(evidence_rows)
    if not excerpt or _text_axis(excerpt) != typed_axis:
        return False
    if cited_source_text and cited_source_text != excerpt:
        return False

    for evidence in evidence_rows:
        try:
            payload = json.loads(evidence)
            table = payload["table"]
            header = table["header"]["text"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False
        if (
            not isinstance(table, Mapping)
            or table.get("axis") != typed_axis
            or not isinstance(header, str)
            or _text_axis(header) != typed_axis
        ):
            return False
    return True


def _span_payload(raw_match: str, match_start: int, match_end: int) -> dict[str, object]:
    return {
        "value": raw_match[match_start:match_end],
        "start": match_start,
        "end": match_end,
    }


def build_revenue_row_evidence(
    *,
    filing_text: str,
    header_start: int,
    header_end: int,
    excerpt_start: int,
    excerpt_end: int,
    row_raw_match: str,
    row_start: int,
    row_end: int,
    row_field_spans: Mapping[str, tuple[int, int]],
    source_index: int,
    selected_index: int,
    public_row: Sequence[str],
    row_count: int,
    total_raw_match: str,
    total_start: int,
    total_end: int,
    total_field_spans: Mapping[str, tuple[int, int]],
    selection: str,
    axis: RevenueAxis,
) -> str:
    """한 공개 행에 붙일 손실 없는 원문 근거 JSON을 만든다."""

    excerpt = filing_text[excerpt_start:excerpt_end]
    row_fields = {
        name: _span_payload(row_raw_match, *row_field_spans[name])
        for name in ("name", "amount", "ratio")
    }
    total_fields = {
        name: _span_payload(total_raw_match, *total_field_spans[name])
        for name in ("name", "amount", "ratio")
    }
    payload = {
        "schema": REVENUE_ROW_PROVENANCE_SCHEMA,
        "extractor": {
            "name": REVENUE_EXTRACTOR_NAME,
            "version": REVENUE_EXTRACTOR_VERSION,
        },
        "source": {
            "filing_sha256": sha256_text(filing_text),
            "excerpt": excerpt,
            "start": excerpt_start,
            "end": excerpt_end,
            "sha256": sha256_text(excerpt),
        },
        "table": {
            "axis": axis,
            "complete": True,
            "row_count": row_count,
            "max_rows": REVENUE_MAX_ROWS,
            "header": {
                "text": filing_text[header_start:header_end],
                "start": header_start,
                "end": header_end,
                "excerpt_start": header_start - excerpt_start,
                "excerpt_end": header_end - excerpt_start,
                "sha256": sha256_text(filing_text[header_start:header_end]),
            },
            "total": {
                "raw_match": total_raw_match,
                "start": total_start,
                "end": total_end,
                "excerpt_start": total_start - excerpt_start,
                "excerpt_end": total_end - excerpt_start,
                "sha256": sha256_text(total_raw_match),
                "raw_fields": total_fields,
            },
        },
        "row": {
            "raw_match": row_raw_match,
            "start": row_start,
            "end": row_end,
            "excerpt_start": row_start - excerpt_start,
            "excerpt_end": row_end - excerpt_start,
            "sha256": sha256_text(row_raw_match),
            "raw_fields": row_fields,
            "selection": selection,
            "source_index": source_index,
            "selected_index": selected_index,
        },
        "public_fields": dict(zip(REVENUE_HEADERS, (str(value) for value in public_row))),
    }
    return canonical_json(payload)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _field_value(fields: object, name: str, raw_match: str) -> str | None:
    if not isinstance(fields, Mapping) or set(fields) != {"name", "amount", "ratio"}:
        return None
    field = fields.get(name)
    if not isinstance(field, Mapping) or set(field) != {"value", "start", "end"}:
        return None
    start, end = _integer(field.get("start")), _integer(field.get("end"))
    value = field.get("value")
    if (
        start is None
        or end is None
        or start < 0
        or end < start
        or end > len(raw_match)
        or not isinstance(value, str)
        or raw_match[start:end] != value
    ):
        return None
    return value


def _raw_span_matches(
    payload: object,
    *,
    excerpt: str,
    excerpt_absolute_start: int,
) -> tuple[str, Mapping[str, object]] | None:
    if not isinstance(payload, Mapping):
        return None
    required = {
        "raw_match",
        "start",
        "end",
        "excerpt_start",
        "excerpt_end",
        "sha256",
        "raw_fields",
    }
    if not required.issubset(payload):
        return None
    raw_match = payload.get("raw_match")
    start, end = _integer(payload.get("start")), _integer(payload.get("end"))
    local_start = _integer(payload.get("excerpt_start"))
    local_end = _integer(payload.get("excerpt_end"))
    digest = payload.get("sha256")
    if (
        not isinstance(raw_match, str)
        or start is None
        or end is None
        or local_start is None
        or local_end is None
        or start != excerpt_absolute_start + local_start
        or end != excerpt_absolute_start + local_end
        or end - start != len(raw_match)
        or local_start < 0
        or local_end > len(excerpt)
        or excerpt[local_start:local_end] != raw_match
        or digest != sha256_text(raw_match)
    ):
        return None
    fields = payload.get("raw_fields")
    if not isinstance(fields, Mapping):
        return None
    if any(_field_value(fields, name, raw_match) is None for name in ("name", "amount", "ratio")):
        return None
    return raw_match, fields


def _header_span_matches(
    payload: object,
    *,
    excerpt: str,
    excerpt_absolute_start: int,
) -> bool:
    if not isinstance(payload, Mapping) or set(payload) != {
        "text",
        "start",
        "end",
        "excerpt_start",
        "excerpt_end",
        "sha256",
    }:
        return False
    text = payload.get("text")
    start, end = _integer(payload.get("start")), _integer(payload.get("end"))
    local_start = _integer(payload.get("excerpt_start"))
    local_end = _integer(payload.get("excerpt_end"))
    return bool(
        isinstance(text, str)
        and text
        and start is not None
        and end is not None
        and local_start is not None
        and local_end is not None
        and start == excerpt_absolute_start + local_start
        and end == excerpt_absolute_start + local_end
        and end - start == len(text)
        and local_start == 0
        and 0 <= local_end <= len(excerpt)
        and excerpt[local_start:local_end] == text
        and payload.get("sha256") == sha256_text(text)
        and re.search(r"비\s*중", text) is not None
    )


def revenue_row_evidence_matches(
    evidence: str,
    *,
    cited_source_text: str,
    filing_text: str | None = None,
    headers: Sequence[str],
    public_row: Sequence[str],
    raw_row: Sequence[str] | None = None,
    expected_selected_index: int | None = None,
    expected_row_count: int | None = None,
) -> bool:
    """공개 매출 행이 인용 조각과 실제 공시 원문에서 왔는지 재검증한다.

    ``filing_text``를 받은 생산 경계에서는 evidence 안의 지문만 서로 맞는
    순환 검증을 허용하지 않는다. 원문 전체 지문과 절대 범위를 다시 대조한다.
    저장본 검증처럼 원문 전체를 더는 갖고 있지 않은 호출자는 생략할 수 있다.
    """

    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(payload, Mapping) or evidence != canonical_json(payload):
        return False
    if set(payload) != {"schema", "extractor", "source", "table", "row", "public_fields"}:
        return False
    if payload.get("schema") != REVENUE_ROW_PROVENANCE_SCHEMA:
        return False
    extractor = payload.get("extractor")
    if extractor != {
        "name": REVENUE_EXTRACTOR_NAME,
        "version": REVENUE_EXTRACTOR_VERSION,
    }:
        return False
    source = payload.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "filing_sha256",
        "excerpt",
        "start",
        "end",
        "sha256",
    }:
        return False
    excerpt = source.get("excerpt")
    source_start, source_end = _integer(source.get("start")), _integer(source.get("end"))
    if (
        not isinstance(excerpt, str)
        or not excerpt
        or source_start is None
        or source_end is None
        or source_start < 0
        or source_end - source_start != len(excerpt)
        or source.get("sha256") != sha256_text(excerpt)
        or not isinstance(source.get("filing_sha256"), str)
        or _HEX_64_RE.fullmatch(str(source.get("filing_sha256"))) is None
        or excerpt not in str(cited_source_text)
    ):
        return False
    if filing_text is not None and (
        type(filing_text) is not str
        or source.get("filing_sha256") != sha256_text(filing_text)
        or source_end > len(filing_text)
        or filing_text[source_start:source_end] != excerpt
    ):
        return False
    row = payload.get("row")
    row_match = _raw_span_matches(
        row,
        excerpt=excerpt,
        excerpt_absolute_start=source_start,
    )
    if row_match is None or not isinstance(row, Mapping):
        return False
    raw_match, fields = row_match
    structural_match = REVENUE_ROW_RE.fullmatch(raw_match)
    if structural_match is None:
        return False
    for group_index, field_name in enumerate(("name", "amount", "ratio"), start=1):
        field = fields.get(field_name)
        if (
            not isinstance(field, Mapping)
            or field.get("value") != structural_match.group(group_index)
            or field.get("start") != structural_match.start(group_index)
            or field.get("end") != structural_match.end(group_index)
        ):
            return False
    source_index = _integer(row.get("source_index"))
    selected_index = _integer(row.get("selected_index"))
    selection = row.get("selection")
    if (
        source_index is None
        or selected_index is None
        or source_index < 0
        or selected_index < 0
        or selection not in {"first-current-period-pair", "explicit-total-row"}
        or (
            expected_selected_index is not None
            and selected_index != expected_selected_index
        )
    ):
        return False
    table = payload.get("table")
    if not isinstance(table, Mapping) or set(table) != {
        "axis",
        "complete",
        "row_count",
        "max_rows",
        "header",
        "total",
    }:
        return False
    row_count = _integer(table.get("row_count"))
    axis_header = table.get("header")
    axis_header_text = (
        axis_header.get("text") if isinstance(axis_header, Mapping) else ""
    )
    if (
        table.get("axis") not in REVENUE_AXES
        or _text_axis(str(axis_header_text)) != table.get("axis")
        or table.get("complete") is not True
        or table.get("max_rows") != REVENUE_MAX_ROWS
        or row_count is None
        or not 2 <= row_count <= REVENUE_MAX_ROWS
        or (expected_row_count is not None and row_count != expected_row_count)
        or selected_index > row_count
        or (selection == "explicit-total-row" and selected_index != row_count)
        or (selection != "explicit-total-row" and selected_index >= row_count)
    ):
        return False
    if not _header_span_matches(
        table.get("header"),
        excerpt=excerpt,
        excerpt_absolute_start=source_start,
    ):
        return False
    total_match = _raw_span_matches(
        table.get("total"),
        excerpt=excerpt,
        excerpt_absolute_start=source_start,
    )
    if total_match is None:
        return False
    header_payload = table.get("header")
    total_payload = table.get("total")
    if (
        not isinstance(header_payload, Mapping)
        or not isinstance(total_payload, Mapping)
        or _integer(header_payload.get("end")) is None
        or _integer(row.get("start")) is None
        or _integer(row.get("end")) is None
        or _integer(total_payload.get("start")) is None
        or _integer(total_payload.get("excerpt_end")) != len(excerpt)
        or int(header_payload["end"]) > int(row["start"])
        or (
            selection == "explicit-total-row"
            and (
                row.get("start") != total_payload.get("start")
                or row.get("end") != total_payload.get("end")
            )
        )
        or (
            selection != "explicit-total-row"
            and int(row["end"]) > int(total_payload["start"])
        )
    ):
        return False
    _, total_fields = total_match
    total_raw_match = str(table["total"]["raw_match"])
    total_structural_match = REVENUE_ROW_RE.fullmatch(total_raw_match)
    if total_structural_match is None:
        return False
    for group_index, field_name in enumerate(("name", "amount", "ratio"), start=1):
        field = total_fields.get(field_name)
        if (
            not isinstance(field, Mapping)
            or field.get("value") != total_structural_match.group(group_index)
            or field.get("start") != total_structural_match.start(group_index)
            or field.get("end") != total_structural_match.end(group_index)
        ):
            return False
    total_name = _field_value(total_fields, "name", total_raw_match)
    total_ratio = _field_value(total_fields, "ratio", total_raw_match)
    try:
        total_value = Decimal(str(total_ratio))
    except (InvalidOperation, ValueError):
        return False
    if (
        not is_revenue_total_name(normalize_revenue_name(str(total_name)))
        or total_value != Decimal(100)
    ):
        return False
    name = _field_value(fields, "name", raw_match)
    amount = _field_value(fields, "amount", raw_match)
    ratio = _field_value(fields, "ratio", raw_match)
    if name is None or amount is None or ratio is None:
        return False
    expected_fields = {
        REVENUE_HEADERS[0]: normalize_revenue_name(name),
        REVENUE_HEADERS[1]: amount,
        REVENUE_HEADERS[2]: f"{ratio}%",
    }
    if payload.get("public_fields") != expected_fields:
        return False
    normalized_headers = tuple(" ".join(str(value).split()) for value in headers)
    if (
        not normalized_headers
        or len(normalized_headers) != len(public_row)
        or any(header not in expected_fields for header in normalized_headers)
        or tuple(str(value) for value in public_row)
        != tuple(expected_fields[header] for header in normalized_headers)
    ):
        return False
    if raw_row is not None and tuple(str(value) for value in raw_row) != tuple(
        str(value) for value in public_row
    ):
        return False
    return True

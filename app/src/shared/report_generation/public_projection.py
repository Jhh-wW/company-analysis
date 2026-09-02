"""웹·PDF·Notion이 공유하는 공개 봉인 블록 자료형과 digest.

composer가 렌더 후 한 번 만들어 저장하는 ``PublicSectionContentBlock``·
``PublicReportProjection``·``PublicReportDigest``를 정의한다. 렌더러는 이
블록만 읽고 문자열을 새로 만들지 않는다 — 파생 문구(도식·읽는 법·3개년 띠·
표지 띠·문단 번호·검증 라벨·부록 행)를 채우는 일은 이 모듈의 몫이 아니다
(그건 ``features/report_standard/public_projection.py`` builder가 한다).

이 모듈은 shared 계층이라 feature(``composer``·``export_pdf``·``export_notion``·
``web``)를 import하지 않는다(``canonical.py:5-6``과 같은 정책). 직렬화·해시는
``models.py``의 ``canonical_value``·``canonical_json``·``canonical_sha256``
세 함수만 쓴다 — 새 직렬화기·새 해시 함수를 만들지 않는다.

불변식 I1~I8은 §02 설계 문서(``017_public_projection_design_02_설계.md``)
표를 따른다. 각 불변식은 생성 시(``__post_init__``)와 ``from_dict`` 복원
시 모두 검사되며(복원은 생성자를 다시 부르므로 자동으로 같이 검사된다),
실패는 예외 없이 전부 ``PublicProjectionError`` 로 닫힌다.

★ I3·I4는 원본 ``Report``(fact_records 전체·source_grades 전체)를 이 타입이
  들고 있지 않아 부분적으로만 검사한다 — 관측 가능한 부분(장 간 fact_id
  중복 없음·등급 기여가 참조하는 출처 번호가 citations 안에 있음)만 이
  레이어에서 닫고, ``report.fact_records``/``report.source_grades`` 원본과의
  완전한 동치는 builder(S2)가 원본 ``Report``를 들고 검사한다.
★ I6은 ``verification_label`` 이 실제 ``source_verification_label()``(feature
  계층, report_standard/section_content.py)과 같은 값인지는 이 레이어에서
  확인할 수 없다(shared는 feature를 import하지 않는다) — 이 레이어는
  구조적 완전성(빈 문자열이 아님)만 닫고, 순수 함수와의 값 동치는 S2가 확인한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Optional

from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS as SECTION_IDS,
)
from src.shared.report_generation.canonical import table_public_projection
from src.shared.report_generation.models import (
    canonical_sha256,
    canonical_value,
)


PUBLIC_PROJECTION_VERSION: Final[str] = "public-section-content-v1"

_SHA256_HEX_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")

#: canonical.py:124-177 ``_source_public_projection`` (private, import하지 않음)이
#: 만드는 Source canonical dict와 같은 28개 키. 값 타입까지 재구현하지 않고
#: 키 집합 동일성만 이 레이어의 계약으로 고정한다.
_PUBLIC_CITATION_SOURCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "number",
        "kind",
        "label",
        "disclosed_at",
        "collected_at",
        "published_at",
        "domain",
        "source_id",
        "title",
        "publisher",
        "host",
        "url",
        "document_id",
        "location",
        "source_type",
        "fact_status",
        "used_in",
        "evidence_hashes",
        "exact_evidence_hashes",
        "domain_attestation_source_id",
        "domain_attestation_evidence",
        "provenance_seal",
        "provenance_role",
        "reporting_period",
        "attachment_url",
        "domain_redirect_verification",
        "domain_redirect_from_host",
        "domain_redirect_to_host",
    }
)


class PublicProjectionError(ValueError):
    """공개 projection 자료형·불변식·canonical wire 왕복 위반."""


# ══════════════════════════════════════════════════════════
# 작은 검증 도우미 — 전부 PublicProjectionError로 닫는다
# ══════════════════════════════════════════════════════════


def _require_str(value: object, *, label: str) -> None:
    if type(value) is not str:
        raise PublicProjectionError(f"{label}은(는) 문자열이어야 합니다")


def _require_nonempty_str(value: object, *, label: str) -> None:
    _require_str(value, label=label)
    if not value.strip():
        raise PublicProjectionError(f"{label}은(는) 빈 문자열일 수 없습니다")


def _require_bool(value: object, *, label: str) -> None:
    if type(value) is not bool:
        raise PublicProjectionError(f"{label}은(는) bool이어야 합니다")


def _require_int(value: object, *, label: str, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise PublicProjectionError(f"{label}은(는) {minimum} 이상의 정수여야 합니다")


def _require_str_tuple(value: object, *, label: str) -> None:
    if type(value) is not tuple or any(type(item) is not str for item in value):
        raise PublicProjectionError(f"{label}은(는) 문자열 tuple이어야 합니다")


def _require_sha256_hex(value: object, *, label: str) -> None:
    _require_str(value, label=label)
    if _SHA256_HEX_RE.fullmatch(value) is None:
        raise PublicProjectionError(f"{label}은(는) SHA-256 64자리 16진수여야 합니다")


def _deep_canonical_safe(value: object, *, label: str) -> None:
    """중첩 Mapping·tuple 안에 float 등 canonical이 아닌 값이 없는지 확인한다.

    ★ I8 — ``models.py`` ``canonical_value``를 그대로 재사용해 float를
      막는다(float는 ``canonical_value``가 지원하지 않아 TypeError를 낸다).
      새 직렬화기를 만들지 않고 기존 함수의 실패를 이 모듈의 오류 타입으로
      바꾸기만 한다.
    """

    try:
        canonical_value(value)
    except TypeError as error:
        raise PublicProjectionError(
            f"I8: {label}에 canonical이 아닌 값(예: float)이 있습니다"
        ) from error


def _tuple_of_str(value: object, *, label: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise PublicProjectionError(f"{label}이(가) 문자열 배열이 아닙니다")
    return tuple(value)


def _tuple_of_str_tuples(value: object, *, label: str) -> tuple[tuple[str, ...], ...]:
    if type(value) is not list:
        raise PublicProjectionError(f"{label}이(가) 배열이 아닙니다")
    rows: list[tuple[str, ...]] = []
    for row in value:
        if type(row) is not list or any(type(cell) is not str for cell in row):
            raise PublicProjectionError(f"{label} 행이 문자열 배열이 아닙니다")
        rows.append(tuple(row))
    return tuple(rows)


def _fixed_width_row(row: object, *, width: int, label: str) -> tuple[object, ...]:
    if type(row) is not list or len(row) != width:
        raise PublicProjectionError(f"{label} 항목 길이가 {width}이 아닙니다")
    return tuple(row)


def _tuple_of_fixed_width(
    value: object, *, width: int, label: str
) -> tuple[tuple[object, ...], ...]:
    if type(value) is not list:
        raise PublicProjectionError(f"{label}이(가) 배열이 아닙니다")
    return tuple(_fixed_width_row(row, width=width, label=label) for row in value)


def _grade_contribution_from_list(
    value: object, *, label: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if type(value) is not list:
        raise PublicProjectionError(f"{label}이(가) 배열이 아닙니다")
    out: list[tuple[str, tuple[str, ...]]] = []
    for item in value:
        if type(item) is not list or len(item) != 2 or type(item[0]) is not str:
            raise PublicProjectionError(f"{label} 항목이 (번호,등급들) 모양이 아닙니다")
        out.append((item[0], _tuple_of_str(item[1], label=f"{label} 등급")))
    return tuple(out)


def _require_grade_contribution_shape(value: object, *, label: str) -> None:
    if type(value) is not tuple or any(
        type(item) is not tuple
        or len(item) != 2
        or type(item[0]) is not str
        or type(item[1]) is not tuple
        or any(type(grade) is not str for grade in item[1])
        for item in value
    ):
        raise PublicProjectionError(f"{label} 모양이 잘못됐습니다")


# ══════════════════════════════════════════════════════════
# 표·도식·띠 — 장 표시(display) 안의 파생 블록
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PublicTableBlock:
    """§4 #11 — 웹·PDF·Notion이 같은 표를 그리게 하는 봉인된 표 한 장."""

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    cite: str
    numeric: bool
    presentation: str
    display_unit: str
    manifest_ref: str

    def __post_init__(self) -> None:
        _require_str(self.caption, label="공개 표 caption")
        _require_str_tuple(self.headers, label="공개 표 headers")
        if type(self.rows) is not tuple or any(
            type(row) is not tuple or any(type(cell) is not str for cell in row)
            for row in self.rows
        ):
            raise PublicProjectionError("공개 표 rows는 문자열 tuple의 tuple이어야 합니다")
        _require_str(self.cite, label="공개 표 cite")
        _require_bool(self.numeric, label="공개 표 numeric")
        _require_str(self.presentation, label="공개 표 presentation")
        _require_str(self.display_unit, label="공개 표 display_unit")
        _require_sha256_hex(self.manifest_ref, label="공개 표 manifest_ref")
        # I7 — 지문 A의 표 7필드 projection(canonical.py:77-90)과 정확히 같아야
        # 한다. 새로 만들지 않고 기존 table_public_projection을 그대로 재사용한다.
        projected = table_public_projection(self)
        expected = {
            "caption": self.caption,
            "headers": list(self.headers),
            "rows": [list(row) for row in self.rows],
            "cite": self.cite,
            "numeric": self.numeric,
            "presentation": self.presentation,
            "display_unit": self.display_unit,
        }
        if projected != expected:
            raise PublicProjectionError(
                "I7: 공개 표 7필드가 table_public_projection과 다릅니다"
            )


def public_table_block_to_dict(value: PublicTableBlock) -> dict[str, object]:
    if type(value) is not PublicTableBlock:
        raise PublicProjectionError("정확한 PublicTableBlock이 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("공개 표 블록을 canonical 객체로 만들 수 없습니다")
    return payload


def public_table_block_from_dict(data: Mapping[str, object]) -> PublicTableBlock:
    expected = {
        "caption",
        "headers",
        "rows",
        "cite",
        "numeric",
        "presentation",
        "display_unit",
        "manifest_ref",
    }
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("공개 표 블록의 key 또는 객체 형식이 계약과 다릅니다")
    value = PublicTableBlock(
        caption=data["caption"],
        headers=_tuple_of_str(data["headers"], label="공개 표 headers"),
        rows=_tuple_of_str_tuples(data["rows"], label="공개 표 rows"),
        cite=data["cite"],
        numeric=data["numeric"],
        presentation=data["presentation"],
        display_unit=data["display_unit"],
        manifest_ref=data["manifest_ref"],
    )
    if public_table_block_to_dict(value) != data:
        raise PublicProjectionError("공개 표 블록이 canonical wire 왕복과 다릅니다")
    return value


def _series_row(item: object, *, label: str) -> tuple[str, str, tuple[Mapping[str, object], ...]]:
    if type(item) is not list or len(item) != 3:
        raise PublicProjectionError(f"{label} 항목이 (label,risk,points) 모양이 아닙니다")
    label_value, risk_value, points_value = item
    if type(label_value) is not str or type(risk_value) is not str:
        raise PublicProjectionError(f"{label} label/risk가 문자열이 아닙니다")
    if type(points_value) is not list or any(
        type(point) is not dict for point in points_value
    ):
        raise PublicProjectionError(f"{label} points가 객체 배열이 아닙니다")
    return (label_value, risk_value, tuple(points_value))


def _cards_row(item: object, *, label: str) -> tuple[str, Mapping[str, object]]:
    if (
        type(item) is not list
        or len(item) != 2
        or type(item[0]) is not str
        or type(item[1]) is not dict
    ):
        raise PublicProjectionError(f"{label} 항목이 (title,fields) 모양이 아닙니다")
    return (item[0], item[1])


@dataclass(frozen=True)
class PublicVisualBlock:
    """§4 #12~16 — ``table_visualization`` 결과를 봉인한 도식 한 개."""

    table_index: int
    kind: str
    caption: str
    unit: str
    note: str
    reading: str
    items: tuple[tuple[str, str, str, bool], ...]
    series: tuple[tuple[str, str, tuple[Mapping[str, object], ...]], ...]
    flows: tuple[tuple[str, ...], ...]
    cards: tuple[tuple[str, Mapping[str, object]], ...]

    def __post_init__(self) -> None:
        _require_int(self.table_index, label="도식 table_index", minimum=0)
        for name in ("kind", "caption", "unit", "note", "reading"):
            _require_str(getattr(self, name), label=f"도식 {name}")
        if type(self.items) is not tuple or any(
            type(item) is not tuple
            or len(item) != 4
            or type(item[0]) is not str
            or type(item[1]) is not str
            or type(item[2]) is not str
            or type(item[3]) is not bool
            for item in self.items
        ):
            raise PublicProjectionError(
                "도식 items는 (label,display,ratio_text,below) 4튜플이어야 합니다"
            )
        if type(self.series) is not tuple or any(
            type(item) is not tuple
            or len(item) != 3
            or type(item[0]) is not str
            or type(item[1]) is not str
            or type(item[2]) is not tuple
            or any(not isinstance(point, Mapping) for point in item[2])
            for item in self.series
        ):
            raise PublicProjectionError(
                "도식 series는 (label,risk,points) 3튜플이어야 합니다"
            )
        if type(self.flows) is not tuple or any(
            type(row) is not tuple or any(type(cell) is not str for cell in row)
            for row in self.flows
        ):
            raise PublicProjectionError("도식 flows는 문자열 tuple의 tuple이어야 합니다")
        if type(self.cards) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or not isinstance(item[1], Mapping)
            for item in self.cards
        ):
            raise PublicProjectionError("도식 cards는 (title,fields) 2튜플이어야 합니다")
        # I8 — ratio_text 등은 이미 str 타입 검사를 통과했지만, series의
        # points·cards의 fields는 느슨한 Mapping이라 그 안에 float가 숨을 수
        # 있다. canonical_value 재사용으로 깊게 한 번 더 막는다.
        _deep_canonical_safe(self, label="도식 블록")


def public_visual_block_to_dict(value: PublicVisualBlock) -> dict[str, object]:
    if type(value) is not PublicVisualBlock:
        raise PublicProjectionError("정확한 PublicVisualBlock이 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("도식 블록을 canonical 객체로 만들 수 없습니다")
    return payload


def public_visual_block_from_dict(data: Mapping[str, object]) -> PublicVisualBlock:
    expected = {
        "table_index",
        "kind",
        "caption",
        "unit",
        "note",
        "reading",
        "items",
        "series",
        "flows",
        "cards",
    }
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("도식 블록의 key 또는 객체 형식이 계약과 다릅니다")
    series_raw = data["series"]
    cards_raw = data["cards"]
    if type(series_raw) is not list or type(cards_raw) is not list:
        raise PublicProjectionError("도식 블록 series/cards가 배열이 아닙니다")
    value = PublicVisualBlock(
        table_index=data["table_index"],
        kind=data["kind"],
        caption=data["caption"],
        unit=data["unit"],
        note=data["note"],
        reading=data["reading"],
        items=_tuple_of_fixed_width(data["items"], width=4, label="도식 items"),
        series=tuple(_series_row(item, label="도식 series") for item in series_raw),
        flows=_tuple_of_str_tuples(data["flows"], label="도식 flows"),
        cards=tuple(_cards_row(item, label="도식 cards") for item in cards_raw),
    )
    if public_visual_block_to_dict(value) != data:
        raise PublicProjectionError("도식 블록이 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicPeriodSummaryBlock:
    """§4 #17 — 웹만 있던 3개년 변화 요약 띠를 채널 공통 필드로 봉인."""

    title: str
    cite: str
    #: 각 항목 = (label, base_period, base_value, latest_period, latest_value,
    #: unit, change, change_kind, direction, note) — 전부 표시용 문자열.
    items: tuple[
        tuple[str, str, str, str, str, str, str, str, str, str], ...
    ]

    def __post_init__(self) -> None:
        _require_str(self.title, label="3개년 띠 title")
        _require_str(self.cite, label="3개년 띠 cite")
        if type(self.items) is not tuple or any(
            type(item) is not tuple
            or len(item) != 10
            or any(type(cell) is not str for cell in item)
            for item in self.items
        ):
            raise PublicProjectionError(
                "3개년 띠 items는 10개 문자열 필드 tuple이어야 합니다"
            )


def public_period_summary_block_to_dict(
    value: PublicPeriodSummaryBlock,
) -> dict[str, object]:
    if type(value) is not PublicPeriodSummaryBlock:
        raise PublicProjectionError("정확한 PublicPeriodSummaryBlock이 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("3개년 띠를 canonical 객체로 만들 수 없습니다")
    return payload


def public_period_summary_block_from_dict(
    data: Mapping[str, object],
) -> PublicPeriodSummaryBlock:
    expected = {"title", "cite", "items"}
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("3개년 띠의 key 또는 객체 형식이 계약과 다릅니다")
    value = PublicPeriodSummaryBlock(
        title=data["title"],
        cite=data["cite"],
        items=_tuple_of_fixed_width(data["items"], width=10, label="3개년 띠 items"),
    )
    if public_period_summary_block_to_dict(value) != data:
        raise PublicProjectionError("3개년 띠가 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicCoverMetricsBlock:
    """§4 #3 — 표지 실적 띠."""

    title: str
    cite: str
    #: 각 항목 = (label, value, unit).
    items: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        _require_str(self.title, label="표지 실적 title")
        _require_str(self.cite, label="표지 실적 cite")
        if type(self.items) is not tuple or any(
            type(item) is not tuple
            or len(item) != 3
            or any(type(cell) is not str for cell in item)
            for item in self.items
        ):
            raise PublicProjectionError("표지 실적 items는 3개 문자열 필드 tuple이어야 합니다")


def public_cover_metrics_block_to_dict(
    value: PublicCoverMetricsBlock,
) -> dict[str, object]:
    if type(value) is not PublicCoverMetricsBlock:
        raise PublicProjectionError("정확한 PublicCoverMetricsBlock이 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("표지 실적 띠를 canonical 객체로 만들 수 없습니다")
    return payload


def public_cover_metrics_block_from_dict(
    data: Mapping[str, object],
) -> PublicCoverMetricsBlock:
    expected = {"title", "cite", "items"}
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("표지 실적 띠의 key 또는 객체 형식이 계약과 다릅니다")
    value = PublicCoverMetricsBlock(
        title=data["title"],
        cite=data["cite"],
        items=_tuple_of_fixed_width(data["items"], width=3, label="표지 실적 items"),
    )
    if public_cover_metrics_block_to_dict(value) != data:
        raise PublicProjectionError("표지 실적 띠가 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicSummaryRow:
    """§4 #5 — 핵심 요약 표 한 행."""

    ordinal: str
    topic: str
    section_display_number: str
    text: str
    section_id: str

    def __post_init__(self) -> None:
        for name in ("ordinal", "topic", "section_display_number", "text", "section_id"):
            _require_str(getattr(self, name), label=f"요약 행 {name}")
        if self.section_id not in SECTION_IDS:
            raise PublicProjectionError("요약 행 section_id가 정본 장 목록 밖입니다")


def public_summary_row_to_dict(value: PublicSummaryRow) -> dict[str, object]:
    if type(value) is not PublicSummaryRow:
        raise PublicProjectionError("정확한 PublicSummaryRow가 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("요약 행을 canonical 객체로 만들 수 없습니다")
    return payload


def public_summary_row_from_dict(data: Mapping[str, object]) -> PublicSummaryRow:
    expected = {"ordinal", "topic", "section_display_number", "text", "section_id"}
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("요약 행의 key 또는 객체 형식이 계약과 다릅니다")
    value = PublicSummaryRow(
        ordinal=data["ordinal"],
        topic=data["topic"],
        section_display_number=data["section_display_number"],
        text=data["text"],
        section_id=data["section_id"],
    )
    if public_summary_row_to_dict(value) != data:
        raise PublicProjectionError("요약 행이 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicCitationRow:
    """§4 #18·19 — 부록 한 행의 표시 문자열과 원자료 Source를 함께 봉인."""

    number: int
    label_display: str
    url: str
    status_display: str
    verification_label: str
    location: str
    used_in_display: str
    source: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_int(self.number, label="부록 행 번호", minimum=1)
        for name in ("label_display", "url", "status_display", "location", "used_in_display"):
            _require_str(getattr(self, name), label=f"부록 행 {name}")
        # I6(부분) — 순수 함수 source_verification_label과의 값 동치는 feature
        # 계층 함수라 shared에서 확인할 수 없다. 이 레이어는 구조적 완전성만 닫는다.
        _require_nonempty_str(self.verification_label, label="부록 행 verification_label")
        if not isinstance(self.source, Mapping) or set(self.source) != _PUBLIC_CITATION_SOURCE_FIELDS:
            raise PublicProjectionError(
                "부록 행 source가 Source canonical dict 28필드와 다릅니다"
            )
        # tuple로 만들어 넣든 JSON 왕복으로 list가 되어 들어오든 저장 형태를
        # canonical_value 결과 하나로 고정한다 — 그래야 직접 생성한 값과
        # from_dict로 복원한 값이 ``==``로 같아진다(I8도 이 재사용으로 같이 막는다).
        try:
            normalized_source = canonical_value(self.source)
        except TypeError as error:
            raise PublicProjectionError(
                "I8: 부록 행 source에 canonical이 아닌 값(예: float)이 있습니다"
            ) from error
        object.__setattr__(self, "source", normalized_source)


def public_citation_row_to_dict(value: PublicCitationRow) -> dict[str, object]:
    if type(value) is not PublicCitationRow:
        raise PublicProjectionError("정확한 PublicCitationRow가 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("부록 행을 canonical 객체로 만들 수 없습니다")
    return payload


def public_citation_row_from_dict(data: Mapping[str, object]) -> PublicCitationRow:
    expected = {
        "number",
        "label_display",
        "url",
        "status_display",
        "verification_label",
        "location",
        "used_in_display",
        "source",
    }
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("부록 행의 key 또는 객체 형식이 계약과 다릅니다")
    source_raw = data["source"]
    if type(source_raw) is not dict:
        raise PublicProjectionError("부록 행 source가 객체가 아닙니다")
    value = PublicCitationRow(
        number=data["number"],
        label_display=data["label_display"],
        url=data["url"],
        status_display=data["status_display"],
        verification_label=data["verification_label"],
        location=data["location"],
        used_in_display=data["used_in_display"],
        source=source_raw,
    )
    if public_citation_row_to_dict(value) != data:
        raise PublicProjectionError("부록 행이 canonical wire 왕복과 다릅니다")
    return value


# ══════════════════════════════════════════════════════════
# 장(section) 봉인 블록 — display + ledger
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PublicSectionDisplay:
    """한 장에서 실제로 보이는 것 — 문단·문장·표·도식·3개년 띠."""

    cell: str
    display_number: str
    title: str
    tag: str
    #: (ordinal, text).
    paragraphs: tuple[tuple[str, str], ...]
    #: (text, cite).
    sentences: tuple[tuple[str, str], ...]
    empty_reason: str
    guidance_lines: tuple[str, ...]
    tables: tuple[PublicTableBlock, ...]
    visuals: tuple[PublicVisualBlock, ...]
    period_summary: Optional[PublicPeriodSummaryBlock]

    def __post_init__(self) -> None:
        if self.cell not in SECTION_IDS:
            raise PublicProjectionError("장 표시 cell이 정본 장 목록 밖입니다")
        for name in ("display_number", "title", "tag", "empty_reason"):
            _require_str(getattr(self, name), label=f"장 표시 {name}")
        _require_str_tuple(self.guidance_lines, label="장 표시 guidance_lines")
        if type(self.paragraphs) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
            for item in self.paragraphs
        ):
            raise PublicProjectionError("장 표시 paragraphs는 (ordinal,text) 2튜플이어야 합니다")
        if type(self.sentences) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
            for item in self.sentences
        ):
            raise PublicProjectionError("장 표시 sentences는 (text,cite) 2튜플이어야 합니다")
        # I2 — 문단을 이어붙인 글자와 문장을 이어붙인 글자가 같아야 한다.
        paragraph_text = " ".join(text for _ordinal, text in self.paragraphs)
        sentence_text = " ".join(text for text, _cite in self.sentences)
        if paragraph_text != sentence_text:
            raise PublicProjectionError("I2: 문단과 문장을 이어붙인 글자가 다릅니다")
        if type(self.tables) is not tuple or any(
            type(item) is not PublicTableBlock for item in self.tables
        ):
            raise PublicProjectionError("장 표시 tables는 PublicTableBlock tuple이어야 합니다")
        if type(self.visuals) is not tuple or any(
            type(item) is not PublicVisualBlock for item in self.visuals
        ):
            raise PublicProjectionError("장 표시 visuals는 PublicVisualBlock tuple이어야 합니다")
        # I5 — 도식은 존재하는 표를 정확히 하나씩만 가리키고, 그 표의
        # presentation이 "table"(순수 표)이 아니어야 한다(그래프·표 동시 반복 금지).
        used_indexes: set[int] = set()
        for visual in self.visuals:
            if not 0 <= visual.table_index < len(self.tables):
                raise PublicProjectionError("I5: 도식 table_index가 가리키는 표가 없습니다")
            if visual.table_index in used_indexes:
                raise PublicProjectionError("I5: 같은 표를 두 도식이 동시에 가리킵니다")
            used_indexes.add(visual.table_index)
            if self.tables[visual.table_index].presentation == "table":
                raise PublicProjectionError(
                    "I5: presentation이 table인 표에는 도식이 있을 수 없습니다"
                )
        if self.period_summary is not None and type(self.period_summary) is not PublicPeriodSummaryBlock:
            raise PublicProjectionError("장 표시 period_summary 타입이 잘못됐습니다")


def public_section_display_to_dict(value: PublicSectionDisplay) -> dict[str, object]:
    if type(value) is not PublicSectionDisplay:
        raise PublicProjectionError("정확한 PublicSectionDisplay가 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("장 표시를 canonical 객체로 만들 수 없습니다")
    return payload


def public_section_display_from_dict(data: Mapping[str, object]) -> PublicSectionDisplay:
    expected = {
        "cell",
        "display_number",
        "title",
        "tag",
        "paragraphs",
        "sentences",
        "empty_reason",
        "guidance_lines",
        "tables",
        "visuals",
        "period_summary",
    }
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("장 표시의 key 또는 객체 형식이 계약과 다릅니다")
    tables_raw = data["tables"]
    visuals_raw = data["visuals"]
    if type(tables_raw) is not list or type(visuals_raw) is not list:
        raise PublicProjectionError("장 표시 tables/visuals가 배열이 아닙니다")
    period_summary_raw = data["period_summary"]
    if period_summary_raw is None:
        period_summary: Optional[PublicPeriodSummaryBlock] = None
    elif type(period_summary_raw) is dict:
        period_summary = public_period_summary_block_from_dict(period_summary_raw)
    else:
        raise PublicProjectionError("장 표시 period_summary가 객체 또는 null이 아닙니다")
    value = PublicSectionDisplay(
        cell=data["cell"],
        display_number=data["display_number"],
        title=data["title"],
        tag=data["tag"],
        paragraphs=_tuple_of_fixed_width(data["paragraphs"], width=2, label="장 표시 paragraphs"),
        sentences=_tuple_of_fixed_width(data["sentences"], width=2, label="장 표시 sentences"),
        empty_reason=data["empty_reason"],
        guidance_lines=_tuple_of_str(data["guidance_lines"], label="장 표시 guidance_lines"),
        tables=tuple(public_table_block_from_dict(item) for item in tables_raw),
        visuals=tuple(public_visual_block_from_dict(item) for item in visuals_raw),
        period_summary=period_summary,
    )
    if public_section_display_to_dict(value) != data:
        raise PublicProjectionError("장 표시가 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicSectionLedger:
    """이 장이 기여한 감사 장부 — FactRecord·인용 등급 기여."""

    fact_ids: tuple[str, ...]
    fact_records: tuple[Mapping[str, object], ...]
    source_grade_contribution: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        _require_str_tuple(self.fact_ids, label="장 ledger fact_ids")
        if type(self.fact_records) is not tuple or any(
            not isinstance(item, Mapping) for item in self.fact_records
        ):
            raise PublicProjectionError("장 ledger fact_records는 Mapping tuple이어야 합니다")
        # I3(전반부) — ledger.fact_ids 순서가 fact_records의 fact_id 순서와 같아야 한다.
        record_fact_ids = tuple(
            str(record.get("fact_id", "")) for record in self.fact_records
        )
        if self.fact_ids != record_fact_ids:
            raise PublicProjectionError("I3: ledger fact_ids가 fact_records 순서와 다릅니다")
        _require_grade_contribution_shape(
            self.source_grade_contribution, label="장 ledger source_grade_contribution"
        )
        _deep_canonical_safe(self.fact_records, label="장 ledger fact_records")


def public_section_ledger_to_dict(value: PublicSectionLedger) -> dict[str, object]:
    if type(value) is not PublicSectionLedger:
        raise PublicProjectionError("정확한 PublicSectionLedger가 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("장 ledger를 canonical 객체로 만들 수 없습니다")
    return payload


def public_section_ledger_from_dict(data: Mapping[str, object]) -> PublicSectionLedger:
    expected = {"fact_ids", "fact_records", "source_grade_contribution"}
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("장 ledger의 key 또는 객체 형식이 계약과 다릅니다")
    fact_records_raw = data["fact_records"]
    if type(fact_records_raw) is not list or any(
        type(item) is not dict for item in fact_records_raw
    ):
        raise PublicProjectionError("장 ledger fact_records가 객체 배열이 아닙니다")
    value = PublicSectionLedger(
        fact_ids=_tuple_of_str(data["fact_ids"], label="장 ledger fact_ids"),
        fact_records=tuple(fact_records_raw),
        source_grade_contribution=_grade_contribution_from_list(
            data["source_grade_contribution"],
            label="장 ledger source_grade_contribution",
        ),
    )
    if public_section_ledger_to_dict(value) != data:
        raise PublicProjectionError("장 ledger가 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicSectionContentBlock:
    """한 장 = 한 봉인 블록. display와 ledger를 나누고 둘 다 덮는 digest를 싣는다."""

    version: str
    display: PublicSectionDisplay
    ledger: PublicSectionLedger
    display_sha256: str = field(init=False)
    block_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.version) is not str or self.version != PUBLIC_PROJECTION_VERSION:
            raise PublicProjectionError("공개 봉인 블록 버전이 지원하지 않는 값입니다")
        if type(self.display) is not PublicSectionDisplay:
            raise PublicProjectionError("공개 봉인 블록 display 타입이 잘못됐습니다")
        if type(self.ledger) is not PublicSectionLedger:
            raise PublicProjectionError("공개 봉인 블록 ledger 타입이 잘못됐습니다")
        object.__setattr__(self, "display_sha256", canonical_sha256(self.display))
        object.__setattr__(
            self,
            "block_sha256",
            canonical_sha256(
                {
                    "version": self.version,
                    "display": self.display,
                    "ledger": self.ledger,
                }
            ),
        )


def public_section_content_block_to_dict(
    value: PublicSectionContentBlock,
) -> dict[str, object]:
    if type(value) is not PublicSectionContentBlock:
        raise PublicProjectionError("정확한 PublicSectionContentBlock이 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("공개 봉인 블록을 canonical 객체로 만들 수 없습니다")
    # display_sha256·block_sha256은 field(init=False)라 canonical_value의
    # dataclass 순회(item.init만 포함)에서 자동으로 빠진다 — transport에는
    # 반드시 실어야 하므로 여기서 명시적으로 채운다(models.py의
    # assessment_sha256 처리와 같은 패턴).
    payload["display_sha256"] = value.display_sha256
    payload["block_sha256"] = value.block_sha256
    return payload


def public_section_content_block_from_dict(
    data: Mapping[str, object],
) -> PublicSectionContentBlock:
    expected = {"version", "display", "ledger", "display_sha256", "block_sha256"}
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("공개 봉인 블록의 key 또는 객체 형식이 계약과 다릅니다")
    display_raw = data["display"]
    ledger_raw = data["ledger"]
    if type(display_raw) is not dict or type(ledger_raw) is not dict:
        raise PublicProjectionError("공개 봉인 블록 display/ledger가 객체가 아닙니다")
    value = PublicSectionContentBlock(
        version=data["version"],
        display=public_section_display_from_dict(display_raw),
        ledger=public_section_ledger_from_dict(ledger_raw),
    )
    # 로드 시 저장된 digest를 믿지 않고 재계산과 대조한다(canonical.py:552-557
    # docstring 원칙 — self-checksum만으로는 대체 위조를 못 막는다).
    _require_sha256_hex(data.get("display_sha256"), label="저장된 display_sha256")
    _require_sha256_hex(data.get("block_sha256"), label="저장된 block_sha256")
    if data["display_sha256"] != value.display_sha256:
        raise PublicProjectionError("저장된 display_sha256이 재계산 값과 다릅니다")
    if data["block_sha256"] != value.block_sha256:
        raise PublicProjectionError("저장된 block_sha256이 재계산 값과 다릅니다")
    if public_section_content_block_to_dict(value) != data:
        raise PublicProjectionError("공개 봉인 블록이 canonical wire 왕복과 다릅니다")
    return value


# ══════════════════════════════════════════════════════════
# 보고서 전체 projection과 digest
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PublicReportProjection:
    """보고서 전체의 공개 projection — 헤더·표지·요약·아홉 장·부록."""

    version: str
    header: Mapping[str, object]
    cover_metrics: Optional[PublicCoverMetricsBlock]
    summary: tuple[PublicSummaryRow, ...]
    sections: tuple[PublicSectionContentBlock, ...]
    citations: tuple[PublicCitationRow, ...]
    summary_source_grade_contribution: tuple[tuple[str, tuple[str, ...]], ...]
    #: (title, detail).
    grade_notice: tuple[str, str]

    def __post_init__(self) -> None:
        if type(self.version) is not str or self.version != PUBLIC_PROJECTION_VERSION:
            raise PublicProjectionError("공개 projection 버전이 지원하지 않는 값입니다")
        if not isinstance(self.header, Mapping):
            raise PublicProjectionError("공개 projection header가 Mapping이 아닙니다")
        _deep_canonical_safe(self.header, label="공개 projection header")
        if self.cover_metrics is not None and type(self.cover_metrics) is not PublicCoverMetricsBlock:
            raise PublicProjectionError("공개 projection cover_metrics 타입이 잘못됐습니다")
        if type(self.summary) is not tuple or any(
            type(item) is not PublicSummaryRow for item in self.summary
        ):
            raise PublicProjectionError("공개 projection summary는 PublicSummaryRow tuple이어야 합니다")
        if type(self.sections) is not tuple or any(
            type(item) is not PublicSectionContentBlock for item in self.sections
        ):
            raise PublicProjectionError(
                "공개 projection sections는 PublicSectionContentBlock tuple이어야 합니다"
            )
        # I1 — 장 순서·cell이 정본 아홉 장(SECTION_IDS)과 정확히 같아야 한다.
        if tuple(block.display.cell for block in self.sections) != SECTION_IDS:
            raise PublicProjectionError(
                "I1: 공개 projection sections 순서가 정본 장 목록과 다릅니다"
            )
        if type(self.citations) is not tuple or any(
            type(item) is not PublicCitationRow for item in self.citations
        ):
            raise PublicProjectionError(
                "공개 projection citations는 PublicCitationRow tuple이어야 합니다"
            )
        _require_grade_contribution_shape(
            self.summary_source_grade_contribution,
            label="공개 projection summary_source_grade_contribution",
        )
        if (
            type(self.grade_notice) is not tuple
            or len(self.grade_notice) != 2
            or type(self.grade_notice[0]) is not str
            or type(self.grade_notice[1]) is not str
        ):
            raise PublicProjectionError(
                "공개 projection grade_notice는 (title,detail) 2튜플이어야 합니다"
            )

        # I3(후반부) — 서로 다른 장의 ledger가 같은 fact_id를 나눠 갖지 않는다.
        # (원본 report.fact_records 전체와의 완전한 동치는 S2 builder가 원본
        #  Report로 검사한다 — 여기서는 이 프로젝션 내부에서 관측 가능한
        #  "정확히 한 장" 절반만 닫는다.)
        seen_fact_ids: dict[str, str] = {}
        for block in self.sections:
            for fact_id in block.ledger.fact_ids:
                owner = seen_fact_ids.get(fact_id)
                if owner is not None and owner != block.display.cell:
                    raise PublicProjectionError(
                        "I3: 같은 fact_id가 서로 다른 장 ledger에 겹칩니다"
                    )
                seen_fact_ids[fact_id] = block.display.cell

        # I4 — 등급 기여가 참조하는 출처 번호는 실제 citations 안에 있어야
        # 한다(병합 대상 도메인이 항상 닫혀 있음). report.source_grades와의
        # 완전한 병합 동치는 이 타입이 원본 source_grades를 들고 있지 않아
        # S2 builder가 원본 Report로 검사한다.
        citation_numbers = {str(row.number) for row in self.citations}
        for number, grades in self.summary_source_grade_contribution:
            if number not in citation_numbers:
                raise PublicProjectionError(
                    "I4: summary 등급 기여 출처 번호가 citations 밖입니다"
                )
            if len(set(grades)) != len(grades):
                raise PublicProjectionError("I4: summary 등급 기여에 중복 등급이 있습니다")
        for block in self.sections:
            for number, grades in block.ledger.source_grade_contribution:
                if number not in citation_numbers:
                    raise PublicProjectionError(
                        "I4: 장 등급 기여 출처 번호가 citations 밖입니다"
                    )
                if len(set(grades)) != len(grades):
                    raise PublicProjectionError("I4: 장 등급 기여에 중복 등급이 있습니다")


def public_report_projection_to_dict(value: PublicReportProjection) -> dict[str, object]:
    if type(value) is not PublicReportProjection:
        raise PublicProjectionError("정확한 PublicReportProjection이 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("공개 projection을 canonical 객체로 만들 수 없습니다")
    payload = dict(payload)
    # canonical_value의 일반 dataclass 순회는 각 section의 field(init=False)인
    # display_sha256·block_sha256을 건너뛴다 — 전용 to_dict로 다시 채운다.
    payload["sections"] = [
        public_section_content_block_to_dict(block) for block in value.sections
    ]
    return payload


def public_report_projection_from_dict(
    data: Mapping[str, object],
) -> PublicReportProjection:
    expected = {
        "version",
        "header",
        "cover_metrics",
        "summary",
        "sections",
        "citations",
        "summary_source_grade_contribution",
        "grade_notice",
    }
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("공개 projection의 key 또는 객체 형식이 계약과 다릅니다")
    header_raw = data["header"]
    if type(header_raw) is not dict:
        raise PublicProjectionError("공개 projection header가 객체가 아닙니다")
    cover_raw = data["cover_metrics"]
    if cover_raw is None:
        cover_metrics: Optional[PublicCoverMetricsBlock] = None
    elif type(cover_raw) is dict:
        cover_metrics = public_cover_metrics_block_from_dict(cover_raw)
    else:
        raise PublicProjectionError("공개 projection cover_metrics가 객체 또는 null이 아닙니다")
    summary_raw = data["summary"]
    citations_raw = data["citations"]
    sections_raw = data["sections"]
    if (
        type(summary_raw) is not list
        or type(citations_raw) is not list
        or type(sections_raw) is not list
    ):
        raise PublicProjectionError("공개 projection summary/citations/sections가 배열이 아닙니다")
    grade_notice_raw = data["grade_notice"]
    if (
        type(grade_notice_raw) is not list
        or len(grade_notice_raw) != 2
        or type(grade_notice_raw[0]) is not str
        or type(grade_notice_raw[1]) is not str
    ):
        raise PublicProjectionError("공개 projection grade_notice가 (title,detail) 모양이 아닙니다")
    value = PublicReportProjection(
        version=data["version"],
        header=header_raw,
        cover_metrics=cover_metrics,
        summary=tuple(public_summary_row_from_dict(item) for item in summary_raw),
        sections=tuple(
            public_section_content_block_from_dict(item) for item in sections_raw
        ),
        citations=tuple(public_citation_row_from_dict(item) for item in citations_raw),
        summary_source_grade_contribution=_grade_contribution_from_list(
            data["summary_source_grade_contribution"],
            label="공개 projection summary_source_grade_contribution",
        ),
        grade_notice=tuple(grade_notice_raw),
    )
    if public_report_projection_to_dict(value) != data:
        raise PublicProjectionError("공개 projection이 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class PublicReportDigest:
    """보고서 전체 지문 셋 — content(전체)·display(장부 제외)·장별."""

    version: str
    content_sha256: str
    display_sha256: str
    section_sha256s: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.version) is not str or self.version != PUBLIC_PROJECTION_VERSION:
            raise PublicProjectionError("공개 digest 버전이 지원하지 않는 값입니다")
        _require_sha256_hex(self.content_sha256, label="공개 digest content_sha256")
        _require_sha256_hex(self.display_sha256, label="공개 digest display_sha256")
        if type(self.section_sha256s) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
            for item in self.section_sha256s
        ):
            raise PublicProjectionError("공개 digest section_sha256s 모양이 잘못됐습니다")
        for _cell, digest in self.section_sha256s:
            _require_sha256_hex(digest, label="공개 digest section_sha256s 값")
        # section_sha256s는 SECTION_IDS 순서로 아홉 개여야 한다.
        if tuple(cell for cell, _digest in self.section_sha256s) != SECTION_IDS:
            raise PublicProjectionError(
                "공개 digest section_sha256s 순서가 정본 장 목록과 다릅니다"
            )


def build_report_digest(projection: PublicReportProjection) -> PublicReportDigest:
    """``PublicReportProjection``에서 세 지문을 계산해 digest를 만든다.

    ``content_sha256``은 ledger(감사 장부)를 포함한 projection 전체를 덮고,
    ``display_sha256``은 ledger·summary_source_grade_contribution을 뺀
    "보이는 것"만 덮는다 — FactRecord·source_grades만 바꾸면 content는
    바뀌어도 display는 불변이어야 한다는 시험이 이 구분을 비교한다.
    """

    if type(projection) is not PublicReportProjection:
        raise PublicProjectionError("공개 digest는 정확한 PublicReportProjection이 필요합니다")
    content_sha256 = canonical_sha256(projection)
    display_payload = _report_display_payload(projection)
    display_sha256 = canonical_sha256(display_payload)
    section_sha256s = tuple(
        (block.display.cell, block.block_sha256) for block in projection.sections
    )
    return PublicReportDigest(
        version=PUBLIC_PROJECTION_VERSION,
        content_sha256=content_sha256,
        display_sha256=display_sha256,
        section_sha256s=section_sha256s,
    )


def _report_display_payload(projection: PublicReportProjection) -> dict[str, object]:
    """content 중 ledger(감사 장부)만 뺀 «보이는 것» 부분집합."""

    payload = canonical_value(projection)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("공개 projection을 canonical 객체로 만들 수 없습니다")
    payload = dict(payload)
    payload.pop("summary_source_grade_contribution", None)
    sections = []
    for section in payload.get("sections", []):
        if isinstance(section, Mapping):
            section = dict(section)
            section.pop("ledger", None)
        sections.append(section)
    payload["sections"] = sections
    return payload


def public_report_digest_to_dict(value: PublicReportDigest) -> dict[str, object]:
    if type(value) is not PublicReportDigest:
        raise PublicProjectionError("정확한 PublicReportDigest가 필요합니다")
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise PublicProjectionError("공개 digest를 canonical 객체로 만들 수 없습니다")
    return payload


def public_report_digest_from_dict(data: Mapping[str, object]) -> PublicReportDigest:
    expected = {"version", "content_sha256", "display_sha256", "section_sha256s"}
    if type(data) is not dict or set(data) != expected:
        raise PublicProjectionError("공개 digest의 key 또는 객체 형식이 계약과 다릅니다")
    section_raw = data["section_sha256s"]
    if type(section_raw) is not list:
        raise PublicProjectionError("공개 digest section_sha256s가 배열이 아닙니다")
    section_sha256s: list[tuple[str, str]] = []
    for item in section_raw:
        if type(item) is not list or len(item) != 2:
            raise PublicProjectionError("공개 digest section_sha256s 항목 모양이 잘못됐습니다")
        section_sha256s.append((item[0], item[1]))
    value = PublicReportDigest(
        version=data["version"],
        content_sha256=data["content_sha256"],
        display_sha256=data["display_sha256"],
        section_sha256s=tuple(section_sha256s),
    )
    if public_report_digest_to_dict(value) != data:
        raise PublicProjectionError("공개 digest가 canonical wire 왕복과 다릅니다")
    return value


__all__ = [
    "PUBLIC_PROJECTION_VERSION",
    "PublicCitationRow",
    "PublicCoverMetricsBlock",
    "PublicPeriodSummaryBlock",
    "PublicProjectionError",
    "PublicReportDigest",
    "PublicReportProjection",
    "PublicSectionContentBlock",
    "PublicSectionDisplay",
    "PublicSectionLedger",
    "PublicSummaryRow",
    "PublicTableBlock",
    "PublicVisualBlock",
    "SECTION_IDS",
    "build_report_digest",
    "public_citation_row_from_dict",
    "public_citation_row_to_dict",
    "public_cover_metrics_block_from_dict",
    "public_cover_metrics_block_to_dict",
    "public_period_summary_block_from_dict",
    "public_period_summary_block_to_dict",
    "public_report_digest_from_dict",
    "public_report_digest_to_dict",
    "public_report_projection_from_dict",
    "public_report_projection_to_dict",
    "public_section_content_block_from_dict",
    "public_section_content_block_to_dict",
    "public_section_display_from_dict",
    "public_section_display_to_dict",
    "public_section_ledger_from_dict",
    "public_section_ledger_to_dict",
    "public_summary_row_from_dict",
    "public_summary_row_to_dict",
    "public_table_block_from_dict",
    "public_table_block_to_dict",
]

"""composer 데이터 계약 (엔진 v2 생성 단계의 고정 계약).

★ 이 파일의 세 타입(ComposedSentence·ComposedSection·ComposedReport)은
  단계3 모든 소단계가 공유하는 «고정 계약»이다. 필드 변경·삭제 금지,
  꼭 필요하면 필드 «추가»만 허용한다.
★ composer는 pipeline·report_standard를 import 하지 않는다.
  파이프라인 쪽 실측 구조(조각 dict, ReportTable)는 아래 얇은 어댑터로 받는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Optional

from src.features.composer.constants import RCEPT_DT_LENGTH, SECTION_IDS
from src.features.provenance.sources import exact_evidence_text_hash


class AskFatalError(Exception):
    """AI 호출이 «문장 내용 문제»가 아니라 «요청 전역 장애»로 죽었을 때만 쓴다.

    ★ 예산 소진(ProviderBudgetExceeded)·billing-uncertain 차단(요청 전체가
      더 못 부르는 상태)은 문장 하나의 실패가 아니라 이 요청 전체의 장애다.
      composer의 다른 모든 예외는 문장 단위로 삼켜 안내문·강등으로 바꾸지만,
      이 타입만은 어디서 잡히든 재전파해 pipeline.run_v2까지 뚫고 나가야
      한다 — 그래야 real.py가 v1과 같은 FAILED로 정직하게 끝낼 수 있다
      (전역 장애를 «검증 실패»로 오표기하지 않기 위함).

    ★ 예외의 예외 — «호출 «횟수» 상한»만은 다르다 (실측).
      돈이 떨어진 것이 아니라 «이 요청에 허락된 AI 호출 수»를 다 쓴 것이다.
      그때는 이미 만들어 둔 장·문장이 멀쩡히 손에 있는데도 보고서 전체가
      버려졌다(현대카드·우리은행 실측 — 완성된 9개 장이 통째로 사라지고
      화면에는 「보고서를 만들다 오류가 났습니다」만 남았다).
      선택적 다듬기(거짓 문장 «재작성»)에서 이 한도를 만나면, 다듬기를
      포기하고 «지금까지 만든 것»으로 끝내는 편이 정직하고 안전하다 —
      다듬지 못한 문장은 재작성 대신 «제거»되므로 검증은 오히려 더 보수적이다.
      `call_limit=True` 가 그 구분을 나른다. 돈·계정 장애는 여전히 False 다.
    """

    def __init__(self, cause: BaseException, *, call_limit: bool = False) -> None:
        self.cause = cause
        #: 호출 «횟수» 상한이라 선택적 단계를 포기하고 이어가도 되는가.
        self.call_limit = bool(call_limit)
        super().__init__(str(cause))


@dataclass(frozen=True)
class ComposedSentence:
    """작가 AI가 쓴 문장 하나."""

    #: 문장 본문 (한국어 산문)
    text: str
    #: 근거로 인용한 수집 조각 id들. 순수 «해석» 문장이면 빈 튜플 허용.
    citations: tuple[str, ...]
    #: "확인"(인용 원문에 직접 근거가 있는 사실) | "해석"(공식 자료 기반 분석·의미 부여)
    grade: str
    #: 생성 계획에서 작가가 선택한 원자 claim 자리. 누락·계약 밖 값은 빈칸이다.
    planned_claim_slot: str = ""
    #: 작가가 아니라 독립 검증기가 확정하는 상태. 기본은 절대 verified가 아니다.
    verification_state: str = "unverified"
    #: 프로그램이 구조화 원자료로 만든 claim만 갖는 손실 없는 결속 DTO.
    structured_claim: Optional["StructuredClaim"] = None
    #: 렌더가 만든 검증 FactRecord를 본문에서 글자 그대로 고른 요약에만
    #: 프로그램이 붙이는 ID. 작가 응답에서는 이 값을 읽지 않는다.
    verified_fact_id: str = ""


@dataclass(frozen=True)
class StructuredClaim:
    """텍스트 역추출 없이 공개 문장과 FactRecord를 1:1로 잇는 계약."""

    fact_id: str
    claim_slot: str
    section_owner: str
    source_fragment_id: str
    source_identity: str
    verification_state: str
    state_evidence: str
    subject_scope: str = ""
    metric: str = ""
    period_start: str = ""
    period_end: str = ""
    sign: str = ""
    unit: str = ""
    unit_dimension: str = ""
    formula: str = ""
    raw_value: str = ""
    calculation: str = ""
    display_value: str = ""
    rounding_rule: str = ""
    numeric_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlowRow:
    """사업 경로 한 줄 — «무엇으로 시작 / 회사가 하는 일 / 누구에게 닿나».

    ★ 한 줄이 한 «경로»다. 고객이 다르면 다른 줄이다. 이 규칙이 도식 결함
      세 가지(주 경로 누락·고객 혼동·지원 관계를 판매 경로에 놓기)를 구조적으로
      막는다 — 기존 flow 렌더러가 「표의 한 행 = 왼쪽→오른쪽 한 흐름」으로
      그리기 때문이다.
    """

    cells: tuple[str, ...]
    #: 이 줄의 근거 조각 id. 비면 근거 없는 줄이라 싣지 않는다.
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposedSection:
    """장 하나. 장 삭제 금지 — 자료가 부족해도 안내문으로 남긴다."""

    #: 기존 v3 정본 장 id 재사용 (identity … competitive_position)
    section_id: str
    #: 이 장의 문장들. 생성 실패 시 빈 튜플.
    sentences: tuple[ComposedSentence, ...]
    #: 자료 부족·생성 실패의 정직한 안내문. 문제없으면 "".
    notice: str = ""
    #: 7장 운영 경로표. 근거가 없으면 빈 튜플 — 빈 도식을 만들지 않는다.
    flow_rows: tuple[FlowRow, ...] = ()


@dataclass(frozen=True)
class ComposedReport:
    """v2 보고서 전체. summary는 소단계 3-3이 채운다 (그전까지 빈 튜플)."""

    sections: tuple[ComposedSection, ...]
    summary: tuple[ComposedSentence, ...] = ()


# ══════════════════════════════════════════════════════════
# 입력 어댑터 — 파이프라인 실측 구조를 얇게 감싼다
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CollectedFragment:
    """수집 조각 하나 — real.py의 `frags: dict[int, dict[str, str]]`를 감싼 것.

    실측 필드 대응: fragment_id ← dict 키(int), kind ← "종류", text ← "원문",
    source_url ← "출처"(홈페이지·공식 IR만), document_title ← "문서명"(공식 IR),
    location ← "원문위치"(공식 IR). 없는 필드는 빈 문자열.
    """

    fragment_id: str
    kind: str
    text: str
    source_url: str = ""
    document_title: str = ""
    location: str = ""
    #: packet raw Mapping이 가진 문서 기준일. legacy ``fragments_from_raw``는
    #: byte 호환을 위해 채우지 않고 packet 준비 경계에서만 보존한다.
    document_date: str = ""
    #: FULL typed packet이 수집 문서에서 확정한 독립 문서 신원. 임의 embedded
    #: fallback은 허용하지 않으며 SHADOW legacy 조각만 빈 값을 유지한다.
    document_identity: str = ""


_GEN8_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


def _packet_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class SectionEvidencePacket:
    """한 회사·한 수집 generation·한 장에 고정된 FULL 작성 입력."""

    company_id: str
    evidence_generation_sha256: str
    section_id: str
    fragments: tuple[CollectedFragment, ...]
    packet_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.company_id) is not str
            or self.company_id != self.company_id.strip()
            or type(self.evidence_generation_sha256) is not str
            or self.evidence_generation_sha256
            != self.evidence_generation_sha256.strip()
        ):
            raise ValueError("section packet 회사·generation 형식이 손상됐습니다")
        company_id = self.company_id
        generation = self.evidence_generation_sha256
        if _GEN8_RE.fullmatch(company_id) is None:
            raise ValueError("section packet의 company_id는 gen8이어야 합니다")
        if _SHA256_RE.fullmatch(generation) is None:
            raise ValueError("section packet의 evidence generation은 SHA-256이어야 합니다")
        if type(self.section_id) is not str or self.section_id not in SECTION_IDS:
            raise ValueError(f"알 수 없는 section packet 장입니다: {self.section_id!r}")
        if type(self.fragments) is not tuple or any(
            type(fragment) is not CollectedFragment for fragment in self.fragments
        ):
            raise TypeError("section packet 조각은 정확한 CollectedFragment tuple이어야 합니다")
        if not self.fragments:
            raise ValueError("FULL section packet은 빈 조각 묶음일 수 없습니다")
        fragment_ids = tuple(fragment.fragment_id for fragment in self.fragments)
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("section packet의 fragment_id가 중복됐습니다")
        for fragment in self.fragments:
            if any(
                type(value) is not str
                for value in (
                    fragment.fragment_id,
                    fragment.kind,
                    fragment.text,
                    fragment.source_url,
                    fragment.document_title,
                    fragment.location,
                    fragment.document_date,
                    fragment.document_identity,
                )
            ):
                raise TypeError("section packet 조각 필드는 문자열이어야 합니다")
            if not fragment.fragment_id.strip() or not fragment.text.strip():
                raise ValueError("section packet 조각의 id·원문은 비울 수 없습니다")
            identity = fragment.document_identity.strip()
            if not identity or identity.startswith("embedded:"):
                raise ValueError(
                    "FULL section packet에는 검증된 비-embedded 문서 신원이 필요합니다"
                )
        payload = {
            "version": 1,
            "company_id": company_id,
            "evidence_generation_sha256": generation,
            "section_id": self.section_id,
            "fragments": [
                {
                    "fragment_id": fragment.fragment_id,
                    "kind": fragment.kind,
                    "text_sha256": exact_evidence_text_hash(fragment.text),
                    "source_url": fragment.source_url,
                    "document_title": fragment.document_title,
                    "location": fragment.location,
                    "document_date": fragment.document_date,
                    "document_identity": fragment.document_identity,
                }
                for fragment in self.fragments
            ],
        }
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "evidence_generation_sha256", generation)
        object.__setattr__(
            self,
            "packet_sha256",
            hashlib.sha256(_packet_json(payload).encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class SectionEvidencePacketSet:
    """정책 순서의 typed 아홉 장 packet을 한 회사 generation에 묶는다."""

    company_id: str
    evidence_generation_sha256: str
    packets: tuple[SectionEvidencePacket, ...]

    def __post_init__(self) -> None:
        if (
            type(self.company_id) is not str
            or self.company_id != self.company_id.strip()
            or type(self.evidence_generation_sha256) is not str
            or self.evidence_generation_sha256
            != self.evidence_generation_sha256.strip()
        ):
            raise ValueError("packet set 회사·generation 형식이 손상됐습니다")
        company_id = self.company_id
        generation = self.evidence_generation_sha256
        if _GEN8_RE.fullmatch(company_id) is None:
            raise ValueError("packet set의 company_id는 gen8이어야 합니다")
        if _SHA256_RE.fullmatch(generation) is None:
            raise ValueError("packet set의 evidence generation은 SHA-256이어야 합니다")
        if type(self.packets) is not tuple or any(
            type(packet) is not SectionEvidencePacket for packet in self.packets
        ):
            raise TypeError("packet set에는 정확한 SectionEvidencePacket tuple이 필요합니다")
        if tuple(packet.section_id for packet in self.packets) != SECTION_IDS:
            raise ValueError("packet set에는 정책 순서의 typed 아홉 장이 필요합니다")
        if any(packet.company_id != company_id for packet in self.packets):
            raise ValueError("다른 회사의 section packet을 섞을 수 없습니다")
        if any(
            packet.evidence_generation_sha256 != generation
            for packet in self.packets
        ):
            raise ValueError("다른 evidence generation의 section packet을 섞을 수 없습니다")
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "evidence_generation_sha256", generation)

    @property
    def packet_sha256s(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (packet.section_id, packet.packet_sha256) for packet in self.packets
        )


def fragments_from_raw(
    raw: Mapping[int, Mapping[str, Any]]
) -> tuple[CollectedFragment, ...]:
    """파이프라인 조각 dict를 CollectedFragment 튜플로 바꾼다.

    ★ 원문이 빈 조각은 뺀다 — 인용해도 대조할 원문이 없어 근거가 못 되기 때문이다.
      (내용을 보고 거르는 게 아니라 «비어 있는가»만 본다.)
    """
    out: list[CollectedFragment] = []
    for number in sorted(raw):
        item = raw[number]
        text = str(item.get("원문") or "").strip()
        if not text:
            continue
        out.append(
            CollectedFragment(
                fragment_id=str(number),
                kind=str(item.get("종류") or "").strip(),
                text=text,
                source_url=str(item.get("출처") or "").strip(),
                document_title=str(item.get("문서명") or "").strip(),
                location=str(item.get("원문위치") or "").strip(),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class PerformanceTable:
    """프로그램이 만든 3개년 실적표 — 작가 AI에게 근거로 주는 표.

    파이프라인 `ReportTable`(canonical_report.py의 table_facts 원천)을
    composer가 직접 import 하지 않으려고 얇게 복사한 모양이다.
    """

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    unit: str = ""
    cite: str = ""
    raw_rows: tuple[tuple[str, ...], ...] = ()
    scale_divisor: str = ""
    scale_places: int = 0
    evidence_rows: tuple[str, ...] = ()
    entity_scope: str = ""
    raw_unit: str = ""
    unit_dimension: str = ""
    #: 행이 원문 직접 결속 대신 이미 검증된 프로그램 사실을 주입할 때 쓰는 ID.
    #: 있으면 rows와 같은 길이여야 하며 manifest canonicalizer가 검증한다.
    row_fact_ids: tuple[str, ...] = ()


def performance_table_from_report_table(table: Any) -> PerformanceTable:
    """파이프라인 ReportTable을 덕 타이핑으로 감싼다 (직접 import 회피)."""
    return PerformanceTable(
        caption=str(getattr(table, "caption", "") or ""),
        headers=tuple(str(h) for h in (getattr(table, "headers", None) or ())),
        rows=tuple(
            tuple(str(cell) for cell in row)
            for row in (getattr(table, "rows", None) or ())
        ),
        unit=str(getattr(table, "display_unit", "") or ""),
        cite=str(getattr(table, "cite", "") or ""),
        raw_rows=tuple(
            tuple(str(cell) for cell in row)
            for row in (getattr(table, "raw_rows", None) or ())
        ),
        scale_divisor=str(getattr(table, "scale_divisor", "") or ""),
        scale_places=int(getattr(table, "scale_places", 0) or 0),
        evidence_rows=tuple(
            str(value) for value in (getattr(table, "evidence_rows", None) or ())
        ),
        entity_scope=str(getattr(table, "entity_scope", "") or ""),
        raw_unit=str(getattr(table, "raw_unit", "") or ""),
        unit_dimension=str(getattr(table, "unit_dimension", "") or ""),
        row_fact_ids=tuple(
            str(value) for value in (getattr(table, "row_fact_ids", None) or ())
        ),
    )


# ══════════════════════════════════════════════════════════
# 공시 신원 — 부록 출처에 «원문 주소»를 싣기 위한 어댑터
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FilingMeta:
    """이번 조사가 실제로 내려받은 공시 1건의 신원.

    ★ 왜 이 타입이 필요한가 (실측 결함) — 전자공시 절 조각(사업내용·MD&A 등)에는
      조각 자체에 주소가 없다. 주소를 가진 것은 조각이 아니라 «그 조각을 떠 온
      문서»다. 그런데 v2는 그 문서 신원을 render까지 넘기지 않아, 현대자동차
      실측에서 부록 출처 12건 중 11건이 「주소 없음」으로 나갔다.
      독자가 원문을 열 수 없으면 근거 표기는 장식일 뿐이다.
    ★ v1은 같은 정보를 provenance/citations.py에서 이미 쓰고 있다. v1 경로는
      건드리지 않고(v1 경로는 그대로 둔다), v2가 같은 재료를 받아 쓰게만 한다.
    """

    #: 공시 접수번호 (`rcept_no`). 이것이 있어야 원문 주소를 만들 수 있다.
    document_id: str = ""
    #: 보고서 이름 (`report_nm`). 예: "반기보고서 (2026.06)".
    title: str = ""
    #: 공시일 `YYYY-MM-DD`. 원래 모양이 아니면 비운다 (지어내지 않는다).
    disclosed_at: str = ""


def _format_disclosed_at(raw: str) -> str:
    """DART 공시일(`YYYYMMDD`) → `"YYYY-MM-DD"`. 모양이 안 맞으면 빈 문자열.

    ★ 날짜를 지어내지 않는다 — 틀린 공시일은 없는 공시일보다 나쁘다.
      v1 `provenance/citations.py._format_rcept_dt`와 같은 규칙이다.
    """
    digits = raw.strip()
    if len(digits) != RCEPT_DT_LENGTH or not digits.isdigit():
        return ""
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def filing_meta_from_raw(filing: Any) -> Optional[FilingMeta]:
    """real.py의 공시 dict(`rcept_no`·`report_nm`·`rcept_dt`)를 FilingMeta로.

    접수번호가 없으면 주소를 만들 수 없으므로 ``None``을 돌려준다 — 이때는
    부록이 예전처럼 주소 없이 나가며, 그 사실이 화면에 그대로 보인다.
    """
    if not isinstance(filing, Mapping):
        return None
    document_id = str(filing.get("rcept_no") or filing.get("rceptNo") or "").strip()
    if not document_id:
        return None
    return FilingMeta(
        document_id=document_id,
        title=str(filing.get("report_nm") or "").strip(),
        disclosed_at=_format_disclosed_at(str(filing.get("rcept_dt") or "")),
    )


#: 합계 행을 알아보는 말들. 도식 판정기(`report_standard/visualization.py`)가
#: 쓰는 것과 같은 뜻이다 — 합계가 섞이면 「부분의 합이 전체」라는 그림이 깨진다.
_TOTAL_LABELS: Final[tuple[str, ...]] = ("합계", "총계", "계", "소계", "합 계")

#: 비중 열을 알아보는 말.
_RATIO_HEADER_HINTS: Final[tuple[str, ...]] = ("비중", "%")


def _composition_shape(
    headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """구성 도식이 그려질 수 있는 «항목 + 비중» 두 열 모양으로 줄인다.

    ★ 왜 필요한가 (실측) — `revenuemix`가 만드는 표는 「구분 · 금액 · 비중」
      3열이고 «합계» 행이 붙는다. 그런데 도식 판정기는 「정확히 2열 · 합계 행
      없음 · 3~5행」일 때만 100% 누적 막대를 그린다. 그래서 진영 실측에서
      **표는 붙었는데 도식은 안 그려졌다.**
    ★ v1이 쓰는 `revenuemix`를 고치지 않는다(v1 무변). 여기서 «도식용 모양»만
      만든다.
    ★ 줄이는 것뿐이고 값을 바꾸거나 만들지 않는다. 비중 열을 못 찾거나 항목
      수가 안 맞으면 원래 모양을 그대로 돌려준다 — 그러면 표로만 나간다.
      억지로 도식을 만들지 않는다.
    """
    if len(headers) <= 2:
        return headers, rows
    ratio_index = next(
        (
            index
            for index in range(len(headers) - 1, 0, -1)
            if any(hint in headers[index] for hint in _RATIO_HEADER_HINTS)
        ),
        None,
    )
    if ratio_index is None:
        return headers, rows
    trimmed = tuple(
        (row[0], row[ratio_index])
        for row in rows
        if len(row) > ratio_index
        and not any(token in str(row[0]) for token in _TOTAL_LABELS)
    )
    if len(trimmed) < 3:
        # 도식 판정기의 하한(3행)에 못 미친다 — 원표를 그대로 두는 편이 낫다.
        return headers, rows
    return (headers[0], headers[ratio_index]), trimmed


def composition_tables_from_raw(tables: Any) -> tuple[PerformanceTable, ...]:
    """`revenuemix.build()`가 돌려준 표 목록을 «전부» 구성표로 바꾼다.

    ★ 왜 필요한가 (실측 결함 ①) — v1은 이 표를 만들어 2장에 붙이는데
      (`pipeline/real.py`의 tables_by_section["business_model"]),
      v2 호출부가 넘기지 않아 «표도 도식도» 통째로 빠져 있었다.
      9개 장 중 4장 하나만 표를 받는 상태였다.
    ★ 왜 여러 개인가 (실측 결함 ②, 설계 변경) — 예전에는 «첫 표만»
      썼다. revenuemix는 제품별·지역별 두 표를 낼 수 있는데, 첫 표만 쓰면
      지역별 표가 통째로 사라진다. v1은 이미 둘 다 2장에 붙이고
      (`ReportTable(**table) for table in revenue_tables`), 정본
      §4 소유권 표(2장 = 「수익 구조·고객 유형·고객·지역·채널 우선순위」)에도
      지역 우선순위가 명시돼 있다 — 첫 표만 쓰는 건 v2만의 축소였다. 다른
      장으로 옮기지 않고 «2장에 표 여러 개»를 그대로 허용한다(사용자 결정).
      «같은 매출을 두 번 보여 준다»는 예전 우려는 성립하지 않는다 —
      제품별·지역별은 같은 매출을 «다른 축»으로 나눈 것이라 정본 규칙의
      「같은 수치의 반복」이 아니다.
    ★ 표마다 «구성 도식 모양»으로 따로 줄인다(_composition_shape) — 표 하나가
      도식 하한(3행)에 못 미쳐도 다른 표에는 영향을 주지 않는다.
    """
    if not tables:
        return ()
    items = tables if isinstance(tables, (list, tuple)) else (tables,)
    out: list[PerformanceTable] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        headers = tuple(str(head) for head in (item.get("headers") or ()))
        rows = tuple(
            tuple(str(cell) for cell in row) for row in (item.get("rows") or ())
        )
        if not rows:
            continue
        headers, rows = _composition_shape(headers, rows)
        if not rows:
            continue
        out.append(
            PerformanceTable(
                caption=str(item.get("caption") or ""),
                headers=headers,
                rows=rows,
                unit=str(item.get("display_unit") or ""),
                cite=str(item.get("cite") or ""),
            )
        )
    return tuple(out)

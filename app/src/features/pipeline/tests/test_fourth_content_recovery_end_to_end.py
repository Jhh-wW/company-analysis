"""4차 내용 회복의 무료 통합 증명 — 검수 입력 «이후» 부분보고서 공개 생존까지.

★ 왜 필요한가(2026-09-23 4차 실측) — 공식 개요 문단의 소재지·사업 정의와 수익인식
  주석의 수익 구성은 원문에 있었지만 선택 후보 칸이 운반되지 않아 검수 전에
  사라졌다. evidence 수정은 이 세 사실이 «검수 입력»까지 오는 것만 증명했다. 이
  시험은 같은 세 역할을 익명 합성 원문으로 재현해, 검수 뒤 scope·근거 결속·중복
  정리를 지나 canonical fact·공개 본문 문장·표지 요약에 살아남는지 본다. 같은
  실행에서 원문에 없는 완료기준 수익인식(L1)과 설립 결정에 덧붙인 목적 해석(L2)은
  가짜 검수자가 «참»이라고 답해도 실제 검증기가 공개에서 빼는지 대칭으로 확인한다.

★ 범위 — 실측과 같은 «부분보고서(SHADOW, evidence-available-v1)» 경로만 본다. 이
  경로는 FULL 공개 구조 봉인·생성 증거를 만들지 않으며, 이 시험도 봉인을 끼워 넣거나
  FULL 자격을 우회하지 않는다. FULL 저장 봉인은 이 시험의 범위 밖이다.

★ 무엇이 진짜이고 무엇이 가짜인가
  · 진짜(생산 코드 그대로): 엔진 공시 수집·슬롯 채점 → 장 후보 선택 → legacy
    관계법인 각주 운반 → typed 병합 → 평문 운반 → 부분보고서 장별 근거 → run_v2
    (장별 정리·검수 결속·scope·근거 재작성·중복 정리·수치 안전·canonical fact·요약·
    validate_v2).
  · 가짜(이 파일이 명시적으로 정의): 공시 조회기, 작가 응답(정해 둔 문장), 검수자
    응답(모든 후보 «참» + 필요한 경우 시험이 적어 둔 정확 인용), 근거 재작성 응답.
    가짜 검수 응답은 실제 네이티브 검수 스키마를 통과해야만 돌려준다.
  · canonical·receipt·무결성·품질 하한은 monkeypatch 하지 않는다.
  · 새 AI 모델의 실제 품질은 이 시험으로 증명되지 않는다.

★ AI·네트워크·DB 0회. 원문은 실측 문구를 복사하지 않은 익명 합성 문자열이다.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from src.features.chapter_evidence.produce import produce_chapter_evidence_candidates
from src.features.composer import pipeline as composer_pipeline
from src.features.composer.evidence_availability import (
    COLLECTION_STATE_PARTIAL,
    EvidenceAvailability,
)
from src.features.composer.grounding_rewrite_constants import (
    GROUNDING_REWRITE_PROMPT_HEADER,
)
from src.features.composer.port import CollectedFragment, filing_meta_from_raw
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import REVIEW_PROMPT_HEADER, REWRITE_PROMPT_HEADER
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import typed_fragments_from_raw
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.summary_binding import summary_verification_binding

#: 엔진 수집기는 app 패키지가 아니라 ``analysis_engine/src`` 아래에 산다. app만 떼어 낸
#: 배치에서는 소리 나게 건너뛴다(조용히 통과시키지 않는다).
_ENGINE_FEATURE = (
    Path(__file__).resolve().parents[5]
    / "analysis_engine" / "src" / "features" / "evidence_collection"
)
pytestmark = pytest.mark.skipif(
    not _ENGINE_FEATURE.is_dir(),
    reason=f"엔진 수집기가 이 트리에 없습니다(통합 트리 전용): {_ENGINE_FEATURE}",
)

_COMPANY = "주식회사 가나다랩"
_COMPANY_ID = "00000001"
_RECEIPT = "20260414000001"
_REPORT_NAME = "감사보고서 (2025.12)"
_BUSINESS_DATE = "2026-09-23"
_REVIEW_COMPARISON = "인용 원문과 대조(시험 가짜 검수)"

# ── 익명 합성 원문: 실측의 세 역할(개요·수익인식 주석·관계자+보고기간 후 사건) ──
_OVERVIEW = (
    "1. 회사의 개요 주식회사 가나다랩(이하 \"회사\")은 2020년 3월 2일 설립되어 "
    "서울특별시 중구 예시로 10에 본점을 두고 있으며, 인공지능 소프트웨어 개발 및 "
    "공급을 주요 사업으로 영위하고 있습니다."
)
#: 「인식」 낱말은 있지만 수익 인식 기준 서술이 없는 절 — L1이 기대는 개요 조각의 꼴.
_POLICY = "2. 중요한 회계정책 회사는 금융자산을 최초 인식시 공정가치로 측정합니다."
_PROGRESS_QUOTE = (
    "용역 제공으로 인한 수익은 용역제공거래의 성과를 신뢰성 있게 추정할 수 있을 때 "
    "진행기준에 따라 인식합니다"
)
_EXCEPTION_QUOTE = (
    "다만 회사는 회계처리 특례를 적용하여 1년 내의 기간에 완료되는 용역 매출에 "
    "대하여는 용역제공을 완료한 날에 수익으로 인식하고 있습니다"
)
_REVENUE = (
    "(13) 수익인식 회사의 주된 영업수익의 형태는 소프트웨어 개발과 관련된 용역 매출, "
    "디지털 콘텐츠 매출 등으로 구성됩니다. 회사는 받았거나 받을 대가의 공정가치로 "
    f"수익을 측정하고 있습니다. {_PROGRESS_QUOTE}. {_EXCEPTION_QUOTE}. 콘텐츠 매출은 "
    "고객이 제공받는 시점에 수익을 인식하고 있습니다."
)
_RELATED = (
    "18. 특수관계자 등 거래 (1) 당기말 현재 회사의 특수관계자 내역은 다음과 같습니다. "
    "구분 특수관계자명 종속기업 Alpha Global Holdings (2) 당기 중 회사는 종속기업 "
    "Alpha Global Holdings에 운영자금을 대여하였습니다."
)
#: 설립 «결정»과 납입 «예정»만 적고 목적은 적지 않은 보고기간 후 사건.
_SUBSEQUENT = (
    "20. 보고기간 후 사건 회사는 이사회 결의에 따라 해외법인 Beta Studio Inc.의 "
    "설립을 결정하였으며, 납입 예정 자본금은 1천 달러입니다."
)
#: 같은 법인의 회계범위 제한 각주 — 관계자 주석(당기 거래)과 다른 주석에 있다.
_INVESTMENT_NOTE = (
    "5. 매도가능증권 당기말 현재 매도가능증권의 내역은 다음과 같습니다. (단위 : 천원) "
    "구분 지분율 취득원가 장부금액 Alpha Global Holdings(*) 100% 40,000 40,000 "
    "(*) 일반기업회계기준 경과규정에 따라 종속기업에서 제외되었습니다. 또한, 중소기업 "
    "회계처리 특례를 적용하여 지분법을 적용하지 아니하고 취득원가를 장부금액으로 "
    "계상하고 있습니다."
)
_FILING_TEXT = (
    f"I. 회사의 개요\n{_OVERVIEW}\n\nII. 주석\n{_POLICY}\n\n{_INVESTMENT_NOTE}\n\n"
    f"{_REVENUE}\n\n{_RELATED}\n\n{_SUBSEQUENT}\n"
)
#: legacy 발췌 — 실측처럼 개요는 여러 장이 쓰는 슬롯 없는 조각, 수익인식은 2장,
#: 관계자(뒤에 보고기간 후 사건이 이어짐)는 7장 소유다.
_LEGACY_KIND_BUSINESS = "사업내용"
_LEGACY_KIND_REVENUE = "수익인식"
_LEGACY_KIND_RELATED = "특수관계자"
_LEGACY = {
    1: {"종류": _LEGACY_KIND_BUSINESS, "원문": f"{_OVERVIEW} {_POLICY}"},
    2: {"종류": _LEGACY_KIND_REVENUE, "원문": _REVENUE},
    3: {"종류": _LEGACY_KIND_RELATED, "원문": f"{_RELATED} {_SUBSEQUENT}"},
}

# ── 작가 문장 ────────────────────────────────────────────────
_LOCATION_SLOT = "identity:official_location"
_DEFINITION_SLOT = "identity:business_definition"
_REVENUE_MODEL_SLOT = "business_model:revenue_model"
_LOCATION = "회사는 2020년 3월 2일 설립되어 서울특별시 중구 예시로 10에 본점을 두고 있다."
_DEFINITION = "회사는 인공지능 소프트웨어 개발 및 공급을 주요 사업으로 영위한다."
_COMPOSITION = (
    "회사의 주된 영업수익은 소프트웨어 개발과 관련된 용역 매출과 디지털 콘텐츠 "
    "매출로 구성된다."
)
#: 원문에 있는 진행 원칙·1년 특례 — 정확 인용이 있을 때만 2장에 남아야 하는 양성.
_PRINCIPLE = (
    "용역 매출은 용역제공거래의 성과를 신뢰성 있게 추정할 수 있을 때 진행기준에 따라 "
    "인식하며, 회계처리 특례를 적용하여 1년 내의 기간에 완료되는 용역 매출에 "
    "대하여는 용역제공을 완료한 날에 수익으로 인식한다."
)
#: L1 — 개요 조각만 인용하면서 한정 없는 완료기준 인식을 단정한다.
_COMPLETION_WITHOUT_SOURCE = (
    "회사의 주된 영업수익은 용역 매출과 콘텐츠 매출로 구성되며, 용역 매출은 "
    "용역제공을 완료한 날에 수익으로 인식한다."
)
#: L2 — 설립 결정 사실 절 뒤에 원문에 없는 목적·의미 해석을 덧붙인다.
_PURPOSE_TAIL = "북미 시장 진출"
_SETUP_WITH_PURPOSE = (
    "회사는 이사회 결의에 따라 해외법인 Beta Studio Inc.의 설립을 결정하였으며, "
    f"이는 {_PURPOSE_TAIL}을 위한 사업 운영 구조의 확장을 의미한다."
)
_SETUP_FACT = "회사는 이사회 결의에 따라 해외법인 Beta Studio Inc.의 설립을 결정하였다."
#: 7장 정상 거래 사실 — L1·L2 차단이 같은 장의 정상 근거 문장을 지우지 않는지 본다.
_LOAN = "회사는 종속기업 Alpha Global Holdings에 운영자금을 대여하였다."
#: 각주의 제외 조건을 그대로 옮긴 정상 후보(7장 각주 인용).
_EXCLUSION_KEPT = (
    "회사는 Alpha Global Holdings를 경과규정에 따라 종속기업에서 제외하고 취득원가로 "
    "계상하고 있다."
)
#: 같은 법인·기간의 «종속기업에서 제외» 조건을 빼고 현재 연결 대상이라고 단정한 후보.
_CONSOLIDATED_WITHOUT_EXCLUSION = (
    "Alpha Global Holdings는 회사의 연결재무제표 작성 대상 종속기업이다."
)
#: 관계자 조각만 자기 인용하고(각주 미인용) 현재 종속 관계를 단정한 후보 — 실측 [27]
#: 과 같은 꼴. 같은 문서의 제외 각주는 packet 에만 있다.
_OWNCITE_WITHOUT_FOOTNOTE = (
    "회사는 Alpha Global Holdings를 종속기업으로 두고 있으며 운영자금을 대여했다."
)

_FRAGMENT_HEAD = re.compile(r"(?m)^\[조각 (\S+)\] \(([^\n]*?)\) ")
_REWRITE_TARGET = re.compile(
    r"(?m)^번호 (\d+) · [^\n]*\n  원문장\(JSON 문자열\): ([^\n]*)$"
)
_GRADE_LINE = re.compile(r"(?m)^  등급: [^\n]+\n")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── 가짜 공시 조회기와 실제 typed 선택·운반 ─────────────────────


def _engine_modules() -> tuple[Any, Any, Any]:
    real._engine()  # noqa: SLF001 - 엔진 경로만 싣는다(키·네트워크 0회)
    return (
        importlib.import_module("features.evidence_collection.collect"),
        importlib.import_module("features.evidence_collection.serialize"),
        importlib.import_module("features.evidence_collection.filing_select"),
    )


def _composer_fragments() -> tuple[CollectedFragment, ...]:
    collect, serialize, filing_select = _engine_modules()

    class _SavedFilingFetcher:
        def fetch_filing_list(self, company_id: str, pblntf_ty: str):
            rows = (
                (filing_select.RawFilingRow(_RECEIPT, _REPORT_NAME, "20260414"),)
                if pblntf_ty == "F"
                else ()
            )
            return filing_select.FilingListResult(state="OK", rows=rows)

        def fetch_document_text(self, rcept_no: str):
            assert rcept_no == _RECEIPT
            return filing_select.DocumentFetchResult(state="OK", text=_FILING_TEXT)

    harvest = collect.collect_dart_evidence(
        _SavedFilingFetcher(), _COMPANY_ID, now=_BUSINESS_DATE
    )
    envelope = serialize.harvest_to_mapping(harvest)
    candidates = produce_chapter_evidence_candidates(
        company_id=_COMPANY_ID,
        company_type=envelope["company_type"],
        documents=envelope["documents"],
        fragments=envelope["fragments"],
        attempts=envelope["attempts"],
    )
    # 운영 경로(real._collect)와 같이 legacy 발췌 뒤 관계법인 각주를 실제 배선으로 싣는다.
    legacy_copy = {number: dict(raw) for number, raw in _LEGACY.items()}
    legacy, _footnotes_added = real._add_entity_scope_footnotes(  # noqa: SLF001
        real._engine(), legacy_copy, _FILING_TEXT,  # noqa: SLF001
    )
    merged, _added = merge_official_evidence_fragments(
        legacy,
        OfficialEvidenceCollectionResult(company_id=_COMPANY_ID, candidates=candidates),
    )
    conversion = typed_fragments_from_raw(
        corp_id=_COMPANY_ID, frags=merged, filing_meta=_filing_meta()
    )
    assert conversion.rejected_count == 0
    return conversion.fragments


def _filing_meta():
    return filing_meta_from_raw(
        {"rcept_no": _RECEIPT, "report_nm": _REPORT_NAME, "rcept_dt": "20260414"}
    )


# ── 가짜 작가: 장 packet 프롬프트에서 인용 번호를 찾아 정해 둔 문장만 돌려준다 ──


def _fragment_blocks(prompt: str) -> tuple[tuple[str, str, str], ...]:
    heads = list(_FRAGMENT_HEAD.finditer(prompt))
    ends = [head.start() for head in heads[1:]] + [len(prompt)]
    return tuple(
        (head.group(1), head.group(2), prompt[head.end():end])
        for head, end in zip(heads, ends)
    )


def _cite(
    marker: str, *, label: str = "", first_only: bool = True
) -> Callable[[str], list[str]]:
    def select(prompt: str) -> list[str]:
        ids = [
            fragment_id
            for fragment_id, fragment_label, body in _fragment_blocks(prompt)
            if marker in body and label in fragment_label
        ]
        assert ids, f"작가 packet에서 «{marker}»({label}) 조각을 찾지 못했습니다"
        return ids[:1] if first_only else ids
    return select


@dataclass(frozen=True)
class _PlannedSentence:
    text: str
    slot: str
    cite: Callable[[str], list[str]]


def _writer_plan(*, location_cite: Callable[[str], list[str]] | None = None):
    # 두 문장 모두 «개요 typed 조각»을 인용한다. 소재지 칸 표기로 조각을 고르지
    # 않는 이유 — 칸 운반이 끊겨도 실제 작가는 같은 조각을 인용해 소재지를 쓸 수
    # 있고, 그 문장이 어디서 빠지는지가 이 시험이 보려는 경계다.
    overview = _cite("본점을 두고", label=_DEFINITION_SLOT)
    return {
        "identity": (
            _PlannedSentence(_LOCATION, _LOCATION_SLOT, location_cite or overview),
            _PlannedSentence(_DEFINITION, _DEFINITION_SLOT, overview),
        ),
        "business_model": (
            # 실측과 같이 legacy 수익인식 조각과 typed 수익 조각을 함께 인용한다.
            _PlannedSentence(
                _COMPOSITION, _REVENUE_MODEL_SLOT,
                _cite("수익의 형태", first_only=False),
            ),
            _PlannedSentence(
                _PRINCIPLE, "business_model:value_exchange",
                _cite("수익의 형태", first_only=False),
            ),
        ),
        "operations_partners": (
            _PlannedSentence(
                _COMPLETION_WITHOUT_SOURCE, "operations_partners:value_chain",
                _cite("회사의 개요"),
            ),
            _PlannedSentence(
                _LOAN, "operations_partners:partnership", _cite("운영자금을 대여"),
            ),
            _PlannedSentence(
                _SETUP_WITH_PURPOSE, "operations_partners:operating_role",
                _cite("설립을 결정"),
            ),
        ),
    }


class _ScriptedWriter:
    """장 ID로 묶인 호출에서 정해 둔 문장만 쓰는 가짜 작가(시험 전용)."""

    def __init__(self, plan: Mapping[str, Sequence[_PlannedSentence]]) -> None:
        self._plan = plan
        self.prompts: dict[str, str] = {}

    def __call__(self, prompt: str) -> str:
        raise AssertionError("부분보고서 작성은 장별 호출(for_section)만 써야 합니다")

    def for_section(self, section_id: str) -> Callable[[str], str]:
        def ask(prompt: str) -> str:
            self.prompts[section_id] = str(prompt)
            rows = [
                {"글": planned.text, "인용": planned.cite(str(prompt)),
                 "등급": "확인", "주장슬롯": planned.slot}
                for planned in self._plan.get(section_id, ())
            ]
            return json.dumps({"문장들": rows}, ensure_ascii=False)
        return ask


# ── 가짜 검수자: 모두 «참». 필요한 정확 인용은 시험이 적어 둔 값만 붙인다 ─────


@dataclass
class _SchemaCheckedReviewer:
    """모든 후보를 «참»으로 답하되 응답을 실제 네이티브 스키마로 먼저 검사한다.

    ``recognition_quotes``는 인식기준 정확 인용으로 붙일 원문 조각이다. 원문과
    다르게 주면 실제 근거 검증기가 거절해야 한다.
    """

    recognition_quotes: tuple[str, ...] = (_PROGRESS_QUOTE, _EXCEPTION_QUOTE)
    rewrites: Mapping[str, str] = field(
        default_factory=lambda: {_SETUP_WITH_PURPOSE: _SETUP_FACT}
    )
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    rewrite_requests: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def __call__(self, prompt: str) -> str:
        text = str(prompt)
        if text.startswith(GROUNDING_REWRITE_PROMPT_HEADER):
            return self._grounding_rewrite(text)
        if text.startswith(REVIEW_PROMPT_HEADER):
            return self._review(text)
        if text.startswith(REWRITE_PROMPT_HEADER):
            self.unexpected.append("sentence_rewrite")
            return ""
        self.unexpected.append(text[:80])
        return ""

    def _review(self, prompt: str) -> str:
        rows: list[dict[str, Any]] = []
        for item in review_items(_GRADE_LINE.sub("", prompt)):
            citations = list(item.citations)
            row: dict[str, Any] = {
                "번호": item.number, "근거대조": _REVIEW_COMPARISON, "결과": "참",
                "장": item.section, "근거": citations,
            }
            if item.text == _PRINCIPLE and self.recognition_quotes:
                row["검증근거"] = {"인식기준": [
                    {"표현": _PRINCIPLE, "근거": citations[0], "원문": quote}
                    for quote in self.recognition_quotes
                ]}
            rows.append(row)
            self.verdicts.append({"text": item.text, "section": item.section, **row})
        answer = {"판정": rows}
        errors = list(Draft202012Validator(FLAT_REVIEW_SCHEMA).iter_errors(answer))
        assert not errors, f"가짜 검수 응답이 네이티브 검수 스키마를 어깁니다: {errors}"
        return json.dumps(answer, ensure_ascii=False)

    def _grounding_rewrite(self, prompt: str) -> str:
        rows = []
        for number, original_json in _REWRITE_TARGET.findall(prompt):
            original = json.loads(original_json)
            self.rewrite_requests.append(original)
            rewritten = self.rewrites.get(original, "")
            rows.append({"번호": int(number), "글": rewritten, "포기": not rewritten})
        return json.dumps({"문장들": rows}, ensure_ascii=False)


@dataclass(frozen=True)
class _ChainRun:
    output: Any
    fragments: tuple[CollectedFragment, ...]
    writer: _ScriptedWriter
    reviewer: _SchemaCheckedReviewer
    review_diagnostics: list[dict]

    @property
    def report(self):
        return self.output.report

    def fact(self, claim: str):
        matches = [fact for fact in self.report.fact_records if fact.claim == claim]
        assert len(matches) == 1, f"공개 fact가 정확히 하나여야 합니다: {claim}"
        return matches[0]

    def section_prose(self, section_id: str) -> str:
        section = next(item for item in self.report.sections if item.cell == section_id)
        return " ".join(text for text, _source in section.prose_lines)

    def public_texts(self) -> list[str]:
        texts = [fact.claim for fact in self.report.fact_records]
        texts += [item.text for item in self.report.summary_items]
        for section in self.report.sections:
            texts += [text for text, _source in section.prose_lines]
            texts += list(section.prose_paragraphs)
            texts += [text for text, _source in section.lines]
        return texts

    def reasons(self, section_id: str) -> list[str]:
        return [
            item["reason_code"] for item in self.review_diagnostics
            if item.get("section_id") == section_id
        ]


def _run_chain(
    *,
    reviewer: _SchemaCheckedReviewer | None = None,
    plan: Mapping[str, Sequence[_PlannedSentence]] | None = None,
) -> _ChainRun:
    fragments = _composer_fragments()
    writer = _ScriptedWriter(plan or _writer_plan())
    reviewer = reviewer or _SchemaCheckedReviewer()
    review_diagnostics: list[dict] = []
    # real.py 부분보고서 호출과 같은 인자다. 빈 장 복구는 예산 없음으로 두어
    # 가짜 작가가 새 문장을 만들지 않게 한다(정답 장은 빈 장이 아니다).
    output = composer_pipeline.run_v2(
        _COMPANY,
        fragments,
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        initial_reviewer_ask=reviewer,
        initial_retry_reviewer_ask=reviewer,
        rewrite_ask=reviewer,
        recheck_ask=reviewer,
        diagram_ask=reviewer,
        empty_recovery_writer_ask=writer,
        empty_recovery_reviewer_ask=reviewer,
        empty_recovery_can_start=lambda: False,
        grounding_rewrite_enabled=True,
        generated_at=_BUSINESS_DATE,
        as_of_date=_BUSINESS_DATE,
        filing_meta=_filing_meta(),
        company_id=_COMPANY_ID,
        review_diagnostics_sink=review_diagnostics,
        composition_diagnostics_sink=[],
        evidence_availability=EvidenceAvailability(COLLECTION_STATE_PARTIAL),
        preserve_on_ask_failure=True,
    )
    assert reviewer.unexpected == []
    return _ChainRun(output, fragments, writer, reviewer, review_diagnostics)


def _cited_ids(fact) -> tuple[str, ...]:
    return tuple(item["fragment_id"] for item in json.loads(fact.state_evidence))


# ── 살릴 것: 세 정답이 검수 뒤 fact·공개 본문·표지 요약까지 ─────────────


def test_세_정답은_검수_뒤_canonical_fact와_공개본문과_표지요약까지_살아남는다() -> None:
    chain = _run_chain()
    report = chain.report
    by_id = {fragment.fragment_id: fragment for fragment in chain.fragments}

    # 선택 후보 칸이 작가 입력의 지원 칸까지 실렸다(원인 구간 회복).
    assert f"지원 주장슬롯: {_DEFINITION_SLOT}" in chain.writer.prompts["identity"]
    assert _LOCATION_SLOT in chain.writer.prompts["identity"]

    targets = {
        _LOCATION: ("identity", _LOCATION_SLOT),
        _DEFINITION: ("identity", _DEFINITION_SLOT),
        _COMPOSITION: ("business_model", _REVENUE_MODEL_SLOT),
    }
    target_fact_ids: dict[str, str] = {}
    for claim, (section_id, slot) in targets.items():
        fact = chain.fact(claim)
        assert (fact.section_owner, fact.claim_slot) == (section_id, slot)
        assert fact.verification_status == "verified"
        # canonical 결속은 생산 함수로 다시 계산해 대조한다(시험이 값을 만들지 않는다).
        assert fact.evidence_binding == fact_evidence_binding(fact)
        # 인용 중 typed 조각이 그 칸을 실제로 지원한다(슬롯 없는 legacy 조각만으로
        # 통과 금지).
        cited_slots = [
            by_id[fragment_id].supported_claim_slots for fragment_id in _cited_ids(fact)
        ]
        assert any(slot in slots for slots in cited_slots)
        assert all(slot in slots for slots in cited_slots if slots)
        section = next(item for item in report.sections if item.cell == section_id)
        assert fact.fact_id in section.fact_ids
        target_fact_ids[claim] = fact.fact_id

    # 2장 정상 원칙·1년 특례도 정확 인용이 있으면 함께 남는다(수익 구성 문장과 별개).
    assert chain.fact(_PRINCIPLE).section_owner == "business_model"

    # 표지 요약 — 새 글자를 만들지 않고 검증 fact를 그대로 재사용하며 결속 지문이 맞다.
    facts_by_id = {fact.fact_id: fact for fact in report.fact_records}
    summary_fact_ids = set()
    for item in report.summary_items:
        assert item.verification_status == "verified"
        assert item.fact_ids
        assert all(fact_id in facts_by_id for fact_id in item.fact_ids)
        assert item.text.startswith(facts_by_id[item.fact_ids[0]].claim)
        assert item.verification_binding == summary_verification_binding(
            item.text, item.section_id, item.fact_ids, item.evidence_text,
            item.verification_status, item.support_terms,
        )
        summary_fact_ids.update(item.fact_ids)
    # 표지 자리(최대 다섯)는 장끼리 경쟁하므로 모든 사실을 약속하지 않는다. 정답이
    # 있는 두 장이 «각자의 정답 fact»로 표지에 도달하는지를 본다.
    summarized = [
        claim
        for claim, fact_id in target_fact_ids.items()
        if fact_id in summary_fact_ids
    ]
    assert {targets[claim][0] for claim in summarized} == {"identity", "business_model"}

    # 공개 본문 — 부분보고서 화면·PDF가 그리는 소유 장 공개 문장에 그대로 있다.
    for claim, (section_id, _slot) in targets.items():
        assert claim in chain.section_prose(section_id)
    # 부분보고서 경로다 — FULL 봉인·생성 증거를 만들지도 끼워 넣지도 않는다.
    assert report.publication_policy == "evidence-available-v1"
    assert report.public_projection is None
    assert report.generation_evidence is None


# ── 막을 것: 가짜 검수 «참»이어도 원문에 없는 기준·목적은 공개되지 않는다 ────────


def test_원문에_없는_완료기준과_목적해석은_가짜검수가_참이어도_공개되지_않는다() -> None:
    chain = _run_chain()

    # 가짜 검수자는 두 문장 모두 «참»으로 답했다 — 제거는 실제 검증기의 몫이다.
    approved = {row["text"] for row in chain.reviewer.verdicts if row["결과"] == "참"}
    assert {_COMPLETION_WITHOUT_SOURCE, _SETUP_WITH_PURPOSE} <= approved
    reasons = chain.reasons("operations_partners")
    assert "scope_condition_unbound" in reasons
    assert "purpose_interpretation_unsupported" in reasons

    public = chain.public_texts()
    assert not any(
        "완료한 날" in text and "콘텐츠 매출로 구성되며" in text for text in public
    )
    assert not any(_PURPOSE_TAIL in text for text in public)
    assert _sha256(_COMPLETION_WITHOUT_SOURCE) in {
        item.get("candidate_sha256") for item in chain.review_diagnostics
    }

    # 대칭 보존 — 목적 꼬리만 빠진 사실 절은 재작성·재검수로 같은 인용에 남고,
    # 같은 장의 정상 거래 사실도 지워지지 않는다.
    setup = chain.fact(_SETUP_FACT)
    loan = chain.fact(_LOAN)
    assert setup.section_owner == loan.section_owner == "operations_partners"
    assert setup.claim_type == "verified_prose"
    by_id = {fragment.fragment_id: fragment for fragment in chain.fragments}
    assert all("설립을 결정" in by_id[fid].text for fid in _cited_ids(setup))
    assert chain.reviewer.rewrite_requests.count(_SETUP_WITH_PURPOSE) == 1


def test_인용에_없는_인식기준_인용문은_2장_원칙을_살리지_못하고_수익구성은_남긴다() -> None:
    misquoted = _SchemaCheckedReviewer(
        recognition_quotes=(_PROGRESS_QUOTE, _EXCEPTION_QUOTE.replace("1년", "2년")),
    )

    chain = _run_chain(reviewer=misquoted)

    assert _PRINCIPLE not in {fact.claim for fact in chain.report.fact_records}
    assert "semantic_grounding_invalid" in chain.reasons("business_model")
    # 같은 인용의 정상 수익 구성 사실은 영향 없이 남는다.
    assert chain.fact(_COMPOSITION).claim_slot == _REVENUE_MODEL_SLOT


def test_소재지_칸이_없는_조각을_인용한_소재지_문장은_검수_전에_빠진다() -> None:
    """선택 칸 운반은 «그 칸을 지원하는 조각»에만 열린다 — typed 가드는 그대로다."""

    plan = _writer_plan(
        location_cite=_cite("설립을 결정", label="identity:corporate_identity"),
    )

    chain = _run_chain(plan=plan)

    assert _LOCATION not in {row["text"] for row in chain.reviewer.verdicts}
    assert _LOCATION not in {fact.claim for fact in chain.report.fact_records}
    # 같은 장의 정답 사업 정의는 그대로 공개된다.
    assert chain.fact(_DEFINITION).section_owner == "identity"


# ── 7장 관계법인 각주: 입력 도달은 문장 정확성 증명이 아니다 ─────────────────


def _plan_with_footnote_sentences(*extra: _PlannedSentence):
    plan = dict(_writer_plan())
    plan["operations_partners"] = (*plan["operations_partners"], *extra)
    return plan


def test_7장_각주가_실려도_실제_대여거래와_제외조건을_지킨_문장은_남는다() -> None:
    chain = _run_chain(plan=_plan_with_footnote_sentences(
        _PlannedSentence(
            _EXCLUSION_KEPT, "operations_partners:partnership",
            _cite("종속기업에서 제외", label=""),
        ),
    ))

    by_id = {fragment.fragment_id: fragment for fragment in chain.fragments}
    footnote_ids = {
        fragment.fragment_id for fragment in chain.fragments
        if fragment.kind == LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE
    }
    assert len(footnote_ids) == 1
    assert "종속기업에서 제외" in chain.writer.prompts["operations_partners"]
    assert not any(
        "종속기업에서 제외" in chain.writer.prompts.get(section_id, "")
        for section_id in ("identity", "business_model")
    )
    kept = chain.fact(_EXCLUSION_KEPT)
    assert set(_cited_ids(kept)) == footnote_ids
    assert chain.fact(_LOAN).section_owner == "operations_partners"
    assert all("운영자금을 대여" in by_id[fid].text for fid in _cited_ids(chain.fact(_LOAN)))


def test_제외조건을_뺀_현재_연결대상_단정은_가짜검수가_참이어도_공개되지_않는다() -> None:
    # 2026-09-23 scope 경계 수정 전에는 strict xfail(알려진 공백)이었다. 원인은
    # 기계 검증기가 표 행 표식(「법인(*)」)↔같은 표식 각주의 «종속기업에서 제외»를
    # 읽지 못한 것이다(scope_guard._footnote_exclusion_problem). 기대를 지운 것이
    # 아니라 아래 단정이 모두 실제 검증기의 거절로 성립한다.
    chain = _run_chain(plan=_plan_with_footnote_sentences(
        _PlannedSentence(
            _CONSOLIDATED_WITHOUT_EXCLUSION, "operations_partners:partnership",
            _cite("종속기업에서 제외", label=""),
        ),
    ))

    # 가짜 검수자는 «참»이라고 답했다 — 제거는 실제 검증기의 몫이다.
    approved = {row["text"] for row in chain.reviewer.verdicts if row["결과"] == "참"}
    assert _CONSOLIDATED_WITHOUT_EXCLUSION in approved
    assert "scope_condition_unbound" in chain.reasons("operations_partners")
    assert _sha256(_CONSOLIDATED_WITHOUT_EXCLUSION) in {
        item.get("candidate_sha256") for item in chain.review_diagnostics
        if item.get("reason_code") == "scope_condition_unbound"
    }
    # 제한 재작성 대상으로 갔고(가짜 작가는 포기), SHADOW 공개 본문·canonical fact
    # 어디에도 남지 않는다.
    assert chain.reviewer.rewrite_requests.count(_CONSOLIDATED_WITHOUT_EXCLUSION) == 1
    assert _CONSOLIDATED_WITHOUT_EXCLUSION not in {
        fact.claim for fact in chain.report.fact_records
    }
    assert not any(_CONSOLIDATED_WITHOUT_EXCLUSION in text for text in chain.public_texts())
    # 대칭 보존 — 같은 실행의 정상 대여 거래는 관계자 조각 인용으로 남는다.
    loan = chain.fact(_LOAN)
    assert loan.section_owner == "operations_partners"
    by_id = {fragment.fragment_id: fragment for fragment in chain.fragments}
    assert all("운영자금을 대여" in by_id[fid].text for fid in _cited_ids(loan))


def test_각주를_인용하지_않은_현재종속_단정도_같은문서_제외각주로_공개되지_않는다() -> None:
    # 2026-09-23 배선(entity_scope_constraints → grounding/verify) 전에는 strict
    # xfail(배선 대기)이었다. 차단은 자기 인용 scope 가 아니라 같은 문서의 인용 밖
    # 각주 제약(세부 단계 entity_scope_exclusion)으로 성립하며, 긍정 근거는 그대로다.
    chain = _run_chain(plan=_plan_with_footnote_sentences(
        _PlannedSentence(
            _OWNCITE_WITHOUT_FOOTNOTE, "operations_partners:partnership",
            _cite("운영자금을 대여"),
        ),
    ))

    by_id = {fragment.fragment_id: fragment for fragment in chain.fragments}
    footnote_ids = {
        fragment.fragment_id for fragment in chain.fragments
        if fragment.kind == LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE
    }
    # 전제: 각주는 packet 에 있지만 후보는 관계자 조각만 인용했다.
    assert footnote_ids and "종속기업에서 제외" in chain.writer.prompts["operations_partners"]
    approved = {row["text"]: row for row in chain.reviewer.verdicts if row["결과"] == "참"}
    assert _OWNCITE_WITHOUT_FOOTNOTE in approved
    assert not set(approved[_OWNCITE_WITHOUT_FOOTNOTE]["근거"]) & footnote_ids
    assert "scope_condition_unbound" in chain.reasons("operations_partners")
    # 인용 밖 같은 문서 각주 제약이 막았다는 원문 없는 세부 진단.
    blocked = [
        item for item in chain.review_diagnostics
        if item.get("candidate_sha256") == _sha256(_OWNCITE_WITHOUT_FOOTNOTE)
    ]
    assert blocked and {item["reason_code"] for item in blocked} == {"scope_condition_unbound"}
    assert {"entity_scope_exclusion"} == {
        item.get("grounding_detail", {}).get("stage") for item in blocked
    }
    # 제한 재작성 대상으로 갔고(가짜 작가는 포기), 공개 본문·canonical fact 에 없다.
    assert chain.reviewer.rewrite_requests.count(_OWNCITE_WITHOUT_FOOTNOTE) == 1
    assert _OWNCITE_WITHOUT_FOOTNOTE not in {fact.claim for fact in chain.report.fact_records}
    assert not any(_OWNCITE_WITHOUT_FOOTNOTE in text for text in chain.public_texts())
    # 대칭 보존 — 같은 관계자 조각을 인용한 정상 대여 거래는 남는다.
    loan = chain.fact(_LOAN)
    assert loan.section_owner == "operations_partners"
    assert all("운영자금을 대여" in by_id[fid].text for fid in _cited_ids(loan))


def test_제외조건을_뺀_연결단정은_재작성으로_조건을_지킨_설명이_되면_각주인용으로_남는다() -> None:
    # 정상 재작성·재검수 경로의 대칭: 같은 각주 인용을 유지한 채 제외 조건을
    # 되살린 문장은 공개된다(인용을 바꾸거나 새 근거를 끼워 넣지 않는다).
    reviewer = _SchemaCheckedReviewer(rewrites={
        _SETUP_WITH_PURPOSE: _SETUP_FACT,
        _CONSOLIDATED_WITHOUT_EXCLUSION: _EXCLUSION_KEPT,
    })
    chain = _run_chain(reviewer=reviewer, plan=_plan_with_footnote_sentences(
        _PlannedSentence(
            _CONSOLIDATED_WITHOUT_EXCLUSION, "operations_partners:partnership",
            _cite("종속기업에서 제외", label=""),
        ),
    ))

    assert reviewer.rewrite_requests.count(_CONSOLIDATED_WITHOUT_EXCLUSION) == 1
    footnote_ids = {
        fragment.fragment_id for fragment in chain.fragments
        if fragment.kind == LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE
    }
    kept = chain.fact(_EXCLUSION_KEPT)
    assert kept.section_owner == "operations_partners"
    assert set(_cited_ids(kept)) == footnote_ids
    assert not any(_CONSOLIDATED_WITHOUT_EXCLUSION in text for text in chain.public_texts())

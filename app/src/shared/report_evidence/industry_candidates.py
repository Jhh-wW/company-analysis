"""회사 사실의 장·슬롯과 분리해 운반하는 공식 산업 조사 원문."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from src.shared.business_challenge_context import IndustryProblemEvidence
from src.shared.report_evidence.models import CollectedEvidenceDocument
from src.shared.report_evidence.section_context import parse_section_context
from src.shared.report_evidence.practice_context import parse_practice_context
from src.shared.report_evidence.source_context import parse_source_context
from src.shared.report_evidence.source_kind_policy import formal_document_is_writer_eligible

_LOCATION_RE = re.compile(r"^(?:chars:|raw_xml_chars:)?([0-9]{1,10})-([0-9]{1,10})$")


@dataclass(frozen=True)
class OfficialIndustryCandidateEvidence:
    """원문 관측일 뿐 회사의 문제·대응 또는 작성 근거 점수가 아니다."""

    company_id: str
    fragment_id: str
    document: CollectedEvidenceDocument
    location: str
    text_sha256: str
    text: str
    source_context_json: str = ""
    section_context_json: str = ""
    practice_context_json: str = ""

    def __post_init__(self) -> None:
        if type(self.document) is not CollectedEvidenceDocument:
            raise ValueError("산업 후보 문서의 자료형이 다릅니다")
        self.document.__post_init__()
        if self.company_id != self.document.company_id or not formal_document_is_writer_eligible(self.document):
            raise ValueError("산업 후보의 공식 회사 결속 또는 출처 자격이 다릅니다")
        for value in (self.company_id, self.fragment_id, self.location, self.text):
            if type(value) is not str or not value.strip():
                raise ValueError("산업 후보 식별자와 원문은 비어 있을 수 없습니다")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("산업 후보 원문 지문이 다릅니다")
        if self.text_sha256 not in self.document.exact_evidence_hashes:
            raise ValueError("산업 후보 원문이 문서의 수집 지문에 없습니다")
        location = _LOCATION_RE.fullmatch(self.location)
        if location is None:
            raise ValueError("산업 후보의 정확 문자 위치가 없습니다")
        start, end = map(int, location.groups())
        if end - start != len(self.text) or not any(
            span.start <= start < end <= span.end for span in self.document.usable_ranges
        ):
            raise ValueError("산업 후보 원문과 문서 사용 구간이 다릅니다")
        date.fromisoformat(self.document.published_on)
        parse_source_context(
            self.source_context_json, document_id=self.document_id,
            document_sha256=self.document.content_sha256,
            fragment_location=self.location, fragment_sha256=self.text_sha256,
            section_context_json=self.section_context_json, binding_scope='fragment',
        )
        parse_practice_context(
            self.practice_context_json, document_id=self.document_id,
            document_sha256=self.document.content_sha256,
            fragment_location=self.location, fragment_sha256=self.text_sha256,
            fragment_text=self.text,
        )
        parse_section_context(
            self.section_context_json, document_id=self.document_id,
            document_sha256=self.document.content_sha256,
            fragment_location=self.location, fragment_sha256=self.text_sha256,
        )

    @property
    def document_id(self) -> str:
        return self.document.document_id


@dataclass(frozen=True)
class OfficialIndustrySupplement:
    """최종 본문 작성 후 검수된 산업 설명과 그 조사 원문만 돌려준다."""

    problems: tuple[IndustryProblemEvidence, ...]
    candidates: tuple[OfficialIndustryCandidateEvidence, ...]

    def __post_init__(self) -> None:
        if type(self.problems) is not tuple or any(type(value) is not IndustryProblemEvidence for value in self.problems):
            raise ValueError("공식 산업 결과의 판정 자료형이 다릅니다")
        if type(self.candidates) is not tuple or any(type(value) is not OfficialIndustryCandidateEvidence for value in self.candidates):
            raise ValueError("공식 산업 결과의 원문 자료형이 다릅니다")
        if len({value.fragment_id for value in self.candidates}) != len(self.candidates):
            raise ValueError("공식 산업 결과의 원문 식별자가 중복됐습니다")
        for candidate in self.candidates:
            candidate.__post_init__()
        for problem in self.problems:
            problem.__post_init__()

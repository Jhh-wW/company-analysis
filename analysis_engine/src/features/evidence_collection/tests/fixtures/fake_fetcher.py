"""시험 전용 가짜 fetcher — 실제 네트워크 접근 0건. 로컬 값만 돌려준다."""

from __future__ import annotations

from dataclasses import dataclass, field

from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult


@dataclass
class FakeFetcher:
    """pblntf_ty·rcept_no별로 미리 정해 둔 응답만 돌려주는 가짜 DartFetcher.

    호출 횟수를 기록해 「pblntf_ty당 한 번만 조회했는지(비용 상한)」·
    「감사보고서 폴백을 실제로 시도했는지」 같은 시험에 쓴다.
    """

    list_responses_by_pblntf_ty: dict[str, FilingListResult] = field(default_factory=dict)
    document_responses_by_rcept_no: dict[str, DocumentFetchResult] = field(default_factory=dict)
    list_calls: list[tuple[str, str]] = field(default_factory=list)
    document_calls: list[str] = field(default_factory=list)

    def fetch_filing_list(self, company_id: str, pblntf_ty: str) -> FilingListResult:
        self.list_calls.append((company_id, pblntf_ty))
        return self.list_responses_by_pblntf_ty.get(
            pblntf_ty, FilingListResult(state="OK", rows=()),
        )

    def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
        self.document_calls.append(rcept_no)
        return self.document_responses_by_rcept_no.get(
            rcept_no, DocumentFetchResult(state="FAILED"),
        )


class RaisingFetcher:
    """fetch_*가 예외를 던지는 가짜 — 「조회 실패」 흡수 경로를 시험한다."""

    def fetch_filing_list(self, company_id: str, pblntf_ty: str) -> FilingListResult:
        raise ConnectionError("가짜 네트워크 실패(시험용)")

    def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
        raise ConnectionError("가짜 네트워크 실패(시험용)")

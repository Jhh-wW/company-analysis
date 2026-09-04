"""공시 수집기·공식 웹 수집기·장별 생산부를 «실제로» 이어 붙인 결합 종단시험.

왜 이 시험이 필요한가
---------------------
세 계층은 각자 자기 시험만 통과했다. 그런데 통합 검토에서
드러난 차단 결함은 전부 «각자는 초록불인데 서로 맞물리는 자리에서 계약이
깨지는» 종류였다(무신호 조각의 빈 section_id, 빈 slot_ids, 본문 없는 문서,
회사 소유권 누락). 각 계층의 단위시험으로는 절대 잡히지 않는다.

그래서 이 시험은 가짜 응답만 쓰되 **각 구현 함수 경로 그대로** 다음을 이어
돌린다. 실서비스 ``RealPipeline`` 호출 경로를 지난다는 뜻은 아니다.

    엔진 공시 수집 ─┐
                    ├→ 장별 근거 후보 생산 → 계약 최종 판정 → 생성 게이트
    공식 웹 수집 ───┘

★ feature 간 직접 import 금지 규칙과의 관계
    이 파일은 의도적으로 다른 feature(``homepage``)와 엔진 패키지를 import한다.
    그 규칙은 «생산 코드»가 서로 얽히는 것을 막기 위한 것이고, 여기서 얽히는
    것은 시험뿐이다. 생산 코드는 여전히 서로를 모르며, 결합점은 계약 Mapping
    하나다. 파일 위치를 옮기더라도 내용은 그대로 쓸 수 있다.

★ 실제 네트워크·실제 AI 호출은 0건이다. 전송 계층과 공시 조회기를 전부 가짜로
    주입한다.

★ 이 파일의 ``InjectedSlotFacts``는 **생성 게이트의 9장 모양만** 시험하는
  가짜 ID다. 실제 Fact payload·검산·운영 전달을 증명하지 않는다.
  아직 남은 일 — typed Fact payload를 운영 결합부에서 이 계약으로 전달하고
  별도 종단시험으로 증명하는 것은 이 파일의 범위 밖이다.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from src.features.chapter_evidence.constants import CompanyType
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError
from src.features.homepage.wide_fragments import build_fragments_for_collection
from src.features.homepage.wide_collect import collect_official_web_documents
from src.shared.report_evidence.constants import (
    EvidenceReadiness,
    GenerationGateStatus,
)
from src.shared.report_evidence.logic import assess_generation_gate, build_section_bundle
from src.shared.report_evidence.models import InjectedSlotFacts
from src.shared.report_evidence.policy import required_slots_for
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import (
    document_slots_for_formal_source_kind,
)

_FROZEN_SECTION_IDS = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
    "competitive_position",
)

_GATE_SHAPE_ONLY_INJECTED_SLOTS = {
    "past_changes": ("past_changes:historical_performance",),
    "competitive_position": (
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    ),
}

#: 엔진 수집기는 app 패키지가 아니라 ``analysis_engine/src`` 아래에 산다.
#: 통합 트리에서만 존재하므로 경로를 직접 얹고, 없으면 소리 나게 건너뛴다.
_ENGINE_SRC = pathlib.Path(__file__).resolve().parents[4].parent / "analysis_engine" / "src"
_ENGINE_FEATURE = _ENGINE_SRC / "features" / "evidence_collection"

pytestmark = pytest.mark.skipif(
    not _ENGINE_FEATURE.is_dir(),
    reason=f"엔진 수집기가 이 트리에 없습니다(통합 트리 전용): {_ENGINE_FEATURE}",
)

if _ENGINE_FEATURE.is_dir() and str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))
if _ENGINE_FEATURE.is_dir():
    # app/src/features가 일반 패키지라 analysis_engine/src/features namespace를
    # 가린다. 시험에서만 검색 경로를 합쳐 두 수집기를 실제로 연결한다.
    import features as _combined_features  # noqa: E402
    import core as _combined_core  # noqa: E402

    engine_feature_path = str(_ENGINE_SRC / "features")
    if engine_feature_path not in _combined_features.__path__:
        _combined_features.__path__.insert(0, engine_feature_path)
    engine_core_path = str(_ENGINE_SRC / "core")
    if engine_core_path not in _combined_core.__path__:
        _combined_core.__path__.insert(0, engine_core_path)


TARGET_COMPANY_ID = "00126380"
ROOT_HOST = "company.example"
ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"

#: 어느 슬롯 키워드에도 걸리지 않는 문단 — 계약이 빈 section_id·slot_id를
#: 거절하므로, 이런 문단이 최종 Mapping으로 새어 나가면 통합이 통째로 죽는다.
#: 통합 검토가 P0로 지목한 경로를 이 시험이 실제로 재현한다.
NO_SIGNAL_PARAGRAPH = (
    "\n\nX. 기타 참고사항\n"
    "본 항목은 별도로 기재할 내용이 없으며 관련 자료는 첨부를 참조한다.\n"
)


def _html(text: str, extra: str = "") -> str:
    return "<html><body><main><p>" + (text + " ") * 10 + "</p>" + extra + "</main></body></html>"


class _FakeSite:
    """가짜 전송 계층 — 미리 정한 URL만 응답하고 나머지는 접속 실패로 본다."""

    def __init__(self, pages: dict[str, WideRawResponse]) -> None:
        self._pages = pages

    def __call__(self, url: str, url_allowed):  # noqa: ANN001 - 전송 계약 그대로
        if url not in self._pages:
            raise WideTransportError(f"가짜 접속 실패: {url}")
        response = self._pages[url]
        if url_allowed is not None and not url_allowed(response.effective_url):
            raise WideTransportError(f"가짜 정책 차단: {url}")
        return response


def _page(text: str, url: str, content_type: str = "text/html") -> WideRawResponse:
    return WideRawResponse(status=200, text=text, effective_url=url, content_type=content_type)


def _absent(url: str) -> WideRawResponse:
    return WideRawResponse(status=404, text="", effective_url=url, content_type="")


def _ir_html_without_links(url: str, *_args, **_kwargs):
    """IR 탐색용 홈페이지는 정상으로 읽히지만 IR 링크가 없는 회사.

    대부분의 중소기업이 이 모양이다(홈페이지는 있고 IR 페이지는 없음).
    이때 IR 수집 결과는 «실패»가 아니라 «부재»여야 한다.
    """

    from src.features.homepage.ir_pdf import FetchedIrHtml

    return FetchedIrHtml("<html><body><p>회사 소개 페이지</p></body></html>", url)


def _ir_html_unreachable(url: str, *_args, **_kwargs):
    """IR 탐색용 홈페이지 접속 자체가 실패하는 회사(일시 장애 흉내)."""

    from src.features.homepage.ir_pdf import OfficialIrFetchError

    raise OfficialIrFetchError("가짜 IR 홈페이지 접속 실패")


def _ir_pdf_unused(*_args, **_kwargs):
    from src.features.homepage.ir_pdf import OfficialIrFetchError

    raise OfficialIrFetchError("가짜 IR PDF 없음")


def _collect_dart(*, document_state: str = "OK", with_no_signal: bool = True):
    """엔진 공시 수집을 가짜 조회기로 실제 실행한다."""

    from features.evidence_collection import collect  # noqa: PLC0415 - 경로 주입 뒤 import
    from features.evidence_collection.filing_select import RawFilingRow
    from features.evidence_collection.tests.fixtures import (  # noqa: PLC0415
        fake_fetcher,
        synthetic_documents,
    )

    text = synthetic_documents.LISTED_BUSINESS_REPORT_TEXT
    if with_no_signal:
        text += NO_SIGNAL_PARAGRAPH
    rows = (
        RawFilingRow(
            rcept_no="20250315000001",
            report_nm="사업보고서 (2024.12)",
            rcept_dt="20250315",
        ),
    )
    fetcher = fake_fetcher.FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": fake_fetcher.FilingListResult(state="OK", rows=rows),
            "F": fake_fetcher.FilingListResult(state="OK", rows=()),
        },
        document_responses_by_rcept_no={
            "20250315000001": fake_fetcher.DocumentFetchResult(
                state=document_state,
                text=text if document_state == "OK" else "",
            )
        },
    )
    return collect.collect_dart_evidence(
        fetcher=fetcher,
        company_id=TARGET_COMPANY_ID,
        now="2026-08-31T00:00:00Z",
        deadline_seconds=30,
    )


def _dart_mapping(**kwargs) -> dict[str, object]:
    from features.evidence_collection import serialize  # noqa: PLC0415

    return serialize.harvest_to_mapping(_collect_dart(**kwargs))


def _web_mapping(*, ir_html_fetch=_ir_html_without_links) -> dict[str, object]:
    """공식 웹 수집을 가짜 사이트로 실제 실행하고 계약 Mapping으로 바꾼다."""

    base = f"https://{ROOT_HOST}"
    pages = {
        f"{base}/robots.txt": _page(ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"),
        f"{base}/sitemap.xml": _absent(f"{base}/sitemap.xml"),
        f"{base}/": _page(
            _html("2010년에 설립한 법인이며 주요 사업을 영위합니다"),
            f"{base}/",
        ),
        f"{base}/careers": _page(
            _html("핵심가치와 일하는 방식"), f"{base}/careers"
        ),
    }
    pages[f"{base}/"] = _page(
        _html(
            "2010년에 설립한 법인이며 주요 사업을 영위합니다",
            '<a href="/careers">채용</a>',
        ),
        f"{base}/",
    )
    result = collect_official_web_documents(
        company_id=TARGET_COMPANY_ID,
        company_name="예시 전자",
        root_homepage_url=ROOT_HOST,
        collected_at="2026-08-31T00:00:00+00:00",
        transport=_FakeSite(pages),
        ir_html_fetch=ir_html_fetch,
        ir_pdf_fetch=_ir_pdf_unused,
    )
    fragments = build_fragments_for_collection(result)
    return to_evidence_mappings(result=result, fragments=fragments)


def _advertising_only_web_mapping() -> dict[str, object]:
    """고객 문제 해결 광고 한 문단뿐인 공식 root를 실제 수집한다."""

    base = f"https://{ROOT_HOST}"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _absent(f"{base}/sitemap.xml"),
        f"{base}/": _page(
            _html("고객의 문제를 해결하는 맞춤형 솔루션을 제공합니다."),
            f"{base}/",
        ),
    }
    result = collect_official_web_documents(
        company_id=TARGET_COMPANY_ID,
        company_name="예시 전자",
        root_homepage_url=ROOT_HOST,
        collected_at="2026-08-31T00:00:00+00:00",
        transport=_FakeSite(pages),
        ir_html_fetch=_ir_html_without_links,
        ir_pdf_fetch=_ir_pdf_unused,
    )
    return to_evidence_mappings(
        result=result,
        fragments=build_fragments_for_collection(result),
    )


def _produce(*, ir_html_fetch=_ir_html_without_links, **dart_kwargs):
    """두 수집기의 산출을 합쳐 아홉 장 후보를 실제로 만든다."""

    dart = _dart_mapping(**dart_kwargs)
    web = _web_mapping(ir_html_fetch=ir_html_fetch)
    return produce_from_collection_envelopes(
        company_id=TARGET_COMPANY_ID,
        company_type=CompanyType.LISTED,
        collection_envelopes=(dart, web),
    )


def _gate_shape_only_bundles(candidates):
    """가짜 사실 ID로 9장 게이트의 슬롯 모양만 채운다.

    실제 사실 본문이나 typed Fact payload의 존재를 뜻하지 않는다.
    """

    bundles = []
    for candidate in candidates:
        injected = tuple(
            InjectedSlotFacts(
                slot_id=slot_id,
                fact_ids=(f"gate-shape-only:{slot_id}",),
            )
            for slot_id in _GATE_SHAPE_ONLY_INJECTED_SLOTS.get(
                candidate.section_id, ()
            )
        )
        bundles.append(
            build_section_bundle(
                candidate,
                required_slot_ids=required_slots_for(candidate.section_id),
                injected_slot_facts=injected,
            )
        )
    return tuple(bundles)


def test_두_수집기_산출이_계약_경계를_예외_없이_통과한다() -> None:
    """무관 문단·본문 없는 문서·기반시설 조회가 섞여도 통합이 죽지 않는다."""

    candidates = _produce()

    assert len(candidates) == len(_FROZEN_SECTION_IDS)
    assert tuple(c.section_id for c in candidates) == _FROZEN_SECTION_IDS
    # 공허한 통과 방지 — 두 수집기가 실제로 근거를 냈는지 먼저 확인한다.
    assert any(candidate.fragments for candidate in candidates)
    assert any(candidate.documents for candidate in candidates)
    assert any(candidate.attempts for candidate in candidates)


def test_아홉_장이_같은_원문_묶음을_그대로_복사받지_않는다() -> None:
    """현행 결함(모든 장에 같은 조각을 통째로 전달)이 재발하지 않게 고정한다."""

    candidates = _produce()
    fragment_sets = [
        frozenset(fragment.fragment_id for fragment in candidate.fragments)
        for candidate in candidates
        if candidate.fragments
    ]

    assert len(fragment_sets) >= 2
    assert len(set(fragment_sets)) > 1

    sections_by_exact_range: dict[tuple[str, str, str], set[str]] = {}
    for candidate in candidates:
        for fragment in candidate.fragments:
            exact_range = (
                fragment.document_id,
                fragment.location,
                fragment.text_sha256,
            )
            sections_by_exact_range.setdefault(exact_range, set()).add(
                candidate.section_id
            )
    assert all(len(section_ids) == 1 for section_ids in sections_by_exact_range.values())


def test_다른_회사_값은_한_건도_섞이지_않는다() -> None:
    candidates = _produce()

    for candidate in candidates:
        assert candidate.company_id == TARGET_COMPANY_ID
        for document in candidate.documents:
            assert document.company_id == TARGET_COMPANY_ID
        for fragment in candidate.fragments:
            assert fragment.company_id == TARGET_COMPANY_ID
        for attempt in candidate.attempts:
            assert attempt.company_id == TARGET_COMPANY_ID


@pytest.mark.parametrize("which", ["dart", "web"])
def test_수집_envelope_최상위_회사가_다르면_배열을_합치기_전에_거절한다(which) -> None:
    dart = _dart_mapping()
    web = _web_mapping()
    target = dart if which == "dart" else web
    target["company_id"] = "other-company"

    with pytest.raises(ValueError, match="최상위 company_id"):
        produce_from_collection_envelopes(
            company_id=TARGET_COMPANY_ID,
            company_type=CompanyType.LISTED,
            collection_envelopes=(dart, web),
        )


def test_모든_조각이_원본_문서의_허용_해시에_결속된다() -> None:
    """계약 generation=7 — 문서 A의 조각을 문서 B에 붙이는 실수를 막는다."""

    candidates = _produce()
    checked = 0
    for candidate in candidates:
        allowed = {
            document.document_id: set(document.exact_evidence_hashes)
            for document in candidate.documents
        }
        for fragment in candidate.fragments:
            assert fragment.text_sha256 in allowed[fragment.document_id]
            checked += 1

    assert checked > 0


def test_gate_shape_only_가짜ID로_정상수집의_9장_게이트_모양만_READY다() -> None:
    """실제 payload 증명이 아니라, 빈 경로가 장애로 오인되지 않는지만 본다."""

    bundles = _gate_shape_only_bundles(_produce(document_state="OK"))
    injected_ids = {
        fact_id
        for bundle in bundles
        for injected in bundle.injected_slot_facts
        for fact_id in injected.fact_ids
    }
    assert injected_ids
    assert all(fact_id.startswith("gate-shape-only:") for fact_id in injected_ids)
    decision = assess_generation_gate(
        company_id=TARGET_COMPANY_ID,
        bundles=bundles,
        required_section_ids=_FROZEN_SECTION_IDS,
    )

    unobserved = [
        reason for reason in decision.reason_codes if "required_path_unobserved" in reason
    ]
    assert unobserved == []
    assert decision.status is GenerationGateStatus.READY_FOR_GENERATION
    assert decision.can_call_ai is True
    for bundle in bundles:
        assert bundle.readiness is EvidenceReadiness.READY


def test_광고문구만_있는_공식_root는_당면과제_종단판정이_READY가_아니다() -> None:
    candidates = produce_from_collection_envelopes(
        company_id=TARGET_COMPANY_ID,
        company_type=CompanyType.AUDIT_ONLY,
        collection_envelopes=(_advertising_only_web_mapping(),),
    )
    challenge = next(
        candidate
        for candidate in candidates
        if candidate.section_id == "current_challenges"
    )

    assert challenge.candidate_readiness is not EvidenceReadiness.READY
    assert not challenge.fragments


def test_공시_조회_실패는_자료_부족이_아니라_확인_못_함으로_판정한다() -> None:
    """거짓 확인 방향 — 수집 장애를 «자료 없음»으로 단정하면 안 된다."""

    bundles = _gate_shape_only_bundles(_produce(document_state="FAILED"))
    decision = assess_generation_gate(
        company_id=TARGET_COMPANY_ID,
        bundles=bundles,
        required_section_ids=_FROZEN_SECTION_IDS,
    )

    assert decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert decision.unknown_section_ids
    assert decision.can_call_ai is False


def test_보조_출처_한_곳의_실패가_보고서_전체를_일시_장애로_만들지_않는다() -> None:
    """유일한 REQUIRED 경로가 아닌 실패는 진단에만 남는다.

    IR 자료 조회만 실패하고 공시·공식 웹 페이지는 정상인 상황이다. 이때
    9개 장 전부가 «확인 못 함»이 되면, 사용자는 멀쩡한 회사에 대해 일시
    장애 중단을 받는다. 통합 결합시험이 실제로 잡아낸 회귀라 여기서 고정한다.
    """

    bundles = _gate_shape_only_bundles(
        _produce(ir_html_fetch=_ir_html_unreachable)
    )
    decision = assess_generation_gate(
        company_id=TARGET_COMPANY_ID,
        bundles=bundles,
        required_section_ids=_FROZEN_SECTION_IDS,
    )

    assert decision.status is not GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert decision.unknown_section_ids == ()


def test_무신호_문단은_최종_Mapping으로_새어_나가지_않는다() -> None:
    """계약은 빈 section_id·slot_id를 거절한다 — 관측은 남기되 조각은 안 낸다."""

    mapping = _dart_mapping(with_no_signal=True)

    assert mapping["fragments"]
    for fragment in mapping["fragments"]:
        assert fragment["section_id"]
        assert fragment["slot_id"]


def test_사업_반기_분기_실제산출이_앱_공식결과_계약까지_통과한다() -> None:
    """수동 envelope가 아니라 엔진 생산물로 자료종류별 슬롯 결속을 증명한다."""

    from features.evidence_collection import collect, serialize  # noqa: PLC0415
    from features.evidence_collection import constants as engine_constants  # noqa: PLC0415
    from features.evidence_collection.filing_select import RawFilingRow  # noqa: PLC0415
    from features.evidence_collection.tests.fixtures import fake_fetcher  # noqa: PLC0415

    business = RawFilingRow(
        "20260315000001", "사업보고서 (2025.12)", "20260315"
    )
    semiannual = RawFilingRow(
        "20260815000002", "반기보고서 (2026.06)", "20260815"
    )
    quarterly = RawFilingRow(
        "20261115000003", "분기보고서 (2026.09)", "20261115"
    )
    fetcher = fake_fetcher.FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": fake_fetcher.FilingListResult(
                state="OK", rows=(business, semiannual, quarterly)
            ),
        },
        document_responses_by_rcept_no={
            business.rcept_no: fake_fetcher.DocumentFetchResult(
                state="OK",
                text=(
                    "II. 사업의 내용\n"
                    "주요 매출은 제품 판매에서 발생하며 고객사에 서비스를 제공한다."
                ),
            ),
            semiannual.rcept_no: fake_fetcher.DocumentFetchResult(
                state="OK",
                text=(
                    "I. 회사의 개요\n"
                    "당사는 정밀부품을 생산하는 주식회사이며 법인이다.\n\n"
                    "II. 위험관리\n"
                    "원재료 가격 변동이 당면 과제이자 위험이며 대응 대책을 추진한다."
                ),
            ),
            quarterly.rcept_no: fake_fetcher.DocumentFetchResult(
                state="OK",
                text=(
                    "II. 주요 제품\n"
                    "대표 제품은 정밀 센서이며 핵심 제품의 매출 비중을 관리한다.\n\n"
                    "III. 요약재무정보\n"
                    "신규 생산라인 증설을 완료하여 공급 능력을 확대했다."
                ),
            ),
        },
    )

    harvest = collect.collect_dart_evidence(
        fetcher,
        TARGET_COMPANY_ID,
        now="2026-09-04",
    )
    mapping = serialize.harvest_to_mapping(harvest)
    candidates = produce_from_collection_envelopes(
        company_id=TARGET_COMPANY_ID,
        company_type=mapping["company_type"],
        collection_envelopes=(mapping,),
    )
    result = OfficialEvidenceCollectionResult(
        company_id=TARGET_COMPANY_ID,
        candidates=candidates,
    )

    assert result.independent_document_count == 3
    supplemental_fragments = [
        fragment
        for candidate in result.candidates
        for fragment in candidate.fragments
        if fragment.document_id.startswith(
            (
                engine_constants.SOURCE_KIND_SEMIANNUAL_REPORT,
                engine_constants.SOURCE_KIND_QUARTERLY_REPORT,
            )
        )
    ]
    assert supplemental_fragments
    documents = {
        document.document_id: document
        for candidate in result.candidates
        for document in candidate.documents
    }
    for fragment in supplemental_fragments:
        assert set(fragment.covered_slot_ids) <= set(
            document_slots_for_formal_source_kind(
                documents[fragment.document_id].source_kind
            )
        )
    assert any(
        fragment.document_id.startswith(
            engine_constants.SOURCE_KIND_SEMIANNUAL_REPORT
        )
        and fragment.section_id == "current_challenges"
        for fragment in supplemental_fragments
    )
    assert any(
        fragment.document_id.startswith(
            engine_constants.SOURCE_KIND_QUARTERLY_REPORT
        )
        and fragment.section_id == "past_changes"
        for fragment in supplemental_fragments
    )

from __future__ import annotations

import ast
import copy
import datetime as dt
import hashlib
import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.chapter_evidence.tests.fixtures import build_wisely_type_fixture
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError
from src.features.pipeline.official_evidence_preflight import assess_official_evidence
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
)
from src.shared.report_evidence.constants import (
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.runtime_port import (
    OfficialEvidenceCollectionRequest,
    OfficialEvidenceCollectionResult,
)
from src.web import official_evidence_adapter, runtime


COMPANY_ID = "00126380"


def _bind_synthetic_fixture_locations(envelope: dict[str, object]) -> None:
    """범용 chapter fixture를 adapter의 생산 location 계약 모양으로 바꾼다."""

    documents = envelope["documents"]
    fragments = envelope["fragments"]
    assert isinstance(documents, list)
    assert isinstance(fragments, list)
    for document in documents:
        assert isinstance(document, dict)
        document_id = str(document["document_id"])
        own_fragments = [
            fragment
            for fragment in fragments
            if isinstance(fragment, dict)
            and str(fragment["document_id"]) == document_id
        ]
        pair_order: list[tuple[str, str]] = []
        for fragment in own_fragments:
            pair = (str(fragment["text_sha256"]), str(fragment["text"]))
            if pair not in pair_order:
                pair_order.append(pair)
        ranges: list[dict[str, int]] = []
        location_by_hash: dict[str, str] = {}
        cursor = 0
        for index, (text_sha256, text) in enumerate(pair_order):
            start = cursor
            end = start + len(text)
            ranges.append({"start": start, "end": end})
            location_by_hash[text_sha256] = (
                f"{start}-{end}"
                if str(document["source_kind"]).startswith("dart_")
                else f"{document['canonical_url']}#{index}"
            )
            cursor = end + 1
        for fragment in own_fragments:
            fragment["location"] = location_by_hash[str(fragment["text_sha256"])]
        document["usable_ranges"] = ranges
        document["exact_evidence_bindings"] = [
            {"location": location, "text_sha256": text_sha256}
            for location, text_sha256 in sorted(
                {
                    (str(fragment["location"]), str(fragment["text_sha256"]))
                    for fragment in own_fragments
                }
            )
        ]


def test_실서비스_real_pipeline은_formal_collector_없이_조립될_수_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FULL 의미 계약이 테스트용 ``RealPipeline()`` 기본값에 기대지 않게 한다."""

    from src.features.pipeline.real import RealPipeline

    monkeypatch.setenv(runtime.PIPELINE_ENV, runtime.PIPELINE_REAL)

    pipeline = runtime.make_pipeline()

    assert isinstance(pipeline, RealPipeline)
    assert isinstance(
        pipeline._official_evidence_collector,  # noqa: SLF001 - 조립 계약 시험
        official_evidence_adapter.ProductionOfficialEvidenceCollector,
    )


def _split_wisely_envelopes() -> tuple[dict[str, object], dict[str, object]]:
    fixture = build_wisely_type_fixture(company_id=COMPANY_ID)
    dart_document_ids = {
        str(document["document_id"])
        for document in fixture["documents"]
        if str(document["source_kind"]).startswith("dart_")
    }
    dart_envelope = {
        "company_id": COMPANY_ID,
        "company_type": "audit_only",
        "documents": [
            document
            for document in fixture["documents"]
            if str(document["document_id"]) in dart_document_ids
        ],
        "fragments": [
            fragment
            for fragment in fixture["fragments"]
            if str(fragment["document_id"]) in dart_document_ids
        ],
        "attempts": [
            attempt
            for attempt in fixture["attempts"]
            if str(attempt["source_kind"]).startswith("dart_")
        ],
    }
    wide_envelope = {
        "company_id": COMPANY_ID,
        "documents": [
            document
            for document in fixture["documents"]
            if str(document["document_id"]) not in dart_document_ids
        ],
        "fragments": [
            fragment
            for fragment in fixture["fragments"]
            if str(fragment["document_id"]) not in dart_document_ids
        ],
        "attempts": [
            attempt
            for attempt in fixture["attempts"]
            if not str(attempt["source_kind"]).startswith("dart_")
        ],
    }
    _bind_synthetic_fixture_locations(dart_envelope)
    _bind_synthetic_fixture_locations(wide_envelope)
    return dart_envelope, wide_envelope


def _request(tmp_path: Path) -> OfficialEvidenceCollectionRequest:
    def get_json(_path, _params, _counter):
        raise AssertionError("가짜 시험에서 실제 DART JSON 함수가 호출되면 안 됩니다")

    def download(_receipt, _directory, _counter):
        raise AssertionError("가짜 시험에서 실제 DART 다운로드가 호출되면 안 됩니다")

    return OfficialEvidenceCollectionRequest(
        company_id=COMPANY_ID,
        company_name="예시전자",
        company_aliases=("EXAMPLE",),
        root_homepage_url="https://example.com",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=("https://ir.example.com/",),
        as_of_date=dt.date(2026, 9, 4),
        dart_document_cache_dir=tmp_path,
        dart_counter=object(),
        dart_get_json=get_json,
        dart_download_document=download,
    )


def test_adapter_import에는_무거운_엔진모듈의_정적_import가_없다() -> None:
    source = Path(official_evidence_adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    engine_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            engine_imports.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("features.evidence_collection")
            )
        elif isinstance(node, ast.ImportFrom) and str(node.module or "").startswith(
            "features.evidence_collection"
        ):
            engine_imports.append(str(node.module))

    assert engine_imports == []


def test_엔진트리가_없으면_다른동명모듈을_대신쓰지않는다(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(official_evidence_adapter.paths, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ImportError, match="엔진 경계"):
        official_evidence_adapter._typed_dart_collector_modules()


def test_실행시_적재한_typed모듈은_모두_analysis_engine_src에서_온다() -> None:
    engine_src = (
        official_evidence_adapter.paths.PROJECT_ROOT / "analysis_engine" / "src"
    ).resolve()

    modules = official_evidence_adapter._typed_dart_collector_modules()

    assert len(modules) == 3
    assert all(engine_src in Path(module.__file__).resolve().parents for module in modules)


def test_일반Writer조각은_location_hash_쌍과_실제_usable_range에_동시에_묶인다() -> None:
    dart_envelope, wide_envelope = _split_wisely_envelopes()

    official_evidence_adapter._classified_evidence_location_bindings(
        dart_envelope,
        company_id=COMPANY_ID,
    )
    official_evidence_adapter._classified_evidence_location_bindings(
        wide_envelope,
        company_id=COMPANY_ID,
    )

    forged = copy.deepcopy(dart_envelope)
    fragment = forged["fragments"][0]
    document_id = fragment["document_id"]
    document = next(
        item for item in forged["documents"] if item["document_id"] == document_id
    )
    # hash·원문은 진짜 값을 그대로 두고 위치와 선언 쌍까지 함께 바꿔도,
    # 실제 usable range가 아니므로 FULL adapter 경계에서 거절되어야 한다.
    fragment["location"] = "999999-1000000"
    document["exact_evidence_bindings"] = [
        {
            "location": (
                "999999-1000000"
                if item["text_sha256"] == fragment["text_sha256"]
                else item["location"]
            ),
            "text_sha256": item["text_sha256"],
        }
        for item in document["exact_evidence_bindings"]
    ]

    with pytest.raises(ValueError, match="usable range"):
        official_evidence_adapter._classified_evidence_location_bindings(
            forged,
            company_id=COMPANY_ID,
        )


def test_일반Writer조각_location만_바꾸면_exact_결속목록과_달라_거절한다() -> None:
    _dart_envelope, wide_envelope = _split_wisely_envelopes()
    forged = copy.deepcopy(wide_envelope)
    fragment = forged["fragments"][0]
    fragment["location"] = str(fragment["location"]) + "0"

    with pytest.raises(ValueError):
        official_evidence_adapter._classified_evidence_location_bindings(
            forged,
            company_id=COMPANY_ID,
        )


def test_엔진경계밖의_동명모듈은_적재뒤에도_거절한다(
    monkeypatch, tmp_path
) -> None:
    engine_dir = tmp_path / "analysis_engine" / "src" / "features" / "evidence_collection"
    engine_dir.mkdir(parents=True)
    for name in ("collect", "dart_fetcher", "serialize"):
        (engine_dir / f"{name}.py").write_text("", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()

    def wrong_module(name: str) -> ModuleType:
        module = ModuleType(name)
        module.__file__ = str(outside / (name.rsplit(".", 1)[-1] + ".py"))
        Path(module.__file__).write_text("", encoding="utf-8")
        return module

    monkeypatch.setattr(official_evidence_adapter.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        official_evidence_adapter,
        "_extend_loaded_package_path",
        lambda *_args: None,
    )
    monkeypatch.setattr(official_evidence_adapter.sys, "path", list(__import__("sys").path))
    monkeypatch.setattr(official_evidence_adapter.importlib, "import_module", wrong_module)

    with pytest.raises(ImportError, match="엔진 경계 밖"):
        official_evidence_adapter._typed_dart_collector_modules()


def test_DART전문_URL후보의_회사와_provenance가_깨지면_웹호출전에_거절한다() -> None:
    envelope = {
        "official_url_candidates": [
            {
                "company_id": "다른회사",
                "url": "https://official.example/",
                "source_document_id": "dart_audit_report:20250315000001",
                "source_receipt_no": "20250315000001",
                "source_member_name": "document.xml",
                "source_location": "raw_xml_chars:10-40",
                "source_document_sha256": "a" * 64,
                "source_payload_sha256": "b" * 64,
            }
        ]
    }

    with pytest.raises(ValueError, match="회사 식별자"):
        official_evidence_adapter._dart_official_candidate_provenance(
            envelope,
            company_id=COMPANY_ID,
        )

    envelope["official_url_candidates"][0]["company_id"] = COMPANY_ID
    envelope["official_url_candidates"][0]["source_member_name"] = "../other.xml"
    with pytest.raises(ValueError, match="provenance"):
        official_evidence_adapter._dart_official_candidate_provenance(
            envelope,
            company_id=COMPANY_ID,
        )


@pytest.mark.parametrize(
    ("receipt_no", "location"),
    (
        ("١" * 14, "raw_xml_chars:10-40"),
        ("20250315000001", "raw_xml_chars:" + "9" * 5_000 + "-10"),
    ),
)
def test_DART후보_접수번호와_원문위치는_ASCII_상한안에서만_받는다(
    receipt_no: str,
    location: str,
) -> None:
    envelope = {
        "official_url_candidates": [
            {
                "company_id": COMPANY_ID,
                "url": "https://official.example/",
                "source_document_id": f"dart_audit_report:{receipt_no}",
                "source_receipt_no": receipt_no,
                "source_member_name": "document.xml",
                "source_location": location,
                "source_document_sha256": "a" * 64,
                "source_payload_sha256": "b" * 64,
            }
        ]
    }

    with pytest.raises(ValueError, match="provenance"):
        official_evidence_adapter._dart_official_candidate_provenance(
            envelope,
            company_id=COMPANY_ID,
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1/company",
        "https://official.example:443/company",
        "https://official.example\\attacker.example/company",
        "https://user:secret@official.example/company",
    ),
)
def test_DART후보_URL은_엔진정본과_같은_닫힌형식만_통과한다(url: str) -> None:
    receipt_no = "20250315000001"
    envelope = {
        "official_url_candidates": [
            {
                "company_id": COMPANY_ID,
                "url": url,
                "source_document_id": f"dart_audit_report:{receipt_no}",
                "source_receipt_no": receipt_no,
                "source_member_name": "document.xml",
                "source_location": "raw_xml_chars:10-40",
                "source_document_sha256": "a" * 64,
                "source_payload_sha256": "b" * 64,
            }
        ]
    }

    with pytest.raises(ValueError, match="provenance"):
        official_evidence_adapter._dart_official_candidate_provenance(
            envelope,
            company_id=COMPANY_ID,
        )


def test_DART_serializer의_company_type누락은_undecided로_메우지않는다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """serializer 배선 손상을 회사유형 미확정·자료 부족으로 위장하지 않는다."""

    dart_envelope = {
        "company_id": COMPANY_ID,
        "documents": [],
        "fragments": [],
        "attempts": [],
    }
    wide_envelope = {
        "company_id": COMPANY_ID,
        "documents": [],
        "fragments": [],
        "attempts": [],
    }

    class FakeDartRuntimeFetcher:
        def __init__(self, **_kwargs) -> None:
            pass

    monkeypatch.setattr(
        official_evidence_adapter,
        "_typed_dart_collector_modules",
        lambda: (
            SimpleNamespace(collect_dart_evidence=lambda *_args, **_kwargs: object()),
            SimpleNamespace(DartRuntimeFetcher=FakeDartRuntimeFetcher),
            SimpleNamespace(harvest_to_mapping=lambda _harvest: dart_envelope),
        ),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "build_fragments_for_collection",
        lambda _result: (),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "to_evidence_mappings",
        lambda **_kwargs: wide_envelope,
    )

    with pytest.raises(ValueError, match="필수 company_type"):
        official_evidence_adapter.ProductionOfficialEvidenceCollector().collect(
            _request(tmp_path)
        )


def test_DART와_공식웹_typed_mapping을_한_merge경계로_합친다(
    monkeypatch, tmp_path
) -> None:
    dart_envelope, wide_envelope = _split_wisely_envelopes()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        official_evidence_adapter,
        "evidence_reclassify_enabled",
        lambda: True,
    )

    class FakeDartRuntimeFetcher:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            calls.append(("dart_fetcher", self))

    fake_harvest = object()

    def collect_dart(
        fetcher,
        company_id: str,
        *,
        now: str,
        short_observation_filter,
    ):
        calls.append(
            (
                "collect_dart",
                (fetcher, company_id, now, short_observation_filter),
            )
        )
        return fake_harvest

    def serialize_dart(harvest):
        calls.append(("serialize_dart", harvest))
        return dart_envelope

    monkeypatch.setattr(
        official_evidence_adapter,
        "_typed_dart_collector_modules",
        lambda: (
            SimpleNamespace(collect_dart_evidence=collect_dart),
            SimpleNamespace(DartRuntimeFetcher=FakeDartRuntimeFetcher),
            SimpleNamespace(harvest_to_mapping=serialize_dart),
        ),
    )

    fake_wide_result = object()
    fake_wide_fragments = (object(),)

    def collect_web(**kwargs):
        calls.append(("collect_web", kwargs))
        return fake_wide_result

    def build_web_fragments(result):
        calls.append(("build_web_fragments", result))
        return fake_wide_fragments

    def serialize_web(*, result, fragments):
        calls.append(("serialize_web", (result, fragments)))
        return wide_envelope

    merge_calls: list[dict[str, object]] = []

    def merge_once(**kwargs):
        merge_calls.append(kwargs)
        return produce_from_collection_envelopes(**kwargs)

    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        collect_web,
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "build_fragments_for_collection",
        build_web_fragments,
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "to_evidence_mappings",
        serialize_web,
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "produce_from_collection_envelopes",
        merge_once,
    )

    request = _request(tmp_path)
    result = official_evidence_adapter.ProductionOfficialEvidenceCollector().collect(
        request
    )

    assert tuple(candidate.section_id for candidate in result.candidates) == (
        REQUIRED_EVIDENCE_SECTION_IDS
    )
    assert len(merge_calls) == 1
    assert merge_calls[0]["company_id"] == COMPANY_ID
    assert merge_calls[0]["company_type"] == "audit_only"
    assert merge_calls[0]["collection_envelopes"] == (
        dart_envelope,
        wide_envelope,
    )
    assert result.independent_document_count == 4
    assert result.reclassify_source is not None
    assert result.reclassify_source.dart_envelope is dart_envelope
    assert result.reclassify_source.wide_envelope is wide_envelope

    by_section = {candidate.section_id: candidate for candidate in result.candidates}
    assert {document.source_kind for document in by_section["business_model"].documents} == {
        "dart_audit_report"
    }
    assert {fragment.slot_id for fragment in by_section["business_model"].fragments} == {
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    }
    assert {document.source_kind for document in by_section["portfolio"].documents} == {
        "official_web_page"
    }

    fetcher = calls[0][1]
    assert isinstance(fetcher, FakeDartRuntimeFetcher)
    assert fetcher.kwargs["counter"] is request.dart_counter
    assert fetcher.kwargs["get_json_fn"] is request.dart_get_json
    assert fetcher.kwargs["download_document_fn"] is request.dart_download_document
    assert fetcher.kwargs["require_official_url_sidecar"] is True
    assert fetcher.kwargs["today"]() == request.as_of_date
    collect_call = next(value for name, value in calls if name == "collect_dart")
    assert collect_call[3] is official_evidence_adapter._paragraph_has_comparison_candidate
    web_kwargs = next(value for name, value in calls if name == "collect_web")
    assert web_kwargs["company_registration_numbers"] == ("1234567890",)
    assert web_kwargs["official_candidate_urls"] == ("https://ir.example.com/",)
    assert web_kwargs["official_candidate_provenance"] == ()
    assert [name for name, _value in calls] == [
        "dart_fetcher",
        "collect_dart",
        "serialize_dart",
        "collect_web",
        "build_web_fragments",
        "serialize_web",
    ]


def test_짧은비교_filter는_같은문단의_다른_부정문에_오염되지_않는다() -> None:
    paragraph = (
        "알파전자는 베타전자와 경쟁하지 않습니다. "
        "가나다전자는 마바사와 경쟁합니다."
    )

    # 문단 전체를 한 문장 판별기에 넣으면 첫 부정 표현이 뒤의 참 문장까지
    # 거절한다. 생산 callback은 먼저 문장 경계를 나눠 하나라도 참이면 보존한다.
    assert not official_evidence_adapter.comparison_source_sentence_has_marker(paragraph)
    assert official_evidence_adapter._paragraph_has_comparison_candidate(paragraph)


def test_adapter는_hm_url이_비어도_DARTproof와_이중신원이_맞으면_Writer에_보낸다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """가짜 evidence를 손으로 보충하지 않는 생산 경계 통합시험.

    DART 쪽만 감사보고서 fixture로 고정하고, 공식 웹은 실제
    ``wide_collect → fragment → mapping → produce → runtime result``를 모두
    지난다. exact DART 문서·접수·첨부 hash proof와 실제 법인명·등록번호,
    HTTPS landing scope가 모두 맞을 때만 이 별도 등록 도메인이 TIER1
    공식 홈페이지 root가 되어 Writer 장 후보에 들어간다.
    """

    dart_envelope, _unused_wide_envelope = _split_wisely_envelopes()
    dart_envelope = dict(dart_envelope)

    class FakeDartRuntimeFetcher:
        def __init__(self, **_kwargs) -> None:
            pass

    fake_harvest = object()
    monkeypatch.setattr(
        official_evidence_adapter,
        "_typed_dart_collector_modules",
        lambda: (
            SimpleNamespace(
                collect_dart_evidence=lambda _fetcher, _company_id, *, now,
                short_observation_filter: fake_harvest
            ),
            SimpleNamespace(DartRuntimeFetcher=FakeDartRuntimeFetcher),
            SimpleNamespace(harvest_to_mapping=lambda harvest: dart_envelope),
        ),
    )

    candidate = "https://wise-shop.example/"
    robots = "https://wise-shop.example/robots.txt"
    sitemap = "https://wise-shop.example/sitemap.xml"
    official_html = """
    <html><body><main>
      <p>2018년에 설립한 주식회사 와이즐리컴퍼니는 생활용품을 제조 및 판매하는 주요 사업을 영위합니다.</p>
      <p>대표 제품과 핵심 제품군을 개인 고객에게 구독 서비스로 제공하며 판매로 매출을 얻는 수익 모델입니다.</p>
      <p>협력사와 공급망을 공동 운영하고 자체 생산 역할을 맡아 고객에게 서비스를 제공합니다.</p>
      <p>새 제품을 출시했고 고객 경험 개선 프로젝트를 완료한 성과가 있으며 향후 전략과 실행 계획을 추진 중입니다.</p>
      <p>핵심가치에 따라 일하는 방식을 적용해 고객 응대 개선 사례를 완료했고 제품 차별화와 경쟁력을 강화했습니다.</p>
    </main><footer>
      주식회사 와이즐리컴퍼니 · 사업자등록번호 123-45-67890
    </footer></body></html>
    """
    pages = {
        robots: WideRawResponse(
            status=404,
            text="",
            effective_url=robots,
            content_type="text/plain",
        ),
        candidate: WideRawResponse(
            status=200,
            text=official_html,
            effective_url=candidate,
            content_type="text/html",
        ),
        sitemap: WideRawResponse(
            status=404,
            text="",
            effective_url=sitemap,
            content_type="application/xml",
        ),
    }
    source_document = dart_envelope["documents"][0]
    source_receipt_no = "20250315000001"
    # 후보는 채점 조각이 0건인 DART 문서에서도 나올 수 있으므로, 이 ID가
    # envelope.documents에 들어 있어야 한다고 가정하지 않는다.
    source_document_id = f"dart_audit_report:{source_receipt_no}"
    dart_envelope["official_url_candidates"] = [
        {
            "company_id": COMPANY_ID,
            "url": candidate,
            "source_document_id": source_document_id,
            "source_receipt_no": source_receipt_no,
            "source_member_name": "covers/company.xml",
            "source_location": "raw_xml_chars:120-154",
            "source_document_sha256": source_document["content_sha256"],
            "source_payload_sha256": "f" * 64,
        }
    ]
    transport_calls: list[str] = []

    def transport(url: str, url_allowed):
        transport_calls.append(url)
        response = pages.get(url)
        if response is None:
            raise WideTransportError(f"가짜 접속 실패: {url}")
        if url_allowed is not None and not url_allowed(response.effective_url):
            raise WideTransportError("가짜 정책 경계를 벗어난 redirect")
        return response

    actual_collect_web = official_evidence_adapter.collect_official_web_documents

    def collect_web(**kwargs):
        return actual_collect_web(**kwargs, transport=transport)

    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        collect_web,
    )

    def forbidden_get_json(_path, _params, _counter):
        raise AssertionError("adapter가 company.json을 다시 조회했습니다")

    def forbidden_download(_receipt, _directory, _counter):
        raise AssertionError("고정 DART fixture에서 다운로드를 다시 열었습니다")

    request = OfficialEvidenceCollectionRequest(
        company_id=COMPANY_ID,
        company_name="주식회사 와이즐리컴퍼니",
        company_aliases=("Wisely Co., Ltd.",),
        root_homepage_url="",
        company_registration_numbers=("123-45-67890",),
        official_candidate_urls=(),
        as_of_date=dt.date(2026, 9, 4),
        dart_document_cache_dir=tmp_path,
        dart_counter=object(),
        dart_get_json=forbidden_get_json,
        dart_download_document=forbidden_download,
    )

    result = official_evidence_adapter.ProductionOfficialEvidenceCollector().collect(
        request
    )

    assert isinstance(result, OfficialEvidenceCollectionResult)
    assert result.company_id == COMPANY_ID
    assert transport_calls == [robots, candidate, sitemap]
    cross_documents = {
        document.document_id: document
        for chapter in result.candidates
        for document in chapter.documents
        if document.source_kind
        == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
    }
    assert cross_documents
    assert all(
        document.source_tier is SourceTier.TIER_1_OFFICIAL
        and document.requirement is SourceRequirement.REQUIRED
        for document in cross_documents.values()
    )
    assert any(
        fragment.document_id in cross_documents
        for chapter in result.candidates
        for fragment in chapter.fragments
    )


def test_재할당된_hm_url의_타사자료는_adapter와_preflight를_속이지_못한다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """풍부한 타사 문구도 회사 신원 불일치면 유료 작성 허가를 만들지 못한다."""

    dart_envelope, _unused_wide_envelope = _split_wisely_envelopes()

    class FakeDartRuntimeFetcher:
        def __init__(self, **_kwargs) -> None:
            pass

    fake_harvest = object()
    monkeypatch.setattr(
        official_evidence_adapter,
        "_typed_dart_collector_modules",
        lambda: (
            SimpleNamespace(
                collect_dart_evidence=lambda _fetcher, _company_id, *, now,
                short_observation_filter: fake_harvest
            ),
            SimpleNamespace(DartRuntimeFetcher=FakeDartRuntimeFetcher),
            SimpleNamespace(harvest_to_mapping=lambda _harvest: dart_envelope),
        ),
    )

    root = "https://reassigned.example/"
    product = "https://reassigned.example/products"
    html = f"""
    <html><body><main>
      <p>주식회사 다른컴퍼니는 대표 제품과 핵심 제품군을 개인 고객에게 판매해 매출을 얻습니다.</p>
      <p>핵심가치와 차별화 경쟁력을 바탕으로 향후 전략과 실행 계획을 추진 중입니다.</p>
      <a href="{product}">타사 제품 전체 보기</a>
    </main><footer>주식회사 다른컴퍼니 · 사업자등록번호 999-99-99999</footer>
    </body></html>
    """
    pages = {
        "https://reassigned.example/robots.txt": WideRawResponse(
            status=404,
            text="",
            effective_url="https://reassigned.example/robots.txt",
            content_type="text/plain",
        ),
        root: WideRawResponse(
            status=200,
            text=html,
            effective_url=root,
            content_type="text/html",
        ),
    }
    transport_calls: list[str] = []

    def transport(url: str, url_allowed):
        transport_calls.append(url)
        response = pages.get(url)
        if response is None:
            raise WideTransportError(f"가짜 접속 실패: {url}")
        if url_allowed is not None and not url_allowed(response.effective_url):
            raise WideTransportError("가짜 정책 경계를 벗어난 redirect")
        return response

    actual_collect_web = official_evidence_adapter.collect_official_web_documents
    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        lambda **kwargs: actual_collect_web(**kwargs, transport=transport),
    )

    request = OfficialEvidenceCollectionRequest(
        company_id=COMPANY_ID,
        company_name="주식회사 와이즐리컴퍼니",
        company_aliases=("Wisely Co., Ltd.",),
        root_homepage_url=root,
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(),
        as_of_date=dt.date(2026, 9, 4),
        dart_document_cache_dir=tmp_path,
        dart_counter=object(),
        dart_get_json=lambda *_args: (_ for _ in ()).throw(
            AssertionError("adapter가 company.json을 다시 조회했습니다")
        ),
        dart_download_document=lambda *_args: (_ for _ in ()).throw(
            AssertionError("고정 DART fixture에서 다운로드를 다시 열었습니다")
        ),
    )

    result = official_evidence_adapter.ProductionOfficialEvidenceCollector().collect(
        request
    )
    preflight = assess_official_evidence(result)

    assert not any(
        document.canonical_url.startswith(root)
        for chapter in result.candidates
        for document in chapter.documents
    )
    assert product not in transport_calls
    assert preflight.can_call_ai is False


def test_collect부터_preflight까지_무분류원문은_근거가아닌_내부분류결함으로_간다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """실제 collect→serialize를 지나되 네트워크와 AI는 전부 가짜다."""

    collect_module, _fetcher_module, serialize_module = (
        official_evidence_adapter._typed_dart_collector_modules()
    )
    filing_module = importlib.import_module(
        "features.evidence_collection.filing_select"
    )
    row = filing_module.RawFilingRow(
        "20250315000001",
        "사업보고서 (2025.03)",
        "20250315",
    )
    unclassified_text = """\
알림 사항
오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.

참고 사항
이 문단도 현재 분류 단어 없이 그저 문장을 채우기 위한 이야기다.
"""

    class FakeDartRuntimeFetcher:
        def __init__(self, **_kwargs) -> None:
            self.list_calls: list[str] = []

        def fetch_filing_list(self, _company_id: str, pblntf_ty: str):
            self.list_calls.append(pblntf_ty)
            return filing_module.FilingListResult(
                state="OK",
                rows=(row,) if pblntf_ty == "A" else (),
            )

        def fetch_document_text(self, rcept_no: str):
            assert rcept_no == row.rcept_no
            return filing_module.DocumentFetchResult(
                state="OK",
                text=unclassified_text,
                elapsed_ms=1,
                bytes_downloaded=len(unclassified_text.encode("utf-8")),
            )

    monkeypatch.setattr(
        official_evidence_adapter,
        "_typed_dart_collector_modules",
        lambda: (
            collect_module,
            SimpleNamespace(DartRuntimeFetcher=FakeDartRuntimeFetcher),
            serialize_module,
        ),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "build_fragments_for_collection",
        lambda _result: (),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "to_evidence_mappings",
        lambda **_kwargs: {
            "company_id": COMPANY_ID,
            "documents": [],
            "fragments": [],
            "attempts": [],
        },
    )

    result = official_evidence_adapter.ProductionOfficialEvidenceCollector().collect(
        _request(tmp_path)
    )
    preflight = assess_official_evidence(result)
    merged, added = merge_official_evidence_fragments({}, result)

    assert result.unclassified_evidence is not None
    assert result.unclassified_evidence.document_count == 1
    assert result.unclassified_evidence.fragment_count == 2
    assert len(result.unclassified_evidence.observation_sha256) == 64
    assert preflight.can_call_ai is False
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
    )
    # 원문과 빈 의미 칸은 장 후보→legacy 근거→writer 방향으로 절대 넘어가지
    # 않는다. 앱 결과에는 개수와 SHA-256만 남는다.
    assert added == 0
    assert merged == {}
    assert "오늘 날씨" not in repr(result)
    assert all(not candidate.fragments for candidate in result.candidates)


def test_adapter는_무분류조각에_주장슬롯을_붙인_우회를_거절한다() -> None:
    text = "현재 분류하지 못한 공식 원문"
    envelope = {
        "unclassified_documents": [
            {
                "company_id": COMPANY_ID,
                "document_id": "dart_audit_report:20250315000001",
                "canonical_url": (
                    "https://dart.fss.or.kr/dsaf001/"
                    "main.do?rcpNo=20250315000001"
                ),
                "source_tier": "TIER_1_OFFICIAL",
                "source_kind": "dart_audit_report",
                "requirement": "REQUIRED",
                "content_sha256": "a" * 64,
                "usable_ranges": [{"start": 0, "end": len(text)}],
                "exact_evidence_hashes": [],
                "identity_binding": (
                    "corp_code=00126380;rcept_no=20250315000001;"
                    "source_kind=dart_audit_report;"
                    "identity_check=unverifiable_no_fetcher_metadata"
                ),
            }
        ],
        "unclassified_fragments": [
            {
                "company_id": COMPANY_ID,
                "fragment_id": "unclassified-1",
                "document_id": "dart_audit_report:20250315000001",
                "location": f"0-{len(text)}",
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "section_id": "business_model",
                "slot_id": "business_model:revenue_model",
                "covered_slot_ids": ["business_model:revenue_model"],
                "score_millis": 900,
                "reason_codes": ["no_signal"],
            }
        ],
    }

    with pytest.raises(ValueError, match="주장 의미"):
        official_evidence_adapter._unclassified_evidence_observation(
            envelope,
            company_id=COMPANY_ID,
        )


def test_비교후보_차선은_짧은_공식원문_한문장만_문서hash에_결속한다() -> None:
    competition = "가나다전자는 베타전자와 경쟁합니다."
    noise = "잡음"
    document_id = "dart_business_report:20250315000001"
    document_hash = hashlib.sha256("full-document".encode("utf-8")).hexdigest()

    def fragment(index: int, text: str) -> dict[str, object]:
        start = index * 30
        return {
            "company_id": COMPANY_ID,
            "fragment_id": f"short-{index}",
            "document_id": document_id,
            "location": f"{start}-{start + len(text)}",
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "section_id": "",
            "slot_id": "",
            "covered_slot_ids": [],
            "score_millis": 0,
            "reason_codes": ["no_signal"],
        }

    envelope = {
        "unclassified_documents": [
            {
                "company_id": COMPANY_ID,
                "document_id": document_id,
                "canonical_url": (
                    "https://dart.fss.or.kr/dsaf001/"
                    "main.do?rcpNo=20250315000001"
                ),
                "source_tier": "TIER_1_OFFICIAL",
                "source_kind": "dart_business_report",
                "publisher": "금융감독원 전자공시시스템(DART)",
                "title": "사업보고서 (2024.12)",
                "published_on": "20250315",
                "collected_at": "2026-09-04T00:00:00+09:00",
                "content_sha256": document_hash,
                "usable_ranges": [
                    {"start": 30, "end": 30 + len(noise)},
                    {"start": 60, "end": 60 + len(competition)},
                ],
                "exact_evidence_hashes": [],
                "identity_binding": (
                    "corp_code=00126380;rcept_no=20250315000001;"
                    "source_kind=dart_business_report;"
                    "identity_check=unverifiable_no_fetcher_metadata"
                ),
                "collector_version": "evidence_collection/1.0",
                "parser_version": "evidence_collection_segment/1.0",
                "requirement": "REQUIRED",
            }
        ],
        "unclassified_fragments": [
            fragment(1, noise),
            fragment(2, competition),
        ],
    }

    candidates = official_evidence_adapter._comparison_candidate_evidence(
        envelope,
        company_id=COMPANY_ID,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.evidence_text == competition
    assert candidate.document_id == document_id
    assert candidate.document_content_sha256 == document_hash
    assert candidate.location.endswith(":sentence:0")
    assert candidate.evidence_sha256 == hashlib.sha256(
        competition.encode("utf-8")
    ).hexdigest()
    assert not hasattr(candidate, "section_id")
    assert not hasattr(candidate, "slot_id")

    forged_location = copy.deepcopy(envelope)
    forged_location["unclassified_fragments"][1]["location"] = "61-80"
    with pytest.raises(ValueError, match="원문 구간"):
        official_evidence_adapter._comparison_candidate_evidence(
            forged_location,
            company_id=COMPANY_ID,
        )

    overlapping_ranges = copy.deepcopy(envelope)
    overlapping_ranges["unclassified_documents"][0]["usable_ranges"] = [
        {"start": 30, "end": 40},
        {"start": 35, "end": 70},
    ]
    with pytest.raises(ValueError, match="구간이 손상"):
        official_evidence_adapter._comparison_candidate_evidence(
            overlapping_ranges,
            company_id=COMPANY_ID,
        )

    non_dart_source = copy.deepcopy(envelope)
    non_dart_source["unclassified_documents"][0]["source_kind"] = (
        "official_web_page"
    )
    with pytest.raises(ValueError, match="문서 신원"):
        official_evidence_adapter._comparison_candidate_evidence(
            non_dart_source,
            company_id=COMPANY_ID,
        )

    wrong_requirement = copy.deepcopy(envelope)
    wrong_requirement["unclassified_documents"][0]["requirement"] = "OPTIONAL"
    with pytest.raises(ValueError, match="문서 신원"):
        official_evidence_adapter._comparison_candidate_evidence(
            wrong_requirement,
            company_id=COMPANY_ID,
        )

    credentialed_url = copy.deepcopy(envelope)
    credentialed_url["unclassified_documents"][0]["canonical_url"] = (
        "https://attacker@dart.fss.or.kr/dsaf001/"
        "main.do?rcpNo=20250315000001"
    )
    with pytest.raises(ValueError, match="문서 신원"):
        official_evidence_adapter._comparison_candidate_evidence(
            credentialed_url,
            company_id=COMPANY_ID,
        )

    forged_company_binding = copy.deepcopy(envelope)
    forged_company_binding["unclassified_documents"][0]["identity_binding"] = (
        "corp_code=99999999;rcept_no=20250315000001;"
        "source_kind=dart_business_report;identity_check=verified_match"
    )
    with pytest.raises(ValueError, match="회사 결속"):
        official_evidence_adapter._comparison_candidate_evidence(
            forged_company_binding,
            company_id=COMPANY_ID,
        )


def test_무분류관측지문은_나열순서가아닌_원문hash에_결속된다() -> None:
    def pair(index: int, text: str) -> tuple[dict[str, object], dict[str, object]]:
        document_id = f"dart_audit_report:2025031500000{index}"
        receipt_number = document_id.rpartition(":")[2]
        start = index * 10
        return (
            {
                "company_id": COMPANY_ID,
                "document_id": document_id,
                "canonical_url": (
                    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
                    + receipt_number
                ),
                "source_tier": "TIER_1_OFFICIAL",
                "source_kind": "dart_audit_report",
                "requirement": "REQUIRED",
                "content_sha256": hashlib.sha256(
                    f"document-{index}".encode("utf-8")
                ).hexdigest(),
                "usable_ranges": [{"start": start, "end": start + len(text)}],
                "exact_evidence_hashes": [],
                "identity_binding": (
                    f"corp_code={COMPANY_ID};rcept_no={receipt_number};"
                    "source_kind=dart_audit_report;"
                    "identity_check=unverifiable_no_fetcher_metadata"
                ),
            },
            {
                "company_id": COMPANY_ID,
                "fragment_id": f"unclassified-{index}",
                "document_id": document_id,
                "location": f"{start}-{start + len(text)}",
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "section_id": "",
                "slot_id": "",
                "covered_slot_ids": [],
                "score_millis": 0,
                "reason_codes": ["no_signal"],
            },
        )

    first = pair(1, "분류하지 못한 첫 공식 원문")
    second = pair(2, "분류하지 못한 둘째 공식 원문")
    normal = {
        "unclassified_documents": [first[0], second[0]],
        "unclassified_fragments": [first[1], second[1]],
    }
    reversed_items = {
        "unclassified_documents": [second[0], first[0]],
        "unclassified_fragments": [second[1], first[1]],
    }
    changed_second = pair(2, "내용이 바뀐 둘째 공식 원문")
    changed = {
        "unclassified_documents": [first[0], changed_second[0]],
        "unclassified_fragments": [first[1], changed_second[1]],
    }

    normal_observation = official_evidence_adapter._unclassified_evidence_observation(
        normal,
        company_id=COMPANY_ID,
    )
    reversed_observation = (
        official_evidence_adapter._unclassified_evidence_observation(
            reversed_items,
            company_id=COMPANY_ID,
        )
    )
    changed_observation = official_evidence_adapter._unclassified_evidence_observation(
        changed,
        company_id=COMPANY_ID,
    )

    assert normal_observation is not None
    assert reversed_observation is not None
    assert changed_observation is not None
    assert (
        normal_observation.observation_sha256
        == reversed_observation.observation_sha256
    )
    assert (
        normal_observation.observation_sha256
        != changed_observation.observation_sha256
    )

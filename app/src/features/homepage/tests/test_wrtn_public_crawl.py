"""보존된 실제 공개 HTML로 기본 수집 경로를 재생한다. 네트워크 호출은 없다."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.homepage import wide_collect
from src.features.homepage.ir_pdf import FetchedIrHtml, OfficialIrFetchError
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError
from src.features.homepage.wide_fragments import build_fragments_for_collection
from src.features.pipeline.official_news_aliases import official_news_aliases
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.web.official_evidence_adapter import _classified_evidence_location_bindings


pytestmark = pytest.mark.local_integration
PROFILE = {"corp_code": "01921445", "corp_name": "주식회사 뤼튼테크놀로지스", "hm_url": "wrtn.io"}
EXPECTED_PAGES = (
    "https://wrtn.io/",
    "https://wrtn.io/careers/",
    "https://career.wrtn.io/o/119686",
    "https://wrtn.io/news/",
    "https://wrtn.io/en/news-en/",
    "https://wrtn.io/company/",
    "https://wrtn.io/en/company-en/",
    "https://wrtn.io/en/service-crack-en/",
    "https://wrtn.io/en/service-kyarapu-en/",
    "https://wrtn.io/en/careers-en/",
    "https://wrtn.io/ja/careers-ja/",
    "https://wrtn.io/ja/news-ja/",
)


def _public_responses():
    root = Path(os.environ.get("WRTN_PUBLIC_CRAWL_FIXTURE_DIR") or (
        Path(__file__).resolve().parents[4] / ".local_evaluation_runs/wrtn-crawl-replay-20260923"
    ))
    if not (root / "manifest.json").is_file():
        pytest.skip("공개 HTML 보존본 없음: WRTN_PUBLIC_CRAWL_FIXTURE_DIR로 지정")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    responses = {}
    for url, row in manifest.items():
        raw = (root / row["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"], url
        responses[url] = WideRawResponse(row["status"], raw.decode("utf-8"),
                                         row["effective_url"], row["content_type"])
    assert manifest["https://wrtn.io/"]["sha256"] == "191827586251b261ec7e8cbd6c458940aecd4f400ca9dd2edcd5818412f03c84"
    assert manifest["https://wrtn.io/news/"]["sha256"] == "50f61c70910018961a1cd729148417272fa1b4390c1eaa0855f608f75cf931ce"
    return responses


@pytest.mark.parametrize("sitemap_failure", [False, True], ids=["공개응답", "sitemap장애대조"])
def test_default_crawl_reaches_newsroom_inside_unchanged_budget(sitemap_failure):
    responses = _public_responses()
    calls, ir_calls = [], []

    def transport(url, url_allowed=None):
        calls.append(url)
        assert url in responses, f"확보하지 않은 공개 응답: {url}"
        if sitemap_failure and url == "https://career.wrtn.io/sitemap.xml":
            # 진단의 sitemap 실패를 별도 대조한다. 실제 실패 URL을 단정하지 않는다.
            raise WideTransportError("sitemap 장애 대역")
        response = responses[url]
        if url_allowed is not None and not url_allowed(response.effective_url):
            raise WideTransportError("수집기 원래 범위 검사에서 최종 URL 거절")
        return response

    def ir_html(url, *args, **kwargs):
        ir_calls.append(url)
        assert url in responses, f"확보하지 않은 공개 IR HTML: {url}"
        response = responses[url]
        return FetchedIrHtml(response.text, response.effective_url)

    def ir_pdf(*args, **kwargs):
        raise OfficialIrFetchError("보존 HTML에 PDF 원문 없음")

    result = wide_collect.collect_official_web_documents(
        company_id=PROFILE["corp_code"], company_name=PROFILE["corp_name"],
        root_homepage_url=PROFILE["hm_url"], collected_at="2026-09-23T00:00:00Z",
        # 공개 footer의 번호와 실측 보고서의 법인명을 사용한다. 신원 검사는 끄지 않는다.
        company_registration_numbers=("202-81-67042",),
        domain_attestation_source_id="dart-company-profile-01921445",
        domain_attestation_evidence=json.dumps(PROFILE, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        transport=transport, ir_html_fetch=ir_html, ir_pdf_fetch=ir_pdf,
    )
    pages = [url for url in calls if not url.endswith(("/robots.txt", "/sitemap.xml"))]
    assert tuple(pages) == EXPECTED_PAGES
    assert len(pages) == wide_collect.WIDE_MAX_PAGES == 12
    assert len(calls) == 16 and len(ir_calls) == 4
    counts = Counter(attempt.reason_code for attempt in result.attempts)
    assert counts["truncated_page_cap"] == 2
    assert counts["root_identity_verified"] == 1
    assert counts["root_identity_name_only"] == 0
    assert counts["network_failed"] == 1  # 실제 채용 URL의 /ko/ redirect는 기존 경계가 거절한다.
    # wrtn.io의 sitemap.xml → sitemap_index.xml redirect는 기존 인프라 경계가 거절한다.
    assert counts["sitemap_failed"] == 1 + int(sitemap_failure)
    assert Counter(attempt.source_kind for attempt in result.attempts if attempt.reason_code == "page_ok") == {
        "official_web_page": 7, "official_recruit_page": 3,
    }

    envelope = to_evidence_mappings(result=result, fragments=build_fragments_for_collection(result))
    _classified_evidence_location_bindings(envelope, company_id=PROFILE["corp_code"])
    assert len(result.documents) == 11
    assert {row["canonical_url"] for row in envelope["documents"]} == {
        "https://wrtn.io/news/", "https://wrtn.io/company/",
    }
    company = next(row for row in envelope["documents"] if row["canonical_url"] == "https://wrtn.io/company/")
    assert company["content_sha256"] == "6dda01a5e69e994271a785cf31f69e34fc94e9c2393849b8ad9abe01609702a7"
    result_typed = OfficialEvidenceCollectionResult(
        company_id=PROFILE["corp_code"],
        candidates=produce_from_collection_envelopes(
            company_id=PROFILE["corp_code"], company_type="audit_only", collection_envelopes=(envelope,),
        ),
    )
    aliases = official_news_aliases(PROFILE, result_typed)
    assert len(aliases) == 1 and aliases[0].alias == "뤼튼"
    assert aliases[0].canonical_url == "https://wrtn.io/news/"
    assert aliases[0].document_sha256 == "0826c07e5ec055c091135727dcb78af2587054a871f617179640e4fec824d530"
    assert aliases[0].definition_sha256 == "93e4cb71525659c561dbaae252a4eefd5da5bb5a578afb07beb25ab9df713dc5"

"""실제 전송 경계에서 301/308 root 이동만 작성 자격으로 연결한다."""

import json

import pytest

from src.features.homepage import safe_http
from src.features.homepage.tests.test_blocked_redirect_discovery import (
    OLD, NEW, LEGAL_NAME, NUMBER, BODY, FOOTER, _FakeResponse, install_site, no_ir,
)
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fragments import build_fragments_for_collection
from src.shared.report_evidence.profile_domain_attestation import dart_profile_attestation_allows_source_url

PROFILE = json.dumps({"corp_code": "01234567", "corp_name": LEGAL_NAME, "hm_url": OLD},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def collect(*, evidence=PROFILE, attestation_id="dart-company-profile-01234567"):
    return collect_official_web_documents(company_id="01234567", company_name=LEGAL_NAME,
        company_aliases=("가나다전자",), company_registration_numbers=(NUMBER,), root_homepage_url=OLD,
        domain_attestation_source_id=attestation_id if evidence else "",
        domain_attestation_evidence=evidence, root_identity_verification_required=True,
        collected_at="2026-10-10T00:00:00+09:00", ir_html_fetch=no_ir, ir_pdf_fetch=no_ir)


@pytest.mark.parametrize("status", [301, 308])
def test_원_관측과_profile을_보존하여_본문을_공식_조각으로_전달한다(monkeypatch, status):
    calls = install_site(monkeypatch)
    original = safe_http._SafeHTTPSHandler.https_open
    def response(handler, request):
        if request.full_url == OLD and status == 308:
            calls.append(OLD)
            return _FakeResponse(OLD, code=status, headers={"Location": NEW})
        return original(handler, request)
    monkeypatch.setattr(safe_http._SafeHTTPSHandler, "https_open", response)
    result = collect()
    documents = [doc for doc in result.documents if doc.canonical_url == NEW]
    assert documents and all(doc.source_kind == "official_web_page" for doc in documents)
    assert all(doc.source_tier == "TIER_1_OFFICIAL" and doc.requirement == "REQUIRED" for doc in documents)
    assert all(doc.domain_attestation_evidence == PROFILE for doc in documents)
    assert all(dart_profile_attestation_allows_source_url(PROFILE, source_url=doc.canonical_url,
        redirect_verification=doc.domain_redirect_verification,
        redirect_from_host=doc.domain_redirect_from_host, redirect_to_host=doc.domain_redirect_to_host) for doc in documents)
    assert build_fragments_for_collection(result)
    assert result.redirect_discoveries[0].status == status
    assert any(a.state == "FAILED" and a.reason_code == "redirect_scope_blocked" for a in result.attempts)
    assert NEW + "sitemap.xml" not in calls
    assert to_evidence_mappings(result=result, fragments=build_fragments_for_collection(result))["documents"]


@pytest.mark.parametrize("status", [302, 303, 307])
def test_일시이동의_이중신원_문서는_기존_감사_차선에_유지한다(monkeypatch, status):
    install_site(monkeypatch)
    original = safe_http._SafeHTTPSHandler.https_open
    def response(handler, request):
        return (_FakeResponse(OLD, code=status, headers={"Location": NEW})
                if request.full_url == OLD else original(handler, request))
    monkeypatch.setattr(safe_http._SafeHTTPSHandler, "https_open", response)
    result = collect()
    assert result.documents and all(doc.requirement == "OPTIONAL" for doc in result.documents)
    assert not build_fragments_for_collection(result)


def test_이동_root_작성_승격은_외부링크_추가탐색을_열지않는다(monkeypatch):
    body = BODY + FOOTER + '<a href="https://external-company.example/">외부 링크</a>'
    before_calls = install_site(monkeypatch, body=body)
    before = collect(evidence="")
    after_calls = install_site(monkeypatch, body=body)
    after = collect()
    assert list(before_calls) == list(after_calls)
    assert len(before.documents) == len(after.documents)
    assert not any("external-company.example" in url for url in after_calls)


def test_등록번호_없으면_이동_root의_회사명만으로_승격하지않는다(monkeypatch):
    install_site(monkeypatch, body=BODY + "<footer>가나다전자 주식회사</footer>")
    result = collect()
    assert not result.documents


@pytest.mark.parametrize("attestation_id", ["", "dart-company-profile-99999999"])
def test_이동_영수증은_현재_단일_DART_profile_Source_ID를_요구한다(monkeypatch, attestation_id):
    install_site(monkeypatch)
    result = collect(attestation_id=attestation_id)
    assert result.documents and all(doc.requirement == "OPTIONAL" for doc in result.documents)


@pytest.mark.parametrize(("status", "location"), [(301, NEW), (308, NEW), (301, NEW.rstrip("/"))])
def test_보조_하위host의_같은본문이_공식_service_원문을_먼저_소비하지않는다(monkeypatch, status, location):
    service = NEW + "services/monitoring"
    auxiliary = "https://careers.new-company.example/"
    shared_body = '<main><p>관제 솔루션은 센서 상태를 감시하여 기업 고객의 현장 운영을 지원하는 서비스입니다.</p></main>' + FOOTER
    root_body = BODY + FOOTER + f'<a href="{auxiliary}">채용</a><a href="{service}">서비스</a>'
    calls = install_site(monkeypatch, body=root_body)
    original = safe_http._SafeHTTPSHandler.https_open
    def response(handler, request):
        if request.full_url == OLD:
            calls.append(OLD)
            return _FakeResponse(OLD, code=status, headers={"Location": location})
        if request.full_url in (service, auxiliary):
            calls.append(request.full_url)
            return _FakeResponse(request.full_url, code=200, headers={"Content-Type": "text/html; charset=utf-8"}, body=shared_body.encode())
        return original(handler, request)
    monkeypatch.setattr(safe_http._SafeHTTPSHandler, "https_open", response)
    result = collect()
    assert calls.index(service) < calls.index(auxiliary)
    document, = [doc for doc in result.documents if doc.canonical_url == service]
    assert document.source_tier == "TIER_1_OFFICIAL" and document.domain_attestation_evidence == PROFILE
    assert build_fragments_for_collection(result)
    assert any(a.reason_code == "duplicate_content_or_empty" for a in result.attempts)

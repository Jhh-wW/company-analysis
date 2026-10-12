"""영구 이동 proof의 원 profile·관측·범위와 공개 봉인을 검증한다."""

import hashlib
import json
from dataclasses import replace

import pytest

from src.shared.company_identity import exact_company_name_key
from src.shared.report_evidence.permanent_homepage_relocation import (
    PERMANENT_RELOCATION_PREFIX, build_permanent_homepage_relocation,
)
from src.shared.report_evidence.profile_domain_attestation import dart_profile_attestation_allows_source_url
from src.shared.report_evidence.source_kind_policy import formal_web_public_source_metadata
from src.features.provenance.sources import (
    build_dart_profile_attester_source, has_valid_provenance_seal,
    official_domain_attestation_problem, seal_collected_source,
    Source, SourceKind,
)

OLD = "https://prior-company.example/"
NEW = "https://current-company.example/"
NAME = "가상 제조회사"
PROFILE = json.dumps({"corp_code": "01234567", "corp_name": NAME, "hm_url": OLD},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def proof(**updates):
    fields = dict(profile_evidence=PROFILE,
        redirect={"source_url": OLD, "target_url": NEW, "status": 301,
                  "raw_location": NEW, "observed_at": "2026-10-10T01:00:00+00:00"},
        candidate_url=NEW, scope_sha256=sha(NEW), identity_evidence_sha256=sha("원 HTML 신원"),
        matched_name_sha256=sha(exact_company_name_key(NAME)),
        registration_number_sha256=sha("1234567890"), expected_registration_hashes=(sha("1234567890"),))
    fields.update(updates)
    return build_permanent_homepage_relocation(**fields)


def allows(verification, url=NEW, evidence=PROFILE, **updates):
    fields = dict(source_url=url, redirect_verification=verification,
                  redirect_from_host="prior-company.example", redirect_to_host="current-company.example")
    fields.update(updates)
    return dart_profile_attestation_allows_source_url(evidence, **fields)


@pytest.mark.parametrize("status", [301, 308])
def test_실제_영구이동은_root와_같은_origin_자손만_허용한다(status):
    redirect = dict(source_url=OLD, target_url=NEW, status=status, raw_location=NEW,
                    observed_at="2026-10-10T01:00:00+00:00")
    verified = proof(redirect=redirect)
    assert verified and allows(verified) and allows(verified, NEW + "products/solution")
    assert not allows(verified, OLD)
    assert not allows(verified, "https://jobs.current-company.example/")


def test_Location의_끝슬래시_생략과_fragment는_실제_관측_정본대로_재검증한다():
    redirect = dict(source_url=OLD, target_url=NEW + "#business", status=301,
                    raw_location=NEW.rstrip("/") + "#business", observed_at="2026-10-10T01:00:00+00:00")
    verified = proof(redirect=redirect)
    assert verified and allows(verified)


@pytest.mark.parametrize("url", ["http://current-company.example/", "https://current-company.example:8443/",
    "https://user@current-company.example/", NEW + "?tenant=other", "https://other.example/",
    "https://127.0.0.1/", "https://sites.google.com/acme"])
def test_공개_URL은_HTTPS_실효port_회사_host와_query_범위를_다시_검사한다(url):
    assert not allows(proof(), url)


@pytest.mark.parametrize("status", [302, 303, 307, True])
def test_일시이동은_공식_root로_승격하지_않는다(status):
    redirect = dict(source_url=OLD, target_url=NEW, status=status, raw_location=NEW,
                    observed_at="2026-10-10T01:00:00+00:00")
    assert not proof(redirect=redirect)


@pytest.mark.parametrize("updates", [
    {"registration_number_sha256": ""}, {"expected_registration_hashes": ()},
    {"registration_number_sha256": sha("다른 번호")}, {"matched_name_sha256": sha("별칭")},
    {"scope_sha256": sha("다른 범위")}, {"candidate_url": NEW + "tenant/"},
    {"candidate_url": NEW + "?tenant=one"}, {"candidate_url": "https://jobs.current-company.example/"},
    {"profile_evidence": PROFILE.replace(NAME, "다른 법인")},
    {"profile_evidence": PROFILE.replace(OLD, OLD + "redirect?next=external")},
    {"profile_evidence": PROFILE.replace(OLD, "https://platform.example/tenant/")},
])
def test_번호누락_범위와_회사_다른입력은_proof를_생성하지_않는다(updates):
    assert not proof(**updates)


@pytest.mark.parametrize("key", ["profile_sha256", "redirect_sha256", "raw_location_sha256",
    "scope_sha256", "matched_name_sha256", "candidate_url"])
def test_영수증_결속필드_단독변조는_공개에서_닫힌다(key):
    payload = json.loads(proof()[len(PERMANENT_RELOCATION_PREFIX):])
    payload[key] = "https://changed.example/" if key == "candidate_url" else sha("변조")
    modified = PERMANENT_RELOCATION_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert not allows(modified)


def test_원_profile은_공개_attester에_그대로_남고_Source_도장을_변조할수없다():
    verified = proof()
    metadata = formal_web_public_source_metadata(source_kind="official_web_page", source_url=NEW,
        company_name=NAME, identity_binding="수집 중 이중 신원 확인", domain_attestation_source_id="dart-company-profile-01234567",
        domain_attestation_evidence=PROFILE, domain_redirect_verification=verified,
        domain_redirect_from_host="prior-company.example", domain_redirect_to_host="current-company.example")
    assert metadata is not None
    attester = build_dart_profile_attester_source(number=2, source_id="dart-company-profile-01234567",
        evidence=PROFILE, company_name=NAME, collected_on="2026-10-10")
    source = seal_collected_source(Source(number=1, kind=SourceKind.OTHER, label="회사 공식 웹",
        source_id="verified-website", title="제품", publisher=NAME, host="current-company.example",
        url=NEW, document_id="document-one", location=NEW, collected_at="2026-10-10",
        source_type="회사 공식 웹", fact_status="기준일 현재 확인", evidence_hashes=[sha("제품 원문")],
        identity_binding="수집 중 이중 신원 확인", domain_attestation_source_id=attester.source_id,
        domain_attestation_evidence=PROFILE, domain_redirect_verification=verified,
        domain_redirect_from_host="prior-company.example", domain_redirect_to_host="current-company.example"))
    assert attester.domain_attestation_evidence == PROFILE
    assert has_valid_provenance_seal(source)
    assert not official_domain_attestation_problem(source, [source, attester])
    assert not has_valid_provenance_seal(replace(source, domain_redirect_verification=verified + " "))
    assert official_domain_attestation_problem(replace(source, url="https://other.example/"), [source, attester])

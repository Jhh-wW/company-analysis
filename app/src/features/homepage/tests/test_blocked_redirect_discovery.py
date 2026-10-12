"""차단된 HTTP Location은 후보 발견일 뿐이며 별도 안전·신원 검증을 요구한다."""
import socket
import urllib.parse

import pytest

from src.features.homepage import safe_http
from src.features.homepage.tests.test_redirect_scheme_upgrade import _FakeResponse as _BaseFakeResponse
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_domain import parse_official_origin
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fetch import WideTransportError, default_wide_transport
from src.features.homepage.wide_fragments import build_fragments_for_collection
from src.features.homepage.wide_types import REQUIREMENT_OPTIONAL, SOURCE_TIER_3_TRUSTED
from src.features.homepage.ir_pdf import OfficialIrFetchError

OLD = 'https://old-company.example/'
NEW = 'https://new-company.example/'
LEGAL_NAME = '가나다전자 주식회사'
NUMBER = '1234567890'
BODY = '<main><h1>회사 소개</h1><p>가나다전자는 개인 고객에게 업무용 전자제품과 소프트웨어를 직접 판매하고 기업 고객에게 제품 유지보수와 운영 지원 서비스를 제공하여 매출을 얻습니다.</p></main>'
FOOTER = '<footer>가나다전자 주식회사 사업자등록번호 123-45-67890</footer>'


class _FakeResponse(_BaseFakeResponse):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def no_ir(*args, **kwargs):
    raise OfficialIrFetchError('시험에서 IR 접속을 사용하지 않습니다')


def install_site(monkeypatch, *, target=NEW, body=BODY + FOOTER, robots='User-agent: *\nAllow: /', loop=False):
    calls = []

    def dns(host, port, **kwargs):
        ip = host if host in ('10.0.0.1', '169.254.169.254') else '8.8.8.8'
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, port))]

    def https_open(handler, request):
        url = request.full_url
        calls.append(url)
        if url == OLD:
            return _FakeResponse(url, code=301, headers={'Location': target})
        if url == NEW and loop:
            return _FakeResponse(url, code=302, headers={'Location': OLD})
        if url.endswith('/robots.txt'):
            text = robots if urllib.parse.urlsplit(url).hostname == urllib.parse.urlsplit(target).hostname else 'User-agent: *\nAllow: /'
            return _FakeResponse(url, code=200, headers={'Content-Type': 'text/plain'}, body=text.encode())
        if url == urllib.parse.urldefrag(target).url:
            return _FakeResponse(url, code=200, headers={'Content-Type': 'text/html; charset=utf-8'}, body=body.encode())
        return _FakeResponse(url, code=404, headers={})

    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    monkeypatch.setattr(safe_http._SafeHTTPSHandler, 'https_open', https_open)
    monkeypatch.setattr(safe_http._SafeHTTPHandler, 'http_open', lambda *args: pytest.fail('평문 요청 금지'))
    return calls


def collect():
    return collect_official_web_documents(company_id='01234567', company_name=LEGAL_NAME,
        company_aliases=('가나다전자',), company_registration_numbers=(NUMBER,),
        root_homepage_url=OLD, root_identity_verification_required=True,
        collected_at='2026-10-10T00:00:00+09:00',
        transport=default_wide_transport, ir_html_fetch=no_ir, ir_pdf_fetch=no_ir)


def test_native_redirect는_따라가지_않고_원_관측을_전달한다(monkeypatch):
    calls = install_site(monkeypatch)
    origin = parse_official_origin(OLD)
    with pytest.raises(WideTransportError) as caught:
        default_wide_transport(OLD, origin.allows_content_url)
    discovery = caught.value.redirect_discovery
    assert calls == [OLD]
    assert discovery.source_url == OLD
    assert discovery.target_url == NEW
    assert discovery.status == 301
    assert discovery.raw_location == NEW
    assert discovery.observed_at


def test_새_후보는_자기_robots와_이중신원_후에만_OPTIONAL로_채택한다(monkeypatch):
    calls = install_site(monkeypatch)
    result = collect()
    documents = [doc for doc in result.documents if doc.publisher == 'new-company.example']
    assert documents
    assert all(doc.requirement == REQUIREMENT_OPTIONAL and doc.source_tier == SOURCE_TIER_3_TRUSTED for doc in documents)
    assert calls.index(NEW + 'robots.txt') < calls.index(NEW)
    assert any(attempt.reason_code == 'redirect_scope_blocked' and attempt.state == 'FAILED' for attempt in result.attempts)
    assert len(result.redirect_discoveries) == 1
    envelope = to_evidence_mappings(result=result, fragments=build_fragments_for_collection(result))
    assert envelope['redirect_discoveries'][0]['target_url'] == NEW
    assert envelope['redirect_discoveries'][0]['sha256'] == result.redirect_discoveries[0].sha256
    assert all('차단된 HTTP Location' in doc.identity_binding for doc in documents)
    assert all(not doc.domain_attestation_source_id for doc in documents)


@pytest.mark.parametrize('target', ['https://10.0.0.1/', 'https://169.254.169.254/'])
def test_private와_metadata는_연결_전에_실패한다(monkeypatch, target):
    calls = install_site(monkeypatch, target=target)
    result = collect()
    assert not result.documents
    assert not any(urllib.parse.urlsplit(url).hostname == urllib.parse.urlsplit(target).hostname for url in calls)
    assert result.redirect_discoveries


@pytest.mark.parametrize('body', [BODY, BODY + '<footer>다른회사 주식회사 사업자등록번호 999-99-99999</footer>'])
def test_미확인회사와_다른회사는_문서로_채택하지_않는다(monkeypatch, body):
    install_site(monkeypatch, body=body)
    result = collect()
    assert not result.documents
    assert any(attempt.reason_code == 'cross_domain_identity_mismatch' for attempt in result.attempts)


def test_robots_거부는_본문을_요청하지_않는다(monkeypatch):
    calls = install_site(monkeypatch, robots='User-agent: *\nDisallow: /')
    assert not collect().documents
    assert NEW not in calls


def test_redirect_loop는_미신뢰_후보에서_재귀_추적하지_않는다(monkeypatch):
    calls = install_site(monkeypatch, loop=True)
    result = collect()
    assert not result.documents
    assert calls.count(OLD) == 1
    assert calls.count(NEW) == 1
    assert len(result.redirect_discoveries) == 1


def test_원_Location과_임의_다른_target은_자료형에서_거절한다():
    with pytest.raises(ValueError, match='결속'):
        safe_http.BlockedRedirectDiscovery(source_url=OLD, target_url=NEW,
            status=301, raw_location='https://different.example/', observed_at='2026-10-10T00:00:00Z')


@pytest.mark.parametrize('location', ['https://user:password@new-company.example/', NEW + '\r\nX-Test: forged'])
def test_userinfo와_제어문자_Location은_후보를_만들지_않는다(monkeypatch, location):
    calls = install_site(monkeypatch, target=location)
    result = collect()
    assert not result.documents
    assert not result.redirect_discoveries
    assert not any('new-company.example' in url for url in calls)


def test_fragment는_원_관측에_남고_본문_후보_URL에서는_제거된다(monkeypatch):
    # HTTP header 원바이트는 ISO-8859-1 표현이므로 ASCII fragment로 관측한다.
    location = NEW + '#business'
    calls = install_site(monkeypatch, target=location)
    result = collect()
    assert result.documents
    assert result.redirect_discoveries[0].raw_location == location
    assert result.redirect_discoveries[0].target_url == location
    assert NEW in calls
    assert not any('#' in url for url in calls)


def test_복수_Location은_첫값을_골라_공식후보로_쓰지_않는다(monkeypatch):
    calls = install_site(monkeypatch)

    def duplicate_redirect(handler, request):
        calls.append(request.full_url)
        response = _FakeResponse(request.full_url, code=301, headers={'Location': NEW})
        response.headers['Location'] = 'https://another.example/'
        return response

    monkeypatch.setattr(safe_http._SafeHTTPSHandler, 'https_open', duplicate_redirect)
    origin = parse_official_origin(OLD)
    with pytest.raises(WideTransportError) as caught:
        default_wide_transport(OLD, origin.allows_content_url)
    assert caught.value.redirect_discovery is None
    assert calls == [OLD]

"""XML 표제 법인 코드가 본문·목록 코드와 섞이지 않는 반례."""
import pytest

from core.dart_client import DartResponseError
from features.evidence_collection import constants as c
from features.evidence_collection.dart_fetcher import _document_header_corp_code
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.tests.test_dart_fetcher import _fetcher


def _xml(code='00126380', *, body='<P>사업 설명</P>'):
    return f'<DOCUMENT><DOCUMENT-NAME>사업보고서</DOCUMENT-NAME><COMPANY-NAME AREGCIK="{code}">가상 회사</COMPANY-NAME><BODY>{body}</BODY></DOCUMENT>'


@pytest.mark.parametrize('encoding', ['utf-8', 'cp949', 'euc-kr', 'utf-16'])
def test_실제_표제_직접선언은_손실없는_인코딩에서_읽는다(encoding):
    assert _document_header_corp_code(_xml().encode(encoding)) == '00126380'


def test_본문_불완전_XML은_표제_메타_검증을_바꾸지_않는다():
    assert _document_header_corp_code(_xml(body='깨진 & 본문 <P>').encode()) == '00126380'


@pytest.mark.parametrize('body', [
    '<COMPANY-NAME AREGCIK="00999999">다른 회사</COMPANY-NAME>',
    '<P>AREGCIK="00999999"</P>',
])
def test_본문_회사_선언과_문자열을_소유메타로_사용하지_않는다(body):
    assert _document_header_corp_code(_xml(body=body).encode()) == '00126380'


def test_표제_코드가_없으면_본문의_코드를_빌리지_않는다():
    raw = b'<DOCUMENT><BODY><COMPANY-NAME AREGCIK="00999999">Other</COMPANY-NAME></BODY></DOCUMENT>'
    assert _document_header_corp_code(raw) == ''


def test_표제_상한_뒤의_명시선언을_부재로_바꾸지_않는다():
    raw = ('<DOCUMENT>' + ' ' * c.DOCUMENT_IDENTITY_HEADER_MAX_BYTES
           + '<COMPANY-NAME AREGCIK="00999999">다른 회사</COMPANY-NAME><BODY/></DOCUMENT>').encode()
    with pytest.raises(DartResponseError):
        _document_header_corp_code(raw)


def test_큰_본문이어도_한도_안에서_표제부재가_확인되면_빈코드다():
    raw = ('<DOCUMENT><BODY>' + '본문 ' * c.DOCUMENT_IDENTITY_HEADER_MAX_BYTES + '</BODY></DOCUMENT>').encode()
    assert _document_header_corp_code(raw) == ''


@pytest.mark.parametrize('code', ['', '123', '123456789', '１２３４５６７８', '00126380 '])
def test_잘못된_명시코드는_빈값으로_후퇴하지_않는다(code):
    with pytest.raises(DartResponseError):
        _document_header_corp_code(_xml(code).encode())


def test_목록_회사가_맞아도_실제_XML_다른법인은_먼저_차단한다(tmp_path):
    receipt = '20250315000001'
    path = tmp_path / f'{receipt}.xml'
    path.write_bytes(_xml('00999999').encode())
    fetcher = _fetcher(tmp_path,
        get_json_fn=lambda *args: {'status': '000', 'list': [{
            'rcept_no': receipt, 'report_nm': '사업보고서 (2025.03)',
            'rcept_dt': '20250315', 'corp_code': '00126380'}]},
        download_document_fn=lambda *args: path)
    harvest = collect_dart_evidence(fetcher, '00126380', now='2026-09-30T00:00:00Z')
    assert not harvest.documents
    assert any(value.reason_code == c.REASON_DOCUMENT_IDENTITY_MISMATCH for value in harvest.attempts)


def test_깨진_명시_XML_선언은_목록_검증으로_대체되지_않는다(tmp_path):
    receipt = '20250315000001'
    path = tmp_path / f'{receipt}.xml'
    path.write_bytes(_xml('').encode())
    fetcher = _fetcher(tmp_path,
        get_json_fn=lambda *args: {'status': '000', 'list': [{
            'rcept_no': receipt, 'report_nm': '사업보고서 (2025.03)',
            'rcept_dt': '20250315', 'corp_code': '00126380'}]},
        download_document_fn=lambda *args: path)
    harvest = collect_dart_evidence(fetcher, '00126380', now='2026-09-30T00:00:00Z')
    assert not harvest.documents
    assert any(value.reason_code == c.REASON_DOCUMENT_FETCH_FAILED for value in harvest.attempts)

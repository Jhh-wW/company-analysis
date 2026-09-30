"""실제 DART 연동 — core.dart_client의 검증된 제한·상태 계약을 재사용한다.

★ 이 슬라이스는 지금까지 주입 Protocol과
가짜 fetcher(tests/fixtures/fake_fetcher.py)만 있었고 실제 DART에 한 번도
연결되지 않았다. 이 파일이 그 간극을 메운다: ``filing_select.DartFetcher``
Protocol을 만족하는 실제 어댑터를 ``core/dart_client.py``의 함수 위에 얹는다.
대표 XML·URL sidecar의 저장/검증 상한은 core 한 곳을 정본으로 재사용하고,
이 소비 경계도 반환된 cache를 다시 bounded 검증한다.

운영 파이프라인은 app의 공식 근거 adapter에서 이 클래스를 만들어
``collect_dart_evidence``에 주입한다. 실네트워크로 시험하지는 않았고,
``get_json``·``download_document``를 대신할 가짜 callable을 주입한 종단
시험만 쓴다(``tests/test_dart_fetcher.py``).

대표 XML의 DOCUMENT 직속 COMPANY-NAME AREGCIK 선언이 있으면 실제 문서
법인 코드로 읽는다. 선언 없는 자료는 빈 값으로 유지한다. 본문의 회사명이나
연결 자회사 태그를 법인 소유 증거로 삼지 않으며 깨진 명시 선언은 거절한다.

★ 알려진 한계 2 — 비표 본문과 불완전·과대 표는 종전처럼 태그를 개행으로
바꾼다. 닫힌 작은 TABLE만 검증된 행·셀 격자에 따라 한 문단으로 묶는다.
병합 셀의 원 텍스트는 소유 격자에 반복될 수 있으나 새 값은 계산하지 않는다.
태그로 나뉜 숫자가 모호한 표는 기존 변환에 남기며, 이 평문 구조만으로
최종 보고서의 출처·숫자·공개 적합성을 증명하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import codecs
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Final, Protocol

from core import dart_client
from features.evidence_collection import constants as c
from features.evidence_collection.filing_select import (
    DiscoveredDocumentUrl,
    DocumentFetchResult,
    FilingListResult,
    RawFilingRow,
)
from features.evidence_collection.table_text import table_replacements

#: list.json 조회 창 — 최근 N년. survey_audit_reports.py·run_pilot.py의
#: AUDIT_WINDOW_YEARS(3년, 「잠정 3년」)와 같은 값을 그대로 따른다 —
#: 사업/감사보고서는 보통 1년 안에 갱신되지만, 조회 누락을 피하려 넉넉히
#: 잡는다는 같은 근거다. 정확한 달력 계산(윤년) 대신 365일 근사를 쓴다 —
#: 이 창은 «조회 누락을 피하는 여유값」이지 정밀한 경계 조건이 아니므로
#: 하루이틀의 오차는 안전하다.
LOOKUP_WINDOW_YEARS: Final[int] = 3
_LOOKUP_WINDOW_DAYS: Final[int] = 365 * LOOKUP_WINDOW_YEARS
LIST_PAGE_COUNT: Final[str] = "100"

#: DART list.json 응답 상태 코드 — get_json은 020(한도)·010~012·901(인증)만
#: 예외로 던지고 나머지는 payload 그대로 돌려준다(core/dart_client.py 실측).
_STATUS_NO_DATA: Final[str] = "013"  # 정상 조회, 결과 없음
_STATUS_OK: Final[str] = "000"

_TAG_PATTERN = re.compile(r"<[^>]+>")
#: 줄 안의 공백만 뭉친다(개행은 남긴다) — survey_audit_reports.py의
#: read_filing_text는 \s+(개행 포함)를 전부 스페이스로 뭉개는데, 그건 그
#: 파일의 정규식 키워드 검색에는 문제없지만 이 feature의 segment.py는
#: 표제·문단을 줄바꿈으로 구분한다. 태그를 스페이스로 지우면 문서 전체가
#: 한 줄이 되어 segment.py의 표제·문단 인식이 통째로 무너진다 — 그래서
#: 태그는 스페이스가 아니라 개행으로 바꾼다(아래).
_INLINE_WHITESPACE_PATTERN = re.compile(r"[ \t\x0b\f\r]+")
#: 태그가 촘촘히 붙어 나오면 개행이 과도하게 쌓인다 — 문단 구분(빈 줄)은
#: 살리되 3개 이상 연속 개행은 2개로 눌러 둔다.
_EXCESS_NEWLINE_PATTERN = re.compile(r"\n{3,}")
#: get_json/download_document의 실제 함수 시그니처를 그대로 흉내낸 타입 —
#: 시험이 실제 네트워크 없이 이 자리에 가짜 callable을 주입한다.
GetJsonFn = Callable[[str, dict[str, Any], dart_client.UsageCounter], dict[str, Any]]


class DownloadDocumentFn(Protocol):
    """core 문서 다운로드의 strict sidecar keyword까지 보존하는 포트."""

    def __call__(
        self,
        rcept_no: str,
        dest_dir: Path,
        counter: dart_client.UsageCounter | None = None,
        *,
        require_official_url_sidecar: bool = False,
    ) -> Path: ...


def _decode_document_bytes(raw: bytes) -> str:
    """DART 원문 bytes를 손실 없는 첫 성공 인코딩으로 문자열화한다."""

    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _document_header_corp_code(raw: bytes) -> str:
    """실제 DOCUMENT 직속의 닫힌 법인 선언만 읽고 본문 파싱 전에 멈춘다."""
    prefix = raw[:c.DOCUMENT_IDENTITY_HEADER_MAX_BYTES]
    encodings = ('utf-16',) if prefix.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp949', 'euc-kr')
    decoded = None
    for encoding in encodings:
        try:
            decoded = codecs.getincrementaldecoder(encoding)(errors='strict').decode(prefix, final=False)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None or '\x00' in decoded:
        raise dart_client.DartResponseError('DART 법인 선언의 인코딩을 확인하지 못했습니다')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', decoded, flags=re.IGNORECASE):
        raise dart_client.DartResponseError('DART 법인 선언에 DTD 또는 엔터티 정의가 있습니다')
    if '<COMPANY-NAME' not in decoded and len(raw) <= c.DOCUMENT_IDENTITY_HEADER_MAX_BYTES:
        return ''
    parser = ET.XMLPullParser(events=('start', 'end'))
    stack: list[str] = []
    company_element = None
    company_count = 0
    root_seen = False
    stopped = False
    try:
        for char in decoded:
            parser.feed(char)
            for event, element in parser.read_events():
                if event == 'start':
                    if not root_seen:
                        root_seen = True
                        if element.tag != 'DOCUMENT':
                            if '<COMPANY-NAME' in decoded:
                                raise dart_client.DartResponseError('DART 법인 선언의 문서 루트가 다릅니다')
                            return ''
                    if element.tag == 'BODY' and stack == ['DOCUMENT']:
                        stopped = True
                        break
                    if element.tag == 'COMPANY-NAME':
                        if stack != ['DOCUMENT']:
                            raise dart_client.DartResponseError('DART 법인 선언이 문서 표제 밖에 있습니다')
                        company_count += 1
                        if company_count > 1:
                            raise dart_client.DartResponseError('DART 법인 선언이 중복됐습니다')
                    stack.append(element.tag)
                else:
                    if element.tag == 'COMPANY-NAME':
                        if list(element):
                            raise dart_client.DartResponseError('DART 법인 선언에 중첩된 내용이 있습니다')
                        company_element = element
                    stack.pop()
            if stopped:
                break
        if not stopped:
            # 닫힌 짧은 문서는 처리하되 크기 상한 뒤의 선언을 추측하지 않는다.
            if len(raw) > c.DOCUMENT_IDENTITY_HEADER_MAX_BYTES:
                raise dart_client.DartResponseError('DART 법인 표제가 읽기 상한 안에서 끝나지 않았습니다')
            parser.close()
    except ET.ParseError:
        raise dart_client.DartResponseError('DART 법인 표제의 XML 구조가 깨졌습니다') from None
    if company_count and company_element is None:
        raise dart_client.DartResponseError('DART 법인 선언이 닫히지 않았습니다')
    if company_element is None or 'AREGCIK' not in company_element.attrib:
        return ''
    code = company_element.attrib['AREGCIK']
    if re.fullmatch(r'[0-9]{8}', code) is None or not (company_element.text or '').strip():
        raise dart_client.DartResponseError('DART 법인 선언의 코드 또는 법인명이 유효하지 않습니다')
    return code


def _xml_to_plain_text(raw: bytes) -> str:
    """비표 줄 구조는 보존하고 검증된 작은 표만 한 문단으로 정규화한다.

    인코딩 시도 순서(utf-8·cp949·euc-kr)는 survey_audit_reports.py의
    read_filing_text와 같은 방식(실측 근거 재사용). 다만 그 함수는 태그를
    스페이스로 지워 문서를 한 줄로 뭉갠다 — 정규식 키워드 검색에는 문제
    없지만, 이 feature의 segment.py는 표제·문단을 줄바꿈으로 구분하므로
    비표 태그는 개행으로 바꾼다(위 _TAG_PATTERN 주석 참고).
    """
    text = _decode_document_bytes(raw)
    replacements = table_replacements(text, max_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)
    if replacements:
        # 표 밖의 문자열은 건드리지 않는다. 형식이 확인된 작은 표만 한 문단으로
        # 묶으며 실패한 표는 아래의 기존 태그-개행 변환을 그대로 탄다.
        pieces: list[str] = []
        cursor = 0
        for replacement in replacements:
            pieces.extend((text[cursor:replacement.start], "\n\n", replacement.text, "\n\n"))
            cursor = replacement.end
        pieces.append(text[cursor:])
        text = "".join(pieces)
    text = _TAG_PATTERN.sub("\n", text)
    text = _INLINE_WHITESPACE_PATTERN.sub(" ", text)
    text = _EXCESS_NEWLINE_PATTERN.sub("\n\n", text)
    return text.strip()


def _from_core_candidate(
    candidate: dart_client.DocumentUrlSidecarCandidate,
) -> DiscoveredDocumentUrl:
    return DiscoveredDocumentUrl(
        url=candidate.url,
        source_member_name=candidate.source_member_name,
        location=candidate.source_location,
        source_payload_sha256=candidate.source_payload_sha256,
    )


def _extract_explicit_web_url_candidates(
    raw: bytes,
    *,
    member_name: str,
) -> tuple[DiscoveredDocumentUrl, ...]:
    """대표 XML도 ZIP sidecar와 같은 URL 추출 정본을 사용한다."""

    return tuple(
        _from_core_candidate(candidate)
        for candidate in dart_client.extract_document_web_url_candidates(
            raw,
            member_name=member_name,
            max_candidates=c.MAX_OFFICIAL_URL_CANDIDATES,
        )
    )


def _load_document_url_sidecar(
    document_path: Path,
    *,
    rcept_no: str,
    main_document: bytes,
    require_valid: bool = False,
) -> tuple[DiscoveredDocumentUrl, ...]:
    """version·receipt·대표 XML hash에 맞는 닫힌 sidecar만 읽는다.

    sidecar는 보조 발견 정보다. 없거나 한 필드라도 깨졌으면 후보 전체를
    버린다. 호환 모드(``require_valid=False``)만 대표 XML fallback을 쓰고,
    FULL 정식 모드는 이를 공식자료 수집 불완전으로 보고 예외를 올린다.
    일부 행만 살리면 공격자가 malformed 행 사이에 URL을 끼워 넣어 검증
    경계를 모호하게 만들 수 있다.

    여기서 확인하는 것은 **형식·접수번호·옆 대표 XML과의 결속**이다. 작은
    ZIP member의 원문은 개인정보·용량을 늘리지 않기 위해 보관하지 않으므로
    ``source_payload_sha256``은 다운로드 당시 provenance이지 로컬에서 다시
    인증하는 서명값이 아니다. 따라서 이 후보 하나만으로 공식 자료가 되지
    않으며, app 수집기가 대상 HTML의 DART 법인명+등록번호와 same-origin을
    별도로 확인한 뒤에만 승격한다.
    """

    loaded = dart_client.load_document_url_sidecar(
        document_path,
        rcept_no=rcept_no,
        main_document=main_document,
    )
    if not loaded.is_valid:
        if require_valid:
            # 정식 FULL은 생산 함수가 sidecar를 만들었다는 호출 규약만 믿지
            # 않는다. 시험 대역·향후 transport·동시 cache 교체가 그 규약을
            # 어겨도 대표 XML fallback으로 조용히 자료 부족처럼 진행하지 않는다.
            raise dart_client.DartResponseError(
                "FULL DART 공시 cache의 공식 URL sidecar 결속을 확인할 수 없습니다"
            )
        return ()
    return tuple(_from_core_candidate(candidate) for candidate in loaded.candidates)


class DartRuntimeFetcher:
    """``filing_select.DartFetcher`` Protocol을 만족하는 실제 DART 어댑터.

    조회 실패·한도 소진·인증 실패는 여기서 흡수하지 않는다. 호출하는
    filing_select.py/collect.py의 ``_safe_fetch_*``는 DART 전송·응답·cache I/O
    같은 닫힌 외부 실패만 FAILED로 바꾸고, TypeError·ValueError 같은 배선·
    구현 오류는 상위 내부 오류로 재전파한다. 따라서 여기서는 실제 상태를
    정직하게 돌려주거나 예외를 그대로 올린다.
    """

    def __init__(
        self,
        *,
        document_cache_dir: Path,
        counter: dart_client.UsageCounter | None = None,
        lookup_window_days: int = _LOOKUP_WINDOW_DAYS,
        get_json_fn: GetJsonFn = dart_client.get_json,
        download_document_fn: DownloadDocumentFn = dart_client.download_document,
        require_official_url_sidecar: bool = False,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], dt.date] | None = None,
    ) -> None:
        self._document_cache_dir = document_cache_dir
        self._counter = counter or dart_client.UsageCounter()
        self._lookup_window_days = lookup_window_days
        self._get_json = get_json_fn
        self._download_document = download_document_fn
        self._require_official_url_sidecar = require_official_url_sidecar
        self._clock = clock
        self._today = today

    def _today_date(self) -> dt.date:
        return self._today() if self._today is not None else dt.date.today()

    def fetch_filing_list(self, company_id: str, pblntf_ty: str) -> FilingListResult:
        end = self._today_date()
        begin = end - dt.timedelta(days=self._lookup_window_days)
        started = self._clock()
        payload = self._get_json(
            "list.json",
            {
                "corp_code": company_id,
                "bgn_de": begin.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": pblntf_ty,
                "page_count": LIST_PAGE_COUNT,
            },
            self._counter,
        )
        elapsed_ms = max(0, int((self._clock() - started) * 1000))

        status = payload.get("status") if isinstance(payload, dict) else None
        if status == _STATUS_NO_DATA:
            return FilingListResult(state=c.ATTEMPT_STATE_OK, rows=(), elapsed_ms=elapsed_ms)
        if status != _STATUS_OK:
            # 020·010~012·901은 get_json이 이미 예외로 던진다. 여기 남는
            # 「알 수 없는 상태」는 fail-closed로 FAILED 처리한다.
            return FilingListResult(state=c.ATTEMPT_STATE_FAILED, elapsed_ms=elapsed_ms)

        raw_rows = payload.get("list")
        if not isinstance(raw_rows, list):
            return FilingListResult(state=c.ATTEMPT_STATE_FAILED, elapsed_ms=elapsed_ms)

        rows = tuple(
            RawFilingRow(
                rcept_no=str(row.get("rcept_no") or ""),
                report_nm=str(row.get("report_nm") or ""),
                rcept_dt=str(row.get("rcept_dt") or ""),
                # item 3 — corp_code·corp_name이
                # 실려 오면 방어적으로(.get) 읽어 filing_select.py의 행
                # 수준 혼입 방어에 쓴다. 필드가 없으면 요청 코드를 대입하지
                # 않고 빈 문자열로 남겨 확인 불가와 실제 일치를 구분한다.
                corp_code=str(row.get("corp_code") or ""),
                corp_name=str(row.get("corp_name") or ""),
            )
            for row in raw_rows
            if isinstance(row, dict) and row.get("rcept_no") and row.get("report_nm")
        )
        return FilingListResult(state=c.ATTEMPT_STATE_OK, rows=rows, elapsed_ms=elapsed_ms)

    def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
        started = self._clock()
        if self._require_official_url_sidecar:
            path = self._download_document(
                rcept_no,
                self._document_cache_dir,
                self._counter,
                require_official_url_sidecar=True,
            )
        else:
            path = self._download_document(
                rcept_no,
                self._document_cache_dir,
                self._counter,
            )
        # 주입된 transport나 깨진 warm cache가 core의 저장 경계를 우회해도
        # 여기서 파일 전체를 메모리에 올리지 않는다. 생산 기본 transport는
        # 이미 같은 상한으로 검증하지만, 실제 소비자도 계약을 독립 검증해야
        # cache 교체 시점의 손상·오배선을 자료 원문으로 읽지 않는다.
        with path.open("rb") as stream:
            raw = stream.read(dart_client.DOCUMENT_MEMBER_MAX_BYTES + 1)
        if len(raw) > dart_client.DOCUMENT_MEMBER_MAX_BYTES:
            raise dart_client.DartResponseError(
                "DART 공시 대표 cache가 허용 크기를 초과했습니다"
            )
        if not dart_client.is_document_xml_payload(raw):
            raise dart_client.DartResponseError(
                "DART 공시 대표 cache가 XML 문서 형식이 아닙니다"
            )
        corp_code = _document_header_corp_code(raw)
        text = _xml_to_plain_text(raw)
        sidecar_candidates = _load_document_url_sidecar(
            path,
            rcept_no=rcept_no,
            main_document=raw,
            require_valid=self._require_official_url_sidecar,
        )
        representative_candidates = _extract_explicit_web_url_candidates(
            raw,
            member_name=path.name,
        )
        official_url_candidates: list[DiscoveredDocumentUrl] = []
        seen_candidate_urls: set[str] = set()
        for candidate in (*sidecar_candidates, *representative_candidates):
            if candidate.url in seen_candidate_urls:
                continue
            seen_candidate_urls.add(candidate.url)
            official_url_candidates.append(candidate)
            if len(official_url_candidates) >= c.MAX_OFFICIAL_URL_CANDIDATES:
                break
        elapsed_ms = max(0, int((self._clock() - started) * 1000))
        return DocumentFetchResult(
            state=c.ATTEMPT_STATE_OK,
            text=text,
            elapsed_ms=elapsed_ms,
            bytes_downloaded=len(raw),
            corp_code=corp_code,
            official_url_candidates=tuple(official_url_candidates),
        )

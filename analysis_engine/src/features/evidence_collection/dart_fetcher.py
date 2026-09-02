"""실제 DART 연동 — core.dart_client의 검증된 제한·상태 계약을 재사용한다.

★ P0-4 — 이 슬라이스는 지금까지 주입 Protocol과
가짜 fetcher(tests/fixtures/fake_fetcher.py)만 있었고 실제 DART에 한 번도
연결되지 않았다. 이 파일이 그 간극을 메운다: ``filing_select.DartFetcher``
Protocol을 만족하는 실제 어댑터를 ``core/dart_client.py``의 함수 위에 얹는다
(그 파일 자체는 고치지 않고 import만 한다 — core/dart_client.py의
상한·상태 처리 패턴을 그대로 재사용한다).

★ 이 슬라이스의 범위 — 여기까지다. 파이프라인 배선(운영 경로에서 실제로
이 클래스를 만들어 ``collect_dart_evidence``에 주입하는 일)은 다른 담당
몫이다(LIVE_COLLECTION_UNVERIFIED — 실제 네트워크로 시험하지 않았다).
시험은 ``get_json``·``download_document``를 대신할 가짜 callable을
주입한 종단 시험만 쓴다(``tests/test_dart_fetcher.py``).

★ 알려진 한계 — DART document.xml 응답 자체에는 구조화된 corp_code가 없다
(실측: core/dart_client.download_document는 원문 zip을 그대로 풀어줄 뿐,
메타데이터 래퍼가 없다). 그래서 이 어댑터의 ``fetch_document_text``는
``corp_code``를 항상 빈 문자열로 돌려준다 — collect.py의 identity_binding이
「검증했다」고 거짓 주장하지 않고 정직하게 unverifiable로 남긴다(P1-4).

★ 알려진 한계 2 — ``_xml_to_plain_text``는 모든 태그를 개행으로 바꾼다(줄
구조를 보존해야 segment.py의 표제·문단 인식이 살기 때문). 실제 DART
document.xml의 태그 어휘를 표본 조사하지 않았으므로(확인 못 함), 문장
중간에 끼는 인라인 태그(굵게·글자색 등)가 있다면 그 문장이 줄 중간에서
쪼개질 수 있다 — segment.py의 표제 인식이 «줄 전체»를 봐야 맞으므로 이
경우 표제를 놓치거나 문단이 예상보다 잘게 쪼개질 수 있다(v1 한계).
"""

from __future__ import annotations

import datetime as dt
import re
import time
from pathlib import Path
from typing import Any, Callable, Final

from core import dart_client
from features.evidence_collection import constants as c
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow

#: list.json 조회 창 — 최근 N년. survey_audit_reports.py·run_pilot.py의
#: AUDIT_WINDOW_YEARS(3년, 「P-07 잠정 3년」)와 같은 값을 그대로 따른다 —
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
DownloadDocumentFn = Callable[[str, Path, dart_client.UsageCounter], Path]


def _xml_to_plain_text(raw: bytes) -> str:
    """공시서류 원문 XML을 태그 없는 평문으로 바꾸되 줄 구조는 보존한다.

    인코딩 시도 순서(utf-8·cp949·euc-kr)는 survey_audit_reports.py의
    read_filing_text와 같은 방식(실측 근거 재사용). 다만 그 함수는 태그를
    스페이스로 지워 문서를 한 줄로 뭉갠다 — 정규식 키워드 검색에는 문제
    없지만, 이 feature의 segment.py는 표제·문단을 줄바꿈으로 구분하므로
    태그는 개행으로 바꾼다(위 _TAG_PATTERN 주석 참고).
    """
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    text = _TAG_PATTERN.sub("\n", text)
    text = _INLINE_WHITESPACE_PATTERN.sub(" ", text)
    text = _EXCESS_NEWLINE_PATTERN.sub("\n\n", text)
    return text.strip()


class DartRuntimeFetcher:
    """``filing_select.DartFetcher`` Protocol을 만족하는 실제 DART 어댑터.

    조회 실패·한도 소진·인증 실패는 여기서 흡수하지 않는다 — 이 클래스를
    호출하는 filing_select.py/collect.py의 ``_safe_fetch_*``가 이미
    ``except Exception``으로 흡수하는 경계를 갖고 있으므로(요구사항 7),
    여기서는 실제 상태를 정직하게 돌려주거나(예외 없는 실패) 예외를 그대로
    올린다 — 이중으로 삼키면 원인 진단이 어려워진다.
    """

    def __init__(
        self,
        *,
        document_cache_dir: Path,
        counter: dart_client.UsageCounter | None = None,
        lookup_window_days: int = _LOOKUP_WINDOW_DAYS,
        get_json_fn: GetJsonFn = dart_client.get_json,
        download_document_fn: DownloadDocumentFn = dart_client.download_document,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], dt.date] | None = None,
    ) -> None:
        self._document_cache_dir = document_cache_dir
        self._counter = counter or dart_client.UsageCounter()
        self._lookup_window_days = lookup_window_days
        self._get_json = get_json_fn
        self._download_document = download_document_fn
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
                # 수준 혼입 방어에 쓴다. 실제 list.json 응답에 이 필드가
                # 오는지는 실측하지 못했다(확인 못 함 — live smoke 필요) —
                # 없으면 빈 문자열로 남아 지금처럼 대조 없이 통과한다.
                corp_code=str(row.get("corp_code") or ""),
                corp_name=str(row.get("corp_name") or ""),
            )
            for row in raw_rows
            if isinstance(row, dict) and row.get("rcept_no") and row.get("report_nm")
        )
        return FilingListResult(state=c.ATTEMPT_STATE_OK, rows=rows, elapsed_ms=elapsed_ms)

    def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
        started = self._clock()
        path = self._download_document(rcept_no, self._document_cache_dir, self._counter)
        raw = path.read_bytes()
        text = _xml_to_plain_text(raw)
        elapsed_ms = max(0, int((self._clock() - started) * 1000))
        return DocumentFetchResult(
            state=c.ATTEMPT_STATE_OK,
            text=text,
            elapsed_ms=elapsed_ms,
            bytes_downloaded=len(raw),
            # DART document.xml 응답에는 구조화된 corp_code가 없다(확인 못
            # 함 — 위 모듈 docstring 참고) — 「검증했다」고 거짓 주장하지
            # 않기 위해 항상 빈 문자열로 둔다.
            corp_code="",
        )

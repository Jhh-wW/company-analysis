"""수집 조각 → 출처 목록 재료 — 「무엇을 근거로 이 문장을 썼나」의 마지막 연결.

★ 왜 필요한가
  `sources.py`는 이미 완성된 출처(Source) 목록을 마크다운으로 쓰고(render) 다시
  읽는(parse) 함수를 갖고 있다. 그런데 그 Source를 **어디서 만드나**가 없었다 —
  수집 단계가 넘겨주는 조각은 `{"종류": ..., "원문": ...}`뿐이라 날짜·언론사
  같은 것이 안 보인다고 여겨졌기 때문이다.

  실제로는 재료가 이미 조각 «안에» 있다 (1판 엔진 `run_pilot.py` 실측):
    - 뉴스 조각 원문 맨 앞: `"(2025-03-12 보도 · mk.co.kr) 제목. 설명"`
      (`run_pilot.collect_news`가 붙인다)
    - 공시 계열 조각: 따로 넘어오는 `filing`(`latest_report_rcept()` 결과) 안에
      `report_nm`(보고서 이름) · `rcept_dt`(공시일, `YYYYMMDD`)가 있다
    - 홈페이지 조각: 수집기(`homepage.logic.collect_homepage_fragments`)가
      조각마다 `"출처"`(실제 읽은 URL)를 함께 준다

  이 파일은 그 재료를 **있는 그대로만** 뽑아 `Source`로 바꾼다. 날짜·이름을
  못 뽑으면 지어내지 않고 비워 둔다 — `Source.is_valid`가 걸러낸다.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from dataclasses import replace
from typing import Any, Optional

from src.features.provenance.constants import (
    DART_ACCOUNT_FRAGMENT_LABEL,
    DART_ACCOUNT_FRAGMENT_PREFIX,
    FRAGMENT_KIND_HOMEPAGE,
    FRAGMENT_KIND_NEWS,
    FRAGMENT_KIND_OFFICIAL_IR,
    HOMEPAGE_FALLBACK_LABEL,
    NEWS_UNKNOWN_DOMAIN,
)
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    official_web_currentness_is_usable,
    official_web_url_requires_document_date,
    seal_collected_source,
    source_type_is_official_ir,
    source_type_is_official_web,
)
from src.shared.official_ir import (
    IR_ATTACHMENT_URL_FIELD,
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    IR_METADATA_VERIFICATION_FIELD,
    IR_METADATA_VERIFICATION_VALUE,
    IR_REPORTING_PERIOD_FIELD,
)

#: 뉴스 조각 원문 맨 앞 — `"(2025-03-12 보도 · mk.co.kr) 제목. 설명"`.
#: 정본은 `run_pilot.collect_news()`의 f-string이다. 여기를 고치면 그쪽도 맞춰야 한다.
_NEWS_PREFIX_RE = re.compile(
    r"^\((?P<date>\d{4}-\d{2}-\d{2}) 보도 · (?P<domain>[^)]*)\)\s*(?P<rest>.*)$",
    re.DOTALL,
)

#: DART 공시일(`rcept_dt`)의 원래 모양 — 8자리 숫자(`YYYYMMDD`) 하나뿐이어야 한다.
_RCEPT_DT_RE = re.compile(r"^\d{8}$")
_OFFICIAL_PLAN_RE = re.compile(
    r"계획|예정|목표|향후|추진(?:할|하고자|하려|\s*중)|방침|로드맵|"
    r"확대할|강화할|진출할|구축할|도입할|개발할|출시할"
)
_PLAN_FRAGMENT_KINDS = frozenset({"신규사업전망"})


def build_citations(
    fragments: dict[int, dict[str, Any]],
    *,
    filing: Optional[dict[str, Any]],
    collected_on: dt.date,
    company_publisher: str = "",
    confirmed_corp_code: str = "",
    selected_evidence_by_fragment: Optional[dict[int, list[str]]] = None,
) -> list[Source]:
    """수집 조각 목록 + 수집 정보 → 출처 목록.

    Args:
        fragments: `{조각번호: {"종류", "원문", ("출처")}}`. 05 생성이 문장 뒤
            `[번호]`로 가리키는 바로 그 조각이다. **번호를 새로 매기지 않는다**
            (근거 표기 규칙 — 번호) — 여기서도 그대로 옮긴다.
        filing: `latest_report_rcept()`가 돌려준 전자공시 최신 보고서 1건
            (`report_nm`·`rcept_dt`·`rcept_no` 등). 못 가져왔으면 `None` —
            그때는 공시 계열 조각의 보고서 이름·공시일을 비워 둔다.
        collected_on: 오늘(수집일). 공시 계열 조각의 「수집 …」 줄에 쓴다 —
            이건 우리 쪽이 언제 수집했는지이므로 `filing` 유무와 무관하게 안다.
        confirmed_corp_code: 사용자가 확정한 8자리 DART 법인코드. 공시 행의
            법인코드와 정확히 같을 때만 ``company_publisher``를 그 공시의 정식
            발행 법인명으로 사용한다.

    Returns:
        `Source` 목록(조각 번호 오름차순). 재료가 모자란 항목은 지어내지 않고
        `Source.is_valid`가 False가 되도록 둔다 — 걸러내는 일은 부르는 쪽이 한다.
    """
    collected_at = collected_on.isoformat()
    sources: list[Source] = []
    requested = selected_evidence_by_fragment or {}
    for number, frag in sorted(fragments.items()):
        kind = frag.get("종류", "")
        text = frag.get("원문", "")
        if kind == FRAGMENT_KIND_NEWS:
            source = _news_source(number, frag)
        elif kind in {FRAGMENT_KIND_HOMEPAGE, FRAGMENT_KIND_OFFICIAL_IR}:
            source = _homepage_source(
                number,
                frag,
                collected_at,
                company_publisher,
                source_type=(
                    "회사 공식 IR"
                    if kind == FRAGMENT_KIND_OFFICIAL_IR
                    else "회사 공식 웹"
                ),
            )
        else:
            source = _filing_source(
                number,
                kind,
                text,
                filing,
                collected_at,
                company_publisher,
                confirmed_corp_code,
                declared_fact_status=str(
                    frag.get("사실상태") or frag.get("fact_status") or ""
                ),
                fragment_document_id=str(frag.get("문서ID") or ""),
            )
        sources.append(
            seal_collected_source(
                replace(
                    source,
                    evidence_hashes=_fragment_evidence_hashes(
                        frag,
                        requested.get(number, []),
                    ),
                    exact_evidence_hashes=_fragment_exact_evidence_hashes(
                        frag,
                        requested.get(number, []),
                    ),
                )
            )
        )
    return sources


def _fragment_evidence_hashes(
    fragment: dict[str, Any], selected_payloads: list[str]
) -> list[str]:
    """수집 payload에서 실제로 확인되는 원문만 해시한다.

    ``selected_payloads``는 작가가 만든 문장을 등록하는 통로가 아니다. 실제
    ``원문``의 연속 부분 문자열이거나 수집기가 별도로 보존한 ``근거원문``의
    정확한 항목일 때만 받아들인다. 따라서 공개 문장·표 행이 스스로 원문 해시를
    만들어 출고되는 경로가 없다.
    """

    payloads, _exact_payloads = _fragment_evidence_payloads(
        fragment, selected_payloads
    )
    return sorted(
        digest for payload in payloads if (digest := evidence_text_hash(payload))
    )


def _fragment_exact_evidence_hashes(
    fragment: dict[str, Any], selected_payloads: list[str]
) -> list[str]:
    """실제 원문과 byte-exact인 명시 근거만 대소문자 보존 해시로 등록한다."""

    _payloads, exact_payloads = _fragment_evidence_payloads(
        fragment, selected_payloads
    )
    return sorted(
        digest
        for payload in exact_payloads
        if (digest := exact_evidence_text_hash(payload))
    )


def _fragment_evidence_payloads(
    fragment: dict[str, Any], selected_payloads: list[str]
) -> tuple[set[str], set[str]]:
    """정규화 식별용 payload와 byte-exact로 입증된 payload를 함께 고른다."""

    raw_text = str(fragment.get("원문") or "").strip()
    structured_raw = fragment.get("근거원문") or []
    if isinstance(structured_raw, str):
        structured_raw = [structured_raw]
    structured = {
        str(payload).strip()
        for payload in structured_raw
        if str(payload).strip()
    } if isinstance(structured_raw, (list, tuple)) else set()

    payloads = {raw_text, *structured} - {""}
    # ``근거원문``은 수집기가 독립 항목으로 보존한 exact 문자열이다. 전체 원문은
    # legacy normalized hash만 유지하고, 명시적으로 선택된 실제 span일 때만 exact
    # 목록에 넣어 기존 citation의 JSON/HMAC이 불필요하게 바뀌지 않게 한다.
    exact_payloads = set(structured)
    normalized_raw = " ".join(raw_text.split())
    for selected in selected_payloads:
        clean = str(selected or "").strip()
        if not clean:
            continue
        normalized = " ".join(clean.split())
        if clean in structured or (normalized_raw and normalized in normalized_raw):
            payloads.add(clean)
        if clean in structured or (raw_text and clean in raw_text):
            exact_payloads.add(clean)
    return payloads, exact_payloads


def _news_source(number: int, frag: dict[str, Any]) -> Source:
    """뉴스 조각 — 원문 앞머리에서 보도일·도메인을 뽑는다. 못 뽑으면 비운다.

    라벨은 기사 제목을 쓴다. 원문이 `"{제목}. {설명}"` 꼴로 이어붙었으므로
    (`run_pilot.collect_news`) 첫 `". "`까지를 제목으로 본다 — 못 가르면
    잘라내다 지어내는 셈이 되므로 원문 전체를 라벨로 남긴다.
    """
    text = frag.get("원문", "")
    match = _NEWS_PREFIX_RE.match(text)
    if match is None:
        return Source(number=number, kind=SourceKind.NEWS, label=text.strip())

    published_at = match.group("date")
    domain = match.group("domain").strip()
    if domain == NEWS_UNKNOWN_DOMAIN:
        domain = ""  # 자리표시자다 — 실제 도메인처럼 옮기면 지어낸 값이 된다

    rest = match.group("rest").strip()
    title, _sep, _description = rest.partition(". ")
    label = title.strip() or rest

    return Source(
        number=number,
        kind=SourceKind.NEWS,
        label=label,
        published_at=published_at,
        domain=domain,
        source_id=f"source-{number}",
        title=label,
        publisher=domain,
        host=domain,
        url=frag.get("출처", "").strip(),
        document_id=frag.get("문서ID", "").strip(),
        location="기사 제목·본문 요약",
        source_type="외부 보도",
        fact_status="보도 확인",
    )


def _homepage_source(
    number: int,
    frag: dict[str, Any],
    collected_at: str,
    company_publisher: str = "",
    source_type: str = "회사 공식 웹",
) -> Source:
    """홈페이지 조각 — 실제 읽은 URL을 라벨로 삼는다. URL이 없으면 지어내지 않는다.

    ★ 수집일을 반드시 넣는다. 홈페이지는 «언제든 바뀌는» 자료라,
      언제 본 것인지 없으면 사용자가 확인하러 갔을 때 다른 내용을 보게 된다.
    """
    url = frag.get("출처", "").strip()
    parsed_url = urllib.parse.urlparse(url)
    host = (parsed_url.hostname or "").lower()
    path = parsed_url.path or "/"
    document_id = str(frag.get("문서ID") or "").strip()
    published_at = str(
        frag.get("문서일") or frag.get("published_at") or ""
    ).strip()
    reporting_period = str(frag.get(IR_REPORTING_PERIOD_FIELD) or "").strip()
    if source_type_is_official_ir(source_type) and (
        str(frag.get(IR_METADATA_VERIFICATION_FIELD) or "").strip()
        != IR_METADATA_VERIFICATION_VALUE
    ):
        published_at = ""
        reporting_period = ""
    if not document_id and url:
        # URL 자체가 변하는 웹 문서의 실제 식별자다. 임의 제목을 만들지 않는다.
        document_id = urllib.parse.urlunparse(
            ("", "", path, "", parsed_url.query, "")
        ) or "/"
    return Source(
        number=number,
        kind=SourceKind.OTHER,
        label=url or HOMEPAGE_FALLBACK_LABEL,
        collected_at=collected_at,
        published_at=published_at,
        reporting_period=reporting_period,
        ir_metadata_verification=str(
            frag.get(IR_METADATA_VERIFICATION_FIELD) or ""
        ).strip(),
        attachment_url=str(frag.get(IR_ATTACHMENT_URL_FIELD) or "").strip(),
        domain_redirect_verification=str(
            frag.get(IR_DART_WWW_REDIRECT_FIELD) or ""
        ).strip(),
        domain_redirect_from_host=str(
            frag.get(IR_DART_WWW_REDIRECT_FROM_FIELD) or ""
        ).strip(),
        domain_redirect_to_host=str(
            frag.get(IR_DART_WWW_REDIRECT_TO_FIELD) or ""
        ).strip(),
        source_id=f"source-{number}",
        title=frag.get("문서명", "").strip() or url or HOMEPAGE_FALLBACK_LABEL,
        publisher=frag.get("발행처", "").strip() or company_publisher.strip() or host,
        host=host,
        url=url,
        document_id=document_id,
        location=frag.get("원문위치", "").strip() or path,
        source_type=source_type,
        fact_status=(
            "문서일 미검증 수집 참고"
            if (
                source_type_is_official_ir(source_type)
                and not (published_at and reporting_period)
            ) or (
                source_type_is_official_web(source_type)
                and official_web_url_requires_document_date(url)
                and not published_at
            )
            else "과거·현재성 미확정 문서 수집 참고"
            if source_type_is_official_web(source_type)
            and not official_web_currentness_is_usable(
                source_type=source_type,
                url=url,
                published_at=published_at,
                collected_at=collected_at,
            )
            else "공식 발행일·보고기간 확정"
            if source_type_is_official_ir(source_type)
            else "기준일 현재 확인"
        ),
        domain_attestation_source_id=str(
            frag.get("도메인근거SourceID")
            or frag.get("domain_attestation_source_id")
            or ""
        ).strip(),
        domain_attestation_evidence=str(
            frag.get("도메인근거원문")
            or frag.get("domain_attestation_evidence")
            or ""
        ).strip(),
    )


def _filing_source(
    number: int,
    kind: str,
    text: str,
    filing: Optional[dict[str, Any]],
    collected_at: str,
    company_publisher: str = "",
    confirmed_corp_code: str = "",
    declared_fact_status: str = "",
    fragment_document_id: str = "",
) -> Source:
    """공시 계열 조각 — `filing`의 보고서 이름 + 공시일에 조각 종류를 붙인다.

    ★ 재무 API(`fnlttSinglAcnt.json`, 주요계정) 조각은 예외다. `filing`
      (사업·감사보고서 1건)과는 «다른» DART API 호출에서 온 값이라 그 보고서의
      공시일과 반드시 같다는 보장이 없다 — filing의 날짜를 갖다 붙이면 지어낸
      것과 같으므로 공시일은 비우고 출처만 밝힌다.
    """
    if text.startswith(DART_ACCOUNT_FRAGMENT_PREFIX):
        return Source(
            number=number,
            kind=SourceKind.FILING,
            label=DART_ACCOUNT_FRAGMENT_LABEL,
            collected_at=collected_at,
            source_id=f"source-{number}",
            title=DART_ACCOUNT_FRAGMENT_LABEL,
            publisher=company_publisher.strip() or "금융감독원",
            host="opendart.fss.or.kr",
            url="https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
            document_id=fragment_document_id.strip() or "fnlttSinglAcnt.json",
            location="주요계정 API 응답",
            source_type="공식 재무 API",
            fact_status="공시 실제값",
        )

    report_nm = str((filing or {}).get("report_nm") or "").strip()
    disclosed_at = _format_rcept_dt(str((filing or {}).get("rcept_dt") or "")) if filing else ""
    document_id = str(
        (filing or {}).get("rcept_no")
        or (filing or {}).get("rceptNo")
        or ""
    ).strip()
    url = (
        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={document_id}"
        if document_id
        else ""
    )
    label = " · ".join(part for part in (report_nm, kind) if part)

    # 한 공시 조각에는 완료 사실과 아직 미실행인 계획 문장이 함께 있을 수 있다.
    # 수집기가 명시한 상태를 우선 보존하고, 없을 때는 공식 계획 표지가 있는
    # 조각임을 기록한다. 개별 FactRecord의 정확한 상태는 이후 원문 문장과
    # claim_type/time_state 결속 검사가 최종 확정한다.
    declared = " ".join(declared_fact_status.split())
    source_fact_status = declared or (
        "공시 원문(실제·계획 문장 병존 가능)"
        if kind in _PLAN_FRAGMENT_KINDS or _OFFICIAL_PLAN_RE.search(text)
        else "공시 실제값"
    )

    filing_publisher = str((filing or {}).get("corp_name") or "").strip()
    filing_corp_code = str((filing or {}).get("corp_code") or "").strip()
    confirmed_code = confirmed_corp_code.strip()
    same_confirmed_company = bool(
        filing is not None
        and re.fullmatch(r"\d{8}", confirmed_code)
        and filing_corp_code == confirmed_code
    )
    # company.json의 정식명과 list.json의 약칭이 달라도 같은 corp_code이면
    # 같은 법인이다. 코드 결속 없이 이름만 덮어쓰면 다른 회사를 같은 회사로
    # 오인할 수 있으므로 정확히 확인된 경우에만 정식명을 우선한다.
    publisher = (
        company_publisher.strip()
        if same_confirmed_company
        else filing_publisher
        if filing_publisher
        else company_publisher.strip()
    )
    return Source(
        number=number,
        kind=SourceKind.FILING,
        label=label,
        disclosed_at=disclosed_at,
        collected_at=collected_at,
        source_id=f"source-{number}",
        title=report_nm or label,
        # 공시를 제출하고 내용에 책임지는 주체는 회사다. DART는 host로 분리한다.
        publisher=publisher,
        host="dart.fss.or.kr",
        url=url,
        document_id=document_id,
        location=kind or "공시 본문",
        source_type="공식 공시",
        fact_status=source_fact_status,
    )


def _format_rcept_dt(raw: str) -> str:
    """DART 공시일(`YYYYMMDD`) → `"YYYY-MM-DD"`. 모양이 안 맞으면 비운다(지어내지 않는다)."""
    if _RCEPT_DT_RE.match(raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""

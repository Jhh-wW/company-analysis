"""기관의 기업 제공 링크와 실제 홈페이지 이중 신원을 결속하는 내부 영수증.

기관의 도메인 소유권 인증이 아니다. 허용된 기관의 실제 검색·상세 HTML에서
동일 법인명과 홈페이지 필드가 발견됐다는 계보만 만든다. 대상 사이트의
DART 법인명·정확한 등록번호 검증은 반드시 별도로 통과해야 한다.
payload_sha256는 내부 전송 무결성 지문이며 전자서명이 아니다. 외부 저장과
공개 Source의 위변조 방지는 기존 provenance HMAC seal이 담당한다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass

from lxml import etree, html

from src.shared.company_identity import exact_company_name_key, normalize_korean_registration_number
from src.shared.report_evidence.identity_verified_web import canonical_identity_verified_web_url
from src.shared.report_evidence.institution_directory_constants import (
    DIRECTORY_BINDING_PREFIX, DIRECTORY_COMPANY_LABEL, DIRECTORY_HOMEPAGE_LABEL,
    DIRECTORY_INFORMATION_BASIS, DIRECTORY_MAX_AGE_DAYS, DIRECTORY_MAX_PAGE_BYTES,
    DIRECTORY_MAX_PROOF_CHARS, DIRECTORY_MAX_ROWS, DIRECTORY_ORIGIN,
    DIRECTORY_POLICY_VERSION, DIRECTORY_PROVIDER_ID, DIRECTORY_PROVENANCE_PREFIX,
    DIRECTORY_RECORD_CAPTION, DIRECTORY_RECORD_PATH, DIRECTORY_SEARCH_CAPTION,
    DIRECTORY_SEARCH_PATH,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CORP_CODE = re.compile(r"^[0-9]{8}$")
_RECORD_ID = re.compile(r"^[1-9][0-9]{0,9}$")


def _sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def directory_search_url(company_name: str) -> str:
    query = exact_company_name_key(company_name).replace("\x1f", " ")
    if not query:
        raise ValueError("기관 검색에 쓸 정확한 법인명이 없습니다")
    return DIRECTORY_ORIGIN + DIRECTORY_SEARCH_PATH + "?" + urllib.parse.urlencode({"lang": "ko", "search": query})


def directory_record_id(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parsed.query, strict_parsing=True)
    except (TypeError, ValueError):
        return ""
    params = dict(pairs)
    if (f"{parsed.scheme}://{parsed.netloc}" != DIRECTORY_ORIGIN
            or parsed.path != DIRECTORY_RECORD_PATH or parsed.fragment
            or len(pairs) != 2 or set(params) != {"lang", "id"}
            or params["lang"] != "ko" or not _RECORD_ID.fullmatch(params["id"])):
        return ""
    return params["id"]


def _text(element: object) -> str:
    return " ".join(" ".join(element.itertext()).split())  # type: ignore[attr-defined]


def _tree(raw: bytes):
    if type(raw) is not bytes or not raw or len(raw) > DIRECTORY_MAX_PAGE_BYTES:
        raise ValueError("기관 원문의 크기 또는 형식이 올바르지 않습니다")
    # 이 공급자는 UTF-8 HTML만 사용한다. 대체문자로 훼손된 신원을 맞추지 않는다.
    try:
        root = html.fromstring(raw.decode("utf-8"))
    except etree.ParserError as error:
        raise ValueError("기관 HTML 문서 구조를 확인하지 못했습니다") from error
    for node in root.xpath("//script|//style|//template|//noscript"):
        node.drop_tree()
    return root


def _table(root, caption: str):
    tables = [table for table in root.xpath("//table")
              if len(table.xpath("./caption")) == 1 and _text(table.xpath("./caption")[0]) == caption]
    if len(tables) != 1:
        raise ValueError("기관 기업정보 표가 없거나 모호합니다")
    return tables[0]


def directory_search_record(raw: bytes, *, company_name: str) -> tuple[str, str, str]:
    """전체 검색 결과에서 정확한 회사명의 유일한 상세 링크만 선택한다."""
    root = _tree(raw)
    table = _table(root, DIRECTORY_SEARCH_CAPTION)
    headers = [_text(cell) for cell in table.xpath("./thead/tr/th")]
    if headers.count(DIRECTORY_COMPANY_LABEL) != 1:
        raise ValueError("기관 검색의 기업명 열이 모호합니다")
    name_index = headers.index(DIRECTORY_COMPANY_LABEL)
    rows = table.xpath("./tbody/tr")
    totals = root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' filter_result_total ')]/em")
    if len(totals) != 1 or not _text(totals[0]).isdigit():
        raise ValueError("기관 검색 결과의 전체 개수를 확인하지 못했습니다")
    total = int(_text(totals[0]))
    if total == 0:
        return "", "", ""
    if total != len(rows) or total > DIRECTORY_MAX_ROWS:
        raise ValueError("기관 검색 결과가 상한을 넘거나 일부 페이지만 수집됐습니다")
    matches = []
    for index, row in enumerate(rows):
        cells = row.xpath("./td")
        if len(cells) != len(headers):
            raise ValueError("기관 검색 행의 열 구조가 다릅니다")
        if exact_company_name_key(_text(cells[name_index])) != exact_company_name_key(company_name):
            continue
        links = []
        for href in row.xpath(".//a/@href"):
            url = urllib.parse.urljoin(DIRECTORY_ORIGIN, href)
            if urllib.parse.urlsplit(url).path == DIRECTORY_RECORD_PATH:
                if not directory_record_id(url):
                    raise ValueError("기관 상세 링크가 허용 범위를 벗어났습니다")
                links.append(url)
        if len(links) > 1:
            raise ValueError("기관의 동일 회사 행에 상세 링크가 여러 개입니다")
        if links:
            matches.append((links[0], f"table:{DIRECTORY_SEARCH_CAPTION}/tbody/tr:{index + 1}", _sha(html.tostring(row))))
    if len(matches) > 1:
        raise ValueError("동일 법인명의 기관 상세 레코드가 모호합니다")
    return matches[0] if matches else ("", "", "")


def _record_fields(raw: bytes) -> tuple[str, str, str]:
    table = _table(_tree(raw), DIRECTORY_RECORD_CAPTION)
    values = {}
    for row in table.xpath("./tbody/tr"):
        headers, cells = row.xpath("./th"), row.xpath("./td")
        if len(headers) != 1 or len(cells) != 1:
            raise ValueError("기관 상세 필드의 표 구조가 다릅니다")
        label = _text(headers[0])
        if label in {DIRECTORY_COMPANY_LABEL, DIRECTORY_HOMEPAGE_LABEL}:
            if label in values:
                raise ValueError("기관 상세에 같은 신원 필드가 여러 개입니다")
            values[label] = cells[0]
    if set(values) != {DIRECTORY_COMPANY_LABEL, DIRECTORY_HOMEPAGE_LABEL}:
        raise ValueError("기관 상세에 회사명 또는 홈페이지 필드가 없습니다")
    homepage = values[DIRECTORY_HOMEPAGE_LABEL]
    hrefs = homepage.xpath(".//a/@href")
    if len(hrefs) != 1 or _text(homepage) != hrefs[0]:
        raise ValueError("기관 홈페이지 필드의 표시 주소와 링크가 다릅니다")
    return _text(values[DIRECTORY_COMPANY_LABEL]), hrefs[0], _sha(html.tostring(table))


def _homepage_url(raw_href: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(raw_href)
    except ValueError:
        return ""
    normalized = urllib.parse.urlunsplit(parsed._replace(path=parsed.path or "/"))
    return canonical_identity_verified_web_url(normalized)


@dataclass(frozen=True)
class InstitutionDirectoryUrlProvenance:
    provider_id: str
    policy_version: str
    information_basis: str
    company_id: str
    company_name: str
    search_url: str
    search_payload_sha256: str
    search_row_location: str
    search_row_sha256: str
    record_url: str
    record_id: str
    record_payload_sha256: str
    record_fields_sha256: str
    declared_company_name: str
    raw_homepage_href: str
    url: str
    fetched_on: str


def _encode(prefix: str, payload: dict) -> str:
    return prefix + _json({"payload": payload, "payload_sha256": _sha(_json(payload))})


def _decode(value: object, prefix: str) -> dict | None:
    if type(value) is not str or len(value) > DIRECTORY_MAX_PROOF_CHARS or not value.startswith(prefix):
        return None
    try:
        raw = json.loads(value[len(prefix):])
        if (type(raw) is not dict or set(raw) != {"payload", "payload_sha256"}
                or type(raw["payload"]) is not dict or _encode(prefix, raw["payload"]) != value):
            return None
        return raw["payload"]
    except (ValueError, TypeError):
        return None


def parse_institution_directory_provenance(value: object, *, reference_date: str = "") -> InstitutionDirectoryUrlProvenance | None:
    raw = _decode(value, DIRECTORY_PROVENANCE_PREFIX)
    if raw is None or set(raw) != set(InstitutionDirectoryUrlProvenance.__annotations__) or any(type(item) is not str for item in raw.values()):
        return None
    proof = InstitutionDirectoryUrlProvenance(**raw)
    try:
        fetched = dt.date.fromisoformat(proof.fetched_on)
        if reference_date and not 0 <= (dt.date.fromisoformat(reference_date[:10]) - fetched).days <= DIRECTORY_MAX_AGE_DAYS:
            return None
        if (proof.provider_id != DIRECTORY_PROVIDER_ID or proof.policy_version != DIRECTORY_POLICY_VERSION
                or proof.information_basis != DIRECTORY_INFORMATION_BASIS or not _CORP_CODE.fullmatch(proof.company_id)
                or not exact_company_name_key(proof.company_name)
                or exact_company_name_key(proof.company_name) != exact_company_name_key(proof.declared_company_name)
                or proof.search_url != directory_search_url(proof.company_name)
                or not directory_record_id(proof.record_url) or directory_record_id(proof.record_url) != proof.record_id
                or not proof.url or _homepage_url(proof.raw_homepage_href) != proof.url
                or not re.fullmatch(r"table:지정기업 목록/tbody/tr:[1-9][0-9]*", proof.search_row_location)
                or any(not _SHA256.fullmatch(item) for item in (proof.search_payload_sha256, proof.search_row_sha256,
                       proof.record_payload_sha256, proof.record_fields_sha256))):
            return None
    except (ValueError, TypeError):
        return None
    return proof


def build_institution_directory_provenance(*, company_id: str, company_name: str, search_url: str,
                                         search_body: bytes, record_url: str, record_body: bytes, fetched_on: str) -> str:
    """실제 원문을 다시 파싱하여 해시·위치·회사·URL이 결속된 발견 영수증을 만든다."""
    found_url, location, row_hash = directory_search_record(search_body, company_name=company_name)
    name, href, fields_hash = _record_fields(record_body)
    if found_url != record_url:
        raise ValueError("기관 상세 URL이 실제 회사 검색 행의 링크와 다릅니다")
    proof = InstitutionDirectoryUrlProvenance(
        DIRECTORY_PROVIDER_ID, DIRECTORY_POLICY_VERSION, DIRECTORY_INFORMATION_BASIS,
        company_id, company_name, search_url, _sha(search_body), location, row_hash,
        record_url, directory_record_id(record_url), _sha(record_body), fields_hash,
        name, href, _homepage_url(href), fetched_on,
    )
    encoded = _encode(DIRECTORY_PROVENANCE_PREFIX, asdict(proof))
    if parse_institution_directory_provenance(encoded, reference_date=fetched_on) is None:
        raise ValueError("기관 원문의 회사·홈페이지 게시 계보가 일치하지 않습니다")
    return encoded


@dataclass(frozen=True)
class VerifiedInstitutionDirectoryOfficialWeb:
    provenance: str
    candidate_url: str
    effective_urls: tuple[str, ...]
    scope_sha256: str
    identity_evidence_sha256: str
    matched_name_sha256: str
    registration_number_sha256: str
    verified_on: str


def _same_origin(left: str, right: str) -> bool:
    a, b = urllib.parse.urlsplit(left), urllib.parse.urlsplit(right)
    return a.scheme == b.scheme == "https" and a.netloc == b.netloc


def parse_verified_institution_directory_binding(value: object) -> VerifiedInstitutionDirectoryOfficialWeb | None:
    raw = _decode(value, DIRECTORY_BINDING_PREFIX)
    if raw is None or set(raw) != set(VerifiedInstitutionDirectoryOfficialWeb.__annotations__):
        return None
    if type(raw.get("effective_urls")) is not list or any(type(item) is not str for key, item in raw.items() if key != "effective_urls"):
        return None
    proof = VerifiedInstitutionDirectoryOfficialWeb(**{**raw, "effective_urls": tuple(raw["effective_urls"])})
    provenance = parse_institution_directory_provenance(proof.provenance, reference_date=proof.verified_on)
    if (provenance is None or proof.verified_on != proof.verified_on[:10] or not proof.verified_on
            or proof.candidate_url != provenance.url
            or proof.matched_name_sha256 != _sha(exact_company_name_key(provenance.company_name))
            or not proof.effective_urls
            or any(not _SHA256.fullmatch(item) for item in (proof.scope_sha256, proof.identity_evidence_sha256,
                    proof.matched_name_sha256, proof.registration_number_sha256))):
        return None
    if any(not canonical_identity_verified_web_url(url) or not _same_origin(proof.candidate_url, url) for url in proof.effective_urls):
        return None
    return proof


def build_verified_institution_directory_binding(*, provenance_value: str, company_id: str, company_name: str,
        company_registration_numbers: tuple[str, ...], candidate_url: str, effective_urls: tuple[str, ...],
        scope_sha256: str, scope_allows: Callable[[str], bool], identity_evidence_sha256: str,
        matched_name_sha256: str, registration_number_sha256: str, collected_at: str) -> str:
    provenance = parse_institution_directory_provenance(provenance_value, reference_date=collected_at)
    hashes = {_sha(number) for raw in company_registration_numbers if (number := normalize_korean_registration_number(raw))}
    if (provenance is None or provenance.company_id != company_id
            or exact_company_name_key(provenance.company_name) != exact_company_name_key(company_name)
            or registration_number_sha256 not in hashes
            or any(not scope_allows(url) for url in effective_urls)):
        return ""
    proof = VerifiedInstitutionDirectoryOfficialWeb(provenance_value, candidate_url, effective_urls, scope_sha256,
        identity_evidence_sha256, matched_name_sha256, registration_number_sha256, collected_at[:10])
    encoded = _encode(DIRECTORY_BINDING_PREFIX, asdict(proof))
    return encoded if parse_verified_institution_directory_binding(encoded) is not None else ""


def institution_directory_binding_allows_url(value: object, *, source_url: object, company_id: str = "", company_name: str = "") -> bool:
    proof = parse_verified_institution_directory_binding(value)
    url = canonical_identity_verified_web_url(source_url)
    if proof is None or not url or not _same_origin(proof.candidate_url, url):
        return False
    provenance = parse_institution_directory_provenance(proof.provenance)
    return bool(provenance and (not company_id or company_id == provenance.company_id)
                and (not company_name or _sha(exact_company_name_key(company_name)) == proof.matched_name_sha256))

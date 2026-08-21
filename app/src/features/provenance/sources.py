"""보고서에 실리는 출처 목록 — 문장 뒤 [번호], 맨 아래에 목록.

★ 왜 필요한가
  지금은 화면이 실행 기록(runs.jsonl)에서 출처를 «역산»한다. 기록 형식이
  바뀌면 조용히 틀어진다. 이 파일은 보고서 자체에 실을 수 있는 출처
  자료구조와, 그것을 마크다운으로 쓰고(직렬화) 다시 읽는(파싱) 함수를 담는다.

  **쓰기(render_sources)와 읽기(parse_sources)는 사람이 보는 표시 필드를 왕복한다.**
  canonical의 source_id·URL·발행처·원문 위치는 Markdown이 아니라 Report JSON
  등록부가 보존한다 — 시험(`tests/test_sources.py`, `storage/tests`)으로 각각 증명한다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum

from src.features.provenance.constants import OTHER_DATE_PREFIX, SOURCES_HEADER


def evidence_text_hash(text: str) -> str:
    """원문 조각의 공백·대소문자 차이를 제거한 SHA-256 식별자."""

    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


_KNOWN_FILING_HOSTS = frozenset(
    {"dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr"}
)
_PROVENANCE_SEAL_ENV = "PROVENANCE_SEAL_SECRET"
_configured_seal_key = os.environ.get(_PROVENANCE_SEAL_ENV, "").encode("utf-8")
# 배포에서는 재시작 뒤 저장 보고서도 검증되도록 환경 비밀을 고정한다. 비어 있거나
# 너무 짧으면 프로세스 한정 키를 써서 최소한 직렬화 payload의 사후 조작은 막고,
# 재시작 뒤 기존 보고서는 안전하게 fail-closed 된다.
_PROVENANCE_SEAL_KEY = (
    _configured_seal_key
    if len(_configured_seal_key) >= 32
    else secrets.token_bytes(32)
)


def seal_key_is_persistent() -> bool:
    """재시작 뒤에도 같은 출처 도장을 검증할 운영 키가 설정됐는가."""

    return len(_configured_seal_key) >= 32


def _host_key(value: str) -> str:
    return str(value or "").strip().casefold().rstrip(".")


def _publisher_key(value: str) -> str:
    """표시용 공백·대소문자만 무시한 발행 법인 비교 키."""

    return re.sub(r"\s+", "", str(value or "")).casefold()


_URL_IN_EVIDENCE = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>\"'()\[\]{}]+"
)


def _hosts_in_domain_attestation_evidence(evidence: str) -> set[str]:
    """공시 원문 조각에 실제 URL로 적힌 host만 꺼낸다.

    단순히 회사명이나 ``공식 홈페이지``라는 문구가 있다는 이유로 도메인을
    신뢰하지 않는다. URL 형태가 아닌 임의 문자열도 도메인 소유 근거로 쓰지
    않는다.
    """

    hosts: set[str] = set()
    for token in _URL_IN_EVIDENCE.findall(str(evidence or "")):
        candidate = token.rstrip(".,;:!?。，、")
        if candidate.casefold().startswith("www."):
            candidate = f"https://{candidate}"
        try:
            parsed = urllib.parse.urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            hosts.add(_host_key(parsed.hostname))
    return hosts


def _url_identity_is_bound(source: "Source") -> bool:
    """선언 host/domain/document_id가 실제 URL의 신원과 맞는지 검사한다."""

    try:
        parsed = urllib.parse.urlparse(source.url.strip())
    except ValueError:
        return False
    hostname = _host_key(parsed.hostname or "")
    declared_host = _host_key(source.host)
    if parsed.scheme not in {"https", "http"} or not hostname or not declared_host:
        return False
    if declared_host != hostname:
        return False
    if source.kind is SourceKind.NEWS:
        domain = _host_key(source.domain)
        if not domain or domain != hostname:
            return False
    if source.kind is SourceKind.FILING:
        if hostname not in _KNOWN_FILING_HOSTS:
            return False
        document_id = source.document_id.strip().casefold()
        decoded_url = urllib.parse.unquote(source.url).casefold()
        if not document_id or document_id not in decoded_url:
            return False
    return True


def _valid_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()):
        return False
    try:
        date.fromisoformat(str(value).strip())
    except ValueError:
        return False
    return True


class SourceKind(str, Enum):
    """출처의 종류. 렌더링 형식이 종류마다 다르다."""

    #: 감사보고서·사업보고서 등 전자공시 원본 자료
    FILING = "공시"
    #: 언론 보도
    NEWS = "뉴스"
    #: 그 밖의 자료 (날짜 메타데이터 없이 이름만 있는 경우)
    OTHER = "기타"


# ``source_type``은 사용자에게도 보이는 한국어 분류라 저장 호환성을 위해 문자열로
# 유지한다. 다만 출고 게이트는 ``공식``이라는 부분 문자열을 믿지 않고 아래의 닫힌
# 목록만 인정한다. 예를 들어 뉴스가 스스로 ``공식 분석 기사``라고 적어도 핵심
# 사실의 원문으로 승격되지 않는다.
_OFFICIAL_SOURCE_TYPES_BY_KIND: dict[SourceKind, frozenset[str]] = {
    SourceKind.FILING: frozenset(
        {
            "공식 공시",
            "공식 재무 api",
            "공식 공시·재무 api",
            "공식 계획",
            "규제기관 공식 자료",
            "공식 규제기관 자료",
            "비교사 공식 공시",
            "비교사 공식 재무 api",
        }
    ),
    SourceKind.OTHER: frozenset(
        {
            "회사 공식 ir",
            "공식 ir",
            "회사 공식 웹",
            "공식 웹",
            "회사 공식 자료",
            "공식 파트너 자료",
            "파트너 공식 자료",
            "규제기관 공식 자료",
            "공식 규제기관 자료",
        }
    ),
    SourceKind.NEWS: frozenset(),
}


@dataclass(frozen=True)
class Source:
    """출처 목록 한 줄 — 문장 뒤 `[번호]`가 가리키는 실제 출처.

    ★ `number`는 **AI가 고른 조각 번호를 그대로 쓴다.** 여기서 새로 매기지
      않는다 (정본 §근거 표기 — 번호).
    """

    number: int
    kind: SourceKind
    label: str
    #: 공시일 (예: "2024-03-15"). 공시 자료일 때만 쓴다.
    disclosed_at: str = ""
    #: 우리가 수집한 날짜 (예: "2026-08-13"). 공시 자료일 때만 쓴다.
    collected_at: str = ""
    #: 보도일. 뉴스일 때 반드시 있어야 한다.
    published_at: str = ""
    #: 언론사 도메인 (예: "mk.co.kr"). 뉴스일 때 반드시 있어야 한다.
    domain: str = ""
    #: canonical(v3) 주장 장부가 참조하는 안정적인 ID. 조각 번호와 분리한다.
    source_id: str = ""
    #: 원문 표지·페이지에 적힌 실제 문서 제목. 없으면 레거시 ``label``을 쓴다.
    title: str = ""
    #: 실제 발행 주체. DART·KIND 같은 호스팅 기관과 구분한다.
    publisher: str = ""
    #: 원문을 호스팅하는 기관·사이트.
    host: str = ""
    #: 사용자가 직접 열 수 있는 원문 URL.
    url: str = ""
    #: 공시 접수번호·보고서 ID 등 원문 식별자.
    document_id: str = ""
    #: PDF 페이지·표 또는 웹 섹션처럼 주장을 찾을 수 있는 위치.
    location: str = ""
    #: 공식 공시·공식 IR·공식 웹·외부 분석 등 자료 분류.
    source_type: str = ""
    #: 실제·잠정·계획·현재·외부 추정 등 이 자료에서 쓴 사실 상태.
    fact_status: str = ""
    #: 이 원문을 실제 사용한 semantic section ID.
    used_in: list[str] = field(default_factory=list)
    #: 수집 단계에서 보존한 원문 조각·표 행의 정규화 SHA-256 목록.
    #: canonical FactRecord.state_evidence는 반드시 이 목록 중 하나와 일치해야 한다.
    evidence_hashes: list[str] = field(default_factory=list)
    #: 회사 공식 웹·IR처럼 ``OTHER``인 원문의 도메인을 확인해 준 독립 공시
    #: Source ID. 자기 자신이나 또 다른 자기선언 웹 자료는 쓸 수 없다.
    domain_attestation_source_id: str = ""
    #: 위 공시 원문에서 회사 홈페이지 URL이 실제로 적힌 보존 조각.
    #: 이 문자열의 해시가 attestation Source.evidence_hashes에 있어야 한다.
    domain_attestation_evidence: str = ""
    #: 수집 경계가 Source 신원·원문 해시를 함께 잠근 서버 HMAC. 공개 보고서가
    #: evidence_hashes를 스스로 고쳐 쓰는 것을 막으며 렌더러에는 표시하지 않는다.
    provenance_seal: str = ""

    @property
    def is_valid(self) -> bool:
        """뉴스는 보도일과 언론사 도메인이 반드시 있어야 한다 (정본 §근거 표기 — 뉴스)."""
        if self.number <= 0 or not self.label.strip():
            return False
        if self.kind is SourceKind.NEWS:
            return bool(self.published_at.strip()) and bool(self.domain.strip())
        return True

    @property
    def is_canonical_valid(self) -> bool:
        """v3 출처표의 필수 신원·위치·상태가 모두 있는가."""

        date = self.published_at or self.disclosed_at or self.collected_at
        return all(
            (
                self.number > 0,
                bool(self.source_id.strip()),
                bool((self.title or self.label).strip()),
                bool(self.publisher.strip()),
                _valid_iso_date(date),
                all(
                    not candidate.strip() or _valid_iso_date(candidate)
                    for candidate in (
                        self.published_at,
                        self.disclosed_at,
                        self.collected_at,
                    )
                ),
                bool(self.host.strip()),
                bool(self.document_id.strip()),
                bool(self.location.strip()),
                _url_identity_is_bound(self),
                bool(self.source_type.strip()),
                bool(self.fact_status.strip()),
                bool(self.evidence_hashes),
                all(re.fullmatch(r"[0-9a-f]{64}", item) for item in self.evidence_hashes),
                len(self.evidence_hashes) == len(set(self.evidence_hashes)),
            )
        )

    @property
    def is_canonical_official(self) -> bool:
        """핵심 FactRecord의 단독 근거로 쓸 수 있는 공식 원문인가.

        canonical 메타데이터가 완전해야 하고, 자료 종류와 ``source_type``의 조합이
        닫힌 허용 목록에 정확히 들어야 한다. 외부 보도·증권사 분석은 검증 보조로만
        수집할 수 있으며 현재의 단일-source FactRecord에는 결속할 수 없다.
        """

        source_type = " ".join(self.source_type.split()).casefold()
        declared_official = (
            self.is_canonical_valid
            and source_type
            in _OFFICIAL_SOURCE_TYPES_BY_KIND.get(self.kind, frozenset())
        )
        if self.kind is SourceKind.OTHER:
            # Source 하나만 보고 OTHER 도메인의 소유자를 확정할 수는 없다. 다만
            # 독립 공시 결속 필드조차 없으면 후보로도 올리지 않아 조립 단계부터
            # fail-closed 한다. 실제 결속은 아래 registry 함수가 검증한다.
            return declared_official and bool(
                self.domain_attestation_source_id.strip()
                and self.domain_attestation_evidence.strip()
            )
        return declared_official


def _source_provenance_payload(source: Source) -> bytes:
    payload = {
        "number": source.number,
        "kind": source.kind.value,
        "label": source.label,
        "disclosed_at": source.disclosed_at,
        "collected_at": source.collected_at,
        "published_at": source.published_at,
        "domain": source.domain,
        "source_id": source.source_id,
        "title": source.title,
        "publisher": source.publisher,
        "host": source.host,
        "url": source.url,
        "document_id": source.document_id,
        "location": source.location,
        "source_type": source.source_type,
        "fact_status": source.fact_status,
        # ``used_in`` is a report-assembly projection, not collected provenance.
        # The assembler derives it from the final FactRecord set after collection,
        # so binding it here would invalidate an otherwise authentic source merely
        # because its section usage was calculated.  The publish gate validates the
        # derived usage independently against the facts.
        "evidence_hashes": sorted(source.evidence_hashes),
        "domain_attestation_source_id": source.domain_attestation_source_id,
        "domain_attestation_evidence": source.domain_attestation_evidence,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_collected_source(source: Source) -> Source:
    """신뢰된 수집 경계가 완성 Source에 붙이는 사후변조 방지 seal."""

    digest = hmac.new(
        _PROVENANCE_SEAL_KEY,
        _source_provenance_payload(source),
        hashlib.sha256,
    ).hexdigest()
    return replace(source, provenance_seal=digest)


def has_valid_provenance_seal(source: Source) -> bool:
    """저장·전달 뒤 Source 신원이나 원문 hash가 바뀌지 않았는지 검증한다."""

    received = str(source.provenance_seal or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", received):
        return False
    expected = hmac.new(
        _PROVENANCE_SEAL_KEY,
        _source_provenance_payload(source),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(received, expected)


def official_domain_attestation_problem(
    source: Source, sources: list[Source] | tuple[Source, ...]
) -> str:
    """OTHER 공식 원문의 도메인 소유 결속 오류를 반환한다.

    회사 웹 자료의 ``publisher/host/source_type`` 자기선언은 신뢰 경계가 아니다.
    알려진 DART/KIND 공시 원문이 같은 발행 법인의 홈페이지 URL을 실제 원문
    조각으로 보존했고 그 조각 해시까지 갖는 경우에만 공식 도메인으로 인정한다.
    특정 회사나 회사 도메인은 코드에 하드코딩하지 않는다.

    OTHER가 아닌 Source에는 적용할 계약이 없으므로 빈 문자열을 반환한다.
    """

    if source.kind is not SourceKind.OTHER:
        return ""
    if not source.is_canonical_official:
        return "공식 OTHER 원문의 필수 메타데이터 또는 독립 도메인 근거가 없습니다"

    attestation_id = source.domain_attestation_source_id.strip()
    matches = [item for item in sources if item.source_id.strip() == attestation_id]
    if len(matches) != 1:
        return "도메인 근거 source_id가 보고서의 단일 Source와 연결되지 않았습니다"
    attester = matches[0]
    if attester.source_id.strip() == source.source_id.strip():
        return "회사 웹 원문이 자기 자신의 공식 도메인을 증명할 수 없습니다"
    if attester.kind is not SourceKind.FILING or not attester.is_canonical_official:
        return "도메인 근거는 검증된 DART/KIND 공식 공시 원문이어야 합니다"
    if not has_valid_provenance_seal(attester):
        return "도메인 근거 공시의 수집 provenance seal이 없거나 변조됐습니다"
    if _publisher_key(attester.publisher) != _publisher_key(source.publisher):
        return "도메인 근거 공시의 발행 법인이 회사 웹 원문의 발행 법인과 다릅니다"

    evidence = source.domain_attestation_evidence.strip()
    if evidence_text_hash(evidence) not in attester.evidence_hashes:
        return "도메인 근거 원문 조각의 해시가 공시 Source에 보존되지 않았습니다"
    if _host_key(source.host) not in _hosts_in_domain_attestation_evidence(evidence):
        return "공시 원문 조각에 회사 웹 원문의 정확한 host URL이 없습니다"
    return ""


def is_canonical_official_with_registry(
    source: Source, sources: list[Source] | tuple[Source, ...]
) -> bool:
    """보고서 Source 등록부까지 대조한 최종 공식 원문 판정."""

    return (
        source.is_canonical_official
        and has_valid_provenance_seal(source)
        and not official_domain_attestation_problem(source, sources)
    )


# ══════════════════════════════════════════════════════════
# 쓰기 — 구조 → 마크다운
# ══════════════════════════════════════════════════════════


def _filing_meta_line(source: Source) -> str:
    """공시 자료의 두 번째 줄 — 「공시일 공시 · 수집 수집일」.

    둘 중 하나만 있어도 그것만 적는다. 있는 것까지 지우면 사실이 사라진다.
    """
    if source.disclosed_at and source.collected_at:
        return f"{source.disclosed_at} 공시 · 수집 {source.collected_at}"
    if source.disclosed_at:
        return f"{source.disclosed_at} 공시"
    if source.collected_at:
        return f"수집 {source.collected_at}"
    return ""


def render_sources(sources: list[Source]) -> str:
    """출처 목록을 화면·워드·노션이 그대로 쓸 마크다운 블록으로 만든다.

    세 형태(화면·워드·노션)가 같은 문자열을 쓰게 하려는 것이다 (P3) — 형태마다
    따로 그리면 한쪽만 고쳤을 때 내용이 갈린다.

    Args:
        sources: 보고서 하나에 실릴 출처 목록.

    Returns:
        `[출처]` 머리말부터 시작하는 마크다운 블록.
    """
    lines = [SOURCES_HEADER]
    for source in sources:
        if source.kind is SourceKind.OTHER and source.collected_at:
            # ★ 홈페이지 같은 「기타」 자료는 «확인»으로 적는다.
            #   공시의 「수집 …」과 글자가 같으면 다시 읽을 때 공시로 잘못 분류된다.
            #   그리고 홈페이지는 언제든 바뀌므로 «언제 본 것인지»가 특히 중요하다.
            lines.append(f" [{source.number}] {source.label}")
            lines.append(f"     {OTHER_DATE_PREFIX}{source.collected_at}")
            continue
        if source.kind is SourceKind.NEWS:
            date = f" {source.published_at}" if source.published_at else ""
            domain = f"  ({source.domain})" if source.domain else ""
            lines.append(f" [{source.number}] {source.label}{date}{domain}")
            continue
        lines.append(f" [{source.number}] {source.label}")
        meta = _filing_meta_line(source)
        if meta:
            lines.append(f"     {meta}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 읽기 — 마크다운 → 구조
# ══════════════════════════════════════════════════════════

#: `[2] 감사보고서 제16장 수익인식 주석` 같은 항목 첫 줄.
_ENTRY = re.compile(r"^\s*\[(?P<num>\d+)\]\s*(?P<rest>.+?)\s*$")
#: 뉴스 줄 끝의 `2025-03-12  (mk.co.kr)` 꼴.
_NEWS_SUFFIX = re.compile(
    r"^(?P<label>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s*\((?P<domain>[^)]+)\)\s*$"
)
#: 공시 자료의 두 번째 줄 — 셋 중 하나로 갈린다 (둘 다 / 공시일만 / 수집일만).
_FILING_BOTH = re.compile(
    r"^\s*(?P<disclosed>\d{4}-\d{2}-\d{2})\s*공시\s*·\s*수집\s*"
    r"(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)
_FILING_DISCLOSED_ONLY = re.compile(r"^\s*(?P<disclosed>\d{4}-\d{2}-\d{2})\s*공시\s*$")
_FILING_COLLECTED_ONLY = re.compile(r"^\s*수집\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$")
#: 기타(홈페이지 등)의 두 번째 줄 — 「확인 날짜」. 공시의 「수집 날짜」와 구분한다.
_OTHER_CONFIRMED = re.compile(
    r"^\s*" + OTHER_DATE_PREFIX.strip() + r"\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)


def parse_sources(text: str) -> list[Source]:
    """마크다운 `[출처]` 블록을 다시 구조로 읽는다.

    ★ `render_sources()`가 쓴 형식만 읽는다 — 사람이 손으로 다르게 쓴 문서를
      복원하는 범용 파서가 아니다. 목적은 «왕복 보장»이다.

    Args:
        text: `render_sources()`가 만들었거나 그와 같은 모양인 마크다운.

    Returns:
        출처 목록. 항목을 하나도 못 찾으면 빈 목록.
    """
    lines = text.splitlines()
    sources: list[Source] = []
    idx = 0
    total = len(lines)

    while idx < total:
        entry = _ENTRY.match(lines[idx])
        if entry is None:
            idx += 1
            continue

        number = int(entry.group("num"))
        rest = entry.group("rest").strip()

        news = _NEWS_SUFFIX.match(rest)
        if news is not None:
            sources.append(
                Source(
                    number=number,
                    kind=SourceKind.NEWS,
                    label=news.group("label").strip(),
                    published_at=news.group("date"),
                    domain=news.group("domain").strip(),
                )
            )
            idx += 1
            continue

        disclosed_at = collected_at = ""
        kind = SourceKind.OTHER
        if idx + 1 < total:
            nxt = lines[idx + 1]
            both = _FILING_BOTH.match(nxt)
            disclosed_only = _FILING_DISCLOSED_ONLY.match(nxt)
            collected_only = _FILING_COLLECTED_ONLY.match(nxt)
            if both is not None:
                disclosed_at = both.group("disclosed")
                collected_at = both.group("collected")
                kind = SourceKind.FILING
                idx += 1
            elif disclosed_only is not None:
                disclosed_at = disclosed_only.group("disclosed")
                kind = SourceKind.FILING
                idx += 1
            elif collected_only is not None:
                collected_at = collected_only.group("collected")
                kind = SourceKind.FILING
                idx += 1
            else:
                confirmed = _OTHER_CONFIRMED.match(nxt)
                if confirmed is not None:
                    # 「확인 날짜」 = 기타(홈페이지 등). 종류를 공시로 바꾸지 않는다.
                    collected_at = confirmed.group("collected")
                    idx += 1

        sources.append(
            Source(
                number=number,
                kind=kind,
                label=rest,
                disclosed_at=disclosed_at,
                collected_at=collected_at,
            )
        )
        idx += 1

    return sources


def count_missing_dates(sources: list[Source]) -> int:
    """출처일·수집일이 하나라도 빠진 공시 자료 개수 (C3).

    맨 아래 목록에 날짜가 한곳에 모이는 «딸려 오는 효과»를 여기서 쓴다
    (정본 §근거 표기 — 딸려 오는 효과).

    ⚠️ 알려진 한계 — 마크다운으로 한 번 왕복(render → parse)하면, 날짜가
      하나도 없던 공시 항목은 겉모양이 '기타'와 똑같아져 더 이상 공시로
      구분되지 않는다 (표시 형식 자체에 「원래 공시였다」는 표식이 없다).
      그래서 이 함수는 **렌더링하기 전, 원본 in-memory 목록**에 대고 불러야
      정확하다.
    """
    return sum(
        1
        for s in sources
        if s.kind is SourceKind.FILING and not (s.disclosed_at and s.collected_at)
    )

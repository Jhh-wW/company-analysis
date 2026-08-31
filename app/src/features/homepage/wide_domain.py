"""공식 도메인군 계산 — «어느 호스트를 결속 근거와 함께 수집할지» 판정.

★ ① 같은 등록 도메인(예: ``x.com``)의 하위 도메인(``recruit.x.com``·``ir.x.com``)은
  자동으로 도메인군에 든다.
★ ② 공식 페이지 안에서 «명시적으로 링크된» 다른 호스트는 «후보»로만 들어온다.
  결속 근거(어느 공식 페이지의 어느 링크에서 발견됐는지)가 없는 호스트는
  절대 수집하지 않는다. 이 모듈은 등급을 매길 뿐 «공식 확정»을 선언하지
  않는다 — 최종 확정은 다른 담당자의 몫이다.

``logic.py``의 인증서 이름 대체-host 판정(``_registrable_core_name``)과 같은
알고리즘을 쓰지만, 그 모듈의 비공개 함수에 결합하지 않기 위해 상수만
공유하고 알고리즘은 이 파일에서 독립적으로 유지한다(의도적 소규모 중복 —
최종 보고서에 설계 결정으로 남긴다).
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from src.features.homepage.constants import (
    MULTI_LABEL_PUBLIC_SUFFIXES,
    SINGLE_LABEL_PUBLIC_SUFFIXES,
    WIDE_EXCLUDED_LINKED_HOST_SUFFIXES,
)


def registrable_core_name(host: str) -> str:
    """호스트에서 공개 접미사(.co.kr·.com 등)를 뗀 등록 도메인 핵심 이름 한 칸.

    Args:
        host: 포트·스킴이 없는 순수 호스트 이름.

    Returns:
        핵심 이름 한 칸(소문자). 판정 불가(빈 문자열 등)면 ``""``.
    """
    labels = [label for label in (host or "").lower().rstrip(".").split(".") if label]
    if len(labels) <= 1:
        return ".".join(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_PUBLIC_SUFFIXES:
        remainder = labels[:-2]
    elif labels[-1] in SINGLE_LABEL_PUBLIC_SUFFIXES:
        remainder = labels[:-1]
    else:
        # 목록에 없는 접미사 — 마지막 한 칸만 접미사로 보는 보수적 기본값.
        remainder = labels[:-1]
    return remainder[-1] if remainder else ""


def is_registered_subdomain(root_host: str, candidate_host: str) -> bool:
    """candidate_host가 root_host와 같은 등록 도메인의 (하위)도메인인가."""
    root_core = registrable_core_name(root_host)
    if not root_core:
        return False
    candidate_core = registrable_core_name(candidate_host)
    return bool(candidate_core) and candidate_core == root_core


def is_excluded_linked_host(host: str) -> bool:
    """소셜·광고·분석 등 «회사의 다른 공식 채널」로 보지 않는 호스트인가."""
    normalized = (host or "").lower().rstrip(".")
    if not normalized:
        return True
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in WIDE_EXCLUDED_LINKED_HOST_SUFFIXES
    )


@dataclass(frozen=True)
class BoundHost:
    """도메인군에 들어온 호스트 하나와 그 결속 근거·요구도."""

    host: str
    identity_binding: str
    #: True면 REQUIRED(고 결속) 문서, False면 OPTIONAL(후보) 문서로 만든다.
    is_high_confidence: bool


def bind_root_host(root_host: str) -> BoundHost:
    """DART가 준 홈페이지 호스트 — 가장 높은 신뢰도."""
    return BoundHost(
        host=root_host.casefold(),
        identity_binding="DART 기업개황 홈페이지 주소(root)",
        is_high_confidence=True,
    )


def bind_registered_subdomain(root_host: str, candidate_host: str) -> BoundHost | None:
    """candidate_host가 root_host와 같은 등록 도메인이면 자동 결속한다."""
    if not is_registered_subdomain(root_host, candidate_host):
        return None
    return BoundHost(
        host=candidate_host.casefold(),
        identity_binding=(
            f"root({root_host})와 같은 등록 도메인의 하위 도메인"
        ),
        is_high_confidence=True,
    )


def bind_linked_host(
    *, source_page_url: str, discovered_url: str, candidate_host: str
) -> BoundHost | None:
    """공식 페이지 안에서 명시적으로 링크된 다른 호스트 — «후보»로만 결속한다.

    소셜·광고·분석 호스트는 결속하지 않는다(``is_excluded_linked_host``).

    Returns:
        결속 정보, 또는 제외 대상이면 ``None``.
    """
    if is_excluded_linked_host(candidate_host):
        return None
    return BoundHost(
        host=candidate_host.casefold(),
        identity_binding=(
            f"공식 페이지 링크 후보 — 출처 페이지: {source_page_url}, "
            f"발견된 링크: {discovered_url}"
        ),
        is_high_confidence=False,
    )


_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)
_TRACKING_PARAM_EXACT: frozenset[str] = frozenset(
    {"gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "spm", "yclid"}
)


def _is_tracking_param(key: str) -> bool:
    lowered = key.casefold()
    return lowered.startswith(_TRACKING_PARAM_PREFIXES) or lowered in _TRACKING_PARAM_EXACT


def canonicalize_url(url: str) -> str:
    """fragment를 없애고 추적 파라미터를 뺀 정규화 URL을 만든다.

    스킴·호스트는 소문자로, 쿼리는 키 순서로 정렬해 같은 문서가 파라미터
    순서차이만으로 다른 URL이 되지 않게 한다.
    """
    parsed = urllib.parse.urlsplit(url)
    query_pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    ]
    query = urllib.parse.urlencode(sorted(query_pairs))
    path = parsed.path or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path, query, "")
    )

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
    WIDE_PRIORITY_HOST_KEYWORDS,
    WIDE_SLOT_KEYWORD_MAP,
)

#: 하위 도메인 이름으로도 매칭해도 안전한 키워드만(예: recruit.company.example).
#: 「company」처럼 흔한 낱말은 회사 등록 도메인 자체에 우연히 들어 있을 수
#: 있어(예: company.example) 호스트 전체 문자열 대조에 넣지 않는다 — 이미
#: `constants.py`의 PRIORITY_PATH_KEYWORDS 주석에 실측으로 남긴 함정과 같다.
_HOST_SAFE_KEYWORDS: frozenset[str] = frozenset(WIDE_PRIORITY_HOST_KEYWORDS)


def registrable_core_name(host: str) -> str:
    """호스트의 전체 등록 도메인(eTLD+1 — 공개 접미사 + 그 바로 앞 한 칸)을 돌려준다.

    ★ P0-1 수정(2026-08-31): 예전 구현은 접미사를 뗀 뒤 «핵심 이름 한 칸만»
      돌려줘서 ``company.com``·``company.net``·``company.co.kr``이 전부 같은
      값(``"company"``)이 되어 서로 다른 등록 도메인이 같다고 오판했다(남의
      도메인이 REQUIRED 고신뢰 문서로 자동 승격됨). 이제 접미사를 **포함해서**
      돌려주므로(``"company.com"``·``"company.co.kr"`` 등) TLD가 다르면 값도
      달라진다.

    Args:
        host: 포트·스킴이 없는 순수 호스트 이름.

    Returns:
        eTLD+1 전체 문자열(소문자). 판정 불가(빈 문자열 등) 또는 공개 접미사
        목록 밖의 접미사면 ``""``(fail-closed — 등록 도메인 경계를 모르는
        채로 «같은 도메인」이라고 주장하지 않는다).
    """
    labels = [label for label in (host or "").lower().rstrip(".").split(".") if label]
    if len(labels) <= 1:
        return ".".join(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_PUBLIC_SUFFIXES:
        suffix_labels = 2
    elif labels[-1] in SINGLE_LABEL_PUBLIC_SUFFIXES:
        suffix_labels = 1
    else:
        # 목록에 없는 접미사 — fail-closed. 경계를 모르는 채로 같다고
        # 주장하지 않는다(예전의 「한 칸만 접미사로 보는 보수적 기본값」은
        # 서로 다른 미지 TLD를 같다고 오판할 수 있어 폐기했다).
        return ""
    remainder = labels[: len(labels) - suffix_labels]
    if not remainder:
        return ""
    return ".".join(labels[-(suffix_labels + 1) :])


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


def slot_ids_for_url(url: str) -> tuple[str, ...]:
    """URL 안의 페이지 유형 키워드로 후보 슬롯 집합을 고른다(첫 일치 우선).

    경로(+쿼리)는 모든 키워드로 대조하지만, 호스트 이름은 «하위 도메인으로
    써도 안전한» 키워드(``_HOST_SAFE_KEYWORDS``, recruit·ir·news 등)만
    라벨 단위로 정확히 대조한다. 그래야 recruit.company.example 같은
    도메인은 잡아내면서, company.example처럼 회사 도메인 자체에 우연히
    들어 있는 낱말(«company» 등)이 모든 페이지를 잘못 분류하지 않는다.

    `wide_collect.py`(attempt.slot_ids)와 `wide_fragments.py`(조각 슬롯 태깅)가
    같은 표(`constants.WIDE_SLOT_KEYWORD_MAP`)를 이 함수 하나로 공유한다 —
    두 곳에 각각 다른 매핑을 두지 않는다.
    """
    parsed = urllib.parse.urlsplit(url)
    host_labels = frozenset((parsed.hostname or "").lower().split("."))
    path_and_query = urllib.parse.unquote(parsed.path).lower()
    if parsed.query:
        path_and_query = f"{path_and_query}?{urllib.parse.unquote(parsed.query).lower()}"

    for keywords, slots in WIDE_SLOT_KEYWORD_MAP:
        if any(keyword in path_and_query for keyword in keywords):
            return slots
        if any(
            keyword in host_labels for keyword in keywords if keyword in _HOST_SAFE_KEYWORDS
        ):
            return slots
    return ()

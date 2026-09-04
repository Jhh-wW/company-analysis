"""회사 공식 웹 결속에 쓰는 닫힌 등록 도메인(eTLD+1) 규칙.

완전한 Public Suffix List를 런타임 네트워크에서 내려받지 않는다. 지원하는
공개 접미사만 명시하고, 모르는 접미사는 같은 회사 도메인이라고 추측하지
않는다. 홈페이지 수집과 공개 Source 역검산이 반드시 이 정본을 함께 쓴다.
"""

from __future__ import annotations

from typing import Final


MULTI_LABEL_PUBLIC_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "co.kr",
        "or.kr",
        "go.kr",
        "ac.kr",
        "ne.kr",
        "pe.kr",
        "re.kr",
        "co.jp",
        "co.uk",
        "com.cn",
    }
)

SINGLE_LABEL_PUBLIC_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "kr",
        "com",
        "net",
        "org",
        "co",
        "io",
        "biz",
        "info",
        "me",
        "tv",
        "asia",
        "shop",
    }
)

# RFC 2606 예약 TLD. 오프라인 시험만 실제 판정 경로를 타게 하며 운영
# 커버리지라고 표시해서는 안 된다.
TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES: Final[frozenset[str]] = frozenset(
    {"example"}
)

_SINGLE_LABEL_SUFFIXES_FOR_MATCHING = (
    SINGLE_LABEL_PUBLIC_SUFFIXES | TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES
)


def registrable_domain(host: object) -> str:
    """지원 목록으로 확정할 수 있는 eTLD+1만 반환한다."""

    if type(host) is not str:
        return ""
    labels = [label for label in host.casefold().rstrip(".").split(".") if label]
    if len(labels) <= 1:
        return ""
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_PUBLIC_SUFFIXES:
        suffix_labels = 2
    elif labels[-1] in _SINGLE_LABEL_SUFFIXES_FOR_MATCHING:
        suffix_labels = 1
    else:
        return ""
    if len(labels) <= suffix_labels:
        return ""
    return ".".join(labels[-(suffix_labels + 1) :])


def is_actual_registered_subdomain(root_host: object, candidate_host: object) -> bool:
    """candidate가 인증 root의 실제 자손 host일 때만 참이다.

    같은 eTLD+1이라는 이유만으로 ``a.company.com``과
    ``b.company.com`` 같은 형제 host를 합치지 않는다. 공개 접미사 자체나
    목록 밖 접미사도 fail-closed 한다.
    """

    if type(root_host) is not str or type(candidate_host) is not str:
        return False
    root = root_host.casefold().rstrip(".")
    candidate = candidate_host.casefold().rstrip(".")
    root_domain = registrable_domain(root)
    candidate_domain = registrable_domain(candidate)
    return bool(
        root
        and candidate
        and root_domain
        and candidate_domain == root_domain
        and candidate != root
        and candidate.endswith(f".{root}")
    )

"""여러 feature·adapter가 공유하는 문서 source_kind 정본을 잠근다.

homepage feature(collector 재생 조각5)가 만드는 문서의 source_kind가
이 상수와 어긋나면, 나중에 배선될 pipeline 병합 로직(조각8·9, 이번
범위 밖)이 공식 웹 문서를 조용히 걸러내게 된다.
"""

from __future__ import annotations

from src.shared.report_evidence.constants import (
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)


def test_공식_웹_source_kind_정본은_두_값만_담는다() -> None:
    assert OFFICIAL_WEB_SOURCE_KINDS == frozenset(
        {"official_web_page", "official_recruit_page"}
    )
    assert SOURCE_KIND_OFFICIAL_WEB_PAGE in OFFICIAL_WEB_SOURCE_KINDS
    assert SOURCE_KIND_OFFICIAL_RECRUIT_PAGE in OFFICIAL_WEB_SOURCE_KINDS

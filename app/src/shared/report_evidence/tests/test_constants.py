"""여러 feature·adapter가 공유하는 문서 source_kind 정본을 잠근다.

homepage feature(collector 재생 조각5)가 만드는 문서의 source_kind가
이 상수와 어긋나면, 나중에 배선될 pipeline 병합 로직(조각8·9, 이번
범위 밖)이 공식 웹 문서를 조용히 걸러내게 된다.
"""

from __future__ import annotations

from src.shared.report_evidence.constants import (
    COLLECTION_CAP_TRUNCATION_REASON_CODES,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)


def test_공식_웹_source_kind_정본은_실제_네_생산종류를_담는다() -> None:
    assert OFFICIAL_WEB_SOURCE_KINDS == frozenset(
        {
            "official_web_page",
            "official_recruit_page",
            "official_ir_pdf",
            "official_identity_verified_web_page",
        }
    )
    assert SOURCE_KIND_OFFICIAL_WEB_PAGE in OFFICIAL_WEB_SOURCE_KINDS
    assert SOURCE_KIND_OFFICIAL_RECRUIT_PAGE in OFFICIAL_WEB_SOURCE_KINDS
    assert SOURCE_KIND_OFFICIAL_IR_PDF in OFFICIAL_WEB_SOURCE_KINDS
    assert SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE in OFFICIAL_WEB_SOURCE_KINDS


def test_출고를_막지_않는_설계상한_잘림_사유는_쪽수_바이트_시간_셋뿐이다() -> None:
    """리터럴 오라클 — 목록이 넓어지면 FULL 출고 차단 예외가 조용히 커진다.

    이 목록에 든 사유로 잘린 필수 웹 경로는 출고를 막지 않는다. 신원 보강·
    리디렉션 상한 같은 다른 잘림이 섞이면 끝까지 확인하지 못한 필수 경로가
    FULL로 나간다. 값을 바꾸려면 ADR 0004와 이 시험을 함께 고친다.
    """

    assert COLLECTION_CAP_TRUNCATION_REASON_CODES == frozenset(
        {"truncated_page_cap", "truncated_byte_cap", "truncated_time_cap"}
    )

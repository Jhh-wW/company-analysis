"""원문 내용과 별개로 명시적인 자료 종류의 오귀속을 막는다."""

from collections.abc import Mapping

from src.features.composer.port import CollectedFragment
from src.features.composer.source_attribution_constants import (
    BUSINESS_REPORT_ATTRIBUTION_RE, BUSINESS_REPORT_NAME_RE,
    BUSINESS_REPORT_SOURCE_KINDS, SOURCE_ATTRIBUTION_MISMATCH,
)


def source_attribution_problem(
    text: str,
    sources: Mapping[str, str],
    fragments: Mapping[str, CollectedFragment],
) -> str:
    """자기 인용 모두가 명시한 자료 종류와 다른 경우만 거절한다.

    문서 이름을 언급한 원문이나 같은 종류의 자료가 함께 인용되면 이 좁은
    검사로 거절하지 않는다. 내용의 지원 여부는 기존 의미 검사가 맡는다.
    메타데이터가 없는 legacy 자료의 종류를 URL로 추측하지 않는다.
    """
    if not BUSINESS_REPORT_ATTRIBUTION_RE.search(text):
        return ""
    selected = [fragments.get(source_id) for source_id in sources]
    if not selected or any(fragment is None or not fragment.formal_source_kind
                           for fragment in selected):
        return ""
    for fragment in selected:
        if (fragment.formal_source_kind in BUSINESS_REPORT_SOURCE_KINDS
                or BUSINESS_REPORT_NAME_RE.search(fragment.document_title)
                or BUSINESS_REPORT_NAME_RE.search(fragment.text)):
            return ""
    return SOURCE_ATTRIBUTION_MISMATCH

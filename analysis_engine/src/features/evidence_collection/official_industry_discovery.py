"""장별 점수와 분리해 공식 산업 검수 기회만 보존한다."""

from features.evidence_collection import official_industry_discovery_constants as c


def official_industry_discovery(text: str) -> bool:
    """산업 범위와 변화 표지만 읽으며 문제·주체·기간·지역을 승인하지 않는다.

    계획이나 정의가 함께 있더라도 원문을 절단하지 않는다. 정확 사업 연결과
    현재 관찰·자료기간·실제 지역은 마지막 별도 산업 검수가 판단한다.
    """
    return bool(c.INDUSTRY_SCOPE_RE.search(text) and c.OBSERVED_CHANGE_RE.search(text))

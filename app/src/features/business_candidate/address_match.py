"""같은 광역지역과 같은 도로·건물번호를 구분하는 검색용 주소 비교."""

import re
import unicodedata

from src.features.business_candidate.address_constants import (
    ADDRESS_BUILDING_STRENGTH,
    ADDRESS_DISTRICT_PATTERN,
    ADDRESS_DISTRICT_STRENGTH,
    ADDRESS_NUMBER_PATTERN,
    ADDRESS_REGION_ALIASES,
    ADDRESS_ROAD_PATTERN,
    ADDRESS_ROAD_STRENGTH,
)


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    for official, short in ADDRESS_REGION_ALIASES.items():
        text = text.replace(official, short)
    return text


def address_match_strength(hint: str, address: str) -> int:
    """0 없음, 1 광역지역, 2 시군구·읍면동, 3 도로, 4 도로와 건물번호."""
    left, right = _normalized(hint), _normalized(address)
    compact_left, compact_right = re.sub(r"\s+", "", left), re.sub(r"\s+", "", right)
    if not compact_left or not compact_right:
        return 0
    strength = 0
    regions = set(ADDRESS_REGION_ALIASES.values())
    left_region = next((item for item in regions if compact_left.startswith(item)), "")
    right_region = next((item for item in regions if compact_right.startswith(item)), "")
    if left_region and right_region:
        if left_region != right_region:
            return 0
        strength = 1
    # 지역이 명백히 다른데 흔한 도로명·건물번호만 같은 경우는 상세 일치가 아니다.
    left_districts = set(re.findall(ADDRESS_DISTRICT_PATTERN, left.removeprefix(left_region)))
    right_districts = set(re.findall(ADDRESS_DISTRICT_PATTERN, right.removeprefix(right_region)))
    for suffix in ("구", "군", "시", "동", "읍", "면"):
        left_parts = {item for item in left_districts if item.endswith(suffix)}
        right_parts = {item for item in right_districts if item.endswith(suffix)}
        if left_parts and right_parts and not left_parts & right_parts:
            return strength
    for district in set(re.findall(ADDRESS_DISTRICT_PATTERN, left + " " + right)):
        if district in compact_left and district in compact_right:
            strength = max(strength, ADDRESS_DISTRICT_STRENGTH)
    for road in set(re.findall(ADDRESS_ROAD_PATTERN, left + " " + right)):
        if road not in compact_left or road not in compact_right:
            continue
        strength = max(strength, ADDRESS_ROAD_STRENGTH)
        pattern = re.escape(road) + ADDRESS_NUMBER_PATTERN
        left_number = re.search(pattern, compact_left)
        right_number = re.search(pattern, compact_right)
        if left_number and right_number and left_number[1] == right_number[1]:
            strength = ADDRESS_BUILDING_STRENGTH
    return strength

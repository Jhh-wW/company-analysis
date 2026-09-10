"""회사명 동점에서 광역지역·상세주소와 서로 다른 지역을 구분한다."""

import pytest

from src.features.business_candidate.address_match import address_match_strength


@pytest.mark.parametrize(("hint", "address", "strength"), [
    ("서울", "서울특별시 강남구 테헤란로 1", 1),
    ("서울 성동구", "서울특별시 성동구 왕십리로 83-21", 2),
    ("왕십리로", "서울 성동구 왕십리로 83-21", 3),
    ("왕십리로 83-21", "서울 성동구 왕십리로 83-21", 4),
    ("서울성동구왕십리로83-21", "서울특별시 성동구 왕십리로 83-21", 4),
    ("왕십리로 83", "서울 성동구 왕십리로 83-21", 3),
    ("왕십리로 83-21", "서울 성동구 왕십리로 83-210", 3),
    ("서울 중구 중앙로 10", "대전광역시 중구 중앙로 10", 0),
    ("서울 중구 중앙로 10", "서울특별시 강남구 중앙로 10", 1),
    ("부산 해운대구", "부산광역시 수영구 수영로 1", 1),
    ("경기 수원시", "경기도 수원시 영통구", 2),
    ("", "서울", 0),
])
def test_address_match_specificity(hint, address, strength):
    assert address_match_strength(hint, address) == strength
    assert address_match_strength(address, hint) == strength

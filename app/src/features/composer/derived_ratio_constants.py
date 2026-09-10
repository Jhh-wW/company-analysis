"""파생 비율 재계산의 허용 오차·재계산 종류·조합 상한과 사유 코드.

★ 왜 별도 파일인가 — 이 값들은 «관문이 얼마나 느슨해지는가»를 정하는 손잡이다.
  코드 안에 숫자로 박아 두면 나중에 조용히 늘어난다. 한곳에 모아 두면 값 하나만
  키워 보는 음성 대조(시험이 실제로 이 값을 지키는지)도 가능하다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from src.features.composer.grounding_constants import (
    DIMENSION_AMOUNT,
    DIMENSION_COUNT,
    DIMENSION_FOREIGN,
)

#: 백분율의 만점. `a / b × 100`.
RATIO_FULL_SCALE: Final[Decimal] = Decimal(100)

#: 재계산 종류 — 구성비(a가 b에서 차지하는 몫)와 증감률(a에서 b로의 변화율).
#: 종류를 늘리면 «우연히 맞는» 조합이 늘어난다. 늘릴 때는 아래 상한과 함께 본다.
DERIVED_RATIO_KIND_SHARE: Final[str] = "구성비"
DERIVED_RATIO_KIND_CHANGE: Final[str] = "증감률"
DERIVED_RATIO_KINDS: Final[tuple[str, ...]] = (
    DERIVED_RATIO_KIND_SHARE,
    DERIVED_RATIO_KIND_CHANGE,
)

#: 허용 오차 = 후보가 «적어 낸 자릿수»의 몇 칸까지인가.
#:
#: ★ 왜 «적어 낸 자릿수» 기준인가 — 작성기가 「90%」라고 소수점 없이 적었다면
#:   그 주장은 「반올림해서 90」이라는 뜻이다. 참값 89.6283%는 여기 든다.
#:   반대로 「89.63%」라고 두 자리까지 적었다면 그 주장은 두 자리까지 맞다는
#:   뜻이므로 오차도 두 자리 기준으로 좁아진다. 고정 폭(±0.5%p)을 쓰면 소수까지
#:   적은 후보를 필요 이상으로 느슨하게 봐 준다.
#: ★ 0.5는 «반올림 반 칸»이다 — 이 값보다 크면 반올림으로 설명되지 않는 차이를
#:   통과시키기 시작한다. 즉 이 상수를 키우는 것은 관문을 여는 것이다.
#: ★ 실측 (2026-09-11, 보관 공시 23건 · 상한 안에 드는 조각 3,504개):
#:   지어낸 백분율이 우연히 통과하는 비율은 적어 낸 자릿수에 정비례해 줄어든다 —
#:   정수 23.4% · 소수 한 자리 3.5% · 소수 두 자리 0.39%. 반 칸이 정확히 격자
#:   한 칸을 덮기 때문이다. 즉 남는 위험은 «둥근 정수 백분율»에 몰려 있고,
#:   그 뒤에도 도식 의미 검수(AI)가 한 번 더 본다.
DERIVED_RATIO_TOLERANCE_QUANTA: Final[Decimal] = Decimal("0.5")

#: 조각 하나에서 볼 원값 조합의 상한.
#:
#: ★ 왜 상한이 필요한가 — 조합이 많을수록 «지어낸 비율»이 우연히 어떤 조합과
#:   맞아떨어질 확률이 올라간다. 상한을 넘는 조각은 인정하지 않고 사유만 남긴다
#:   (fail-closed). 계산량도 함께 묶인다.
#: ★ 값의 근거 (2026-09-11 실측, 보관 공시 23건을 운영 평문으로 바꿔
#:   1,200자 조각 6,465개로 자른 뒤 잰 값):
#:     · 이 규칙이 «살리려는» 자리 = 매출 구성 표가 든 조각 530개.
#:       그 조각들의 조합 수는 중앙값 32 · p90 58 · **p95 64** · 최대 79다.
#:     · 상한 64는 그 조각들의 95%를 살린다. 상한 16이면 23.8%,
#:       상한 36이면 59.2%만 살아 정작 고치려던 카드가 계속 죽는다.
#:     · 상한 64를 넘는 조각(전체의 8%)은 대개 수십 줄짜리 재무제표 전문이라
#:       우연 일치가 가장 잘 나는 자리다.
#: ⚠️ 상한은 우연 일치를 «많이» 줄여 주지 못한다 — 상한 없음 26.5%,
#:    상한 50 20.7%, 상한 64 23.4%(정수 백분율 기준)다. 우연 일치를 실제로
#:    막는 것은 앵커 제약(`derived_ratio.anchor_values`)과 자릿수 기반 오차다.
DERIVED_RATIO_MAX_PAIRS_PER_FRAGMENT: Final[int] = 64

#: 원값으로 쓸 수 있는 차원 — 금액·수량·외화. 비율과 배수는 원값이 아니다.
#: (판정은 grounding._dimension_at 한 벌을 그대로 쓴다. 목록을 새로 만들지 않는다.)
DERIVED_RATIO_SOURCE_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {DIMENSION_AMOUNT, DIMENSION_COUNT, DIMENSION_FOREIGN}
)

#: 사유 코드 — 인정한 경우와, 조합이 상한을 넘어 인정하지 않은 경우.
DERIVED_RATIO_RECOMPUTED_CODE: Final[str] = "derived_ratio_recomputed"
DERIVED_RATIO_PAIR_LIMIT_CODE: Final[str] = "derived_ratio_pair_limit"

#: 진단 기록의 종류 표시 — 도식 수치 관문이 남기는 항목임을 밝힌다.
DERIVED_RATIO_DIAGNOSTIC_KIND: Final[str] = "도식수치"

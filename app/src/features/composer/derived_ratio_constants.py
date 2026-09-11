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
# 사유 코드와 종류 이름은 «실행 기록 계약»이 정본이다. 여기서 따로 적으면
# 기록 쪽 닫힌 목록과 어긋나 항목이 조용히 버려진다.
from src.shared.report_quality.composition_diagnostic_constants import (
    DERIVED_RATIO_PAIR_LIMIT,
    DERIVED_RATIO_RECOMPUTED,
    DERIVED_RATIO_SHARE_KIND,
)

#: 백분율의 만점. `a / b × 100`.
RATIO_FULL_SCALE: Final[Decimal] = Decimal(100)

#: 재계산 종류 — «구성비»(a가 b에서 차지하는 몫) 하나뿐이다.
#:
#: ★ 증감률 갈래는 걷어냈다 (2026-09-11 독립 검토) — 실측 결함은 구성비뿐이고,
#:   증감률을 함께 인정하면 지어낸 정수 백분율의 우연 통과율이 두 배가 된다
#:   (검토 코퍼스 DART 36건·조각 10,576개: 구성비만 17.37% → 둘 다 34.02%).
#:   그러면서 증감률 쪽에는 지켜 줄 시험이 하나도 없었다.
DERIVED_RATIO_KIND_SHARE: Final[str] = DERIVED_RATIO_SHARE_KIND

#: 분모가 될 수 있는 «총액 성격의 행 이름».
#:
#: ★ 왜 분모를 가두나 (독립 검토 실측) — 분모를 자유롭게 두면 조각 안 아무 큰
#:   값이나 전체가 되어, 지어낸 정수 백분율의 34.02%가 통과했다. 분모를 «총액
#:   줄»로만 좁히면 2%대로 떨어지고 실제 인텍 카드는 그대로 살아난다.
#: ★ 구성비의 뜻 자체가 「부분 ÷ 전체」다. 전체가 아닌 값을 분모로 쓰는 구성비는
#:   애초에 구성비가 아니다. 즉 이 제약은 기능을 깎는 게 아니라 정의를 지킨다.
#: ★ 닫힌 어휘다 — 회사·업종별 분기가 아니라 «공시 표가 총계 줄에 쓰는 말»이다.
#: ★ 여러 글자짜리는 «꼬리»로 본다(「자 본 총 계」→「자본총계」). 한 글자
#:   「계」와 뜻이 넓은 「매출액」·「전체」는 «그 줄 이름 전체»가 그것일 때만
#:   인정한다 — 꼬리로 보면 「상품매출액」·「회계」까지 총액이 된다.
DERIVED_RATIO_TOTAL_ROW_SUFFIXES: Final[tuple[str, ...]] = (
    "합계",
    "총계",
    "소계",
    "총액",
    "합계액",
)
DERIVED_RATIO_TOTAL_ROW_NAMES: Final[frozenset[str]] = frozenset(
    {*DERIVED_RATIO_TOTAL_ROW_SUFFIXES, "계", "매출액", "전체"}
)

#: 행 이름을 찾아 거슬러 올라갈 최대 줄 수.
#:
#: ★ 왜 필요한가 (독립 검토 실측) — 운영 평문은 표를 줄 단위로 편다. 행 이름과
#:   값이 «다른 줄»에 있어, 같은 줄만 보는 `grounding._cell_label`은 실제
#:   평문에서 이름을 0.9%만 읽었다. 그래서 앞 줄을 거슬러 읽는다.
#: ★ 값이 여러 해(당기·전기·전전기)로 늘어서면 이름이 그만큼 위에 있다.
#:   3개년 + 여유 1줄로 4줄이면 실측 표를 덮는다. 더 멀리 올라가면 «위 행의
#:   이름»을 이 행에 붙이게 되므로 늘리지 않는다.
DERIVED_RATIO_LABEL_LOOKBACK_LINES: Final[int] = 4

#: 허용 오차 = 후보가 «적어 낸 자릿수»의 몇 칸까지인가.
#:
#: ★ 왜 «적어 낸 자릿수» 기준인가 — 작성기가 「90%」라고 소수점 없이 적었다면
#:   그 주장은 「반올림해서 90」이라는 뜻이다. 참값 89.6283%는 여기 든다.
#:   반대로 「89.63%」라고 두 자리까지 적었다면 그 주장은 두 자리까지 맞다는
#:   뜻이므로 오차도 두 자리 기준으로 좁아진다. 고정 폭(±0.5%p)을 쓰면 소수까지
#:   적은 후보를 필요 이상으로 느슨하게 봐 준다.
#: ★ 0.5는 «반올림 반 칸»이다 — 이 값보다 크면 반올림으로 설명되지 않는 차이를
#:   통과시키기 시작한다. 즉 이 상수를 키우는 것은 관문을 여는 것이다.
#: ★ 실측 (2026-09-11 재측정, 독립 검토 코퍼스 DART 36건·조각 10,576개 · Re 규칙):
#:   지어낸 백분율이 우연히 통과하는 비율은 적어 낸 자릿수에 정비례해 줄어든다 —
#:   정수 1.60% · 소수 한 자리 0.23% · 소수 두 자리 0.026%. 반 칸이 정확히 격자
#:   한 칸을 덮기 때문이다.
DERIVED_RATIO_TOLERANCE_QUANTA: Final[Decimal] = Decimal("0.5")

#: 조각 하나에서 볼 원값 조합의 상한.
#:
#: ★ 이것은 «계산량 한계»이지 우연 일치 방어가 아니다. 우연 일치를 막는 일은
#:   분모 총액 제약과 앵커 제약이 한다. 상한을 근거로 관문이 좁다고 말하지 마라.
#: ★ 값의 근거 (2026-09-11 재측정 — 독립 검토 코퍼스 DART 36건·조각 10,576개를
#:   Re 규칙으로 다시 재고, 짝이 가장 많이 나오는 «총액 줄을 앵커로 잡은» 최악을
#:   골라 잼). 카드 한 줄이 적는 금액 개수별 조각당 조합 수:
#:
#:       금액 1개 → p99 64 · 최대 87
#:       금액 2개 → p99 124 · 최대 168
#:       금액 3개 → p99 179 · **최대 228**
#:
#:   256은 그 실측 최대 위에 있다. 즉 실제 공시에서는 이 상한에 걸리지 않는다.
#: ⚠️ 64로 두었다가 **정작 고치려던 카드를 잘랐다** (독립 검토 P0-1) — 실제
#:   조각[45]은 카드가 「273.51억원 중 89.63%」처럼 «총액» 쪽을 적으면 짝이
#:   66개가 되어 상한을 넘었다. 상한을 «살리려는 조각의 분포»로 정했는데 정작
#:   목표가 꼬리에 있었다. 이 상수를 다시 낮추려면 실제 조각으로 먼저 재라.
DERIVED_RATIO_MAX_PAIRS_PER_FRAGMENT: Final[int] = 256

#: 원값으로 쓸 수 있는 차원 — 금액·수량·외화. 비율과 배수는 원값이 아니다.
#: (판정은 grounding._dimension_at 한 벌을 그대로 쓴다. 목록을 새로 만들지 않는다.)
DERIVED_RATIO_SOURCE_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {DIMENSION_AMOUNT, DIMENSION_COUNT, DIMENSION_FOREIGN}
)

#: 사유 코드 — 인정한 경우와, 조합이 상한을 넘어 인정하지 않은 경우.
DERIVED_RATIO_RECOMPUTED_CODE: Final[str] = DERIVED_RATIO_RECOMPUTED
DERIVED_RATIO_PAIR_LIMIT_CODE: Final[str] = DERIVED_RATIO_PAIR_LIMIT

"""3장 카드에 공시 이름 표의 이름을 «결정적으로» 올린다.

★ 왜 필요한가 (2026-09-06 운영 실측 2회) — 3장 packet에 대표 이름 조각이
  열 개 넘게 실렸고, 작가 안내문(`PORTFOLIO_TABLE_GUIDE_V2`)도 「카드 하나는
  «반드시» 그 이름들을 담는다」로 강제했는데, 실제 작가 AI는 여전히 부문
  카드 두 개만 냈다. 안내문은 «부탁»이고 부탁은 지켜지지 않을 수 있다.
  이름이 카드에 오르는 일을 작가의 순응에만 맡기지 않는다.

★ 이 모듈은 카드를 «지어내지» 않는다. 조각의 글자와 인용만 투영한다:
  · 「제품·서비스명」 = 첫 번째 이름을 «글자 그대로».
  · 「제품·서비스 범위」 = 이름들을 종류별로 묶어 나열. 종류 라벨은
    정본 표(`shared/name_fragments`)에서 꺼내며 우리가 짓지 않는다.
  · 「중점 추진 근거」 = 고정 문구 하나(아래 상수). 회사·업종 어휘 없음.
  · 「사업적 역할」 = 빈 칸. 이름 표만으로는 역할을 알 수 없어서 비운다
    (카드 렌더러는 빈 칸을 «뺀다» — 「미확인」이 인쇄되지 않는다).
  · 「범위·한계」 = 카드 렌더러가 이미 붙이는 그 장의 고정 문구 그대로.

★ 제목 칸을 왜 «종류 라벨」이 아니라 «첫 이름»으로 두나 —
  `diagram_check`의 3장 이름 접지 검사는 제목 칸이 인용 조각 원문의
  부분문자열이 아니면 그 칸을 조용히 비운다. 종류 라벨을 쓰려면 그
  검사에 「결정적 카드」 면제를 뚫어야 하는데, 그 면제는 «근거 없는 이름을
  막는 유일한 기계 장치»에 구멍을 내는 일이다. 뒷날 다른 경로가 그
  구멍으로 들어오면 우리는 못 잡는다. 이름을 그대로 쓰면 면제가 필요
  없고, 검사를 다시 돌려도 우리 카드가 그대로 남는다(멱등).

★ 업종·회사로 갈리는 분기를 두지 않는다. 조각의 «종류 라벨»만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Optional, Sequence

from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
from src.features.composer.diagram_check import portfolio_name_is_grounded
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    CollectedFragment,
    FlowRow,
)
from src.features.composer.portfolio_names import (
    MIN_REPRESENTATIVE_NAMES_FOR_CARD,
    RepresentativeName,
    portfolio_name_usage,
    representative_name_sources,
)
from src.shared.name_fragments.constants import NAME_VALUE_SEPARATOR


# ── 카드 글자 ────────────────────────────────────────────────────────
#: 한 카드에 나열할 이름의 최대 개수.
#:
#: ★ 왜 6인가 — 작가 안내문이 요구하는 나열 범위(「3~6개, 원문에 나온
#:   순서대로」)의 위쪽 끝과 같은 수다. 우리 카드가 작가 카드보다 더 많이
#:   나열하면 같은 표 안에서 규칙이 두 벌이 된다.
NAME_CARD_MAX_NAMES: Final[int] = 6

#: 「중점 추진 근거」 칸의 고정 문구.
#:
#: ★ 수·퍼센트·연도를 한 글자도 넣지 않는다 — 도식 수치 검사는 칸 안의 수가
#:   인용 원문에 없으면 그 줄을 통째로 버린다.
#: ★ 회사명·그룹명·상품명·업종 어휘도 넣지 않는다. 어느 회사에서 돌아도
#:   참인 «절차 서술»만 쓴다.
NAME_CARD_REASON: Final[str] = "공시 이름 표에 실린 이름"

#: 「사업적 역할」 칸. 이름 표는 역할을 말해 주지 않으므로 비운다.
NAME_CARD_ROLE: Final[str] = ""

#: 같은 종류의 이름을 잇는 글자.
NAME_CARD_NAME_SEPARATOR: Final[str] = "·"

#: 서로 다른 종류의 묶음을 가르는 글자.
NAME_CARD_GROUP_SEPARATOR: Final[str] = " / "

#: 실행 기록에 남기는 단계 이름 — 카드를 «덧붙인» 실행.
PORTFOLIO_NAME_CARD_STEP: Final[str] = "3장_대표이름_카드_보강"


# ── 못 붙인 이유 ─────────────────────────────────────────────────────
#: 이름 조각이 아예 없거나 하한에 못 미친다.
BLOCKED_TOO_FEW_NAMES: Final[str] = "too_few_names"
#: 작가 카드가 이미 이름을 썼다 — 덧붙일 이유가 없다.
BLOCKED_ALREADY_USED: Final[str] = "already_used"
#: 3장이 보고서에 없다(장 삭제는 없어야 하지만 방어).
BLOCKED_NO_SECTION: Final[str] = "no_portfolio_section"
#: 이름이 인용할 조각 원문에 글자 그대로 없다 — 접지 자체 검사 실패.
BLOCKED_NAME_NOT_IN_SOURCE: Final[str] = "name_not_in_source"


@dataclass(frozen=True)
class PortfolioNameCardResult:
    """결정적 이름 카드 보강 결과."""

    #: 보강 뒤 보고서. 안 붙였으면 입력과 «같은 객체»다.
    report: ComposedReport
    #: 카드에 실제로 실린 이름들. 안 붙였으면 빈 튜플.
    names: tuple[RepresentativeName, ...] = ()
    #: 못 붙였을 때의 이유 코드. 붙였으면 "".
    blocked_reason: str = ""

    @property
    def added(self) -> bool:
        return bool(self.names)

    @property
    def name_count(self) -> int:
        return len(self.names)

    @property
    def counts_by_label(self) -> dict[str, int]:
        """종류 라벨별 이름 수 — 실행 기록의 «종류별» 값."""

        counts: dict[str, int] = {}
        for source in self.names:
            counts[source.label] = counts.get(source.label, 0) + 1
        return counts


def _grouped_scope_text(names: Sequence[RepresentativeName]) -> str:
    """「제품·서비스 범위」 칸 글자 — 원문 순서로 잇되 종류가 섞이면 묶는다.

    ★ 종류가 하나면 라벨을 «안» 붙인다. 칸 이름이 이미 「제품·서비스 범위」라
      「범위: 제품: 가·나」처럼 콜론이 겹쳐 읽기 나쁘고, 한 종류뿐이면 그
      라벨이 알려 주는 것도 없다.
    ★ 종류가 섞이면 라벨을 «반드시» 붙인다. 섞어 늘어놓으면 독자가
      「이게 제품인가 IP인가」를 알 수 없고, 그 판단을 독자에게 떠넘기는 것이
      이 표가 하려던 일과 정반대다. 라벨은 정본 표에서 온 글자 그대로다.
    """

    ordered_labels: list[str] = []
    by_label: dict[str, list[str]] = {}
    for source in names:
        if source.label not in by_label:
            ordered_labels.append(source.label)
            by_label[source.label] = []
        by_label[source.label].append(source.name)
    if len(ordered_labels) == 1:
        return NAME_CARD_NAME_SEPARATOR.join(by_label[ordered_labels[0]])
    return NAME_CARD_GROUP_SEPARATOR.join(
        label
        + NAME_VALUE_SEPARATOR
        + NAME_CARD_NAME_SEPARATOR.join(by_label[label])
        for label in ordered_labels
    )


def _all_names_are_grounded(
    names: Sequence[RepresentativeName],
) -> bool:
    """실을 이름이 «자기 조각 원문»에 글자 그대로 있는지 스스로 검사한다.

    ★ 왜 다시 세나 — 이름은 파서가 조각의 ``원문위치``에 적어 준 값이고,
      그 파서는 이미 「이름이 원문에 있다」를 확인한다. 그래도 여기서 한 번
      더 본다: 우리 카드는 검수 AI를 지나지 않는 «결정적» 줄이라, 상류가
      언젠가 그 보장을 잃으면 아무도 못 잡는 자리가 되기 때문이다.
      잣대는 3장 카드 접지 검사와 «같은 함수»를 쓴다 — 두 벌이면 어긋난다.
    """

    return all(
        bool(source.name.strip())
        and portfolio_name_is_grounded(source.name, (source.text,))
        for source in names
    )


def augment_portfolio_name_card(
    report: ComposedReport,
    fragments: Sequence[CollectedFragment],
    *,
    allowed_fragment_ids: Optional[frozenset[str]] = None,
) -> PortfolioNameCardResult:
    """3장 카드가 대표 이름을 하나도 안 썼으면 이름 카드 하나를 덧붙인다.

    Args:
        report: 검증까지 끝난 본문. 아직 렌더·봉인 전이어야 한다.
        fragments: 3장 packet 조각(또는 flat union).
        allowed_fragment_ids: 3장이 인용해도 되는 조각 id. 주면 그 밖의
            조각은 후보에서 뺀다 — 장별 근거 소유권(evidence invariant)을
            깨는 인용을 «만들지 않기» 위해서다. ``None``이면 거르지 않는다
            (장별 packet 계약이 없는 legacy 경로).

    Returns:
        보강 결과. 안 붙였으면 ``report``는 입력과 같은 객체이고
        ``blocked_reason``에 이유 코드가 담긴다.
    """

    # 조각 id가 없는 이름은 «인용할 수 없는» 이름이다. 빈 인용을 실으면
    # FULL 봉인이 그 행을 못 읽어 보고서 전체가 막히고, 장별 근거 소유권
    # 검사도 빈 문자열을 소유 밖 인용으로 본다. 애초에 후보에서 뺀다.
    candidates = tuple(
        source
        for source in representative_name_sources(fragments)
        if source.fragment_id
    )
    if allowed_fragment_ids is not None:
        candidates = tuple(
            source
            for source in candidates
            if source.fragment_id in allowed_fragment_ids
        )
    usage = portfolio_name_usage(report, fragments)
    if usage.used_names:
        return PortfolioNameCardResult(
            report=report, blocked_reason=BLOCKED_ALREADY_USED
        )
    names = candidates[:NAME_CARD_MAX_NAMES]
    # 하한은 판정 모듈의 정본을 그대로 쓴다 — 「카드를 요구할 만큼 모였나」와
    # 「카드를 만들 만큼 모였나」가 다른 수면 한쪽만 움직여 조용히 어긋난다.
    if len(names) < MIN_REPRESENTATIVE_NAMES_FOR_CARD:
        return PortfolioNameCardResult(
            report=report, blocked_reason=BLOCKED_TOO_FEW_NAMES
        )
    if not _all_names_are_grounded(names):
        return PortfolioNameCardResult(
            report=report, blocked_reason=BLOCKED_NAME_NOT_IN_SOURCE
        )

    sections = report.sections
    index = next(
        (
            position
            for position, section in enumerate(sections)
            if section.section_id == PORTFOLIO_TABLE_SECTION_ID
        ),
        -1,
    )
    if index < 0:
        return PortfolioNameCardResult(
            report=report, blocked_reason=BLOCKED_NO_SECTION
        )

    row = FlowRow(
        cells=(
            names[0].name,
            _grouped_scope_text(names),
            NAME_CARD_REASON,
            NAME_CARD_ROLE,
        ),
        citations=tuple(dict.fromkeys(source.fragment_id for source in names)),
    )
    section: ComposedSection = sections[index]
    rebuilt = replace(section, flow_rows=(*section.flow_rows, row))
    return PortfolioNameCardResult(
        report=replace(
            report, sections=(*sections[:index], rebuilt, *sections[index + 1:])
        ),
        names=names,
    )


def portfolio_name_card_steps(output: object) -> list[dict[str, object]]:
    """이름 카드를 덧붙인 실행에만 단계 한 줄을 만든다.

    ★ 이 함수는 composer가 «정본»으로 소유한다. 실행 기록을 쌓는 쪽
      (`features/pipeline/real.py`)이 한 줄로 부르면 된다 — 단계 이름·필드를
      그쪽에서 손으로 다시 적으면 한쪽이 바뀔 때 조용히 어긋난다.

    Args:
        output: composer ``run_v2``의 결과. 이 필드를 모르는 옛 결과도 받는다.

    Returns:
        남길 단계가 없으면 빈 목록, 있으면 단계 dict 하나짜리 목록.
    """

    try:
        count = int(getattr(output, "portfolio_name_card_count", 0) or 0)
    except (TypeError, ValueError):
        return []
    if count <= 0:
        return []
    pairs = getattr(output, "portfolio_name_card_counts_by_label", ()) or ()
    try:
        by_label = {str(label): int(value) for label, value in pairs}
    except (TypeError, ValueError):
        by_label = {}
    return [
        {
            "step": PORTFOLIO_NAME_CARD_STEP,
            "이름수": count,
            "종류별": by_label,
        }
    ]


__all__ = [
    "BLOCKED_ALREADY_USED",
    "BLOCKED_NAME_NOT_IN_SOURCE",
    "BLOCKED_NO_SECTION",
    "BLOCKED_TOO_FEW_NAMES",
    "NAME_CARD_MAX_NAMES",
    "NAME_CARD_REASON",
    "NAME_CARD_ROLE",
    "PORTFOLIO_NAME_CARD_STEP",
    "PortfolioNameCardResult",
    "augment_portfolio_name_card",
    "portfolio_name_card_steps",
]

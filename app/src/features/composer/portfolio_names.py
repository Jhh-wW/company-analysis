"""3장 카드가 packet의 «회사를 대표하는 이름»을 실제로 썼는지 본다.

★ 왜 필요한가 (2026-09-06 실측 재현) — 3장 packet에 이름 조각이 열 개 넘게
  실렸는데도 운영 보고서 카드에는 그 이름이 하나도 없었다. 조각은 갔는데
  작가가 안 쓴 것인지, 애초에 조각이 안 갔는지를 «실행 기록»으로는 가를 수
  없었다. 이 모듈은 그 두 경우를 가르는 표식을 만든다.

★ 업종·회사로 갈리는 분기를 두지 않는다. 판정은 오직 조각의 «종류 라벨»만
  본다 — 엔터·제조·금융 어디서 온 이름이든 같은 규칙을 지난다.

★ 이 모듈은 «판정»과 «이름 뽑기»만 한다. 카드를 만들거나 고치지 않는다 —
  근거 없이 카드를 지어내는 것이 이 보고서에서 가장 하면 안 되는 일이다.
  조각의 글자만 투영하는 결정적 표는 `portfolio_name_table.py`가 만들고,
  그쪽도 여기서 뽑은 이름·조각 id·원문 밖으로는 한 글자도 쓰지 않는다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final, Iterable, Sequence

from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
from src.features.composer.port import CollectedFragment, ComposedReport
from src.shared.name_fragments.constants import (
    REPRESENTATIVE_NAME_LABELS,
    parse_name_location,
)


# 종류 라벨·표기 구분자·«대표 이름» 종류의 정본은 `shared/name_fragments.py`다.
# 생산자(이름 표 파서)와 소비자(여기)가 같은 객체를 읽어야 한 쪽이 바뀔 때
# 조용히 어긋나지 않는다 — 어긋나면 이 판정이 아무것도 못 찾고도 초록불이 된다.

#: 이 개수 이상 대표 이름이 왔는데 카드에 하나도 없으면 «작가가 안 쓴 것»으로 본다.
#:
#: ★ 왜 2인가 — 이름이 하나뿐이면 그 하나가 회사를 대표한다고 단정하기 어렵다.
#:   둘부터는 「이름 표」라고 부를 만하고, 카드 하나를 그 이름들로 채우라는
#:   안내문 요구와 같은 하한이다.
MIN_REPRESENTATIVE_NAMES_FOR_CARD: Final[int] = 2

#: 실행 기록에 남기는 단계 이름.
UNUSED_REPRESENTATIVE_NAMES_STEP: Final[str] = "3장_대표이름_미사용"


@dataclass(frozen=True)
class PortfolioNameUsage:
    """3장 packet의 대표 이름과 카드 사용 여부."""

    #: (종류 라벨, 이름) 쌍 — 조각 순서, 이름 중복 제거.
    labelled_names: tuple[tuple[str, str], ...]
    #: 카드 어느 칸에서든 실제로 쓰인 이름.
    used_names: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for _label, name in self.labelled_names)

    @property
    def name_count(self) -> int:
        return len(self.labelled_names)

    @property
    def counts_by_label(self) -> dict[str, int]:
        """종류 라벨별 이름 수 — 실행 기록의 «종류별» 값."""

        counts: dict[str, int] = {}
        for label, _name in self.labelled_names:
            counts[label] = counts.get(label, 0) + 1
        return counts

    @property
    def should_have_used(self) -> bool:
        """안내문이 카드 하나를 요구할 만큼 이름이 모였는가."""

        return self.name_count >= MIN_REPRESENTATIVE_NAMES_FOR_CARD

    @property
    def unused(self) -> bool:
        """요구할 만큼 모였는데 카드가 하나도 안 썼는가."""

        return self.should_have_used and not self.used_names


def _normalized(text: str) -> str:
    """호환문자·대소문자·공백 차이를 지운 비교용 문자열."""

    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(character for character in folded if not character.isspace())


@dataclass(frozen=True)
class RepresentativeName:
    """대표 이름 하나와 그 이름이 «어느 조각에서 왔는지»."""

    #: 종류 라벨 — 정본 표(`shared/name_fragments`)의 글자 그대로.
    label: str
    #: 파서가 조각 원문위치에 적어 둔 이름.
    name: str
    #: 그 이름이 실린 조각 id. 카드가 인용할 번호다.
    fragment_id: str
    #: 그 조각의 원문(표 한 행). 접지 검사가 대조할 글자다.
    text: str


def representative_name_sources(
    fragments: Iterable[CollectedFragment],
) -> tuple[RepresentativeName, ...]:
    """3장 packet 조각에서 대표 이름을 «출처 조각과 함께» 뽑는다.

    이름은 조각 원문에서 «추측»하지 않는다 — 원문은 표 한 행이라 어느 칸이
    이름인지 알 수 없다. 파서가 원문위치에 적어 둔 값을 그대로 읽는다.

    ★ 왜 조각 id·원문까지 함께 내나 — 이 이름을 카드에 실으려면 그 이름이
      나온 조각을 «인용»해야 하고, 그 조각 원문에 이름이 글자 그대로 있는지
      다시 대조해야 한다. 뽑기와 되찾기를 두 벌로 구현하면 둘이 어긋난 채
      「인용은 A 조각인데 이름은 B 조각에서 온」 줄이 만들어진다.
    """

    found: list[RepresentativeName] = []
    seen: set[str] = set()
    for fragment in fragments:
        parsed = parse_name_location(getattr(fragment, "location", ""))
        if parsed is None:
            continue
        label, name = parsed
        # 사업부문·종속회사·주요 계약은 이 요구의 «충족 근거»가 아니다.
        key = _normalized(name)
        if label not in REPRESENTATIVE_NAME_LABELS or not key or key in seen:
            continue
        seen.add(key)
        found.append(
            RepresentativeName(
                label=label,
                name=name,
                fragment_id=str(getattr(fragment, "fragment_id", "")).strip(),
                text=str(getattr(fragment, "text", "")),
            )
        )
    return tuple(found)


def representative_names(
    fragments: Iterable[CollectedFragment],
) -> tuple[tuple[str, str], ...]:
    """3장 packet 조각에서 대표 이름을 (라벨, 이름) 쌍으로 뽑는다."""

    return tuple(
        (source.label, source.name)
        for source in representative_name_sources(fragments)
    )


def portfolio_name_usage(
    report: ComposedReport,
    fragments: Sequence[CollectedFragment],
) -> PortfolioNameUsage:
    """3장 카드가 packet의 대표 이름을 썼는지 판정한다.

    Args:
        report: 검증까지 끝난 본문. 3장이 없으면 쓰인 이름은 0개다.
        fragments: 3장 packet 조각. flat union을 줘도 대표 이름 표기를 가진
            조각만 세므로 결과가 같다.

    Returns:
        packet의 (라벨, 이름) 목록과 카드가 실제로 쓴 이름 목록.
    """

    labelled = representative_names(fragments)
    if not labelled:
        return PortfolioNameUsage(labelled_names=(), used_names=())

    # ★ 칸을 이어 붙이지 않는다 — 이어 붙이면 두 칸 경계에 우연히 걸친
    #   글자가 「이름을 썼다」로 잡힌다(3장 이름 접지 검사가 막는 것과 같은 함정).
    cells = tuple(
        _normalized(cell)
        for section in report.sections
        if section.section_id == PORTFOLIO_TABLE_SECTION_ID
        for row in section.flow_rows
        for cell in row.cells
    )
    used = tuple(
        name
        for _label, name in labelled
        if any(_normalized(name) in cell for cell in cells)
    )
    return PortfolioNameUsage(labelled_names=labelled, used_names=used)


__all__ = [
    "MIN_REPRESENTATIVE_NAMES_FOR_CARD",
    "PortfolioNameUsage",
    "RepresentativeName",
    "UNUSED_REPRESENTATIVE_NAMES_STEP",
    "portfolio_name_usage",
    "representative_name_sources",
    "representative_names",
]

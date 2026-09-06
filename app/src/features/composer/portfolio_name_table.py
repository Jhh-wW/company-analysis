"""3장에 「회사가 공시한 대표 이름」 표 하나를 «결정적으로» 덧붙인다.

★ 왜 카드가 아니라 표인가 (2026-09-06 운영 실측) — 앞선 설계는 이름 표의
  «첫 이름»을 카드 제목 칸에 올렸다. 그 결과 상장 엔터사 보고서 3장에 제목이
  「<그룹 하나>」이고 범위가 「<그룹 하나>·<그룹 둘>·…」인 카드가 나왔다.
  3장 카드의 제목 칸은 «종류»를 적는 자리인데 거기에 종류의 «보기» 하나가
  올라간 것이다. 제조사에서 같은 코드는 제목이 「TV」인 카드를 만든다.
  카드 제목을 첫 이름으로 둔 이유는 오직 하나 — 3장 카드 접지 검사가 제목
  칸이 인용 원문의 부분문자열이 아니면 그 칸을 비우기 때문이었다. 즉 규칙을
  고친 것이 아니라 규칙을 피한 «우회»였고, 화면에는 그 대가가 그대로 남았다.

★ 이 모듈이 만드는 것은 카드가 아니라 «표»다. 종류 라벨(대표 IP·제품·
  브랜드)이 「구분」 열에 오고 이름들이 「이름」 열에 온다. 표는 작가 카드가
  아니므로 카드 접지 검사를 지나지 않는다. 대신 «자체 검사»를 통과해야만
  만들어진다(아래 `_verified_names`) — 면제가 아니라 자기 규칙이다.

★ 작가가 이름을 썼든 안 썼든 «항상» 만든다. 「작가가 안 썼을 때만」이라는
  조건은 같은 회사를 두 번 돌렸을 때 화면이 달라지게 만든다. 예측 가능성이
  중복 회피보다 중요하다 — 중복은 읽는 사람이 넘기면 되지만, 어떤 실행에서만
  이름이 사라지는 화면은 아무도 못 고친다.

★ 업종·회사로 갈리는 분기를 두지 않는다. 조각의 «종류 라벨»만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional, Sequence

from src.features.composer.diagram_check import portfolio_name_is_grounded
from src.features.composer.port import CollectedFragment
from src.features.composer.portfolio_names import (
    MIN_REPRESENTATIVE_NAMES_FOR_CARD,
    RepresentativeName,
    representative_name_sources,
)
from src.shared.name_fragments.constants import (
    NAME_KIND_LABELS,
    NAME_LABEL_SEPARATOR,
    REPRESENTATIVE_NAME_KINDS,
    parse_name_location,
)


# ── 표의 글자 ────────────────────────────────────────────────────────
#: 표 캡션 앞머리. 뒤에 「 (N)」이 붙는다(N = 표에 실제로 실린 이름 수).
#:
#: ★ 회사명·업종 어휘를 넣지 않는다. 어느 회사에서 돌아도 참인 말만 쓴다.
#: ★ 「공시한」이라고 못 박는 이유 — 이 표의 이름은 우리가 고른 것이 아니라
#:   회사가 공시 원문의 표에 적어 둔 것이다. 독자가 그 차이를 알아야 한다.
NAME_TABLE_CAPTION_PREFIX: Final[str] = "회사가 공시한 대표 이름"

#: 「구분」 열 — 종류 라벨이 오는 자리.
NAME_TABLE_KIND_HEADER: Final[str] = "구분"
#: 「이름」 열 — 그 종류의 이름들이 오는 자리.
NAME_TABLE_NAME_HEADER: Final[str] = "이름"
NAME_TABLE_HEADERS: Final[tuple[str, str]] = (
    NAME_TABLE_KIND_HEADER,
    NAME_TABLE_NAME_HEADER,
)

#: 행 순서 = 종류 순서. 정본 표에서 «만든다» — 손으로 적으면 어긋난다.
#:
#: ★ 이 순서는 이름 표 파서의 조각 배분 순서(`features/product_names`의
#:   ``NAME_FRAGMENT_KIND_ORDER``)에서 «대표 이름 종류만» 남긴 것과 같다.
#:   feature 간 직접 import는 이 저장소의 경계 규칙이 막으므로 공용 정본
#:   (`shared/name_fragments`)의 순서를 쓰고, 두 순서가 어긋나지 않는지는
#:   시험(`test_portfolio_name_table.py`)이 두 상수를 함께 읽어 지킨다.
NAME_TABLE_ROW_LABELS: Final[tuple[str, ...]] = tuple(
    NAME_KIND_LABELS[kind] for kind in REPRESENTATIVE_NAME_KINDS
)

#: 한 종류(=한 행)에 나열할 이름의 최대 개수.
#:
#: ★ 왜 8인가 — 한 칸이 한 줄에 들어가야 표가 읽힌다. 실측 공시에서 한
#:   종류의 이름은 열 건 넘게 나오는 일이 흔한데, 전부 이으면 칸 하나가
#:   화면 폭을 넘겨 표가 가로로 밀린다. 8개를 「·」로 이으면 국문 고유명사
#:   기준 대략 한 줄에 담긴다. 넘치는 만큼은 「외 N」으로 «수만» 남긴다 —
#:   자르고 아무 말도 안 하면 독자는 그게 전부인 줄 안다.
NAME_TABLE_MAX_NAMES_PER_KIND: Final[int] = 8

#: 같은 행 안에서 이름을 잇는 글자.
NAME_TABLE_NAME_SEPARATOR: Final[str] = "·"

#: 상한을 넘겨 못 실은 이름의 수를 남기는 말.
NAME_TABLE_OVERFLOW_TEMPLATE: Final[str] = "외 {count}"

#: 캡션 뒤에 이름 수를 적는 모양.
#:
#: ★ 왜 「(3)」이 아니라 「(3개)」인가 (PDF 실측) — 표 캡션 뒤에는 렌더러가
#:   인용 번호를 「(3)」 모양으로 붙인다. 개수를 「(3)」으로 적으면 캡션이
#:   「… 대표 이름 (3) (3)」이 되어 둘 중 무엇이 근거 번호인지 알 수 없다.
#:   단위 한 글자가 그 혼동을 없앤다.
NAME_TABLE_CAPTION_TEMPLATE: Final[str] = "{prefix} ({count}개)"


# ── 실행 기록 ────────────────────────────────────────────────────────
#: 표를 만든 실행에 남기는 단계 이름.
PORTFOLIO_NAME_TABLE_STEP: Final[str] = "3장_대표이름_표"
#: 이름은 왔는데 표를 못 만든 실행에 남기는 단계 이름.
PORTFOLIO_NAME_TABLE_BLOCKED_STEP: Final[str] = "3장_대표이름_표_불가"


# ── 못 만든 이유 ─────────────────────────────────────────────────────
#: 대표 이름이 하한(2개)에 못 미친다. 표라고 부를 것이 없다.
BLOCKED_TOO_FEW_NAMES: Final[str] = "too_few_names"
#: 인용할 조각을 조각 목록에서 되찾지 못했다 — 인용 번호가 헛번호가 된다.
BLOCKED_CITE_NOT_RESOLVABLE: Final[str] = "cite_not_resolvable"
#: 이름이 «되찾은» 조각 원문에 글자 그대로 없다. 접지 자체 검사 실패.
BLOCKED_NAME_NOT_IN_SOURCE: Final[str] = "name_not_in_source"
#: 「구분」에 쓸 라벨이 «되찾은» 조각의 원문위치에서 읽히지 않는다.
BLOCKED_LABEL_NOT_IN_LOCATION: Final[str] = "label_not_in_location"


@dataclass(frozen=True)
class PortfolioNameTable:
    """3장에 실을 결정적 이름 표 하나.

    ★ 여기에는 «렌더러가 그릴 글자»와 «그 글자가 어느 조각에서 왔는지»만
      담는다. 표 객체(`ReportTable`)를 직접 만들지 않는 이유 — 같은 표를
      렌더러(`render.py`)와 공개 봉인(`public_manifest.py`)이 각각 자기
      모양으로 만들어야 하는데, 한쪽 타입으로 굳혀 두면 다른 쪽이 그 값을
      다시 뜯어 옮기다 조용히 어긋난다.
    """

    #: 표 캡션. 「회사가 공시한 대표 이름 (N)」.
    caption: str
    #: 열 이름 — 「구분」「이름」.
    headers: tuple[str, ...]
    #: 행. 각 행은 (종류 라벨, 이름들).
    rows: tuple[tuple[str, ...], ...]
    #: 행별 인용 조각 id. 「외 N」에 셈된 이름의 조각도 «함께» 싣는다 —
    #: 그 수 역시 근거가 있어야 독자가 확인할 수 있다.
    row_fragment_ids: tuple[tuple[str, ...], ...]
    #: 표에 실제로 실린 이름 수(「외 N」에 접힌 이름은 빼고 센다).
    name_count: int
    #: 종류 라벨별 실린 이름 수 — 실행 기록의 「종류별」 값.
    counts_by_label: tuple[tuple[str, int], ...]
    #: 이름이 나온 공시 표의 제목들 — 실행 기록의 「표제목」 값.
    table_titles: tuple[str, ...]

    @property
    def fragment_ids(self) -> tuple[str, ...]:
        """표 전체가 인용하는 조각 id — 행 순서, 중복 제거."""

        return tuple(
            dict.fromkeys(
                fragment_id
                for row_ids in self.row_fragment_ids
                for fragment_id in row_ids
            )
        )


@dataclass(frozen=True)
class PortfolioNameTableResult:
    """이름 표 생성 결과. 못 만들었으면 ``table``이 ``None``이다."""

    #: 만들어진 표. 못 만들었으면 ``None``.
    table: Optional[PortfolioNameTable] = None
    #: 못 만들었을 때의 이유 코드. 만들었거나 애초에 이름이 없으면 "".
    blocked_reason: str = ""
    #: 검사 전 후보 이름 수. 0이면 이 회사에는 이름 조각이 애초에 없었다.
    candidate_count: int = 0

    @property
    def added(self) -> bool:
        return self.table is not None


def _table_title(location: str) -> str:
    """조각 원문위치에서 «공시 표 제목»만 떼어 낸다.

    원문위치는 `"{표 제목} · {N}행 · {라벨}: {이름}"` 모양이라 첫 마디가 표
    제목이다. 표 제목 자체에 같은 구분자가 들어 있으면 앞부분만 남는데, 이
    값은 화면에 나가지 않고 «실행 기록(진단)»에만 쓰이므로 그 손실을 감수한다.
    """

    return str(location or "").split(NAME_LABEL_SEPARATOR, 1)[0].strip()


def _verified_names(
    candidates: Sequence[RepresentativeName],
    by_id: dict[str, CollectedFragment],
    ambiguous_ids: frozenset[str] = frozenset(),
) -> tuple[tuple[RepresentativeName, ...], str]:
    """표에 실을 이름을 «인용할 조각으로 되찾아» 세 가지를 확인한다.

    (a) 이름이 그 조각 «원문»의 부분문자열인가 (정규화 비교),
    (b) 「구분」에 쓸 라벨이 그 조각 «원문위치»에서 읽히는 라벨과 같은가,
    (c) 인용할 조각 id가 실제 조각 목록에서 «하나로» 되찾아지는가.

    ★ 왜 «되찾아» 보나 — 이름·라벨·원문은 모두 상류 파서가 한 조각에서
      함께 만든 값이라, 그 값들끼리만 비교하면 언제나 참인 «순환 검사»가
      된다. 그래서 이름이 들고 있는 조각 id로 조각 목록을 다시 뒤져, 그
      조각이 정말 그 이름·그 라벨을 담고 있는지 본다. 상류가 언젠가 id와
      내용을 어긋나게 만들면 여기서 걸린다.

    ★ ``ambiguous_ids``는 같은 id가 두 조각에 붙은 경우다. 그때 인용 번호
      하나가 어느 조각을 가리키는지 정할 수 없다 — 부록이 고른 조각과 우리가
      본 조각이 다를 수 있으므로, 그 번호는 «틀린 근거»가 된다. 표를 안 만든다.

    Returns:
        (검사를 통과한 이름들, 실패 사유 코드). 통과하면 사유는 "".
    """

    verified: list[RepresentativeName] = []
    for source in candidates:
        if not source.name.strip():
            return (), BLOCKED_NAME_NOT_IN_SOURCE
        fragment = by_id.get(source.fragment_id)
        if fragment is None or source.fragment_id in ambiguous_ids:
            return (), BLOCKED_CITE_NOT_RESOLVABLE
        if not portfolio_name_is_grounded(
            source.name, (str(getattr(fragment, "text", "") or ""),)
        ):
            return (), BLOCKED_NAME_NOT_IN_SOURCE
        parsed = parse_name_location(str(getattr(fragment, "location", "") or ""))
        if parsed is None or parsed[0] != source.label:
            return (), BLOCKED_LABEL_NOT_IN_LOCATION
        verified.append(source)
    return tuple(verified), ""


def build_portfolio_name_table(
    fragments: Sequence[CollectedFragment],
    *,
    allowed_fragment_ids: Optional[frozenset[str]] = None,
) -> PortfolioNameTableResult:
    """3장 packet의 대표 이름으로 결정적 표 하나를 만든다.

    Args:
        fragments: 3장 packet 조각(또는 flat union).
        allowed_fragment_ids: 3장이 인용해도 되는 조각 id. 주면 그 밖의
            조각은 후보에서 뺀다 — 장별 근거 소유권(evidence invariant)을
            깨는 인용을 «만들지 않기» 위해서다. ``None``이면 거르지 않는다
            (장별 packet 계약이 없는 legacy 경로).

    Returns:
        표를 못 만들면 ``table``이 ``None``이고 ``blocked_reason``에 사유가
        담긴다. 이름 조각이 애초에 하나도 없으면 사유도 비운다 — 「자료가
        없다」는 실패가 아니라 대부분의 회사에서 흔한 정상 상태다.
    """

    by_id: dict[str, CollectedFragment] = {}
    ambiguous_ids: set[str] = set()
    for fragment in fragments:
        fragment_id = str(getattr(fragment, "fragment_id", "")).strip()
        if not fragment_id:
            continue
        if fragment_id in by_id:
            # 같은 번호를 두 조각이 쓰면 그 인용은 어느 원문을 가리키는지
            # 정할 수 없다. 「틀린 근거」를 인쇄하느니 표를 안 만든다.
            ambiguous_ids.add(fragment_id)
            continue
        by_id[fragment_id] = fragment

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
    if not candidates:
        return PortfolioNameTableResult()
    # 하한은 판정 모듈의 정본을 그대로 쓴다 — 「이름 표라고 부를 만한가」가
    # 두 벌이면 한쪽만 움직여 조용히 어긋난다.
    if len(candidates) < MIN_REPRESENTATIVE_NAMES_FOR_CARD:
        return PortfolioNameTableResult(
            blocked_reason=BLOCKED_TOO_FEW_NAMES,
            candidate_count=len(candidates),
        )

    verified, reason = _verified_names(
        candidates, by_id, frozenset(ambiguous_ids)
    )
    if reason:
        return PortfolioNameTableResult(
            blocked_reason=reason, candidate_count=len(candidates)
        )

    grouped: dict[str, list[RepresentativeName]] = {}
    for source in verified:
        grouped.setdefault(source.label, []).append(source)

    rows: list[tuple[str, ...]] = []
    row_fragment_ids: list[tuple[str, ...]] = []
    counts_by_label: list[tuple[str, int]] = []
    titles: list[str] = []
    name_count = 0
    for label in NAME_TABLE_ROW_LABELS:
        group = grouped.get(label)
        if not group:
            continue
        shown = group[:NAME_TABLE_MAX_NAMES_PER_KIND]
        hidden = len(group) - len(shown)
        names_text = NAME_TABLE_NAME_SEPARATOR.join(source.name for source in shown)
        if hidden:
            names_text = (
                f"{names_text} "
                f"{NAME_TABLE_OVERFLOW_TEMPLATE.format(count=hidden)}"
            )
        rows.append((label, names_text))
        # 「외 N」의 N도 근거가 있어야 한다 — 접힌 이름의 조각까지 인용한다.
        row_fragment_ids.append(
            tuple(dict.fromkeys(source.fragment_id for source in group))
        )
        counts_by_label.append((label, len(shown)))
        name_count += len(shown)
        for source in group:
            fragment = by_id.get(source.fragment_id)
            title = _table_title(
                str(getattr(fragment, "location", "") or "")
                if fragment is not None
                else ""
            )
            if title and title not in titles:
                titles.append(title)

    if not rows:
        # 검사를 통과한 이름이 모두 «대표 이름이 아닌» 라벨이면 여기 온다.
        # 상류 계약상 일어나지 않지만, 빈 표를 만드는 것보다 안 만드는 게 낫다.
        return PortfolioNameTableResult(
            blocked_reason=BLOCKED_TOO_FEW_NAMES,
            candidate_count=len(candidates),
        )

    return PortfolioNameTableResult(
        table=PortfolioNameTable(
            caption=NAME_TABLE_CAPTION_TEMPLATE.format(
                prefix=NAME_TABLE_CAPTION_PREFIX, count=name_count
            ),
            headers=NAME_TABLE_HEADERS,
            rows=tuple(rows),
            row_fragment_ids=tuple(row_fragment_ids),
            name_count=name_count,
            counts_by_label=tuple(counts_by_label),
            table_titles=tuple(titles),
        ),
        candidate_count=len(candidates),
    )


def portfolio_name_table_steps(output: object) -> list[dict[str, object]]:
    """이름 표에 관한 실행 기록 한 줄을 만든다.

    ★ 이 함수는 composer가 «정본»으로 소유한다. 실행 기록을 쌓는 쪽
      (`features/pipeline/real.py`)이 한 줄로 부르면 된다 — 단계 이름·필드를
      그쪽에서 손으로 다시 적으면 한쪽이 바뀔 때 조용히 어긋난다.

    Args:
        output: composer ``run_v2``의 결과. 이 필드를 모르는 옛 결과도 받는다.

    Returns:
        남길 단계가 없으면 빈 목록, 있으면 단계 dict 하나짜리 목록.
    """

    try:
        count = int(getattr(output, "portfolio_name_table_name_count", 0) or 0)
    except (TypeError, ValueError):
        return []
    if count > 0:
        pairs = getattr(output, "portfolio_name_table_counts_by_label", ()) or ()
        try:
            by_label = {str(label): int(value) for label, value in pairs}
        except (TypeError, ValueError):
            by_label = {}
        titles = [
            str(value)
            for value in (getattr(output, "portfolio_name_table_titles", ()) or ())
        ]
        return [
            {
                "step": PORTFOLIO_NAME_TABLE_STEP,
                "이름수": count,
                "종류별": by_label,
                "표제목": titles,
            }
        ]
    reason = str(
        getattr(output, "portfolio_name_table_blocked_reason", "") or ""
    ).strip()
    if not reason:
        return []
    return [{"step": PORTFOLIO_NAME_TABLE_BLOCKED_STEP, "사유": reason}]


__all__ = [
    "BLOCKED_CITE_NOT_RESOLVABLE",
    "BLOCKED_LABEL_NOT_IN_LOCATION",
    "BLOCKED_NAME_NOT_IN_SOURCE",
    "BLOCKED_TOO_FEW_NAMES",
    "NAME_TABLE_CAPTION_PREFIX",
    "NAME_TABLE_HEADERS",
    "NAME_TABLE_KIND_HEADER",
    "NAME_TABLE_MAX_NAMES_PER_KIND",
    "NAME_TABLE_NAME_HEADER",
    "NAME_TABLE_NAME_SEPARATOR",
    "NAME_TABLE_OVERFLOW_TEMPLATE",
    "NAME_TABLE_ROW_LABELS",
    "PORTFOLIO_NAME_TABLE_BLOCKED_STEP",
    "PORTFOLIO_NAME_TABLE_STEP",
    "PortfolioNameTable",
    "PortfolioNameTableResult",
    "build_portfolio_name_table",
    "portfolio_name_table_steps",
]

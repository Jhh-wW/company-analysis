"""뉴스 보조 조각을 장 끝의 결정적 표 「최근 보도 (보조)」로 올린다.

★ 왜 필요한가 (2026-09-06 운영 실측) — 뉴스 조각 6개가 작가 프롬프트까지
  갔는데 작가가 한 문장도 인용하지 않아, 보고서에도 부록에도 흔적이 0건이었다.
  조각을 «주는 일»과 조각이 «보고서에 실리는 일»은 다른 사건이다. 작가의
  인용에 맡기면 실릴지 안 실릴지가 실행마다 갈린다. 그래서 조각을 받은 장의
  끝에 표 하나를 «결정적으로» 붙인다 — 작가가 이미 본문에 인용했더라도
  붙인다(예측 가능성 우선. 「어떤 실행에서는 있고 어떤 실행에서는 없다」가
  이 기능에서 가장 나쁜 결과다).

★ 이 모듈은 아무것도 «지어내지» 않는다. 조각의 글자와 메타만 투영한다:
  · 「발행일」 = 조각이 운반한 기사 날짜 그대로.
  · 「매체」   = 조각이 운반한 발행처 그대로.
  · 「보도 문장」 = 조각 원문 그대로. 줄이거나 바꾸거나 따옴표를 떼지 않는다
    (5·6장에는 인용문 조각만 오므로 따옴표까지 원문 그대로 실린다).
  요약·해석·이어붙이기를 하지 않으므로 검수 AI를 다시 부를 이유가 없다.

★ 스스로 네 가지를 다시 검사하고, 하나라도 어긋나면 «그 행만» 뺀다
  (`_row_problem` 참고). 결정적으로 만든 줄은 검수 AI를 지나지 않으므로,
  상류가 언젠가 보장을 잃으면 여기 말고는 잡을 자리가 없다.

★ 9장은 대상이 아니다. 수집하는 쪽이 이미 9장을 빼지만, 그 규칙이 바뀌면 이
  표가 조용히 9장에 붙는다. 그래서 shared의 «정본 목록»
  (`report_evidence.constants.NEWS_EXCLUDED_SECTION_IDS`)을 여기서도 읽어 다시
  막는다 — 목록을 베껴 적으면 한쪽만 바뀔 때 어긋난다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Final, Mapping, Optional, Sequence

from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    CollectedFragment,
    NewsRow,
)

# ★ 「9장에는 뉴스를 싣지 않는다」는 판단의 정본은 shared에 있다. 수집하는
#   쪽(news_intake)과 싣는 쪽(여기)이 같은 객체를 보므로, 한쪽만 바뀌어
#   조용히 어긋나는 일이 없다. feature끼리 직접 import하지 않는다.
from src.shared.report_evidence.constants import (
    NEWS_EXCLUDED_SECTION_IDS,
    SOURCE_KIND_NEWS,
)


# ── 표 모양 ──────────────────────────────────────────────────────────
#: 한 장에 실을 보도 행의 최대 개수.
#:
#: ★ 왜 3인가 — 이 표는 보조 자료이지 본문이 아니다. 장 끝에 붙는 표가
#:   본문보다 길어지면 독자가 회사 공식 자료와 언론 보도를 같은 무게로 읽게
#:   된다. 기사 하나가 내는 조각이 최대 2개(`news_intake`의 기사당 상한)라,
#:   3행이면 서로 다른 기사 두 건 이상이 반드시 섞인다 — 한 기사만 길게
#:   옮겨 적는 모양은 나오지 않는다.
NEWS_BLOCK_MAX_ROWS: Final[int] = 3

#: 표 머리글. 「보도 문장」이 마지막이라 긴 글이 오른쪽으로 흐른다.
NEWS_BLOCK_HEADERS: Final[tuple[str, ...]] = ("발행일", "매체", "보도 문장")

#: 표 캡션 틀. 「(보조, N)」의 N은 실제로 실린 행 수다 — 몇 건을 보고 있는지
#: 독자가 표를 세지 않아도 알게 한다.
NEWS_BLOCK_CAPTION_TEMPLATE: Final[str] = "최근 보도 (보조, {count})"

#: 공개 표현. 도식이 아니라 «그냥 표»다 — 세 칸이 흐름으로 이어지지 않으므로
#: 화살표·카드로 그리면 뜻이 없는 그림이 된다(`report_standard.visualization`은
#: 이 값이 "table"이면 도식을 만들지 않고 일반 표로 그린다).
NEWS_BLOCK_PRESENTATION: Final[str] = "table"


# ── 실행 기록 단계 이름 ──────────────────────────────────────────────
#: 표를 실제로 붙인 실행.
NEWS_BLOCK_STEP: Final[str] = "뉴스_보도표"
#: 조각은 왔는데 표를 못 붙인 실행.
NEWS_BLOCK_BLOCKED_STEP: Final[str] = "뉴스_보도표_불가"


# ── 행을 뺀 이유 ─────────────────────────────────────────────────────
#: 조각의 source_kind가 news가 아니다.
BLOCKED_NOT_NEWS_KIND: Final[str] = "not_news_kind"
#: 그 장의 packet 소유가 아닌 조각이다 — 인용하면 장별 근거 소유권이 깨진다.
BLOCKED_NOT_SECTION_OWNED: Final[str] = "not_section_owned"
#: 표에 실을 글자가 조각 원문과 다르다.
BLOCKED_TEXT_MISMATCH: Final[str] = "text_mismatch"
#: 인용할 조각 id가 없다 — 빈 인용은 봉인이 읽지 못한다.
BLOCKED_CITATION_MISSING: Final[str] = "citation_missing"
#: 발행일 또는 매체가 비었다 — 세 칸 중 두 칸을 비운 표는 싣지 않는다.
BLOCKED_MISSING_META: Final[str] = "missing_publisher_or_published_on"
#: 장별 소유권을 확인할 packet이 없다 — 어느 장 조각인지 모르면 안 싣는다.
BLOCKED_NO_SECTION_OWNERSHIP: Final[str] = "no_section_ownership"
#: 뉴스를 싣지 않는 장(9장)에 조각이 왔다.
BLOCKED_EXCLUDED_SECTION: Final[str] = "excluded_section"
#: 장당 상한을 넘겨 뺀 행.
BLOCKED_ROW_LIMIT: Final[str] = "row_limit"
#: 이 실행 모드는 공개 구조를 결속하지 못해, 표를 붙이면 보고서 전체가 막힌다.
BLOCKED_UNBINDABLE_MODE: Final[str] = "release_mode_cannot_bind_structures"


def news_block_caption(row_count: int) -> str:
    """표 캡션 — 캡션 글자를 두 곳에서 따로 만들지 않는다."""

    return NEWS_BLOCK_CAPTION_TEMPLATE.format(count=int(row_count))


def _normalized(text: str) -> str:
    """공백 차이만 지우고 글자는 그대로 두는 대조용 정규화."""

    return " ".join(str(text or "").split())


def _is_news_fragment(fragment: CollectedFragment) -> bool:
    """조각이 언론 보조 근거인가.

    typed packet은 ``formal_source_kind``에 닫힌 종류를 봉인하고, packet 계약이
    없는 옛 경로는 transport 지문 ``kind``에만 남는다. 둘 중 어느 쪽이든
    «news»라고 «선언»한 조각만 받는다 — 종류를 추측하지 않는다.
    """

    declared = {
        str(getattr(fragment, "formal_source_kind", "") or "").strip(),
        str(getattr(fragment, "kind", "") or "").strip(),
    }
    return SOURCE_KIND_NEWS in declared


def _row_problem(
    row: NewsRow,
    fragment: CollectedFragment,
    *,
    allowed_fragment_ids: Optional[frozenset[str]],
) -> str:
    """만든 행을 스스로 다시 검사한다. 문제없으면 "".

    ★ 왜 만들자마자 다시 세나 — 이 행은 검수 AI를 지나지 않는 «결정적» 줄이다.
      위쪽에서 한 번 걸렀으니 여기서는 통과할 수밖에 없어 보이지만, 그 «위쪽»이
      나중에 바뀌면 아무도 못 잡는 자리가 된다. 표를 짓는 코드와 표를 재는
      코드를 갈라 놓으면, 짓는 쪽이 언젠가 글자를 줄이거나 이어 붙일 때 이
      검사가 그 행을 뺀다.
    """

    fragment_id = str(fragment.fragment_id).strip()
    if not _is_news_fragment(fragment):
        return BLOCKED_NOT_NEWS_KIND
    if not fragment_id or tuple(row.citations) != (fragment_id,):
        return BLOCKED_CITATION_MISSING
    if allowed_fragment_ids is not None and fragment_id not in allowed_fragment_ids:
        return BLOCKED_NOT_SECTION_OWNED
    if len(row.cells) != len(NEWS_BLOCK_HEADERS):
        # 칸 수가 머리글과 다르면 표가 깨진다. 예외로 보고서 전체를 막지 않고
        # 그 행만 뺀다 — 이 표는 «보조»라서 없어도 보고서는 성립한다.
        return BLOCKED_TEXT_MISMATCH
    published_on, publisher, sentence = row.cells
    if not published_on.strip() or not publisher.strip():
        return BLOCKED_MISSING_META
    if not sentence.strip() or _normalized(sentence) != _normalized(fragment.text):
        return BLOCKED_TEXT_MISMATCH
    return ""


def _news_row(fragment: CollectedFragment) -> NewsRow:
    """조각 하나를 표 한 행으로 «옮기기만» 한다."""

    return NewsRow(
        cells=(
            str(getattr(fragment, "document_date", "") or "").strip(),
            str(getattr(fragment, "source_publisher", "") or "").strip(),
            str(fragment.text or "").strip(),
        ),
        citations=(str(fragment.fragment_id).strip(),),
    )


def _ordered_candidates(
    fragments: Sequence[CollectedFragment],
) -> list[tuple[int, CollectedFragment]]:
    """최신 발행일이 먼저 오도록 세우고, 같은 날은 수집 순서를 지킨다.

    발행일이 비어 있으면 맨 뒤로 보낸다 — 어차피 ``_row_problem``이 그 행을
    빼지만, 정렬에서부터 뒤로 밀어 두면 상한 세 자리를 먹지 않는다.
    """

    indexed = list(enumerate(fragments))
    return sorted(
        indexed,
        key=lambda item: (
            str(getattr(item[1], "document_date", "") or "").strip() == "",
            _descending_date_key(
                str(getattr(item[1], "document_date", "") or "").strip()
            ),
            item[0],
        ),
    )


def _descending_date_key(published_on: str) -> str:
    """``YYYY-MM-DD``를 내림차순 정렬용 열쇠로 바꾼다.

    날짜 자료형으로 바꾸지 않는다 — 상류가 형식을 이미 강제하고
    (`news_intake.models`), 형식이 아닌 값이 와도 여기서 예외를 던져 보고서
    전체를 막을 이유가 없다. 글자 그대로 뒤집어 세우면 형식이 깨진 값은
    자연히 뒤로 간다.
    """

    return "".join(chr(0x10FFFF - ord(char)) for char in published_on)


@dataclass(frozen=True)
class NewsBlockResult:
    """결정적 보도표 보강 결과."""

    #: 보강 뒤 보고서. 아무 장에도 안 붙였으면 입력과 «같은 객체»다.
    report: ComposedReport
    #: 장별로 실제 실린 행 수. 붙인 장만 담는다.
    row_counts_by_section: tuple[tuple[str, int], ...] = ()
    #: 뺀 행·못 붙인 장의 사유별 수.
    blocked_counts_by_reason: tuple[tuple[str, int], ...] = ()

    @property
    def added(self) -> bool:
        return bool(self.row_counts_by_section)

    @property
    def row_count(self) -> int:
        return sum(count for _section_id, count in self.row_counts_by_section)

    @property
    def section_count(self) -> int:
        return len(self.row_counts_by_section)


def augment_news_blocks(
    report: ComposedReport,
    fragments: Sequence[CollectedFragment],
    *,
    allowed_fragment_ids_by_section: Optional[Mapping[str, frozenset[str]]] = None,
    enabled: bool = True,
) -> NewsBlockResult:
    """뉴스 조각이 온 장마다 끝에 「최근 보도 (보조)」 표 하나를 붙인다.

    Args:
        report: 검증·도식까지 끝난 본문. 아직 렌더·봉인 전이어야 한다.
        fragments: 검증용 조각(대개 장별 packet의 flat union).
        allowed_fragment_ids_by_section: 장별로 인용해도 되는 조각 id.
            ``None``이면 어느 조각이 어느 장 소유인지 알 수 없으므로 표를
            «만들지 않는다». 소유를 모른 채 실으면 다른 장 소유의 조각을
            인용하는 행이 생겨 evidence invariant가 보고서 전체를 막는다.
        enabled: 이 실행이 공개 구조(표)를 «결속할 수 있는» 경로인가.
            거짓이면 표를 만들지 않고 사유만 센다. 결속하지 못하는 모드에서
            표를 붙이면 품질 계약이 「fact_id와 결속되지 않은 공개 내용」으로
            보고 보고서 «전체»를 막는다 — 보조 표 하나 때문에 보고서를
            잃는 것이 이 기능이 바라는 결과일 수 없다.

    Returns:
        보강 결과. 아무 장에도 안 붙였으면 ``report``는 입력과 같은 객체다.
    """

    blocked: Counter[str] = Counter()
    news_fragments = tuple(
        fragment for fragment in fragments if _is_news_fragment(fragment)
    )
    if not news_fragments:
        return NewsBlockResult(report=report)
    if not enabled:
        blocked[BLOCKED_UNBINDABLE_MODE] += len(news_fragments)
        return NewsBlockResult(
            report=report,
            blocked_counts_by_reason=_counts(blocked),
        )
    if allowed_fragment_ids_by_section is None:
        blocked[BLOCKED_NO_SECTION_OWNERSHIP] += len(news_fragments)
        return NewsBlockResult(
            report=report,
            blocked_counts_by_reason=_counts(blocked),
        )

    ordered = _ordered_candidates(news_fragments)
    rebuilt: list[ComposedSection] = []
    row_counts: list[tuple[str, int]] = []
    changed = False
    for section in report.sections:
        allowed = allowed_fragment_ids_by_section.get(section.section_id)
        owned = [
            fragment
            for _index, fragment in ordered
            if allowed is not None and str(fragment.fragment_id).strip() in allowed
        ]
        if not owned:
            rebuilt.append(section)
            continue
        if section.section_id in NEWS_EXCLUDED_SECTION_IDS:
            blocked[BLOCKED_EXCLUDED_SECTION] += len(owned)
            rebuilt.append(section)
            continue
        rows: list[NewsRow] = []
        for fragment in owned:
            if len(rows) >= NEWS_BLOCK_MAX_ROWS:
                blocked[BLOCKED_ROW_LIMIT] += 1
                continue
            row = _news_row(fragment)
            problem = _row_problem(row, fragment, allowed_fragment_ids=allowed)
            if problem:
                blocked[problem] += 1
                continue
            rows.append(row)
        if not rows:
            rebuilt.append(section)
            continue
        changed = True
        row_counts.append((section.section_id, len(rows)))
        # ★ 언제나 «덮어쓴다» — 이어 붙이지 않는다. 이 표는 조각에서만
        #   나오므로 두 번 불러도 같은 행이 나와야 한다(멱등). 이어 붙이면
        #   보충 회차가 도는 회사에서만 행이 두 배가 된다.
        rebuilt.append(replace(section, news_rows=tuple(rows)))
    if not changed:
        return NewsBlockResult(
            report=report,
            blocked_counts_by_reason=_counts(blocked),
        )
    return NewsBlockResult(
        report=replace(report, sections=tuple(rebuilt)),
        row_counts_by_section=tuple(row_counts),
        blocked_counts_by_reason=_counts(blocked),
    )


def _counts(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    """얼린 결과에 실어 나르려고 (사유, 수) 쌍의 정렬된 tuple로 바꾼다."""

    return tuple(sorted(counter.items()))


def news_block_steps(output: object) -> list[dict[str, object]]:
    """보도표에 관한 실행 기록 줄을 만든다.

    ★ 이 함수는 composer가 «정본»으로 소유한다. 실행 기록을 쌓는 쪽
      (`features/pipeline/real.py`)이 한 줄로 부르면 된다 — 단계 이름·필드를
      그쪽에서 손으로 다시 적으면 한쪽이 바뀔 때 조용히 어긋난다.

    Args:
        output: composer ``run_v2``의 결과. 이 필드를 모르는 옛 결과도 받는다.

    Returns:
        남길 단계가 없으면 빈 목록. 일부 장은 붙고 일부는 못 붙었으면
        «두 줄을 다 남긴다» — 한쪽만 남기면 진단이 반쪽이 된다.
    """

    steps: list[dict[str, object]] = []
    by_section = _pairs(getattr(output, "news_block_row_counts_by_section", ()))
    if by_section:
        steps.append(
            {
                "step": NEWS_BLOCK_STEP,
                "행수": sum(by_section.values()),
                "장별행수": by_section,
            }
        )
    blocked = _pairs(getattr(output, "news_block_blocked_counts_by_reason", ()))
    if blocked:
        steps.append(
            {
                "step": NEWS_BLOCK_BLOCKED_STEP,
                "사유별": blocked,
            }
        )
    return steps


def _pairs(value: object) -> dict[str, int]:
    """(열쇠, 수) 쌍 tuple을 dict로 바꾼다. 모양이 깨졌으면 빈 dict."""

    try:
        pairs = tuple(value or ())  # type: ignore[arg-type]
        return {str(key): int(count) for key, count in pairs}
    except (TypeError, ValueError):
        return {}


__all__ = [
    "BLOCKED_CITATION_MISSING",
    "BLOCKED_EXCLUDED_SECTION",
    "BLOCKED_MISSING_META",
    "BLOCKED_NOT_NEWS_KIND",
    "BLOCKED_NOT_SECTION_OWNED",
    "BLOCKED_NO_SECTION_OWNERSHIP",
    "BLOCKED_ROW_LIMIT",
    "BLOCKED_UNBINDABLE_MODE",
    "BLOCKED_TEXT_MISMATCH",
    "NEWS_BLOCK_BLOCKED_STEP",
    "NEWS_BLOCK_CAPTION_TEMPLATE",
    "NEWS_BLOCK_HEADERS",
    "NEWS_BLOCK_MAX_ROWS",
    "NEWS_BLOCK_PRESENTATION",
    "NEWS_BLOCK_STEP",
    "NewsBlockResult",
    "augment_news_blocks",
    "news_block_caption",
    "news_block_steps",
]

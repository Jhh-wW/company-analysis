"""검증된 뉴스 조각을 기사별로 묶어 장 끝의 보조 목록으로 공개한다.

같은 기사와 원문은 반복하지 않으며, 제목·날짜·발행처·본문과 출처 결속을
유지한다. 본문 활용은 news_usage에서 별도로 검수하고 진단한다. 목록이
존재하는 것만으로 본문 반영을 인정하지 않는다. 기사 목록에 별도 수량
상한을 두지 않으며 9장 공식 비교에는 뉴스를 싣지 않는다.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Mapping, Optional, Sequence

from src.features.composer.constants import SECTION_IDS
from src.features.composer.news_constants import (
    NEWS_BODY_DUPLICATE_PREVIEW_CHARS,
    NEWS_BODY_REFERENCE_SUFFIX,
    NEWS_PERIOD_MONTHS,
)
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

# ★ 「어느 의미 칸이 어느 장의 것인가」의 정본도 shared 하나뿐이다
#   (`report_claim_policy`). 여기서 목록을 베껴 적으면 정책이 칸을 하나
#   옮길 때 이쪽만 옛 장에 붙인다.
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION


#: 의미 칸(claim slot) → 그 칸이 속한 장. 정본 표(장 → 칸들)를 뒤집은 것이다.
#:
#: ★ 칸 이름은 ``<장>:<칸>`` 모양이라 문자열을 잘라도 장이 나오지만, 자르지
#:   않는다 — 이름 규칙이 바뀌면 조용히 틀린 장에 붙는다. 정본 표에서 뒤집으면
#:   정책이 칸을 옮길 때 이 표도 같이 옮겨진다.
_SECTION_OF_SLOT: Final[dict[str, str]] = {
    slot_id: section_id
    for section_id, slot_ids in CLAIM_SLOTS_BY_SECTION.items()
    for slot_id in slot_ids
}


# ── 표 모양 ──────────────────────────────────────────────────────────
#: 표 머리글. 「보도 문장」이 마지막이라 긴 글이 오른쪽으로 흐른다.
NEWS_BLOCK_HEADERS: Final[tuple[str, ...]] = ("발행일", "매체 · 기사", "보도 내용")

#: 조각 수와 혼동하지 않도록 기사 단위를 표시한다.
NEWS_BLOCK_CAPTION_TEMPLATE: Final[str] = "최근 보도 (보조, {count}기사)"

#: 표에 실린 기사 중 1차 기간창(``NEWS_PERIOD_MONTHS[0]`` = 12개월)보다 오래된
#: 발행일이 섞였을 때 쓴다. 「최근」이라는 말이 몇 년 전 기사에도 그대로
#: 붙는 것을 막기 위해 가장 오래된 기사의 발행연도를 덧붙인다.
NEWS_BLOCK_CAPTION_TEMPLATE_WITH_YEAR: Final[str] = (
    "최근 보도 (보조, {count}기사 · {year}년 보도 포함)"
)

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
BLOCKED_DUPLICATE_ARTICLE: Final[str] = "article_listed_in_another_section"
BLOCKED_ARTICLE_META: Final[str] = "inconsistent_article_metadata"


def news_block_caption(row_count: int, oldest_stale_year: Optional[int] = None) -> str:
    """표 캡션 — 캡션 글자를 두 곳에서 따로 만들지 않는다.

    Args:
        row_count: 표에 실제로 실은 기사 수.
        oldest_stale_year: 표에 실린 기사 중 가장 오래된 발행연도가 1차
            기간창(12개월)보다 앞서 있을 때만 그 연도를 준다. 그 밖에는
            ``None`` — 「최근」이라는 말과 실제 기사 시점이 어긋날 때만
            표시를 늘리고, 나머지는 기존 문구를 그대로 지킨다.
    """

    if oldest_stale_year is not None:
        return NEWS_BLOCK_CAPTION_TEMPLATE_WITH_YEAR.format(
            count=int(row_count), year=int(oldest_stale_year)
        )
    return NEWS_BLOCK_CAPTION_TEMPLATE.format(count=int(row_count))


def _month_boundary(as_of: date, months: int) -> date:
    """윤년과 월말을 보존하는 달력 기준 기간 경계.

    ★ ``news_intake.search_snapshot.month_boundary``와 같은 계산이다. 이
      파일 위쪽의 shared import 주석대로 feature끼리는 직접 import하지
      않으므로 가져다 쓸 수 없고, 같은 이유로 이 파일이 속한 `composer`
      안의 `news_usage.py`도 이미 같은 계산을 따로 들고 있다
      (``news_usage_diagnostics``). 값을 바꾸려면 세 자리
      (``news_intake.search_snapshot``·``composer.news_usage``·여기)를
      함께 맞춰야 한다.
    """

    month_index = as_of.year * 12 + as_of.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(as_of.day, calendar.monthrange(year, month)[1]))


def oldest_stale_report_year(rows: Sequence[NewsRow], as_of_date: str) -> Optional[int]:
    """표에 실제로 실린 행 중 가장 오래된 발행일의 연도 — 1차 기간창보다 오래됐을 때만.

    ★ fail-safe — 기준일이 비었거나 형식이 다르거나, 행 발행일 중 하나라도
      ``YYYY-MM-DD``가 아니면 «모른다»로 보고 ``None``을 돌려준다(예외를
      던져 보고서 전체를 막지 않는다). 알 수 없는 연도를 지어내는 것보다
      표시를 생략하고 기존 문구를 유지하는 쪽이 안전하다.

    Args:
        rows: 표에 실제로 실린 행(citation이 살아남아 인쇄되는 행만 — 인용
            번호를 못 찾아 빠진 행은 넣지 않는다).
        as_of_date: 보고서 기준일. ``YYYY-MM-DD`` 형식이 아니면 ``None``.

    Returns:
        가장 오래된 발행일이 기준일보다 1차 기간창(``NEWS_PERIOD_MONTHS[0]``
        = 12개월) 이상 앞설 때만 그 발행연도. 그 밖에는 ``None``.
    """

    if not rows:
        return None
    try:
        as_of = date.fromisoformat(str(as_of_date).strip())
    except (TypeError, ValueError):
        return None
    oldest: Optional[date] = None
    for row in rows:
        if not row.cells:
            return None
        try:
            published = date.fromisoformat(str(row.cells[0]).strip())
        except (TypeError, ValueError):
            return None
        if oldest is None or published < oldest:
            oldest = published
    if oldest is None or oldest >= _month_boundary(as_of, NEWS_PERIOD_MONTHS[0]):
        return None
    return oldest.year


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


def news_ownership_from_claim_slots(
    fragments: Sequence[CollectedFragment],
) -> dict[str, frozenset[str]]:
    """뉴스 조각이 «스스로 봉인해 온» 의미 칸에서 장 소유권을 되찾는다.

    ★ 왜 필요한가 — 장별 근거 packet은 FULL에서만 만들어진다. 자료가 모자라
      부분 보고서로 내려가면 packet이 ``None``이라 소유권 표가 없고,
      `augment_news_blocks`는 «모르면 안 싣는다»는 원칙대로 뉴스 조각을 전부
      뺀다(사유 ``no_section_ownership``). 그런데 packet이 없는 그 경로가 바로
      «부분 보고서» — 회사 공식 자료가 모자란 실행이고, 언론 보도가 가장
      필요한 실행이다. 정작 필요한 자리에서만 표가 사라진다.

    ★ 왜 이렇게 되찾아도 되는가 — typed 근거 운반 계약의 불변식이
      「조각이 실리는 장의 집합 == 그 조각 ``supported_claim_slots``가 속한 장의
      집합」이다. 즉 packet이 하던 일을 조각이 이미 스스로 들고 다닌다.
      packet이 있으면 여전히 packet 표가 정본이고(FULL 동작 불변), 없을 때만
      이 표를 쓴다.

    Args:
        fragments: 이 실행의 조각 전체(공식 조각이 섞여 있어도 된다).

    Returns:
        장 id → 그 장에서 인용해도 되는 뉴스 조각 id 집합. ``SECTION_IDS``의
        모든 장이 키로 있으므로 ``.get(section_id)``가 ``None``을 돌려주지
        않는다 — 소유를 «모른다»와 «없다»가 뒤섞이지 않게 한다. 의미 칸을
        하나도 안 들고 온 조각은 어느 장에도 넣지 않는다(fail-closed).
    """

    owned: dict[str, set[str]] = {section_id: set() for section_id in SECTION_IDS}
    for fragment in fragments:
        if not _is_news_fragment(fragment):
            continue
        fragment_id = str(getattr(fragment, "fragment_id", "") or "").strip()
        if not fragment_id:
            continue
        for slot_id in tuple(getattr(fragment, "supported_claim_slots", ()) or ()):
            # 모르는 칸 이름은 «무시»한다. 보조 표 하나 때문에 보고서 전체를
            # 예외로 막지 않는다 — 그 칸이 가리키는 장을 모를 뿐이다.
            section_id = _SECTION_OF_SLOT.get(str(slot_id).strip())
            if section_id in owned:
                owned[section_id].add(fragment_id)
    return {section_id: frozenset(ids) for section_id, ids in owned.items()}


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
            article_label(fragment),
            str(fragment.text or "").strip(),
        ),
        citations=(str(fragment.fragment_id).strip(),),
        evidence_texts=(fragment.text,),
    )


def article_label(fragment: CollectedFragment) -> str:
    """제목·발행처는 같은 칸에, 실제 링크는 행 출처 번호로 제공한다."""
    return " · ".join(value.strip() for value in (
        fragment.source_publisher, fragment.document_title,
    ) if value.strip())


def news_list_excerpt(text: str, body_text: str) -> str:
    """긴 원문이 본문에 이미 실렸으면 목록에는 원문 앞부분과 본문 위치를 표시한다."""
    text = text.strip()
    if len(text) <= NEWS_BODY_DUPLICATE_PREVIEW_CHARS or text not in body_text:
        return text
    end = text.rfind(" ", 0, NEWS_BODY_DUPLICATE_PREVIEW_CHARS)
    if end <= 0:
        end = NEWS_BODY_DUPLICATE_PREVIEW_CHARS
    return text[:end] + NEWS_BODY_REFERENCE_SUFFIX


def article_row(fragments: Sequence[CollectedFragment], body_text: str = "") -> NewsRow:
    """기사 하나의 서로 다른 정확 원문을 한 행에 묶는다."""
    first = fragments[0]
    unique = tuple(dict.fromkeys(news_list_excerpt(f.text, body_text) for f in fragments))
    return NewsRow(
        cells=(first.document_date.strip(), article_label(first), "\n".join(unique)),
        citations=tuple(dict.fromkeys(f.fragment_id for f in fragments)),
        evidence_texts=tuple(f.text for f in fragments),
    )


def _ordered_candidates(
    fragments: Sequence[CollectedFragment],
) -> list[tuple[int, CollectedFragment]]:
    """최신 발행일이 먼저 오도록 세우고, 같은 날은 수집 순서를 지킨다.

    발행일이 비어 있으면 맨 뒤로 보낸다. 해당 행은 ``_row_problem``이
    필수 메타데이터 누락으로 제외한다.
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
            packet이 있으면 packet 표가, 없으면
            `news_ownership_from_claim_slots`가 만든 표가 온다.
            ``None``이면 어느 조각이 어느 장 소유인지 알 수 없으므로 표를
            «만들지 않는다». 소유를 모른 채 실으면 다른 장 소유의 조각을
            인용하는 행이 생겨 evidence invariant가 보고서 전체를 막는다.
            (지금 호출자는 언제나 표를 만들어 넘기지만, 이 방어는 남겨 둔다 —
            표를 못 만드는 호출자가 새로 생겨도 여기서 fail-closed 된다.)
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
    listed_articles: set[str] = set()
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
        grouped: dict[str, list[CollectedFragment]] = {}
        for fragment in owned:
            row = _news_row(fragment)
            problem = _row_problem(row, fragment, allowed_fragment_ids=allowed)
            if not fragment.source_publisher.strip():
                problem = BLOCKED_MISSING_META
            if not fragment.document_title.strip() or not fragment.source_url.strip():
                problem = BLOCKED_ARTICLE_META
            if problem:
                blocked[problem] += 1
                continue
            key = fragment.document_identity or fragment.source_url or fragment.fragment_id
            grouped.setdefault(key, []).append(fragment)
        for key, article in grouped.items():
            if key in listed_articles:
                blocked[BLOCKED_DUPLICATE_ARTICLE] += len(article)
                continue
            if len({(f.document_date, f.source_publisher, f.document_title) for f in article}) != 1:
                blocked[BLOCKED_ARTICLE_META] += len(article)
                continue
            rows.append(article_row(article, "\n".join(sentence.text for sentence in section.sentences)))
            listed_articles.add(key)
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
    "NEWS_BLOCK_CAPTION_TEMPLATE_WITH_YEAR",
    "NEWS_BLOCK_HEADERS",
    "NEWS_BLOCK_PRESENTATION",
    "NEWS_BLOCK_STEP",
    "NewsBlockResult",
    "augment_news_blocks",
    "news_block_caption",
    "news_block_steps",
    "news_ownership_from_claim_slots",
    "oldest_stale_report_year",
]

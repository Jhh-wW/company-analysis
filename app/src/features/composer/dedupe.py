"""장 간 중복 문장 제거 (사실 단일 소유의 «강제» 단계).

★ 왜 필요한가 (실측) — 순차 작성·장별 소유 경계 지침은 «작가에게 주는
  부탁»이라 지켜지기도 하고 안 지켜지기도 한다. 다른 엔터사 실측에서 수치 반복은 0건까지
  내려갔지만, 숫자가 없는 사실은 여전히 6개 장에 거의 같은 문장으로 반복됐다:

      [3장] 회사는 Sony Music, TME, Republic Records 등 글로벌 유통 전문사와의…
      [5장] 회사는 Sony Music, TME, Republic Records 등 글로벌 유통 전문사와의…
      [6장] 회사는 Sony Music, TME, Republic Records 등 글로벌 유수의 음반·음원…

  정본 「사실_소유권과_중복_검사.md」 §3은 «모든 사실은 본문 한 장만 소유한다»이고
  §5는 «단어를 바꾸었어도 대상·사건·시점·값이 같으면 중복»이라고 못 박는다.

★ 이것은 «닫힌 목록 게이트»가 아니다.
  - 어휘 목록·어미 패턴·출처 종류 화이트리스트를 쓰지 않는다.
  - 문장 «내용의 좋고 나쁨»을 판단하지 않는다. 두 문장이 같은 근거를 쓰면서
    글자가 겹치는가라는 «모양»만 본다.
  - 거절이 아니라 «이동»이다 — 지운 문장은 소유 장에 그대로 남아 있다.
  - 장을 삭제하지 않는다. 비면 정직한 안내문을 남긴다.

★ 소유 장을 «먼저 나온 장»으로 정하지 않는다. 실측에서 파트너 사실이 1장에
  먼저 스쳐 지나가고 7장이 세 문장으로 제대로 다뤘다. 순서대로 지우면 제대로
  다룬 쪽이 지워진다. 그래서 «그 사실을 가장 많이 다룬 장»이 소유한다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from typing import Final, Optional

from src.features.composer.constants import (
    CHALLENGE_FLOW_SECTION_ID,
    NOTICE_DUPLICATE_MOVED,
    NOTICE_DUPLICATE_MOVED_TABLE_KEPT,
    SECTION_IDS,
    STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.future_plan_guard import has_forward_marker
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

logger = logging.getLogger(__name__)

#: 글자 n-그램 길이. 한국어는 조사가 붙어 어절이 달라지므로(「파트너십을」·
#: 「파트너십과」) 어절 단위로는 같은 사실을 못 알아본다. 글자 3-그램은
#: 형태소 목록 없이도 그 차이를 넘어선다 — 닫힌 목록을 만들지 않기 위한 선택이다.
_NGRAM_SIZE: Final[int] = 3

#: 두 문장을 «같은 사실»로 볼 겹침 비율. 겹침 = 교집합 ÷ 짧은 쪽 크기.
#: 0.6은 보수적인 값이다 — 애매하면 남긴다(잘못 지우는 쪽이 더 나쁘다).
#: 이 값은 «근거 조각을 공유하는» 짝에만 쓴다.
_OVERLAP_THRESHOLD: Final[float] = 0.6

#: 조각은 다르지만 «같은 원문 문서»를 근거로 든 짝에 쓰는 더 높은 겹침 기준.
#:
#: ★ 왜 필요한가 (실측) — 교육서비스 회사 실측에서 같은 수주 사실이 3장과
#:   7장에 거의 같은 문장으로 실렸는데(겹침 0.8889) 한 문장도 빠지지 않았다.
#:   두 문장이 «같은 사업보고서의 서로 다른 조각»을 인용했기 때문이다:
#:     · 3장 v2-frag-8   ← rcept_no 20260316000476
#:     · 7장 v2-frag-26·28 ← rcept_no 20260316000476 (같은 문서)
#:   «조각이 다르면 다른 자료»라는 기존 조건의 «뜻»은 옳지만 «단위»가 틀렸다.
#:   한 공시를 수십 개 조각으로 쪼개는 수집 구조에서 다른 자료의 단위는
#:   조각이 아니라 «문서»다. 그래서 조건을 없애지 않고 단위만 바로잡는다.
#: ★ 문서까지 다르면 여전히 비교하지 않는다 — 정말 다른 자료에서 온 두 사실이
#:   표현만 닮았다고 지워지는 일을 만들지 않기 위해서다.
#: ★ 값의 근거 (실측) — 저장본 픽스처 5건 + 위 실행 1건에서 «조각을 공유하지
#:   않고 장이 다르고 길이 하한을 넘는» 짝 2,652개를 전수로 쟀다.
#:     · 진짜 중복 1쌍 = 0.8889 (위 수주 문장)
#:     · 그다음으로 높은 짝 = 0.7843 — 엔터사 저장본의
#:       «수익은 여섯 부문에서 발생한다»(2장) ↔ «핵심 제품은 여섯 부문으로
#:       구성된다»(3장). 여섯 부문 «목록»이 길어서 글자가 겹칠 뿐 주장이
#:       서로 다르다. 이건 지우면 안 되는 짝이다.
#:     · 세 번째 = 0.6957
#:   0.7843~0.8889 사이에는 관측이 하나도 없다. 0.85는 그 빈 구간 안이고,
#:   지우면 안 되는 최고치보다 0.066 위, 지워야 할 중복보다 0.039 아래다.
#:   «애매하면 남긴다»는 이 파일의 원칙대로 빈 구간 밖으로 내리지 않는다.
_SAME_DOCUMENT_OVERLAP_THRESHOLD: Final[float] = 0.85

#: 이 길이 미만의 문장은 비교하지 않는다. 짧은 문장은 우연히 많이 겹친다.
_MIN_COMPARE_CHARS: Final[int] = 20

#: 소유권을 «정본 순서» 대신 «시제»로 가르는 장 쌍 — 5장(당면 과제와 대응)과
#: 6장(성장 전략).
#:
#: ★ 왜 이 쌍만인가 — 이 두 장은 주제가 아니라 «시점»으로 갈린다. 5장은 지금
#:   겪는 과제와 그 대응, 6장은 아직 하지 않은 계획이다(SECTION_GUIDES 원문).
#:   회사가 「…해 나가겠습니다」라고 적은 한 문장을 작가가 두 장에 각각 옮기면,
#:   6장에는 「…하겠다는 계획」으로, 5장에는 「…하려 하고 있다」로 어투만 달리
#:   실린다. 근거가 같으니 중복 제거가 하나를 뺀다.
#: ★ 왜 순서로는 안 되는가 (실측 — 4차 유료 실행 멀티캠퍼스) — 두 장이 각각
#:   한 문장씩이라 «깊이»가 동점이고, 동점이면 정본 목차에서 앞선 5장이 이긴다.
#:   미래 계획은 현재 과제와 같은 근거를 쓰는 일이 잦아 6장이 «구조적으로»
#:   진다. 그 결과 6장이 세 실행 중 두 번 통째로 비었다.
#: ★ 그래서 이 쌍에서는 «어느 장이 앞이냐»가 아니라 «그 문장이 앞일을 말하나»로
#:   가른다. 미래 표지를 한쪽만 달고 있으면 그 장이 소유한다. 둘 다 달고
#:   있거나 둘 다 없으면 판단 근거가 없으므로 기존 규칙(정본 순서)에 맡긴다 —
#:   억지로 한쪽에 몰면 이번에는 반대 장이 통째로 빈다.
#: ★ «깊이»보다 앞세우지 않는다. 이 파일의 중심 규칙(그 사실을 가장 많이 다룬
#:   장이 소유한다)은 실측으로 세운 것이고, 여기서 고치는 것은 그 뒤에 오던
#:   «동점 처리»뿐이다.
_TENSE_OWNED_PAIR: Final[frozenset[str]] = frozenset(
    {CHALLENGE_FLOW_SECTION_ID, STRATEGY_TABLE_SECTION_ID}
)

#: 글자만 남긴다 — 한글·영문·숫자. 공백·문장부호는 표기 차이라 무시한다.
_KEEP_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[^0-9A-Za-z가-힣]+")


def _document_keys(
    fragments: Optional[Sequence[CollectedFragment]],
) -> dict[str, frozenset[str]]:
    """조각 id → «어느 원문 문서에서 왔나» 열쇠 «집합».

    ★ 왜 «하나»가 아니라 «집합»인가 (실측) — 조각 생산자가 두 갈래다.
      typed 조각은 지문(document_content_sha256)과 신원(document_identity)을
      둘 다 채우지만, legacy 조각은 지문을 빈 문자열로 «고정»하고
      (pipeline/evidence_transport.py의 legacy 갈래) 신원만 채운다. 우선순위
      하나만 열쇠로 쓰면 같은 공시에서 나온 두 조각이 한쪽은 지문, 한쪽은
      신원으로 갈려 교집합이 비고 «같은 문서» 판정이 통째로 꺼진다.
      실측 실행에서 3장 legacy 조각과 7장 typed 조각이 정확히 그 모양이었다.
      그래서 조각이 가진 강한 열쇠를 «전부» 담고 하나라도 겹치면 같은 문서로
      본다.

    ★ 열쇠가 없는 조각은 «자기 자신»을 열쇠로 갖는다 — 빈 값끼리 묶여 서로
      무관한 조각이 «같은 문서»가 되는 사고를 막는다(fail-closed).

    ★ 문서명은 강한 열쇠가 하나도 없을 때만 쓴다 — 「사업보고서」처럼 흔한
      제목은 서로 다른 회사의 다른 문서에도 똑같이 붙어 있어서, 지문·신원·
      주소와 나란히 두면 무관한 문서를 묶는 다리가 된다.
    """

    keys: dict[str, frozenset[str]] = {}
    for fragment in fragments or ():
        fragment_id = str(fragment.fragment_id)
        strong = {
            candidate
            for candidate in (
                fragment.document_content_sha256,
                fragment.document_identity,
                fragment.source_url,
            )
            if candidate
        }
        keys[fragment_id] = frozenset(
            strong or {fragment.document_title or f"조각:{fragment_id}"}
        )
    return keys


def _documents_of(
    citations: frozenset[str], keys: dict[str, frozenset[str]]
) -> frozenset[str]:
    """그 문장이 근거로 든 문서 열쇠 집합. 모르는 조각은 자기 id로 남는다."""

    merged: set[str] = set()
    for citation in citations:
        merged |= keys.get(citation, frozenset({f"조각:{citation}"}))
    return frozenset(merged)


def _signature(text: str) -> frozenset[str]:
    """문장을 글자 3-그램 집합으로 바꾼다 (표기 차이에 둔감한 지문)."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    condensed = _KEEP_CHARS_RE.sub("", normalized)
    if len(condensed) < _NGRAM_SIZE:
        return frozenset()
    return frozenset(
        condensed[index : index + _NGRAM_SIZE]
        for index in range(len(condensed) - _NGRAM_SIZE + 1)
    )


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    """겹침 비율 = 교집합 ÷ 짧은 쪽. 길이 차이에 벌을 주지 않는다.

    같은 사실을 한 장은 길게, 다른 장은 짧게 쓰는 일이 흔하다. 자카드 계수를
    쓰면 그 경우를 놓치므로 짧은 쪽 기준으로 본다.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _section_order() -> dict[str, int]:
    return {section_id: order for order, section_id in enumerate(SECTION_IDS)}


def _tight_groups(similar: dict[int, set[int]], size: int) -> list[list[int]]:
    """서로 «모두» 닮은 문장만 한 무리로 묶는다 (완전 연결).

    ★ 왜 연쇄 병합(union-find)을 쓰지 않는가 — 한 문장이 두 사실을 함께
      언급하면(「Sony와 파트너십을 맺었고 Live Nation과도…」) 서로 무관한
      사실들이 그 문장을 다리 삼아 한 덩어리로 합쳐진다. 실측에서 그렇게
      묶인 덩어리가 7장의 파트너 문장 4개를 통째로 날렸다.
      «모든 짝이 서로 닮아야» 한 무리로 본다.
    """
    grouped: set[int] = set()
    groups: list[list[int]] = []
    for seed in range(size):
        if seed in grouped or not similar.get(seed):
            continue
        group = [seed]
        for candidate in sorted(similar[seed]):
            if candidate in grouped:
                continue
            if all(candidate in similar.get(member, set()) for member in group):
                group.append(candidate)
        if len(group) < 2:
            continue
        grouped.update(group)
        groups.append(group)
    return groups


def _future_section_owner(
    tied: set[int],
    group: Sequence[int],
    flat: Sequence[tuple[int, int, ComposedSentence]],
    report: ComposedReport,
) -> Optional[int]:
    """5장↔6장 동점을 «시제»로 가른다. 가를 수 없으면 None을 돌려준다.

    돌려주는 값은 `report.sections`의 색인이다. 6장 쪽 문장만 미래 표지를 달고
    있을 때에만 6장을 준다 — 반대(5장만 미래)일 때는 기존 정본 순서가 이미
    5장을 주므로 여기서 따로 갈라 줄 것이 없다.

    ★ 판정은 «그 장에 실린 그 문장»을 본다. 같은 사실이라도 두 장에 실린 글은
      어투가 다르다. 실측에서 6장에는 「…확보해 나가겠다는 계획은」, 5장에는
      「…확보하려 하고 있다」로 실렸다 — 원문은 「…확보해 나가겠습니다」다.
    ★ 미래 표지 목록은 `future_plan_guard.has_forward_marker` 하나를 쓴다.
      6장 장 배치 관문이 쓰는 바로 그 목록이다. 여기서 목록을 새로 만들면
      「관문은 통과했는데 소유는 못 가져가는」 장이 생긴다.
    """

    if {report.sections[index].section_id for index in tied} != _TENSE_OWNED_PAIR:
        return None
    by_section_id = {report.sections[index].section_id: index for index in tied}
    forward = {
        section_id: any(
            has_forward_marker(flat[index][2].text)
            for index in group
            if flat[index][0] == section_index
        )
        for section_id, section_index in by_section_id.items()
    }
    if forward[STRATEGY_TABLE_SECTION_ID] and not forward[CHALLENGE_FLOW_SECTION_ID]:
        return by_section_id[STRATEGY_TABLE_SECTION_ID]
    return None


def _empty_section_notice(section: ComposedSection) -> str:
    """문장이 다 빠진 장에 남길 안내문. 표가 남으면 그 사실까지 적는다.

    ★ 왜 갈라 쓰나 (실측 — 4차 유료 실행 멀티캠퍼스 6장) — 「이 장에 담겼던
      내용이 … 그쪽으로 모았습니다」라고 적고 바로 아래에 「회사가 밝힌 성장
      계획」 표를 그대로 실었다. 표는 문장과 별개 재료라 남는 것이 맞지만,
      안내문이 그 사실을 말하지 않아 읽는 사람에게는 글과 화면이 어긋나 보인다.
    ★ 표를 «빼서» 맞추지 않는다. 표는 자기 근거로 검증을 통과한 자료다 —
      문장을 옮긴다고 함께 지우면 근거 있는 내용을 잃는다.
    """

    if section.flow_rows or section.news_rows:
        return NOTICE_DUPLICATE_MOVED_TABLE_KEPT
    return NOTICE_DUPLICATE_MOVED


def _section_sentence_counts(report: ComposedReport) -> dict[str, int]:
    return {section.section_id: len(section.sentences) for section in report.sections}


def _log_chapter_sentence_counts(before: ComposedReport, after: ComposedReport) -> None:
    """장별 문장 수를 «정리 전→후»로 한 줄에 남긴다 (무과금 진단용).

    ★ 왜 필요한가 — 어느 장이 이 단계에서 얼마나 깎였는지 지금까지는 코드로
      볼 방법이 없어 실측(=유료 AI 재호출)을 다시 돌려야 했다(
      대조 회사 재조사 2건). 장 id와 «개수»만 남기고 문장 본문은 넣지 않는다
      — 로그에 회사 원문이 그대로 남으면 안 된다.
    ★ 문장마다 찍지 않는다 — 장 단위로 한 줄만 남겨 본 작업(중복 제거)을
      느리게 하지 않는다.
    """
    before_counts = _section_sentence_counts(before)
    after_counts = _section_sentence_counts(after)
    logger.info(
        "장별 문장 수(정리 전→후): %s",
        ", ".join(
            f"{section_id}:{before_counts[section_id]}→{after_counts.get(section_id, 0)}"
            for section_id in before_counts
        ),
    )


def drop_cross_section_duplicates(
    report: ComposedReport,
    *,
    fragments: Optional[Sequence[CollectedFragment]] = None,
) -> tuple[ComposedReport, int]:
    """여러 장에 반복된 같은 사실을 «소유 장 하나»만 남기고 뺀다.

    두 문장을 같은 사실로 보는 조건은 글자 3-그램 겹침 하나이고, 문턱은
    «근거가 얼마나 가까운가»로 갈린다:
      ① 근거 조각을 하나 이상 공유하면 — 낮은 문턱(_OVERLAP_THRESHOLD)
      ② 조각은 달라도 같은 원문 문서를 근거로 들었으면 —
         높은 문턱(_SAME_DOCUMENT_OVERLAP_THRESHOLD)
      ③ 문서까지 다르면 — 비교하지 않는다 (정말 다른 자료면 다른 사실이다)
    ②가 필요한 이유는 한 공시 문서가 수십 개 조각으로 쪼개져, 같은 사실이
    서로 다른 조각을 인용한 채 두 장에 실릴 수 있기 때문이다(상수 주석의 실측).

    장 쌍을 가리지 않는다 — 3장↔7장이든 4장↔2장이든 같은 규칙이 걸린다.

    소유 장은 «그 사실을 가장 많이 다룬 장»이다. 같으면 정본 목차에서 앞선 장.
    이 결정 규칙은 ②로 새로 걸린 짝에도 그대로 쓴다 — 무리의 인용을 모두
    모아(group_citations) 그 근거를 가장 여러 문장으로 다룬 장이 이긴다.

    Args:
        report: 검증(verify_report)까지 끝난 보고서.
        fragments: 이 보고서가 인용한 조각들. ②를 판정하려면 «어느 조각이 어느
            문서에서 왔는지»가 있어야 한다. 넘기지 않으면 ②가 꺼지고 ①만
            남는다 — 예전 동작 그대로다. 운영 호출부는 반드시 넘긴다
            (시험: test_dedupe_unshared_citations.py 의 배선 단정).

    Returns:
        (중복이 빠진 보고서, 뺀 문장 수).

    ★ 요약(compose_summary) «전»에 부르는 것을 전제한다. 요약이 곧 사라질
      문장을 재료로 고르면 본문에 없는 요약이 남는다.
    """
    flat: list[tuple[int, int, ComposedSentence]] = []
    for section_index, section in enumerate(report.sections):
        for sentence_index, sentence in enumerate(section.sentences):
            flat.append((section_index, sentence_index, sentence))
    if len(flat) < 2:
        _log_chapter_sentence_counts(report, report)
        return report, 0

    signatures = [_signature(item[2].text) for item in flat]
    citation_sets = [frozenset(item[2].citations) for item in flat]
    document_keys = _document_keys(fragments)
    document_sets = [_documents_of(citations, document_keys)
                     for citations in citation_sets]
    comparable = [
        bool(citation_sets[index]) and len(item[2].text) >= _MIN_COMPARE_CHARS
        for index, item in enumerate(flat)
    ]

    # 닮음 그래프 — 같은 장 안의 짝도 넣는다. 무리를 «정확히» 묶기 위해서다
    #  (지우는 것은 장이 다를 때뿐이다).
    similar: dict[int, set[int]] = {}
    for left in range(len(flat)):
        if not comparable[left]:
            continue
        for right in range(left + 1, len(flat)):
            if not comparable[right]:
                continue
            # 조각을 공유하면 기존 문턱, 조각은 달라도 같은 문서를 근거로 들면
            # 더 높은 문턱, 문서까지 다르면 아예 비교하지 않는다.
            # 장 쌍을 가리지 않는다 — 모든 장 쌍에 같은 규칙이 걸린다.
            if citation_sets[left] & citation_sets[right]:
                threshold = _OVERLAP_THRESHOLD
            elif fragments is not None and (document_sets[left] & document_sets[right]):
                threshold = _SAME_DOCUMENT_OVERLAP_THRESHOLD
            else:
                continue
            if _overlap(signatures[left], signatures[right]) >= threshold:
                similar.setdefault(left, set()).add(right)
                similar.setdefault(right, set()).add(left)

    order = _section_order()
    drop: set[int] = set()
    for group in _tight_groups(similar, len(flat)):
        if len({flat[index][0] for index in group}) < 2:
            continue  # 한 장 안의 반복은 이 단계가 다루지 않는다
        # 소유 장 = «그 근거를 가장 깊이 쓴 장». 무리 안 문장 수로 재면 각 장이
        # 한 문장씩일 때 동점이 나 앞선 장이 이겨 버린다(실측 — 7장이 1장에게
        # 파트너 사실을 뺏겼다). 그래서 «그 출처를 인용한 문장이 그 장에 몇
        # 개인가»로 잰다. 파트너를 세 문장으로 다룬 7장이 이렇게 하면 이긴다.
        group_citations: set[str] = set()
        for index in group:
            group_citations |= citation_sets[index]
        depth: dict[int, int] = {}
        for section_index, section in enumerate(report.sections):
            count = sum(
                1
                for sentence in section.sentences
                if group_citations & set(sentence.citations)
            )
            if count:
                depth[section_index] = count
        # 깊이가 같은 장들만 남긴다 — 여기부터가 «동점 처리»다.
        candidates = {flat[index][0] for index in group}
        best_depth = max(depth.get(section_index, 0) for section_index in candidates)
        tied = {
            section_index
            for section_index in candidates
            if depth.get(section_index, 0) == best_depth
        }
        # 5장↔6장 동점은 정본 순서가 아니라 시제로 가른다(_TENSE_OWNED_PAIR 주석).
        owner = _future_section_owner(tied, group, flat, report)
        if owner is None:
            owner = min(
                tied,
                key=lambda section_index: order.get(
                    report.sections[section_index].section_id, section_index
                ),
            )
        for index in group:
            if flat[index][0] != owner:
                drop.add(index)

    if not drop:
        _log_chapter_sentence_counts(report, report)
        return report, 0

    dropped_by_section: dict[int, set[int]] = {}
    for index in drop:
        dropped_by_section.setdefault(flat[index][0], set()).add(flat[index][1])

    rebuilt: list[ComposedSection] = []
    for section_index, section in enumerate(report.sections):
        removed = dropped_by_section.get(section_index)
        if not removed:
            rebuilt.append(section)
            continue
        kept = tuple(
            sentence
            for sentence_index, sentence in enumerate(section.sentences)
            if sentence_index not in removed
        )
        # 장 삭제 금지 — 비면 왜 비었는지 정직하게 남긴다.
        # ★ 표는 문장과 별개 재료라 이 단계에서 그대로 남는다(아래 flow_rows).
        #   그런데 안내문이 「그쪽으로 모았습니다」만 말하면, 바로 아래에 표가
        #   실려 글과 화면이 어긋난다(실측 — 6장 「회사가 밝힌 성장 계획」).
        #   그래서 표가 남는 장에는 남는다는 사실까지 적는다.
        notice = section.notice
        if not kept and not notice:
            notice = _empty_section_notice(section)
        rebuilt.append(
            ComposedSection(
                section_id=section.section_id,
                sentences=kept,
                notice=notice,
                # ★ 경로표를 «반드시» 함께 넘긴다. 안 넘기면 기본값 ()로 떨어져
                #   7장에서 문장이 하나라도 빠질 때 도식 재료가 통째로 사라진다
                #   — 실측에서 7장 흐름도가 두 번 연속 안 나온 진짜 원인이었다.
                #   중복 «문장»을 옮기는 단계가 «도식»까지 지우면 안 된다.
                flow_rows=section.flow_rows,
                # ★ 보도표와 그 제외 사유도 같은 이유로 함께 넘긴다. 기본값이
                #   빈 값이라 안 넘기면 이 단계가 조용히 지운다.
                news_rows=section.news_rows,
                news_decisions=section.news_decisions,
            )
        )

    result = ComposedReport(sections=tuple(rebuilt), summary=report.summary)
    _log_chapter_sentence_counts(report, result)
    return result, len(drop)

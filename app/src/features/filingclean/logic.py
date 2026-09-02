"""공시 조각이 «목차»를 담아 오는 것을 고친다.

★ 무엇이 잘못됐나 — 1판은 절 표제를 찾을 때 **첫 출현만** 본다:

      for m in re.finditer(head, filing_text):
          chunk = filing_text[m.start(): m.start() + FRAG_CHARS]
          break        # ← 표제당 «첫 출현»만

  그런데 사업보고서의 **첫 페이지가 목차**다. 「사업의 내용」의 첫 출현은
  본문이 아니라 **목차 줄**이고, 거기서 1,200자를 뜨면 통째로 목차가 된다:

      사업의 내용 ------- 15  III. 재무에 관한 사항 ------- 40
      1. 요약재무정보 ------- 40  2. 연결재무제표 ------- 42 …

  실측 — 하이브 사업보고서 조각 **8개 중 3개가 목차**(사업내용·재무·MD&A).
  그래서 1·3·4번 칸이 통째로 비었다.
  저장 기록 전체로는 238개 중 **14개(5.9%)**다 — 로보스타는 **9개 중 5개**가 목차였다.
  ⚠️ 처음에는 6개로 셌는데, 그건 **붙임표(`---`) 목차만 보던 때의 수치**다.

★ 어떻게 고치나 — **목차로 보이면 «다음 출현»을 찾는다.** 1판은 안 고친다.
  1판이 만든 조각을 받아서, 목차인 것만 원문에서 다시 뜬다.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# ★ 목차 판정은 «두 feature»가 같이 쓴다 (여기 + grading) → core 경유가 규칙이다.
#   각자 복사해 두면 한쪽만 고쳐져 「앞단은 목차로 보는데 뒷단은 아닌」 상태가 된다 (사고).
from src.core.docshape import is_table_of_contents

#: 조각으로 인정할 최소 길이. 1판과 같은 값(200)을 쓴다 —
#: 여기서 다른 값을 쓰면 「1판은 만들었는데 우리는 버리는」 조각이 생긴다.
_MIN_CHUNK_CHARS = 200

# 같은 표제가 목차·요약·교차참조·본문에 여러 번 나온다. ``목차인가``만 보면
# JYP의 「상세 내용은 본문 참조」 요약과 「재무제표 주석을 참조」 교차참조가
# 본문으로 통과했다. 종류별 실제 내용 표시를 세어 가장 설명력이 큰 덩어리를 고른다.
_KIND_MARKERS: dict[str, tuple[str, ...]] = {
    "사업내용": (
        "사업의 개요", "사업부문", "세부사업내용", "주요 제품", "매출 실적",
        "음반사업", "매니지먼트사업", "파트너십", "판매", "유통",
    ),
    "수익인식": ("수익인식", "고객과의 계약", "수행의무", "거래가격", "한 시점"),
    "재무": (
        "요약 연결 재무정보", "연결 재무상태표", "매출액", "영업이익", "당기순이익",
        "자산", "부채", "현금흐름", "(단위 : 원)",
    ),
    "MD&A": (
        "재무상태", "영업실적", "매출 및 영업이익", "경영성과", "사업부문", "개요",
        "원가", "수익성",
    ),
    "연구개발": (
        "연구개발활동", "연구개발인력", "담당 조직", "신인개발", "주요계약", "기술개발",
    ),
    "특수관계자": ("보고기간말 현재", "관계기업", "종속기업", "특수관계자 거래", "지분율"),
    "판관비": ("판매비와관리비", "급여", "광고선전비", "지급수수료", "감가상각비"),
    "매출수주": ("매출 실적", "사업부문", "매출유형", "품 목", "수주", "합계"),
}
_WEAK_REFERENCE_MARKERS: tuple[str, ...] = (
    "상세 내용은 본문",
    "참조하시기 바랍니다",
    "기재 하였습니다",
)


def find_body_chunk(
    filing_text: str, heads: Iterable[str], frag_chars: int
) -> str:
    """표제를 찾되 **목차가 아닌 첫 덩어리**를 돌려준다.

    Args:
        filing_text: 공시 원문 전체.
        heads: 이 종류의 절 표제 후보들 (1판의 `SECTION_HEADS[종류]`).
        frag_chars: 한 조각의 길이 (1판의 `FRAG_CHARS`).

    Returns:
        본문 덩어리. 못 찾으면 빈 문자열.

    ★ 1판과 다른 점은 **`break`가 없다는 것 하나**다.
      첫 출현이 목차면 다음 출현을 본다. 그게 전부다.
    ⚠️ 표제가 아주 많이 나오는 문서를 대비해 살펴보는 횟수를 제한한다 —
      안 걸면 큰 공시에서 느려진다.
    """
    for head in heads:
        for match in _iter_limited(re.finditer(re.escape(head), filing_text)):
            chunk = filing_text[match.start(): match.start() + frag_chars].strip()
            if len(chunk) <= _MIN_CHUNK_CHARS:
                continue
            if is_table_of_contents(chunk):
                continue          # ★ 목차면 «다음 출현»으로 (1판은 여기서 멈췄다)
            return chunk
    return ""


def _body_score(chunk: str, kind: str) -> int:
    if len(chunk) <= _MIN_CHUNK_CHARS or is_table_of_contents(chunk):
        return -10_000
    head = chunk[:320]
    score = 2 * sum(1 for marker in _KIND_MARKERS.get(kind, ()) if marker in chunk)
    score -= 6 * sum(1 for marker in _WEAK_REFERENCE_MARKERS if marker in head)
    if chunk.count("미영위") >= 5:
        score -= 8
    if re.search(r"(?:제\s*\d+\s*기|20\d{2}년)", chunk):
        score += 1
    return score


def find_best_body_chunk(
    filing_text: str, heads: Iterable[str], frag_chars: int, kind: str
) -> str:
    """모든 제한된 표제 출현 중 종류별 내용 표시가 가장 많은 본문을 고른다."""

    candidates: list[tuple[int, int, str]] = []
    for head in heads:
        for match in _iter_limited(re.finditer(re.escape(head), filing_text)):
            chunk = filing_text[match.start(): match.start() + frag_chars].strip()
            candidates.append((_body_score(chunk, kind), -match.start(), chunk))
    if not candidates:
        return ""
    score, _position, chunk = max(candidates, key=lambda item: (item[0], item[1]))
    return chunk if score > -10_000 else ""


#: 표제 하나당 살펴볼 최대 출현 수.
_MAX_OCCURRENCES = 20


def _iter_limited(matches):
    for index, match in enumerate(matches):
        if index >= _MAX_OCCURRENCES:
            return
        yield match


def repair(
    frags: dict[int, dict[str, Any]],
    filing_text: str,
    section_heads: dict[str, Any],
    frag_chars: int,
) -> tuple[dict[int, dict[str, Any]], int]:
    """1판이 만든 조각 중 «목차»인 것을 본문으로 바꾼다.

    Args:
        frags: 1판 `make_fragments()`가 돌려준 조각들.
        filing_text: 공시 원문 전체.
        section_heads: 1판의 `SECTION_HEADS` (종류 → 표제 목록).
        frag_chars: 1판의 `FRAG_CHARS`.

    Returns:
        (고친 조각들, 고친 개수).

    ★ **못 고치면 원래 것을 그대로 둔다.** 버리지 않는다 —
      목차라도 남겨 두면 뒤의 검사(표 덤프·알맹이)가 걸러 주지만,
      여기서 버리면 「자료가 아예 없다」로 잘못 읽힌다.
    ★ 원본을 바꾸지 않고 «새 dict»를 돌려준다 — 부르는 쪽이 원본을 계속 써도 안전하게.
    """
    # ★ 재료가 없으면 «고치지 않고» 그대로 돌려준다 — 여기서 터지면 조사가 통째로 멈춘다.
    if not filing_text or not section_heads or frag_chars <= 0:
        return dict(frags), 0

    고침 = 0
    out: dict[int, dict[str, Any]] = {}
    for fid, frag in frags.items():
        원문 = frag.get("원문", "")
        종류 = frag.get("종류", "")

        # ① 목차를 담아 왔으면 «다음 출현»에서 본문을 가져온다.
        if is_table_of_contents(원문):
            본문 = find_body_chunk(filing_text, section_heads.get(종류) or (), frag_chars)
            if 본문:
                원문 = 본문
                고침 += 1

        # 목차가 아니어도 요약/교차참조가 실제 본문보다 먼저 나올 수 있다.
        # DART 주요계정 API 줄은 공시 HTML과 다른 원천이므로 교체하지 않는다.
        if not str(원문).startswith("주요계정(DART API):"):
            최선 = find_best_body_chunk(
                filing_text,
                section_heads.get(종류) or (),
                frag_chars,
                str(종류),
            )
            if 최선 and _body_score(최선, str(종류)) >= _body_score(str(원문), str(종류)) + 3:
                원문 = 최선
                고침 += 1

        # ② 법적 면책 문구로 시작하면 «진짜 내용»부터 다시 뜬다.
        #   ★ ①과 «따로» 건다 — 목차를 고친 결과가 면책으로 시작할 수도 있다.
        #     실제로 하이브 MD&A가 그랬다: 목차 → (고침) → 면책 문구.
        내용 = skip_boilerplate(원문, filing_text, frag_chars)
        if 내용:
            원문 = 내용
            고침 += 1

        out[fid] = {**frag, "원문": 원문} if 원문 != frag.get("원문", "") else frag
    return out, 고침


# ══════════════════════════════════════════════════════════
# 정형 면책 문구 건너뛰기
# ══════════════════════════════════════════════════════════
# ★ 무엇이 문제인가 — 「이사의 경영진단 및 분석의견」(MD&A) 절은
#   **거의 항상 「1. 예측정보에 대한 주의사항」으로 시작**한다. 법적 면책 문구다.
#   회사 이름을 바꿔도 그대로 말이 되는 «모든 회사가 똑같이 쓰는 글»이라
#   지원동기 재료로는 아무 값어치가 없다.
#
#   실측 (저장 조각 238개) — **MD&A 조각의 91%(10/11)**가 이 문구로 시작하고,
#   조각의 **24~60%**를 차지한다. MD&A는 4-1(과제)·4-3(미래 방향)의 주 재료라,
#   그 칸들이 만성적으로 비는 큰 원인이었다.
#
# ★ 다행히 경계가 «일관»된다 — 실측 6곳 전부 「2. 개요」로 진짜 내용이 시작한다.

#: 면책 문구임을 알려주는 표시. **두 개 이상** 나와야 면책으로 본다 —
#: 하나만 보면 「예측정보를 활용해…」 같은 정상 문장까지 걸린다.
_BOILERPLATE_MARKS: tuple[str, ...] = (
    "예측정보에 대한 주의사항",
    "작성시점의 사건 및 재무성과",
    "부정확한 것으로 판명",
    "정정보고서를 공시할 의무는 없",
    "유의하시기 바랍니다",
)
_BOILERPLATE_MIN_MARKS = 2

#: 면책이 끝나고 «진짜 내용»이 시작하는 지점.
#: ★ 실측 — 저장된 MD&A 6곳 전부 「2. 개요」였다. 다른 표기도 같이 받는다.
_CONTENT_START_RE = re.compile(
    r"\d+\.\s*(?:개요|사업의\s*개요|재무상태\s*및\s*영업실적|영업실적|경영실적|"
    r"재무상태|당해\s*사업연도)"
)

#: 면책 표시를 찾아볼 범위(글자). 조각 앞부분만 본다 —
#: 뒤쪽에 「유의하시기 바랍니다」가 있다고 면책 조각은 아니다.
_BOILERPLATE_HEAD_CHARS = 900


def starts_with_boilerplate(text: str) -> bool:
    """이 덩어리가 «법적 면책 문구»로 시작하는가.

    Args:
        text: 조각 원문.

    Returns:
        면책 문구로 시작하면 True.

    ★ 표시 «두 개 이상»을 요구한다 — 하나만 보면 정상 문장까지 걸린다.
    """
    head = (text or "")[:_BOILERPLATE_HEAD_CHARS]
    return sum(1 for mark in _BOILERPLATE_MARKS if mark in head) >= _BOILERPLATE_MIN_MARKS


def skip_boilerplate(chunk: str, filing_text: str, frag_chars: int) -> str:
    """면책 문구를 건너뛰고 «진짜 내용»부터 다시 뜬다.

    Args:
        chunk: 1판이 뜬 조각.
        filing_text: 공시 원문 전체.
        frag_chars: 조각 길이.

    Returns:
        진짜 내용부터 시작하는 덩어리. 못 찾으면 빈 문자열(= 안 고침).

    ★ 원문에서 «다시 뜨는» 이유 — 그냥 잘라내기만 하면 남는 글이 짧아진다.
      면책이 60%를 먹은 조각은 480자만 남는다. 원문에서 다시 떠야 온전한 1,200자가 된다.
    ⚠️ 원문에서 위치를 못 찾으면 **잘라내기만** 한다 — 짧아도 없는 것보다 낫다.
    """
    if not starts_with_boilerplate(chunk):
        return ""
    match = _CONTENT_START_RE.search(chunk)
    if match is None:
        return ""
    잘라낸 = chunk[match.start():].strip()
    if not filing_text:
        return 잘라낸
    # 조각의 «절대 위치»를 찾아 거기서 온전한 길이로 다시 뜬다.
    앞머리 = chunk[:60]
    start = filing_text.find(앞머리)
    if start < 0:
        return 잘라낸
    조각시작 = start + match.start()
    다시 = filing_text[조각시작: 조각시작 + frag_chars].strip()
    return 다시 if len(다시) > len(잘라낸) else 잘라낸

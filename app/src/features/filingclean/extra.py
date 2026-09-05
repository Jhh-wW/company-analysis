"""1판이 «안 뜨는» 공시 절을 더 모은다.

★ 왜 필요한가 — 결과가 전자공시 원문을 그대로 옮긴 것과 다를 바 없어 보였다.
  파보니 **정작 필요한 절을 안 뜨고 있었다.**

  실측 (하이브 사업보고서 375,934자, 직접 확인) —
  1판이 뜨는 절 8종에 **아래가 하나도 없다:**

  | 안 뜨던 절 | 실제 내용 |
  |---|---|
  | **신규사업 등의 내용 및 전망** | 「하이브는 **멀티 레이블 전략을 통해서 성장을 지속시켜나갈 계획**입니다. 한국·일본·미국에 걸쳐 15개의 독립 레이블을 운영」 |
  | **미션·비전** | 「음악에 기반한 세계 최고의 엔터테인먼트 라이프스타일 플랫폼 기업을 지향합니다」 |
  | **시장점유율** | 「써클차트에 따르면…」 |
  | **우발부채·소송** | 계류 중인 소송·분쟁 |
  | **핵심감사사항** (나중에 더함) | 감사인이 이름을 걸고 짚은 급소 |
  | **주요 지적재산권** (나중에 더함) | 출원만 해 두고 아직 등록·매출이 없는 것 |

★ 왜 이 절들인가 — 조사 근거
  · 증권사 리포트 9건 중 **8건**이 「앞으로 어디로 가나(신사업·수주·투자계획)」를
    **별도 섹션**으로 다룬다. 우리 4-3이 만성적으로 비던 재료가 바로 이것이다.
  · 실제 합격 자소서 3건이 전부 **「전략 선언·슬로건」**을 근거로 썼다 (숫자가 아니라).
    → 미션·비전.
  · 취업 가이드가 「사업보고서에서 꼭 볼 절」로 **우발부채(법적 리스크)**를 지목했다.
  · 증권사 리포트 7/9가 **산업·시장 맥락**을 다룬다 → 시장점유율.
  · 3장 이름 카드는 제품·상품·자회사 이름이 필요하지만, 1판은 그 표제를 뜨지 않는다.
    이름은 표의 행에 있으므로 이 세 절만 숫자 표여도 보존한다.

★ **1판은 0줄 고치지 않는다.** 1판이 만든 조각에 «더할» 뿐이다.
"""

from __future__ import annotations

import re
from typing import Any, Final

from src.core.docshape import is_table_of_contents
from src.features.filingclean.logic import starts_with_boilerplate
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_AUDITOR_FINDING,
    LEGACY_KIND_GOODS_CONTENT,
    LEGACY_KIND_INTELLECTUAL_PROPERTY,
    LEGACY_KIND_LITIGATION,
    LEGACY_KIND_MARKET_SHARE,
    LEGACY_KIND_NEW_BUSINESS_OUTLOOK,
    LEGACY_KIND_PRODUCT_SERVICE,
    LEGACY_KIND_RISK_FACTOR,
    LEGACY_KIND_SUBSIDIARY_BUSINESS,
)

#: 새로 뜰 절 — {조각 종류: (찾을 표제들…)}.
#: ★ 표제는 «여러 개» 둔다. 회사마다 표기가 다르다.
#: ⚠️ 늘릴 때마다 실측으로 «무엇이 실제로 뜨는지» 확인할 것 —
#:   이름 표 3종을 빼면 표제만 맞고 내용이 표·목차일 때 아무 값어치가 없다.
EXTRA_SECTION_HEADS: Final[dict[str, tuple[str, ...]]] = {
    # 4-3(앞으로 어디로 가나)의 «주 재료». 증권사 리포트 8/9가 별도 섹션으로 다룬다.
    LEGACY_KIND_NEW_BUSINESS_OUTLOOK: (
        "신규사업 등의 내용 및 전망",
        "신규사업등의 내용 및 전망",
        "신규사업의 내용 및 전망",
        "신규 사업의 내용",
        "향후 추진하려는 신규사업",
    ),
    # ⚠️ 「미션·비전」은 «별도 절로 안 뜬다» — 실측으로 확인했다.
    #   하이브의 「"We believe in music"이라는 미션 아래…」는 **「사업의 개요」 안에**
    #   묻혀 있고, 「경영방침」·「중장기 전략」 같은 표제는 **0회** 나온다.
    #   → 표제로 뜰 수 없다. 억지로 넣으면 「사업내용」과 겹치는 조각만 늘어난다.
    #   실제 합격 자소서가 「전략 선언」을 근거로 쓴다는 조사 결과는 유효하므로,
    #   그 재료는 **홈페이지(vision·ir 페이지)**에서 가져오는 것이 맞다.
    # 산업 맥락 — 「이 회사가 업계 어디쯤인가」.
    LEGACY_KIND_MARKET_SHARE: (
        "시장점유율",
        "시장 점유율",
        "시장의 특성",
        "경쟁요소",
        "경쟁 요소",
    ),
    # 4-1(지금 뭐가 문제인가)의 재료. 취업 가이드가 「꼭 볼 절」로 지목.
    LEGACY_KIND_LITIGATION: (
        "계류 중인 소송사건",
        "계류중인 소송사건",
        "제재현황",
        "우발채무 등",
        "그 밖의 우발채무",
    ),
    # ★ 사용자 선택 ⑤ — 「무엇이 잘못될 수 있나」.
    #   근거: 증권사 리포트 원문 11건 중 **9건**이 리스크를 별도로 세운다.
    #   면접에서 「우리 회사 어려움이 뭐라고 보나요」가 나온다.
    #   ⚠️ 4-1(당면 과제)과 다르다 — 4-1은 «이미 벌어진 일», 여기는 «벌어질 수 있는 일»이다.
    #
    # ⚠️⚠️ **기대를 낮춰야 한다 — 실측 결과 (하이브 사업보고서)**
    #   「사업위험」 **0회** · 「위험요소」 **0회** · 「투자위험요소」 **0회**.
    #   그 절은 «증권신고서»에 있지 «사업보고서»에는 없다.
    #   실제로 뜨는 것은 「위험관리」뿐이고, 내용은 **재무제표 주석의 금융위험**이다
    #   (시장위험·신용위험·유동성위험). 취준생에게 값이 낮다.
    #   → **⑤의 실질적인 재료는 「소송·분쟁」(바로 위)과 뉴스 「과제」 딱지**다.
    #   그래도 남겨 둔다 — 회사에 따라 「사업위험」을 쓰는 곳이 있고, 없으면
    #   `collect()`가 알아서 빼므로 빈 조각이 생기지 않는다.
    # ★ 추가 — 5장(당면 과제)의 «근거를 올리기» 위한 절.
    #   지금 5장의 근거는 뉴스 추측이 섞인다. 감사인이 «감사보고서에 이름을 걸고»
    #   짚은 항목은 회사 홍보문구에 존재하지 않고, 회사가 부인할 수도 없다.
    #
    #   실측 (공시 원문 2건 직접 확인) —
    #   · ㈜진영 2025 사업보고서(본문 167,830자):
    #       「핵심감사사항 제30기(당기) … 적정의견 … **재고자산의 평가**」
    #   · 다른 대기업 사업보고서(본문 715,332자):
    #       「**건설중인자산의 감가상각개시시점 평가** / **재화의 판매장려활동에 대한
    #        매출차감의 정확성과 완전성**」
    #   둘 다 앞 400자에 금액꼴 숫자가 0~1개라 표 판정에 안 걸리고 그대로 떴다.
    #
    # ⚠️ 「회계감사인의 명칭 및 감사의견」은 **일부러 안 넣었다** — 그 표제는 곧바로
    #   감사인·의견 표로 이어져 `_looks_like_table()`에 걸리거나, 걸리지 않아도
    #   「한미회계법인 적정의견」만 남아 지원동기에 쓸 것이 없다.
    LEGACY_KIND_AUDITOR_FINDING: (
        "핵심감사사항",
        "핵심감사사항(Key Audit Matters)",
        "핵심감사사항(Key audit matters)",
        "계속기업 관련 중요한 불확실성",
        "계속기업가정",
        "강조사항",
    ),
    # ★ 추가 — 3장(핵심 제품)의 «아직 안 파는 것» 재료.
    #   합격 자소서에서 가장 많이 쓰인 재료가 제품 지목(53.6%)인데, 다들 홈페이지에
    #   있는 «현재» 제품을 쓴다. 출원만 해 둔 상표·특허는 홈페이지에도 뉴스에도 없다.
    #
    #   실측 —
    #   · ㈜진영: 「번호 구분 내용 권리자 **출원일 등록일** 적용제품 출원국 **현재상태**」
    #     → 「등록일이 비어 있고 현재상태가 출원완료인 행」을 그대로 뽑을 수 있는 모양이다.
    #   · 대기업: 표가 아니라 서술문(「…향후 활용될 예정이며…」)으로 나온다.
    #   즉 회사에 따라 «표»로도 «문장»으로도 온다. 둘 다 재료가 된다.
    #
    # ⚠️ 날짜(2004.08.30)는 `_MONEY_RE`(1,234 꼴)에 안 걸려서 표 판정을 통과한다.
    #   이 표는 «버리면 안 되는» 표라 다행이지만, 판정 규칙을 바꿀 때 여기를 같이 봐야 한다.
    #
    # ⚠️⚠️ **「산업재산권」은 일부러 뺐다 — 회계 주석을 오탐한다.**
    #   실측 (회사 20곳 사업보고서 직접 조회) — 「산업재산권」으로 뜬 8곳이
    #   전부 **무형자산 장부금액 표**였다:
    #     삼성전자 「산업재산권 회원권 기타 … 4,789,366 272,898 11,507,761 …」
    #     NAVER   「산업재산권 장부금액 취득원가 11,951,314 상각누계액 (10,468,580) …」
    #   지원동기에 쓸 것이 하나도 없는 숫자 표다. 「지적재산권」 계열 표제만 남긴다.
    #
    # ★ 실재율: **8/20 (40%)** — 절반 이하다. 없는 회사가 더 많으므로
    #   이 재료에 기대는 칸은 «있을 때만 뜨는» 설계여야 한다.
    #   쓸 만하게 뜬 곳: 카카오·셀트리온·한화에어로·하이브·삼성바이오·로보스타·야놀자·㈜진영
    #   (로보스타는 「총출원 건수 / 총등록 건수 / **미등록 건수** / 미등록사유」까지 준다)
    LEGACY_KIND_INTELLECTUAL_PROPERTY: (
        "주요 지적재산권 보유 현황",
        "지적재산권 보유 현황",
        "지적재산권 보유현황",
        "지적재산권 현황",
        "지적재산권 보유",
        "지적재산권",
    ),
    # 3장 이름 카드의 직접 재료. 제품명·브랜드명 표는 숫자 표여도 보존한다.
    LEGACY_KIND_PRODUCT_SERVICE: (
        "주요 제품 및 서비스",
        "주요 제품 등의 현황",
        "주요 제품·서비스",
        "주요 제품 및 서비스 현황",
    ),
    # 금융업은 「제품」 대신 「상품」 표제를 쓴다.
    LEGACY_KIND_GOODS_CONTENT: (
        "주요 상품 및 서비스의 내용",
        "주요 상품 및 서비스",
    ),
    # 법인 목록 자체가 아니라 자회사 이름과 실제 사업을 함께 주는 표만 받는다.
    LEGACY_KIND_SUBSIDIARY_BUSINESS: (
        "주요 종속회사의 업종 및 주요 사업",
        "주요 종속회사",
        "종속기업 현황",
    ),
    LEGACY_KIND_RISK_FACTOR: (
        "사업위험",
        "사업 위험",
        "회사위험",
        "회사 위험",
        "투자위험요소",
        "위험관리",
        "리스크 관리",
        "시장위험과 위험관리",
    ),
    # ⚠️ **참조 안내뿐인 「계열·조직」은 여전히 넣지 않는다** — 실측으로 확인했다.
    #   「계열회사의 현황」·「종속회사 현황」·「조직도」를 다 걸어 봤을 때
    #   하이브 사업보고서에서 실제로 나온 것은 이것뿐이었다:
    #     「계열회사의 현황은 '상세표-2. 계열회사 현황(상세)'를 **참조하시기 바랍니다**.
    #      … 해당사항 없습니다. 라. 회사와 계열회사간 임원 겸직 현황」
    #   **알맹이가 아니라 «다른 표를 보라»는 안내문**이다. 이런 표제는 계속 제외한다.
    #   반면 이번에는 3장 이름 카드에 자회사 이름이 필요하므로, 위의
    #   「주요 종속회사의 업종 및 주요 사업」처럼 이름과 사업을 함께 주는 절만 받는다.
    #
    #   ★ 대신 ⑦의 실질적인 답은 **제품·서비스별 매출 표**가 준다 —
    #     「음반/음원 29% · 공연 29% · MD 22% · 콘텐츠 10%」를 보면
    #     **이 회사에 어떤 부문이 있고 어디가 큰지**가 한눈에 보인다.
    #     법인 목록보다 지원자에게 값진 정보다.
}

#: 기존 추가 절 하나의 길이(글자). 1판의 `FRAG_CHARS`와 같은 값을 기본으로 쓴다.
#: 이름 표 3종만 행 이름을 보존하기 위해 아래 전용 상한까지 늘린다.
DEFAULT_FRAG_CHARS: Final[int] = 1200

#: 조각으로 인정할 최소 길이. 1판과 같은 기준(200자).
MIN_CHUNK_CHARS: Final[int] = 200

#: 표제 하나당 살펴볼 최대 출현 수 (큰 공시에서 느려지지 않게).
MAX_OCCURRENCES: Final[int] = 20

#: 표 신호 — 이만큼 이상이면 「문장이 아니라 표」로 보고 건너뛴다.
#: ★ 새 절을 뜨는 김에 표까지 담아 오면 예전에 겪은 일이 되풀이된다.
_MONEY_RE: Final[re.Pattern[str]] = re.compile(r"\d{1,3}(?:,\d{3})+")
_TABLE_NUMBER_MIN: Final[int] = 6

#: 표인지 볼 때 «앞부분만» 본다 (글자 수).
#: ★ 왜 앞만 보나 — 공시 절은 «설명 문장 → 표» 순서로 이어지는 일이 흔하다.
#:   1,200자를 통째로 세면 뒤쪽 표의 숫자 때문에 **앞의 좋은 문장까지 버린다.**
#:   실측 — 하이브 「신규사업 등의 내용 및 전망」이 정확히 그랬다:
#:     앞: 「하이브는 멀티 레이블 전략을 통해서 성장을 지속시켜나갈 계획입니다…」 ← 4-3의 핵심 재료
#:     뒤: 표 (숫자 12개) → 통째로 «표»로 오판돼 버려졌다
#:   목차 판정(`docshape.TOC_HEAD_CHARS`)에서 배운 것과 **같은 이유·같은 해법**이다.
_TABLE_HEAD_CHARS: Final[int] = 400

#: 제품·상품·자회사 이름 표는 긴 행을 보존하되 이 상한을 넘기지 않는다.
NAME_TABLE_MAX_FRAG_CHARS: Final[int] = 3000

#: 표 자체가 3장 이름 카드의 근거이므로 숫자 표 판정에서 제외할 종류.
NAME_TABLE_SECTION_KINDS: Final[frozenset[str]] = frozenset(
    {
        LEGACY_KIND_PRODUCT_SERVICE,
        LEGACY_KIND_GOODS_CONTENT,
        LEGACY_KIND_SUBSIDIARY_BUSINESS,
    }
)

#: 태그를 지우고 공백으로 뭉친 DART 원문에서 다음 번호·한글 소절 표제를 찾는다.
_NEXT_SECTION_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\S)(?P<label>(?:[IVXLC]+|\d+(?:-\d+)*|[가-하])\.)\s+(?P<title>\S)"
)


def _looks_like_table(text: str) -> bool:
    """이 덩어리가 «표로 시작하는가» (새 조각을 받을지 판단용).

    ★ 뒤에 표가 붙어 있어도 **앞이 문장이면 받는다** — 쓸 문장이 앞에 있기 때문이다.
    """
    return len(_MONEY_RE.findall(text[:_TABLE_HEAD_CHARS])) >= _TABLE_NUMBER_MIN


def _chunk_until_next_heading(
    filing_text: str,
    *,
    start: int,
    limit: int,
    heads: tuple[str, ...],
) -> str:
    """현재 종류의 하위 표제는 넘기고 다음 절 표제 앞에서 자른다."""
    end = min(len(filing_text), start + limit)
    for match in _NEXT_SECTION_HEADING_RE.finditer(filing_text, start, end):
        title_start = match.start("title")
        if any(filing_text.startswith(head, title_start) for head in heads):
            continue
        end = match.start()
        break
    return filing_text[start:end].strip()


def find_section(
    filing_text: str,
    heads: tuple[str, ...],
    frag_chars: int,
    *,
    keep_table: bool = False,
    stop_at_next_heading: bool = False,
) -> str:
    """표제를 찾아 «쓸 만한» 본문 덩어리를 돌려준다.

    Args:
        filing_text: 공시 원문 전체.
        heads: 찾을 표제 후보들.
        frag_chars: 조각 길이.
        keep_table: 숫자 표로 시작해도 보존하는가.
        stop_at_next_heading: 길이 상한보다 먼저 다음 표제가 나오면 거기서 자르는가.

    Returns:
        본문 덩어리. 못 찾으면 빈 문자열.

    ★ 기본으로 세 가지를 건너뛴다 — **목차 · 법적 면책 문구 · 표**.
      예전에 겪은 것을 여기서 «처음부터» 막는다.
      새 절을 뜨면서 같은 실수를 반복할 이유가 없다.
      다만 이름 표 3종은 표의 행 자체가 재료라 호출자가 ``keep_table``로 보존한다.
    """
    for head in heads:
        for index, match in enumerate(re.finditer(re.escape(head), filing_text)):
            if index >= MAX_OCCURRENCES:
                break
            if stop_at_next_heading:
                chunk = _chunk_until_next_heading(
                    filing_text,
                    start=match.start(),
                    limit=frag_chars,
                    heads=heads,
                )
            else:
                chunk = filing_text[match.start(): match.start() + frag_chars].strip()
            if len(chunk) <= MIN_CHUNK_CHARS:
                continue
            if is_table_of_contents(chunk):
                continue          # 목차 — 다음 출현으로
            if starts_with_boilerplate(chunk):
                continue          # 법적 면책 문구
            if not keep_table and _looks_like_table(chunk):
                continue          # 일반 절의 숫자 나열은 자소서에 못 쓴다
            return chunk
    return ""


def collect(
    filing_text: str, frag_chars: int = DEFAULT_FRAG_CHARS
) -> dict[str, str]:
    """안 뜨던 절들을 모은다.

    Args:
        filing_text: 공시 원문 전체.
        frag_chars: 기존 추가 절의 조각 길이. 이름 표 3종은 전용 상한을 쓴다.

    Returns:
        {조각 종류: 원문}. 못 찾은 종류는 «아예 안 담는다**.

    ★ 못 찾으면 빈 문자열을 담지 않고 «빼 버린다» — 빈 조각이 들어가면
      「재료가 있는데 안 쓰였다」로 잘못 읽힌다.
    """
    if not filing_text or frag_chars <= 0:
        return {}
    out: dict[str, str] = {}
    for kind, heads in EXTRA_SECTION_HEADS.items():
        is_name_table = kind in NAME_TABLE_SECTION_KINDS
        chunk = find_section(
            filing_text,
            heads,
            NAME_TABLE_MAX_FRAG_CHARS if is_name_table else frag_chars,
            keep_table=is_name_table,
            stop_at_next_heading=is_name_table,
        )
        if chunk:
            out[kind] = chunk
    return out


def add_to(
    frags: dict[int, dict[str, Any]],
    filing_text: str,
    frag_chars: int = DEFAULT_FRAG_CHARS,
) -> tuple[dict[int, dict[str, Any]], int]:
    """1판이 만든 조각에 «새 절»을 더한다.

    Args:
        frags: 1판 `make_fragments()` 결과 (이미 앞선 보정을 거친 것).
        filing_text: 공시 원문 전체.
        frag_chars: 기존 추가 절의 조각 길이. 이름 표 3종은 전용 상한을 쓴다.

    Returns:
        (합친 조각들, 더한 개수).

    ★ 원본을 바꾸지 않고 «새 dict»를 돌려준다.
    ★ **이미 같은 종류가 있으면 안 더한다** — 1판이 이미 뜬 것을 덮어쓰지 않는다.
    """
    있는종류 = {f.get("종류", "") for f in frags.values()}
    out = dict(frags)
    next_id = max(frags, default=0) + 1
    더함 = 0
    for kind, chunk in collect(filing_text, frag_chars).items():
        if kind in 있는종류:
            continue
        out[next_id] = {"종류": kind, "원문": chunk}
        next_id += 1
        더함 += 1
    return out, 더함

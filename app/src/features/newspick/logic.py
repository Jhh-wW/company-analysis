"""뉴스를 «AI가 번호로» 고른다.

★ 정본과 같은 방식이다 — **AI는 번호를 고르고, 원문 복사는 프로그램이 한다.**
  기사 제목·요약은 한 글자도 안 바뀐 채로 조각이 된다.

★ 이 파일의 대부분은 **순수 함수**다 (시계·네트워크·AI 없음).
  AI를 부르는 것은 맨 아래 `pick_with_ai` 하나뿐이라 나머지는 전부 시험할 수 있다.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.features.newspick.constants import (
    CEO_SLOT,
    EXEC_QUOTE_KIND,
    EXEC_QUOTE_MAX,
    FRAGMENT_KIND,
    FRAGMENT_PREFIX,
    MAX_CANDIDATES,
    MAX_PICKED,
    MAX_YEARS,
    OTHER_CORP_MAX,
    PROMPT_HEADER,
    PROMPT_LIST_HEAD,
    PROMPT_PROFILE_HEAD,
    PROMPT_RULES,
    PROMPT_TAIL,
    UNKNOWN_PRESS,
    USE_KIND_DESC,
    USE_KINDS,
)

#: 「㈜○○」 꼴로 적힌 «다른» 회사를 세는 자 — 1판과 같은 규칙이다.
_OTHER_CORP_RE = re.compile(
    r"(?:㈜|\(주\)|주식회사)\s*([가-힣A-Za-z0-9&]{2,15})"
    r"|([가-힣A-Za-z0-9&]{2,15})\s*(?:㈜|\(주\))"
)

#: 회사 이름 뒤에 붙어 딸려 오는 조사 — 떼고 비교한다.
#: ★ 왜 필요한가 — 위 규칙은 「㈜하이브**가**」에서 「하이브가」를 통째로 집는다.
#:   그대로 비교하면 「하이브가 ≠ 하이브」라서 **자기 회사가 「다른 회사」로 세어진다.**
#:   그러면 나열 기사 문턱(3곳)에 실제보다 빨리 닿아 멀쩡한 기사를 버린다.
_TRAILING_PARTICLES: tuple[str, ...] = (
    "가", "는", "은", "이", "의", "도", "를", "을", "와", "과", "에", "로", "으로",
)


@dataclass(frozen=True)
class Candidate:
    """AI 앞에 올릴 기사 하나. **원문 그대로** 담는다."""

    number: int          #: 프롬프트에 찍히는 번호 (1부터)
    title: str
    body: str
    published: Optional[dt.date]
    press: str
    #: 원 기사 주소. 옛 저장·시험 생성자는 비워도 되지만 신규 수집은 반드시 보존한다.
    url: str = ""


@dataclass(frozen=True)
class Picked:
    """AI가 고른 기사 하나."""

    candidate: Candidate
    use_kind: str        #: 과제·성과·전략


# ══════════════════════════════════════════════════════════
# ① 사전 걸러내기 — 코드가 한다 (AI 앞에 가기 전)
# ══════════════════════════════════════════════════════════


def press_of(original_link: str, link: str) -> str:
    """기사 주소에서 매체를 알아낸다.

    ★ 네이버 API가 매체 이름을 안 줘서 «원 기사 도메인»으로 대신한다 —
      1판이 쓰던 방법 그대로다.
    """
    for url in (original_link, link):
        if not url:
            continue
        host = urllib.parse.urlparse(url).netloc
        if host:
            return host
    return UNKNOWN_PRESS


def _strip_particle(name: str) -> str:
    """이름 뒤에 붙은 조사를 뗀다 — 「하이브가」 → 「하이브」."""
    for 조사 in sorted(_TRAILING_PARTICLES, key=len, reverse=True):
        if len(name) > len(조사) + 1 and name.endswith(조사):
            return name[: -len(조사)]
    return name


def count_other_corps(text: str, company: str) -> int:
    """이 글에 «다른» 회사가 몇 곳 나오나.

    Args:
        text: 제목 + 본문 요약.
        company: 이 보고서의 회사 — **이 회사는 세지 않는다.**

    Returns:
        다른 회사 수.

    ★ 조사를 떼고 비교한다. 안 떼면 「㈜하이브가」가 «다른 회사»로 세어져
      나열 기사 문턱에 실제보다 빨리 닿고, **멀쩡한 기사를 버린다.**
    """
    found = set()
    for match in _OTHER_CORP_RE.findall(text):
        for name in match:
            if not name:
                continue
            깨끗한이름 = _strip_particle(name)
            if 깨끗한이름 in company or company in 깨끗한이름:
                continue          # 이 보고서의 회사다
            found.add(깨끗한이름)
    return len(found)


def _dedupe_key(item: Any) -> str:
    """같은 기사인지 가릴 열쇠.

    ★ 같은 기사가 매체마다 다른 주소로 올라온다. **제목**으로 가른다 —
      주소로 가르면 같은 기사가 5번 후보에 오른다 (실측: 캣츠아이 기사 4중복).
    """
    title = (getattr(item, "title", "") or "").strip()
    return re.sub(r"[\s'\"‘’“”·,.\-…]", "", title)


def interleave(groups: list[list[Any]], limit: int) -> list[Any]:
    """여러 검색 결과를 «번갈아» 섞는다.

    Args:
        groups: 검색어별 결과 목록.
        limit: 최대 개수.

    Returns:
        섞은 목록 (같은 기사는 한 번만).

    ★ 왜 번갈아 넣나 — 앞에서부터 이어 붙이면 **첫 검색어가 자리를 다 차지한다.**
      실측 — 회사 이름만으로 최신순 검색하면 20건이 전부 그날 기사라
      「실적」·「신사업」 검색 결과가 후보에 한 건도 못 들어간다.
    """
    out: list[Any] = []
    seen: set[str] = set()
    깊이 = max((len(g) for g in groups), default=0)
    for i in range(깊이):
        for group in groups:
            if i >= len(group):
                continue
            key = _dedupe_key(group[i])
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(group[i])
            if len(out) >= limit:
                return out
    return out


def prefilter(
    items: list[Any],
    *,
    company: str,
    today: dt.date,
    max_years: int = MAX_YEARS,
    other_corp_max: int = OTHER_CORP_MAX,
    limit: int = MAX_CANDIDATES,
) -> tuple[list[Candidate], dict[str, int]]:
    """AI 앞에 올릴 후보를 고른다. **날짜와 나열 기사만** 본다.

    Args:
        items: 검색 결과 (`title`·`description`·`pub_date`·`link`·`originallink`).
        company: 회사 이름.
        today: 오늘.
        max_years: 몇 년 이내만 받을지.
        other_corp_max: 다른 회사가 몇 곳부터 「나열 기사」인지.
        limit: 최대 후보 수.

    Returns:
        (후보 목록, 버린 사유별 개수).

    ★ **회사 이름을 여기서 안 본다.** 그게 이번 변경의 핵심이다 —
      이름 맞추기로는 「BTS」 기사가 하이브 기사인 걸 알 수 없다.
      그 판단은 AI가 한다.
    ⚠️ 날짜·나열 기사는 «싸고 확실한» 거절이라 AI에게 맡기지 않는다.
      후보가 줄면 프롬프트가 짧아져 값도 싸진다.
    """
    out: list[Candidate] = []
    dropped = {"날짜없음": 0, "오래됨": 0, "나열기사": 0}
    for item in items:
        published = getattr(item, "pub_date", None)
        if published is None:
            dropped["날짜없음"] += 1
            continue
        if (today - published).days > max_years * 365:
            dropped["오래됨"] += 1
            continue
        title = getattr(item, "title", "") or ""
        body = getattr(item, "description", "") or ""
        if count_other_corps(f"{title} {body}", company) >= other_corp_max:
            dropped["나열기사"] += 1
            continue
        out.append(
            Candidate(
                number=len(out) + 1,
                title=title,
                body=body,
                published=published,
                press=press_of(
                    getattr(item, "originallink", "") or "",
                    getattr(item, "link", "") or "",
                ),
                url=(
                    getattr(item, "originallink", "")
                    or getattr(item, "link", "")
                    or ""
                ),
            )
        )
        if len(out) >= limit:
            break
    return out, dropped


# ══════════════════════════════════════════════════════════
# ② 지시문 만들기
# ══════════════════════════════════════════════════════════


#: 대표자 이름 뒤에 붙는 «괄호 설명». 여는 괄호부터 끝까지 통째로 뗀다.
#: 전자공시가 「서대표 (Seo Daepyo Sample)」·「오대표(각자 대표이사)」처럼
#: 이름 뒤에 영문명이나 직함을 붙여 주는 경우가 있다.
_CEO_PAREN = re.compile(r"[(（].*$")


def ceo_name(profile: dict[str, Any]) -> str:
    """기업개황에서 대표자 «한 사람»의 이름을 뽑는다.

    ★ 공동대표면 「김대표, 이대표」처럼 쉼표로 이어져 온다. 검색어에는
      한 사람만 넣는다 — 두 이름을 다 넣으면 둘 다 나온 기사만 걸린다.

    ★ 괄호 설명도 뗀다 (실측). 대기업·비상장 30곳을 전자공시에
      직접 물어 보니 대표자 이름은 «30곳 전부» 있었지만, 한 곳은
      「서대표 (Seo Daepyo Sample)」꼴로 와서 **쉼표가 없어 split(",")에 안 걸렸다.**
      그대로 두면 검색어가 「삼성바이오로직스 서대표 (Seo Daepyo Sample) 인터뷰」가 되어
      기사가 안 걸린다 — 그러면 「대표가 직접 한 말」이 보고서에서 통째로 빠진다.
      그 말은 «공시에 없는 말»이라 자소서에 쓰면 진짜 찾아봤다는 증거가 되는 재료다.
    """
    첫사람 = (profile.get("ceo_nm") or "").split(",")[0]
    return _CEO_PAREN.sub("", 첫사람).strip()


def search_terms(
    company: str, profile: dict[str, Any], queries: tuple[tuple[str, str, int], ...]
) -> list[tuple[str, str, int]]:
    """검색어 목록을 만든다. 대표자 이름 자리를 채운다.

    Args:
        company: 회사 이름.
        profile: 기업개황.
        queries: (꼬리말, 정렬, 개수) 목록. 꼬리말에 `{ceo}`가 있을 수 있다.

    Returns:
        (완성된 검색어, 정렬, 개수) 목록.

    ★ **대표자 이름이 없으면 그 검색은 통째로 뺀다.** 빈칸으로 검색하면
      「하이브 인터뷰」가 되어 엉뚱한 회사 기사가 그대로 들어온다 (실측).
    """
    ceo = ceo_name(profile)
    out: list[tuple[str, str, int]] = []
    for 꼬리, 정렬, 개수 in queries:
        if CEO_SLOT in 꼬리:
            if not ceo:
                continue
            꼬리 = 꼬리.replace(CEO_SLOT, ceo)
        out.append((f"{company} {꼬리}".strip(), 정렬, 개수))
    return out


def profile_lines(profile: dict[str, Any]) -> list[str]:
    """기업개황에서 «회사를 알아볼 단서»만 뽑는다.

    ★ 이게 있어야 동명 타사를 가른다. AI에게 「이 회사를 알고 있어라」고
      요구하지 않는다 — 모르는 회사면 지어낼 위험이 있기 때문이다.
    """
    쓸것 = (
        ("업종", "induty_code_nm"),
        ("대표자", "ceo_nm"),
        ("주소", "adres"),
        ("설립일", "est_dt"),
    )
    out = []
    for 이름, 열쇠 in 쓸것:
        값 = str(profile.get(열쇠) or "").strip()
        if 값:
            out.append(f"· {이름}: {값}")
    return out


def build_prompt(
    company: str,
    profile: dict[str, Any],
    candidates: list[Candidate],
    limit: int = MAX_PICKED,
) -> str:
    """AI에게 줄 지시문. **고르기만** 시킨다."""
    부분 = [PROMPT_HEADER.format(company=company)]
    단서 = profile_lines(profile)
    if 단서:
        부분.append(PROMPT_PROFILE_HEAD + "\n".join(단서) + "\n")
    부분.append(PROMPT_RULES)
    부분.append("   쓰임새:\n" + "\n".join(
        f"   · {kind} — {USE_KIND_DESC[kind]}" for kind in USE_KINDS
    ) + "\n")
    부분.append(PROMPT_LIST_HEAD)
    for c in candidates:
        날짜 = c.published.isoformat() if c.published else "날짜미상"
        부분.append(f"[{c.number}] ({날짜}) {c.title}\n    {c.body}\n")
    부분.append(PROMPT_TAIL.format(limit=limit))
    return "".join(부분)


def answer_schema() -> dict[str, Any]:
    """AI 답의 모양 — {번호, 쓰임새} 목록.

    ★ **글자를 넣을 자리가 없다.** 번호와 정해진 딱지뿐이라
      지어낼 여지가 구조로 막혀 있다.
    """
    return {
        "type": "object",
        "properties": {
            "고른기사": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "번호": {"type": "integer"},
                        "쓰임새": {"type": "string", "enum": list(USE_KINDS)},
                    },
                    "required": ["번호", "쓰임새"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["고른기사"],
        "additionalProperties": False,
    }


# ══════════════════════════════════════════════════════════
# ③ 답 받아 조각 만들기 — 프로그램이 원문을 복사한다
# ══════════════════════════════════════════════════════════


#: 한글 한 글자.
_HANGUL_CHAR = re.compile(r"[가-힣]")


def _word_from(text: str, start: int, company: str) -> str:
    """회사 이름이 붙어 있는 «한 낱말»을 통째로 떼어 온다.

    「…제작사 하이브미디어코프의 현대사…」 에서 **「하이브미디어코프의」**를 준다.
    """
    end = start + len(company)
    while end < len(text) and _HANGUL_CHAR.match(text[end]):
        end += 1
    return text[start:end]


def _strip_suffixes(word: str, company: str) -> str:
    """낱말 뒤에 붙은 조사·꼬리말을 벗긴다 — 「하이브측은」 → 「하이브」."""
    from src.features.newspick.constants import NAME_SUFFIXES

    changed = True
    while changed and len(word) > len(company):
        changed = False
        for 꼬리 in sorted(NAME_SUFFIXES, key=len, reverse=True):
            if word.endswith(꼬리) and len(word) - len(꼬리) >= len(company):
                word = word[: -len(꼬리)]
                changed = True
                break
    return word


def looks_like_other_company(text: str, company: str) -> bool:
    """이 글에 나온 「회사 이름」이 사실은 **다른 회사**인가.

    Args:
        text: 기사 제목 + 본문 요약.
        company: 이 보고서의 회사 이름.

    Returns:
        회사 이름이 나오긴 하는데 **전부 더 긴 다른 회사 이름의 일부**면 True.

    ★ 왜 프로그램이 하나 — AI가 지시문으로 막아도 계속 「하이브미디어코프」(영화
      제작사)를 하이브 기사로 골랐다 (실측). **이름이 같은지는 «판단»이 아니라
      «확인»이라 코드가 하는 편이 확실하다.**

    ★ **이름이 아예 안 나오면 False다** — 그게 이번 변경의 핵심이다.
      「BTS 신곡」처럼 브랜드로만 난 기사를 여기서 되레 막으면 안 된다.
      그 판단은 AI가 이미 했다.
    """
    이름 = (company or "").strip()
    if not 이름 or 이름 not in text:
        return False
    for match in re.finditer(re.escape(이름), text):
        낱말 = _word_from(text, match.start(), 이름)
        if _strip_suffixes(낱말, 이름) == 이름:
            return False          # 이 회사를 제대로 가리키는 자리가 하나라도 있다
    return True


def apply_picks(
    candidates: list[Candidate],
    payload: Optional[dict[str, Any]],
    company: str,
    limit: int = MAX_PICKED,
) -> tuple[list[Picked], dict[str, int]]:
    """AI 답에서 «번호»를 받아 원문을 찾아 온다.

    Args:
        candidates: AI에게 보여 준 후보들.
        payload: AI 답 (`None`이면 호출 실패).
        company: 회사 이름 — 「이름 비슷한 다른 회사」를 거부하는 데 쓴다.
        limit: 채택 상한.

    Returns:
        (고른 기사들, 버린 사유별 개수).

    ★ 없는 번호·중복 번호는 **버린다.** 정본 W2와 같은 방식이다.
    ★ 원문은 후보에서 «그대로» 가져온다 — AI가 준 글자는 하나도 안 쓴다.
    ★ **AI가 골라도 프로그램이 거부할 수 있다** — 이름이 더 긴 다른 회사
      이름의 일부일 때다. 판단은 AI가, 확인은 코드가 한다.
    """
    버림 = {"모르는번호": 0, "중복": 0, "이름다른회사": 0}
    if not payload:
        return [], 버림
    번호표 = {c.number: c for c in candidates}
    out: list[Picked] = []
    본것: set[int] = set()
    for item in payload.get("고른기사") or []:
        번호 = item.get("번호")
        쓰임새 = item.get("쓰임새")
        if 번호 not in 번호표 or 쓰임새 not in USE_KINDS:
            버림["모르는번호"] += 1
            continue
        if 번호 in 본것:
            버림["중복"] += 1
            continue
        본것.add(번호)
        후보 = 번호표[번호]
        if looks_like_other_company(f"{후보.title} {후보.body}", company):
            버림["이름다른회사"] += 1
            continue
        out.append(Picked(candidate=후보, use_kind=쓰임새))
        if len(out) >= limit:
            break
    return keep_latest_exec_quote(out), 버림


def keep_latest_exec_quote(picked: list[Picked]) -> list[Picked]:
    """경영진 발언 기사는 **가장 최신 것 하나만** 남긴다.

    ★ 제품 결정: 여러 건이면 가장 최신 것 하나만 쓴다.
    ⚠️ 왜 하나만 — 같은 대표가 여러 자리에서 비슷한 말을 한다. 다 실으면
      보고서가 «같은 이야기 반복»이 되고, 정작 다른 재료가 밀려난다.
    ★ 나머지 딱지의 «순서»는 건드리지 않는다 — 순서를 흔들면 앞뒤가 안 맞는다.
    """
    발언 = [p for p in picked if p.use_kind == EXEC_QUOTE_KIND]
    if len(발언) <= EXEC_QUOTE_MAX:
        return picked
    최신 = sorted(
        발언,
        key=lambda p: p.candidate.published or dt.date.min,
        reverse=True,
    )[:EXEC_QUOTE_MAX]
    남길것 = set(id(p) for p in 최신)
    return [
        p for p in picked
        if p.use_kind != EXEC_QUOTE_KIND or id(p) in 남길것
    ]


def to_fragments(picked: list[Picked]) -> list[dict[str, str]]:
    """고른 기사를 조각으로 바꾼다. 1판과 «같은 모양»이다.

    ★ 모양이 다르면 뒤쪽(문장 고르기·출처 각주)이 뉴스를 못 알아본다.
    """
    out = []
    for p in picked:
        c = p.candidate
        날짜 = c.published.isoformat() if c.published else "날짜미상"
        머리 = FRAGMENT_PREFIX.format(date=날짜, press=c.press)
        out.append(
            {
                "종류": FRAGMENT_KIND,
                "원문": f"{머리}{c.title}. {c.body}",
                "출처": c.url,
            }
        )
    return out


# ══════════════════════════════════════════════════════════
# ④ 통째로 — 여기만 AI를 부른다
# ══════════════════════════════════════════════════════════


def pick_with_ai(
    ask: Callable[[str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]],
    *,
    company: str,
    profile: dict[str, Any],
    candidates: list[Candidate],
    limit: int = MAX_PICKED,
) -> tuple[list[Picked], dict[str, Any]]:
    """후보를 AI에게 보여 주고 번호를 받는다.

    Args:
        ask: (지시문, 답 모양) → (답, 사용량). AI 호출을 감싼 것.
        company: 회사 이름.
        profile: 기업개황.
        candidates: 사전 걸러내기를 통과한 후보들.
        limit: 채택 상한.

    Returns:
        (고른 기사들, 기록에 남길 것).

    ★ 후보가 없으면 **AI를 안 부른다** — 돈이 나가기 때문이다.
    """
    if not candidates:
        return [], {"후보": 0, "채택": 0, "비고": "후보 없음 — AI 호출 안 함"}
    payload, usage = ask(
        build_prompt(company, profile, candidates, limit), answer_schema()
    )
    기록: dict[str, Any] = {"후보": len(candidates), "사용량": usage}
    if payload is None:
        # ★ AI가 대답을 못 했다. 뉴스를 통째로 버리는 대신 **1판 방식으로 되돌아간다** —
        #   제목에 회사 이름이 있는 기사만 받는다. 덜 잡지만 «틀리지는 않는다».
        #   ⚠️ 다시 부르지 않는다 — 돈이 두 번 나가고, 같은 이유로 또 실패할 수 있다.
        picked = fallback_title_match(candidates, company, limit)
        기록.update({
            "채택": len(picked),
            "오류": usage.get("error", "AI 답 없음"),
            "비고": "AI 실패 — 제목 일치 방식으로 되돌아감",
        })
        return picked, 기록
    picked, 버림 = apply_picks(candidates, payload, company, limit)
    기록.update({"채택": len(picked), "버림": {k: v for k, v in 버림.items() if v}})
    return picked, 기록


def fallback_title_match(
    candidates: list[Candidate], company: str, limit: int = MAX_PICKED
) -> list[Picked]:
    """AI가 실패했을 때 쓰는 «안전한» 대체 — 1판과 같은 제목 일치 방식.

    ★ 덜 잡는다(하이브는 0건). 그래도 **틀린 기사를 넣는 것보다 낫다.**
    ★ 쓰임새를 못 정하므로 첫 갈래로 둔다 — 뒤에서 어느 칸에 쓸지는 문장 고르기가 정한다.
    """
    깨끗한이름 = company.replace(" ", "")
    out = []
    for c in candidates:
        if 깨끗한이름 and 깨끗한이름 in c.title.replace(" ", ""):
            out.append(Picked(candidate=c, use_kind=USE_KINDS[0]))
        if len(out) >= limit:
            break
    return out

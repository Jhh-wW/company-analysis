"""데모용 알맹이 — 초기 조사 엔진이 실제로 돌려 남긴 기록을 «재생»한다.

★ 이건 임시다. 진짜 파이프라인을 만들면 이 파일만 갈아끼운다.
  화면 코드는 한 줄도 안 바뀐다 (`port.py` 참조).

돈이 들지 않는다. AI를 부르지 않고 저장된 파일만 읽는다.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import time
from dataclasses import replace
from functools import lru_cache
from typing import Optional

from src.core import paths
from src.core.citations import citation_number
from src.core.constants import (
    CELL_LABELS,
    COMPANY_FACT_CELLS,
    COUNTED_CELLS,
    PROGRESS_STEPS,
    SITUATION_CELLS,
    REPLAY_MODEL_MARK,
    REPORT_CELL_ORDER,
    SUBSTANCE_FAILED_REASON,
    TABLE_DUMP_REASON,
)
from src.features.grading.constants import ACCOUNTING_POLICY_REASON
from src.features.homepage.link import browser_url
from src.features.company_use.logic import build_company_use_section
from src.features.grading.financial import FINANCIAL_CITE, parse_financial_table
from src.features.provenance.citations import build_citations
from src.features.provenance.sources import Source, SourceKind
from src.features.provenance.freshness import append_direction_warning
from src.features.grading.logic import grade_of, is_accounting_policy, is_table_dump
from src.features.spanselect.constants import NEWS_FRAGMENT_KIND
from src.features.spanselect.logic import (
    is_audit_opinion,
    is_market_price_news,
    is_unusable_candidate,
    strip_leading_noise,
)
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    Report,
    ReportSection,
    ReportTable,
    RunResult,
    SourceStatus,
    StepReporter,
    UserInput,
    outcome_for,
)
from src.features.pipeline.canonical_demo import (
    DEMO_ALIASES as CANONICAL_DEMO_ALIASES,
    DEMO_COMPANY as CANONICAL_DEMO_COMPANY,
    DEMO_REF as CANONICAL_DEMO_REF,
    build_demo_report as build_canonical_demo_report,
    demo_card as canonical_demo_card,
)

#: 데모에서 각 단계를 보여주는 간격(초). 진짜 파이프라인에서는 실제 소요 시간이 된다.
_DEMO_STEP_DELAY_SEC = 0.45

#: 데모의 「회사 홈페이지」 수집 현황에 적는 사유.
#: ★ 「아직 연결되지 않음」이라고 쓰면 안 된다 — 기능은 이미 붙어 있고,
#:   없는 것은 «이 저장 기록»이다. 둘을 섞으면 사용자가 도구의 한계를 오해한다.
DEMO_HOMEPAGE_DETAIL = "이 데모 기록을 만들 때는 안 읽었습니다 — 진짜 조사에서는 읽습니다"


def _step_delay() -> float:
    """단계 사이 간격. ★ 시험에서는 0이다.

    불러올 때 한 번 정하면 안 된다 — pytest는 시험이 «시작될 때» 표시를 켜므로
    상수로 두면 시험에서도 0.45초씩 쉬어 전체가 2분씩 걸린다.
    """
    return 0.0 if os.environ.get("PYTEST_CURRENT_TEST") else _DEMO_STEP_DELAY_SEC

# 기존 조사 기록의 종료 코드를 우리 종료 종류로 옮긴다.
# 왼쪽 문자열은 runs.jsonl에 실제로 찍힌 값의 앞부분이다 (예: "중단_식별실패", "완료_성립미달").
_OUTCOME_MAP: dict[str, Outcome] = {
    "완료": Outcome.REPORT,
    "중단_식별실패": Outcome.NOT_FOUND,
    "중단_후보없음": Outcome.NOT_FOUND,
    "중단_게이트": Outcome.GATE_STOPPED,
    "거부_거부A": Outcome.REJECT_PUBLIC,
    "거부_거부B": Outcome.REJECT_NO_DISCLOSURE,
    "폐기": Outcome.POSTING_DISCARDED,
    "생성실패": Outcome.FAILED,
}

# 실패했을 때 사용자에게 보여줄 말 (뜻은 기획서, 문장은 여기)
_OUTCOME_MESSAGE: dict[Outcome, str] = {
    Outcome.NOT_FOUND: (
        "입력하신 이름으로 회사를 찾지 못했습니다. "
        "전자공시에 등록된 이름과 다를 수 있어요 (예: 11번가 → 십일번가)."
    ),
    Outcome.REJECT_PUBLIC: (
        "공공기관·공기업은 채용 방식과 공개되는 자료의 성격이 달라 "
        "지금은 다루지 않고 있습니다."
    ),
    # ★ 정정 — 예전 문구는 「감사보고서를 낸 기록이 없습니다」였다.
    #   그건 우리가 «찾은 방법»이지 사용자가 알아야 할 사실이 아니었고,
    #   더구나 틀렸다 — 사업보고서에 첨부해 내는 회사는 별도 감사보고서가 없다.
    #   지금은 감사보고서와 재무제표를 «둘 다» 확인하므로 결과만 말한다.
    Outcome.REJECT_NO_DISCLOSURE: (
        "이 회사는 최근 3년 안에 전자공시에 공개된 재무 자료가 없습니다. "
        "지어내지 않고는 말할 수 있는 게 없습니다."
    ),
    Outcome.POSTING_DISCARDED: (
        "올려주신 것이 채용공고로 보이지 않습니다. "
        "공고 화면을 다시 캡처하시거나, 개인정보를 가리고 올려주세요."
    ),
    Outcome.GATE_STOPPED: (
        "「무엇을 팔아 돈을 버는지」를 찾지 못했습니다. "
        "이게 없으면 나머지를 채워도 지원동기를 쓸 수 없어서 여기서 멈춥니다."
    ),
    Outcome.FAILED: "보고서를 만들다 오류가 났습니다. 잠시 후 다시 시도해주세요.",
}

#: 기존 기록에 쓰인 AI 모델. 기록에 없어 1판 설정값을 그대로 적는다.
#: ★ 꼬리표는 상수에서 가져온다 — 비용을 세는 쪽(`observability`)이 같은 글자를 본다.
_PILOT_MODEL = f"claude-haiku-4-5 {REPLAY_MODEL_MARK}"

#: 보고서 한 줄에서 「문장 〔출처〕」를 갈라내는 규칙
_LINE_WITH_CITE = re.compile(r"^\s*-\s*(?P<text>.*?)\s*〔(?P<cite>[^〕]+)〕\s*$")
#: 「## 1. 뭘 팔아서 돈 버나」에서 칸 번호와 제목을 뽑는 규칙
_SECTION_HEAD = re.compile(r"^##\s*(?P<cell>[\d\-]+|附)\.\s*(?P<title>.+?)\s*$")


#: 행정구역 이름 끝에 붙는 말. 「강원」과 「강원도」를 같게 보려고 떼어낸다.
_ADMIN_SUFFIXES: tuple[str, ...] = (
    "특별자치도", "특별자치시", "광역시", "특별시", "도", "시", "군", "구",
)
#: 접미사를 뗀 뒤 남아야 하는 최소 글자 수. 「대구」에서 「구」를 떼면 「대」가 되어 못 쓴다.
_MIN_REGION_STEM = 2

#: 법인격 표기. 「주식회사 로보스타」와 「(주)로보스타」를 같은 회사로 보려고 떼어낸다.
_LEGAL_ENTITY_TOKENS: tuple[str, ...] = ("주식회사", "(주)", "㈜", "(유)", "유한회사")


def _clean(value: object) -> str:
    """기록에서 꺼낸 글자를 화면에 쓸 수 있게 다듬는다.

    초기 조사 엔진이 웹에서 긁어온 값이라 `&nbsp;` 같은 HTML 기호가 그대로 섞여 있다.
    """
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _region_stem(token: str) -> str:
    """「강릉시」→「강릉」처럼 행정구역 접미사를 떼어낸다."""
    for suffix in _ADMIN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_REGION_STEM:
            return token[: -len(suffix)]
    return token


def _region_matches(typed: str, address: str) -> bool:
    """입력한 지역이 주소와 같은 곳을 가리키는가.

    「강원 강릉시」와 「강원도 강릉시 과학단지로 77-19」은 같은 곳이다.
    글자 그대로 비교하면 다르다고 나오므로 토막마다 접미사를 떼고 본다.
    """
    tokens = [t for t in typed.split() if t]
    if not tokens:
        return True
    return all(_region_stem(t) in address for t in tokens)


def _in_report_order(sections: list[ReportSection]) -> list[ReportSection]:
    """항목을 기획서의 정본 순서로 맞춘다.

    기록 파일에 5번이 뒤에 붙어 있는 등 순서가 흐트러진 경우가 있어 화면에서 바로잡는다.
    정본에 없는 번호는 뒤에 원래 순서대로 붙인다 (버리지 않는다).
    """
    rank = {cell: i for i, cell in enumerate(REPORT_CELL_ORDER)}
    tail = len(REPORT_CELL_ORDER)
    return sorted(
        sections,
        key=lambda s: (rank.get(s.cell, tail), sections.index(s)),
    )


def _outcome_of(raw: str) -> Outcome:
    """기존 조사 기록의 종료 문자열을 종료 종류로 옮긴다.

    ★ 옮기는 «규칙»은 `port.outcome_for` 한 곳에 있다.
      예전에는 이 4줄을 진짜 파이프라인이 «다른 방법»으로 따로 갖고 있었고,
      그 차이가 운영 결함이 됐다. 표(`_OUTCOME_MAP`)는 데모 것이 따로 맞다.
    """
    return outcome_for(raw, _OUTCOME_MAP)


def _step(record: dict, name: str) -> Optional[dict]:
    """기록에서 특정 단계를 찾는다. 같은 이름이 여럿이면 첫 번째."""
    for s in record.get("steps", []):
        if s.get("step") == name:
            return s
    return None


@lru_cache(maxsize=1)
def _load_runs() -> tuple[dict, ...]:
    """기존 실행 기록을 읽어 둔다. 한 번만 읽고 재사용한다."""
    if not paths.PILOT_RUNS_FILE.exists():
        return ()
    rows: list[dict] = []
    with paths.PILOT_RUNS_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return tuple(rows)


def _normalize(name: str) -> str:
    """이름 대조용으로 다듬는다. 법인격 표기와 공백을 지운다.
    """
    cleaned = name.strip()
    for token in _LEGAL_ENTITY_TOKENS:
        cleaned = cleaned.replace(token, "")
    return re.sub(r"[\s.\-·]", "", cleaned).lower()


def available_companies() -> list[dict[str, str]]:
    """데모에서 고를 수 있는 회사 목록.

    입력 화면에서 「이 회사들만 됩니다」로 보여준다.
    실제 서비스에서는 이 함수가 없어진다.
    """
    # 구형 파일럿 15건은 4-1·4-2·4-3·활용 구조이고 사실 원장도 없다.
    # 새 결과처럼 재생하지 않고 현재 출고 게이트를 실제로 통과한 한 건만 공개한다.
    return [
        {
            "company": CANONICAL_DEMO_COMPANY,
            "job": "",
            "outcome": Outcome.REPORT.value,
            "is_report": "1",
        }
    ]


def _is_canonical_demo_name(name: str) -> bool:
    normalized = _normalize(name)
    aliases = {_normalize(item) for item in CANONICAL_DEMO_ALIASES}
    aliases.add(_normalize(CANONICAL_DEMO_COMPANY))
    return bool(normalized) and normalized in aliases


def _find_record(company: str) -> Optional[dict]:
    """입력한 이름과 맞는 기록을 찾는다."""
    target = _normalize(company)
    if not target:
        return None
    rows = _load_runs()

    def preferred(candidates: list[dict]) -> Optional[dict]:
        """목록과 같은 규칙으로 보고서가 나온 기록을 먼저 고른다."""
        if not candidates:
            return None
        return next(
            (
                row
                for row in candidates
                if _outcome_of(row.get("outcome", "")) is Outcome.REPORT
            ),
            candidates[0],
        )

    # 정확히 같은 것 먼저. 같은 회사를 여러 번 조사했다면 입력 화면의 성공 배지와
    # 실제 실행이 어긋나지 않도록 보고서가 나온 기록을 우선한다.
    exact = [
        row
        for row in rows
        if _normalize((row.get("input") or {}).get("company", "")) == target
    ]
    if exact:
        return preferred(exact)

    # 없으면 포함하는 것. 이때도 목록과 같은 성공 우선 규칙을 유지한다.
    partial = []
    for row in rows:
        name = _normalize((row.get("input") or {}).get("company", ""))
        if name and (target in name or name in target):
            partial.append(row)
    return preferred(partial)


def _sources_of(record: dict) -> list[SourceStatus]:
    """기록에서 소스별 수집 현황을 뽑는다.

    ⭕ 찾음 / ❌ 없음 / ⚠️ 못 가져옴 — 셋을 섞지 않는다.
    """
    sources: list[SourceStatus] = []

    collect = _step(record, "6_수집")
    if collect:
        origin = collect.get("원문", "")
        pieces = collect.get("조각수", 0)
        sources.append(
            SourceStatus("전자공시", "ok", f"{origin} · 조각 {pieces}개")
            if origin
            else SourceStatus("전자공시", "none", "자료 없음")
        )
    else:
        sources.append(SourceStatus("전자공시", "none", "여기까지 오지 못함"))

    news = _step(record, "6_수집_뉴스")
    if news:
        taken = news.get("채택", 0)
        found = news.get("검색결과", 0)
        sources.append(
            SourceStatus("뉴스", "ok", f"검색 {found}건 중 {taken}건 채택")
            if taken
            else SourceStatus("뉴스", "none", f"검색 {found}건 · 채택 조건 통과 0건")
        )
    else:
        sources.append(SourceStatus("뉴스", "none", "여기까지 오지 못함"))

    # ★ 「아직 연결되지 않음」이라고 말하면 안 된다 — 홈페이지 수집은 **이미 붙어 있다**
    #   (해소, `features/homepage/`). 없는 것은 «기능»이 아니라 이 저장 기록이다.
    #   초기 조사 엔진이 이 기록을 만들 때는 홈페이지 수집이 없었을 뿐이다.
    #   기능이 붙었는데 화면이 옛말을 하면 사용자가 「이 도구는 홈페이지를 못 본다」고
    #   잘못 안다 (P-63과 같은 사고).
    # ★ ❌(없음)로 적지 않는 이유 — 회사에 홈페이지가 없다는 뜻이 되어 버린다.
    sources.append(SourceStatus("회사 홈페이지", "failed", DEMO_HOMEPAGE_DETAIL))
    return sources


def _parse_report(text: str) -> tuple[list[ReportSection], list[str], str]:
    """보고서 마크다운을 항목별로 가른다.

    Returns:
        (항목 목록, 요구역량 목록, 생성일)
    """
    sections: list[ReportSection] = []
    requirements: list[str] = []
    generated_at = ""

    cur_cell = ""
    cur_title = ""
    cur_lines: list[tuple[str, str]] = []
    cur_tables: list[ReportTable] = []
    cur_reason = ""

    def flush() -> None:
        if not cur_cell:
            return
        sections.append(
            ReportSection(
                cell=cur_cell,
                title=CELL_LABELS.get(cur_cell, cur_title),
                lines=list(cur_lines),
                empty_reason=cur_reason,
                tables=list(cur_tables),
            )
        )

    for raw in text.splitlines():
        line = raw.rstrip()

        if line.startswith("> ") and "생성" in line:
            found = re.search(r"생성\s*([\d\-]+)", line)
            if found:
                generated_at = found.group(1)
            continue

        head = _SECTION_HEAD.match(line)
        if head:
            flush()
            cur_cell = head.group("cell")
            cur_title = head.group("title")
            cur_lines = []
            cur_tables = []
            cur_reason = ""
            continue

        if not line.startswith("-"):
            continue

        body = line.lstrip("- ").strip()

        # 빈칸 — 「왜 비었는지」를 프로그램이 붙여 둔 줄
        if body.startswith("(빈칸)"):
            cur_reason = body.replace("(빈칸)", "").replace("사유:", "").strip()
            continue
        # 알맹이 검사에서 걸린 칸
        if body.startswith("(칸 X"):
            cur_reason = SUBSTANCE_FAILED_REASON
            continue

        cite_match = _LINE_WITH_CITE.match(line)
        if cite_match:
            sentence = cite_match.group("text").strip()
            cite = cite_match.group("cite").strip()
            if cite == "공고":
                requirements.append(sentence)
                continue
        else:
            sentence, cite = body, ""

        # ★ 재무·회계 수치는 «표 그대로» 낸다 (결정기록 D13). 버리지 않는다.
        # 저장 MD의 `조각 N·종류`를 표로 바꾸면서 버리지 않는다. 화면·DOCX의
        # 표 캡션에는 내부 종류명이 아니라 아래 출처표를 가리키는 번호만 보인다.
        public_cite = citation_number(cite)
        table = parse_financial_table(
            sentence, cite=public_cite or FINANCIAL_CITE
        )
        if table is not None:
            cur_tables.append(table)
            continue

        # 그 밖의 표가 통째로 들어온 줄은 채우지 않는다.
        # 버린 사실을 사유로 남긴다 — 조용히 사라지면 왜 비었는지 알 수 없다.
        if is_table_dump(sentence):
            cur_reason = cur_reason or TABLE_DUMP_REASON
            continue

        # 회계기준 설명 문구는 회사 이름을 바꿔도 말이 된다.
        # 자소서에 한 글자도 못 쓰므로 화면에 내보내기 직전에 버린다.
        if is_accounting_policy(sentence):
            cur_reason = cur_reason or ACCOUNTING_POLICY_REASON
            continue

        cur_lines.append((sentence, cite))

    flush()
    return sections, requirements, generated_at


#: 본문 문장 뒤 표기에서 조각 번호와 종류를 뽑는다 — 「조각 4·MD&A」
_CITE_PARTS = re.compile(r"^조각\s*(?P<number>\d+)\s*·\s*(?P<kind>.+)$")


def _news_citations(record: dict) -> dict[int, Source]:
    """뉴스 조각의 보도일·언론사를 조각 원문에서 읽어 둔다.

    ★ 뉴스 조각 원문은 「(2026-07-15 보도 · domain) 제목. 본문」 꼴이라
      **날짜와 언론사가 조각 안에 들어 있다.** 이미 그 파싱을 하는 함수가 있으므로
      그것을 그대로 쓴다 (`provenance.citations`) — 새로 만들면 두 벌이 어긋난다.
    ★ 공시 보고서 이름을 뉴스에 갖다 붙이지 않는다. 「감사보고서 (2025.12) · 뉴스」는
      **없는 사실**이다 — 그 기사는 감사보고서에서 나오지 않았다.

    Args:
        record: 기존 실행 기록 한 줄.

    Returns:
        조각 번호 → 뉴스 출처. 뉴스 조각이 없으면 빈 표.
    """
    frags = {
        number: {"종류": kind, "원문": text}
        for number, kind, text in _load_fragments(str(record.get("id", "")))
        if kind == NEWS_FRAGMENT_KIND
    }
    if not frags:
        return {}
    # `collected_on`은 뉴스에는 쓰이지 않는다 (보도일을 쓴다). 공시 조각을 안 넘기므로
    # 오늘 날짜가 화면에 새어 나가지 않는다.
    made = build_citations(frags, filing=None, collected_on=dt.date.today())
    return {source.number: source for source in made}


def _demo_citations(record: dict, sections: list[ReportSection]) -> list[Source]:
    """저장된 보고서에서 출처 목록을 «있는 것만» 만든다.

    ★ 기존 조사 기록에는 **공시일이 없다**.
      날짜를 지어내지 않는다 — 「어느 보고서 어느 절에서 왔나」만 정직하게 적는다.
      진짜 조사에서는 날짜까지 채워진다.
    ★ 뉴스는 예외다 — 보도일·언론사가 조각 원문에 남아 있어 그대로 옮긴다.
    """
    collect = _step(record, "6_수집") or {}
    filing_name = str(collect.get("원문") or "")
    news = _news_citations(record)
    fragments = {
        number: (kind, text)
        for number, kind, text in _load_fragments(str(record.get("id", "")))
    }
    seen: dict[int, Source] = {}
    for section in sections:
        cites = [cite for _text, cite in section.lines]
        cites.extend(table.cite for table in section.tables)
        for cite in cites:
            matched = _CITE_PARTS.match(cite.strip())
            if matched is not None:
                number = int(matched.group("number"))
                kind_name = matched.group("kind").strip()
            else:
                public_number = citation_number(cite)
                if not public_number:
                    continue
                number = int(public_number)
                fragment = fragments.get(number)
                if fragment is None:
                    continue
                kind_name = fragment[0]
            if number in seen:
                continue
            if kind_name == NEWS_FRAGMENT_KIND and number in news:
                seen[number] = news[number]
                continue
            stored_kind = f"저장 분류명: {kind_name}"
            label = f"{filing_name} · {stored_kind}" if filing_name else stored_kind
            label += " · 공시일·수집일·원문 ID 미저장"
            seen[number] = Source(
                number=number,
                kind=SourceKind.NEWS if kind_name == NEWS_FRAGMENT_KIND else SourceKind.FILING,
                label=label,
            )
    return [seen[n] for n in sorted(seen)]


# ══════════════════════════════════════════════════════════
# 4번(회사 상황) 재도출 — 저장된 뉴스 조각에서 «코드 규칙만으로» 고른다
# ══════════════════════════════════════════════════════════
# ★ 왜 필요한가
#   데모 15곳 중 13곳에서 4-1·4-2·4-3이 통째로 비었다. 그 탓에 8번 교차표가
#   14곳에서 안 나온다. 자료가 없어서가 아니다 — 1판 프롬프트가 4축에 대해
#   「회사가 직접 말한 문장만」이라고 지시해 **뉴스 후보 106문장을 한 번도
#   안 골랐다.** 정본은 4-1을 「회사가 밝혔거나 **언론이 지적한** 과제」로 정했다.
#
# ★ 진짜 파이프라인은 이미 고쳤다(`spanselect/`가 지시문을 바꿨다). 그런데 데모는
#   저장된 `.md`를 «재생»하므로 화면에서 아무것도 안 바뀐다. 그래서 6·7·8번을
#   저장 기록에서 재도출하던 것과 **같은 자리·같은 방식**으로 4번도 재도출한다
#   (결정 D14-3).
#
# ★ AI를 부르지 않는다 (0원). 저장된 조각을 낱말 규칙으로만 배치한다.
#   그래서 «진짜 AI가 고른 배치와는 다르다» — 그 사실을 화면에 밝힌다(아래 안내문).

#: 기존 조사 기록의 조각 원문이 들어 있는 폴더 이름 (`analysis_engine/data/pilot/fragments/`).
_PILOT_FRAGMENTS_DIRNAME = "fragments"

#: 문장 가르기. 1판 `run_pilot.split_sentences`와 같은 규칙이다(`(?<=[.!?])\s+`).
#: ★ 1판 함수를 직접 부르지 않는 이유 — `run_pilot`을 불러오면 anthropic·presidio가
#:   딸려 와 4초가 걸리고, 그것들이 안 깔린 PC에서는 **데모 화면 자체가 안 뜬다.**
#:   여기서 필요한 것은 그보다 더 엄격한 규칙(아래 «잘린 문장» 배제)이기도 하다.
_NEWS_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: 이보다 짧은 토막은 문장으로 보지 않는다. 뉴스 조각에서 이 길이 아래는
#: 전부 잘린 꼬리(「기존 고주파 의료기기에서 주로 사용되던 6....」)였다 — 실측.
_NEWS_MIN_SENTENCE_CHARS = 25

#: 문장 끝에서 떼고 볼 기호. 뉴스 API가 본문을 「…」로 자르기 때문에 붙는다.
_SENTENCE_TAIL_MARKS = ".!?…‥ "

#: 한국어 서술문의 종결. 이걸로 안 끝나면 **문장이 잘린 것**이라 보고 버린다.
#: 실측 — 「…첫 고주파 의료기기가 전...」처럼 낱말 도중에 끊긴 토막이 절반이 넘는다.
_SENTENCE_ENDERS: tuple[str, ...] = ("다", "음", "됨")

#: 「이 회사는 무엇인가」를 설명하는 종결. 4-2(요즘 뭘 하고 있나)는 «하는 일»이지
#: «무엇인가»가 아니다.
#: 실측 — 「…대한민국 1세대 산업용 로봇 제조 전문기업이다」가 4-2로 들어오던 문장.
_IDENTITY_ENDINGS: tuple[str, ...] = (
    "기업이다", "회사이다", "회사다", "업체이다", "업체다", "제품이다", "브랜드다", "곳이다",
)

#: 회사가 «주어»인지 보는 조사. 이게 없으면 그 회사는 문장의 주인공이 아니다.
#: 실측 — 「티웨이항공에 따르면 **카카오페이로** 15만원 이상 결제 시 …」는
#:   티웨이항공의 행사지 카카오페이의 활동이 아니다. 「로」는 여기 없으므로 걸러진다.
_SUBJECT_PARTICLES = "이가은는의"

#: 회사의 «실제 활동»을 가리키는 사업 동사. 하나라도 없으면 4번 재료로 안 본다.
#: ★ 낱말은 실제 뉴스 조각에서 확인한 것만 넣는다 (지어내지 않는다).
#: ⚠️ 일부러 뺀 낱말 — 「증가·기록·발표」: 실적 문장(3번 「요즘 잘 되고 있나」)을
#:   4번으로 끌어와 두 칸이 섞인다.
_BUSINESS_ACTION_MARKERS: tuple[str, ...] = (
    "체결", "출시", "확대", "확장", "진출", "인증", "수주", "개발", "투자", "설립",
    "인수", "공급", "획득", "확보", "개최", "통합", "출범", "합병", "상장", "선보",
    "도입", "구축", "탑재", "수출", "참가",
)

#: 「앞으로 어디로 가려 하나」(4-3)를 가리키는 표지. 「몇 년 뒤 방향」.
#: ⚠️ 일부러 뺀 낱말 — 「전망」: 증권가 전망은 «회사의 방향»이 아니다.
_DIRECTION_MARKERS: tuple[str, ...] = (
    "계획", "예정", "추진", "방침", "목표", "전략", "나선다", "속도를 낸다",
)

#: 4번 세부 칸. 정본이 각 3개를 쓴다.
_SITUATION_MAX_LINES = 3
#: 4-1 「지금 뭐가 문제인가」 — ★ 데모는 이 칸을 코드로 채우지 않는다 (아래 사유 참고).
_PROBLEM_CELL = "4-1"
#: 4-2 「요즘 뭘 하고 있나」 — 사업 동사가 든 문장의 기본 자리.
_ACTION_CELL = "4-2"
#: 4-3 「앞으로 어디로 가나」 — 방향 표지가 붙은 문장만 여기로 보낸다.
_DIRECTION_CELL = "4-3"

#: 재도출한 칸 맨 앞에 붙이는 안내. ★ 빼지 않는다.
#: 데모가 «어떻게 채웠는지»를 숨겨 사고가 난 적이 있다.
#: 진짜 조사는 AI가 고르므로 여기서 나온 배치와 **다른 문장이 나온다.**
_REDRAWN_NOTICE = (
    "※ 이 칸은 데모가 저장된 뉴스 기사에서 다시 고른 것입니다 — "
    "AI 없이 낱말 규칙만 썼으므로, 진짜 조사에서는 다른 문장이 나옵니다."
)

#: 뉴스는 있는데 이 칸에 쓸 문장을 못 찾았을 때의 빈칸 사유.
#: ★ 저장된 보고서의 옛 사유(「채택 조건을 통과한 기사 없음」)는 **사실이 아니다** —
#:   루트로닉은 기사 6건이 채택돼 있는데도 그 사유가 적혀 있었다.
_SITUATION_NEWS_UNUSED_REASON = (
    "뉴스 {count}건은 모아 두었지만, 이 칸에 쓸 문장(회사가 주어이고 사업 활동이 "
    "드러나는 문장)을 찾지 못했습니다 — 데모는 AI 없이 낱말 규칙으로만 고릅니다."
)

#: 4번이 «이미 차 있어» 재도출을 안 한 보고서에서, 그래도 비어 있는 칸의 사유.
#: ★ 저장된 사유(「채택 기사 없음」)를 그대로 두면 화면이 스스로 모순된다 —
#:   같은 화면의 수집 현황은 「검색 20건 중 1건 채택」이라고 말한다.
_SITUATION_NOT_PICKED_REASON = (
    "뉴스 {count}건은 모아 두었지만, 이 보고서를 만든 조사에서는 이 칸에 아무 문장도 "
    "배치되지 않았습니다."
)

#: 4-1을 아예 시도하지 않는 사유. ★ 「재료가 없다」고 말하지 않는다 — 거짓이 된다.
#: 「무엇이 문제인가」는 낱말로 못 가른다. 실측 — 「지난해 같은 기간 적자에서 흑자로
#: 돌아섰다」에는 「적자」가 들어 있지만 이것은 좋은 소식이다.
_PROBLEM_CELL_SKIPPED_REASON = (
    "데모는 이 칸을 채우지 않습니다 — 「무엇이 문제인가」는 낱말 규칙으로 가릴 수 "
    "없어 코드가 판단하지 않습니다. 진짜 조사에서는 AI가 뉴스·경영진단에서 고릅니다."
)


@lru_cache(maxsize=None)
def _load_fragments(run_id: str) -> tuple[tuple[int, str, str], ...]:
    """조사 엔진이 저장해 둔 조각 원문을 읽는다. 한 번 읽고 재사용한다.

    Args:
        run_id: 실행 기록 번호 (예: `"재수집-p010"`).

    Returns:
        `(조각 번호, 종류, 원문)` 목록. 파일이 없거나 깨졌으면 빈 목록.
        ★ 조각 번호를 **int로 바꿔서** 돌려준다 — JSON 열쇠는 문자열이라
          그대로 두면 출처 목록(`Source.number`) 비교에서 터진다.
    """
    path = paths.PILOT_DIR / _PILOT_FRAGMENTS_DIRNAME / f"{run_id}.json"
    if not run_id or not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 조각을 못 읽어도 보고서는 나가야 한다. 재도출만 포기한다.
        return ()
    out: list[tuple[int, str, str]] = []
    for key, frag in raw.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        out.append((number, str(frag.get("종류", "")), str(frag.get("원문", ""))))
    return tuple(sorted(out))


def _news_fragment_count(run_id: str) -> int:
    """이 회사로 «모아 둔» 뉴스 기사 수. 시세 기사도 센다.

    ★ 빈칸 사유에 쓰는 숫자다. 걸러낸 뒤의 수를 적으면 「1건 모았다」처럼
      실제보다 적게 말하게 된다 — 수집 현황(「검색 20건 중 2건 채택」)과도 어긋난다.
    """
    return sum(1 for _n, kind, _t in _load_fragments(run_id) if kind == NEWS_FRAGMENT_KIND)


def _news_fragments(run_id: str) -> tuple[tuple[int, str], ...]:
    """4번 재료로 쓸 뉴스 조각. 시세·증시 기사는 «기사 통째로» 뺀다.

    ★ 시세 판정은 이미 있는 것을 그대로 쓴다 (`spanselect.logic`) — 두 벌로
      나뉘면 반드시 어긋난다.
      「종목 나열 기사가 들어가면 주가가 빠졌습니다밖에 못 쓴다」.

    Returns:
        `(조각 번호, 원문)` 목록.
    """
    return tuple(
        (number, text)
        for number, kind, text in _load_fragments(run_id)
        if kind == NEWS_FRAGMENT_KIND and not is_market_price_news(kind, text)
    )


@lru_cache(maxsize=None)
def _company_subject_pattern(company: str) -> Optional[re.Pattern[str]]:
    """「그 회사가 주어인가」를 보는 규칙을 만든다.

    회사명 바로 뒤(괄호 설명은 건너뛴다)에 주격·주제격 조사가 붙어 있어야 한다.
      · 「콜로세움코퍼레이션(이하 콜로세움)이 …」 → 맞음 (괄호를 건너뛴다)
      · 「티웨이항공에 따르면 카카오페이로 …」 → 아님 (「로」는 도구격이다)
    조사 뒤에 한글이 더 붙으면 «다른 회사 이름»이므로 제외한다
      · 회사 「카카오」 · 문장 「카카오엔터테인먼트는 …」 → 아님 (자회사 소식이다)

    Args:
        company: 사용자가 입력한 회사 이름.

    Returns:
        대조용 규칙. 이름이 비어 있으면 None.
    """
    name = company
    for token in _LEGAL_ENTITY_TOKENS:
        name = name.replace(token, "")
    name = name.strip()
    if not name:
        return None
    return re.compile(
        rf"{re.escape(name)}\s*(?:\([^)]*\))?\s*[{_SUBJECT_PARTICLES}](?![가-힣])"
    )


def _news_sentences(text: str) -> list[str]:
    """뉴스 조각 원문을 문장으로 가른다. **잘린 토막은 버린다.**

    뉴스 API가 본문을 「…」로 잘라 보내므로, 그대로 두면 「…의료기기가 전...」
    같은 반쪽 문장이 보고서에 실린다. 서술형 종결로 끝나는 것만 남긴다.

    Args:
        text: 조각 원문 (`"(2026-07-15 보도 · domain) 제목. 본문"` 꼴).

    Returns:
        온전한 문장 목록. 뜻은 **다듬지 않는다** (정본 출력틀 규칙⑤).
        ★ 앞머리의 «껍데기»(기사 말머리·목록 순번)만 뗀다 —
          「[편집자주] 우리엔, 동물 전용 제품으로…」를 자소서에 그대로 못 쓴다.
          진짜 조사 경로(`spanselect`)와 **같은 함수**를 쓴다 — 규칙이 두 벌이 되면
          데모와 진짜 조사가 다른 문장을 내놓아 사용자가 헷갈린다.

    ⚠️ **여기서 규칙을 새로 만들지 말 것.**
      아래 종결형 검사는 데모만의 «두 번째 규칙»이었고, 실제로 새어나갔다 —
      「…수출 노선을 확보했다....」는 꼬리 점을 떼면 「다」로 끝나 통과해 버린다.
      그래서 **유료 조사에서는 걸러지는 문장이 무료 데모에는 그대로 실렸다.**
      `is_unusable_candidate()`를 «먼저» 물어 두 경로의 답을 하나로 맞춘다.
    """
    kept: list[str] = []
    for piece in _NEWS_SENTENCE_SPLIT.split(text):
        sentence = piece.strip()
        if len(sentence) < _NEWS_MIN_SENTENCE_CHARS:
            continue
        # ★ 진짜 조사와 «같은 판정»을 먼저 쓴다. 아래 종결형 검사는 그 뒤의 보강이다.
        if is_unusable_candidate(sentence):
            continue
        if not sentence.rstrip(_SENTENCE_TAIL_MARKS).endswith(_SENTENCE_ENDERS):
            continue
        kept.append(strip_leading_noise(sentence))
    return kept


def _is_situation_material(sentence: str, subject: re.Pattern[str]) -> bool:
    """이 문장이 4번(회사 상황)의 재료가 되는가.

    Args:
        sentence: 후보 문장 하나.
        subject: `_company_subject_pattern()`이 만든 대조 규칙.

    Returns:
        회사가 주어이고 사업 활동이 드러나면 True.
    """
    # 감사인 의견 문구는 4-1과 섞지 않는다.
    # ★ 판정은 이미 있는 것을 쓴다 — 새로 구현하지 않는다.
    if is_audit_opinion(sentence):
        return False
    if sentence.rstrip(_SENTENCE_TAIL_MARKS).endswith(_IDENTITY_ENDINGS):
        return False
    if subject.search(sentence) is None:
        return False
    return any(marker in sentence for marker in _BUSINESS_ACTION_MARKERS)


def _situation_cell_of(sentence: str) -> str:
    """이 문장을 4-2에 둘까 4-3에 둘까.

    ★ 4-1(지금 뭐가 문제인가)은 돌려주지 않는다. 「문제인가」는 낱말로 못 가른다 —
      「적자에서 흑자로 돌아섰다」에 「적자」가 들어 있듯, 부정 낱말이 있다고
      과제 문장인 것이 아니다. 근거 없는 배치를 하느니 비워 둔다.

    Args:
        sentence: 4번 재료로 뽑힌 문장.

    Returns:
        `"4-3"`(방향 표지가 있을 때) 또는 `"4-2"`.
    """
    if any(marker in sentence for marker in _DIRECTION_MARKERS):
        return _DIRECTION_CELL
    return _ACTION_CELL


def _pick_situation_lines(
    company: str, news: tuple[tuple[int, str], ...]
) -> dict[str, list[tuple[str, str]]]:
    """뉴스 조각에서 4-2·4-3에 넣을 (문장, 출처표기)를 고른다.

    ★ 한 기사에서 한 문장만 뽑는다. 여러 문장을 뽑으면 같은 사건이 반복돼
      8번 교차표까지 같은 행으로 부풀어 오른다.

    Args:
        company: 회사 이름 (주어 대조용).
        news: `(조각 번호, 원문)` 목록.

    Returns:
        칸 번호 → (문장, 출처표기) 목록. 출처표기는 기존과 같은 「조각 N·종류」 꼴이다.
    """
    picked: dict[str, list[tuple[str, str]]] = {c: [] for c in SITUATION_CELLS}
    subject = _company_subject_pattern(company)
    if subject is None:
        return picked

    for number, text in news:
        for sentence in _news_sentences(text):
            if not _is_situation_material(sentence, subject):
                continue
            cell = _situation_cell_of(sentence)
            if len(picked[cell]) >= _SITUATION_MAX_LINES:
                continue
            picked[cell].append((sentence, f"조각 {number}·{NEWS_FRAGMENT_KIND}"))
            break  # 한 기사에서 한 문장만
    return picked


def _restate_empty_reasons(
    sections: list[ReportSection], collected: int
) -> list[ReportSection]:
    """재도출을 «안 한» 보고서에서, 비어 있는 4번 칸의 사유만 사실로 바로잡는다.

    문장은 한 줄도 건드리지 않는다 — 여기 오는 보고서는 4번이 이미 채워진 것이다.

    Args:
        sections: 저장된 보고서 항목들.
        collected: 이 회사로 모아 둔 뉴스 기사 수 (1건 이상인 것이 보장된다).
    """
    return [
        replace(section, empty_reason=_SITUATION_NOT_PICKED_REASON.format(count=collected))
        if section.cell in SITUATION_CELLS and not section.is_filled
        else section
        for section in sections
    ]


def _redraw_situation(
    record: dict, sections: list[ReportSection]
) -> tuple[list[ReportSection], frozenset[str]]:
    """4번이 통째로 빈 보고서를 저장된 뉴스 조각으로 다시 채운다.

    ★ 이미 문장이 있는 4번은 **건드리지 않는다.** 재도출은 빈 칸에만 한다 —
      AI가 고른 문장과 코드가 고른 문장이 한 칸에 섞이면 어느 쪽인지 알 수 없다.

    Args:
        record: 기존 실행 기록 한 줄.
        sections: 저장된 보고서에서 읽어 낸 항목들.

    Returns:
        (새 항목 목록, 재도출한 칸 번호들).
    """
    run_id = str(record.get("id", ""))
    collected = _news_fragment_count(run_id)
    if not collected:
        # 뉴스 조각이 아예 없으면 저장된 사유(「채택 기사 없음」)가 사실이다. 그대로 둔다.
        return sections, frozenset()

    by_cell = {s.cell: s for s in sections}
    if any(by_cell.get(cell) and by_cell[cell].lines for cell in SITUATION_CELLS):
        # 재도출은 하지 않는다. 다만 «거짓이 된 사유»는 그대로 둘 수 없다 —
        # 같은 화면의 수집 현황이 「검색 20건 중 1건 채택」이라고 말하는데
        # 빈칸 사유가 「통과한 기사 없음」이면 화면이 스스로 모순된다.
        return _restate_empty_reasons(sections, collected), frozenset()

    news = _news_fragments(run_id)
    company = (record.get("input") or {}).get("company", "")
    picked = _pick_situation_lines(company, news)

    redrawn = {cell for cell, lines in picked.items() if lines}
    remade: list[ReportSection] = []
    for section in sections:
        if section.cell not in SITUATION_CELLS:
            remade.append(section)
            continue
        lines = picked[section.cell]
        if lines:
            remade.append(replace(section, lines=lines, empty_reason=""))
            continue
        # ★ 못 채운 칸의 사유를 «사실»로 바꾼다. 기사가 있는데 「기사 없음」이
        #   남아 있으면 사용자가 그 회사를 포기한다.
        reason = (
            _PROBLEM_CELL_SKIPPED_REASON
            if section.cell == _PROBLEM_CELL
            else _SITUATION_NEWS_UNUSED_REASON.format(count=collected)
        )
        remade.append(replace(section, empty_reason=reason))
    return remade, frozenset(redrawn)


def _note_redrawn(section: ReportSection) -> ReportSection:
    """재도출한 칸 맨 앞에 「데모가 다시 고른 것」이라는 안내를 붙인다.

    ★ 8번 교차표를 만든 **뒤에** 불러야 한다. 안내 줄은 회사 상황 문장이
      아니므로 교차표의 재료로 섞이면 안 된다.
    ★ 여러 번 불러도 안내가 겹치지 않는다 (`append_direction_warning`과 같은 약속).
    """
    if not section.lines or section.lines[0][0] == _REDRAWN_NOTICE:
        return section
    return replace(section, lines=[(_REDRAWN_NOTICE, ""), *section.lines])


def _load_report(record: dict) -> Optional[Report]:
    """기록에 딸린 보고서 파일을 읽어 구조로 만든다."""
    output = _step(record, "13_출력")
    filename = (output or {}).get("파일", "")
    if not filename:
        return None
    path = paths.PILOT_REPORTS_DIR / filename
    if not path.exists():
        return None

    sections, _legacy_requirements, generated_at = _parse_report(path.read_text(encoding="utf-8"))
    # ★ 4번(회사 상황)이 통째로 비었으면 저장된 뉴스 조각에서 «재도출»한다
    #   AI를 부르지 않는다.
    sections, redrawn_cells = _redraw_situation(record, sections)

    corp_type_raw = record.get("corp_type", "")
    corp_type = "비상장 외감" if "비상장" in corp_type_raw else "상장사"

    # ★ 附(참고 숫자 — 1인평균급여액·평균근속연수)는 걷어냈다.
    #   이 보고서는 「지원동기를 합격 퀄리티로 만드는 정보」만 담는다(사용자 지시).
    #   옛 기록에 附이 남아 있어도 화면에 올리지 않는다.
    sections = [s for s in sections if s.cell in COMPANY_FACT_CELLS and s.cell != "附"]

    # ★ 재도출한 칸에 「데모가 다시 고른 것」임을 밝힌다. 8번 표를 만든 «뒤»다 —
    #   안내 줄이 교차표의 재료로 섞이면 근거 없는 행이 생긴다.
    sections = [_note_redrawn(s) if s.cell in redrawn_cells else s for s in sections]

    # 4-3에 날짜·변경 경고
    sections = [append_direction_warning(s) if s.cell == "4-3" else s for s in sections]

    sections.append(build_company_use_section(sections))

    # ★ 등급은 «화면에 실제로 보이는 것»으로 다시 센다.
    #   기록에 저장된 판정(final_cells)을 그대로 쓰면, 표 덩어리를 비운 칸이
    #   여전히 「채워짐」으로 남아 화면과 등급이 어긋난다.
    cells = {s.cell: s.is_filled for s in sections if s.cell in COUNTED_CELLS}
    grade, reasons = grade_of(cells)

    return Report(
        company=(record.get("input") or {}).get("company", ""),
        job="",
        corp_type=corp_type,
        grade=grade,
        sections=_in_report_order(sections),
        requirements=[],
        sources=_sources_of(record),
        cells=cells,
        shortfall_reasons=reasons,
        citations=_demo_citations(record, sections),
        generated_at=generated_at,
    )


def _metrics_of(record: dict, report: Optional[Report]) -> dict:
    """저장된 기록에서 «대시보드가 쓸 숫자»를 꺼낸다.

    ★ 없는 값을 지어내지 않는다. 기록에 없으면 0으로 둔다 —
      0은 「아직 못 잼」이 아니라 「그만큼이었다」로 읽히므로, 화면 쪽에서
      건수가 적을 때의 해석을 함께 보여줘야 한다.
    """
    collect = _step(record, "6_수집") or {}
    verify = _step(record, "10_검증_W대조") or {}
    kept = int(verify.get("유지", 0) or 0)
    deleted = int(verify.get("삭제", 0) or 0)
    cited = 0
    if report is not None:
        cites = [
            cite
            for section in report.sections
            for _text, cite in section.lines
        ]
        cites.extend(
            table.cite
            for section in report.sections
            for table in section.tables
        )
        cited = len({number for cite in cites if (number := citation_number(cite))})
    return {
        "corp_type": ("비상장 외감" if "비상장" in str(record.get("corp_type", ""))
                      else "상장사"),
        "fragments_collected": int(collect.get("조각수", 0) or 0),
        "fragments_cited": cited,
        "sentences_made": kept + deleted,
        "sentences_passed": kept,
        # ★ 데모는 «0원»이다.
        #   port.py의 약속은 「**이 요청에** 쓴 AI 비용」이고, 데모는 저장된 것을
        #   그대로 보여줄 뿐 AI를 한 번도 부르지 않는다. 실제로 나간 돈은 0이다.
        #   예전에는 기존 기록에 담긴 비용(`cost_usd`)을 그대로 실었다. 그래서
        #   이력 791건에 34,222원이 쌓였는데 **그 달 진짜 지출은 약 750원**이었다.
        #   관리 화면의 「이번 달 AI 비용」이 45배로 부풀어, 예산 판단을 망친다.
        # ⚠️ 원래 얼마였는지는 **1판 기록(`analysis_engine`의 `runs.jsonl`)에 그대로 있다.**
        #   여기서 0으로 두어도 정보는 잃지 않는다 — 다른 곳으로 옮겨 적지 않을 뿐이다.
        "cost_krw": 0.0,
        "model": _PILOT_MODEL,
    }


class DemoPipeline:
    """저장된 파일럿 기록을 돌려주는 알맹이."""

    supports_posting_image_input = False

    def find_company(self, user_input: UserInput) -> Optional[CompanyCard]:
        """입력한 이름으로 회사 하나를 찾는다."""
        if _is_canonical_demo_name(user_input.company):
            card = canonical_demo_card(user_input.company)
            warning = ""
            if user_input.region.strip() and not _region_matches(
                user_input.region, card.address
            ):
                warning = (
                    f"입력하신 지역({user_input.region.strip()})과 본사 주소가 다릅니다. "
                    "지사·공장이거나 최근 이전했을 수 있습니다."
                )
            return replace(card, region_warning=warning)

        record = _find_record(user_input.company)
        if record is None:
            return None

        confirm = _step(record, "3_사람확인") or {}
        card_data = confirm.get("확인카드") or {}
        if not card_data:
            # 확인 카드까지 못 간 기록(식별 실패)은 회사를 못 찾은 것으로 본다.
            return None

        layer3 = _step(record, "2_식별_층3") or {}
        same_name = int(layer3.get("후보수", 1) or 1)

        typed_region = user_input.region.strip()
        card_region = _clean(card_data.get("주소"))
        warning = ""
        if typed_region and not _region_matches(typed_region, card_region):
            warning = (
                f"입력하신 지역({typed_region})과 본사 주소가 다릅니다. "
                "지사·공장이거나 최근 이전했을 수 있습니다."
            )

        return CompanyCard(
            legal_name=_clean(card_data.get("회사")) or user_input.company,
            typed_name=user_input.company,
            address=card_region,
            ceo=_clean(card_data.get("대표")),
            founded=_clean(card_data.get("설립")),
            homepage=_clean(card_data.get("홈페이지")),
            # 데모는 네트워크를 호출하지 않지만 http(s) 모양을 안전하게 정규화해
            # 확인 카드에서 실제 링크로 열 수 있게 한다.
            homepage_url=browser_url(_clean(card_data.get("홈페이지"))),
            region_warning=warning,
            same_name_count=same_name,
            ref=str(record.get("id", "")),
        )

    def run(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[StepReporter] = None,
    ) -> RunResult:
        """확인받은 회사로 끝까지 돌린다 (실제로는 저장된 결과를 돌려준다).

        데모라 진짜로 걸리는 시간이 없으므로 **단계를 흉내 내며 천천히 넘긴다.**
        진짜 파이프라인에서는 각 단계가 끝날 때 실제로 알린다.
        """
        for key, _label in PROGRESS_STEPS:
            if on_step is not None:
                on_step(key)
            time.sleep(_step_delay())

        if card.ref == CANONICAL_DEMO_REF or _is_canonical_demo_name(
            user_input.company
        ):
            report = build_canonical_demo_report()
            fact_count = len(report.fact_records)
            return RunResult(
                outcome=Outcome.REPORT,
                report=report,
                sources=[],
                charged=True,
                elapsed_sec=0.0,
                corp_type=report.corp_type,
                fragments_collected=fact_count,
                fragments_cited=len(report.citations),
                sentences_made=fact_count,
                sentences_passed=fact_count,
                cost_krw=0.0,
                model="canonical-demo-v3",
            )

        record = _find_record(user_input.company)
        if record is None:
            return RunResult(
                outcome=Outcome.NOT_FOUND,
                message=_OUTCOME_MESSAGE[Outcome.NOT_FOUND],
            )

        outcome = _outcome_of(record.get("outcome", ""))
        elapsed = float(record.get("elapsed_sec", 0) or 0)

        if outcome is Outcome.REPORT:
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "이 저장본은 현재 보고서 구조와 사실 검증 계약을 거치지 않아 "
                    "새 보고서로 재생하지 않습니다."
                ),
                sources=_sources_of(record),
                charged=False,
                elapsed_sec=elapsed,
                **_metrics_of(record, None),
            )

        return RunResult(
            outcome=outcome,
            message=_OUTCOME_MESSAGE.get(outcome, ""),
            sources=_sources_of(record),
            charged=False,  # 보고서가 안 나가면 차감 0
            elapsed_sec=elapsed,
            **_metrics_of(record, None),
        )

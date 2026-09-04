# -*- coding: utf-8 -*-
"""야간 파일럿 — 15단계 뼈대 그대로의 경량 일괄 실행.

목적: 현재 확정된 정본 기준대로 프로그램을 실제로 돌려 「어디가 걸리는지」를 찾는다.
      결과·문제는 그 기준을 고치는 재료가 된다. 50건 이상.

뼈대 — 단계 번호를 그대로 로그에 남긴다:
  1 계정·할당량      → 배치 모드 생략 (기록만 — UI·계정 없음)
  1.5 입력           → 텍스트 경로 (파일럿 1단계 기본과 일치) · 주소는 「모름」
  2 식별 3층         → 층1 코드(corpCode) → 층2 AI 5회 상한(+DART 실재 대조) → 층3 주소
                       ★ 주소 모름 + 동명 2곳+ = 재요구 필요 → 배치는 중단 기록
  3 사람 확인        → 확인 카드 생성 + 자동 [맞습니다] (섀도 — 카드 내용은 기록)
  4 캐시             → 캐시 없음(신규) 기록 · 지문은 5.5 뒤 계산해 저장
  5 판정 (전부 코드)  → 조건1 법인구분 → 조건0 공공기관 → 조건2 감사보고서(3년)
  5.5 공고 판별 3층   → 1층 AI(개정 정본 질문) → 2층 코드 → 3층 지우개(검출=삭제 후 진행)
                       → 요구역량 추출(AI) · 공고 원문은 저장하지 않는다 (S2 — 목록·지문만)
  6 수집             → DART 재무 API + 공시 원본(사업/감사보고서) 조각화.
                       ⚠️ 뉴스·홈페이지 소스 미보유 → 비상장 4-1·4-2·4-3 빈칸+사유 (실측 — 무명 회사는 검색되는 뉴스가 없다)
  7 사전 게이트       → 칸 재료 개략 + 신선도 → 미달 즉시 중단 (생성 0원)
  8·9 생성 (AI 1회)   → 원문 문장 그대로 + 조각 번호 + 블록. 자유 문장 금지
  10 검증            → 코드 W1~W4 대조 + 7칸 판정 + ①-b 알맹이 AI 1회 (보고서 문장만 제공)
  11·12 분기·루프     → 파일럿 생략: 미달 칸은 X·문장은 삭제, 재수집·재생성 루프 없음 (기록)
  13 출력            → 보고서 md — 출처·빈칸 사유·날짜 경고는 프로그램이 붙인다
  14 저장            → runs.jsonl (지표: 비용·시간·단계 결과)

안전: 예산가드 $8 · 체크포인트(건별) · S1(개인정보 값 로그 금지 — 3층 검출은 유형·건수만)
      S2(공고 원문 미저장 — 요구역량 목록·지문만 기록).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional, Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
APP_DIR = PROJECT_ROOT.parent / "app"
for p in (APP_DIR, SRC_DIR, PROJECT_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import anthropic  # noqa: E402

from src.core import pricing as ai_pricing  # noqa: E402
from src.core.clock import subtract_years, today_kst  # noqa: E402
from core.env import load_env  # noqa: E402
from core.logging_util import log_step  # noqa: E402
from core.paths import PUBLIC_ORG_REGISTRY  # noqa: E402
from core.runtime_paths import LOCAL_DATA_DIR, runtime_data_dir  # noqa: E402
from core.dart_client import (  # noqa: E402
    UsageCounter, download_corpcode, download_document, get_json)
from core.naver_client import search_news  # noqa: E402
from features.name_match.logic import build_index, match_layer1, normalize_name  # noqa: E402
from features.judgment.logic import decide  # noqa: E402
from features.public_org.logic import load_public_org_registry, match_public_org  # noqa: E402
from features.posting_gate.logic import layer2  # noqa: E402
from features.privacy_filter.logic import erase  # noqa: E402
from features.fingerprint.logic import posting_fingerprint  # noqa: E402
from features.provider_diagnostics.logic import build_usage_diagnostic  # noqa: E402
from features.cell_check.logic import (  # noqa: E402
    count_amounts, establishment, has_corp_name, news_ok)
from features.draft_check.logic import DraftItem, check_draft  # noqa: E402
from survey_audit_reports import read_filing_text  # noqa: E402  (공시 원문 텍스트화 재사용)

DATA = runtime_data_dir()
PILOT_DIR = DATA / "pilot"
REPORT_DIR = PILOT_DIR / "reports"
FRAG_DIR = PILOT_DIR / "fragments"
RAW_DIR = PILOT_DIR / "raw_filings"
RUNS_PATH = PILOT_DIR / "runs.jsonl"
CORPCODE_DIR = DATA / "corpcode"
# 이 둘은 실행 중 생기는 자료가 아니라 Git에 포함된 읽기 전용 시험 입력이다.
# 영속 디스크로 보내면 새 배포에서 입력을 찾지 못하므로 로컬 원본을 계속 읽는다.
RECOLLECT_DIR = LOCAL_DATA_DIR / "posting_samples_recollect"
OCR_TEXT_DIR = LOCAL_DATA_DIR / "ocr_samples" / "extracted" / "claude-haiku-4-5"
RUN_ID = "파일럿_야간50"

MODEL = "claude-haiku-4-5"          # 확정 운영 모델 (3단 조합·비용 목표 정합)
BUDGET_STOP_USD = 8.0
PAID_EXECUTION_DISABLED_MESSAGE = (
    "야간 파일럿의 유료 실행은 중단됐습니다. 이 도구에는 재시작 뒤에도 남는 "
    "attempt 비용 원장과 공용 provider gateway가 아직 없습니다. 두 경계를 붙이기 "
    "전에는 ANTHROPIC_API_KEY를 읽거나 provider를 호출할 수 없습니다."
)
AI_NAME_RETRY_MAX = 5               # 층2 재시도 상한 (OWASP LLM06 가드레일)
ANTHROPIC_TIMEOUT_SEC = 180.0        # 단일 provider 호출 worker 점유 상한
AUDIT_WINDOW_YEARS = 3              # 조건 2 창 — 잠정 3년
FRAG_CHARS = 1200                   # 조각 길이 상한
MAX_FRAGS = 14                      # 생성 입력 조각 수 상한 (비용·맥락 관리)
GEN_MAX_TOKENS = 3000

# 실캡처 5건의 회사명 — 정본은 OCR 비교 실측 기록의 확인 결과다.
# 1차 실행은 3건(원티드-01·02, 잡코리아-01)을 기억으로 잘못 적어 감시에
# 적발됐다 → 실측 기록 기준으로 정정·재실행.
OCR_INPUTS = {
    "원티드-01": ("에이블리코퍼레이션", "데이터분석가"),
    "원티드-02": ("아티언스", "AI서비스기획자"),
    "자사홈페이지-02": ("로보스타", "연구개발"),
    "자사홈페이지-03": ("하이브", "매니지먼트"),
    "잡코리아-01": ("투핸즈인터랙티브", "에듀테크 솔루션 영업"),
}
# 입력의 「주소」 칸 — 실사용자는 지원 회사의 지역을 안다. 확신 있는 회사만 채우고
# 나머지는 「모름」(정본 기준이 허용) — 동명 2곳+에서 모름이면 중단되는 것 자체가 실측이다.
PILOT_ADDRESSES = {
    "카카오": "제주 제주시",            # 골든셋 사용자 확인 표기
    "카카오페이": "경기 성남시",
    "카카오모빌리티": "경기 성남시",
    "카카오헬스케어": "경기 성남시",
    "하이브": "서울 용산구",
    "한글과컴퓨터": "경기 성남시",
}
LAYER3_CANDIDATE_CAP = 15           # 동명 후보 전부 조회 상한 (에스엠 11곳 실측 대비 여유)

# ── 공시 원문 조각화 — 칸 재료가 되는 절 표제 (상장·비상장 공용) ──────────────
SECTION_HEADS: dict[str, tuple[str, ...]] = {
    "사업내용": ("사업의 내용", "사업의내용", "회사의 개요", "회사의개요", "일반사항",
             "회사의 개황", "일반적 사항"),
    "수익인식": ("수익인식", "수익 인식", "매출의 인식", "매출인식", "수익의 인식"),
    "재무": ("재무제표", "재무에 관한 사항", "요약재무정보"),
    "MD&A": ("이사의 경영진단", "경영진단 및 분석", "경영진단및분석"),
    "연구개발": ("연구개발", "주요 계약", "주요계약"),
    "특수관계자": ("특수관계자",),
    "판관비": ("판매비와관리비", "판매비및관리비", "영업비용"),
    "매출수주": ("매출 및 수주", "수주상황", "매출에 관한 사항"),
}
# 칸 ← 조각 종류 매핑
# 네이버 뉴스 연결: 뉴스가 소스인 칸(2·4축)에 「뉴스」 추가
CELL_SOURCES: dict[str, tuple[str, ...]] = {
    "1": ("사업내용", "수익인식"),
    "2": ("사업내용", "연구개발", "뉴스"),
    "3": ("재무",),
    "4-1": ("MD&A", "뉴스"),
    "4-2": ("MD&A", "연구개발", "뉴스"),
    "4-3": ("MD&A", "뉴스"),
    # 파트너·유통 구조는 사업보고서의 사업내용/주요계약에도 실린다. 거래처
    # 주석만 허용하면 JYP의 Sony·TME·Republic·Live Nation 같은 공식 관계가
    # 수집돼도 9번 후보가 될 수 없다.
    "9": ("사업내용", "연구개발", "매출수주", "특수관계자"),
}
MAX_NEWS_FRAGS = 6                  # 뉴스 조각 상한 (채택 조건 통과분만)
VOTE_ROUNDS = 3                     # 판정 반복 횟수 — 같은 자료로 반복 후 다수결
VOTE_MIN = 2                        # 이 횟수 이상 채워진 칸만 인정 (다수결)
OTHER_CORP_MAX = 3                  # 제목·본문의 다른 회사명 이 수 이상이면 종목 나열 기사로 보고 버림
BLOCK_ORDER = ("1", "2", "3", "4-1", "4-2", "4-3", "5", "6", "7", "8", "9")
POSTING_BLOCKS = ("5", "6", "7", "8")

# 기사 속 「다른 회사」 세기용 — 회사 표기가 붙은 이름만 센다 (종목 나열 기사 판별)
_OTHER_CORP_RE = re.compile(r"(?:㈜|\(주\)|주식회사)\s*([가-힣A-Za-z0-9&]{2,15})"
                            r"|([가-힣A-Za-z0-9&]{2,15})\s*(?:㈜|\(주\))")

_spent_usd = 0.0


def _client() -> anthropic.Anthropic:
    # SDK 내부 retry는 호출별 예상비용·계수 경계를 다시 거치지
    # 않는다. 자동 retry를 끄고 단일 호출 대기 상한을 명시한다.
    return anthropic.Anthropic(max_retries=0, timeout=ANTHROPIC_TIMEOUT_SEC)


def _ask(client: anthropic.Anthropic, prompt: str, schema: dict[str, Any],
         max_tokens: int = 700) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """구조화 출력 1회 호출 — (payload, 사용량). 예산가드는 호출 전에 확인."""
    global _spent_usd
    if _spent_usd > BUDGET_STOP_USD:
        raise RuntimeError(f"예산가드: 누적 ${_spent_usd:.2f} > ${BUDGET_STOP_USD}")
    t0 = time.perf_counter()
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=max_tokens, temperature=0,  # 원문 복사 충실도 우선
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIError as e:
        return None, {"error": type(e).__name__, "elapsed": round(time.perf_counter() - t0, 1)}
    cost_krw = ai_pricing.usage_cost_krw(
        MODEL, resp.usage.input_tokens, resp.usage.output_tokens
    )
    cost = cost_krw / ai_pricing.AI_COST_KRW_PER_USD
    _spent_usd += cost
    usage = {
        "in": resp.usage.input_tokens,
        "out": resp.usage.output_tokens,
        "usd": round(cost, 6),
        "krw": cost_krw,
        "elapsed": round(time.perf_counter() - t0, 1),
        **build_usage_diagnostic(
            stop_reason=getattr(resp, "stop_reason", None),
            output_tokens=resp.usage.output_tokens,
            requested_max_tokens=max_tokens,
        ),
    }
    if resp.stop_reason == "refusal":
        return None, {**usage, "refusal": True, "error": "refusal"}
    try:
        return json.loads(resp.content[0].text), usage
    except (ValueError, IndexError):
        return None, {
            **usage,
            **build_usage_diagnostic(
                stop_reason=getattr(resp, "stop_reason", None),
                output_tokens=resp.usage.output_tokens,
                requested_max_tokens=max_tokens,
                parse_failed=True,
            ),
            "error": "파싱실패",
        }


# ── 2번 식별 ──────────────────────────────────────────────────────────────

def identify(client: anthropic.Anthropic, company: str, address: str,
             index: dict[str, list[str]], counter: UsageCounter,
             steps: list[dict[str, Any]]) -> Optional[str]:
    """층1 → (0건이면) 층2 AI 5회 → 동명이면 층3 주소 좁힘. 반환 = corp_code 1개.

    층3: 후보 **전부** DART 기업개황 조회 → 입력 주소(시/도+구/군)로
    좁힌다. 모름이면 동명 2곳+에서 재요구 — 배치는 재요구 불가라 중단 기록.
    좁혀서 정확히 1곳이면 확정. 0곳(불일치)은 정본상 「차단 안 함 + 1개 제시 경고」지만
    배치는 사람 확인이 없어 오확정 위험 → 중단 기록 (배치 제약, 아침 보고).
    """
    stage, hits = match_layer1(company, index)
    steps.append({"step": "2_식별_층1", "걸린변형": stage, "후보수": len(hits)})
    if not hits:
        # 웹(뉴스) 검색 보강 — 기사에서 법인명 표기를 뽑아 DART 대조.
        # 사명 변경(한글과컴퓨터→한컴)·브랜드명 문제의 1차 방어. 실패하면 AI 5회로.
        try:
            news = search_news(company, display=10)
        except Exception:
            news = []
        name_re = re.compile(r"(?:㈜|\(주\)|주식회사)\s*([가-힣A-Za-z0-9&]{2,15})"
                             r"|([가-힣A-Za-z0-9&]{2,15})\s*(?:㈜|\(주\))")
        # 가드레일 (오식별 실측 — 네오와이즈→네오펄스): ① 입력 회사명을 실제로
        # 언급한 기사만 본다 ② 서로 다른 기사 2건 이상에 등장한 표기만 후보 (한 기사에 스친
        # 무관 법인명 배제). 그래도 사람 확인 카드가 최종 방어선이다 — 배치는 자동이라 한계.
        cand_counts: dict[str, int] = {}
        cand_first: dict[str, str] = {}
        for it in news:
            text = f"{it.title} {it.description}"
            if company not in text:
                continue
            in_this: set[str] = set()
            for m in name_re.finditer(text):
                cand = m.group(1) or m.group(2)
                norm = normalize_name(cand)
                if norm in in_this or norm == normalize_name(company):
                    continue
                in_this.add(norm)
                cand_counts[norm] = cand_counts.get(norm, 0) + 1
                cand_first.setdefault(norm, cand)
        for norm, count in sorted(cand_counts.items(), key=lambda kv: -kv[1]):
            if count < 2:
                break
            _, web_hits = match_layer1(cand_first[norm], index)
            if web_hits:
                hits = web_hits
                steps.append({"step": "2_식별_뉴스보강", "채택명": cand_first[norm],
                              "등장기사수": count, "후보수": len(web_hits),
                              "비고": "입력사 언급 기사 2건+ 등장 표기 → DART 실재 대조"})
                break
        if not hits:
            steps.append({"step": "2_식별_뉴스보강", "결과": "후보 없음",
                          "추출표기수": len(cand_counts)})
    if not hits:
        tried: list[str] = []
        schema = {"type": "object", "properties": {"names": {
            "type": "array", "items": {"type": "string"}}},
            "required": ["names"], "additionalProperties": False}
        for attempt in range(1, AI_NAME_RETRY_MAX + 1):
            payload, usage = _ask(client, (
                f"한국 회사 「{company}」의 DART(전자공시) 등록 법인명 후보를 1~3개 답하라. "
                f"브랜드명·서비스명이면 운영 법인명으로. 이미 시도한 것 제외: {tried or '없음'}. "
                "지어내지 말고 실제 법인명 가능성이 높은 것만."), schema)
            steps.append({"step": "2_식별_층2AI", "회차": attempt, "usage": usage,
                          "후보": (payload or {}).get("names")})
            if not payload:
                continue
            for name in payload["names"]:
                tried.append(name)
                # 가드레일: AI 답이 DART 목록에 실재하는지 코드 대조 — 없으면 버림
                _, ai_hits = match_layer1(name, index)
                if ai_hits:
                    hits = ai_hits
                    steps.append({"step": "2_식별_층2확정", "채택명": name, "후보수": len(ai_hits)})
                    break
            if hits:
                break
        if not hits:
            steps.append({"step": "2_식별_종료", "결과": "못찾음",
                          "안내": "이름 재입력 안내 (배치라 재입력 불가 — 종료)"})
            return None
    if len(hits) > 1:
        if address == "모름":
            # 동명 2곳+ & 주소 모름 → 재요구 — 배치는 불가 → 중단
            steps.append({"step": "2_식별_층3", "결과": "동명 재요구 필요",
                          "후보수": len(hits), "주소": "모름",
                          "안내": "주소 재요구 필요 — 배치라 불가, 중단 기록"})
            return None
        tokens = address.split()
        matched: list[str] = []
        for code in hits[:LAYER3_CANDIDATE_CAP]:      # 후보 전부 조회 (개정 — 전부 넘김)
            prof = get_json("company.json", {"corp_code": code}, counter)
            adres = prof.get("adres") or ""
            if all(t in adres for t in tokens):
                matched.append(code)
        steps.append({"step": "2_식별_층3", "후보수": len(hits), "주소": address,
                      "조회수": min(len(hits), LAYER3_CANDIDATE_CAP), "좁힌후": len(matched)})
        if len(matched) == 1:
            return matched[0]
        결과 = "주소로도 여럿 — 1개 제시+개수 알림 필요" if matched else "주소 불일치 — 전 후보 탈락"
        steps.append({"step": "2_식별_층3종료", "결과": 결과,
                      "안내": "정본은 차단 안 함(사람 확인) — 배치는 오확정 방지 위해 중단"})
        return None
    return hits[0]


# ── 6번 수집 ──────────────────────────────────────────────────────────────

#: 원문으로 쓸 공시를 «어떤 순서»로 찾을지. (공시유형 코드, 보고서 이름).
#:
#: ★ 비상장은 「사업보고서」를 «먼저» 찾는다. 실측 근거:
#:   현대카드(비상장)는 `pblntf_ty="F"`(외부감사관련)에 **0건**(status 013)인데
#:   `pblntf_ty="A"`(정기공시)에는 **사업보고서가 3건** 있다.
#:   대형 비상장사는 감사보고서를 «따로 내지 않고» 사업보고서에 첨부해 낸다
#:   (외부감사법 23조① 단서 — 첨부해 내면 감사인이 제출한 것으로 «본다»).
#:
#: ⚠️ 이 순서를 안 지키면 무슨 일이 나나 — 「감사보고서」만 찾다 못 찾으면 이 함수가 None 을
#:   돌려주고, 원문이 «빈 문자열»이 되고, 조각이 **0개**가 된다. 그러면 재무 API
#:   숫자만으로 보고서가 쓰여 **각 장이 한 문장씩**인 껍데기가 나온다.
#:   «판정»만 고치고(재무 자료가 있으면 대상) 이 «수집»을 안 고치면,
#:   문은 열어 주고 안에는 아무것도 안 넣어 주는 상태가 된다.
#:
#: ★ 감사보고서 폴백을 «지운 것이 아니다». 사업보고서를 안 내는 비상장 중소기업은
#:   감사보고서가 유일한 원문이다. 순서만 바꿨다.
FILING_LOOKUP_ORDER: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "상장사": (("A", "사업보고서"),),
}
#: 상장사가 아닌 모든 구분(비상장 외감 등)이 쓰는 순서.
FILING_LOOKUP_DEFAULT: Final[tuple[tuple[str, str], ...]] = (
    ("A", "사업보고서"),
    ("F", "감사보고서"),
)
#: 본문으로 고른 1건과 별개로, 같은 조회에서 확인한 기재·첨부 정정까지
#: completion 출처 지문에 싣는 비민감 필드. 원문·회사정보는 넣지 않는다.
SOURCE_IDENTITY_RCEPT_NOS_KEY: Final[str] = "source_identity_rcept_nos"
# DART list.json은 재무 API의 ``reprt_code``를 돌려주지 않는다. 원문 선택기가
# 사업/감사보고서 annual 경로에서 실제로 골랐다는 사실을 공급자 필드인 척
# 만들지 않고 별도 내부 신원으로 운반한다. 비교 생산기는 이 값과 report_nm,
# 접수번호, 접수일, 완료 사업연도를 함께 확인한다.
SELECTED_REPORT_PERIOD_KIND_KEY: Final[str] = "engine_selected_report_period_kind"
SELECTED_REPORT_PERIOD_KIND_ANNUAL: Final[str] = "annual"


def latest_report_rcept(
    corp_code: str,
    corp_type: str,
    counter: UsageCounter,
    *,
    business_date: Optional[dt.date] = None,
) -> Optional[dict[str, Any]]:
    """최신 원문 1건.

    상장은 사업보고서(A)만 본다. 비상장은 **사업보고서를 먼저** 보고,
    없을 때만 감사보고서(F)로 넘어간다 (`FILING_LOOKUP_ORDER` 머리말 참고).
    """
    effective_date = business_date or today_kst()
    for ty, want in FILING_LOOKUP_ORDER.get(corp_type, FILING_LOOKUP_DEFAULT):
        found = _latest_of_kind(
            corp_code,
            ty,
            want,
            counter,
            business_date=effective_date,
        )
        if found is not None:
            return found
    return None


def _latest_of_kind(
    corp_code: str,
    ty: str,
    want: str,
    counter: UsageCounter,
    *,
    business_date: Optional[dt.date] = None,
) -> Optional[dict[str, Any]]:
    """공시 유형 하나에서 「원하는 이름」의 최신 보고서를 고른다. 없으면 None."""
    end = business_date or today_kst()
    bgn = subtract_years(end, AUDIT_WINDOW_YEARS)
    payload = get_json("list.json", {
        "corp_code": corp_code, "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"), "pblntf_ty": ty, "page_count": "100"}, counter)
    if not isinstance(payload, dict):
        raise RuntimeError("DART 최신 공시목록 응답 모양이 올바르지 않습니다")
    status = payload.get("status")
    if not isinstance(status, str):
        raise RuntimeError("DART 최신 공시목록 상태값 모양이 올바르지 않습니다")
    raw_rows = payload.get("list")
    if status == "013":
        if raw_rows not in (None, []):
            raise RuntimeError("DART 최신 공시목록 013 응답에 비어 있지 않은 목록이 있습니다")
        return None
    if status != "000":
        raise RuntimeError("DART 최신 공시목록 응답이 정상 상태가 아닙니다")
    if not isinstance(raw_rows, list) or not all(isinstance(row, dict) for row in raw_rows):
        raise RuntimeError("DART 최신 공시목록 성공 응답의 목록 모양이 올바르지 않습니다")
    rows = [r for r in raw_rows
            if want in (r.get("report_nm") or "") and "연결" not in (r.get("report_nm") or "")]
    if not rows:
        return None
    # 「[첨부정정]」 공시의 원본 zip엔 본문이 없고 고친 첨부만 있다 (로보스타 실측) — 본문 있는 공시 우선
    main_rows = [r for r in rows if "첨부정정" not in (r.get("report_nm") or "")]
    selected = dict(max(main_rows or rows, key=lambda r: r["rcept_no"]))
    selected[SELECTED_REPORT_PERIOD_KIND_KEY] = SELECTED_REPORT_PERIOD_KIND_ANNUAL
    # 본문 zip은 첨부정정을 피해서 고르되, 정정 사실까지 출처 신원에서 지우면
    # 같은 원문 번호만 보고 옛 캐시가 살아난다. 조회 범위의 같은 보고서 종류
    # 접수번호를 전부 싣고, delivery 쪽에서 14자리·중복·순서를 다시 검산한다.
    selected[SOURCE_IDENTITY_RCEPT_NOS_KEY] = sorted(
        {
            str(row.get("rcept_no") or "").strip()
            for row in rows
            if str(row.get("rcept_no") or "").strip()
        }
    )
    return selected


#: 재무 조회에서 «정상»으로 볼 상태값. 그 밖은 기술 실패다.
#: 000 = 정상 · 013 = 조회 범위에 자료 없음(빈 결과이지 오류가 아니다).
#: 인증(010~012·901)·한도(020)는 dart_client.get_json 이 «이미» 예외로 터뜨린다
#: — 그래서 여기까지 오지 않는다. 901(키 만료)도 인증 쪽에서 먼저 잡는다.
FINANCIALS_OK_STATUSES: Final[frozenset[str]] = frozenset({"000", "013"})


def fetch_financials(
    corp_code: str,
    counter: UsageCounter,
    *,
    business_date: Optional[dt.date] = None,
) -> tuple[Optional[dict], list[int]]:
    """단일회사 주요계정 — 최근 3개 사업연도 시도. (첫 성공 payload, 성공 연도들).

    ★ 왜 오류를 «터뜨리나»
      예전에는 status 가 000 이 아니면 «전부» 조용히 건너뛰고 빈 결과를 돌려줬다.
      그래서 DART 시스템 점검(800)·정의되지 않은 오류(900)·부적절한 값(100)·
      부적절한 접근(101)·조회 가능 회사 수 초과(021) 같은
      **기술 실패가 「이 회사는 재무 자료가 없다」로 둔갑**했다.
      (901 은 «키 만료»라 성격이 다르다 — dart_client 가 인증 오류로 먼저 잡는다.)
      이 값이 판정에 쓰이게 되면서(조건 2-b) 그 둔갑은 곧바로 «거부»가 된다 —
      점검 시간에 멀쩡한 회사가 「분석할 근거가 없다」는 화면을 보게 된다.
      공시 목록 쪽은 이미 같은 원칙을 지키고 있다(app/.../real.py 의 013 처리):
      **「빈 결과」와 「못 물어봄」은 다르다.**
    """
    got, years = None, []
    this_year = (business_date or today_kst()).year
    for year in range(this_year - 1, this_year - 4, -1):
        payload = get_json("fnlttSinglAcnt.json", {
            "corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}, counter)
        status = payload.get("status")
        if status not in FINANCIALS_OK_STATUSES:
            raise RuntimeError("DART 재무 조회 응답이 정상 상태가 아닙니다")
        if status == "000":
            years.append(year)
            if got is None:
                got = payload
    return got, years


def make_fragments(filing_text: str, financials: Optional[dict]) -> dict[int, dict[str, str]]:
    """공시 원문에서 절 표제 기준으로 조각을 뽑는다 — {id: {종류, 원문}}."""
    frags: dict[int, dict[str, str]] = {}
    fid = 1
    for kind, heads in SECTION_HEADS.items():
        for head in heads:
            for m in re.finditer(re.escape(head), filing_text):
                chunk = filing_text[m.start(): m.start() + FRAG_CHARS].strip()
                if len(chunk) > 200:
                    frags[fid] = {"종류": kind, "원문": chunk}
                    fid += 1
                break  # 표제당 첫 출현만
            if any(f["종류"] == kind for f in frags.values()):
                break  # 종류당 1조각 (경량)
    if financials:
        rows = financials.get("list", [])[:14]
        text = " · ".join(f"{r.get('account_nm')} {r.get('thstrm_amount')}"
                          f"({r.get('thstrm_dt', '')})" for r in rows if r.get("account_nm"))
        if text:
            frags[fid] = {"종류": "재무", "원문": "주요계정(DART API): " + text}
    return dict(list(frags.items())[:MAX_FRAGS])


def collect_news(company: str, profile: dict[str, Any], homonym_count: int,
                 steps: list[dict[str, Any]], *,
                 business_date: Optional[dt.date] = None) -> list[dict[str, str]]:
    """6번 뉴스 수집 — 채택 조건(소스정책: 제목 회사명·3년·동명 시 본문 단서) 통과분만.

    동명 단서는 기업개황의 대표자·주소 토큰과 대조한다.
    한계(기록): 조건 「타사 3개 미만」은 제목 내 타사 수를 셀 방법이 없어 미적용.
    """
    try:
        items = search_news(company, display=20, sort="date")
    except Exception as exc:  # 한도·인증·네트워크 — 사유만 기록하고 뉴스 없이 진행
        steps.append({"step": "6_수집_뉴스", "오류": f"{type(exc).__name__}: {str(exc)[:80]}"})
        return []
    today = business_date or today_kst()
    ceo = (profile.get("ceo_nm") or "").strip().split(",")[0].strip()
    adres_tokens = [t for t in (profile.get("adres") or "").split()[:2] if len(t) >= 2]
    # 동명 단서를 대표자·주소에 더해 업종·제품·계열까지 인정한다.
    # 유명 회사일수록 기사에 주소를 안 적어 과차단됐다(카카오 20건 중 1건 채택 실측).
    industry = [t for t in (profile.get("induty_code_nm") or "").replace("및", " ").split()
                if len(t) >= 2][:3]
    group_hint = [company[:2]] if len(company) >= 2 else []
    hints = [t for t in (ceo, *adres_tokens, *industry, *group_hint) if t]
    accepted = []
    dropped_listy = 0
    for it in items:
        if it.pub_date is None:
            continue
        # 종목 나열 기사 차단 — 제목·본문의 「다른」 회사명 표기 수를 센다
        blob = f"{it.title} {it.description}"
        others = {n for m in _OTHER_CORP_RE.findall(blob) for n in m if n and n not in company}
        if len(others) >= OTHER_CORP_MAX:
            dropped_listy += 1
            continue
        hint_found = any(t in blob for t in hints)
        if news_ok(it.title, company, it.pub_date, today, other_companies_in_title=0,
                   homonym_count=homonym_count, identity_hint_found=hint_found):
            accepted.append(it)
        if len(accepted) >= MAX_NEWS_FRAGS:
            break
    steps.append({"step": "6_수집_뉴스", "검색결과": len(items), "채택": len(accepted),
                  "동명수": homonym_count, "나열기사_차단": dropped_listy,
                  "비고": "조건 5종 전부 적용 — 제목 회사명·3년·언론사·타사 3개 미만·"
                          "동명시 단서(대표자·주소·업종·계열)"})
    # 언론사 표기(소스정책 규칙 2) — API가 매체명을 안 줘 원 기사 도메인으로 대신한다
    return [{"종류": "뉴스", "원문": f"({it.pub_date} 보도 · "
             f"{urllib.parse.urlparse(it.originallink or it.link).netloc or '출처미상'}) "
             f"{it.title}. {it.description}"}
            for it in accepted]


# ── 8·9 생성 + 10 검증 ────────────────────────────────────────────────────
# 생성 계약: AI가 문장을 「쓰지」 않는다.
# 조각을 문장 단위로 쪼개 번호를 붙이고, AI는 번호만 고른다 — 원문 복사는 프로그램이 한다.
# 파일럿 실측에서 AI가 온도 0·강한 지시로도 원문을 바꿔 써 W3가 절반을 지웠기 때문(성립 0건).
# W1~W4 검사는 안전망으로 그대로 돌린다.

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
MIN_SENT_CHARS = 20           # 이보다 짧은 토막은 문장 후보에서 제외 (표 찌꺼기)
MAX_SENTS_PER_FRAG = 12


def split_sentences(text: str) -> list[str]:
    pieces = [s.strip() for s in _SENT_SPLIT_RE.split(text)
              if len(s.strip()) >= MIN_SENT_CHARS]
    # 조각 절단면의 미완성 꼬리 제거 — 「…카카오페」처럼 끊긴 문장이 보고서에 실리는 것 방지 (감시 발견)
    if pieces and not pieces[-1].rstrip().endswith((".", "!", "?", "다", "음", "됨", ")")):
        pieces = pieces[:-1]
    return pieces[:MAX_SENTS_PER_FRAG]


def generate_and_check(client: anthropic.Anthropic, frags: dict[int, dict[str, str]],
                       requirements: list[str], job: str,
                       steps: list[dict[str, Any]]) -> tuple[list[DraftItem], list]:
    # 문장 번호표: "3-2" = 조각 3의 2번째 문장 · "R-1" = 요구역량 1번
    sent_map: dict[str, tuple[Optional[int], str]] = {}
    lines: list[str] = []
    for fid, f in frags.items():
        for si, sent in enumerate(split_sentences(f["원문"]), start=1):
            sid = f"{fid}-{si}"
            sent_map[sid] = (fid, sent)
            lines.append(f"[{sid}] ({f['종류']}) {sent[:250]}")
    for ri, req in enumerate(requirements, start=1):
        sent_map[f"R-{ri}"] = (None, req)
    req_lines = "\n".join(f"[R-{ri}] {req}" for ri, req in enumerate(requirements, start=1))
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {
            "block": {"type": "string", "enum": list(BLOCK_ORDER)},
            "sid": {"type": "string"}},
        "required": ["block", "sid"], "additionalProperties": False}}},
        "required": ["items"], "additionalProperties": False}
    payload, usage = _ask(client, (
        "지원동기 재료 보고서의 「사실 뽑기+배치」다. 문장을 쓰지 마라 — **번호만 골라라**.\n"
        "① 아래 번호 문장 중 각 블록에 맞는 것을 골라 {block, sid}로 답하라\n"
        "② 블록과 조건: 1(뭘 팔아 돈 버나 — 사업·수익 방식 서술) 2(뭘 잘하나 — 기술·강점) "
        "3(요즘 잘 되나 — **금액·숫자가 든 실적 문장만**) 4-1(지금 문제 — 회사가 직접 밝힌 과제) "
        "4-2(요즘 하는 일) 4-3(앞으로 방향) 9(누구에게 파나 — **거래처·발주처·고객사 실명이 든 "
        "문장만.** 목적사업 나열·서비스 소개는 9가 아니다) — 4축·9는 회사가 직접 말한 문장만, "
        "추론 금지. 맞는 문장이 없는 블록은 내지 마라\n"
        "③ 블록 5·6·7·8(공고)은 [R-번호] 요구역량을 전부 나눠 배치하라\n"
        f"지원 직무: {job}\n\n[조각 문장]\n" + "\n".join(lines) +
        f"\n\n[요구역량]\n{req_lines}"), schema, max_tokens=GEN_MAX_TOKENS)
    steps.append({"step": "8·9_생성(스팬선택)", "usage": usage, "문장후보수": len(sent_map),
                  "선택수": len((payload or {}).get("items", []))})
    if not payload:
        return [], []
    items: list[DraftItem] = []
    bad_sids = 0
    used_sids: set[str] = set()
    for it in payload["items"]:
        picked = sent_map.get(it["sid"])
        if picked is None:                      # 실재하지 않는 번호 — W2에 해당, 버림
            bad_sids += 1
            continue
        used_sids.add(it["sid"])
        fid, sentence = picked
        items.append(DraftItem(sentence=sentence, fragment_id=fid, block=it["block"]))
    # 요구역량 완전성 검사 (감시 발견 — AI가 일부 R번호를 소리 없이 빼먹음):
    # 배치 안 된 요구역량은 프로그램이 5번 블록에 원문 그대로 되살린다 (조용한 누락 금지)
    restored = 0
    for ri in range(1, len(requirements) + 1):
        sid = f"R-{ri}"
        if sid not in used_sids:
            items.append(DraftItem(sentence=sent_map[sid][1], fragment_id=None, block="5"))
            restored += 1
    result = check_draft(items, {i: f["원문"] for i, f in frags.items()}, requirements)
    steps.append({"step": "10_검증_W대조", "유지": len(result.kept), "삭제": len(result.deleted),
                  "없는번호": bad_sids, "요구역량_복원": restored,
                  "삭제사유": [w for _, w in result.deleted][:6]})
    return result.kept, result.deleted


def cell_pattern_ok(kept: list[DraftItem]) -> dict[str, bool]:
    """칸 판정에 형태 검사를 건다 — 9번에 목적사업 나열이 실리던 문제를 막는다.

    한계: 표기 없는 영문 거래처명(MRF 등)은 못 센다 — 미달 방향(안전)이라 감수하고 기록.
    """
    text9 = " ".join(it.sentence for it in kept if it.block == "9")
    text3 = " ".join(it.sentence for it in kept if it.block == "3")
    return {"9": has_corp_name(text9) or "거래없음" in text9.replace(" ", ""),
            "3": count_amounts(text3) >= 1}


def substance_check(client: anthropic.Anthropic, kept: list[DraftItem],
                    steps: list[dict[str, Any]]) -> dict[str, bool]:
    """①-b 알맹이 검사 — 보고서 문장만 준다 (자료를 주면 안 된다 · 다른 프롬프트/컨텍스트)."""
    by_cell: dict[str, list[str]] = {}
    for it in kept:
        if it.block in CELL_SOURCES:
            by_cell.setdefault(it.block, []).append(it.sentence)
    if not by_cell:
        return {}
    lines = "\n".join(f"{c}: " + " / ".join(s[:150] for s in ss) for c, ss in by_cell.items())
    cell_props = {c: {"type": "boolean"} for c in CELL_SOURCES}
    schema = {"type": "object", "properties": {"cells": {
        "type": "object", "properties": cell_props,
        "required": list(CELL_SOURCES), "additionalProperties": False}},
        "required": ["cells"], "additionalProperties": False}
    payload, usage = _ask(client, (
        "다음은 어떤 회사 보고서의 칸별 문장이다. 칸마다 판정하라: 회사 이름을 다른 회사로 "
        "바꿔도 그대로 말이 되는 일반론이면 false(알맹이 없음), 이 회사여야만 성립하는 "
        "구체 내용이면 true. 문장이 없는 칸은 false. 문장 외 정보는 없다.\n" + lines), schema)
    steps.append({"step": "10_검증_①b알맹이", "usage": usage, "판정": (payload or {}).get("cells")})
    return (payload or {}).get("cells", {})


# ── 13 출력 ──────────────────────────────────────────────────────────────

EMPTY_REASONS = {
    "4-1": "채택 조건(제목 회사명·3년·동명 단서)을 통과한 기사 없음 · MD&A 재료 없음",
    "4-2": "채택 기사·주요계약/R&D 재료 없음 (회사 채용페이지 소스는 미연결)",
    "4-3": "채택 기사·MD&A 재료 없음 (홈페이지·IR 소스는 미연결)",
    "2": "사업 서술·채택 기사 재료 없음 (홈페이지·기술블로그 소스는 미연결)",
}


def write_report(run: dict[str, Any], kept: list[DraftItem],
                 frags: dict[int, dict[str, str]], cells: dict[str, bool], *,
                 business_date: Optional[dt.date] = None) -> Path:
    generated_on = business_date or today_kst()
    lines = [f"# {run['input']['company']} · {run['input']['job']} — 지원동기 재료 보고서 (파일럿)",
             "", f"> 유형: {run.get('corp_type')} · 생성 {generated_on.isoformat()} · "
             "자료 연도는 각 출처 참조 — 3년 밖 자료 없음(신선도 O9)", ""]
    by_block: dict[str, list[DraftItem]] = {}
    for it in kept:
        by_block.setdefault(it.block, []).append(it)
    names = {"1": "뭘 팔아서 돈 버나", "2": "뭘 잘하나", "3": "요즘 잘 되고 있나",
             "4-1": "지금 뭐가 문제인가", "4-2": "요즘 뭘 하고 있나", "4-3": "앞으로 어디로 가나",
             "5": "요구역량(공고)", "6": "요구역량(공고)", "7": "요구역량(공고)",
             "8": "요구역량(공고)", "9": "누구에게 파나"}
    for block in BLOCK_ORDER:
        items = by_block.get(block, [])
        if block in ("6", "7", "8") and not items:
            continue  # 공고 블록은 5에 몰아 담겨도 무방 (경량)
        lines.append(f"## {block}. {names[block]}")
        if not items:
            reason = EMPTY_REASONS.get(block, "수집 자료에 해당 재료 없음")
            lines.append(f"- (빈칸) 사유: {reason}")
        elif block in CELL_SOURCES and cells.get(block) is False:
            lines.append(f"- (칸 X — 알맹이 검사 미달) 문장 {len(items)}건 보류")
        else:
            for it in items:
                src = f" 〔조각 {it.fragment_id}·{frags[it.fragment_id]['종류']}〕" \
                    if it.fragment_id in frags else " 〔공고〕"
                lines.append(f"- {it.sentence}{src}")
        lines.append("")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{run['id']}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── 본체 ─────────────────────────────────────────────────────────────────

def build_inputs() -> list[dict[str, str]]:
    inputs = []
    manifest = (RECOLLECT_DIR / "manifest.jsonl").read_text(encoding="utf-8")
    for ln in manifest.splitlines():
        row = json.loads(ln)
        inputs.append({"id": f"재수집-{row['file'][:4]}", "company": row["company"],
                       "job": row.get("title", "")[:40] or "미상",
                       "address": PILOT_ADDRESSES.get(row["company"], "모름"),
                       "posting_path": str(RECOLLECT_DIR / row["file"])})
    for pid, (company, job) in OCR_INPUTS.items():
        inputs.append({"id": f"실캡처-{pid}", "company": company, "job": job,
                       "address": PILOT_ADDRESSES.get(company, "모름"),
                       "posting_path": str(OCR_TEXT_DIR / f"{pid}.txt")})
    return inputs


def run_one(item: dict[str, str], ctx: dict[str, Any]) -> dict[str, Any]:
    client, index, registry, counter = ctx["client"], ctx["index"], ctx["registry"], ctx["counter"]
    business_date = today_kst()
    address = item.get("address", "모름")
    steps: list[dict[str, Any]] = [
        {"step": "1_계정할당량", "결과": "배치 모드 생략 (계정·차감 없음)"},
        {"step": "1.5_입력", "경로": "텍스트", "회사명": item["company"],
         "직무": item["job"], "주소": address},
    ]
    run: dict[str, Any] = {"id": item["id"], "input": {"company": item["company"],
                           "job": item["job"], "주소": address}, "steps": steps}
    # 재실행 시 이전 실행의 산출물을 먼저 지운다 — 안 지우면 옛 보고서(오식별분 포함)가
    # 최종 기록과 어긋난 채 남는다 (감시 발견 — 오토콘테크/보해양조 오염)
    for stale in (REPORT_DIR / f"{item['id']}.md", FRAG_DIR / f"{item['id']}.json"):
        if stale.exists():
            stale.unlink()
    t0 = time.perf_counter()
    spent0 = _spent_usd

    def fin(outcome: str, **extra: Any) -> dict[str, Any]:
        run.update({"outcome": outcome, "elapsed_sec": round(time.perf_counter() - t0, 1),
                    "cost_usd": round(_spent_usd - spent0, 5), **extra})
        return run

    # 2·3 식별 + 사람 확인
    corp_code = identify(client, item["company"], address, index, counter, steps)
    if corp_code is None:
        return fin("중단_식별실패")
    profile = get_json("company.json", {"corp_code": corp_code}, counter)
    if profile.get("status") != "000":
        steps.append({"step": "2_식별_층3", "오류": profile.get("status")})
        return fin("중단_기업개황실패")
    steps.append({"step": "3_사람확인", "확인카드": {
        "회사": profile.get("corp_name"), "주소": (profile.get("adres") or "")[:40],
        "대표": profile.get("ceo_nm"), "설립": profile.get("est_dt"),
        "홈페이지": profile.get("hm_url") or "(없음 — 주소·대표·설립으로 대체)",
        "주소경고": "입력 주소 모름 — 대조 불가 표기"},
        "응답": "[맞습니다] (섀도 자동)"})
    steps.append({"step": "4_캐시", "결과": "캐시 없음 — 신규 (배치)"})

    # 5 판정 (전부 코드)
    end = business_date
    audit = get_json("list.json", {
        "corp_code": corp_code, "bgn_de": subtract_years(end, AUDIT_WINDOW_YEARS)
        .strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
        "pblntf_ty": "F", "page_count": "100"}, counter)
    has_audit = any("감사보고서" in (r.get("report_nm") or "") for r in audit.get("list", []))
    judgment = decide(profile.get("corp_cls", ""), has_audit, profile.get("bizr_no"),
                      lambda b: match_public_org(b, registry))
    steps.append({"step": "5_판정", "status": judgment.status, "유형": judgment.corp_type,
                  "근거": judgment.reason})
    if judgment.status != "대상":
        return fin(f"거부_{judgment.status}")
    run["corp_type"] = judgment.corp_type

    # 5.5 공고 판별 3층 (텍스트 경로 — 판정 통과분만)
    posting = Path(item["posting_path"]).read_text(encoding="utf-8")
    schema_l1 = {"type": "object", "properties": {"is_job_posting": {"type": "boolean"}},
                 "required": ["is_job_posting"], "additionalProperties": False}
    l1, usage = _ask(client, (
        "아래 글이 채용공고인가? 채용공고란 모집 주체인 회사가 사람을 고용하려고 지원자에게 "
        "직접 알리는 공고 본문이다. 채용 소식을 제3자가 전하거나 논평하는 글, 고용 목적이 "
        "아닌 모집 글은 채용공고가 아니다. 애매하면 false.\n\n---\n" + posting), schema_l1)
    l1_pass = bool(l1 and l1.get("is_job_posting"))
    steps.append({"step": "5.5_1층AI", "판정": "공고" if l1_pass else "아님", "usage": usage})
    if not l1_pass:
        return fin("폐기_공고아님_1층")
    l2 = layer2(posting)
    steps.append({"step": "5.5_2층코드", "통과": l2.passed, "항목": list(l2.sections_found),
                  "사유": l2.reason})
    if not l2.passed:
        return fin("폐기_공고아님_2층")
    erased = erase(posting, run_id=RUN_ID)          # 3층 — 검출=삭제 후 진행
    steps.append({"step": "5.5_3층지우개", "검출건수": erased.counts,
                  "비고": "S1 — 유형·건수만 기록"})
    schema_req = {"type": "object", "properties": {"requirements": {
        "type": "array", "items": {"type": "string"}}},
        "required": ["requirements"], "additionalProperties": False}
    req, usage = _ask(client, (
        "아래 채용공고에서 지원자에게 요구·우대하는 역량 문장을 **원문 그대로** 목록으로 뽑아라. "
        "다듬거나 요약하지 마라.\n\n---\n" + erased.text), schema_req, max_tokens=1200)
    requirements = (req or {}).get("requirements", [])
    fp = posting_fingerprint(requirements) if requirements else None
    steps.append({"step": "5.5_요구역량추출", "건수": len(requirements), "지문": fp,
                  "usage": usage, "비고": "공고 원문은 저장하지 않는다 (S2)"})
    if not requirements:
        return fin("중단_요구역량없음")
    run["requirements"] = requirements
    run["fingerprint"] = fp

    # 6 수집
    report = latest_report_rcept(
        corp_code,
        judgment.corp_type,
        counter,
        business_date=business_date,
    )
    filing_text = ""
    if report:
        try:
            path = download_document(report["rcept_no"], RAW_DIR, counter)
            filing_text = read_filing_text(path)
        except (RuntimeError, OSError) as exc:
            steps.append({"step": "6_수집_원문", "오류": str(exc)[:120]})
    financials, fin_years = fetch_financials(
        corp_code,
        counter,
        business_date=business_date,
    )
    frags = make_fragments(filing_text, financials)
    homonym = max((s.get("후보수", 1) for s in steps
                   if s["step"].startswith("2_식별") and s.get("후보수")), default=1)
    for nf in collect_news(
        item["company"],
        profile,
        homonym,
        steps,
        business_date=business_date,
    ):
        frags[max(frags, default=0) + 1] = nf
    FRAG_DIR.mkdir(parents=True, exist_ok=True)
    (FRAG_DIR / f"{item['id']}.json").write_text(
        json.dumps(frags, ensure_ascii=False, indent=1), encoding="utf-8")
    steps.append({"step": "6_수집", "원문": (report or {}).get("report_nm"),
                  "조각수": len(frags), "조각종류": sorted({f['종류'] for f in frags.values()}),
                  "재무API연도": fin_years,
                  "비고": "뉴스 연결됨(채택 조건 적용) · 홈페이지 소스는 미연결"})

    # 7 사전 게이트 — 칸 재료 개략 + 신선도
    kinds = {f["종류"] for f in frags.values()}
    rough = {cell: any(k in kinds for k in srcs) for cell, srcs in CELL_SOURCES.items()}
    ok, reasons = establishment(rough)
    fresh_note = ("최신 공시 미공개 구간 가능" if not fin_years
                  else f"최신 사업연도 {max(fin_years)}")
    steps.append({"step": "7_사전게이트", "개략칸": rough, "성립": ok, "사유": reasons,
                  "신선도": fresh_note})
    if not ok:
        return fin("중단_게이트미달", gate_reasons=reasons)

    # 8·9 생성 + 10 검증 — 같은 자료로 3회 반복 후 다수결.
    # 판정이 문턱(4/7) 근처에서 실행마다 흔들리던 문제 대응. 채택 = 2회 이상 채워진 칸.
    rounds: list[dict[str, bool]] = []
    kept: list[DraftItem] = []
    deleted: list = []
    for round_no in range(1, VOTE_ROUNDS + 1):
        k, d = generate_and_check(client, frags, requirements, item["job"], steps)
        if not k:
            continue
        cells_ai_r = substance_check(client, k, steps)
        pattern_r = cell_pattern_ok(k)
        cells_r = {c: (any(it.block == c for it in k) and cells_ai_r.get(c, True)
                       and pattern_r.get(c, True)) for c in CELL_SOURCES}
        rounds.append(cells_r)
        steps.append({"step": f"10_검증_회차{round_no}", "칸": cells_r, "유지문장": len(k)})
        if len(k) > len(kept):        # 보고서로 낼 본문은 가장 많이 살아남은 회차분
            kept, deleted = k, d
    if not rounds:
        return fin("중단_생성실패")
    cells_ai = {}                     # 아래 최종 계산은 다수결 결과를 쓴다
    final_cells = {c: sum(1 for r in rounds if r.get(c)) >= VOTE_MIN for c in CELL_SOURCES}
    ok2, reasons2 = establishment(final_cells)
    steps.append({"step": "10_검증_7칸(다수결)", "회차수": len(rounds), "칸": final_cells,
                  "회차별_채움수": [sum(1 for v in r.values() if v) for r in rounds],
                  "성립": ok2, "사유": reasons2})
    steps.append({"step": "11·12_분기루프", "결과":
                  "파일럿 생략 — 미달 칸 X·문장 삭제만, 재수집·재생성 루프 없음"})

    # 13 출력 + 14 저장
    report_path = write_report(
        run,
        kept,
        frags,
        final_cells,
        business_date=business_date,
    )
    steps.append({"step": "13_출력", "파일": report_path.name,
                  "빈칸사유": "프로그램이 부착 (AI 아님)"})
    return fin("완료_성립" if ok2 else "완료_성립미달",
               final_cells=final_cells, 미달사유=reasons2,
               kept=len(kept), deleted=len(deleted))


def main() -> None:
    # 이 수동 도구는 웹의 영속 비용 원장 밖에 있다. usage 없는 실패·프로세스
    # 재시작을 0원으로 보는 옛 $8 메모리 가드는 안전하지 않으므로 실행 자체를
    # 먼저 닫는다. ``load_env``보다 앞이어야 secret도 읽지 않는다.
    raise SystemExit(PAID_EXECUTION_DISABLED_MESSAGE)

    # 아래 코드는 과거 파일럿 재현 자료로만 보존한다. 영속 batch attempt 원장과
    # 공용 gateway를 연결할 때 별도 변경으로 다시 활성화해야 한다.
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", type=str, default=None, help="특정 id만 (재실행용, 콤마 구분)")
    args = ap.parse_args()
    loaded = load_env()
    for key in ("DART_API_KEY", "ANTHROPIC_API_KEY"):
        if key not in loaded:
            raise SystemExit(f"{key} 없음 — analysis_engine/.env 확인")
    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    counter = UsageCounter()
    xml = download_corpcode(CORPCODE_DIR, counter)
    from build_goldenset_answer import parse_corpcode  # noqa: E402  (색인 재사용)
    corps = parse_corpcode(xml)
    index = build_index([(c, n) for c, n, _ in corps])
    registry = load_public_org_registry(PUBLIC_ORG_REGISTRY)
    ctx = {"client": _client(), "index": index, "registry": registry, "counter": counter}

    done: set[str] = set()
    if RUNS_PATH.exists():
        done = {json.loads(ln)["id"] for ln in RUNS_PATH.read_text(encoding="utf-8").splitlines()}
    inputs = build_inputs()
    if args.only:
        wanted = set(args.only.split(","))
        inputs = [it for it in inputs if it["id"] in wanted]
        done -= wanted  # 재실행 허용
    todo = [it for it in inputs if it["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"입력 {len(inputs)}건 · 완료 {len(done)}건 · 이번에 {len(todo)}건", flush=True)

    for i, item in enumerate(todo, start=1):
        try:
            run = run_one(item, ctx)
        except Exception as exc:  # 1건 실패가 전체를 멈추지 않게 — 사유 기록
            run = {"id": item["id"], "input": {"company": item["company"], "job": item["job"]},
                   "outcome": "오류", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        with RUNS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run, ensure_ascii=False) + "\n")
        print(f"[{i}/{len(todo)}] {item['id']} {item['company']} → {run['outcome']} "
              f"(누적 ${_spent_usd:.3f})", flush=True)
        if run["outcome"] == "오류" and "예산가드" in run.get("error", ""):
            break
    log_step(RUN_ID, "일괄 실행", {"이번": len(todo)},
             {"누적_usd": round(_spent_usd, 4), "DART_오늘": counter.today_count()})


if __name__ == "__main__":
    main()

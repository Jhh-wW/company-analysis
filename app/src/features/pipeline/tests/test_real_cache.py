"""1층 캐시가 진짜 알맹이(`real.py`)에 «제대로» 꽂혔는지 검사한다.

★ 진짜 엔진을 부르지 않는다. `_engine()`이 돌려주는 것을 **가짜 엔진**으로
  바꿔 끼워, AI 호출 0회·0원으로 파이프라인 전체를 돌린다.
  (진짜로 돌리면 1건당 AI 최대 13회 = 실제 비용이 나간다.)

★ 저장소는 `conftest.py`가 시험마다 임시 폴더로 바꿔 놓는다 (P-62) —
  이 시험은 진짜 DB를 건드리지 않는다.

이 시험이 잡는 것:
  - 같은 회사·같은 공고를 다시 조사할 때 **생성·검증 AI가 또 나가는 것**
  - 공고가 다른데 **남의 옛 보고서가 나가는 것** (정본 §★ 사고 시나리오)
  - 사업연도가 바뀌었는데 **작년 보고서가 계속 나가는 것** (O9 신선도)
  - 캐시가 깨졌을 때 **조사 전체가 같이 죽는 것**
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, ReportTable, UserInput
#: 8·9 생성 지시문임을 알아보는 표시. 글자를 베끼지 않고 «상수를 그대로» 쓴다 —
#: 지시문이 바뀌어도 이 시험이 조용히 어긋나지 않는다.
from src.features.spanselect.constants import PROMPT_PICK

# ── 가짜 엔진이 쓰는 고정값 ───────────────────────────────
CORP_ID = "00126380"
JOB = "백엔드 개발자"
POSTING = "3년 이상 경력\n파이썬 실무 경험"
OTHER_POSTING = "신입 가능\n자바 경험 우대"
#: 재무 API가 「이 연도 자료가 있다」고 답하는 값 — 신선도(O9) 비교 기준이 된다.
FISCAL_YEARS = [2025, 2024]
#: 최신 공시 이름에 찍히는 결산 연도 — 「사업보고서 (2025.12)」의 2025.
#: (1판 실측 116건 전부 이 모양이었다 — `analysis_engine/data/pilot/runs*.jsonl`)
FILING_YEAR = 2025


class _FakeCounter:
    """엔진의 `UsageCounter` 흉내 — 몇 번 불렀는지만 센다."""

    def __init__(self) -> None:
        self.count = 0


class _FakeJudgment:
    """`engine.decide()` 결과 흉내 — 항상 「대상」."""

    status = "대상"
    corp_type = "상장사"


#: 가짜 지우개가 지우는 것 — 휴대전화 번호 모양. 진짜 지우개는 이름·생년월일도 본다.
_PHONE_PATTERN = re.compile(r"\d{2,3}-\d{3,4}-\d{4}")
_MASK = "[삭제:연락처]"


class _FakeDraftItem:
    """엔진이 고른 문장 하나 흉내."""

    def __init__(self, block: str, sentence: str, fragment_id: Optional[int]) -> None:
        self.block = block
        self.sentence = sentence
        self.fragment_id = fragment_id


class _FakeMessages:
    """요청별 계량 client 계약을 지키되 네트워크는 전혀 쓰지 않는다."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            model=kwargs.get("model", "가짜모델"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class _FakeClient:
    """실제 SDK처럼 retry 옵션을 받되 provider 네트워크는 전혀 쓰지 않는다."""

    def __init__(self) -> None:
        self.messages = _FakeMessages()
        self.retry_options: list[int] = []

    def with_options(self, *, max_retries: int):
        assert max_retries == 0
        self.retry_options.append(max_retries)
        return self


class FakeEngine:
    """1판 엔진 흉내. `real.py`가 부르는 이름만 갖고 있다.

    ★ AI를 부르는 자리(`_ask`·`generate_and_check`·`substance_check`)에서
      호출 수를 센다. 캐시가 먹었는지는 «이 숫자»로 판정한다.
    """

    MODEL = "가짜모델"
    RAW_DIR = "(가짜)"
    PUBLIC_ORG_REGISTRY = "(가짜)"
    #: 칸 → 그 칸을 채울 수 있는 조각 종류. 가짜라 두 칸만 둔다.
    CELL_SOURCES = {"1": ("사업",), "4-1": ("사업",)}
    EMPTY_REASONS: dict[str, str] = {}
    UsageCounter = _FakeCounter
    _spent_usd = 0.0

    def __init__(self, fiscal_years: Optional[list[int]] = None) -> None:
        #: 5.5(공고 판별·요구역량 추출) AI 호출 수
        self.posting_ai_calls = 0
        self.posting_ai_prompts: list[str] = []
        #: 8·9 생성 + 10 검증 AI 호출 수 — **캐시가 먹으면 0이어야 한다**
        self.generate_ai_calls = 0
        self.fiscal_years = list(FISCAL_YEARS if fiscal_years is None else fiscal_years)
        #: 최신 공시 이름에 찍히는 결산 연도. None이면 연도가 안 적힌 이름을 준다.
        self.filing_year: Optional[int] = FILING_YEAR
        #: True면 뉴스 수집이 «우리 쪽 실패»(⚠️)로 끝난 것처럼 군다.
        self.news_fails = False
        self.client = _FakeClient()

    # ── 준비 ─────────────────────────────────────────────
    def load_env(self) -> None:
        return None

    def _client(self) -> SimpleNamespace:
        return self.client

    # ── DART ─────────────────────────────────────────────
    def get_json(self, endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        counter.count += 1
        if endpoint == "company.json":
            if params.get("corp_code") == "00999999":
                return {
                    "status": "000",
                    "corp_name": "베타전자",
                    "adres": "서울특별시 영등포구 국제금융로 1",
                    "ceo_nm": "김비교",
                    "est_dt": "20010101",
                    "hm_url": "",
                    "corp_cls": "Y",
                    "stock_code": "999999",
                    "bizr_no": "9999999999",
                }
            return {
                "status": "000",
                "corp_name": "가나다전자",
                "adres": "서울특별시 강남구 테헤란로 1",
                "ceo_nm": "홍길동",
                "est_dt": "20000101",
                "hm_url": "",           # 빈 값 → 홈페이지 수집이 네트워크를 안 탄다
                "corp_cls": "Y",
                "bizr_no": "1234567890",
            }
        if endpoint == "list.json":
            return {"status": "000", "list": [{"report_nm": "감사보고서"}]}
        if endpoint == "empSttus.json":
            return {"status": "013"}     # 자료 없음 → 附은 사유만 붙는다
        return {"status": "000"}

    def load_public_org_registry(self, path: Any) -> dict[str, Any]:
        return {}

    def match_public_org(self, bizr_no: Any, registry: Any) -> None:
        return None

    def decide(
        self, corp_cls: str, has_audit: bool, bizr_no: Any, matcher: Any
    ) -> _FakeJudgment:
        return _FakeJudgment()

    # ── 5.5 공고 판별 (AI) · 8·9 생성 (AI) ────────────────
    def _ask(
        self, client: Any, prompt: str, schema: dict[str, Any], max_tokens: int = 700
    ) -> tuple[dict[str, Any], dict[str, int]]:
        # 실제 1판 `_ask`와 같이 provider client 경계를 지난다. 이 한 줄이 없으면
        # 파이프라인은 돌아도 새 요청별 비용 계량기는 아무것도 시험하지 못한다.
        response = client.messages.create(
            model=self.MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        usage = {
            "in": response.usage.input_tokens,
            "out": response.usage.output_tokens,
        }
        self.posting_ai_prompts.append(prompt)
        # canonical 사실 배치 — AI는 번호와 의미 섹션만 돌려준다.
        if "공식 근거 기반 회사분석 보고서의 사실 배치 작업" in prompt:
            self.generate_ai_calls += 1

            def item(
                section_id: str,
                sid: str,
                claim_type: str,
                subject_label: str,
                *,
                market_stage: str = "",
                market_observation: str = "",
                product_role: str = "",
                portfolio_stage: str = "",
                revenue_model_sid: str = "",
                response_to_sid: str = "",
                basis_sids: Optional[list[str]] = None,
                priority_signals: Optional[list[str]] = None,
                event_date: str = "",
                response_action: str = "",
                initial_signal: str = "",
                next_check_metric: str = "",
                plan_status: str = "",
                plan_timing: str = "",
                plan_condition: str = "",
                plan_expected_effect: str = "",
                plan_execution_signal: str = "",
                operation_role: str = "",
                value_chain_stage: str = "",
                relationship_type: str = "",
            ) -> dict[str, Any]:
                """실제 canonical 응답 스키마의 필드를 하나도 생략하지 않는다."""

                return {
                    "section_id": section_id,
                    "sid": sid,
                    "claim_type": claim_type,
                    "subject_label": subject_label,
                    "market_stage": market_stage,
                    "market_observation": market_observation,
                    "product_role": product_role,
                    "portfolio_stage": portfolio_stage,
                    "revenue_model_sid": revenue_model_sid,
                    "response_to_sid": response_to_sid,
                    "basis_sids": list(basis_sids or []),
                    "priority_signals": list(priority_signals or []),
                    "event_date": event_date,
                    "response_action": response_action,
                    "initial_signal": initial_signal,
                    "next_check_metric": next_check_metric,
                    "plan_status": plan_status,
                    "plan_timing": plan_timing,
                    "plan_condition": plan_condition,
                    "plan_expected_effect": plan_expected_effect,
                    "plan_execution_signal": plan_execution_signal,
                    "operation_role": operation_role,
                    "value_chain_stage": value_chain_stage,
                    "relationship_type": relationship_type,
                }

            return (
                {
                    "items": [
                        item(
                            "identity",
                            "1-1",
                            "identity_summary",
                            "가나다전자",
                        ),
                        item(
                            "business_model",
                            "2-1",
                            "revenue_model",
                            "SmartX 반도체 검사 장비",
                        ),
                        item(
                            "business_model",
                            "3-1",
                            "customer_market",
                            "국내외 반도체 제조사",
                            market_stage="핵심",
                            market_observation="핵심 고객 시장",
                        ),
                        item(
                            "portfolio",
                            "4-1",
                            "priority_product",
                            "SmartX 반도체 검사 장비",
                            product_role="반도체 제조사 판매 장비",
                            portfolio_stage="주력",
                            revenue_model_sid="2-1",
                            priority_signals=["출시·운영", "투자·증설"],
                        ),
                        item(
                            "past_changes",
                            "5-1",
                            "completed_execution",
                            "SmartX 생산 설비",
                            event_date="2025",
                        ),
                        item(
                            "past_changes",
                            "6-1",
                            "change_interpretation",
                            "SmartX 생산 체계 변경",
                            basis_sids=["5-1"],
                        ),
                        item(
                            "current_challenges",
                            "7-1",
                            "current_issue",
                            "SmartX 원가율 부담",
                            next_check_metric="원가율",
                        ),
                        item(
                            "current_challenges",
                            "8-1",
                            "current_response",
                            "생산 공정 재설계",
                            response_to_sid="7-1",
                            response_action="생산 공정 재설계를 추진 중",
                        ),
                        item(
                            "future_strategy",
                            "9-1",
                            "future_plan",
                            "SmartX 수출 유통망",
                            plan_status="announced",
                            plan_timing="2027년",
                            plan_execution_signal="SmartX 수출 유통망을 확대",
                        ),
                        item(
                            "operations_partners",
                            "10-1",
                            "operating_core",
                            "TraceOne 데이터 시스템",
                            operation_role="TraceOne 데이터 시스템을 생산 운영에 사용",
                            value_chain_stage="production",
                            relationship_type="internal_operation",
                        ),
                        item(
                            "operations_partners",
                            "11-1",
                            "partner_role",
                            "DeltaParts",
                            operation_role="DeltaParts가 부품을 공급한다",
                            value_chain_stage="procurement",
                            relationship_type="supplier",
                        ),
                        item(
                            "culture",
                            "12-1",
                            "official_value",
                            "존중과 책임",
                        ),
                    ]
                },
                usage,
            )
        # 작가 — 원문 하나마다 같은 뜻의 한 문장과 그 근거 번호만 돌려준다.
        if "공식 근거 기반 기업분석 보고서" in prompt and "■ 칸과 근거" in prompt:
            self.generate_ai_calls += 1
            texts = {
                "identity": [
                    (
                        "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다.",
                        "identity-1",
                    ),
                ],
                "business_model": [
                    (
                        "가나다전자는 SmartX 반도체 검사 장비를 국내외 반도체 제조사에 판매해 장비 매출을 얻는다.",
                        "business_model-1",
                    ),
                    (
                        "가나다전자의 SmartX 반도체 검사 장비는 국내외 반도체 제조사를 핵심 고객 시장으로 삼아 판매된다.",
                        "business_model-2",
                    ),
                ],
                "portfolio": [
                    (
                        "가나다전자는 SmartX 반도체 검사 장비를 주력 제품으로 출시해 판매하고 생산 설비에 투자했다.",
                        "portfolio-1",
                    ),
                ],
                "past_changes": [
                    (
                        "가나다전자는 2025년 SmartX 생산 설비를 도입했다.",
                        "past_changes-1",
                    ),
                    (
                        "가나다전자의 SmartX 생산 체계 변경에는 2025년 설비 도입 실행이 포함됐다.",
                        "past_changes-2",
                    ),
                ],
                "current_challenges": [
                    (
                        "가나다전자는 2026년 SmartX 원가율 부담을 현재 미해결 과제로 관리한다.",
                        "current_challenges-1",
                    ),
                    (
                        "가나다전자는 2026년 SmartX 원가 부담에 대응해 생산 공정 재설계를 추진 중이다.",
                        "current_challenges-2",
                    ),
                ],
                "future_strategy": [
                    (
                        "가나다전자는 2027년 SmartX 수출 유통망을 확대할 계획이다.",
                        "future_strategy-1",
                    ),
                ],
                "operations_partners": [
                    (
                        "가나다전자는 SmartX 검사 설비와 TraceOne 데이터 시스템을 생산 운영에 사용하며 부품 공급 계약을 관리한다.",
                        "operations_partners-1",
                    ),
                    (
                        "가나다전자는 DeltaParts와 SmartX 핵심 부품 공급 계약을 체결해 DeltaParts가 부품을 공급한다.",
                        "operations_partners-2",
                    ),
                ],
                "culture": [
                    (
                        "가나다전자는 존중과 책임을 핵심 가치로 두고 팀 간 협업을 일하는 방식으로 제시한다.",
                        "culture-1",
                    ),
                ],
            }
            return {
                "칸": [
                    {
                        "칸번호": cell,
                        "문장들": [
                            {"글": text, "근거": sid}
                            for text, sid in sentences
                        ],
                    }
                    for cell, sentences in texts.items()
                ]
            }, usage
        # 핵심 요약은 본문 결론만 숫자 없이 돌려준다.
        if "첫 장 핵심 요약" in prompt:
            self.generate_ai_calls += 1
            return {
                "items": [
                    {
                        "section_id": "identity",
                        "text": "반도체 검사 장비 전문기업으로 경쟁 관계가 확인된다",
                    },
                    {
                        "section_id": "business_model",
                        "text": "반도체 검사 장비 판매와 매출이 사업 구조의 핵심이다",
                    },
                    {
                        "section_id": "culture",
                        "text": "존중과 책임을 핵심 가치로 두고 협업하는 문화다",
                    },
                ]
            }, usage
        # 작가·요약의 독립 근거 대조는 모든 번호를 명시적으로 판정한다.
        if "이 문장의 내용이 근거 원문 안에 있는가" in prompt:
            self.generate_ai_calls += 1
            numbers = [int(value) for value in re.findall(r"\[(\d+)\]", prompt)]
            return {
                "판정": [
                    {"번호": number, "근거에있다": True}
                    for number in dict.fromkeys(numbers)
                ]
            }, usage
        # v2 호환 시험용 선택 경로.
        if PROMPT_PICK in prompt:
            self.generate_ai_calls += 1
            return (
                {"items": [{"block": "1", "sid": "1-1"}, {"block": "5", "sid": "R-1"}]},
                usage,
            )
        self.posting_ai_calls += 1
        if "채용공고인가" in prompt:
            return {"is_job_posting": True}, usage
        # 요구역량 추출 — 프롬프트 끝에 붙은 공고 본문을 줄 단위로 돌려준다.
        body = prompt.split("---\n", 1)[-1]
        return {
            "requirements": [ln.strip() for ln in body.splitlines() if ln.strip()]
        }, usage

    def layer2(self, posting: str) -> SimpleNamespace:
        return SimpleNamespace(passed=True)

    def erase(self, text: str, run_id: str = "") -> SimpleNamespace:
        """3층 개인정보 지우개 흉내 — **실제로 지운다.**

        ★ 예전에는 원문을 그대로 돌려주는 껍데기였다. 그때
          `test_공고_원문은_저장소에_들어가지_않는다`가 통과한 것은 지우개 덕이
          아니라, 가짜 생성기가 배치 안 된 요구역량을 **조용히 버렸기** 때문이다.
          정본 「조용한 누락 금지」(05_생성/1_흐름/01_문장스팬선택.md:123-132)를
          지키자 그 구멍이 드러났다 — **시험이 엉뚱한 이유로 통과하고 있었다.**
          진짜 경로는 요구역량을 뽑기 «전에» 지우개를 돌린다 (`real.py` 5.5).
        """
        erased, hits = _PHONE_PATTERN.subn(_MASK, text)
        return SimpleNamespace(text=erased, counts={"연락처": hits} if hits else {})

    # ── 6 수집 ───────────────────────────────────────────
    def latest_report_rcept(
        self, corp_code: str, corp_type: str, counter: Any
    ) -> dict[str, Any]:
        year = self.filing_year
        name = "사업보고서" if year is None else f"사업보고서 ({year}.12)"
        return {
            "report_nm": name,
            "rcept_no": "20260315000123",
            "rcept_dt": "20260315",
            "reprt_code": "11011",
        }

    def download_document(self, rcept_no: str, raw_dir: Any, counter: Any) -> str:
        return "(가짜 경로)"

    def read_filing_text(self, path: str) -> str:
        return (
            "국내외 반도체 제조 고객을 대상으로 SmartX 반도체 검사 장비 제품을 "
            "반도체 검사 장비 시장에 공급한다. 연결재무제표의 매출액과 영업이익을 공시한다."
        )

    def fetch_financials(
        self, corp_code: str, counter: Any
    ) -> tuple[dict[str, Any], list[int]]:
        comparator = corp_code == "00999999"

        def row(
            account_id: str,
            account_nm: str,
            amounts: tuple[int, int, int],
        ) -> dict[str, str]:
            return {
                "account_id": account_id,
                "account_nm": account_nm,
                "sj_div": "IS",
                "fs_div": "CFS",
                "currency": "KRW",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": str(amounts[0]),
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": str(amounts[1]),
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": str(amounts[2]),
            }

        amounts = (
            {
                "revenue": (1_000_000_000, 900_000_000, 800_000_000),
                "operating": (100_000_000, 90_000_000, 80_000_000),
                "profit": (70_000_000, 60_000_000, 50_000_000),
            }
            if comparator
            else {
                "revenue": (2_000_000_000, 1_800_000_000, 1_600_000_000),
                "operating": (300_000_000, 250_000_000, 200_000_000),
                "profit": (240_000_000, 190_000_000, 150_000_000),
            }
        )
        return {
            "status": "000",
            "reprt_code": "11011",
            "list": [
                row("ifrs-full_Revenue", "매출액", amounts["revenue"]),
                row(
                    "dart_OperatingIncomeLoss",
                    "영업이익",
                    amounts["operating"],
                ),
                row("ifrs-full_ProfitLoss", "당기순이익", amounts["profit"]),
            ]
        }, list(self.fiscal_years)

    def make_fragments(
        self, filing_text: str, financials: Optional[dict[str, Any]]
    ) -> dict[int, dict[str, str]]:
        return {
            1: {
                "종류": "사업내용",
                "원문": "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다.",
                "근거원문": [
                    "사업보고서 회사 개요: 홈페이지 https://www.ganada.example"
                ],
            },
            2: {
                "종류": "사업내용",
                "원문": "가나다전자는 SmartX 반도체 검사 장비를 국내외 반도체 제조사에 판매해 장비 매출을 얻는다.",
            },
            3: {
                "종류": "사업내용",
                "원문": "가나다전자의 SmartX 반도체 검사 장비는 국내외 반도체 제조사를 핵심 고객 시장으로 삼아 판매된다.",
            },
            4: {
                "종류": "사업내용",
                "원문": "가나다전자는 SmartX 반도체 검사 장비를 주력 제품으로 출시해 판매하고 생산 설비에 투자했다.",
            },
            5: {
                "종류": "MD&A",
                "원문": "가나다전자는 2025년 SmartX 생산 설비를 도입했다.",
            },
            6: {
                "종류": "MD&A",
                "원문": "가나다전자의 SmartX 생산 체계 변경에는 2025년 설비 도입 실행이 포함됐다.",
            },
            7: {
                "종류": "MD&A",
                "원문": "가나다전자는 2026년 SmartX 원가율 부담을 현재 미해결 과제로 관리한다.",
            },
            8: {
                "종류": "MD&A",
                "원문": "가나다전자는 2026년 SmartX 원가 부담에 대응해 생산 공정 재설계를 추진 중이다.",
                "사실상태": "현재 실제 대응",
            },
            9: {
                "종류": "신규사업전망",
                "원문": "가나다전자는 2027년 SmartX 수출 유통망을 확대할 계획이다.",
                "사실상태": "공식 계획 미실행",
            },
            10: {
                "종류": "사업내용",
                "원문": "가나다전자는 SmartX 검사 설비와 TraceOne 데이터 시스템을 생산 운영에 사용하며 부품 공급 계약을 관리한다.",
            },
            11: {
                "종류": "사업내용",
                "원문": "가나다전자는 DeltaParts와 SmartX 핵심 부품 공급 계약을 체결해 DeltaParts가 부품을 공급한다.",
            },
            12: {
                "종류": "홈페이지",
                "원문": "가나다전자는 존중과 책임을 핵심 가치로 두고 팀 간 협업을 일하는 방식으로 제시한다.",
                "출처": "https://www.ganada.example/culture",
                "문서명": "가나다전자 인재상과 일하는 방식",
                "원문위치": "/culture",
                "발행처": "가나다전자",
                "도메인근거SourceID": "source-1",
                "도메인근거원문": (
                    "사업보고서 회사 개요: 홈페이지 https://www.ganada.example"
                ),
            },
            13: {
                "종류": "재무",
                "원문": (
                    "주요계정(DART API): 매출액·영업이익·당기순이익의 "
                    "2025년, 2024년, 2023년 연결 원값"
                ),
            },
        }

    def search_news(
        self, query: str, display: int = 10, sort: str = "date"
    ) -> list[SimpleNamespace]:
        """네이버 뉴스 검색 흉내 (P-108).

        ★ 1판 `collect_news`를 대신한다 — 이제 `real.py`가 «검색»과 «고르기»를
          나눠서 한다. 고르기는 AI가 하므로 여기서는 «검색 결과»만 준다.
        ⚠️ 이름·인자가 진짜(`core/naver_client.search_news`)와 달라지면
          실행 시점에 터진다. 여기서 맞춰 둔다.
        """
        if self.news_fails:
            raise RuntimeError("타임아웃")
        return []

    def collect_news(
        self, company: str, profile: dict[str, Any], homonym: int, steps: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """1판 방식(제목 일치). ★ `real.py`는 더 이상 이걸 부르지 않는다 (P-108).

        1판에 «아직 남아 있는» 함수라 모양만 유지한다.
        """
        steps.append({"step": "6_수집_뉴스", "채택": 0, "검색결과": 0})
        return []

    # ── 7 게이트 · 8·9 생성 · 10 검증 ────────────────────
    def establishment(self, rough: dict[str, bool]) -> tuple[bool, list[str]]:
        return True, []

    # ★ 8·9 생성은 이제 `spanselect.select_spans`가 맡는다 (P-43). `real.py`는
    #   `generate_and_check`를 더 이상 부르지 않는다. 아래 셋은 그 대체 경로가
    #   1판에서 «빌려 쓰는» 부품들이다 — 이름이 틀리면 실행 시점에 터진다.
    BLOCK_ORDER = ("1", "2", "3", "4-1", "4-2", "4-3", "5", "6", "7", "8", "9")
    GEN_MAX_TOKENS = 3000
    DraftItem = _FakeDraftItem

    def split_sentences(self, text: str) -> list[str]:
        """마침표로 자르는 간단판. 진짜 규칙(절단면 꼬리 제거 등)은 1판 것을 쓴다."""
        return [s.strip() for s in text.split(".") if s.strip()]

    def check_draft(
        self,
        items: list[_FakeDraftItem],
        originals: dict[int, str],
        requirements: list[str],
    ) -> SimpleNamespace:
        """W1~W4 원문 대조 흉내 — 여기서는 전부 통과시킨다.

        ★ 진짜 대조 규칙은 `spanselect/tests/`가 **1판 코드를 직접 불러** 검증한다.
          이 파일이 재는 것은 「캐시가 먹었나」이지 대조 정확도가 아니다.
        """
        return SimpleNamespace(kept=list(items), deleted=[])

    def substance_check(
        self, client: Any, kept: list[_FakeDraftItem], steps: list[dict[str, Any]]
    ) -> dict[str, bool]:
        client.messages.create(
            model=self.MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": "가짜 검증"}],
        )
        self.generate_ai_calls += 1
        return {cell: True for cell in self.CELL_SOURCES}

    def cell_pattern_ok(self, kept: list[_FakeDraftItem]) -> dict[str, bool]:
        return {cell: True for cell in self.CELL_SOURCES}


# ══════════════════════════════════════════════════════════
# 준비물
# ══════════════════════════════════════════════════════════


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """진짜 엔진 대신 가짜를 끼운다 — 이 시험에서 돈이 나갈 길이 없다."""
    fake = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            (CORP_ID, "가나다전자", "", "000001", "20260819"),
            ("00999999", "베타전자", "", "999999", "20260819"),
        ),
    )
    return fake


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    """직접 RealPipeline 시험도 웹 worker와 같은 유료 문맥에서 실행한다."""
    with provider_budget.activate(100_000.0):
        yield


def _card() -> CompanyCard:
    return CompanyCard(
        legal_name="가나다전자",
        typed_name="가나다전자",
        address="서울특별시 강남구 테헤란로 1",
        ceo="홍길동",
        founded="20000101",
        ref=CORP_ID,
    )


def _run(posting: str = POSTING, job: str = JOB, card: Optional[CompanyCard] = None):
    user_input = UserInput(company="가나다전자", job=job, region="서울 강남구", posting_text=posting)
    return real.RealPipeline().run(user_input, card or _card())


def test_삼성전자_저장원문은_가짜AI로_생성이후까지_무과금재현한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P01에서 저장된 DART 원문을 읽되 provider 대신 완전한 가짜 응답을 쓴다.

    원문은 파일럿 실행의 로컬 증거라 저장소에 추가하지 않는다. 증거가 없는 일반
    개발 환경에서는 건너뛰고, 있으면 해시를 먼저 맞춘 뒤 실제 파서·보정기·원문
    대조기를 거쳐 writer와 실제 경쟁사 비교 게이트까지 도달하는지 검사한다.
    """

    expected_raw_sha256 = (
        "107f3645e46dcd5af1ba7613d5480304c1ceaa98a89ac3a70fd113d23365d163"
    )
    raw_root = Path(real.paths.APP_ROOT) / ".local_evaluation_runs"
    raw_path: Path | None = None
    raw_candidates = raw_root.glob(
        "*/analysis_engine/pilot/raw_filings/20260310002820.xml"
    )
    for candidate in sorted(raw_candidates):
        try:
            with candidate.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError:
            continue
        if digest == expected_raw_sha256:
            raw_path = candidate
            break
    if raw_path is None:
        pytest.skip("검증된 삼성전자 P01 DART 원문이 로컬에 없습니다")

    actual = real._engine()

    class SamsungRawFakeEngine(FakeEngine):
        """수집·대조는 실제 로컬 코드, AI와 외부 I/O는 가짜인 혼합 엔진."""

        RAW_DIR = raw_path.parent
        CELL_SOURCES = actual.CELL_SOURCES
        BLOCK_ORDER = actual.BLOCK_ORDER
        GEN_MAX_TOKENS = actual.GEN_MAX_TOKENS
        DraftItem = actual.DraftItem
        SECTION_HEADS = actual.SECTION_HEADS
        FRAG_CHARS = actual.FRAG_CHARS

        def get_json(
            self, endpoint: str, params: dict[str, Any], counter: Any
        ) -> dict[str, Any]:
            counter.count += 1
            if endpoint == "company.json":
                return {
                    "status": "000",
                    "corp_name": "삼성전자",
                    "corp_eng_name": "SAMSUNG ELECTRONICS CO., LTD.",
                    "adres": "경기도 수원시 영통구 삼성로 129",
                    "ceo_nm": "한종희",
                    "est_dt": "19690113",
                    "hm_url": "",
                    "corp_cls": "Y",
                    "stock_code": "005930",
                    "bizr_no": "1248100998",
                }
            if endpoint == "list.json":
                return {"status": "000", "list": [{"report_nm": "감사보고서"}]}
            if endpoint == "empSttus.json":
                return {"status": "013"}
            return {"status": "000"}

        def latest_report_rcept(
            self, corp_code: str, corp_type: str, counter: Any
        ) -> dict[str, Any]:
            assert corp_code == CORP_ID
            return {
                "report_nm": "사업보고서 (2025.12)",
                "rcept_no": "20260310002820",
                "rcept_dt": "20260310",
                "reprt_code": "11011",
            }

        def download_document(self, rcept_no: str, raw_dir: Any, counter: Any) -> str:
            assert rcept_no == "20260310002820"
            return str(raw_path)

        def read_filing_text(self, path: str) -> str:
            return actual.read_filing_text(Path(path))

        def make_fragments(
            self, filing_text: str, financials: Optional[dict[str, Any]]
        ) -> dict[int, dict[str, str]]:
            return actual.make_fragments(filing_text, financials)

        def split_sentences(self, text: str) -> list[str]:
            return actual.split_sentences(text)

        def check_draft(
            self,
            items: list[Any],
            fragments: dict[int, str],
            requirement_lines: list[str],
        ) -> Any:
            return actual.check_draft(items, fragments, requirement_lines)

        def _ask(
            self, client: Any, prompt: str, schema: dict[str, Any], max_tokens: int = 700
        ) -> tuple[dict[str, Any], dict[str, int]]:
            if "공식 근거 기반 회사분석 보고서의 사실 배치 작업" not in prompt:
                return super()._ask(client, prompt, schema, max_tokens=max_tokens)

            response = client.messages.create(
                model=self.MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
            self.posting_ai_prompts.append(prompt)
            self.generate_ai_calls += 1

            def item(
                section_id: str,
                sid: str,
                claim_type: str,
                subject_label: str = "",
                *,
                event_date: str = "",
                response_action: str = "",
                initial_signal: str = "",
                next_check_metric: str = "",
                plan_status: str = "",
                plan_timing: str = "",
                plan_condition: str = "",
                plan_expected_effect: str = "",
                plan_execution_signal: str = "",
                operation_role: str = "",
                value_chain_stage: str = "",
                relationship_type: str = "",
            ) -> dict[str, Any]:
                return {
                    "section_id": section_id,
                    "sid": sid,
                    "claim_type": claim_type,
                    "subject_label": subject_label,
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "",
                    "portfolio_stage": "",
                    "revenue_model_sid": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                    "event_date": event_date,
                    "response_action": response_action,
                    "initial_signal": initial_signal,
                    "next_check_metric": next_check_metric,
                    "plan_status": plan_status,
                    "plan_timing": plan_timing,
                    "plan_condition": plan_condition,
                    "plan_expected_effect": plan_expected_effect,
                    "plan_execution_signal": plan_execution_signal,
                    "operation_role": operation_role,
                    "value_chain_stage": value_chain_stage,
                    "relationship_type": relationship_type,
                }

            return (
                {
                    "items": [
                        item("business_model", "2-1", "revenue_model", "재화의 판매"),
                        item(
                            "past_changes",
                            "4-2",
                            "completed_execution",
                            event_date="2025",
                        ),
                        item(
                            "current_challenges",
                            "11-5",
                            "current_issue",
                            "환율변동위험",
                            next_check_metric="환율",
                        ),
                        item(
                            "operations_partners",
                            "8-5",
                            "partner_role",
                            "Qualcomm",
                        ),
                    ]
                },
                {
                    "in": response.usage.input_tokens,
                    "out": response.usage.output_tokens,
                },
            )

    fake = SamsungRawFakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            (
                CORP_ID,
                "삼성전자",
                "SAMSUNG ELECTRONICS CO., LTD.",
                "005930",
                "20260821",
            ),
        ),
    )

    calls = {"writer": 0, "comparison": 0, "finalize": 0}
    original_writer = real.write_and_verify_sections
    original_comparison = real._attach_competitive_position
    original_finalize = real.finalize_report

    def counted_writer(*args: Any, **kwargs: Any):
        calls["writer"] += 1
        return original_writer(*args, **kwargs)

    def counted_finalize(*args: Any, **kwargs: Any):
        calls["finalize"] += 1
        return original_finalize(*args, **kwargs)

    def counted_comparison(*args: Any, **kwargs: Any):
        calls["comparison"] += 1
        return original_comparison(*args, **kwargs)

    monkeypatch.setattr(real, "write_and_verify_sections", counted_writer)
    monkeypatch.setattr(real, "_attach_competitive_position", counted_comparison)
    monkeypatch.setattr(real, "finalize_report", counted_finalize)

    progress: list[str] = []
    result = real.RealPipeline().run(
        UserInput(company="삼성전자", job="", region="", posting_text=""),
        CompanyCard(
            legal_name="삼성전자",
            typed_name="삼성전자",
            address="경기도 수원시 영통구 삼성로 129",
            ceo="한종희",
            founded="19690113",
            ref=CORP_ID,
        ),
        progress.append,
    )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.outcome is not Outcome.FAILED
    assert result.report is None
    assert result.billing_uncertain is False
    # 이 P01 원문은 법인명을 붙인 직접 경쟁 근거가 없어 실제 9장 게이트에서
    # 멈추는 것이 정답이다. 경쟁사를 추측해 finalize까지 우회하지 않는다.
    assert "양사 공식 원문" in result.message
    assert calls == {"writer": 1, "comparison": 1, "finalize": 0}
    assert progress[:6] == [
        "identify", "judge", "collect", "gate", "generate", "verify"
    ]
    assert fake.client.messages.calls == 5
    assert len(result.ai_cost_events) == 5
    assert all(event.failed_call is False for event in result.ai_cost_events)


# ══════════════════════════════════════════════════════════
# 적중 — 같은 회사 · 같은 공고
# ══════════════════════════════════════════════════════════


def test_본조사_DART_회사정보오류는_거부가_아니라_AI전_기술실패다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.get_json

    def dart_error(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        if endpoint == "company.json":
            return {"status": "999", "message": "DART 한도 오류"}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", dart_error)

    result = _run()

    assert result.outcome is Outcome.FAILED
    assert result.outcome is not Outcome.REJECT_NO_DISCLOSURE
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


def test_본조사_DART_공시목록오류는_감사보고서없음으로_거부하지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.get_json

    def dart_error(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        if endpoint == "list.json":
            return {"status": "999", "message": "DART 인증 오류"}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", dart_error)

    result = _run()

    assert result.outcome is Outcome.FAILED
    assert result.outcome is not Outcome.REJECT_NO_DISCLOSURE
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


def test_본조사_DART_013은_비상장_공시없음으로_명확히거부하고_추가호출하지않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.get_json
    endpoints: list[str] = []

    def no_disclosure(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        endpoints.append(endpoint)
        if endpoint == "company.json":
            profile = dict(original(endpoint, params, counter))
            profile["corp_cls"] = "E"
            return profile
        if endpoint == "list.json":
            counter.count += 1
            return {"status": "013", "message": "조회된 데이타가 없습니다."}
        raise AssertionError(f"013 뒤 추가 provider 호출: {endpoint}")

    monkeypatch.setattr(engine, "get_json", no_disclosure)
    monkeypatch.setattr(
        engine,
        "decide",
        lambda *_args, **_kwargs: SimpleNamespace(status="거부B", corp_type=None),
    )

    result = _run()

    assert result.outcome is Outcome.REJECT_NO_DISCLOSURE
    assert "감사보고서" in result.message
    assert result.sources[0].state == "none"
    assert endpoints == ["company.json", "list.json"]
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "000"},
        {"status": "000", "list": {}},
        {"status": "013", "list": [{"report_nm": "모순"}]},
        {"status": "013 "},
        {"status": 13},
        {"status": "000 ", "list": []},
        {"status": 0, "list": []},
    ],
)
def test_본조사_DART_공시목록_모순응답은_공시없음이아니라_기술실패다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    original = engine.get_json

    def malformed(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        if endpoint == "list.json":
            counter.count += 1
            return payload
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", malformed)

    result = _run()

    assert result.outcome is Outcome.FAILED
    assert result.outcome is not Outcome.REJECT_NO_DISCLOSURE
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


def test_본조사_DART_013이어도_상장사는_공시없음으로_오거부하지않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.get_json

    def listed_no_audit(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        if endpoint == "list.json":
            counter.count += 1
            return {"status": "013"}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", listed_no_audit)

    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.outcome is not Outcome.REJECT_NO_DISCLOSURE


def test_최신공시목록도_정확한_013만_자료없음으로_인정한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_engine = real._engine()
    payload: dict[str, Any] = {"status": "013"}
    monkeypatch.setattr(
        actual_engine,
        "get_json",
        lambda *_args, **_kwargs: payload,
    )
    counter = SimpleNamespace()

    assert actual_engine.latest_report_rcept("00126380", "상장사", counter) is None

    for malformed in (
        {"status": "013 "},
        {"status": 13},
        {"status": "000 ", "list": []},
        {"status": 0, "list": []},
        {"status": "013", "list": [{"report_nm": "모순"}]},
    ):
        payload = malformed
        with pytest.raises(RuntimeError):
            actual_engine.latest_report_rcept("00126380", "상장사", counter)


def test_같은_공고를_다시_조사하면_생성AI를_한_번도_안_부른다(engine: FakeEngine) -> None:
    """★ 이 시험이 캐시의 존재 이유다 — 두 번째 요청에서 «비싼 쪽»이 0이어야 한다."""
    first = _run()
    assert first.outcome is Outcome.REPORT
    calls_after_first = engine.generate_ai_calls
    assert calls_after_first > 0, "첫 조사에서는 생성 AI가 돌아야 한다(시험이 헛돈 것)"

    second = _run()

    assert second.outcome is Outcome.REPORT
    assert engine.generate_ai_calls == calls_after_first, (
        "두 번째 조사에서 생성·검증 AI가 또 나갔습니다 — 캐시가 안 먹었습니다."
    )


def test_가짜엔진도_9장_양사공식원문과_동일비교조건을_정직하게_채운다(
    engine: FakeEngine,
) -> None:
    """캐시 fixture도 production 비교 게이트를 우회하지 않고 같은 계약을 지킨다."""

    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    report = result.report
    comparison_facts = [
        fact
        for fact in report.fact_records
        if fact.section_owner == "competitive_position"
    ]
    assert {fact.comparison_metric for fact in comparison_facts} == {
        "영업이익률",
        "매출 규모",
    }
    assert {
        fact.comparison_metric: fact.comparison_judgment
        for fact in comparison_facts
    } == {
        "영업이익률": "competitive_advantage",
        "매출 규모": "operating_characteristic",
    }

    sources = {source.source_id: source for source in report.citations}
    expected_condition_keys = {
        "customer",
        "product",
        "market",
        "self_period",
        "comparator_period",
        "self_definition",
        "comparator_definition",
        "self_accounting_scope",
        "comparator_accounting_scope",
    }
    for fact in comparison_facts:
        conditions = fact.comparison_conditions
        assert fact.comparison_target == "베타전자"
        assert fact.comparison_period == "2025-01-01~2025-12-31"
        assert fact.comparison_scope == "연결재무제표(CFS)"
        assert set(conditions) == expected_condition_keys
        assert all(str(value).strip() for value in conditions.values())
        assert conditions["self_period"] == conditions["comparator_period"]
        assert conditions["self_period"] == fact.comparison_period
        assert conditions["self_definition"] == conditions["comparator_definition"]
        assert conditions["self_definition"] == fact.comparison_definition
        assert (
            conditions["self_accounting_scope"]
            == conditions["comparator_accounting_scope"]
            == fact.comparison_scope
        )
        assert fact.source_id != fact.comparator_source_id
        assert sources[fact.source_id].publisher == "가나다전자"
        assert sources[fact.comparator_source_id].publisher == "베타전자"
        assert '"official_text"' in fact.state_evidence
        assert '"financials"' in fact.state_evidence
        assert '"official_text"' in fact.comparator_state_evidence
        assert '"financials"' in fact.comparator_state_evidence


def test_캐시로_돌려준_보고서가_처음_만든_것과_같다(engine: FakeEngine) -> None:
    first = _run()
    second = _run()
    assert second.report == first.report


def test_캐시_적중은_할당량을_안_깎는다(engine: FakeEngine) -> None:
    """정본 00_공통/2_규칙/04_할당량.md — 「캐시 반환(1층 히트) → 0 · 무제한」."""
    first = _run()
    second = _run()
    assert first.charged is True
    assert second.charged is False


def test_캐시_적중이면_저장된_결과라고_밝힌다(engine: FakeEngine) -> None:
    """사용자가 「방금 새로 조사한 것」으로 오해하면 안 된다 (P-63 교훈)."""
    first = _run()
    second = _run()

    assert first.message == "", "새로 만든 요청에는 캐시 안내가 붙으면 안 된다"
    assert "이미 조사해 둔" in second.message
    assert first.report is not None
    assert first.report.generated_at in second.message, "언제 조사한 것인지 밝혀야 한다"


def test_캐시_적중은_공고_AI를_추가로_부르지_않는다(engine: FakeEngine) -> None:
    """회사분석 전용 경로는 직무·공고를 읽지 않고 회사 캐시만 본다."""
    _run()
    calls_after_first = engine.posting_ai_calls
    _run()
    assert engine.posting_ai_calls == calls_after_first


# ══════════════════════════════════════════════════════════
# 미적중 — 공고가 다르다 · 회사가 다르다 · 직무가 다르다
# ══════════════════════════════════════════════════════════


def test_공고_입력값이_달라도_같은_회사분석_캐시를_쓴다(engine: FakeEngine) -> None:
    """신규 제품에서는 공고가 보고서·캐시 정체성에 관여하지 않는다."""
    _run(posting=POSTING)
    calls_after_first = engine.generate_ai_calls

    other = _run(posting=OTHER_POSTING)

    assert other.outcome is Outcome.REPORT
    assert engine.generate_ai_calls == calls_after_first
    assert other.charged is False
    assert "이미 조사해 둔" in other.message


def test_회사가_다르면_캐시가_안_섞인다(engine: FakeEngine) -> None:
    _run()
    calls_after_first = engine.generate_ai_calls

    other_card = CompanyCard(
        legal_name="가나다전자",   # 이름은 같지만
        typed_name="가나다전자",
        address="부산광역시 해운대구",
        ceo="김철수",
        founded="20100101",
        ref="00999999",             # 고유번호가 다른 «다른 법인»
    )
    _run(card=other_card)

    assert engine.generate_ai_calls > calls_after_first


def test_직무_입력값이_달라도_같은_회사분석_캐시를_쓴다(engine: FakeEngine) -> None:
    _run(job="백엔드 개발자")
    calls_after_first = engine.generate_ai_calls
    _run(job="영업 관리")
    assert engine.generate_ai_calls == calls_after_first


def test_직무_표기가_공백_대소문자만_달라도_같은_것으로_본다(engine: FakeEngine) -> None:
    """정규화 규칙(`cache.normalize_job`)이 파이프라인까지 이어지는지 본다."""
    _run(job="Backend Engineer")
    calls_after_first = engine.generate_ai_calls
    _run(job="  backend   engineer ")
    assert engine.generate_ai_calls == calls_after_first


def test_공고_문장_순서만_바뀐_것은_같은_공고로_본다(engine: FakeEngine) -> None:
    """지문은 정렬 뒤 해시다 — AI가 문장을 뽑는 순서는 매번 같다는 보장이 없다."""
    _run(posting="3년 이상 경력\n파이썬 실무 경험")
    calls_after_first = engine.generate_ai_calls
    _run(posting="파이썬 실무 경험\n3년 이상 경력")
    assert engine.generate_ai_calls == calls_after_first


# ══════════════════════════════════════════════════════════
# 만료 — O9 신선도 (정본 §2)
# ══════════════════════════════════════════════════════════


def test_사업연도가_바뀌면_저장된_보고서를_안_쓴다(engine: FakeEngine) -> None:
    """★ 없으면 「작년 보고서」가 영원히 나간다 (정본 §2가 막으려던 구멍)."""
    _run()
    calls_after_first = engine.generate_ai_calls

    engine.fiscal_years = [2026, 2025]   # 새 사업연도 자료가 올라왔다
    again = _run()

    assert engine.generate_ai_calls > calls_after_first, "만료된 보고서를 재사용했습니다"
    assert again.charged is True


def test_재무API가_빈손이어도_공시_이름으로_캐시가_적중한다(engine: FakeEngine) -> None:
    """★ 비상장 외감 회사의 실제 모습이다.

    1판 실측 28건 중 13건(전부 비상장 외감)은 재무 API가 사업연도를 못 줬다.
    재무 API만 보면 그 회사들은 캐시가 **영영 적중하지 않는다.**
    """
    engine.fiscal_years = []             # 재무 API 빈손
    engine.filing_year = 2025            # 감사보고서 (2025.12)는 있다
    _run()
    calls_after_first = engine.generate_ai_calls

    _run()

    assert engine.generate_ai_calls == calls_after_first


def test_공시_사업연도가_바뀌면_만료된다(engine: FakeEngine) -> None:
    """재무 API가 빈손인 회사도 새 감사보고서가 올라오면 다시 만들어야 한다."""
    engine.fiscal_years = []
    engine.filing_year = 2025
    _run()
    calls_after_first = engine.generate_ai_calls

    engine.filing_year = 2026            # 새 결산 보고서가 올라왔다
    _run()

    assert engine.generate_ai_calls > calls_after_first


def test_사업연도를_아예_모르면_신선하다고_우기지_않는다(engine: FakeEngine) -> None:
    """재무 API도 공시 이름도 연도를 안 주면 «모르는 상태»다 — 다시 만든다(보수적)."""
    _run()
    calls_after_first = engine.generate_ai_calls

    engine.fiscal_years = []
    engine.filing_year = None            # 이름에 결산 기간이 안 적힌 공시
    _run()

    assert engine.generate_ai_calls > calls_after_first


def test_최신_사업연도는_추측하지_않고_전자공시가_답한_값을_쓴다() -> None:
    """1~3월에는 작년 자료가 아직 없는 것이 정상이다 — `올해-1`로 추측하면 안 된다."""
    filing = {"report_nm": "감사보고서 (2025.12)"}
    assert real._current_fiscal_year([2024, 2023], None) == 2024
    assert real._current_fiscal_year([], filing) == 2025
    # 한쪽이 옛 연도에 멈춰 있어도 다른 쪽이 새 자료를 알아채면 만료되게 «더 최신»을 쓴다.
    assert real._current_fiscal_year([2023], filing) == 2025
    assert real._current_fiscal_year([], {"report_nm": "감사보고서"}) is None
    assert real._current_fiscal_year([], None) is None


def test_3개년표만_있고_필수_정체성제품근거가_없으면_출고하지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """숫자표가 있어도 필수 1~3장을 일반론으로 채워 보고서를 만들지 않는다."""

    def row(account_id: str, account_nm: str, values: tuple[str, str, str]) -> dict[str, str]:
        return {
            "fs_div": "CFS",
            "account_id": account_id,
            "account_nm": account_nm,
            "bsns_year": "2025",
            "thstrm_dt": "2025.01.01 ~ 2025.12.31",
            "thstrm_amount": values[0],
            "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
            "frmtrm_amount": values[1],
            "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
            "bfefrmtrm_amount": values[2],
        }

    financials = {
        "list": [
            row("ifrs-full_Revenue", "매출액", ("821850000000", "601790000000", "566500000000")),
            row("dart_OperatingIncomeLoss", "영업이익", ("155250000000", "128260000000", "169440000000")),
        ]
    }
    monkeypatch.setattr(engine, "fetch_financials", lambda _corp, _counter: (financials, [2025]))
    monkeypatch.setattr(
        engine,
        "make_fragments",
        lambda _text, _financials: {
            1: {"종류": "사업", "원문": "회사는 반도체 검사 장비를 만들어 제조사에 판다."},
            7: {"종류": "사업", "원문": "이 조각은 본문에서 사용하지 않는다."},
            9: {
                "종류": "재무",
                "원문": "주요계정(DART API): 매출액 821850000000(2025.12.31)",
            },
        },
    )

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.charged is False


def test_공시_사업연도는_접수일이_아니라_결산연도를_읽는다() -> None:
    """접수일(2026-03 제출)로 읽으면 「사업연도가 바뀌었다」를 못 가른다."""
    filing = {"report_nm": "감사보고서 (2025.12)", "rcept_dt": "20260315"}
    assert real._filing_fiscal_year(filing) == 2025


def test_3개년표와_최신_반기공시의_기간을_서로_덮어쓰지_않는다() -> None:
    table = ReportTable(
        caption="3개년 연결 실적",
        headers=["사업연도", "매출액"],
        rows=[["2023", "1"], ["2024", "2"], ["2025", "3"]],
    )
    analysis, latest = real._performance_period_labels(
        table,
        {"report_nm": "반기보고서 (2026.06)"},
    )
    assert analysis == "2023~2025 완료 회계연도"
    assert latest == "2026년 반기 공식 공시"


@pytest.mark.parametrize(
    "report_name,want",
    [
        ("분기보고서 (2026.03)", "2026년 1분기 공식 공시"),
        ("분기보고서 (2026.09)", "2026년 3분기 공식 공시"),
        ("사업보고서 (2025.12)", "2025년 연간 공식 공시"),
    ],
)
def test_최신_실적_기간은_공시_종류까지_표시한다(report_name: str, want: str) -> None:
    assert real._performance_period_labels(None, {"report_nm": report_name})[1] == want


# ══════════════════════════════════════════════════════════
# 캐시가 깨졌을 때 — 조용히 원래 길로 (AC 4)
# ══════════════════════════════════════════════════════════


def test_캐시_조회가_실패해도_조사는_끝까지_간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("저장소가 잠겼습니다")

    monkeypatch.setattr(real.cache_store, "get_company_report_hit", boom)
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None


def test_캐시_저장이_실패해도_보고서는_나간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("디스크가 가득 찼습니다")

    monkeypatch.setattr(real.cache_store, "save_company_report", boom)
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.charged is True


def test_DB를_아예_못_열어도_조사는_끝까지_간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """저장소 파일 자체가 없거나 권한이 없는 경우 — 조사는 계속돼야 한다."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("저장소를 열 수 없습니다")

    monkeypatch.setattr(real.storage_db, "connect", boom)
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None


# ══════════════════════════════════════════════════════════
# 저장 규칙 — S2 · 키 일치
# ══════════════════════════════════════════════════════════


def test_보고서를_만들면_1층_캐시에_한_줄이_남는다(engine: FakeEngine) -> None:
    """저장이 «실제로» 됐는지 DB를 직접 열어 확인한다 (되는 줄 알았는데 안 되는 사고 방지)."""
    from src.features.storage import db as storage_db

    _run()

    with storage_db.connect() as conn:
        rows = conn.execute(
            "SELECT corp_id, job_key, fiscal_year FROM layer1_cache"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["corp_id"] == CORP_ID
    assert rows[0]["job_key"] == "product:company-analysis"
    assert rows[0]["fiscal_year"] == max(FISCAL_YEARS)


def test_수집_실패가_끼면_캐시에_저장하지_않는다(engine: FakeEngine) -> None:
    """★ 정본 03_수집/1_흐름/02_실패처리.md — 「⚠️ 못 가져옴 → ❌ 저장 안 함」.

    그날만 죽은 소스 때문에 그 회사가 「자료 없는 회사」로 굳어버리면 안 된다.
    """
    from src.features.storage import db as storage_db

    engine.news_fails = True
    first = _run()
    calls_after_first = engine.generate_ai_calls

    with storage_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM layer1_cache").fetchone()["n"]

    second = _run()

    assert first.outcome is Outcome.REPORT      # 보고서 자체는 나간다
    assert count == 0, "우리 쪽 실패가 낀 결과를 캐시에 저장했습니다"
    assert engine.generate_ai_calls > calls_after_first  # 다시 시도하면 새로 만든다
    assert second.message == ""


def test_자료가_없는_것은_실패가_아니므로_캐시한다(engine: FakeEngine) -> None:
    """❌ 없음(회사의 사실)과 ⚠️ 못 가져옴(우리 실패)을 섞으면 캐시가 영영 안 찬다."""
    engine.news_fails = False       # 뉴스 0건 = ❌ 없음
    _run()
    calls_after_first = engine.generate_ai_calls
    _run()
    assert engine.generate_ai_calls == calls_after_first


def test_공고와_요구역량은_회사분석_저장소에_들어가지_않는다(engine: FakeEngine) -> None:
    """신규 보고서 payload는 공고·직무·요구역량을 보관하지 않는다."""
    from src.features.storage import db as storage_db

    secret = "지원자 홍길동 010-1234-5678"
    _run(posting=f"{POSTING}\n{secret}")

    with storage_db.connect() as conn:
        dump = "\n".join(
            str(row[0])
            for row in conn.execute("SELECT payload_json FROM reports").fetchall()
        )
    assert dump, "보고서가 저장되지 않았다면 이 시험은 아무것도 안 본 것이다"
    assert '"job": ""' in dump
    assert '"requirements": []' in dump
    assert "3년 이상 경력" not in dump
    assert "010-1234-5678" not in dump


def test_공고_개인정보는_AI에_아예_전달하지_않는다(engine: FakeEngine) -> None:
    """회사분석 경로는 공고 입력을 읽거나 마스킹해 보내지도 않는다."""
    secret = "010-1234-5678"

    _run(posting=f"{POSTING}\n담당자 연락처 {secret}")

    assert engine.posting_ai_prompts, "회사 분석 AI 경계를 실제로 지나지 않은 시험입니다"
    assert all(secret not in prompt for prompt in engine.posting_ai_prompts)
    assert all(_MASK not in prompt for prompt in engine.posting_ai_prompts)


def test_캐시_없이는_매번_생성AI가_돈다(engine: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 위 시험들이 «캐시 덕분에» 통과한 것인지 확인하는 대조군.

    캐시 조회를 항상 미적중으로 만들면 두 번째 요청에서도 생성 AI가 돌아야 한다.
    안 돈다면 위 시험들은 캐시가 아니라 다른 이유로 통과한 것이다.
    """
    monkeypatch.setattr(real.cache_store, "get_company_report_hit", lambda *a, **k: None)
    _run()
    calls_after_first = engine.generate_ai_calls
    _run()
    assert engine.generate_ai_calls > calls_after_first

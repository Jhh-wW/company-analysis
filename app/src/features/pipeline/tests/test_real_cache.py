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
from dataclasses import replace
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.core import deployment_identity
from src.core.constants import GENERATION_MODEL
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.budget.constants import (
    PAID_PHASE_PROVIDER_BUDGET_KRW,
    SPEND_PHASE_PIPELINE,
)
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.pipeline import real
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import (
    CompanyCard,
    Grade,
    Outcome,
    Report,
    ReportTable,
    RunResult,
    UserInput,
)
from src.features.storage import cache as cache_store
from src.features.storage import db as storage_db
#: 8·9 생성 지시문임을 알아보는 표시. 글자를 베끼지 않고 «상수를 그대로» 쓴다 —
#: 지시문이 바뀌어도 이 시험이 조용히 어긋나지 않는다.
from src.features.spanselect.constants import PROMPT_PICK
from src.shared import engine_build_identity as build_identity_contract
from src.shared.official_ir import (
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    IR_DART_WWW_REDIRECT_VALUE,
    IR_METADATA_VERIFICATION_FIELD,
    IR_METADATA_VERIFICATION_VALUE,
    IR_REPORTING_PERIOD_FIELD,
)

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


@pytest.fixture(autouse=True)
def _검증된_배포에서_캐시를_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


def test_생성cache_namespace는_교대하는_raw환경도_한_snapshot만_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revision=A, build=B로 찢어지는 두 번 읽기 TOCTOU를 재현해 막는다."""

    first = "1" * deployment_identity.COMMIT_FULL_LEN
    second = "2" * deployment_identity.COMMIT_FULL_LEN
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    original_environment = deployment_identity.os.environ
    commit_reads = 0

    class AlternatingRawEnvironment:
        def get(self, name: str, default: str = "") -> str:
            nonlocal commit_reads
            if name == "RENDER_GIT_COMMIT":
                commit_reads += 1
                return first if commit_reads % 2 else second
            if name == "APP_GIT_COMMIT":
                return ""
            return original_environment.get(name, default)

    # 전역 ``os.environ`` 자체를 갈아 끼우면 pytest 등 같은 프로세스의 다른
    # 소비자까지 공격용 mapping을 보게 된다. 대상 모듈의 os binding만 격리한다.
    monkeypatch.setattr(
        deployment_identity,
        "os",
        SimpleNamespace(environ=AlternatingRawEnvironment()),
    )

    identity = build_identity_contract.process_engine_build_identity()
    generation_mode = real.engine_mode.freeze_process_engine_mode()
    namespace = real._generation_cache_namespace(
        SimpleNamespace(MODEL="snapshot-test-model"),
        identity,
        generation_mode,
        # 이 시험이 보는 것은 배포 신원뿐이다. 모드를 모르는 경우(None)는
        # 옛 열쇠 구성을 그대로 쓰므로 여기 단정이 흔들리지 않는다.
        release_mode=None,
    )

    assert namespace is not None
    assert namespace.deployment_revision == first
    # 배포 revision이 따로 있어도 생성기 build 계약을 image에서 버리면 안 된다.
    assert namespace.image_digest == f"generator-build:{identity.build_id}"
    assert second not in namespace.image_digest
    assert commit_reads == 1


def test_v1_롤백namespace도_같은_배포build_contract를_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """운영 v2를 끈 순간 adapter와 v1 namespace가 갈라져 출고가 막히지 않는다."""

    commit = "3" * deployment_identity.COMMIT_FULL_LEN
    monkeypatch.delenv(real.ENGINE_V2_ENV_NAME, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", commit)

    identity = build_identity_contract.process_engine_build_identity()
    generation_mode = real.engine_mode.freeze_process_engine_mode()
    namespace = real._generation_cache_namespace(
        SimpleNamespace(MODEL="rollback-test-model"),
        identity,
        generation_mode,
        release_mode=None,
    )

    assert namespace is not None
    assert namespace.schema_version == real.CANONICAL_SCHEMA_VERSION
    assert namespace.deployment_revision == commit
    assert namespace.image_digest == f"generator-build:{identity.build_id}"


@pytest.mark.parametrize("cache_kind", ("v1", "v2"))
@pytest.mark.parametrize(
    ("start_commit", "current_commit"),
    (("a" * 40, "b" * 40), ("a" * 40, ""), ("", "b" * 40)),
)
def test_layer1_생성helper는_쓰기직전_배포drift때_행을_남기지_않는다(
    cache_kind: str,
    start_commit: str,
    current_commit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    if start_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", start_commit)
    frozen = build_identity_contract.capture_engine_build_identity()
    if current_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", current_commit)
    else:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    if cache_kind == "v1":
        real._company_cache_save(
            corp_id=CORP_ID,
            report=build_demo_report(),
            fiscal_year=FILING_YEAR,
            source_identity_digest="a" * 64,
            build_identity=frozen,
        )
    else:
        real._v2_cache_save(
            corp_id=CORP_ID,
            report=Report(
                company="테스트전자",
                job="",
                corp_type="상장사",
                grade=Grade.COMPLETE,
                sections=[],
                schema_version=ENGINE_V2_SCHEMA_VERSION,
                generated_at="2026-08-31",
            ),
            fiscal_year=FILING_YEAR,
            source_identity_digest="a" * 64,
            build_identity=frozen,
        )

    with storage_db.connect() as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache_store.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0


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
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        self.requests.append(kwargs)
        return SimpleNamespace(
            model=kwargs.get("model", "가짜모델"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class _FakeClient:
    """실제 SDK처럼 retry 옵션을 받되 provider 네트워크는 전혀 쓰지 않는다."""

    def __init__(self) -> None:
        self.messages = _FakeMessages()
        self.retry_options: list[int] = []

    def with_options(self, *, max_retries: int, timeout: float):
        assert max_retries == 0
        assert timeout == real.ANTHROPIC_TIMEOUT_SEC
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
        #: `decide()` 가 받은 인자 기록 — 「무엇을 근거로 판정했나」를 시험이 본다
        self.decide_calls: list[dict[str, Any]] = []
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
                    "corp_code": "00999999",
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
                "corp_code": CORP_ID,
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
        self,
        corp_cls: str,
        has_audit: bool,
        bizr_no: Any,
        matcher: Any,
        has_financial_statements: bool = False,
    ) -> _FakeJudgment:
        """★ 진짜 `judgment.logic.decide`와 «같은 모양»을 유지한다.

        모양이 어긋나면 앱은 TypeError로 죽는데, 가짜가 `**kwargs`로 다 받아
        주면 시험은 초록불이라 그 사고를 못 잡는다. 그래서 인자를 하나하나 적는다.
        """
        # 앱이 «무엇을 근거로» 판정을 요청했는지 기록한다 — 시험이 이 값을 대조한다.
        self.decide_calls.append(
            {
                "corp_cls": corp_cls,
                "has_audit": has_audit,
                "has_financial_statements": has_financial_statements,
            }
        )
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
                            product_role="출시해 판매",
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
          정본 「조용한 누락 금지」를
          지키자 그 구멍이 드러났다 — **시험이 엉뚱한 이유로 통과하고 있었다.**
          진짜 경로는 요구역량을 뽑기 «전에» 지우개를 돌린다 (`real.py` 5.5).
        """
        erased, hits = _PHONE_PATTERN.subn(_MASK, text)
        return SimpleNamespace(text=erased, counts={"연락처": hits} if hits else {})

    # ── 6 수집 ───────────────────────────────────────────
    def latest_report_rcept(
        self, corp_code: str, corp_type: str, counter: Any, *, business_date: Any = None
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
            "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다. "
            "국내외 반도체 제조 고객을 대상으로 SmartX 반도체 검사 장비 제품을 "
            "반도체 검사 장비 시장에 공급한다. 연결재무제표의 매출액과 영업이익을 공시한다."
        )

    def fetch_financials(
        self, corp_code: str, counter: Any, *, business_date: Any = None
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
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
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


@pytest.mark.local_integration
def test_로컬통합_삼성전자_저장원문은_가짜AI로_생성이후까지_무과금재현한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P01에서 저장된 DART 원문을 읽되 provider 대신 완전한 가짜 응답을 쓴다.

    원문은 파일럿 실행의 로컬 증거라 저장소에 추가하지 않는다. 증거가 없는 일반
    개발 환경에서는 건너뛰고, 있으면 해시를 먼저 맞춘 뒤 실제 파서·보정기·원문
    대조기를 거친다. 가짜 선택이 1~8장 계약을 채우지 못하면 Writer가 빈칸을
    지어내기 전에 멈추는지도 함께 검사한다.
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
        pytest.fail(
            "로컬 통합 시험을 선택했지만 해시가 검증된 삼성전자 P01 DART 원문이 없습니다"
        )

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
                    "corp_code": CORP_ID,
                    "corp_name": "삼성전자",
                    "corp_name_eng": "SAMSUNG ELECTRONICS CO., LTD.",
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
            self, corp_code: str, corp_type: str, counter: Any, *, business_date: Any = None
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
    assert "핵심 기본 보고서" in result.message
    assert calls == {"writer": 0, "comparison": 0, "finalize": 0}
    assert progress[:5] == ["identify", "judge", "collect", "gate", "generate"]
    assert fake.client.messages.calls == real.VOTE_ROUNDS
    assert len(result.ai_cost_events) == real.VOTE_ROUNDS
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


def test_본조사_DART_013이고_재무제표도_없으면_공시없음으로_명확히거부한다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 계약 변경 — 옛 이름은 「013은 …추가호출하지않는다」였다.

    013(감사보고서 목록 비어 있음)은 「이름이 감사보고서인 공시가 없다」일 뿐
    「분석할 자료가 없다」가 아니다. 사업보고서를 내는 회사는 감사보고서를 그
    안에 첨부하므로 별도 공시가 안 생긴다(외부감사법 23조① 단서). 그래서 이제
    **재무제표를 한 번 더 확인한 뒤에** 거부한다 — 그 확인도 없이 거부하던 것이
    현대카드·우리은행을 삼킨 결함이었다.

    이 시험은 「둘 다 없을 때」의 거부 화면을 지킨다. 「감사보고서는 없지만
    재무제표는 있을 때」는 바로 아래 시험이 지킨다.
    """
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
    # 재무제표«도» 없는 상황을 만든다 — 그래야 거부가 «정직한» 거부다.
    monkeypatch.setattr(engine, "fetch_financials", lambda *_a, **_k: (None, []))
    monkeypatch.setattr(
        engine,
        "decide",
        lambda *_args, **_kwargs: SimpleNamespace(status="거부B", corp_type=None),
    )

    result = _run()

    assert result.outcome is Outcome.REJECT_NO_DISCLOSURE
    # ★ 화면은 «우리가 찾은 방법»이 아니라 «사용자가 알아야 할 사실»을 말한다.
    #   옛 문구 「감사보고서를 낸 기록이 없습니다」는 방법이었고, 틀리기도 했다.
    assert "재무 자료" in result.message
    assert "감사보고서" not in result.message
    assert result.sources[0].state == "none"
    # ★ 가짜 엔진의 fetch_financials 는 get_json 을 안 거치므로 여기 안 잡힌다.
    #   진짜 엔진에서는 fnlttSinglAcnt.json 이 «3번» 더 나간다 (최근 3개 사업연도).
    #   ⚠️ 돈은 0원이지만 DART 일일 호출 한도는 그만큼 쓴다. 게다가 판정 «전»으로
    #      옮겼으므로 거부될 회사도 3번을 쓴다 — 예전엔 통과분만 썼다.
    #      개수를 못 박는 시험은 test_engine_financials_contract.py 에 있다.
    assert endpoints == ["company.json", "list.json"]
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


def test_본조사_감사보고서가_없어도_재무제표가_있으면_그_사실을_판정에_넘긴다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 현대카드·우리은행 부류 — 이 시험이 수정의 «이유»다.

    실측: 현대카드는 3년간 331건을 공시하고 재무 API 가 38개 계정을 정상으로
    주는데도, 「감사보고서」라는 이름의 공시가 없다는 이유로 거부됐다.
    이름난 비상장사 13곳을 재보니 7곳이 같은 이유로 막혀 있었다
    (우리은행·SC제일은행·토스·야놀자·현대카드·현대캐피탈·현대커머셜).

    이 시험은 앱이 판정에 «무엇을 근거로» 넘기는지를 못 박는다.
    판정 사다리 자체는 `analysis_engine/src/features/judgment/tests` 가 지킨다.
    """
    original = engine.get_json

    def no_audit_but_has_financials(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        if endpoint == "company.json":
            profile = dict(original(endpoint, params, counter))
            profile["corp_cls"] = "E"  # 비상장 — 조건 2 로 내려간다
            return profile
        if endpoint == "list.json":
            counter.count += 1
            return {"status": "013", "message": "조회된 데이타가 없습니다."}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", no_audit_but_has_financials)

    _run()

    assert engine.decide_calls, "판정이 아예 호출되지 않았다"
    넘긴것 = engine.decide_calls[-1]
    assert 넘긴것["corp_cls"] == "E"
    assert 넘긴것["has_audit"] is False, "감사보고서가 없는 상황이어야 한다"
    # ↓ 이 한 줄이 이번 수정의 핵심이다. 빠지면 현대카드가 다시 거부된다.
    assert 넘긴것["has_financial_statements"] is True


def test_본조사_재무제표도_없으면_판정에_없다고_넘긴다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """위 시험의 «반대쪽» — 자료가 정말 없을 때 있다고 넘기면 안 된다."""
    original = engine.get_json

    def nothing_at_all(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        if endpoint == "company.json":
            profile = dict(original(endpoint, params, counter))
            profile["corp_cls"] = "E"
            return profile
        if endpoint == "list.json":
            counter.count += 1
            return {"status": "013", "message": "조회된 데이타가 없습니다."}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", nothing_at_all)
    monkeypatch.setattr(engine, "fetch_financials", lambda *_a, **_k: (None, []))

    _run()

    assert engine.decide_calls, "판정이 아예 호출되지 않았다"
    넘긴것 = engine.decide_calls[-1]
    assert 넘긴것["has_audit"] is False
    assert 넘긴것["has_financial_statements"] is False


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


def test_첫_선택라운드가_완결되면_추가호출을_생략하고_진단이_남는다(
    engine: FakeEngine,
) -> None:
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.span_selection_result_reason == "validated_basic_coverage"
    assert len(result.span_selection_diagnostics) == 1
    assert [item.round_number for item in result.span_selection_diagnostics] == [1]
    requested_limits = {
        item.requested_max_tokens for item in result.span_selection_diagnostics
    }
    assert len(requested_limits) == 1
    assert all(limit > 0 for limit in requested_limits)
    assert all(
        item.provider_selected > 0 for item in result.span_selection_diagnostics
    )
    # 선택 1 + Writer 1 + 독립 Reviewer 1. 요약은 검수된 본문을 재사용해 0회다.
    assert engine.client.messages.calls == 3
    assert len(result.ai_cost_events) == 3
    assert all(
        item.validation_kept > 0 for item in result.span_selection_diagnostics
    )


def test_첫_선택이_불완전해도_두번째_단독완결이면_보고서를_만든다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = real.select_canonical_spans
    calls = 0

    def first_incomplete_then_complete(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        picked, rejected = original(*args, **kwargs)
        if calls == 1:
            picked = [
                item for item in picked if item.claim_type != "future_plan"
            ]
        return picked, rejected

    monkeypatch.setattr(
        real,
        "select_canonical_spans",
        first_incomplete_then_complete,
    )

    result = _run()

    assert result.outcome is Outcome.REPORT
    assert calls == 2
    assert [item.round_number for item in result.span_selection_diagnostics] == [1, 2]
    # 선택 2 + Writer 1 + 독립 Reviewer 1. 요약과 뉴스 선별은 0회다.
    assert engine.client.messages.calls == 4
    selection_requests: list[tuple[object, str]] = []
    for request in engine.client.messages.requests:
        content = request["messages"][0]["content"]
        if isinstance(content, str):
            prompt = content
        else:
            prompt = "".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict)
            )
        if "공식 근거 기반 회사분석 보고서의 사실 배치 작업" in prompt:
            selection_requests.append((content, prompt))

    assert len(selection_requests) == 2
    # 1회차와 focus가 붙은 2회차는 정확한 user text가 다르므로, 전체 text 한
    # 블록을 ephemeral cache로 쓰지 않는다. 두 호출 모두 보통 입력으로 보낸다.
    assert all(isinstance(content, str) for content, _prompt in selection_requests)
    assert selection_requests[0][1] != selection_requests[1][1]
    assert "선택 2회차 보정 초점" not in selection_requests[0][1]
    assert "선택 2회차 보정 초점" in selection_requests[1][1]
    selection_costs = [
        event for event in result.ai_cost_events if event.stage == "span_selection"
    ]
    assert len(selection_costs) == 2
    assert all(event.cache_creation_tokens == 0 for event in selection_costs)
    assert all(event.cache_read_tokens == 0 for event in selection_costs)


def test_두_선택의_검증통과_근거가_서로_보완되면_누적해_보고서를_만든다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = real.select_canonical_spans
    calls = 0
    received_focus: list[dict[str, Any]] = []

    def complementary_rounds(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        received_focus.append(dict(kwargs))
        picked, rejected = original(*args, **kwargs)
        if calls == 1:
            # 첫 회에는 현재 문제와 미래 계획이 빠진다. 현재 문제는 전역 출고
            # 필수 장은 아니지만, 이미 실행하는 2회차의 보충 초점에는 포함한다.
            return [
                item
                for item in picked
                if item.claim_type not in {"current_issue", "future_plan"}
            ], rejected
        # 두 번째 회도 보충 대상 두 건뿐이라 단독으로는 성립하지 않는다.
        return [
            item
            for item in picked
            if item.claim_type in {"current_issue", "future_plan"}
        ], rejected

    monkeypatch.setattr(real, "select_canonical_spans", complementary_rounds)

    result = _run()

    assert result.outcome is Outcome.REPORT
    assert calls == 2
    assert [item.round_number for item in result.span_selection_diagnostics] == [1, 2]
    assert received_focus[0]["focus_missing_claim_roles"] == ()
    assert "current_issue" in received_focus[1]["focus_missing_claim_roles"]
    assert "future_plan" in received_focus[1]["focus_missing_claim_roles"]
    assert received_focus[1]["focus_verified_sids"]
    # 선택 2 + Writer 1 + 독립 Reviewer 1. 동일 전체 선택을 세 번째로 반복하지 않는다.
    assert engine.client.messages.calls == 4


def test_JYP처럼_전체역할은_부족해도_정체성_수익구조_삼개년표로_부분출고한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """검증 통과 사실을 8개 미만이라는 이유만으로 전부 폐기하지 않는다."""

    original = real.select_canonical_spans
    calls = 0

    def minimum_verified_rounds(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        picked, rejected = original(*args, **kwargs)
        return [
            item
            for item in picked
            if item.claim_type in {"identity_summary", "revenue_model"}
        ], rejected

    monkeypatch.setattr(real, "select_canonical_spans", minimum_verified_rounds)

    result = _run()
    calls_after_first = engine.generate_ai_calls
    second = _run()

    assert calls == real.VOTE_ROUNDS * 2
    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.report.grade is Grade.PARTIAL
    sections = {section.cell: section for section in result.report.sections}
    assert {"identity", "business_model", "past_changes"} <= set(sections)
    assert sections["past_changes"].tables
    assert any("3장" in reason for reason in result.report.shortfall_reasons)
    assert any("6장" in reason for reason in result.report.shortfall_reasons)
    # 선택 2 + Writer 1 + 독립 Reviewer 1. 요약은 검수된 사실 재사용이다.
    assert calls_after_first == 4
    assert second.outcome is Outcome.REPORT
    assert engine.generate_ai_calls > calls_after_first
    assert not second.cache_hit


@pytest.mark.parametrize(
    ("missing_section", "expected_claim_type"),
    (("identity", "identity_summary"), ("business_model", "revenue_model")),
)
def test_Writer가_최소핵심을_한번빠뜨리면_검증된_span만_한번보충한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    missing_section: str,
    expected_claim_type: str,
) -> None:
    original_ask = engine._ask
    writer_calls = 0

    def omit_minimum_once(client, prompt, schema, max_tokens=700):
        nonlocal writer_calls
        payload, usage = original_ask(client, prompt, schema, max_tokens=max_tokens)
        if "공식 근거 기반 기업분석 보고서" in prompt and "■ 칸과 근거" in prompt:
            writer_calls += 1
            if writer_calls == 1:
                payload = {
                    **payload,
                    "칸": [
                        item
                        for item in payload["칸"]
                        if item["칸번호"] != missing_section
                    ],
                }
        return payload, usage

    monkeypatch.setattr(engine, "_ask", omit_minimum_once)

    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert writer_calls == 2
    assert len(result.span_selection_diagnostics) == 1
    assert any(
        fact.claim_type == expected_claim_type for fact in result.report.fact_records
    )
    # 선택은 다시 하지 않고 Writer+독립 Reviewer만 한 묶음 추가된다.
    assert engine.client.messages.calls == 5


def test_Writer가_수익구조를_보충에도_빠뜨리면_닫힌사유로_멈춘다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_ask = engine._ask
    writer_calls = 0

    def always_omit_revenue(client, prompt, schema, max_tokens=700):
        nonlocal writer_calls
        payload, usage = original_ask(client, prompt, schema, max_tokens=max_tokens)
        if "공식 근거 기반 기업분석 보고서" in prompt and "■ 칸과 근거" in prompt:
            writer_calls += 1
            payload = {
                **payload,
                "칸": [
                    item
                    for item in payload["칸"]
                    if item["칸번호"] != "business_model"
                ],
            }
        return payload, usage

    monkeypatch.setattr(engine, "_ask", always_omit_revenue)

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.final_gate_reason == "publish_missing_revenue"
    assert writer_calls == 2
    assert len(result.span_selection_diagnostics) == 1


def test_뒤_선택에서_최소사실_SID가_충돌하면_앞_부분집합을_되살리지_않는다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = real.select_canonical_spans
    calls = 0

    def conflicting_minimum_rounds(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        picked, rejected = original(*args, **kwargs)
        minimum = [
            item
            for item in picked
            if item.claim_type in {"identity_summary", "revenue_model"}
        ]
        if calls == 1:
            return minimum, rejected
        return [
            replace(
                item,
                sentence=f"충돌한 {item.sentence}",
                fragment_id=item.fragment_id + 100,
            )
            for item in minimum
        ], rejected

    monkeypatch.setattr(real, "select_canonical_spans", conflicting_minimum_rounds)

    result = _run()

    assert calls == real.VOTE_ROUNDS
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    # 선택 두 번 뒤 충돌을 코드로 중단하므로 Writer·Reviewer는 부르지 않는다.
    assert engine.client.messages.calls == 2


def test_삼개년표가_없으면_선택AI를_부르기전에_멈춘다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(real, "build_three_year_table", lambda *_args, **_kwargs: None)
    selection_calls = 0

    def forbidden_selection(*_args: Any, **_kwargs: Any):
        nonlocal selection_calls
        selection_calls += 1
        raise AssertionError("정적 실패 조건에서 선택 AI를 호출했습니다")

    monkeypatch.setattr(real, "select_canonical_spans", forbidden_selection)

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert selection_calls == 0
    assert engine.client.messages.calls == 0
    assert result.span_selection_diagnostics == ()
    assert (
        result.span_selection_result_reason
        == "preflight_missing_three_year_performance"
    )


def test_원문조각이_없으면_기타게이트_코드로_멈춘다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "make_fragments", lambda *_args: {})

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == "other_gate"


def test_공식IR_ok_수집은_조각과_단계기록을_그대로_보존한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IR 수집기의 provenance 메타데이터를 파이프라인이 다시 만들지 않는다."""

    ir_fragment = {
        "종류": real.OFFICIAL_IR_FRAGMENT_KIND,
        "원문": "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다.",
        "출처": "https://www.ganada.example/ir/2025-results.pdf",
        "후보출처검증": "https_exact_dart_host",
        "문서명": "2025년 연간 실적 설명자료",
        "문서ID": "sha256:official-ir-pdf",
        "원문위치": "PDF 3쪽 · 문단 2",
    }
    monkeypatch.setattr(real, "_collect_news", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="분석에 쓸 본문 없음",
            candidate_scope_complete=True,
        ),
    )
    ir_call: dict[str, Any] = {}

    def collect_ir(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        ir_call.update(kwargs)
        return SimpleNamespace(
            state="ok",
            fragments=[dict(ir_fragment)],
            detail="검증 완료",
            attempted_documents=1,
            downloaded_pdf_bytes=4096,
            candidate_scope_complete=True,
        )

    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        collect_ir,
    )
    counter = engine.UsageCounter()
    financials, years = engine.fetch_financials(CORP_ID, counter)
    steps: list[dict[str, Any]] = []

    fragments, _tables, _filing_text = real._collect(
        engine,
        engine._client(),
        {
            "status": "000",
            "corp_code": CORP_ID,
            "corp_name": "가나다전자",
            "corp_name_eng": "GANADA ELECTRONICS CO., LTD.",
            "hm_url": "https://www.ganada.example",
        },
        UserInput(
            company="가나다전자",
            job=JOB,
            region="서울 강남구",
            posting_text=POSTING,
        ),
        counter,
        steps,
        financials=financials,
        fin_years=years,
        filing=None,
    )

    collected_ir = [
        fragment
        for fragment in fragments.values()
        if fragment.get("종류") == real.OFFICIAL_IR_FRAGMENT_KIND
    ]
    assert collected_ir == [ir_fragment]
    assert ir_call["company_aliases"] == ("GANADA ELECTRONICS CO., LTD.",)
    assert next(step for step in steps if step.get("step") == "6_수집_공식IR") == {
        "step": "6_수집_공식IR",
        "조각수": 1,
        "문서시도": 1,
        "PDF바이트": 4096,
        "상세": "검증 완료",
        "후보범위완전": True,
    }
    ir_status = next(
        source for source in real._sources_from(steps) if source.name == "회사 공식 IR"
    )
    assert ir_status.state == "ok"
    assert "PDF 조각 1개" in ir_status.detail
    assert "문서 1개 시도" in ir_status.detail


@pytest.mark.parametrize(
    ("ir_step", "expected_state"),
    [
        ({"오류": "PDF 파서 실패", "후보범위완전": False}, "failed"),
        ({"없음": "IR PDF 링크 없음", "후보범위완전": True}, "none"),
        (
            {
                "조각수": 2,
                "문서시도": 1,
                "상세": "검증 완료",
                "후보범위완전": True,
            },
            "ok",
        ),
    ],
)
def test_공식IR_SourceStatus는_실패_없음_성공을_섞지_않는다(
    ir_step: dict[str, Any],
    expected_state: str,
) -> None:
    steps = [{"step": "6_수집_공식IR", **ir_step}]

    source = next(
        item for item in real._sources_from(steps) if item.name == "회사 공식 IR"
    )

    assert source.state == expected_state


@pytest.mark.parametrize(
    "ir_result",
    [
        SimpleNamespace(
            state="failed",
            fragments=[],
            detail="PDF 다운로드·검증 실패",
            attempted_documents=1,
            downloaded_pdf_bytes=2048,
            candidate_scope_complete=False,
        ),
        SimpleNamespace(
            state="ok",
            fragments=[
                {
                    "종류": real.OFFICIAL_IR_FRAGMENT_KIND,
                    "원문": "가나다전자의 2025년 연간 실적 설명자료다.",
                    "출처": "https://www.ganada.example/ir/truncated.pdf",
                    "후보출처검증": "https_exact_dart_host",
                    "문서명": "2025년 연간 실적 설명자료",
                    "문서ID": "sha256:truncated-ir-pdf",
                    "원문위치": "PDF 1쪽 · 문단 1",
                }
            ],
            detail="페이지·글자 상한 잘림 1개",
            attempted_documents=1,
            downloaded_pdf_bytes=4096,
            candidate_scope_complete=False,
        ),
    ],
    ids=("failed", "truncated"),
)
def test_공식IR_실패나_잘림은_후보없음으로_거짓확정하지않는다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    ir_result: SimpleNamespace,
) -> None:
    original_get_json = engine.get_json
    original_make_fragments = engine.make_fragments

    def profile_with_homepage(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        payload = original_get_json(endpoint, params, counter)
        if endpoint == "company.json" and params.get("corp_code") == CORP_ID:
            return {**payload, "corp_code": CORP_ID, "hm_url": "https://www.ganada.example"}
        return payload

    def fragments_without_candidate(*args: Any, **kwargs: Any):
        fragments = original_make_fragments(*args, **kwargs)
        fragments[1] = {
            **fragments[1],
            "원문": "가나다전자는 반도체 검사 장비 전문기업이다.",
        }
        return fragments

    monkeypatch.setattr(engine, "get_json", profile_with_homepage)
    monkeypatch.setattr(
        engine,
        "read_filing_text",
        lambda _path: "가나다전자는 반도체 검사 장비를 국내외 고객에게 공급한다.",
    )
    monkeypatch.setattr(engine, "make_fragments", fragments_without_candidate)
    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="후보 문장 없음",
            candidate_scope_complete=True,
        ),
    )
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        lambda *_args, **_kwargs: ir_result,
    )
    span_calls = 0

    def empty_span(*_args: Any, **_kwargs: Any):
        nonlocal span_calls
        span_calls += 1
        return [], []

    monkeypatch.setattr(real, "select_canonical_spans", empty_span)

    result = _run()

    assert span_calls == real.VOTE_ROUNDS
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == "other_gate"
    assert result.final_gate_reason != "comparison_blocked"
    ir_status = next(source for source in result.sources if source.name == "회사 공식 IR")
    assert ir_status.state == ("failed" if ir_result.state == "failed" else "ok")


def test_공식IR까지_비교후보가_없어도_기본분석은_계속한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_json = engine.get_json
    original_make_fragments = engine.make_fragments

    def profile_with_homepage(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        payload = original_get_json(endpoint, params, counter)
        if endpoint == "company.json" and params.get("corp_code") == CORP_ID:
            return {**payload, "corp_code": CORP_ID, "hm_url": "https://www.ganada.example"}
        return payload

    def fragments_without_candidate(*args: Any, **kwargs: Any):
        fragments = original_make_fragments(*args, **kwargs)
        fragments[1] = {
            **fragments[1],
            "원문": "가나다전자는 반도체 검사 장비 전문기업이다.",
        }
        return fragments

    monkeypatch.setattr(engine, "get_json", profile_with_homepage)
    monkeypatch.setattr(
        engine,
        "read_filing_text",
        lambda _path: "가나다전자는 반도체 검사 장비를 국내외 고객에게 공급한다.",
    )
    monkeypatch.setattr(engine, "make_fragments", fragments_without_candidate)
    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="후보 문장 없음",
            candidate_scope_complete=True,
        ),
    )
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="같은 HTTPS 호스트에서 공식 IR PDF 링크를 찾지 못함",
            attempted_documents=0,
            downloaded_pdf_bytes=0,
            candidate_scope_complete=True,
        ),
    )
    span_called = False

    def record_span(*_args: Any, **_kwargs: Any):
        nonlocal span_called
        span_called = True
        return [], []

    monkeypatch.setattr(real, "select_canonical_spans", record_span)

    result = _run()

    assert span_called is True
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == "other_gate"
    assert engine.generate_ai_calls == 0
    ir_status = next(source for source in result.sources if source.name == "회사 공식 IR")
    assert ir_status.state == "none"


def test_실제_run은_메타검증_IR을_DART법인에_먼저_결속한_뒤_Writer후보로_넘긴다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_json = engine.get_json
    captured: list[dict[int, dict[str, Any]]] = []

    def profile_with_homepage(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        payload = original_get_json(endpoint, params, counter)
        if endpoint == "company.json" and params.get("corp_code") == CORP_ID:
            return {
                **payload,
                "corp_code": CORP_ID,
                "corp_name": "가나다전자",
                "hm_url": "https://ganada.example",
            }
        return payload

    ir_fragment = {
        "종류": real.OFFICIAL_IR_FRAGMENT_KIND,
        "원문": "가나다전자는 반도체 검사 장비 기업이다.",
        "출처": "https://www.ganada.example/ir/2026-q2",
        "첨부URL": "https://cdn.example/ganada-q2.pdf",
        IR_DART_WWW_REDIRECT_FIELD: IR_DART_WWW_REDIRECT_VALUE,
        IR_DART_WWW_REDIRECT_FROM_FIELD: "ganada.example",
        IR_DART_WWW_REDIRECT_TO_FIELD: "www.ganada.example",
        "문서일": "2026-08-12",
        IR_REPORTING_PERIOD_FIELD: "2026-Q2",
        IR_METADATA_VERIFICATION_FIELD: IR_METADATA_VERIFICATION_VALUE,
        "후보출처검증": "https_exact_dart_host",
        "문서명": "26년 2분기 IR자료",
        "문서ID": "c" * 64,
        "원문위치": "PDF p.2 1문단 · pypdf 6.16.1",
    }
    monkeypatch.setattr(engine, "get_json", profile_with_homepage)
    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="후보 문장 없음",
            candidate_scope_complete=True,
        ),
    )
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="ok",
            fragments=[dict(ir_fragment)],
            detail="검증 완료",
            attempted_documents=1,
            downloaded_pdf_bytes=4096,
            candidate_scope_complete=True,
        ),
    )

    def capture_span(_client: Any, fragments: dict[int, dict[str, Any]], *_args: Any, **_kwargs: Any):
        captured.append(fragments)
        return [], []

    monkeypatch.setattr(real, "select_canonical_spans", capture_span)

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert captured
    generated_ir = [
        fragment
        for fragments in captured
        for fragment in fragments.values()
        if fragment.get("종류") == real.OFFICIAL_IR_FRAGMENT_KIND
    ]
    assert generated_ir
    assert {item["발행처"] for item in generated_ir} == {"가나다전자"}
    assert all(item.get("도메인근거SourceID") for item in generated_ir)
    assert all(item.get("IR수집기준일") for item in generated_ir)


def test_오래된_newsroom_경쟁문장은_비교에서_빼고_기본분석은_계속한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_json = engine.get_json
    original_make_fragments = engine.make_fragments

    def profile_with_homepage(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        payload = original_get_json(endpoint, params, counter)
        if endpoint == "company.json" and params.get("corp_code") == CORP_ID:
            return {
                **payload,
                "corp_code": CORP_ID,
                "hm_url": "https://www.ganada.example",
            }
        return payload

    def fragments_without_candidate(*args: Any, **kwargs: Any):
        fragments = original_make_fragments(*args, **kwargs)
        fragments[1] = {
            **fragments[1],
            "원문": "가나다전자는 반도체 검사 장비 전문기업이다.",
        }
        return fragments

    monkeypatch.setattr(engine, "get_json", profile_with_homepage)
    monkeypatch.setattr(
        engine,
        "read_filing_text",
        lambda _path: "가나다전자는 반도체 검사 장비를 공급한다.",
    )
    monkeypatch.setattr(engine, "make_fragments", fragments_without_candidate)
    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="ok",
            fragments=[
                {
                    "종류": real.HOMEPAGE_FRAGMENT_KIND,
                    "원문": "가나다전자는 베타전자와 경쟁 관계인 기업이다.",
                    "출처": "https://www.ganada.example/newsroom/2015-competition",
                    "문서일": "2015-06-01",
                    "후보출처검증": "https_exact_dart_host",
                }
            ],
            detail="",
            candidate_scope_complete=True,
        ),
    )
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="공식 IR PDF 없음",
            attempted_documents=0,
            downloaded_pdf_bytes=0,
            candidate_scope_complete=True,
        ),
    )

    span_calls = 0

    def empty_span(*_args: Any, **_kwargs: Any):
        nonlocal span_calls
        span_calls += 1
        return [], []

    monkeypatch.setattr(real, "select_canonical_spans", empty_span)
    monkeypatch.setattr(
        real,
        "_attach_competitive_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("비교 후보가 없으면 후단 비교 수집을 호출하면 안 됩니다")
        ),
    )

    result = _run()

    assert span_calls == real.VOTE_ROUNDS
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == "other_gate"
    assert result.sentences_made == 0
    assert engine.generate_ai_calls == 0


def test_공식IR_Source와_attester는_참고로_보존되지만_v2후보가_아니다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_json = engine.get_json
    original_discover = real.discover_official_source_candidates
    captured: dict[str, Any] = {}

    def profile_with_homepage(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        payload = original_get_json(endpoint, params, counter)
        if endpoint == "company.json" and params.get("corp_code") == CORP_ID:
            return {**payload, "corp_code": CORP_ID, "hm_url": "https://www.ganada.example"}
        return payload

    ir_fragment = {
        "종류": real.OFFICIAL_IR_FRAGMENT_KIND,
        "원문": "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다.",
        "출처": "https://www.ganada.example/ir/2025-results.pdf",
        "후보출처검증": "https_exact_dart_host",
        "문서명": "2025년 연간 실적 설명자료",
        "문서ID": "sha256:official-ir-pdf",
        "원문위치": "PDF 3쪽 · 문단 2",
    }
    monkeypatch.setattr(engine, "get_json", profile_with_homepage)
    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="none",
            fragments=[],
            detail="후보 문장 없음",
            candidate_scope_complete=True,
        ),
    )
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="ok",
            fragments=[dict(ir_fragment)],
            detail="검증 완료",
            attempted_documents=1,
            downloaded_pdf_bytes=4096,
            candidate_scope_complete=True,
        ),
    )

    def recording_discover(evidence_rows: Any, sources: Any, catalog: Any, **kwargs: Any):
        rows = tuple(evidence_rows)
        source_registry = tuple(sources)
        ir_rows = tuple(
            row for row in rows if row.source.source_type == "회사 공식 IR"
        )
        if ir_rows:
            captured["ir_candidates"] = original_discover(
                ir_rows,
                source_registry,
                catalog,
                **kwargs,
            )
        return original_discover(rows, source_registry, catalog, **kwargs)

    def capture_comparison_input(report: Any, **kwargs: Any):
        captured["final_rows"] = tuple(kwargs["official_candidate_sentences"])
        captured["final_registry"] = tuple(kwargs["candidate_source_registry"])
        return report

    monkeypatch.setattr(real, "discover_official_source_candidates", recording_discover)
    monkeypatch.setattr(real, "_attach_competitive_position", capture_comparison_input)

    _run()

    ir_candidates = captured.get("ir_candidates")
    assert ir_candidates == ()

    final_ir_rows = [
        row
        for row in captured["final_rows"]
        if row.source.source_type == "회사 공식 IR"
    ]
    assert len(final_ir_rows) == 1
    ir_source = final_ir_rows[0].source
    assert ir_source.domain_attestation_source_id == f"dart-company-profile-{CORP_ID}"
    assert ir_source.url == ir_fragment["출처"]
    attesters = [
        source
        for source in captured["final_registry"]
        if source.provenance_role == "attestation_only"
    ]
    assert len(attesters) == 1
    assert attesters[0].source_id == ir_source.domain_attestation_source_id


def test_전체공시원문에_비교후보가_없어도_기본사실_span은_계속한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 뉴스에만 경쟁 문장이 남아 있어도 후보로 승격하지 않는다. 공식 홈페이지·
    # IR일 수 있는 비뉴스 fragment는 거짓 차단을 막기 위한 상위집합으로 본다.
    monkeypatch.setattr(
        engine,
        "read_filing_text",
        lambda _path: "회사는 반도체 검사 장비를 국내외 고객에게 공급한다.",
    )
    original_make_fragments = engine.make_fragments

    def fragments_with_news_only_candidate(*args: Any, **kwargs: Any):
        fragments = original_make_fragments(*args, **kwargs)
        fragments[1] = {**fragments[1], "종류": real.NEWS_FRAGMENT_KIND}
        return fragments

    monkeypatch.setattr(engine, "make_fragments", fragments_with_news_only_candidate)

    span_calls = 0

    def empty_span(*_args: Any, **_kwargs: Any):
        nonlocal span_calls
        span_calls += 1
        return [], []

    monkeypatch.setattr(real, "select_canonical_spans", empty_span)
    monkeypatch.setattr(
        real,
        "_attach_competitive_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("비교 후보가 없으면 후단 비교 수집을 호출하면 안 됩니다")
        ),
    )

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == "other_gate"
    assert span_calls == real.VOTE_ROUNDS
    assert result.sentences_made == 0
    assert result.sentences_passed == 0
    assert len(result.span_selection_diagnostics) == real.VOTE_ROUNDS
    assert result.span_selection_result_reason == "all_provider_rounds_empty"
    assert engine.generate_ai_calls == 0
    assert "회사 사실" in result.message


def test_경쟁사비교_실패는_원문사유를_숨기고_기본보고서를_반환한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    internal_reason = "외부에 저장하면 안 되는 경쟁사 비교 상세"

    def block_comparison(*_args: Any, **_kwargs: Any):
        raise real.ComparisonBlockedError((internal_reason,))

    monkeypatch.setattr(real, "_attach_competitive_position", block_comparison)

    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.report.grade is Grade.PARTIAL
    assert result.final_gate_reason == ""
    assert internal_reason not in result.message
    assert "9장 동종업계 비교" in result.message


def test_정본출고_차단은_원문사유대신_닫힌코드만_반환한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    internal_reason = "외부에 저장하면 안 되는 정본 검증 상세"

    def block_publish(*_args: Any, **_kwargs: Any):
        raise real.PublishBlockedError(
            SimpleNamespace(reasons=(internal_reason,))
        )

    monkeypatch.setattr(real, "finalize_report", block_publish)

    result = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == "publish_blocked"
    assert internal_reason not in result.message


def test_선택_최대2회_출력상한은_실측최대입력에서도_건별예산안이다(
    engine: FakeEngine,
) -> None:
    # 최신 P03·P05~P10 원장에서 span-selection 실측 입력 최댓값은 16,033이다.
    # cache 혜택을 0으로 두고 두 호출 모두 같은 최대 입력·최대 출력을 쓴다고 본다.
    pilot_max_observed_input_tokens = 16_033
    result = _run()
    requested_limits = {
        item.requested_max_tokens for item in result.span_selection_diagnostics
    }
    assert len(requested_limits) == 1
    requested_max_tokens = requested_limits.pop()
    max_selection_cost = provider_budget.usage_cost_krw(
        GENERATION_MODEL,
        pilot_max_observed_input_tokens * real.VOTE_ROUNDS,
        requested_max_tokens * real.VOTE_ROUNDS,
    )

    # 본조사 phase 900원은 평가 건별 1,200원보다 더 좁은 경계다.
    assert max_selection_cost == pytest.approx(386.68)
    assert max_selection_cost <= PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE]


def test_캐시로_돌려준_보고서가_처음_만든_것과_같다(engine: FakeEngine) -> None:
    first = _run()
    second = _run()
    assert second.report == first.report


def test_캐시_적중은_할당량을_안_깎는다(engine: FakeEngine) -> None:
    """「캐시 반환(1층 히트) → 0 · 무제한」."""
    first = _run()
    second = _run()
    assert first.charged is True
    assert second.charged is False


def test_v1은_배포commit을_모르면_provider전에_fail_closed한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(
        real.generation_coordination.GenerationCoordinationError,
        match="정상 배포 epoch",
    ):
        _run()

    assert engine.generate_ai_calls == 0
    assert engine.posting_ai_calls == 0
    with real.storage_db.connect() as conn:
        rows = conn.execute(
            f"SELECT COUNT(*) FROM {real.cache_store.TABLE_LAYER1_CACHE}"
        ).fetchone()[0]
    assert rows == 0


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
            "sj_div": "IS",
            "currency": "KRW",
            "reprt_code": "11011",
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
        "status": "000",
        "reprt_code": "11011",
        "list": [
            row("ifrs-full_Revenue", "매출액", ("821850000000", "601790000000", "566500000000")),
            row("dart_OperatingIncomeLoss", "영업이익", ("155250000000", "128260000000", "169440000000")),
            row("ifrs-full_ProfitLoss", "당기순이익", ("101000000000", "90000000000", "80000000000")),
        ]
    }
    monkeypatch.setattr(
        engine,
        "fetch_financials",
        lambda _corp, _counter, **_kwargs: (financials, [2025]),
    )
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
    assert "확인되지 않은 내용을 보고서처럼 보여주지 않" in result.message


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


def test_수집_실패가_끼면_캐시에_저장하지_않는다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 「⚠️ 못 가져옴 → ❌ 저장 안 함」.

    그날만 죽은 소스 때문에 그 회사가 「자료 없는 회사」로 굳어버리면 안 된다.
    """
    from src.features.storage import db as storage_db

    homepage_calls = 0

    def first_failure_then_none(_url: str, **_kwargs: Any) -> SimpleNamespace:
        nonlocal homepage_calls
        homepage_calls += 1
        if homepage_calls == 1:
            return SimpleNamespace(
                state="failed",
                fragments=[],
                detail="홈페이지 수집 시간 초과",
                candidate_scope_complete=False,
            )
        return SimpleNamespace(
            state="none",
            fragments=[],
            detail="공식 홈페이지 주소 없음",
            candidate_scope_complete=True,
        )

    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        first_failure_then_none,
    )
    first = _run()
    calls_after_first = engine.generate_ai_calls

    with storage_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM layer1_cache").fetchone()["n"]

    second = _run()

    assert first.outcome is Outcome.REPORT      # 보고서 자체는 나간다
    assert first.generation_cache_eligible is False
    assert count == 0, "우리 쪽 실패가 낀 결과를 캐시에 저장했습니다"
    assert engine.generate_ai_calls > calls_after_first  # 다시 시도하면 새로 만든다
    assert second.message == ""


def test_조건부_기본장_누락_부분본은_고정하지_않고_다음번에_다시_조사한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5·8장 누락은 선택 변동일 수 있어 다음 회계연도까지 고정하지 않는다."""

    from src.features.storage import db as storage_db

    original_finalize = real.finalize_report

    def finalize_without_optional(*args: Any, **kwargs: Any):
        report = original_finalize(*args, **kwargs)
        return replace(
            report,
            grade=Grade.PARTIAL,
            sections=[
                section
                for section in report.sections
                if section.cell not in {"current_challenges", "culture"}
            ],
        )

    monkeypatch.setattr(real, "finalize_report", finalize_without_optional)

    first = _run()
    calls_after_first = engine.generate_ai_calls
    with storage_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM layer1_cache").fetchone()["n"]
    second = _run()

    assert first.outcome is Outcome.REPORT
    assert first.generation_cache_eligible is False
    assert second.outcome is Outcome.REPORT
    assert count == 0
    assert engine.generate_ai_calls > calls_after_first


def test_핵심내용_결손_부분본도_고정하지_않고_다음번에_다시_조사한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.features.storage import db as storage_db

    original_finalize = real.finalize_report

    def finalize_with_content_shortfall(*args: Any, **kwargs: Any):
        report = original_finalize(*args, **kwargs)
        return replace(
            report,
            grade=Grade.PARTIAL,
            shortfall_reasons=[real.CUSTOMER_MARKET_SHORTFALL_REASON],
        )

    monkeypatch.setattr(real, "finalize_report", finalize_with_content_shortfall)

    first = _run()
    calls_after_first = engine.generate_ai_calls
    with storage_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM layer1_cache").fetchone()["n"]
    second = _run()

    assert first.outcome is Outcome.REPORT
    assert first.generation_cache_eligible is False
    assert second.outcome is Outcome.REPORT
    assert count == 0
    assert engine.generate_ai_calls > calls_after_first


def test_비교장만_누락된_부분본은_기본보고서로_캐시한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9장은 후단 비교 조건이므로 5·8장이 있으면 기본 보고서를 재사용한다."""

    monkeypatch.setattr(
        real,
        "_attach_competitive_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            real.ComparisonBlockedError(("동일 조건 비교 불가",))
        ),
    )

    first = _run()
    calls_after_first = engine.generate_ai_calls
    second = _run()

    assert first.outcome is Outcome.REPORT
    assert first.generation_cache_eligible is True
    assert first.report is not None
    assert first.report.grade is Grade.PARTIAL
    assert second.outcome is Outcome.REPORT
    assert engine.generate_ai_calls == calls_after_first
    assert "이미 조사해 둔" in second.message


def test_자료가_없는_것은_실패가_아니므로_캐시한다(engine: FakeEngine) -> None:
    """❌ 없음(회사의 사실)과 ⚠️ 못 가져옴(우리 실패)을 섞으면 캐시가 영영 안 찬다."""
    first = _run()
    assert first.generation_cache_eligible is True
    calls_after_first = engine.generate_ai_calls
    _run()
    assert engine.generate_ai_calls == calls_after_first


def test_사용하지_않는_뉴스검색장애는_공식보고서와_캐시에_영향주지_않는다(
    engine: FakeEngine,
) -> None:
    engine.news_fails = True

    first = _run()
    calls_after_first = engine.generate_ai_calls
    second = _run()

    assert first.outcome is Outcome.REPORT
    assert second.outcome is Outcome.REPORT
    assert engine.generate_ai_calls == calls_after_first
    news = next(source for source in first.sources if source.name == "뉴스")
    assert news.state == "none"
    assert "사용하지 않습니다" in news.detail


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


def test_paid_불변캐시경로는_생성기신원없는_옛layer1을_명시적miss로_본다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """옛 Report를 현재 배포·모델 결과라고 거짓 승격하지 않는다."""

    first = _run()
    assert first.outcome is Outcome.REPORT
    calls_after_first = engine.generate_ai_calls
    assert _run().cache_hit, "옛 layer1 대조군이 먼저 적중해야 합니다"

    monkeypatch.setattr(real.generation_coordination, "is_active", lambda: True)
    paid_result = _run()

    assert not paid_result.cache_hit
    assert engine.generate_ai_calls > calls_after_first


# ══════════════════════════════════════════════════════════
# ★ v2를 켜면 1층 캐시를 읽지 않는다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 (실측 사고) — 1층 캐시 조회가 v2 분기«보다 앞»에 있어서,
#   ENGINE_V2=1을 켜도 그 회사의 v1 저장본이 살아 있으면 v1 보고서가 그대로
#   반환됐다. 실제 로컬 DB에 8개 회사(진영·하이브·카카오 등)가 유효한 상태로
#   남아 있어서, 그 회사들로 시험하면 v2 수정이 하나도 반영 안 된 것처럼 보였다.
#   화면에는 「이전에 조사한 결과입니다」만 뜨므로 원인을 알아채기도 어렵다.
#   v2는 캐시에 «저장»도 하지 않으므로 적중분은 반드시 옛 v1 보고서다.

_V2_도달_표식 = "이 응답은 v2 경로에서 나왔다 (시험 전용 표식)"


def test_v2를_켜면_1층_캐시_적중을_무시하고_v2로_간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2를 켠 요청에 옛 v1 보고서를 돌려주는 것은 조용한 거짓말이다."""
    first = _run()
    assert first.outcome is Outcome.REPORT
    assert not first.cache_hit

    # 대조군 — v1에서는 캐시가 «먹어야» 한다. 안 먹으면 이 시험이 헛돈 것이다.
    assert _run().cache_hit, "v1 캐시가 애초에 안 먹었습니다(시험 전제가 깨졌습니다)"

    조회된_인자: list[dict] = []

    def 기록하는_조회(**kwargs):
        조회된_인자.append(kwargs)
        return _company_cache_lookup_원본(**kwargs)

    _company_cache_lookup_원본 = real._company_cache_lookup
    monkeypatch.setattr(real, "_company_cache_lookup", 기록하는_조회)
    monkeypatch.setattr(
        real,
        "_run_v2_composer",
        lambda **kwargs: RunResult(outcome=Outcome.REPORT, message=_V2_도달_표식),
    )
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    real.engine_mode._reset_process_engine_mode_for_tests()

    v2결과 = _run()

    assert 조회된_인자 == [], (
        "v2를 켰는데 1층 캐시를 읽었습니다 — 옛 v1 보고서가 그대로 나갈 수 있습니다"
    )
    assert not v2결과.cache_hit
    assert v2결과.message == _V2_도달_표식, "v2 분기까지 도달하지 못했습니다"


def test_v2를_꺼도_v1캐시는_같은배포에서만_재사용한다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1 롤백은 살리되 다른 배포의 결과를 현재 결과로 속이지 않는다."""
    first = _run()
    assert first.outcome is Outcome.REPORT
    호출수 = engine.generate_ai_calls

    second = _run()

    assert second.cache_hit
    assert engine.generate_ai_calls == 호출수, "v1 캐시가 생성 AI를 못 막았습니다"

    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    third = _run()

    # 프로세스가 살아 있는 동안 raw 환경만 바뀌는 것은 새 배포가 아니다.
    # 요청마다 다시 읽으면 한 보고서 안에서 A/B가 찢어지는 TOCTOU가 생긴다.
    assert third.cache_hit
    assert engine.generate_ai_calls == 호출수


def test_프로세스가_재시작되어_B로_동결되면_A의_v1캐시를_재사용하지않는다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _run()
    assert first.outcome is Outcome.REPORT
    호출수 = engine.generate_ai_calls
    assert _run().cache_hit

    # 실제 새 process를 단위시험에서 흉내 낸다. production 요청에서는 이 reset을
    # 호출할 수 없고, process bootstrap만 새 환경 B를 한 번 읽는다.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    build_identity_contract._reset_process_engine_build_identity_for_tests()

    after_restart = _run()

    assert not after_restart.cache_hit
    assert engine.generate_ai_calls > 호출수, "새 배포가 A의 v1 캐시를 재사용했습니다"

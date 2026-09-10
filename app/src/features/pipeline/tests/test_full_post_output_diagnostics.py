"""FULL 생성 뒤 최종 근거 결속 오류가 안전한 내부 사유로 닫히는지 검증한다."""

from __future__ import annotations

import json
import re
from typing import Final

import pytest

import src.features.composer.pipeline as composer_pipeline
import src.features.pipeline.tests.test_full_evidence_end_to_end as full_evidence_e2e
import src.shared.report_generation.canonical as generation_canonical
from src.core import deployment_identity
from src.features.budget import provider_budget
from src.features.company_comparison.tests.test_logic import _v2_comparison_result
from src.features.composer.constants import (
    SECTION_IDS,
    STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_PLAN,
    FUTURE_QUOTE_KEY,
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
)
from src.features.composer.grounding_constants import GROUNDING_KEY
from src.features.pipeline import real
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_full_evidence_end_to_end import (
    _BUSINESS_DATE,
    _COMPANY_ID,
    _FILING,
    _FILING_TEXT,
    _REVIEW_ITEM_RE,
    _ExactBundledReviewer,
    _ExactPacketWriter,
    _official_evidence,
    _section_sentences,
)
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.revenuemix.logic import build as build_revenue_mix
from src.shared import engine_build_identity as build_identity_contract
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


# 6장(성장 전략)은 «앞으로 할 일»을 쓰는 자리다. 진행·완료 표현만 있는 본문은
# `future_section_prose_problem` 이 장 범위 위반으로 제외하고, 그러면 이 시험이
# 보려던 «출력 뒤 manifest 결속 오류»에 닿기 전에 보충 재작성이 먼저 돌아 다른
# 사유로 닫힌다. 그래서 원문 조각과 작가 응답이 함께 계획 문장을 쓰고, 검수
# 대역이 그 계획의 대상·활동·인용 원문을 실제 근거로 붙인다.
#: (대상, 활동, 문장). 대상과 활동은 그 문장 안에 그대로 있어야 한다.
_FUTURE_STRATEGY_PLANS: Final[tuple[tuple[str, str, str], ...]] = (
    ("해외 고객 기반", "확대", "회사는 해외 고객 기반을 확대할 계획이다."),
    ("프리미엄 제품 라인업", "강화", "회사는 프리미엄 제품 라인업을 강화할 계획이다."),
    ("물류 자동화 설비", "도입", "회사는 물류 자동화 설비를 도입할 계획이다."),
    ("디지털 마케팅 조직", "신설", "회사는 디지털 마케팅 조직을 신설할 계획이다."),
    ("해외 생산 거점", "확대", "회사는 해외 생산 거점을 확대할 계획이다."),
)
_FUTURE_STRATEGY_SENTENCES: Final[tuple[str, ...]] = tuple(
    sentence for _target, _activity, sentence in _FUTURE_STRATEGY_PLANS
)
_FUTURE_SECTION_INDEX: Final[int] = SECTION_IDS.index(STRATEGY_TABLE_SECTION_ID)
#: 검수 prompt 한 항목에서 «그 후보가 실제로 쓴 문장»이 실린 줄.
_CANDIDATE_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^\s*문장\(JSON 문자열\): (.+)$"
)
# 작가는 `SECTION_IDS` 순번으로, 공식 근거는 `REQUIRED_EVIDENCE_SECTION_IDS`
# 순번으로 같은 문장 도우미를 부른다. 두 정본이 어긋나면 원문과 작가 응답이 서로
# 다른 장을 가리키므로 조용히 지나가지 않게 여기서 먼저 깨뜨린다.
assert REQUIRED_EVIDENCE_SECTION_IDS[_FUTURE_SECTION_INDEX] == (
    STRATEGY_TABLE_SECTION_ID
)
assert len(_FUTURE_STRATEGY_PLANS) == len(_section_sentences(_FUTURE_SECTION_INDEX))


def _section_sentences_with_future_plans(section_index: int) -> tuple[str, ...]:
    """미래 장만 계획 문장으로 바꾸고 나머지 여덟 장은 공용 자료를 그대로 쓴다.

    공용 도우미 한 곳만 갈아 끼워 «원문 조각»과 «작가 응답»이 같은 문장을 쓰게
    한다. 둘을 따로 적으면 한쪽만 고쳐져 조용히 어긋난다.
    """

    if section_index == _FUTURE_SECTION_INDEX:
        return _FUTURE_STRATEGY_SENTENCES
    return _section_sentences(section_index)


def _future_plan_evidence(
    block: str,
    evidence_ids: list[str],
) -> dict[str, object]:
    """후보 순번이 아니라 «그 후보의 실제 문장»에서 계획 근거를 찾아 붙인다."""

    match = _CANDIDATE_TEXT_RE.search(block)
    if match is None or not evidence_ids:
        return {}
    text = json.loads(match.group(1))
    entries = [
        {
            FUTURE_SOURCE_KEY: evidence_ids[0],
            FUTURE_TARGET_KEY: target,
            FUTURE_ACTIVITY_KEY: activity,
            FUTURE_QUOTE_KEY: sentence,
            FUTURE_MODE_KEY: FUTURE_MODE_PLAN,
        }
        for target, activity, sentence in _FUTURE_STRATEGY_PLANS
        if sentence in text
    ]
    return {FUTURE_KEY: entries} if entries else {}


class _FuturePlanBundledReviewer(_ExactBundledReviewer):
    """공용 exact 검수 대역에 미래 장 계획 근거만 덧붙인다."""

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        matches = list(_REVIEW_ITEM_RE.finditer(prompt))
        assert matches, "bundled 검수 prompt에 판정할 문장이 없습니다"
        verdicts: list[dict[str, object]] = []
        for index, match in enumerate(matches):
            number, section_id, _kind, citations = match.groups()
            evidence_ids = re.findall(r"조각 (\d+)", citations)
            verdict: dict[str, object] = {
                "번호": int(number),
                "장": section_id,
                "근거": evidence_ids,
                "결과": "참",
            }
            if section_id == STRATEGY_TABLE_SECTION_ID:
                end = (
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(prompt)
                )
                evidence = _future_plan_evidence(
                    prompt[match.end():end], evidence_ids
                )
                if evidence:
                    verdict[GROUNDING_KEY] = evidence
            verdicts.append(verdict)
        return json.dumps({"판정": verdicts}, ensure_ascii=False)


@pytest.fixture
def _full_runtime(monkeypatch: pytest.MonkeyPatch):
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    generation_mode = real.engine_mode.freeze_process_engine_mode(
        real.engine_mode.EngineMode.V2
    )
    build_identity = build_identity_contract.freeze_process_engine_build_identity()
    yield generation_mode, build_identity
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001


@pytest.mark.parametrize("error_type", (TypeError, ValueError))
def test_FULL_생성후_manifest_결속형식오류는_자료부족이_아닌_내부계약오류다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    generation_mode, build_identity = _full_runtime
    # 공식 근거 조각을 만들기 «전»에 갈아 끼운다. 원문 조각과 작가 응답이 같은
    # 계획 문장을 쓰게 하는 유일한 자리다.
    monkeypatch.setattr(
        full_evidence_e2e,
        "_section_sentences",
        _section_sentences_with_future_plans,
    )
    fake_engine = FakeEngine()
    financials, _years = fake_engine.fetch_financials(
        _COMPANY_ID,
        object(),
        business_date=_BUSINESS_DATE,
    )
    revenue_fragments, revenue_tables = real._bind_revenue_table_evidence_fragments(
        {},
        build_revenue_mix(_FILING_TEXT),
        filing=_FILING,
        filing_text=_FILING_TEXT,
    )
    fragments, added = merge_official_evidence_fragments(
        revenue_fragments,
        _official_evidence(),
    )
    assert added == 9
    financial_fragment = next(
        dict(fragment)
        for fragment in fake_engine.make_fragments("", financials).values()
        if fragment.get("종류") == "재무"
        and str(fragment.get("원문") or "").startswith("주요계정(DART API):")
    )
    fragments[max(fragments) + 1] = financial_fragment
    writer = _ExactPacketWriter()
    reviewer = _FuturePlanBundledReviewer()

    def fake_ask_factory(
        _engine, _client, *, stage: str, max_tokens: int, reserved_calls: int = 0,
    ):
        assert max_tokens > 0
        if stage == "v2_compose":
            return writer
        if stage == "v2_review":
            return reviewer

        def forbidden_diagram(_prompt: str) -> str:
            raise AssertionError("FULL 구성 도식은 별도 AI를 부르면 안 됩니다")

        return forbidden_diagram

    def fail_post_output_binding(*_args, **_kwargs) -> None:
        raise error_type("시험 원문·내부 예외문은 최종 사유에 실리면 안 됩니다")

    monkeypatch.setattr(real, "_v2_ask_via_provider", fake_ask_factory)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)
    # composer는 모듈 import 때 잡은 원래 검증 함수를 써 정상 출력을 만든다.
    # real.py가 출력 뒤 지연 import하는 마지막 결속 검사만 여기서 깨뜨린다.
    monkeypatch.setattr(
        generation_canonical,
        "assert_report_matches_generation_evidence",
        fail_post_output_binding,
    )
    assert (
        composer_pipeline.assert_report_matches_generation_evidence
        is not fail_post_output_binding
    )
    steps: list[dict[str, object]] = []

    with provider_budget.activate(100_000.0):
        result = real._run_v2_composer(
            engine=real._MeteredEngine(fake_engine),
            client=object(),
            company_name="가나다회사",
            corp_type="상장사",
            frags=fragments,
            financials=financials,
            filing=_FILING,
            revenue_tables=revenue_tables,
            sources=[],
            business_date=_BUSINESS_DATE,
            model="가짜모델",
            steps=steps,
            corp_id=_COMPANY_ID,
            current_fiscal_year=2025,
            source_identity_digest="a" * 64,
            build_identity=build_identity,
            generation_mode=generation_mode,
            comparison_result=_v2_comparison_result(),
        )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    assert "시험 원문" not in result.message
    # 검수·요약 진단은 실행 finally에서 단계 목록 뒤에 덧붙으므로 차단 기록이
    # 마지막 항목이라고 가정하지 않는다. 이름으로 찾아 내용만 대조한다.
    gate_steps = [step for step in steps if step.get("step") == "v2_출고검증_차단"]
    assert gate_steps == [{
        "step": "v2_출고검증_차단",
        "사유": ["FULL 생성 생산 증거와 최종 보고서 결속이 깨졌습니다"],
    }]
    assert "시험 원문" not in str(steps)
    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1

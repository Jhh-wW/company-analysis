"""보고서의 회사 고유번호는 릴리스 모드와 무관하게 실린다.

★ 왜 이 시험이 필요한가:
  초대 링크에 보고서를 다시 묶을 때, 서버는 「이 링크가 지금 가리키는 회사」의
  고유번호를 **이미 묶여 있는 보고서**에서 읽는다
  (`web/routers/admin.py`의 `_link_company_id`). 그 값이 비어 있으면 회사 일치
  검증에 이름 비교만 남아, 이름이 같고 고유번호가 다른 회사의 보고서가 그대로
  묶인다(재현: 동명·다른 corp_id 보고서가 303으로 통과).

★ 값을 지우던 곳은 «세 군데»였다:
  같은 `release_mode is FULL` 조건이 `pipeline/real.py`의 run_v2 호출 한 곳과
  `composer/pipeline.py`의 렌더 호출 두 곳(중간 렌더·최종 렌더)에 있었다.
  저장되는 값을 정하는 것은 그중 **최종 렌더**다. 그래서 한 겹만 고치면
  최종 company_id는 여전히 빈 값이다 — 아래 ①과 ③을 «따로» 두는 이유가
  이것이다.

★ 여기서 지키는 것:
  ① SHADOW·ENFORCE_NO_PARTIAL 산출물의 최종 `Report.company_id`가 확인된
     corp_id다. 저장되는 값 자체를 본다 (그 구멍이 닫혔다는 증거).
  ② FULL 산출물의 company_id는 예전 그대로 corp_id다 (회귀 방지 대조군).
  ③ `real.py`의 v2 연결부가 릴리스 모드와 무관하게 `run_v2`에 corp_id를
     넘긴다. ①이 빨간불일 때 «어느 겹이 지웠는지»를 이 시험이 갈라 준다.

★ 진짜 엔진·AI·네트워크는 부르지 않는다 — `test_real_cache`의 FakeEngine과
  `test_real_v2_switch`가 쓰는 것과 같은 가짜 작가·검수 클로저만 쓴다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.pipeline import real
from src.features.pipeline.port import Grade, Outcome, Report
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode

_DATE = dt.date(2026, 8, 24)

# 9개 장에 하나씩 붙일 표식 글자 — 장마다 다른 문장이 나오게 한다.
_SECTION_MARKS = "가나다라마바사아자"
_SENTENCE_ENDINGS = ("첫째", "둘째", "셋째", "넷째", "다섯째")

#: 가짜 회사 목록이 쓰는 것과 같은 gen8 고유번호. FULL 경로는 이 값으로
#: section packet을 만들므로 8자리가 아니면 입력 계약에서 먼저 걸린다.
_EXPECTED_CORP_ID = CORP_ID


@pytest.fixture(autouse=True)
def _검증된_배포에서_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """배포 신원이 확정된 상태에서만 v2 경로가 열린다 — 그 전제를 만든다."""
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


@pytest.fixture(autouse=True)
def _유료_문맥에서_시험한다(monkeypatch: pytest.MonkeyPatch):
    """웹 worker와 같은 예산·시도 문맥을 연다 (가짜 provider라 돈은 안 나간다)."""
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.SHADOW.value)
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    return fake


def _build_identity() -> build_identity_contract.EngineBuildIdentity:
    return build_identity_contract.process_engine_build_identity()


def _frozen_v2_mode() -> real.engine_mode.EngineMode:
    return real.engine_mode.freeze_process_engine_mode(real.engine_mode.EngineMode.V2)


def _frags() -> dict[int, dict[str, str]]:
    """장마다 하나씩, 확인 등급을 받을 만큼 긴 공식 자료 조각 9개."""
    return {
        index: {
            "종류": "공식 IR",
            "원문": " ".join(
                f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 대응 "
                f"협력 실적 {ending} 공식 자료에서 확인했다."
                for ending in _SENTENCE_ENDINGS
            ),
            "출처": f"https://corpid.example/document/{index}",
            "문서명": f"공식 자료 {index}",
            "문서일": "2026-08-24",
        }
        for index, mark in enumerate(_SECTION_MARKS, start=1)
    }


class _가짜작가:
    """장 9개를 순서대로 써 주고, 그 뒤 호출(요약)에는 빈 결과를 준다.

    SHADOW는 본문 9회 뒤 요약을 한 번 더 요청한다. 빈 요약이어도 composer가
    본문 「확인」 문장으로 보충하므로(차단하지 않는다) 이 시험의 관심사인
    company_id 운반을 그대로 확인할 수 있다.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = len(self.prompts) - 1
        if index >= len(SECTION_IDS):
            return json.dumps({"문장들": []}, ensure_ascii=False)
        mark = _SECTION_MARKS[index]
        slots = CLAIM_SLOTS_BY_SECTION[SECTION_IDS[index]]
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": (
                            f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 "
                            f"과제 대응 협력 실적 {ending} 공식 자료에서 확인했다."
                        ),
                        "인용": [str(index + 1)],
                        "등급": GRADE_CONFIRMED,
                        "주장슬롯": slots[slot_index % len(slots)],
                    }
                    for slot_index, ending in enumerate(_SENTENCE_ENDINGS)
                ]
            },
            ensure_ascii=False,
        )


#: FULL은 장 경계를 잠근 검수 프롬프트를, 그 밖의 모드는 옛 등급 표기
#: 프롬프트를 쓴다(`composer/verify.py`의 두 프롬프트 조립부). 한쪽만 읽는
#: 가짜를 쓰면 다른 모드에서 검수 응답이 통째로 «불능»이 되어 본문이 비고,
#: 이 시험이 company_id가 아니라 요약 부족으로 빨간불이 된다.
_검수항목_장잠금 = re.compile(r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)")
_검수항목_옛표기 = re.compile(r"\[(\d+)\] \(등급: ([^,]+), 인용: ([^)]+)\)")
#: `composer/verify.py`의 REWRITE_SENTENCE_HEAD — 검수가 아니라 재작성 요청이다.
_재작성_프롬프트_표식 = "불합격 문장: "


class _가짜검수:
    """프롬프트에 실린 문장 목록을 그대로 「참」으로 돌려준다 (두 형식 모두)."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if _재작성_프롬프트_표식 in prompt:
            # 검수가 «거짓»을 낸 문장만 오는 재작성 요청이다. 이 시험은
            # 전부 «참»으로 답하므로 여기 오면 안 되지만, 오더라도 빈
            # 응답으로 닫는다(그 문장은 빠진다) — 가짜가 엉뚱한 형식을
            # 판정으로 오독해 «참»을 지어내는 것보다 안전하다.
            return ""
        장잠금 = _검수항목_장잠금.findall(prompt)
        if 장잠금:
            판정 = [
                {
                    "번호": int(number),
                    "장": section_id,
                    "근거": re.findall(r"조각 (\d+)", citations),
                    "결과": "참",
                }
                for number, section_id, _kind, citations in 장잠금
            ]
        else:
            판정 = [
                {"번호": int(number), "결과": "참"}
                for number, _grade, _citations in _검수항목_옛표기.findall(prompt)
            ]
        assert 판정, "검수 프롬프트에서 문장 목록을 읽지 못했습니다 — 가짜가 형식을 놓쳤습니다"
        return json.dumps({"판정": 판정}, ensure_ascii=False)


def _가짜_ask를_끼운다(monkeypatch: pytest.MonkeyPatch):
    """작가·검수·도식 ask를 전부 가짜로 바꾼다 — 진짜 AI 호출 경로가 없다."""
    writer = _가짜작가()
    reviewer = _가짜검수()

    def fake_ask_factory(_engine, _client, *, stage: str, max_tokens: int):
        assert max_tokens > 0
        if stage == "v2_compose":
            return writer
        if stage == "v2_review":
            return reviewer

        def diagram(_prompt: str) -> str:
            return json.dumps({}, ensure_ascii=False)

        return diagram

    monkeypatch.setattr(real, "_v2_ask_via_provider", fake_ask_factory)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)
    return writer, reviewer


def _보고서를_만든다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    release_mode: ReleaseMode,
) -> Report:
    """주어진 릴리스 모드로 v2 분기를 끝까지 돌려 산출 보고서를 돌려준다."""
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, release_mode.value)
    writer, reviewer = _가짜_ask를_끼운다(monkeypatch)
    steps: list[dict[str, Any]] = []
    result = real._run_v2_composer(
        engine=real._MeteredEngine(engine),
        client=object(),
        company_name="가나다전자",
        corp_type="상장사",
        frags=_frags(),
        financials=None,
        filing=None,
        revenue_tables=[],
        sources=[],
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
        corp_id=_EXPECTED_CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="a" * 64,
        build_identity=_build_identity(),
        generation_mode=_frozen_v2_mode(),
    )
    assert result.outcome is Outcome.REPORT, (
        f"{release_mode.value} 모드에서 보고서가 나오지 않았습니다: {steps} "
        f"(작가 호출 {len(writer.prompts)}회 · 검수 호출 {len(reviewer.prompts)}회)"
    )
    assert result.report is not None
    return result.report


# ══════════════════════════════════════════════════════════
# ① 비FULL 산출물도 고유번호를 싣는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "release_mode",
    [ReleaseMode.SHADOW, ReleaseMode.ENFORCE_NO_PARTIAL],
    ids=["shadow", "enforce_no_partial"],
)
def test_비FULL_산출물도_company_id에_확인된_corp_id를_싣는다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    release_mode: ReleaseMode,
) -> None:
    """고유번호를 이미 확인했는데도 릴리스 모드 때문에 버리면 안 된다.

    이 값이 비면 링크 재결속이 이름 비교만 남아 동명·다른 회사를 못 가른다.
    회사 신원은 릴리스 정책과 무관한 사실이다. 여기서 보는 것은
    «저장되는 최종 보고서»라 세 겹을 다 고쳐야 초록이 된다.
    """
    report = _보고서를_만든다(engine, monkeypatch, release_mode=release_mode)

    # SHADOW는 옛 저장본과 바이트를 맞추려고 release_mode 표기를 비워 둔다
    # (`composer/pipeline.py`의 렌더 호출). 이 시험의 관심사가 아니므로
    # 그 기존 동작을 그대로 못 박고 지나간다.
    assert report.release_mode == (
        "" if release_mode is ReleaseMode.SHADOW else release_mode.value
    )
    assert report.company_id == _EXPECTED_CORP_ID, (
        f"{release_mode.value} 산출물이 확인된 고유번호를 버렸습니다 "
        f"(실제: {report.company_id!r})"
    )


# ══════════════════════════════════════════════════════════
# ③ real.py 연결부만 따로 — 겹을 가르는 진단용
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "release_mode",
    [ReleaseMode.SHADOW, ReleaseMode.ENFORCE_NO_PARTIAL, ReleaseMode.FULL],
    ids=["shadow", "enforce_no_partial", "full"],
)
def test_real_연결부는_모드와_무관하게_run_v2에_corp_id를_넘긴다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    release_mode: ReleaseMode,
) -> None:
    """아래 계층이 무엇을 하든, real.py가 회사 신원을 지우지 않는 것만 본다.

    ★ 이 시험이 보는 것은 «real.py가 composer에 넘긴 값» 하나다. 최종
      산출물은 ① 시험이 본다. 둘을 갈라 두는 이유는 값을 지우던 곳이 세
      군데였기 때문이다 — ①이 빨간불인데 이 시험이 초록이면 지운 곳은
      real.py가 아니라 composer 계층이다. 진단이 시험 이름만으로 끝난다.
    """
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, release_mode.value)
    _가짜_ask를_끼운다(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_run_v2(company_name, fragments, performance_table, **kwargs):
        captured.update(kwargs)
        # real.py 자신의 transport 자체검사에 걸려 GATE_STOPPED로 끝나도 좋다 —
        # 이 시험이 보는 것은 «넘긴 값» 하나뿐이다.
        return composer_pipeline.V2RunOutput(
            report=Report(
                company=company_name,
                job="",
                corp_type="상장사",
                grade=Grade.COMPLETE,
                sections=[],
                citations=[],
            ),
            composed_sentences=0,
            verified_sentences=0,
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", fake_run_v2)

    real._run_v2_composer(
        engine=real._MeteredEngine(engine),
        client=object(),
        company_name="가나다전자",
        corp_type="상장사",
        frags=_frags(),
        financials=None,
        filing=None,
        revenue_tables=[],
        sources=[],
        business_date=_DATE,
        model="가짜모델",
        steps=[],
        corp_id=_EXPECTED_CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="a" * 64,
        build_identity=_build_identity(),
        generation_mode=_frozen_v2_mode(),
    )

    assert captured.get("company_id") == _EXPECTED_CORP_ID, (
        f"{release_mode.value}에서 real.py가 회사 고유번호를 지웠습니다 "
        f"(실제: {captured.get('company_id')!r})"
    )


# ══════════════════════════════════════════════════════════
# ② 대조군 — FULL 산출물은 예전 그대로
# ══════════════════════════════════════════════════════════


def test_FULL_산출물의_company_id는_그대로다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회귀 방지 — 비FULL을 고치면서 FULL의 기존 동작을 건드리지 않았는지 본다."""
    report = _보고서를_만든다(engine, monkeypatch, release_mode=ReleaseMode.FULL)

    assert report.release_mode == ReleaseMode.FULL.value
    assert report.company_id == _EXPECTED_CORP_ID
    # FULL은 생산 증거에도 같은 회사가 결속돼 있어야 한다 (기존 계약).
    assert report.generation_evidence is not None
    assert report.generation_evidence.company_id == _EXPECTED_CORP_ID

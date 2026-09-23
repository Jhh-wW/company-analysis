"""검수 통합 — 같은 공시의 «인용 밖» 제외 각주가 현재 종속·연결 단정을 막는다(제약만).

★ 4차 실측 [27]: 특수관계자 조각만 인용한 「…를 종속기업으로 두고 있으며 … 대여」가,
  같은 공시의 「종속기업에서 제외」 각주가 검수 묶음에 와 있는데도 자기 인용 sources에
  없어서 범위 방어를 우회했다. 긍정 근거 sources는 넓히지 않고, 같은 DART 접수 신원의
  등록 각주만 제약으로 넘긴다(설계 `fourth-scope-constraint-sidecar`, 총괄 채택).

세 검수 경로(평문 최초·묶음 최초·제한 재작성 뒤 재검수)가 같은 경계를 타고, 추가 AI
호출이 없고, 프롬프트·긍정 sources가 바뀌지 않는지 본다. 법인 이름은 익명이다.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.features.composer import verify
from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE,
    LEGACY_KIND_RELATED_PARTY,
)

DOCUMENT = "document:dart.fss.or.kr:20260414000008"
OTHER_DOCUMENT = "document:dart.fss.or.kr:20250414000003"
SECTION = "operations_partners"
RELATED = "회사는 가람 Holdings에 운영자금 40,000천원을 대여하였습니다."
FOOTNOTE = (
    "5. 매도가능증권 당기말 현재 매도가능증권의 내역은 다음과 같습니다. "
    "(단위 : 천원) 구분 지분율 취득원가 장부금액 가람 Holdings(*) 100% 40,000 "
    "40,000 나래 Studio 30% 12,000 12,000 (*) 일반기업회계기준 경과규정에 따라 "
    "종속기업에서 제외되었습니다."
)
CURRENT_CLAIM = "회사는 가람 Holdings를 종속기업으로 두고 있으며 운영자금을 대여하였다."
#: 제약이 막으면 안 되는 후보 — 정상 거래, 제외 조건 설명, 과거 관계 서술.
KEPT_CLAIMS = (
    "회사는 가람 Holdings에 운영자금을 대여하였다.",
    "회사는 종속기업 가람 Holdings에 운영자금을 대여하였다.",
    "회사는 가람 Holdings를 경과규정에 따라 종속기업에서 제외하고 취득원가로 계상하고 있다.",
    "가람 Holdings는 과거 종속기업이었으나 경과규정에 따라 종속기업에서 제외되었다.",
)


def _fragments(*, footnote=FOOTNOTE, related_identity=DOCUMENT, footnote_identity=DOCUMENT):
    return {
        "1": CollectedFragment("1", LEGACY_KIND_RELATED_PARTY, RELATED, document_identity=related_identity),
        "2": CollectedFragment("2", LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, footnote,
                               document_identity=footnote_identity),
    }


def _sentence(text):
    return ComposedSentence(text, ("1",), "확인")


def _true_reviewer(calls, *, grouped=False):
    def ask(prompt):
        calls.append(str(prompt))
        row = {"번호": 1, "근거대조": "원문 대조", "결과": "참"}
        if grouped:
            row.update({"장": SECTION, "근거": ["1"]})
        return json.dumps({"판정": [row]}, ensure_ascii=False)
    return ask


@pytest.fixture
def captured(monkeypatch):
    """실제 constrain_verdicts 를 그대로 부르되, 받은 긍정 sources·제약을 기록한다."""
    seen = []
    original = verify.constrain_verdicts

    def spy(raw, verdicts, candidates, **kwargs):
        seen.append((dict(candidates), kwargs.get("entity_scope_by_number")))
        return original(raw, verdicts, candidates, **kwargs)

    monkeypatch.setattr(verify, "constrain_verdicts", spy)
    return seen


def _flat(text, fragments, calls):
    problems: dict[int, str] = {}
    verdicts = verify._ask_verdicts(
        _true_reviewer(calls), (verify._ReviewItem(1, _sentence(text), SECTION),),
        fragments, "", grounding_problems=problems,
    )
    return verdicts, problems


def _grouped(text, fragments, calls):
    problems: dict[int, str] = {}
    item = verify._GroupedReviewItem(1, SECTION, "문장", ("1",), sentence=_sentence(text))
    verdicts = verify._ask_grouped_verdicts(
        _true_reviewer(calls, grouped=True), (item,), fragments, None, grounding_problems=problems,
    )
    return verdicts, problems


@pytest.mark.parametrize("review", [_flat, _grouped], ids=["flat", "grouped"])
def test_uncited_same_document_footnote_blocks_current_relation_without_touching_sources(review, captured):
    calls: list[str] = []
    verdicts, problems = review(CURRENT_CLAIM, _fragments(), calls)
    assert verdicts == {1: REVIEW_GROUNDING_REJECTED}
    assert problems == {1: SCOPE_CONDITION_UNBOUND}
    assert len(calls) == 1  # 추가 AI 호출 없음
    assert FOOTNOTE not in calls[0]  # 검수 프롬프트에 제약 원문을 싣지 않는다
    (candidates, entity_scope), = captured
    assert candidates[1][1] == {"1": RELATED}  # 긍정 근거는 자기 인용 그대로
    (context,) = entity_scope[1]
    assert context.document_identity == DOCUMENT
    assert dict(context.constraint_sources) == {"2": FOOTNOTE}


@pytest.mark.parametrize("review", [_flat, _grouped], ids=["flat", "grouped"])
@pytest.mark.parametrize("fragments", [
    _fragments(footnote_identity=OTHER_DOCUMENT),                          # 다른 문서
    _fragments(related_identity="", footnote_identity=""),                 # 빈 신원 — 대상 밖
    _fragments(footnote=FOOTNOTE.replace("가람 Holdings(*)", "나래 Holdings(*)")),  # 다른 법인
    _fragments(footnote=FOOTNOTE.replace("당기말 현재", "전기말 현재")),    # 과거(전기) 각주
], ids=["other_document", "empty_identity", "other_entity", "prior_period"])
def test_constraint_is_not_borrowed_across_documents_entities_or_periods(review, fragments, captured):
    verdicts, problems = review(CURRENT_CLAIM, fragments, [])
    assert verdicts == {1: "참"} and problems == {}


@pytest.mark.parametrize("review", [_flat, _grouped], ids=["flat", "grouped"])
@pytest.mark.parametrize("text", KEPT_CLAIMS)
def test_transactions_exclusion_descriptions_and_history_survive(review, text, captured):
    verdicts, problems = review(text, _fragments(), [])
    assert verdicts == {1: "참"} and problems == {}


def test_without_the_constraint_the_same_candidate_passes():
    """대조군 — 각주 조각이 없으면(현재 우회 경로) 같은 후보가 통과한다."""
    fragments = {"1": _fragments()["1"]}
    verdicts, problems = _flat(CURRENT_CLAIM, fragments, [])
    assert verdicts == {1: "참"} and problems == {}


def test_recheck_after_limited_rewrite_applies_the_same_constraint(captured):
    """제한 재작성 뒤 재검수(`_recheck_rewritten` → `_ask_verdicts`)도 같은 제약을 쓴다."""
    fragments = _fragments()
    items = (
        verify._ReviewItem(1, _sentence(CURRENT_CLAIM), SECTION),
        verify._ReviewItem(2, _sentence(KEPT_CLAIMS[0]), SECTION),
    )
    calls: list[str] = []

    def ask(prompt):
        calls.append(str(prompt))
        return json.dumps({"판정": [
            {"번호": number, "근거대조": "원문 대조", "결과": "참"} for number in (1, 2)
        ]}, ensure_ascii=False)

    final: dict[int, ComposedSentence | None] = {1: None, 2: None}
    verify._recheck_rewritten(ask, items, fragments, "", "", final)
    assert len(calls) == 1
    assert final[1] is None  # 재작성문이 여전히 현재 종속기업이라 단정 → 제거
    assert final[2] == replace(items[1].sentence, verification_state="verified")
    (candidates, entity_scope), = captured
    assert candidates[1][1] == candidates[2][1] == {"1": RELATED}
    assert set(entity_scope) == {1, 2}


def test_blocked_candidate_records_the_stage_without_source_text_and_survives_shared_filter():
    from src.features.composer.entity_scope_constraint_constants import ENTITY_SCOPE_EXCLUSION_STAGE
    from src.shared.report_quality.review_diagnostic_constants import GROUNDING_DETAIL_STAGES
    from src.shared.report_quality.review_diagnostics import observed_review_outcomes

    diagnostics: list[dict] = []
    verify._ask_verdicts(_true_reviewer([]), (verify._ReviewItem(1, _sentence(CURRENT_CLAIM), SECTION),),
                         _fragments(), "", diagnostics=diagnostics)
    (record,) = diagnostics
    assert record["reason_code"] == SCOPE_CONDITION_UNBOUND  # 공개 사유 코드는 기존 그대로
    assert record["grounding_detail"] == {
        "version": "grounding-detail-v1", "check_kind": "근거", "stage": ENTITY_SCOPE_EXCLUSION_STAGE}
    assert ENTITY_SCOPE_EXCLUSION_STAGE in GROUNDING_DETAIL_STAGES
    assert FOOTNOTE not in json.dumps(diagnostics, ensure_ascii=False)
    (observed,) = observed_review_outcomes(diagnostics)
    assert observed["grounding_detail"]["stage"] == ENTITY_SCOPE_EXCLUSION_STAGE


def test_blocked_candidate_is_a_limited_rewrite_target_with_transaction_only_guidance():
    """재작성 프롬프트는 기존 «세부 검사» 줄로 이 단계의 안내만 읽는다 — 다른 범위 탈락은 그대로."""
    from src.features.composer.entity_scope_constraint_constants import ENTITY_SCOPE_EXCLUSION_STAGE
    from src.features.composer.grounding_rewrite import (
        GroundingRewriteTarget, build_grounding_rewrite_prompt,
    )

    assert verify._is_grounding_rewrite_target(_sentence(CURRENT_CLAIM), SECTION, SCOPE_CONDITION_UNBOUND)
    fragments = _fragments()
    entity = GroundingRewriteTarget(1, CURRENT_CLAIM, ("1",), SCOPE_CONDITION_UNBOUND,
                                    {"stage": ENTITY_SCOPE_EXCLUSION_STAGE})
    other_scope = replace(entity, number=2, detail={})
    prompt, loaded = build_grounding_rewrite_prompt((entity, other_scope), fragments)
    assert len(loaded) == 2
    entity_block, other_block = prompt.split("번호 1 ·", 1)[1].split("번호 2 ·", 1)
    assert "거래 사실만 남기십시오" in entity_block
    assert "인용 원문에도 그 설명이 있을 때만" in entity_block
    assert "거래 사실만 남기십시오" not in other_block
    assert FOOTNOTE not in prompt  # 재작성도 자기 인용 원문만 싣는다

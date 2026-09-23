"""사건 키·메타가 같아도 원문 행동 전체가 완료되지 않으면 계획을 보존한다."""

from dataclasses import replace
from hashlib import sha256

import pytest

from src.features.composer.news_supersession import news_plan_is_explicitly_completed
from src.features.composer.port import CollectedFragment
from src.shared.report_evidence.transport_kind import TYPED_TRANSPORT_KIND_PREFIX


PLAN = "가나다전자는 이달 중 청소년 보호 정책의 세부 내용을 공개할 예정이라고 밝혔다."
DONE = "가나다전자는 청소년 보호 정책의 세부 내용을 공개했다. 고객이 열람할 수 있는 정책 안내서도 게시했다."


def _news(text, *, planned):
    number = "1" if planned else "2"
    return CollectedFragment(
        number, "news", text, source_url=f"https://fixtures.invalid/news/{number}",
        document_title="정책 안내 보도", document_date="2026-09-02" if planned else "2026-09-08",
        document_identity=f"url:https://fixtures.invalid/news/{number}",
        document_content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        formal_source_kind="news", source_publisher="시험신문",
        news_claim_kind="company_plan" if planned else "reported_fact",
        news_temporal_status="planned" if planned else "completed",
        news_event_key="정책 세부 내용 공개", news_source_category="official_release",
        news_grounded=True,
    )


def _matches(plan=PLAN, done=DONE):
    return news_plan_is_explicitly_completed(_news(plan, planned=True), _news(done, planned=False))


@pytest.mark.parametrize("action", ("발표", "공개"))
@pytest.mark.parametrize("plan_ending", ("할 예정이다", "할 계획이다", "할 예정이라고 밝혔다"))
@pytest.mark.parametrize("done_ending", ("했다", "하였다", "했습니다", "하였습니다"))
def test_같은행동의_좁은_시제변화만_명시완료로_인정한다(action, plan_ending, done_ending):
    subject = "가나다전자는 청소년 보호 정책의 세부 내용을 "
    assert _matches(subject + action + plan_ending + ".", subject + action + done_ending + ".")


def test_국소적인_예고시점과_추가완료문장이_있어도_같은행동은_대체된다():
    assert _matches()


@pytest.mark.parametrize("plan,done", (
    ("가나다전자는 정책을 내년 초부터 적용할 계획이라고 밝혔다. 보호자 동의를 얻은 경우에는 별도 인증을 거치도록 할 예정이다.", DONE),
    (PLAN, DONE.replace("가나다전자", "라마바전자")),
    (PLAN.replace("청소년", "성인"), DONE),
    (PLAN.replace("세부 내용", "2개 세부 항목"), DONE.replace("세부 내용", "3개 세부 항목")),
    (PLAN.replace("세부 내용", "2.5단계 세부 항목"), DONE.replace("세부 내용", "3.5단계 세부 항목")),
    (PLAN.replace("이달 중", "보호자 동의를 얻은 경우에만"), DONE),
    (PLAN.replace("이달 중", "내년 초부터"), DONE),
    (PLAN.replace("공개", "발표"), DONE),
    (PLAN.replace("이달 중", "자료를 준비하고 있으며 이달 중"), DONE),
    (PLAN + " 보호자 동의를 얻은 경우에는 별도 인증을 거치도록 할 예정이다.", DONE),
    (PLAN, DONE + " 다만 실제로 공개하지 않은 것으로 확인됐다."),
    (PLAN, DONE + " 회사는 해당 발표를 철회했다."),
    (PLAN, DONE + " 공개 여부는 확인되지 않았다."),
    (PLAN, DONE + " 회사는 공개 여부를 알 수 없다고 설명했다."),
    (PLAN, DONE + " 해당 공개는 사실이 아니라고 정정했다."),
    (PLAN, DONE + " 실제 적용 범위에 대해서는 별도 검토가 필요하다."),
    (PLAN, DONE + " 별도 적용은 내년부터 시작할 예정이다."),
    (PLAN.replace("이달 중", "2026년 9월 7일"), DONE),
    (PLAN.replace("세부 내용", "제품 A의 세부 내용"), DONE.replace("세부 내용", "제품 a의 세부 내용")),
))
def test_같은키로_주체_수치_조건_시점_다른행동_부정을_삭제하지_않는다(plan, done):
    assert not _matches(plan, done)


def test_조건과_수치는_양쪽_원문에서_동일하게_보존해야_한다():
    core = "가나다전자는 보호자 동의를 얻은 경우에만 정책 2개 항목을 "
    assert _matches(core + "공개할 예정이다.", core + "공개했다.")


def test_계획의_모든행동이_순서대로_각각_완료되어야_한다():
    first = "가나다전자는 정책 세부 내용을 "
    second = "가나다전자는 별도 이용자 안내서를 "
    plan = first + "발표할 예정이다. " + second + "공개할 예정이다."
    assert _matches(plan, first + "발표했다. " + second + "공개했다.")
    assert not _matches(plan, first + "발표했다.")
    assert not _matches(plan, second + "공개했다. " + first + "발표했다.")


@pytest.mark.parametrize("side", ("planned", "completed"))
@pytest.mark.parametrize("changes", (
    {"news_grounded": False},
    {"news_grounded": 1},
    {"formal_source_kind": "official_identity_verified_web_page"},
    {"kind": "회사 공식 자료"},
    {"news_source_category": "opinion"},
    {"news_event_key": "다른 사건"},
    {"news_event_key": ""},
    {"document_date": ""},
    {"document_date": "20260908"},
    {"news_event_on": "잘못된 날짜"},
    {"news_temporal_status": "ongoing"},
    {"document_identity": ""},
    {"source_publisher": ""},
))
def test_미승인_뉴스종류나_시점_메타를_대체권위로_쓰지_않는다(side, changes):
    plan, done = _news(PLAN, planned=True), _news(DONE, planned=False)
    if side == "planned":
        plan = replace(plan, **changes)
    else:
        done = replace(done, **changes)
    assert not news_plan_is_explicitly_completed(plan, done)


@pytest.mark.parametrize("changes", (
    {"document_date": "2026-09-01"},
    {"news_event_on": "2026-09-01"},
    {"news_claim_kind": "company_plan"},
))
def test_앞선_완료일이나_계획메타는_뒤따른완료가_아니다(changes):
    assert not news_plan_is_explicitly_completed(
        _news(PLAN, planned=True), replace(_news(DONE, planned=False), **changes),
    )


def test_등록된_typed_종류는_승인된_뉴스_메타와_함께_허용한다():
    planned = replace(_news(PLAN, planned=True), kind=TYPED_TRANSPORT_KIND_PREFIX + "a" * 64)
    completed = replace(_news(DONE, planned=False), kind=TYPED_TRANSPORT_KIND_PREFIX + "b" * 64)
    assert news_plan_is_explicitly_completed(planned, completed)


@pytest.mark.parametrize("side", ("planned", "completed"))
@pytest.mark.parametrize("changes", (
    {"kind": "typed-evidence-v1:" + "a" * 64},
    {"kind": "typed-evidence-v4:" + "a" * 64},
    {"kind": TYPED_TRANSPORT_KIND_PREFIX + "a" * 63},
    {"kind": TYPED_TRANSPORT_KIND_PREFIX + "A" * 64},
    {"kind": TYPED_TRANSPORT_KIND_PREFIX + "g" * 64},
    {"kind": TYPED_TRANSPORT_KIND_PREFIX + "a" * 64 + "\n"},
    {"kind": "임의 종류"},
    {"formal_source_kind": "official_identity_verified_web_page"},
    {"news_grounded": False},
    {"news_grounded": 1},
    {"news_claim_kind": "임의 주장"},
    {"news_temporal_status": "ongoing"},
    {"news_source_category": "opinion"},
    {"document_date": "잘못된 날짜"},
    {"document_content_sha256": "임의 지문"},
))
def test_typed_접두만으로_위조종류나_미승인메타를_허용하지_않는다(side, changes):
    planned = replace(_news(PLAN, planned=True), kind=TYPED_TRANSPORT_KIND_PREFIX + "a" * 64)
    completed = replace(_news(DONE, planned=False), kind=TYPED_TRANSPORT_KIND_PREFIX + "b" * 64)
    if side == "planned":
        planned = replace(planned, **changes)
    else:
        completed = replace(completed, **changes)
    assert not news_plan_is_explicitly_completed(planned, completed)

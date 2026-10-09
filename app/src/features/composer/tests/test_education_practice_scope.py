"""실습 프롬프트와 실제 회사 실행의 최소 경계."""

import pytest

from src.features.composer.education_practice_scope import education_practice_scope_problem

SLOT = "past_changes:completed_execution"
RULES = "# Project Rules - 확인되지 않은 내용을 사실처럼 단정하지 않는다. - 데이터로 확인된 사실과 AI가 만든 가설을 구분한다."
PROMPT = "현재 온보딩 플로우를 기준으로 두 가지 안을 구현한다. A안: 단계형 진행 방식 B안: 필요한 정보를 순차적으로 보여주는 방식 조건: - 모바일 375px을 기준으로 한다."


def check(claim, sources, slot=SLOT, section="past_changes"):
    return education_practice_scope_problem(claim, sources, section_id=section, claim_slot=slot)


@pytest.mark.parametrize("source,claim", [
    (RULES, "회사는 데이터로 확인된 사실과 AI가 만든 가설을 구분하는 절차를 마련했다."),
    (PROMPT, "회사는 모바일 375px 기준 온보딩 두 안을 구현했다."),
    ("요청 예시: 온보딩 완료율을 개선하는 두 안을 구현한다.", "회사는 온보딩 개선안을 구현했다."),
])
def test_명시_예시_지시문은_회사_완료_실행을_지원하지_않는다(source, claim):
    assert check(claim, {"a": source}) == "source_practice_unbound"


@pytest.mark.parametrize("claim", [
    "예시교육은 보안 중견기업과 공공기관을 대상으로 PoC를 진행하고 있다고 밝혔다.",
    "회사는 AI 도구를 도입하여 온보딩 완료율을 개선했다.",
    "회사는 실제 직원 교육에서 데이터의 사실과 가설을 구분하는 업무 원칙을 시행했다.",
    "당사 직원은 화면마다 동일한 행동의 표현이 일치하는지 확인합니다.",
    "회사는 QA 에이전트를 도입해 예외 케이스를 검수했다.",
])
def test_실제_회사_도입_성과_직원원칙_및_PoC는_보존한다(claim):
    assert check(claim, {"a": claim}) == ""


@pytest.mark.parametrize("section,slot", [
    ("portfolio", "portfolio:product_role"),
    ("culture", "culture:working_style"),
    ("past_changes", "past_changes:change_context"),
    ("future_strategy", "future_strategy:stated_plan"),
])
def test_다른_지원_슬롯을_예시만으로_통째로_폐기하지_않는다(section, slot):
    assert check("교육 자료는 프로젝트 규칙의 예시를 소개한다.", {"a": RULES}, slot, section) == ""


def test_문맥없는_일반_QA_안내는_어미만으로_차단하지_않는다():
    source = "사용자가 이전 화면으로 돌아가거나 중간에 이탈할 때도 흐름이 자연스러운지 확인합니다."
    assert check("직원은 이용 흐름을 확인하는 절차를 운용한다.", {"a": source}) == ""


def test_무관한_실제회사_출처를_추가해_예시를_실행으로_세탁할_수_없다():
    sources={"a":PROMPT,"b":"당사는 보안 관제 서비스를 운영합니다."}
    assert check("회사는 모바일 375px 기준 온보딩 두 안을 구현했다.",sources)=="source_practice_unbound"


def test_혼합_원문의_명시_실제회사_행동은_독립적으로_보존한다():
    actual="당사는 보안 관제 서비스를 운영합니다."
    sources={"a":PROMPT+" "+actual}
    assert check(actual,sources)==""


def test_요약에서_원_슬롯이_없어도_예시의_실제_구현_주장을_제한한다():
    assert check("회사는 모바일 375px 기준 온보딩 두 안을 구현했다.",{"a":PROMPT},"","summary")=="source_practice_unbound"


def test_요약의_단순_교육내용_소개를_실제실행으로_취급하지_않는다():
    assert check("교육 자료는 온보딩 두 안의 프롬프트 예시를 소개한다.",{"a":PROMPT},"","summary")==""


def test_결속된_가상회사_문장을_실제회사_절로_다시_추출하지_않는다():
    import hashlib
    from src.shared.report_evidence.practice_context import build_practice_context
    source = "회사는 2024년 검수 시스템을 도입했습니다."
    ranges = ("예를 들어 가상의 회사 업무를 수행했다고 가정합니다.", source)
    raw = build_practice_context(
        ranges=ranges, document_id="fiction", document_sha256=hashlib.sha256("\n".join(ranges).encode()).hexdigest(),
        fragment_index=1, fragment_location="목록 2번째 항목",
    )
    assert education_practice_scope_problem(
        source, {"a": source}, section_id="past_changes", claim_slot=SLOT,
        practice_context_by_source_id={"a": raw},
    ) == "source_practice_unbound"

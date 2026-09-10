"""5장 대응이 «추출은 됐는데 화면에서 사라지던» 자리를 못 박는다.

실측 근거 — (주)멀티캠퍼스 유료 실행 `979d5e3f3cdd68ad4f4249c4bca3adef`.
보고서 5장의 대응표가 통째로 비었고(`report.json` 5장 `tables: []`), 사실
3건은 모두 문제 서술이었다. 그런데 단계 기록 29번을 보면 작가는 대응을
«이미 냈다»:

    [current_challenges] 경로 «교육서비스 부문 매출 3년 연속 감소 →
    AI 교육 체계 고도화, 진단 기반 리더십 교육 강화, 신성장 동력 발굴 및
    새로운 시장·고객군 확장»: 인용 원문에 없는 수 —
    「교육서비스 부문 매출 3년 연속 감소」의 수 3

즉 추출 실패도, 파서 손실도, 조각 경계 문제도 아니었다. 원문에는 과제 문장
바로 뒤에 대응 문장이 «같은 문단»에 이어져 있었고 작가는 둘 다 읽었다.
`diagram_check._drop_invented_numbers` 가 «한 칸이라도» 근거 없는 수를 담으면
줄을 통째로 버리기 때문에, 과제 칸의 「3년 연속」 하나가 근거가 멀쩡한 대응까지
같이 지운 것이다.

여기서 지키는 것:
  ① 그 손실을 그대로 재현한다 — 수를 담은 과제 칸은 지금도 줄을 버린다(완화 금지).
  ② 같은 원문·같은 대응이라도 과제 칸을 원문 표현으로 쓰면 대응이 살아남는다.
  ③ 대응 칸이 인용 원문에 없는 말이면 검수 AI가 «참»이라 해도 빠진다(새 가드).
  ④ 대응 칸이 빈 값이면 기존 `challenge_response_missing` 이 그대로 막는다.
  ⑤ 5장 프롬프트가 「바로 뒤 대응 문장」과 「원문에 없는 수 금지」를 지시한다.
"""

from __future__ import annotations

import json

import pytest

from src.features.composer.challenge_constants import (
    CHALLENGE_RESPONSE_MISSING,
    CHALLENGE_RESPONSE_NOT_IN_SOURCE,
)
from src.features.composer.challenge_response_evidence import (
    challenge_response_evidence_problem,
)
from src.features.composer.constants import (
    CHALLENGE_FLOW_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.diagram_check import (
    check_diagram_numbers,
    check_diagrams,
)
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    FlowRow,
)
from src.features.composer.prose_own_source import prose_own_source_problem
from src.features.composer.prose_own_source_constants import (
    PROSE_OWN_SOURCE_UNSUPPORTED,
)
from src.features.composer.verify import verify_report


# ══════════════════════════════════════════════════════════
# 실측 원문 — DART 20260316000476 «[기재정정]사업보고서 (2025.12)»
# 「Ⅱ. 사업의 내용 · 3. 재무상태 및 영업실적」에서 그대로 옮겼다.
# 과제 문장 «바로 뒤»에 대응 문장이 붙어 있는 것이 이 사건의 핵심이라
# 문장 사이를 손대지 않는다.
# ══════════════════════════════════════════════════════════
ACTUAL_EDUCATION_SOURCE = (
    "교육서비스는 국내 경기위축에 따른 공공기관 및 교육예산 축소 등으로 인하여 "
    "매출액은 전기 대비 35,213백만원 감소한 253,902백만원을 기록하였습니다."
    "'26년은 AI 교육 체계를 고도화하고, 빠르게 변화하는 AI 기술과 산업 수요에 "
    "대응하는 과정을 지속적으로 확충해 나가겠습니다. 또한 진단 기반 리더십 교육을 "
    "강화함으로써 안정적인 매출 기반과 차별화된 수익 모델을 확보해 나가겠습니다."
)
ACTUAL_GROWTH_SOURCE = (
    "'26년에는 기존 사업 고도화에 더해 신성장 동력을 발굴하고 새로운 시장과 "
    "고객군으로 사업 영역을 확장하며 중장기 성장 기반을 강화하겠습니다."
)

#: 실행이 실제로 낸 줄. 대응 칸은 원문 그대로인데 과제 칸에 원문에 없는 수가 있다.
RUN_ISSUE_CELL = "교육서비스 부문 매출 3년 연속 감소"
RUN_RESPONSE_CELL = (
    "AI 교육 체계 고도화, 진단 기반 리더십 교육 강화, "
    "신성장 동력 발굴 및 새로운 시장·고객군 확장"
)
#: 같은 대응을 살리는 과제 칸 — 원문 「국내 경기위축에 따른 … 교육예산 축소」.
GROUNDED_ISSUE_CELL = "국내 경기위축에 따른 교육예산 축소"


def _fragments() -> tuple[CollectedFragment, ...]:
    return (
        CollectedFragment("1", "사업내용", ACTUAL_EDUCATION_SOURCE),
        CollectedFragment("8", "사업내용", ACTUAL_GROWTH_SOURCE),
    )


def _draft(row: FlowRow, section_id: str = CHALLENGE_FLOW_SECTION_ID) -> ComposedReport:
    return ComposedReport((ComposedSection(section_id, (), flow_rows=(row,)),))


def _approving_ask(section_id: str, citations: tuple[str, ...]):
    """검수 AI가 무조건 «참»이라고 답하는 대역 — 기계 가드만 남긴다.

    ★ 근거 id를 그 줄이 실제로 단 인용 «그대로» 되돌린다. 다른 id를 답하면
      판정 계약 위반으로 줄이 빠져서, 정작 보려던 가드가 한 번도 안 돌아간다.
    """

    calls: list[str] = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(
            {"판정": [{"번호": 1, "결과": "참", "장": section_id, "근거": list(citations)}]},
            ensure_ascii=False,
        )

    return ask, calls


def _run_review(row: FlowRow, grouped: bool, section_id=CHALLENGE_FLOW_SECTION_ID):
    """flat(check_diagrams)·grouped(verify_report) 두 공개 경로를 같이 본다."""

    draft = _draft(row, section_id)
    fragments = _fragments()
    diagnostics: list[dict] = []
    ask, calls = _approving_ask(section_id, row.citations)
    if grouped:
        result = verify_report(
            draft,
            fragments,
            None,
            ask,
            allowed_fragment_ids_by_section={section_id: frozenset(row.citations)},
            diagnostics=diagnostics,
        )
    else:
        result, _problems = check_diagrams(draft, fragments, ask, diagnostics=diagnostics)
    return result.sections[0].flow_rows, diagnostics, calls


def _reason_codes(diagnostics: list[dict]) -> set[str]:
    return {str(item.get("reason_code") or "") for item in diagnostics}


# ══════════════════════════════════════════════════════════
# ① 운영에서 대응이 사라진 경로를 그대로 재현한다
# ══════════════════════════════════════════════════════════


def test_실행이_낸_줄은_과제_칸의_수_때문에_통째로_버려진다():
    """대응 칸은 근거가 멀쩡했는데도 줄이 사라졌다 — 이것이 「대응 0건」의 원인이다."""

    row = FlowRow((RUN_ISSUE_CELL, RUN_RESPONSE_CELL), ("1", "8"))

    kept, problems = check_diagram_numbers(_draft(row), _fragments())

    assert kept.sections[0].flow_rows == ()
    assert len(problems) == 1
    reason = problems[0]
    assert "인용 원문에 없는 수" in reason
    # 버려진 이유는 «과제» 칸의 3이다. 대응 칸이 근거 없다는 뜻이 아니다.
    assert f"「{RUN_ISSUE_CELL}」의 수 3" in reason
    assert RUN_RESPONSE_CELL not in reason.split("»")[1]


def test_실행이_낸_대응_칸_자체는_원문_근거를_통과한다():
    """대응을 못 뽑은 것이 아니라는 증거 — 같은 대응이 근거 검사를 통과한다."""

    sources = {"1": ACTUAL_EDUCATION_SOURCE, "8": ACTUAL_GROWTH_SOURCE}

    assert challenge_response_evidence_problem(
        (RUN_ISSUE_CELL, RUN_RESPONSE_CELL), sources
    ) == ""


# ══════════════════════════════════════════════════════════
# ② 과제 칸을 원문 표현으로 쓰면 대응이 살아남는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_원문_바로_뒤_대응_문장은_같은_줄로_공개된다(grouped):
    row = FlowRow((GROUNDED_ISSUE_CELL, "AI 교육 체계 고도화, 진단 기반 리더십 교육 강화"), ("1",))

    rows, diagnostics, calls = _run_review(row, grouped)

    assert rows == (row,), "과제·대응이 모두 원문에 있는데도 줄이 빠졌습니다"
    assert rows[0].cells[1], "대응 칸이 비었습니다"
    assert len(calls) == 1
    assert not _reason_codes(diagnostics) & {
        CHALLENGE_RESPONSE_MISSING,
        CHALLENGE_RESPONSE_NOT_IN_SOURCE,
    }


# ══════════════════════════════════════════════════════════
# ③④ 근거 없는 대응·빈 대응은 계속 빠진다 (음성 대조)
# ══════════════════════════════════════════════════════════


#: 지어낸 대응 — 이 원문과 겹치는 낱말이 없고, 다른 가드(역할·과금 결속,
#: 문화, 적용범위)에는 걸리지 않는 말을 골랐다. 그래야 이 시험이 «내 가드»를
#: 재는 것이 된다(다른 가드가 먼저 막으면 초록불이어도 아무것도 증명 못 한다).
UNGROUNDED_RESPONSE_CELL = "부산 지사를 설립한다"


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_원문에_없는_대응은_검수가_참이라도_빠진다(grouped):
    """작가가 지어낸 대응. 검수 AI는 «참»이라 답하므로 기계 가드만이 막는다."""

    row = FlowRow((GROUNDED_ISSUE_CELL, UNGROUNDED_RESPONSE_CELL), ("1",))

    rows, diagnostics, calls = _run_review(row, grouped)

    assert rows == ()
    assert len(calls) == 1
    assert CHALLENGE_RESPONSE_NOT_IN_SOURCE in _reason_codes(diagnostics)


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize("response", ("", "미확인", "해당 사항 없음"))
def test_빈_대응_행_제외_규칙은_그대로다(response, grouped):
    row = FlowRow((GROUNDED_ISSUE_CELL, response), ("1",))

    rows, diagnostics, _calls = _run_review(row, grouped)

    assert rows == ()
    codes = _reason_codes(diagnostics)
    assert CHALLENGE_RESPONSE_MISSING in codes
    # 빈 칸에 두 사유를 겹쳐 남기지 않는다 — 고칠 곳이 하나로 보여야 한다.
    assert CHALLENGE_RESPONSE_NOT_IN_SOURCE not in codes


def test_인용_원문이_없으면_판단_불가로_물러난다():
    """근거가 «비었다»와 «맞지 않는다»는 다르다. 빈 원문으로 벌하지 않는다."""

    assert challenge_response_evidence_problem(
        (GROUNDED_ISSUE_CELL, "AI 교육 체계 고도화"), {}
    ) == ""
    assert challenge_response_evidence_problem(
        (GROUNDED_ISSUE_CELL, "AI 교육 체계 고도화"), {"1": "   "}
    ) == ""


def test_인용_하나만_복원_실패해도_판단_불가로_물러난다():
    """독립 검토(P2-3) — «일부만» 빈 원문에서 산문과 잣대가 갈라져 있었다.

    복원 못 한 인용을 «0단어»로 세면, 남은 인용만으로 대응을 벌하게 된다.
    뉴스 정확 원문처럼 다른 자리에 보관된 근거가 있으므로 이때는 판정하지
    않는다 — 그러지 않으면 5장 대응표가 뉴스를 인용하는 순간 이 가드가
    살리려던 그 「대응이 사라진다」가 다시 일어난다.
    """

    assert challenge_response_evidence_problem(
        (GROUNDED_ISSUE_CELL, UNGROUNDED_RESPONSE_CELL),
        {"1": ACTUAL_EDUCATION_SOURCE, "8": ""},
    ) == ""


#: 인용 복원 상태 × (산문 경로, 대응표 경로). 두 경로가 «같은 자리»에서
#: 물러나는지 한 시험에서 함께 확인한다 — 조건을 한쪽에만 적어 두면 한쪽만
#: 고쳐져 잣대가 다시 갈라진다(모듈 머리말이 약속한 계약).
UNRECOVERABLE_MATRIX = (
    ("전부 복원 실패", {"1": "", "8": "   "}, False),
    ("일부만 복원 실패", {"1": ACTUAL_EDUCATION_SOURCE, "8": ""}, False),
    ("전부 복원 성공", {"1": ACTUAL_EDUCATION_SOURCE, "8": ACTUAL_GROWTH_SOURCE}, True),
)


@pytest.mark.parametrize(
    "name,sources,should_judge",
    UNRECOVERABLE_MATRIX,
    ids=[case[0] for case in UNRECOVERABLE_MATRIX],
)
def test_대응표와_산문이_같은_자리에서_물러난다(name, sources, should_judge):
    """같은 입력에서 두 «생산» 함수가 함께 판정하거나 함께 물러나야 한다."""

    prose = prose_own_source_problem(UNGROUNDED_RESPONSE_CELL, sources)
    table = challenge_response_evidence_problem(
        (GROUNDED_ISSUE_CELL, UNGROUNDED_RESPONSE_CELL), sources
    )
    assert bool(prose) == bool(table), (name, prose, table)
    assert bool(table) is should_judge, (name, table)
    if should_judge:
        # 물러나지 않은 자리에서는 실제로 «이» 사유가 붙는지까지 본다.
        assert prose == PROSE_OWN_SOURCE_UNSUPPORTED, prose
        assert table == CHALLENGE_RESPONSE_NOT_IN_SOURCE, table


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_다른_장의_두_칸_행에는_이_사유가_붙지_않는다(grouped):
    """대응표 계약은 5장만의 것이다 — 다른 장 도식을 이 잣대로 벌하지 않는다.

    ⚠️ 「그 줄이 살아남는가」로는 확인할 수 없다. 다른 장에는 다른 가드가 있어
      어차피 빠질 수 있기 때문이다. 그래서 «이 사유 코드가 붙었는가»만 본다.
    """

    row = FlowRow((GROUNDED_ISSUE_CELL, UNGROUNDED_RESPONSE_CELL), ("1",))

    _rows, diagnostics, _calls = _run_review(row, grouped, section_id="business_model")

    assert CHALLENGE_RESPONSE_NOT_IN_SOURCE not in _reason_codes(diagnostics)


# ══════════════════════════════════════════════════════════
# ⑤ 프롬프트가 «바로 뒤 대응»과 «없는 수 금지»를 지시한다
# ══════════════════════════════════════════════════════════


def _prompt_for(section_id: str) -> str:
    return build_section_prompt(
        "가나다전자",
        section_id,
        (CollectedFragment(fragment_id="1", kind="사업내용", text=ACTUAL_EDUCATION_SOURCE),),
        None,
    )


def test_5장_프롬프트가_바로_뒤_대응_문장을_같은_줄로_내라고_지시한다():
    prompt = _prompt_for(CHALLENGE_FLOW_SECTION_ID)

    assert "«바로 뒤»" in prompt
    assert "회사가 밝힌 대응»이다" in prompt
    assert "과제만 적고 뒤 문장을 버리지 않는다" in prompt


def test_5장_프롬프트의_대응_지시가_칸_길이_규칙과_맞선다():
    """독립 검토(P2-4) — 두 지시가 서로 맞서면 모델은 어느 쪽이든 어긴다.

    「두 문장을 두 칸으로 함께 낸다」와 「각 칸은 짧은 이름·구로 쓴다(한 문장을
    통째로 넣지 않는다)」가 같은 지침에 함께 있었다. 앞을 따르면 칸이 길어져
    수치 게이트에 걸릴 면이 넓어지는데, 줄 통째 폐기의 원인이 바로 그것이었다.
    """

    prompt = _prompt_for(CHALLENGE_FLOW_SECTION_ID)

    # 칸 길이 규칙은 그대로 남아 있다.
    assert "각 칸은 «짧은 이름·구»로 쓴다(한 문장을 통째로 넣지 않는다)" in prompt
    # 그와 맞서던 문구는 없어졌다.
    assert "두 문장을 한 줄의 두 칸으로 함께 낸다" not in prompt
    # 대신 «핵심 구를 나눠 낸다»로 지시한다 — 뒤 문장을 버리라는 뜻이 아니다.
    assert "두 문장의 «핵심 구»를 한 줄의 두 칸으로 나눠 낸다" in prompt
    assert "원문 표현 그대로 짧게 줄인 구를 넣는다" in prompt


def test_5장_프롬프트가_원문에_없는_수를_금지한다():
    prompt = _prompt_for(CHALLENGE_FLOW_SECTION_ID)

    assert "「3년 연속」" in prompt
    assert "«대응»까지 함께 사라진다" in prompt
    assert "근거 없는 대응으로 그 줄이 빠진다" in prompt


def test_출력형식_안내는_여전히_하나뿐이다():
    """★ 두 개면 작가가 「이 JSON만 출력한다」를 따라 표를 통째로 빼먹는다."""

    assert _prompt_for(CHALLENGE_FLOW_SECTION_ID).count("출력 형식") == 1


def test_새_지시는_5장에만_붙는다():
    for section_id in SECTION_IDS:
        prompt = _prompt_for(section_id)
        if section_id == CHALLENGE_FLOW_SECTION_ID:
            assert "「3년 연속」" in prompt
        else:
            assert "「3년 연속」" not in prompt, section_id

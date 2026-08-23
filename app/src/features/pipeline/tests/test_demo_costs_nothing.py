"""데모가 «쓰지 않은 돈»을 기록하지 않는지 못 박는다 (문제로그 P-84).

★ 이 시험이 잡는 것 — **관리 화면의 「이번 달 AI 비용」이 부풀어 오르는 것.**
  데모는 저장된 결과를 그대로 보여줄 뿐 AI를 한 번도 부르지 않는다. **0원이다.**
  그런데 예전에는 기존 기록에 담긴 비용을 그대로 실어 이력에 남겼다.
  실측 — 이력 **791건에 34,222원**이 쌓였는데 그 달 **진짜 지출은 약 750원**이었다.
  **45배.** 이 숫자를 보고 예산을 판단하면 그대로 틀린다.

★ 왜 시험이 필요한가 — 이 값을 못 박은 시험이 **하나도 없었다.**
  데모 비용을 0으로 바꿔도 706개 시험이 전부 통과했다. 아무도 안 지키고 있던 값이다.

★ P-70·P-82와 같은 부류 — **화면의 숫자가 사실과 다르다.**
  P-70은 캐시 재사용을 전부로 셌고, P-82는 옛 금액을 말했고, 이것은 안 쓴 돈을 셌다.

⚠️ 원래 얼마였는지는 **1판 기록에 그대로 있다.** 여기서 0으로 두어도 정보는 잃지 않는다.
"""

from __future__ import annotations

import pytest

from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import Outcome, UserInput

#: 현재 출고 게이트를 통과한 무료 canonical 데모.
_표본 = ("(주)진영",)


def _데모결과(회사: str):
    pipeline = DemoPipeline()
    user_input = UserInput(company=회사, job="", region="", posting_text="")
    card = pipeline.find_company(user_input)
    if card is None:
        pytest.skip(f"[{회사}] 데모 기록이 없습니다")
    return pipeline.run(user_input, card)


# ══════════════════════════════════════════════════════════
# ① 데모는 0원이다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("회사", _표본)
def test_데모는_돈이_안_든다(회사: str):
    """★ P-84 그 자체. `port.py`의 약속은 「**이 요청에** 쓴 AI 비용」이다."""
    result = _데모결과(회사)

    assert result.cost_krw == 0.0, (
        f"[{회사}] 데모가 {result.cost_krw}원을 썼다고 말합니다 — "
        "AI를 한 번도 안 불렀는데 돈이 나갈 수 없습니다"
    )


def test_보고서가_나와도_0원이다():
    """★ 「실패해서 0원」이 아니라 «성공했는데도» 0원이어야 한다.

    이 확인이 없으면, 데모가 전부 실패하는 바람에 통과하는 시험이 된다.
    """
    result = _데모결과("(주)진영")

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.cost_krw == 0.0


# ══════════════════════════════════════════════════════════
# ② 이력에 쌓아도 0원이다
# ══════════════════════════════════════════════════════════


def test_데모를_여러_번_돌려도_비용_합이_0이다():
    """★ 관리 화면은 «합»을 보여준다 — 한 건이 0이어도 합이 새면 소용없다."""
    합 = sum(_데모결과(회사).cost_krw for 회사 in _표본)

    assert 합 == 0.0


# ══════════════════════════════════════════════════════════
# ③ 잃은 정보가 없다 (반대 방향)
# ══════════════════════════════════════════════════════════


def test_어느_모델로_만든_기록인지는_그대로_말한다():
    """0원으로 바꾸느라 «출처»까지 지우면 안 된다.

    저장된 canonical 샘플이라는 표시가 없으면 실제 API 실행으로 오해할 수 있다.
    """
    result = _데모결과("(주)진영")

    assert result.model == "canonical-demo-v3"


def test_보고서_알맹이는_그대로다():
    """비용만 0으로 바꿨을 뿐, 보여주는 내용은 건드리지 않았다."""
    result = _데모결과("(주)진영")

    assert result.report is not None
    assert result.report.schema_version == "company-report-v4-canonical"
    assert result.fragments_collected > 0
    assert result.sentences_passed > 0

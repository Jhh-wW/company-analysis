"""데모와 진짜 조사가 «같은 문장 판정»을 쓰는지 못 박는다.

★ 이 시험이 잡는 것 — **무료 데모가 유료 조사보다 «나쁜» 문장을 보여주는 것.**
  데모는 자기만의 종결형 검사를 따로 갖고 있었다. 그런데 그 검사는
  「…수출 노선을 확보했다....」를 **통과시킨다** — 꼬리 점을 떼면 「다」로 끝나기 때문이다.
  그래서 유료 조사(`spanselect`)에서는 걸러지는 잘린 토막이 **데모 화면에는 그대로 실렸다.**
  사용자는 데모를 «먼저» 보므로, 이 도구가 실제보다 엉성해 보인다.

★ 왜 이 부류가 반복되나 — 같은 판단을 **두 곳에** 적어 두면 한 곳만 고쳐진다.
  앞머리 떼기를 «같은 함수»로 합치며 이미 겪었는데, 잘림 검사는 합치지 않았다.
  이 시험은 **규칙이 두 벌로 갈라지는 순간** 깨진다.

⚠️ 이 시험은 「데모와 진짜 조사가 **같은 문장을 고른다**」고 말하지 않는다.
  고르는 주체가 다르다 — 데모는 코드, 진짜 조사는 AI다. 못 박는 것은
  **「못 쓴다고 판정하는 기준이 하나뿐」**이라는 것뿐이다.
"""

from __future__ import annotations

import pytest

from src.features.pipeline import demo
from src.features.pipeline.port import UserInput
from src.features.spanselect.logic import is_unusable_candidate

#: 실제로 데모 화면에 실렸던 잘린 토막 (루트로닉 4-2).
_실제로_새어나갔던_문장 = (
    "사이노슈어 루트로닉의 세르프가 CE MDR 인증을 받으며 "
    "전 세계 18개국에 수출 노선을 확보했다...."
)

#: 실제 공개되는 canonical 데모만 검사한다. 구형 저장본은 출고 게이트에서 막힌다.
_표본 = (demo.CANONICAL_DEMO_COMPANY,)


def _데모_4번_인용문장() -> list[tuple[str, str]]:
    """데모가 실제로 화면에 내보내는 4번 문장을 모은다.

    Returns:
        `(회사, 문장)` 목록. **출처가 붙은 줄만** 담는다 —
        안내·경고 줄은 「본문 문장」이 아니라서 세면 결과가 부풀어 오른다.
    """
    pipeline = demo.DemoPipeline()
    out: list[tuple[str, str]] = []
    for 회사 in _표본:
        user_input = UserInput(company=회사, job="", region="", posting_text="")
        card = pipeline.find_company(user_input)
        if card is None:
            continue
        result = pipeline.run(user_input, card)
        if result.report is None:
            continue
        out.extend(
            (회사, sentence)
            for section in result.report.sections
            if section.cell == "past_changes"
            for sentence, cite in section.lines
            if (cite or "").strip()
        )
    return out


# ══════════════════════════════════════════════════════════
# ① 데모 화면에 «못 쓸 문장»이 없다
# ══════════════════════════════════════════════════════════


def test_데모_4번에_못_쓸_문장이_없다():
    """★ 그 자체. 실측 — 걸러내기 전 13문장 중 1개가 잘린 토막이었다."""
    범인 = [
        f"[{회사}] {문장[:70]}"
        for 회사, 문장 in _데모_4번_인용문장()
        if is_unusable_candidate(문장)
    ]

    assert not 범인, (
        "유료 조사에서는 걸러지는 문장이 무료 데모에 실렸습니다: " + " / ".join(범인)
    )


def test_실제로_새어나갔던_그_문장이_지금은_막힌다():
    """★ 「증상이 안 보인다」가 아니라 «그 입력»을 직접 넣어 확인한다 (교훈)."""
    assert is_unusable_candidate(_실제로_새어나갔던_문장)
    assert not demo._news_sentences(_실제로_새어나갔던_문장)


# ══════════════════════════════════════════════════════════
# ② 걸러내기가 과하지 않다 (안전핀)
# ══════════════════════════════════════════════════════════


def test_걸러내도_canonical_과거장이_비지_않는다():
    """공개 canonical 데모의 완료 실행 문장은 공통 필터 뒤에도 남아야 한다."""
    찬_회사 = {회사 for 회사, _ in _데모_4번_인용문장()}

    assert 찬_회사 == {demo.CANONICAL_DEMO_COMPANY}


def test_쓸_만한_뉴스_문장은_데모에서도_살아남는다():
    """진짜 조사 쪽 안전핀(`test_candidate_filter.py`)과 짝을 이룬다."""
    원문 = (
        "토스씨엑스가 상담 직무를 단순 고객 응대가 아닌 "
        "서비스 개선과 연결되는 전문 역할로 확장하는 데 힘을 싣고 있다."
    )

    assert demo._news_sentences(원문) == [원문]


# ══════════════════════════════════════════════════════════
# ③ 규칙이 «두 벌»로 갈라지지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "문장",
    [
        "사이노슈어 루트로닉의 세르프가 CE MDR 인증을 받으며 전 세계 18개국에 수출 노선을 확보했다....",
        "파마리서치는 최근 호주 시드니에서 열린 국제 미용의학 심포지엄에 참가해...",
        "Find a provider News About Us Back Leadership Careers Contact Us CLShop Webstore AMPS",
    ],
)
def test_진짜_조사가_버리는_것은_데모도_버린다(문장: str):
    """★ 두 경로의 답이 갈리는 순간 깨진다 — 그게 이 시험의 목적이다."""
    assert is_unusable_candidate(문장), "전제가 틀렸습니다 — 진짜 조사 쪽 판정부터 확인하세요"

    assert not demo._news_sentences(문장), (
        "진짜 조사는 버리는데 데모는 살렸습니다 — 규칙이 두 벌로 갈라졌습니다"
    )

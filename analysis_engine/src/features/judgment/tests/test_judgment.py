"""판정 사다리 테스트 — 「답이 하나로 정해지는 것은 점수가 아니라 버그로 다룬다」."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from features.judgment.logic import (
    STATUS_ACCEPT,
    STATUS_REJECT_A,
    STATUS_REJECT_B,
    TYPE_AUDITED,
    TYPE_LISTED,
    decide,
)

FAKE_REGISTRY = {"1208200052": "한국전력공사"}


def lookup(bizno):
    digits = "".join(ch for ch in str(bizno or "") if ch.isdigit())
    return FAKE_REGISTRY.get(digits)


def test_상장_공기업은_거부A():
    r = decide("Y", True, "120-82-00052", lookup)
    assert r.status == STATUS_REJECT_A and "한국전력" in r.reason


def test_일반_유가증권은_상장사():
    r = decide("Y", False, "111-11-11111", lookup)
    assert r.status == STATUS_ACCEPT and r.corp_type == TYPE_LISTED


def test_코넥스도_상장사다():
    assert decide("N", True, None, lookup).corp_type == TYPE_LISTED


def test_한진해운형_기타는_상장이_아니다():
    # 종목코드가 남아 있어도 법인구분 E면 조건 2로 내려간다 (corpcode 실측 §2)
    r = decide("E", True, None, lookup)
    assert r.corp_type == TYPE_AUDITED


def test_기타_감사보고서_있으면_비상장외감():
    assert decide("E", True, "222-22-22222", lookup).status == STATUS_ACCEPT


def test_기타_감사보고서_없으면_거부B():
    assert decide("E", False, None, lookup).status == STATUS_REJECT_B


def test_조건0은_상장이든_아니든_돈다():
    """★ 2026-08-27 뒤집힘 — 옛 이름은 `test_조건0은_상장_경로에서만_돈다` 였다.

    옛 규칙은 「비상장(E)은 공기업 번호여도 명단 대조를 안 탄다」였다.
    그 결과 **비상장 공공기관이 「공개된 재무 자료가 없습니다」 화면**을 봤다.
    거부되는 것은 맞지만(정본: 공공기관·공기업은 다루지 않는다) «이유»가 거짓이었다.
    실측 2026-08-27: 한국철도공사·한국토지주택공사·한국관광공사·인천국제공항공사·
    한국산업은행 5곳이 공공기관 명단(355개)에 정확히 있는데도 그 화면을 봤다.
    → 사용자 결정으로 조건 0 을 사다리 «맨 위»로 올렸다.
    """
    r = decide("E", True, "120-82-00052", lookup)
    assert r.status == STATUS_REJECT_A and "한국전력" in r.reason


def test_공공기관은_재무제표가_있어도_거부A다():
    """조건 2-b 로 새로 열린 문으로도 공공기관은 «안» 들어온다."""
    r = decide("E", False, "120-82-00052", lookup, has_financial_statements=True)
    assert r.status == STATUS_REJECT_A


def test_공공기관이_아니면_비상장은_예전대로다():
    """조건 0 을 위로 옮긴 것이 «일반» 비상장 판정을 흔들지 않는다."""
    assert decide("E", True, "222-22-22222", lookup).status == STATUS_ACCEPT
    assert decide("E", False, "222-22-22222", lookup).status == STATUS_REJECT_B
    assert decide("E", False, None, lookup, has_financial_statements=True).status == STATUS_ACCEPT


# ══ 조건 2-b 「공개된 재무제표」 (2026-08-27 추가) ═══════════════════
#
# ★ 왜 갈래를 «더했나» — 실측 2026-08-27
#   「감사보고서」라는 이름의 독립 공시는 사업보고서를 «안» 내는 회사만 낸다
#   (외부감사법 23조① 단서: 사업보고서에 첨부하면 제출한 것으로 «본다»).
#   그래서 현대카드·우리은행·현대캐피탈처럼 «공시를 가장 많이 하는» 비상장
#   대기업이 거부됐다. 조회한 이름난 비상장사 13곳 중 7곳이 이 갈래로 되살아난다.
#
# ★ 왜 2-a 를 «안 뺐나» — 빼면 회귀한다 (실측)
#   삼성디스플레이·쿠팡·우아한형제들은 감사보고서가 있는데 재무 API 는 자료가
#   없다(status 013). 2-a 를 빼고 2-b 로 «갈아치우면» 이 셋이 죽는다.
#   두 갈래는 서로를 대체하지 않는다 — 서로 다른 회사를 살린다.


def test_감사보고서가_없어도_재무제표가_있으면_대상():
    """현대카드·우리은행 부류 — 이 시험이 이번 수정의 «이유»다."""
    r = decide("E", False, None, lookup, has_financial_statements=True)
    assert r.status == STATUS_ACCEPT and r.corp_type == TYPE_AUDITED


def test_둘_다_없으면_거부B():
    r = decide("E", False, None, lookup, has_financial_statements=False)
    assert r.status == STATUS_REJECT_B


def test_감사보고서만_있어도_대상_이다_재무제표_없이():
    """삼성디스플레이·쿠팡 부류 — 2-a 를 빼면 여기가 빨간불이 된다."""
    r = decide("E", True, None, lookup, has_financial_statements=False)
    assert r.status == STATUS_ACCEPT and r.corp_type == TYPE_AUDITED


def test_감사보고서가_먼저_판정한다():
    """둘 다 있으면 2-a 가 이긴다 — 사유 문구로 어느 갈래인지 구분된다."""
    r = decide("E", True, None, lookup, has_financial_statements=True)
    assert r.reason == "감사보고서 공시 존재"


def test_재무제표_갈래는_자기_사유를_남긴다():
    """왜 통과시켰는지가 기록에 남아야 나중에 되짚을 수 있다."""
    r = decide("E", False, None, lookup, has_financial_statements=True)
    assert "재무제표" in r.reason


def test_상장사는_재무제표_인자를_보지_않는다():
    """상장 경로는 예전 그대로다 — 새 인자가 상장 판정을 흔들면 안 된다."""
    for 재무 in (True, False):
        r = decide("Y", False, "111-11-11111", lookup, has_financial_statements=재무)
        assert r.status == STATUS_ACCEPT and r.corp_type == TYPE_LISTED


def test_상장_공기업은_재무제표가_있어도_거부A():
    """조건 0 이 새 인자에 밀리면 안 된다."""
    r = decide("Y", True, "120-82-00052", lookup, has_financial_statements=True)
    assert r.status == STATUS_REJECT_A


def test_새_인자를_안_넘기면_예전과_똑같다():
    """기본값 False — 옛 호출부(run_pilot.py)가 바뀌지 않았음을 못 박는다."""
    assert decide("E", False, None, lookup).status == STATUS_REJECT_B
    assert decide("E", True, None, lookup).status == STATUS_ACCEPT

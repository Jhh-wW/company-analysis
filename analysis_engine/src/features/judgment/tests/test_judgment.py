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


def test_조건0은_상장_경로에서만_돈다():
    # 비상장(E)은 공기업 번호여도 명단 대조를 안 탄다 — 감사보고서 유무로만 판정
    r = decide("E", True, "120-82-00052", lookup)
    assert r.status == STATUS_ACCEPT and r.corp_type == TYPE_AUDITED

# -*- coding: utf-8 -*-
"""DART 응답 «상태값»이 어느 예외로 갈리는지 지키는 시험.

★ 왜 이 파일이 생겼나 (2026-08-27 · 적대 검수 D12)
  ─────────────────────────────────────────────────────────
  `_AUTH_STATUSES` 에 901(키 만료)을 넣었는데, 적대 검수가 재보니
  **그 값을 도로 빼도 아무 데도 빨간불이 안 떴다.** 상태값 → 예외 매핑을
  지키는 시험이 저장소에 «0건»이었기 때문이다. 있던 것은
  `test_dart_counter.py` 의 HTTP 상태(401·429·503)뿐이고, 그건 다른 층이다.

  넣었는데 시험이 안 깨지면 아무도 안 지켜 준다는 뜻이다. 이 파일이 그 구멍을 막는다.

★ 이 매핑이 왜 중요한가
  ─────────────────────────────────────────────────────────
  「열쇠가 죽은 것」과 「이 회사 자료가 없는 것」을 섞으면, 열쇠가 만료된 뒤에도
  배치가 남은 회사를 전부 돌며 호출만 태우고 화면에는
  「분석할 근거가 없습니다」가 뜬다 — 회사 탓이 아닌데 회사 탓을 한다.

  출처(공식 코드표):
  https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import dart_client  # noqa: E402


class _계수기:
    """UsageCounter 흉내 — 파일을 안 건드린다."""

    def __init__(self) -> None:
        self.ticks = 0

    def tick(self, today: Optional[str] = None) -> int:
        self.ticks += 1
        return self.ticks


@pytest.fixture
def 응답을_정한다(monkeypatch: pytest.MonkeyPatch):
    """`get_json` 이 받을 JSON 본문을 시험이 정하게 한다 (망·열쇠 없이)."""

    def 정하기(payload: dict[str, Any]) -> None:
        monkeypatch.setattr(dart_client, "api_key", lambda: "가짜열쇠")
        monkeypatch.setattr(
            dart_client,
            "_read_url",
            lambda *_a, **_k: json.dumps(payload).encode("utf-8"),
        )

    return 정하기


@pytest.mark.parametrize("상태", ["010", "011", "012", "901"])
def test_열쇠가_죽은_상태는_인증_오류로_터진다(응답을_정한다, 상태: str) -> None:
    """★ 901 이 `_AUTH_STATUSES` 에 없으면 이 시험이 빨간불이 된다.

    010 등록되지 않은 키 · 011 사용할 수 없는 키 · 012 접근할 수 없는 IP ·
    901 개인정보 보유기간 만료로 사용할 수 없는 키.
    넷 다 «회사»가 아니라 «열쇠»의 문제다.
    """
    응답을_정한다({"status": 상태, "message": "무언가"})

    with pytest.raises(dart_client.DartAuthenticationError):
        dart_client.get_json("list.json", {"corp_code": "00000000"}, _계수기())


def test_한도_소진은_한도_오류로_터진다(응답을_정한다) -> None:
    응답을_정한다({"status": "020", "message": "무언가"})

    with pytest.raises(dart_client.DartLimitReached):
        dart_client.get_json("list.json", {"corp_code": "00000000"}, _계수기())


@pytest.mark.parametrize("상태", ["000", "013"])
def test_정상과_자료없음은_터지지_않고_그대로_돌려준다(응답을_정한다, 상태: str) -> None:
    """013(조회 범위에 자료 없음)은 «빈 결과»이지 오류가 아니다."""
    응답을_정한다({"status": 상태, "list": []})

    받은것 = dart_client.get_json("list.json", {"corp_code": "00000000"}, _계수기())

    assert 받은것["status"] == 상태


@pytest.mark.parametrize("상태", ["800", "900", "100", "101", "021", "014"])
def test_그_밖의_오류는_여기서_안_터지고_부르는_쪽이_정한다(응답을_정한다, 상태: str) -> None:
    """★ 이 갈래가 실제 결함을 만들었다.

    `get_json` 은 이 상태들을 «그대로» 돌려준다. 그래서 부르는 쪽이 검사해야 한다.
    `fetch_financials` 가 그 검사를 안 해서 시스템 점검(800) 중에 멀쩡한 회사가
    「재무 자료 없음」으로 거부됐다(2026-08-27 수정).
    이 시험은 그 계약을 «여기서» 못 박는다 — get_json 은 안 터뜨린다.
    """
    응답을_정한다({"status": 상태, "message": "무언가"})

    받은것 = dart_client.get_json("list.json", {"corp_code": "00000000"}, _계수기())

    assert 받은것["status"] == 상태


def test_상태값이_없으면_응답_오류다(응답을_정한다) -> None:
    응답을_정한다({"list": []})

    with pytest.raises(dart_client.DartResponseError):
        dart_client.get_json("list.json", {"corp_code": "00000000"}, _계수기())


def test_호출할_때마다_계수기를_한_번_민다(응답을_정한다) -> None:
    """무료여도 일일 한도가 있다 — 세지 않으면 한도를 모른다."""
    응답을_정한다({"status": "000", "list": []})
    counter = _계수기()

    dart_client.get_json("list.json", {"corp_code": "00000000"}, counter)

    assert counter.ticks == 1

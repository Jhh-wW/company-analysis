# -*- coding: utf-8 -*-
"""독자 채널(웹·PDF·노션)에 나오면 안 되는 «만드는 과정» 문구 목록.

★ 왜 있나 (사용자 결정, 2026-09-05)
  서비스는 출시됐고 보고서는 더 이상 「임시」가 아니다. 「안전 확인 중」·
  「아직 끝나지 않았습니다」·「…문장 N개를 뺐습니다」·「자료가 적으니 다른
  자료와 함께 보시길 권합니다」는 전부 «우리가 아직 무엇을 못 했는지»를 적은
  과정·변명 문구다. 독자가 이 말을 듣고 할 수 있는 일이 없다.

★ 왜 «한 곳»에 두나
  세 채널이 각자 금지 목록을 들면, 한 채널만 목록을 빠뜨려도 아무 시험이
  깨지지 않는다. 실제로 고지 문구가 채널마다 갈라졌던 사고가 그렇게 났다.
  세 채널을 «같은 목록»으로 검사하려고 이 파일을 둔다.

★ 문장 전문이 아니라 «표현»을 막는다. 전문만 막으면 토씨를 조금 바꿔
  되살릴 때 못 잡는다.

★ 지운 것은 «표시»뿐이다. 사유 자료(``shortfall_reasons``)·안전 판정
  (``safety_decision``)·공개 정책(``publication_policy``)·영수증·해시는
  그대로 저장되어 관리자 화면과 진단에서 읽힌다.
"""

from __future__ import annotations

from typing import Final

#: 독자 채널의 «보이는 글자»에 있으면 안 되는 표현과 그 이유.
READER_BANNED_EXPRESSIONS: Final[dict[str, str]] = {
    "안전 확인 중": "내부 검사 진행 상태 — 독자가 손쓸 수 없는 우리 사정",
    "임시 부분 보고서": "출시 전 단계 이름 — 발행된 보고서는 임시가 아니다",
    "아직 끝나지 않았습니다": "미완료 안내 — 독자에게 필요한 건 «지금 무엇이 있는가»",
    "확인하지 못했": "우리가 못 한 일의 고백 — 본문에 없는 것은 이미 안 실렸다",
    "뺐습니다": "제외 과정 설명 — 무엇을 뺐는지는 내부 기록이다",
    "권합니다": "자료가 적으니 다른 자료를 보라는 변명형 권고",
}


def banned_hits(text: str) -> list[str]:
    """``text`` 안에서 발견된 금지 표현을 돌려준다. 없으면 빈 목록."""

    return [
        expression
        for expression in READER_BANNED_EXPRESSIONS
        if expression in text
    ]


def banned_hits_by_channel(texts: dict[str, str]) -> dict[str, list[str]]:
    """채널 이름 → 그 채널에서 발견된 금지 표현.

    Args:
        texts: ``{"웹": ..., "PDF": ..., "노션": ...}`` 처럼 채널별 «보이는 글자».

    Returns:
        채널마다 걸린 표현 목록. 전부 깨끗하면 값이 모두 빈 목록이다.
    """

    return {channel: banned_hits(text) for channel, text in texts.items()}

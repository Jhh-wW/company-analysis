"""보충 회차가 «승인하지 않은 장의 감사 장부»를 건드리면 생산에서 막는지 지킨다.

기존 그물의 구멍 — ``_validate_supplement_binding``은 영수증의
``section_sha256s``만 비교했다. 그 값은 pre-render 공개 content 봉인(지문 A)에서
오고 지문 A는 «보이는 것»만 덮는다. 그래서 보충 회차에서 비대상 장의 글자는
그대로 두고 FactRecord나 등급 기여만 바꾸면 그 그물을 그냥 통과했다.

``section_block_sha256s``는 공개 봉인 블록의 ``block_sha256``, 즉 display와
ledger를 «함께» 덮는 지문이다. 이걸 같이 비교하면 장부만 바꾼 표류가 생산
실행 중에 fail-closed로 막힌다(I3).

★ 재료는 옆 파일 ``test_logic.py``의 영수증 도구를 그대로 빌려 쓴다. 거기가
  이 판정의 정본 시험이라 두 벌로 갈라지면 안 된다.
"""

from __future__ import annotations

import pytest

from src.features.report_recovery.logic import decide_post_validation
from src.features.report_recovery.models import RecoveryAction
from src.features.report_recovery.tests.test_logic import (
    _assessment,
    _primary,
    _recoverable,
    _section_sha256s,
    _sha256,
    _supplement,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


_TARGET = "identity"
_UNTOUCHED = "culture"


def _blocks_with(**overrides: str) -> tuple[tuple[str, str], ...]:
    """기본 보충 블록 지문에서 원하는 장만 다른 값으로 바꾼다."""

    base = dict(_section_sha256s("supplement-block"))
    base.update(overrides)
    return tuple(
        (section_id, base[section_id])
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    )


def _authorized_primary():
    primary = _primary(_recoverable(_TARGET))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    return primary, first.supplement_authorization


def _decide(primary, authorization, supplement):
    return decide_post_validation(
        primary,
        supplement_authorization=authorization,
        supplement_receipt=supplement,
    )


def test_정상_보충은_대상_장의_블록만_바뀌어_통과한다() -> None:
    """음성 대조 — 아래 시험이 «무엇이든» 막는 그물이 아님을 보인다."""

    primary, authorization = _authorized_primary()
    unchanged = dict(primary.section_block_sha256s)
    supplement = _supplement(
        primary,
        authorization,
        _assessment(),
        section_block_sha256s=_blocks_with(
            **{
                section_id: digest
                for section_id, digest in unchanged.items()
                if section_id != _TARGET
            }
        ),
    )

    decision = _decide(primary, authorization, supplement)

    assert decision.action is RecoveryAction.RELEASE_COMPLETE


def test_비대상_장의_장부만_바뀌어도_보충_결속이_막힌다() -> None:
    """★ 이 파일의 핵심 — 글자는 그대로, 장부만 바뀐 표류.

    ``section_sha256s``(보이는 것)는 비대상 장이 그대로라 예전 그물은 이걸
    통과시켰다. ``section_block_sha256s``는 장부까지 덮으므로 여기서 걸린다.
    """

    primary, authorization = _authorized_primary()
    unchanged = dict(primary.section_block_sha256s)
    drifted = {
        section_id: digest
        for section_id, digest in unchanged.items()
        if section_id != _TARGET
    }
    # 비대상 장 하나의 «장부만» 달라진다. 보이는 글자(section_sha256s)는
    # _supplement 기본값 그대로라 대상 장만 바뀐 완벽한 모양이다.
    drifted[_UNTOUCHED] = _sha256("장부만-바뀐-비대상-장")
    supplement = _supplement(
        primary,
        authorization,
        _assessment(),
        section_block_sha256s=_blocks_with(**drifted),
    )

    with pytest.raises(ValueError, match="승인하지 않은 장"):
        _decide(primary, authorization, supplement)


def test_대상_장의_블록_지문이_그대로면_보충_결속이_막힌다() -> None:
    """보충했다면서 그 장의 봉인 블록이 한 글자도 안 바뀐 경우."""

    primary, authorization = _authorized_primary()
    supplement = _supplement(
        primary,
        authorization,
        _assessment(),
        section_block_sha256s=primary.section_block_sha256s,
    )

    with pytest.raises(ValueError, match="승인된 보충 장"):
        _decide(primary, authorization, supplement)


def test_block_지문이_빠진_영수증은_보충_결속에서_거부된다() -> None:
    """그물을 «끄는» 방법이 없어야 한다.

    새 필드를 안 채우면 검사가 조용히 없어지는 구조라면, 다음 변경이 그 필드를
    안 채우는 순간 이 티켓이 만든 보호가 통째로 사라진다. 그래서 없으면 막는다.
    """

    primary, authorization = _authorized_primary()
    supplement = _supplement(
        primary,
        authorization,
        _assessment(),
        section_block_sha256s=(),
    )

    with pytest.raises(ValueError, match="장별 봉인 블록 지문"):
        _decide(primary, authorization, supplement)

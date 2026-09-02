# -*- coding: utf-8 -*-
"""표와 DART 원 payload 의 대조가 «실제 자료에서» 성립하는지 못 박는다.

★ 왜 이 파일이 생겼나 (실측)
  ─────────────────────────────────────────────────────────
  4장 「누적 증감률」 문장은 이 대조가 통과해야만 만들어진다. 그런데 저장된
  보고서 38건이 «전부» 구조화 사실 0개였고, 실제 실행 어디에도 그 문장이
  없었다. 반면 오프라인 fixture 시험은 3개를 만들었다 —
  즉 생산자는 멀쩡하고 «실제 자료에서만» 죽고 있었다.

★ 원인
  한 지표의 계정 ID 를 고르는 방식이 두 모듈에서 «달랐다».
    · 표를 만드는 쪽(`company_performance/logic.py::_ACCOUNT_IDS`)
      — ID 를 순서대로 하나씩 보고 먼저 맞는 ID 에서 멈춘다.
    · 대조기(옛 판) — 그 지표의 ID 를 «전부 한꺼번에» 모았다(집합).
  영업이익은 ID 가 둘인데 DART 응답에 둘 다 들어 있으면, 대조기는 서로 다른
  행 2개를 얻어 「모호하다」며 포기했다. 두 쪽이 «다른 행»을 고르니 대조가
  성립할 수 없었다.

★ 이 시험이 지키는 것
  ① 두 모듈의 계정 표가 «순서까지» 같다          ← 다시 어긋나면 즉시 깨진다
  ② ID 가 둘 다 든 payload 에서도 대조가 통과한다  ← 이 버그의 재현
  ③ 느슨해지지 않았다 — 값이 다르면 여전히 실패한다
"""

from __future__ import annotations

import json
from typing import Any

from src.features.company_performance.logic import (
    _ACCOUNT_IDS,
    MIN_METRICS_FOR_TABLE,
    _row_signature as _표_서명,
    build_three_year_table,
)
from src.features.composer.port import performance_table_from_report_table
from src.shared.dart_financial_provenance import (
    _ACCOUNT_IDENTITIES,
    MIN_METRICS_FOR_MATCH,
    _row_signature as _대조_서명,
    dart_payload_matches_table,
)

_기간 = (
    "2025.01.01 ~ 2025.12.31",
    "2024.01.01 ~ 2024.12.31",
    "2023.01.01 ~ 2023.12.31",
)
_매출액 = ("821850000000", "601790000000", "566500000000")
_영업이익 = ("155250000000", "128260000000", "169440000000")
_당기순이익 = ("118400000000", "97300000000", "129900000000")


def _행(account_id: str, account_nm: str, 금액: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": account_id,
        "account_nm": account_nm,
        "bsns_year": "2025",
        "reprt_code": "11011",
        "currency": "KRW",
        "thstrm_dt": _기간[0],
        "thstrm_amount": 금액[0],
        "frmtrm_dt": _기간[1],
        "frmtrm_amount": 금액[1],
        "bfefrmtrm_dt": _기간[2],
        "bfefrmtrm_amount": 금액[2],
    }


def _payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "000", "list": rows}


def _봉인(payload: dict[str, Any]) -> str:
    """대조기가 요구하는 «정규화된» JSON 원문으로 만든다."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _표(payload: dict[str, Any]):
    """실제 경로와 «같은» 자료형으로 만든다 — 대조기는 PerformanceTable 을 받는다."""
    built = build_three_year_table(payload, cite="조각 1·재무")
    if built is None:
        return None
    return performance_table_from_report_table(built)


def _기본_행들() -> list[dict[str, Any]]:
    return [
        _행("ifrs-full_Revenue", "매출액", _매출액),
        _행("dart_OperatingIncomeLoss", "영업이익", _영업이익),
        _행("ifrs-full_ProfitLoss", "당기순이익", _당기순이익),
    ]


# ══════════════════════════════════════════════════════════
# ① 두 모듈의 계정 표가 «순서까지» 같다
# ══════════════════════════════════════════════════════════


def test_표를_만드는_쪽과_대조기의_계정_표가_같다() -> None:
    """★ 두 쪽이 다른 행을 고르면 대조는 영원히 성립하지 않는다.

    ⚠️ 한쪽에만 계정을 추가하면 이 시험이 먼저 깨진다 — 그게 목적이다.
    """
    만드는쪽 = [(label, tuple(ids), tuple(names)) for label, ids, names in _ACCOUNT_IDS]
    대조기 = [(label, tuple(ids), tuple(names)) for label, ids, names in _ACCOUNT_IDENTITIES]

    assert 대조기 == 만드는쪽, (
        "★ 계정 표가 어긋났다 — 대조기가 표와 다른 행을 고르게 된다"
    )


def test_계정_ID_는_집합이_아니라_순서있는_묶음이다() -> None:
    """★ 집합으로 되돌리면 순서가 사라져 이 버그가 그대로 재발한다."""
    for _label, ids, names in _ACCOUNT_IDENTITIES:
        assert isinstance(ids, tuple), "★ 계정 ID 가 순서를 잃었다"
        assert isinstance(names, tuple), "★ 계정 이름이 순서를 잃었다"


# ══════════════════════════════════════════════════════════
# ② 이 버그의 재현 — ID 가 둘 다 든 payload
# ══════════════════════════════════════════════════════════


def test_영업이익_계정이_둘_다_있어도_대조가_통과한다() -> None:
    """★ 이게 수정의 «이유»다.

    DART 응답에 영업이익이 두 계정 ID 로 «둘 다» 들어오는 일이 흔하다.
    옛 판은 그 둘을 한꺼번에 모아 「모호」로 떨어뜨렸고, 그 탓에 4장
    누적 증감률 문장이 실제 자료에서 한 번도 만들어지지 않았다.
    """
    행들 = _기본_행들()
    # 같은 영업이익을 «다른 계정 ID»로 한 번 더 싣는다 (실제 DART 응답 모양).
    행들.append(
        _행("ifrs-full_ProfitLossFromOperatingActivities", "영업이익", _영업이익)
    )
    payload = _payload(행들)
    표 = _표(payload)
    assert 표 is not None, "시험 전제 — 표가 만들어져야 한다"

    assert dart_payload_matches_table(표, _봉인(payload)) is True, (
        "★ 계정 ID 가 둘 다 있다는 이유로 대조가 실패했다"
    )


def test_계정이_하나만_있는_보통_경우도_그대로_통과한다() -> None:
    """★ 회귀 방지 — 흔한 모양이 망가지면 안 된다."""
    payload = _payload(_기본_행들())
    표 = _표(payload)
    assert 표 is not None

    assert dart_payload_matches_table(표, _봉인(payload)) is True


# ══════════════════════════════════════════════════════════
# ③ 느슨해지지 않았다
# ══════════════════════════════════════════════════════════


def test_값이_다르면_여전히_대조가_실패한다() -> None:
    """★ 안전선 — 선택 «순서»만 맞췄지 값 검사를 뺀 것이 아니다."""
    payload = _payload(_기본_행들())
    표 = _표(payload)
    assert 표 is not None

    # 원 payload 의 금액을 바꿔치기한다 — 표는 옛 값 그대로다.
    위조 = _payload(
        [
            _행("ifrs-full_Revenue", "매출액", ("999850000000", "601790000000", "566500000000")),
            _행("dart_OperatingIncomeLoss", "영업이익", _영업이익),
            _행("ifrs-full_ProfitLoss", "당기순이익", _당기순이익),
        ]
    )

    assert dart_payload_matches_table(표, _봉인(위조)) is False, (
        "★ 표와 원본의 값이 다른데 통과했다"
    )


def test_같은_계정_ID_에_서로_다른_행이_섞이면_여전히_고르지_않는다() -> None:
    """★ 안전선 — 같은 후보군 안의 모호성은 그대로 막는다."""
    행들 = _기본_행들()
    # 같은 계정 ID 로 «값이 다른» 행을 하나 더 넣는다.
    행들.append(
        _행("ifrs-full_Revenue", "매출액", ("111110000000", "222220000000", "333330000000"))
    )
    payload = _payload(행들)
    표 = _표(_payload(_기본_행들()))
    assert 표 is not None

    assert dart_payload_matches_table(표, _봉인(payload)) is False, (
        "★ 같은 계정에 서로 다른 값이 섞였는데 하나를 골랐다"
    )


# ══════════════════════════════════════════════════════════
# ④ 「같은 행인가」 판정이 두 모듈에서 같다 (실측으로 잡은 진짜 원인)
# ══════════════════════════════════════════════════════════


def test_같은_행인가_판정이_두_모듈에서_같다() -> None:
    """★ 실측 — 이게 4장 claim 이 한 번도 안 만들어진 «진짜» 원인이다.

    표를 만드는 쪽은 13개 필드만 보고 「같은 행」이라 판정했는데, 대조기는
    행 «전체»를 JSON 으로 찍어 비교했다. 표시 순서(`ord`) 같은 무관한 필드가
    하나만 달라도 대조기만 「모호하다」며 포기했다.
    실측 로그: 「지표 행 고르기 실패: 당기순이익 · 후보 2개(서로 다름)」
    """
    행 = _행("ifrs-full_ProfitLoss", "당기순이익", _당기순이익)
    닮은행 = dict(행)
    닮은행["ord"] = "99"                 # 표시 순서 — 표와 무관
    닮은행["thstrm_add_amount"] = "0"    # 합계 보조값 — 표와 무관

    assert _대조_서명(행) == _대조_서명(닮은행), (
        "★ 표와 무관한 필드 때문에 «다른 행»으로 본다"
    )
    assert _표_서명(행) == _표_서명(닮은행), "시험 전제 — 표 쪽은 같다고 본다"
    assert _대조_서명(행) == _표_서명(행), (
        "★ 두 모듈의 서명이 어긋났다 — 대조가 성립하지 않는다"
    )


def test_금액이_다르면_두_모듈_모두_다른_행으로_본다() -> None:
    """★ 안전선 — 서명을 좁힌 것이 «값 검사»를 뺀 것이 아니다."""
    행 = _행("ifrs-full_ProfitLoss", "당기순이익", _당기순이익)
    다른금액 = _행("ifrs-full_ProfitLoss", "당기순이익", ("1", "2", "3"))

    assert _대조_서명(행) != _대조_서명(다른금액)
    assert _표_서명(행) != _표_서명(다른금액)


def test_무관한_필드만_다른_중복행이_있어도_대조가_통과한다() -> None:
    """★ 이 시험이 깨지면 4장 누적 증감률 문장이 다시 사라진다."""
    행들 = _기본_행들()
    닮은행 = dict(행들[2])          # 당기순이익
    닮은행["ord"] = "42"
    행들.append(닮은행)
    payload = _payload(행들)
    표 = _표(payload)
    assert 표 is not None, "시험 전제 — 표가 만들어져야 한다"

    assert dart_payload_matches_table(표, _봉인(payload)) is True, (
        "★ 표시 순서만 다른 중복 행 때문에 대조가 실패했다"
    )


# ══════════════════════════════════════════════════════════
# ⑤ 매출액이 없는 업종(은행)도 대조가 성립한다
# ══════════════════════════════════════════════════════════


def test_매출액이_없는_표도_대조가_통과한다() -> None:
    """★ 실측(우리은행) — 은행 손익계산서에는 매출액 계정이 없다.

    v2-107 이 표를 만드는 쪽에서 「매출액·영업이익 둘 다」 가정을 걷어냈는데
    대조기에는 남아 있어서, 표는 만들어지지만 대조는 «영원히» 실패했다(분기 8).
    그 결과 우리은행 보고서의 구조화 사실이 계속 0개였다.
    ⚠️ 이 시험이 깨지면 은행·보험 업종에서 4장 문장이 다시 사라진다.
    """
    행들 = [
        _행("dart_OperatingIncomeLoss", "영업이익", _영업이익),
        _행("ifrs-full_ProfitLoss", "당기순이익", _당기순이익),
    ]
    payload = _payload(행들)
    표 = _표(payload)
    assert 표 is not None, "시험 전제 — 매출액 없이도 표가 만들어져야 한다"
    assert "매출액" not in 표.headers

    assert dart_payload_matches_table(표, _봉인(payload)) is True, (
        "★ 매출액이 없다는 이유로 대조가 실패했다"
    )


def test_지표가_하나뿐인_표는_여전히_대조에서_막힌다() -> None:
    """★ 안전선 — 열이 하나면 비교표가 아니다. 관문이 사라지지 않았다."""
    한개 = _payload([_행("dart_OperatingIncomeLoss", "영업이익", _영업이익)])
    # 표 자체가 안 만들어지는 것이 정상 — 그 전제부터 확인한다.
    assert _표(한개) is None

    # 손으로 한 열짜리 표를 만들어 대조기에 직접 물어도 막혀야 한다.
    두열표 = _표(_payload(_기본_행들()))
    assert 두열표 is not None
    깎은표 = 두열표.__class__(
        caption=두열표.caption,
        headers=("사업연도", "영업이익"),
        rows=tuple(tuple([row[0], row[2]]) for row in 두열표.rows),
        unit=두열표.unit,
        cite=두열표.cite,
        raw_rows=tuple(tuple([row[0], row[2]]) for row in 두열표.raw_rows),
        scale_divisor=두열표.scale_divisor,
        scale_places=두열표.scale_places,
        evidence_rows=두열표.evidence_rows,
        entity_scope=두열표.entity_scope,
        raw_unit=두열표.raw_unit,
        unit_dimension=두열표.unit_dimension,
    )

    assert dart_payload_matches_table(깎은표, _봉인(_payload(_기본_행들()))) is False


def test_두_모듈의_최소_지표_수가_같다() -> None:
    """★ 표는 만들어지는데 대조만 실패하는 어긋남을 막는다."""
    assert MIN_METRICS_FOR_MATCH == MIN_METRICS_FOR_TABLE

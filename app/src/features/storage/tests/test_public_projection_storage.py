"""저장된 공개 projection이 왕복에서 살아남고, 손대면 로드가 닫히는지 지킨다.

설계 017 §02-4 「저장 wire」 규칙 세 줄을 기계로 못 박는다.

  · ``report_to_dict``는 값이 **있을 때만** ``public_projection`` 키를 넣는다
    (옛 payload 바이트 불변 — 그 바이트가 이미 승인된 PDF 출고 기록의
    ``report_sha256`` 입력이다).
  · 로드는 저장된 digest를 그냥 믿지 않고 **재계산해 대조**한다.
  · 대조가 어긋나면 조용히 고치지 않고 닫는다(I3 — 봉인이 깨진 보고서는
    공개하지 않는다).

★ 재료를 손으로 짓지 않는 이유 — 손으로 지은 projection은 render가 실제로
  만드는 모양(문단 번호·숨은 인용·도식)을 재현하지 못해, 바로 그 모양 때문에
  생기는 결함을 그물이 통과한다. 그래서 builder 시험이 쓰는 «실제
  ``render_report()``를 통과한» 보고서를 그대로 빌려 쓴다. 그쪽 재료가 바뀌면
  이 시험도 같이 바뀌는 것이 맞다.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from src.features.pipeline.port import Report
from src.features.report_standard.public_projection import build_public_projection
from src.features.report_standard.tests.test_public_projection_builder import (
    _report as _rendered_report,
)
from src.features.storage.reports import (
    report_from_dict,
    report_from_json,
    report_to_dict,
    report_to_json,
)
from src.shared.report_generation.public_projection import build_report_digest


def _report_with_projection() -> Report:
    rendered = _rendered_report()
    return replace(rendered, public_projection=build_public_projection(rendered))


def test_report_dict_왕복에서_projection이_보존된다() -> None:
    report = _report_with_projection()

    payload = report_to_dict(report)
    assert "public_projection" in payload

    restored = report_from_dict(copy.deepcopy(payload))

    assert restored.public_projection is not None
    assert restored.public_projection == report.public_projection
    # dataclass 동치만으로는 «지문까지» 같다고 말할 수 없다(field(init=False)는
    # eq에 들어가지만, 재계산 없이 저장값을 흡수한 경우를 갈라내려면 digest를
    # 다시 계산해 본다).
    assert build_report_digest(restored.public_projection) == build_report_digest(
        report.public_projection
    )
    assert report_to_dict(restored) == payload


def test_projection_JSON_문자열_왕복도_같은_값을_돌려준다() -> None:
    report = _report_with_projection()

    restored = report_from_json(report_to_json(report))

    assert restored.public_projection == report.public_projection


def test_projection이_없으면_payload에_키_자체가_없다() -> None:
    """옛 보고서 payload 바이트를 한 글자도 바꾸지 않는다."""

    report = _rendered_report()
    assert report.public_projection is None

    payload = report_to_dict(report)

    assert "public_projection" not in payload
    assert report_from_dict(payload).public_projection is None


def test_저장된_projection의_display_digest가_다르면_로드가_거부된다() -> None:
    """저장된 digest를 믿지 않고 재계산해 대조한다는 계약."""

    payload = report_to_dict(_report_with_projection())
    forged = copy.deepcopy(payload)
    forged["public_projection"]["digest"]["display_sha256"] = "0" * 64

    with pytest.raises(ValueError):
        report_from_dict(forged)


def test_저장된_projection의_content_digest가_다르면_로드가_거부된다() -> None:
    payload = report_to_dict(_report_with_projection())
    forged = copy.deepcopy(payload)
    forged["public_projection"]["digest"]["content_sha256"] = "1" * 64

    with pytest.raises(ValueError):
        report_from_dict(forged)


def test_저장된_projection_본문을_바꾸면_로드가_거부된다() -> None:
    """digest는 그대로 두고 «보이는 글자»만 바꾼 위조."""

    payload = report_to_dict(_report_with_projection())
    forged = copy.deepcopy(payload)
    display = forged["public_projection"]["projection"]["sections"][0]["display"]
    assert display["sentences"], "첫 장에 문장이 있어야 이 위조가 성립한다"
    display["sentences"][0][0] = "위조된 문장이다."

    with pytest.raises(ValueError):
        report_from_dict(forged)


def test_저장된_projection의_감사장부만_바꿔도_로드가_거부된다() -> None:
    """장부(ledger)는 화면에 안 보이지만 ``content_sha256``이 덮는다.

    ★ 이 위조는 «화면 글자를 하나도 안 건드린다» — 등급 기여만 바꾼다. 그런
      위조까지 닫히는지가 설계 017 §02-1 #2가 요구한 성질이다(digest가 감사
      장부를 덮어야 한다). 위조값 자체는 I4를 통과하는 «있을 수 있는» 값으로
      고른다. 형식 오류로 걸리면 digest 대조를 시험한 것이 아니게 된다.
    """

    payload = report_to_dict(_report_with_projection())
    forged = copy.deepcopy(payload)
    ledger = forged["public_projection"]["projection"]["sections"][0]["ledger"]
    contribution = ledger["source_grade_contribution"]
    assert contribution, "첫 장에 등급 기여가 있어야 이 위조가 성립한다"
    number, grades = contribution[0]
    assert grades, "등급 기여에 등급이 하나는 있어야 한다"
    swapped = "해석" if grades[0] != "해석" else "확인"
    contribution[0] = [number, [swapped]]

    with pytest.raises(ValueError):
        report_from_dict(forged)


def test_projection_wire는_projection과_digest_두_칸만_갖는다() -> None:
    """키를 늘리거나 줄이면 닫는다 — 조용한 스키마 표류 방지."""

    payload = report_to_dict(_report_with_projection())
    assert set(payload["public_projection"]) == {"projection", "digest"}

    for broken in ({"projection": payload["public_projection"]["projection"]}, {}):
        forged = copy.deepcopy(payload)
        forged["public_projection"] = broken
        with pytest.raises(ValueError):
            report_from_dict(forged)

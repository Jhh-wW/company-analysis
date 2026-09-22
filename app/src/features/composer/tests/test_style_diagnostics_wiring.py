# -*- coding: utf-8 -*-
"""최종 렌더의 문체 진단이 «운영 경로»에서 실제로 기록되는지 잰다.

★ 왜 이 시험이 있나 (2026-09-22 독립 검토 실측)
  `render_report`에 `style_diagnostics` 인자를 만들어 뒀는데 `pipeline.py`의
  «어느 호출부도 넘기지 않았다». 기존 시험은 `render_report`를 직접 부르면서
  인자를 손으로 넘겼기 때문에 전부 초록이었고, 그래서 배선이 통째로 비어
  있다는 사실이 한 번도 드러나지 않았다.

★ 여기서 지키는 것
  ⓐ 운영 진입점(`run_v2`)을 실제로 돌려 지난 일정 미래형 문장 1개가 있을 때
     개수 1이 기록된다.
  ⓑ 기준일이 그 일정보다 앞서면 아무것도 기록하지 않는다(무조건 기록이 아님).
  ⓒ 보충 회차도 «출고되는» 렌더에서만 받는다 — 요약 후보를 고르려고 버리는
     중간 렌더에서도 받으면 같은 문장을 두 번 센다.
  ⓓ 확보 근거 보고서(강등) 경로도 같은 배선을 받는다.

★ 기록 자리 — 닫힌 진단 전송 계약(`REVIEW_SCOPE_ITEMS`)이 아니라 운영 로그다.
  그 목록은 화면 안내문이 「…개를 뺐습니다」로 세는 «제외» 장부인데, 시제
  표기는 문장을 빼지 않고 표시만 고쳐 그대로 싣기 때문이다.

⚠️ 실측 메모 — 실제 산출 PDF(주식회사 뤼튼테크놀로지스 2026-09-22)에 인쇄된
  「회사의 재무제표는 … 2026년 3월 31일자 주주총회에서 최종 승인될 예정이다.」
  를 그대로 넣으면 이번 통합에 들어온 회계정책 가드가 규칙 「회계기준적용」으로
  `accounting_policy_boilerplate`를 내어 그 문장을 «먼저» 제거한다
  (`accounting_policy_problem`에 직접 먹여 확인). 그래서 시제 배선을 재는 이
  시험은 회계 어휘가 없는 «같은 꼴»의 지난 일정 문장을 쓴다.
"""
from __future__ import annotations

import json
import logging

from src.features.composer import pipeline
from src.features.composer.constants import SECTION_IDS
from src.features.composer.style_normalizer_constants import (
    DISCLOSURE_ORIGINAL_MARKER,
    PAST_DATED_FUTURE_TENSE,
)
from src.features.composer.tests import test_pipeline as shadow_fixture

#: 1장(기업 정체성) — 시험용 작가 픽스처의 장 표시.
_TARGET_SECTION = "identity"
_MARK = shadow_fixture._SECTION_MARKS[SECTION_IDS.index(_TARGET_SECTION)]
#: 「2026년 3월 31일자 … 예정이다」 — 실제 PDF 문장과 «같은 꼴»의 지난 일정.
_PLANNED_SENTENCE = (
    "가나다전자의 두 번째 검사 장비 생산라인은 2026년 3월 31일자로 가동될 예정이다."
)
#: 그 일정이 «이미 지난» 기준일과 «아직 안 지난» 기준일.
_AFTER_PLAN = "2026-08-24"
_BEFORE_PLAN = "2026-03-01"


def _fragments() -> dict[int, dict[str, str]]:
    """후보가 기댄 원문에도 같은 문장을 둔다 — 없으면 근거 결속에서 먼저 빠진다."""
    raw = shadow_fixture._raw_fragments()
    raw[1] = dict(raw[1])
    raw[1]["원문"] = f"{raw[1]['원문']} {_PLANNED_SENTENCE}"
    return raw


def _patch_writer_sentence(monkeypatch) -> None:
    """작가 응답 «생산자 한 곳»만 갈아 끼운다 — 원문과 후보가 갈라지지 않게."""
    original = shadow_fixture._section_json

    def patched(mark: str) -> str:
        payload = json.loads(original(mark))
        if mark == _MARK:
            payload["문장들"][0]["글"] = f"{mark} 장: {_PLANNED_SENTENCE}"
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(shadow_fixture, "_section_json", patched)


def _run_shadow(as_of_date: str):
    return pipeline.run_v2(
        "가나다전자",
        _fragments(),
        None,
        writer_ask=shadow_fixture._FakeWriter(),
        reviewer_ask=shadow_fixture._FakeReviewer(),
        corp_type="상장사",
        as_of_date=as_of_date,
    )


def _recorded(caplog) -> list[dict[str, int]]:
    return [
        record.pipeline_style_diagnostics
        for record in caplog.records
        if hasattr(record, "pipeline_style_diagnostics")
    ]


def _section_lines(output) -> list[str]:
    section = next(
        item for item in output.report.sections if item.cell == _TARGET_SECTION
    )
    return [text for text, _hint in section.prose_lines]


# ══════════════════════════════════════════════════════════
# ⓐⓑ 본 경로 — 기준일에 따라 기록이 생기고 사라진다
# ══════════════════════════════════════════════════════════


def test_운영_경로가_지난_일정_미래형_개수를_기록한다(monkeypatch, caplog):
    _patch_writer_sentence(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.features.composer.pipeline"):
        output = _run_shadow(_AFTER_PLAN)

    lines = _section_lines(output)
    assert any(DISCLOSURE_ORIGINAL_MARKER in text for text in lines), (
        "지난 일정 표기가 본문에 안 붙었다면 진단 자체가 생길 수 없어 "
        f"이 시험이 아무것도 증명하지 못합니다: {lines}"
    )
    assert _recorded(caplog) == [{PAST_DATED_FUTURE_TENSE: 1}]


def test_기준일이_일정보다_앞서면_아무것도_기록하지_않는다(monkeypatch, caplog):
    """★ 무조건 기록이면 위 단정은 배선이 아니라 상수를 잰 것이 된다."""
    _patch_writer_sentence(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.features.composer.pipeline"):
        output = _run_shadow(_BEFORE_PLAN)

    lines = _section_lines(output)
    assert any(_PLANNED_SENTENCE in text for text in lines), (
        f"문장이 아예 빠졌다면 «기록 없음»이 다른 이유가 됩니다: {lines}"
    )
    assert not any(DISCLOSURE_ORIGINAL_MARKER in text for text in lines)
    assert _recorded(caplog) == []


def test_기록에_회사_원문_글자가_실리지_않는다(monkeypatch, caplog):
    """★ 진단 기록에 원문을 싣지 않는다는 계약 — 사유 이름과 개수만 남긴다."""
    _patch_writer_sentence(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.features.composer.pipeline"):
        _run_shadow(_AFTER_PLAN)

    messages = [
        record.getMessage()
        for record in caplog.records
        if hasattr(record, "pipeline_style_diagnostics")
    ]
    assert messages, "기록이 없으면 이 단정이 공전합니다"
    assert not any(_PLANNED_SENTENCE in message for message in messages)


# ══════════════════════════════════════════════════════════
# ⓒ 보충 회차 — 출고 렌더에서만 받는다
# ══════════════════════════════════════════════════════════


def test_보충_회차는_출고_렌더에서만_진단을_받는다(monkeypatch):
    """★ 중간 렌더에서도 받으면 같은 문장을 두 번 세어 개수가 부풀려진다."""
    from src.features.composer.tests.test_section_public_manifest import (
        _run_recovering_full,
    )

    original = pipeline.render_report
    받은_인자: list[object] = []

    def spy(*args, **kwargs):
        받은_인자.append(kwargs.get("style_diagnostics"))
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "render_report", spy)
    _run_recovering_full((_TARGET_SECTION,))

    assert len(받은_인자) == 4, (
        "보충 실행의 렌더 호출 수가 바뀌었습니다 — 이 시험이 어느 호출을 재는지 "
        f"다시 확인해야 합니다: {len(받은_인자)}"
    )
    # 1·3번째는 요약 후보를 고르려고 버리는 중간 렌더, 2·4번째가 출고 렌더다.
    assert [값 is None for 값 in 받은_인자] == [True, False, True, False]
    assert all(isinstance(값, dict) for 값 in 받은_인자[1::2])


# ══════════════════════════════════════════════════════════
# ⓓ 확보 근거 보고서(강등) 경로
# ══════════════════════════════════════════════════════════


def test_확보_근거_보고서_경로도_진단_인자를_받는다(monkeypatch):
    from src.features.composer.evidence_availability import EvidenceAvailability
    from src.features.composer.pipeline import compose_evidence_available_report

    original = pipeline.render_report
    받은_인자: list[object] = []

    def spy(*args, **kwargs):
        받은_인자.append(kwargs.get("style_diagnostics"))
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "render_report", spy)
    compose_evidence_available_report(
        "가나다전자",
        (),
        None,
        evidence_availability=EvidenceAvailability("none"),
        corp_type="상장사",
        company_id="00123456",
        as_of_date="2026-09-14",
    )

    assert 받은_인자 and all(isinstance(값, dict) for 값 in 받은_인자)


# ══════════════════════════════════════════════════════════
# 기록 자리 — 닫힌 «제외» 장부에 넣지 않는다
# ══════════════════════════════════════════════════════════


def test_시제_진단은_닫힌_제외_장부에_들어가지_않는다():
    """★ 「…개를 뺐습니다」를 세는 장부에 넣으면 안 뺀 문장을 뺐다고 말한다."""
    from src.shared.report_quality.review_diagnostic_constants import (
        REVIEW_SCOPE_ITEMS,
    )

    assert PAST_DATED_FUTURE_TENSE not in REVIEW_SCOPE_ITEMS


def test_진단이_비면_기록하지_않는다(caplog):
    """★ 「0건」 로그를 매 실행 남기면 운영 기록이 의미 없는 줄로 찬다."""
    with caplog.at_level(logging.INFO, logger="src.features.composer.pipeline"):
        pipeline._record_style_diagnostics({})

    assert _recorded(caplog) == []

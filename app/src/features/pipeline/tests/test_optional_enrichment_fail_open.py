"""보조 추가물(이름 조각·9장 자기 선언 승격)은 실패해도 보고서를 멈추지 않는다.

2026-09-06 운영 실측: 상장사 조사가 「시스템 내부 연결 문제」로 멈췄다. 보조 추가물은
없어도 되는 것이므로, 검사에 걸린 조각만 빼고(사유를 남기고) 보고서는 계속 만들어야 한다.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.features.pipeline import real
from src.features.pipeline.tests import test_product_name_fragments as names
from src.features.pipeline.tests import test_evidence_reclassify_e2e as e2e
from src.features.pipeline.tests.test_evidence_reclassify_e2e import (  # noqa: F401 - autouse 픽스처 등록
    _paid_provider_budget_context,
    _reset_reclassify_switch,
)
from src.features.pipeline.port import Outcome


def test_transport검사에_걸린_이름조각만_탈락하고_나머지는_붙는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_maker = real.name_candidate_fragments

    def corrupt_one(*args: Any, **kwargs: Any) -> list[dict[str, object]]:
        made = real_maker(*args, **kwargs)
        assert len(made) >= 2
        broken = dict(made[0])
        broken["_evidence_document_identity"] = "깨진-신원"
        return [broken, *made[1:]]

    monkeypatch.setattr(real, "name_candidate_fragments", corrupt_one)
    before = names._legacy_fragments()  # noqa: SLF001
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        dict(before),
        filing_text=names._kakao_text(),  # noqa: SLF001
        filing_meta=names._filing(),  # noqa: SLF001
        corp_id=names.CORP_ID,
        typed_fragments=(names._typed_anchor(),),  # noqa: SLF001
        steps=steps,
    )

    assert added >= 1
    assert all(
        raw.get("_evidence_document_identity") != "깨진-신원" for raw in frags.values()
    )
    step = steps[-1]
    assert step["step"] == "7_이름후보"
    assert step["조각"] == added
    rejected = step["탈락"]
    assert isinstance(rejected, dict) and sum(rejected.values()) == 1


def test_기본조각이_검사를_못넘으면_이름조각을_붙이지_않고_사유만_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = names._legacy_fragments()  # noqa: SLF001
    anchor = names._typed_anchor()  # noqa: SLF001
    poisoned = dict(anchor)
    poisoned["_evidence_document_identity"] = "깨진-기본-신원"
    before = {**before, max(before) + 1: poisoned}
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        dict(before),
        filing_text=names._kakao_text(),  # noqa: SLF001
        filing_meta=names._filing(),  # noqa: SLF001
        corp_id=names.CORP_ID,
        typed_fragments=(anchor,),
        steps=steps,
    )

    assert added == 0
    assert set(frags) == set(before)
    rejected = steps[-1]["탈락"]
    assert any(key.startswith("기본조각: ") for key in rejected)


def test_9장_자기선언_승격이_실패해도_보고서는_완성된다(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, observations = e2e._wire_pipeline(  # noqa: SLF001
        monkeypatch,
        reclassify_enabled=False,
    )
    collector = e2e._wire_audit_collector(  # noqa: SLF001
        monkeypatch, fixture_name=e2e._HIVE_FIXTURE  # noqa: SLF001
    )

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("competitive_position")

    monkeypatch.setattr(real, "add_stated_differentiator_fragments", explode)

    with caplog.at_level("WARNING", logger="src.features.pipeline.real"):
        result = e2e._run(collector)  # noqa: SLF001

    assert result.outcome is Outcome.REPORT
    assert any(
        "9장 자기 선언 승격을 건너뜁니다" in record.getMessage()
        and "KeyError" in record.getMessage()
        for record in caplog.records
    )

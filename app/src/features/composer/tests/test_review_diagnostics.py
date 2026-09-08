"""의미 근거 결속 실패 진단은 공개 후보 내용 없이 정확한 위치만 남긴다."""

from __future__ import annotations

import hashlib
import json
import logging

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.grounding_constants import (
    GROUNDING_INVALID,
    GROUNDING_MISSING,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.verify import verify_report, verify_sentences


_SOURCE = (
    "가나다전자는 반도체 검사 장비 전문기업이다. "
    "2024년 매출액은 168,312,345,678원이다."
)
_CANDIDATE = "2024년 매출액은 약 1,683억원이다."


def _sentence(text: str = _CANDIDATE) -> ComposedSentence:
    return ComposedSentence(
        text=text,
        citations=("1",),
        grade=GRADE_CONFIRMED,
    )


def _fragments() -> tuple[CollectedFragment, ...]:
    return (CollectedFragment(fragment_id="1", kind="사업내용", text=_SOURCE),)


def _flat_response(grounding: object = None, *, include: bool = False) -> str:
    entry: dict[str, object] = {"번호": 1, "결과": "참"}
    if include:
        entry["검증근거"] = grounding
    return json.dumps({"판정": [entry]}, ensure_ascii=False)


def _grouped_response(count: int) -> str:
    return json.dumps(
        {
            "판정": [
                {
                    "번호": number,
                    "장": "operations_partners",
                    "근거": ["1"],
                    "결과": "참",
                }
                for number in range(1, count + 1)
            ]
        },
        ensure_ascii=False,
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_flat_본문_결속누락은_원문없는_구조화진단으로_남는다(
    caplog,
) -> None:
    diagnostics: list[dict] = []
    report = ComposedReport(
        sections=(ComposedSection(section_id="identity", sentences=(_sentence(),)),)
    )

    with caplog.at_level(
        logging.INFO, logger="src.features.composer.verify"
    ):
        verified = verify_report(
            report,
            _fragments(),
            None,
            lambda _prompt: _flat_response(),
            diagnostics=diagnostics,
        )

    assert verified.sections[0].sentences == ()
    assert diagnostics == [
        {
            "section_id": "identity",
            "kind": "본문",
            "reason_code": GROUNDING_MISSING,
            "candidate_sha256": _digest(_CANDIDATE),
            "verification_items": ("수치",),
        }
    ]
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert _SOURCE not in serialized
    assert _CANDIDATE not in serialized
    assert "168,312,345,678" not in serialized
    assert "1,683" not in serialized
    assert "근거결속실패→제거 1" in caplog.text
    assert "응답에 번호없음→제거 0" in caplog.text


def test_verify_sentences는_요약의_잘못된_결속을_invalid로_구분한다() -> None:
    diagnostics: list[dict] = []
    invalid_grounding = {
        "수치": [
            {
                "표현": "2024년 매출액은 약 1,683억원",
                "항목": "영업이익",
                "근거": "1",
                "원문": _SOURCE,
                "원문항목": "매출액",
                "원문값": "168,312,345,678원",
            }
        ]
    }

    verified = verify_sentences(
        (_sentence(),),
        _fragments(),
        None,
        lambda _prompt: _flat_response(invalid_grounding, include=True),
        diagnostics=diagnostics,
    )

    assert verified == ()
    assert diagnostics == [
        {
            "section_id": "summary",
            "kind": "요약",
            "reason_code": GROUNDING_INVALID,
            "candidate_sha256": _digest(_CANDIDATE),
            "verification_items": ("수치",),
        }
    ]


def test_grouped_본문과_도식의_결속누락을_각각_기록한다() -> None:
    diagnostics: list[dict] = []
    flow_text = "원재료 매출액은 약 1,683억원 고객"
    report = ComposedReport(
        sections=(
            ComposedSection(
                section_id="operations_partners",
                sentences=(_sentence(),),
                flow_rows=(
                    FlowRow(
                        cells=("원재료", "매출액은 약 1,683억원", "고객"),
                        citations=("1",),
                    ),
                ),
            ),
        )
    )

    verified = verify_report(
        report,
        _fragments(),
        None,
        lambda _prompt: _grouped_response(2),
        allowed_fragment_ids_by_section={
            "operations_partners": frozenset({"1"})
        },
        diagnostics=diagnostics,
    )

    assert verified.sections[0].sentences == ()
    assert verified.sections[0].flow_rows == ()
    assert diagnostics == [
        {
            "section_id": "operations_partners",
            "kind": "본문",
            "reason_code": GROUNDING_MISSING,
            "candidate_sha256": _digest(_CANDIDATE),
            "verification_items": ("수치",),
        },
        {
            "section_id": "operations_partners",
            "kind": "도식",
            "reason_code": GROUNDING_MISSING,
            "candidate_sha256": _digest(flow_text),
            "verification_items": ("수치",),
        },
    ]


def test_legacy_도식도_같은_진단수집기를_쓴다() -> None:
    diagnostics: list[dict] = []
    row = FlowRow(
        cells=("원재료", "매출액은 약 1,683억원", "고객"),
        citations=("1",),
    )
    report = ComposedReport(
        sections=(
            ComposedSection(
                section_id="operations_partners",
                sentences=(),
                flow_rows=(row,),
            ),
        )
    )

    verified, problems = check_diagrams(
        report,
        _fragments(),
        lambda _prompt: _flat_response(),
        diagnostics=diagnostics,
    )

    assert verified.sections[0].flow_rows == ()
    assert any(GROUNDING_MISSING in problem for problem in problems)
    assert diagnostics == [
        {
            "section_id": "operations_partners",
            "kind": "도식",
            "reason_code": GROUNDING_MISSING,
            "candidate_sha256": _digest(
                "원재료 매출액은 약 1,683억원 고객"
            ),
            "verification_items": ("수치",),
        }
    ]


def test_정상판정은_기존_수집기내용을_건드리지_않는다() -> None:
    marker = {"existing": True}
    diagnostics: list[dict] = [marker]
    direct = ComposedSentence(
        text="가나다전자는 반도체 검사 장비 전문기업이다.",
        citations=("1",),
        grade=GRADE_CONFIRMED,
    )

    verified = verify_sentences(
        (direct,),
        _fragments(),
        None,
        lambda _prompt: _flat_response(),
        diagnostics=diagnostics,
    )

    assert verified == (direct.__class__(
        text=direct.text,
        citations=direct.citations,
        grade=direct.grade,
        verification_state="verified",
    ),)
    assert diagnostics == [marker]

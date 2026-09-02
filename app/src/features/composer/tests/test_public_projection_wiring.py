"""composer가 FULL 완주 보고서에 공개 봉인 projection을 «실제로» 싣는지 지킨다.

설계 017 §07 조각 S3. 지키는 불변식:

  I3 — 봉인은 공개의 전제다. builder가 실패하면 그 보고서는 나가지 않는다
       (예외를 삼키고 projection 없이 출고하면 안 된다).
  I7 — 웹·PDF·Notion이 같은 블록을 읽어야 한다. 그 블록은 «생성 시점에 한 번»
       만들어져 보고서에 실린다.
  I11 — 저장·출고가 한 벌이어야 한다. projection이 저장 왕복에서 살아남고
        생산 증거가 그 digest를 들고 있어야 그 한 벌이 성립한다.

★ 재료는 옆 파일 ``test_section_public_manifest.py``의 FULL 실행 도구를 그대로
  빌려 쓴다. 그쪽은 가짜 AI 응답으로 packet·검수·보충 회차를 «진짜 파이프라인
  으로» 통과시키는 유일한 도구다. 여기서 다시 지으면 두 벌이 갈라진다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.pipeline import run_v2
from src.features.composer.port import CollectedFragment
from src.features.composer.tests.test_section_public_manifest import (
    _MARKS,
    _NoDiagram,
    _run_full,
    _run_recovering_full,
)
from src.features.pipeline.port import Grade
from src.features.storage.reports import report_from_dict, report_to_dict
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    PublicProjectionError,
    build_report_digest,
)


# ══════════════════════════════════════════════════════════
# SHADOW 실행 도구 — legacy 요약·검수를 그대로 타는 평면 보고서
# ══════════════════════════════════════════════════════════


class _FlatWriter:
    """packet 없이 아홉 장 + legacy 요약 한 번을 답하는 가짜 작성자."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "핵심 요약" in prompt:
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": f"{mark} 회사 자료를 종합해 핵심을 확인했다.",
                            "인용": ["1"],
                            "등급": GRADE_CONFIRMED,
                        }
                        for mark in _MARKS[:3]
                    ]
                },
                ensure_ascii=False,
            )
        index = len(self.prompts) - 1
        section_id = SECTION_IDS[index]
        mark = _MARKS[index]
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": f"{mark} 회사의 공식 자료를 확인했다.",
                        "인용": ["1"],
                        "등급": GRADE_CONFIRMED,
                        "주장슬롯": CLAIM_SLOTS_BY_SECTION[section_id][0],
                    }
                ]
            },
            ensure_ascii=False,
        )


class _LegacyReviewer:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        numbers = [int(value) for value in re.findall(r"\[(\d+)\] \(", prompt)]
        return json.dumps(
            {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
            ensure_ascii=False,
        )


def _run_shadow():
    fragment = CollectedFragment(
        fragment_id="1",
        kind="회사 공식 자료",
        text=" ".join(f"{mark} 회사의 공식 자료를 확인했다." for mark in _MARKS),
        source_url="https://projection.example/flat",
    )
    return run_v2(
        "가나다전자",
        (fragment,),
        None,
        writer_ask=_FlatWriter(),
        reviewer_ask=_LegacyReviewer(),
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.SHADOW,
    )


# ══════════════════════════════════════════════════════════
# ① FULL 두 경로가 projection을 만든다
# ══════════════════════════════════════════════════════════


def test_FULL_완주_보고서는_projection을_가진다() -> None:
    output, _writer, _reviewer, _diagram = _run_full()

    projection = output.report.public_projection
    assert projection is not None
    assert projection.version == PUBLIC_PROJECTION_VERSION
    assert tuple(block.display.cell for block in projection.sections) == SECTION_IDS
    # 봉인 블록은 «화면에 나갈 글자»를 이미 들고 있어야 한다 — 렌더러가
    # 문단을 다시 쪼개지 않아도 되는 상태인지 여기서 본다.
    for block in projection.sections:
        assert block.display.paragraphs
        assert block.display.paragraphs[0][0] == "1."
    assert projection.citations


def test_보충_회복_경로도_projection을_만든다() -> None:
    output, _writer, _reviewer = _run_recovering_full(("identity",))

    projection = output.report.public_projection
    assert projection is not None
    assert tuple(block.display.cell for block in projection.sections) == SECTION_IDS


def test_SHADOW에서는_projection을_만들지_않는다(monkeypatch) -> None:
    """FULL 전용이다(F-S1a) — SHADOW는 표 ``manifest_ref``가 없어 봉인 불가."""

    calls: list[object] = []
    original = pipeline_module.build_public_projection

    def counted(report):
        calls.append(report)
        return original(report)

    monkeypatch.setattr(pipeline_module, "build_public_projection", counted)

    output = _run_shadow()

    assert calls == []
    assert output.report.public_projection is None
    assert "public_projection" not in report_to_dict(output.report)


# ══════════════════════════════════════════════════════════
# ② 봉인 실패 = 공개 불가 (I3)
# ══════════════════════════════════════════════════════════


def test_projection_봉인_실패는_출고를_막는다(monkeypatch) -> None:
    """builder가 닫으면 run_v2도 닫혀야 한다 — 예외를 삼키지 않는다.

    ★ 삼키면 무슨 일이 나나 — projection 없는 FULL 보고서가 그대로 출고되고,
      웹·PDF·Notion이 각자 문자열을 다시 만들어 채널이 갈라진다. 그 갈라짐은
      화면을 눈으로 보기 전에는 아무도 모른다.
    """

    def boom(_report):
        raise PublicProjectionError("봉인 실패를 흉내낸다")

    monkeypatch.setattr(pipeline_module, "build_public_projection", boom)

    with pytest.raises(PublicProjectionError):
        _run_full()


def test_projection_봉인_실패는_보충_경로에서도_출고를_막는다(monkeypatch) -> None:
    def boom(_report):
        raise PublicProjectionError("봉인 실패를 흉내낸다")

    monkeypatch.setattr(pipeline_module, "build_public_projection", boom)

    with pytest.raises(PublicProjectionError):
        _run_recovering_full(("identity",))


# ══════════════════════════════════════════════════════════
# ③ 봉인 시점 — «최종» 보고서를 봉인해야 한다
# ══════════════════════════════════════════════════════════


def test_projection_header는_최종_완성_등급을_싣는다() -> None:
    """봉인을 최종 판정 «앞»에서 하면 이 시험이 빨개진다.

    ``run_v2``의 grade 기본값은 ``Grade.PARTIAL``이고, 엄격 계약을 통과한
    보고서만 마지막에 ``Grade.COMPLETE``로 다시 봉인된다
    (``pipeline.py``의 「입력 grade의 옛 기본값 PARTIAL을 그대로 두면 …」 주석).
    그래서 첫 seal 단정 직후에 봉인하면 header에 「부분」이 박히고 부분 보고서
    고지 문구까지 딸려 들어가, 저장된 보고서는 「완성」인데 화면 블록만 「부분」
    이라고 말하는 보고서가 남는다. 봉인은 «최종» 상태에서 한 번 해야 한다.
    """

    output, _writer, _reviewer, _diagram = _run_full()

    report = output.report
    assert report.grade is Grade.COMPLETE
    projection = report.public_projection
    assert projection is not None
    assert projection.header["grade"] == "완성"
    assert projection.header["shortfall_reasons"] == []
    assert projection.header["publication_policy"] == report.publication_policy
    assert projection.header["safety_decision"] == report.safety_decision
    assert projection.header["release_mode"] == ReleaseMode.FULL.value
    # 완성 보고서에는 부분 보고서 고지가 붙지 않는다.
    assert projection.grade_notice == ("", "")


# ══════════════════════════════════════════════════════════
# ④ 저장 왕복 (I11)
# ══════════════════════════════════════════════════════════


def test_FULL_보고서의_projection은_저장_왕복에서_살아남는다() -> None:
    output, _writer, _reviewer, _diagram = _run_full()

    payload = report_to_dict(output.report)
    restored = report_from_dict(json.loads(json.dumps(payload, ensure_ascii=False)))

    assert restored.public_projection == output.report.public_projection
    assert build_report_digest(restored.public_projection) == build_report_digest(
        output.report.public_projection
    )

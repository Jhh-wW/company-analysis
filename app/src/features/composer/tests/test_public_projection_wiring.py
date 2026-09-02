"""composer가 FULL 완주 보고서에 공개 봉인 projection을 «실제로» 싣는지 지킨다.

지키는 불변식:

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

from dataclasses import replace

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
from src.features.storage import db, reports
from src.features.storage.reports import report_to_dict
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.features.report_standard.public_projection import build_public_projection
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    PublicProjectionError,
    PublicSectionContentBlock,
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
    """FULL 전용이다 — SHADOW는 표 ``manifest_ref``가 없어 봉인 불가."""

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
# ② 봉인 실패 = 공개 불가
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
# ④ 저장 왕복
# ══════════════════════════════════════════════════════════


def test_FULL_완주_보고서는_저장_왕복_뒤에도_같은_봉인을_갖는다(tmp_path) -> None:
    """진짜 SQLite에 넣었다 빼도 봉인이 그대로여야 한다.

    ★ 봉인은 보고서 payload가 아니라 별도 표에 저장한다 — payload에 넣으면
      저장 JSON이 두 배로 커진다. 그래서 dict 왕복이 아니라 «실제 저장·로드»를 태운다.
    """

    output, _writer, _reviewer, _diagram = _run_full()
    path = tmp_path / "reports.sqlite3"

    with db.connect(path) as conn:
        reports.save(conn, "r1", "00123456", "분석", output.report)
    with db.connect(path) as conn:
        loaded = reports.load(conn, "r1")

    assert loaded is not None
    assert loaded.public_projection == output.report.public_projection
    assert build_report_digest(loaded.public_projection) == build_report_digest(
        output.report.public_projection
    )
    # payload에는 봉인이 한 글자도 없다 — 그 계약은 storage 시험이 못 박는다.
    assert "public_projection" not in report_to_dict(output.report)


# ══════════════════════════════════════════════════════════
# ⑤ 생산 증거 결속 — 증거가 이 봉인의 지문을 들고 있어야 한다
# ══════════════════════════════════════════════════════════


def test_생성_증거의_projection_digest는_봉인된_projection과_같다() -> None:
    """증거가 「어떤 공개본을 냈는지」를 지문으로 지목한다.

    지문이 없으면 저장된 보고서의 블록을 나중에 갈아 끼워도 생산 증거가
    아무 말을 못 한다. ``content_sha256``은 화면(display)뿐 아니라 감사
    장부(ledger)까지 덮으므로, 장부만 바꾼 위조도 이 지문 하나로 드러난다.
    """

    output, _writer, _reviewer, _diagram = _run_full()

    evidence = output.generation_evidence
    projection = output.report.public_projection
    assert evidence is not None
    assert projection is not None
    assert (
        evidence.public_projection_sha256
        == build_report_digest(projection).content_sha256
    )
    assert output.report.generation_evidence is evidence
    # 지문 A(pre-render 공개 content 봉인)와는 «다른 값»이다 — 하나는 렌더
    # 이전 기대값, 다른 하나는 장부까지 덮는 최종 봉인이라 서로를 대신하지
    # 못한다. 같은 값이면 둘 중 하나가 의미를 잃은 것이다.
    assert evidence.public_projection_sha256 != evidence.public_content_sha256


def test_projection_내용이_바뀌면_증거_digest도_함께_바뀐다() -> None:
    """digest가 내용에 «실제로» 반응하는지 본다(상수 비교가 아님)."""

    plain, _w1, _r1, _d1 = _run_full()
    with_flow, _w2, _r2, _d2 = _run_full(flow=True)

    assert plain.report.public_projection != with_flow.report.public_projection
    assert (
        plain.generation_evidence.public_projection_sha256
        != with_flow.generation_evidence.public_projection_sha256
    )
    for output in (plain, with_flow):
        assert (
            output.generation_evidence.public_projection_sha256
            == build_report_digest(output.report.public_projection).content_sha256
        )


def test_보충_회복_보고서의_증거도_최종_projection을_가리킨다() -> None:
    output, _writer, _reviewer = _run_recovering_full(("identity",))

    evidence = output.generation_evidence
    assert evidence is not None
    assert (
        evidence.public_projection_sha256
        == build_report_digest(output.report.public_projection).content_sha256
    )


# ★ 아래 두 시험은 storage 층으로 옮겼다 — 봉인이
#   payload가 아니라 별도 표에 저장되므로 위조도 그 표에서 해야 실제 경로를 탄다.
#     · 「다른 실행 봉인 바꿔치기」 →
#       storage/tests/test_public_projection_storage.py::
#       test_저장된_projection이_생성_증거의_지문과_다르면_로드가_거부된다
#     · 「FULL인데 봉인이 없음」 → 같은 파일::
#       test_FULL인데_projection_행이_없으면_봉인_없음_상태로_읽힌다
#       ★ 이건 «뒤집혔다». 예전에는 거부였는데, 봉인 없음을 예외로 만들면 옛
#         저장본이 화면에서 통째로 안 열린다. 이제는 정의된 상태로 두고 화면이
#         판단한다.


# ══════════════════════════════════════════════════════════
# ⑥ 보충 불변식 — 바뀐 장 «만» 바뀐다 (장부 포함)
# ══════════════════════════════════════════════════════════


def test_보충뒤_바뀐_장만_block_digest가_바뀐다(monkeypatch) -> None:
    """보충(1회) 뒤 비대상 장은 «감사 장부까지» 한 글자도 안 바뀐다.

    ★ 기존 receipt의 ``section_sha256s``로는 이걸 다 말할 수 없다 — 그 값은
      pre-render 공개 content 봉인(지문 A)에서 오고 지문 A는 «보이는 것»만
      덮는다. 보충 회차에서 비대상 장의 FactRecord나 등급 기여가 조용히
      바뀌어도 지문 A는 그대로다. ``block_sha256``은 display와 ledger를 함께
      덮으므로 그 구멍을 닫는다(보충 결속 불변식).

    ★ 왜 primary 시점 보고서를 가로채나 — 최종 결과만으로는 「보충 전에는
      어땠는지」를 알 수 없다. 품질 후보를 만들 때 넘어오는 렌더 결과가 그
      회차의 완성된 보고서라 그 자리에서 붙잡는다.
    """

    target = "identity"
    captured: list[object] = []
    original = pipeline_module.build_generation_quality_candidate

    def capture(rendered, composed):
        captured.append(rendered)
        return original(rendered, composed)

    monkeypatch.setattr(
        pipeline_module,
        "build_generation_quality_candidate",
        capture,
    )

    output, _writer, _reviewer = _run_recovering_full((target,))

    assert len(captured) == 2, "보충은 정확히 한 번, 즉 회차는 두 번이다"
    primary_projection = build_public_projection(captured[0])
    final_projection = output.report.public_projection
    assert final_projection is not None

    primary_blocks = {
        block.display.cell: block for block in primary_projection.sections
    }
    final_blocks = {block.display.cell: block for block in final_projection.sections}

    assert (
        primary_blocks[target].block_sha256 != final_blocks[target].block_sha256
    ), "보충한 장은 실제로 바뀌어야 한다"

    for cell in SECTION_IDS:
        if cell == target:
            continue
        assert primary_blocks[cell].block_sha256 == final_blocks[cell].block_sha256
        assert primary_blocks[cell].display_sha256 == final_blocks[cell].display_sha256
        # dataclass 동치까지 본다 — 지문이 같아도 내용이 다르면(충돌) 여기서
        # 갈린다. 장부는 화면에 안 보이므로 «따로» 못 박는다.
        assert primary_blocks[cell].ledger == final_blocks[cell].ledger
        assert primary_blocks[cell].display == final_blocks[cell].display

    # digest의 장별 목록도 같은 이야기를 해야 한다.
    primary_digest = dict(build_report_digest(primary_projection).section_sha256s)
    final_digest = dict(build_report_digest(final_projection).section_sha256s)
    assert primary_digest[target] != final_digest[target]
    assert {
        cell: digest for cell, digest in primary_digest.items() if cell != target
    } == {cell: digest for cell, digest in final_digest.items() if cell != target}


def test_보충_비대상_장의_장부를_건드리면_block_digest가_빨개진다() -> None:
    """위 시험이 «지켜 준다»는 말이 참인지 보는 반대 경우 시험.

    비대상 장의 장부만 한 칸 바꾼 projection을 만들어, 그 장의
    ``block_sha256``이 실제로 달라지는지 확인한다. 안 달라지면 위 시험은
    아무것도 안 지키는 것이다.
    """

    output, _writer, _reviewer, _diagram = _run_full()
    projection = output.report.public_projection
    assert projection is not None

    victim = projection.sections[1]
    contribution = victim.ledger.source_grade_contribution
    assert contribution, "장부 위조를 하려면 등급 기여가 있어야 한다"
    number, grades = contribution[0]
    swapped = "해석" if grades[0] != "해석" else "확인"
    forged_ledger = replace(
        victim.ledger,
        source_grade_contribution=((number, (swapped,)),) + contribution[1:],
    )
    forged_block = PublicSectionContentBlock(
        version=victim.version,
        display=victim.display,
        ledger=forged_ledger,
    )

    assert forged_block.display_sha256 == victim.display_sha256
    assert forged_block.block_sha256 != victim.block_sha256


# ══════════════════════════════════════════════════════════
# ⑦ 영수증의 장별 블록 지문
# ══════════════════════════════════════════════════════════


def test_영수증의_장별_블록_지문은_저장된_봉인과_같다() -> None:
    """지문 계산용 projection과 저장되는 봉인이 갈라지지 않았음을 못 박는다.

    ★ 왜 갈라질 수 있나 — 영수증용 지문은 렌더 직후에, 저장되는 봉인은 등급을
      완성으로 다시 봉인한 «뒤»에 만든다. 두 시점의 header는 다르지만 장
      블록(display+ledger)은 같아야 한다. 이게 깨지면 영수증이 가리키는 장과
      화면에 나가는 장이 다른 보고서가 된다.
    """

    output, _writer, _reviewer, _diagram = _run_full()

    projection = output.report.public_projection
    assert projection is not None
    stored = tuple(
        (block.display.cell, block.block_sha256) for block in projection.sections
    )
    evidence = output.generation_evidence
    assert evidence is not None
    for receipt in evidence.validation_receipts:
        assert receipt.section_block_sha256s
    assert evidence.validation_receipts[-1].section_block_sha256s == stored


def test_보충_영수증의_블록_지문도_최종_봉인과_같다() -> None:
    output, _writer, _reviewer = _run_recovering_full(("identity",))

    projection = output.report.public_projection
    assert projection is not None
    stored = tuple(
        (block.display.cell, block.block_sha256) for block in projection.sections
    )
    evidence = output.generation_evidence
    assert evidence is not None
    primary, supplement = evidence.validation_receipts
    assert supplement.section_block_sha256s == stored
    # 보충한 장만 달라지고 나머지는 그대로 — 생산 영수증에서도 같은 이야기다.
    base = dict(primary.section_block_sha256s)
    result = dict(supplement.section_block_sha256s)
    assert base["identity"] != result["identity"]
    assert {k: v for k, v in base.items() if k != "identity"} == {
        k: v for k, v in result.items() if k != "identity"
    }

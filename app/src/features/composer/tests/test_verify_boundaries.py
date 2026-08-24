"""엔진 v2 검증기 경계 시험 (실행계획 05장 소단계 4-B) — 무과금.

★ 지키는 경계: 검증의 처분은 «문장 단위»뿐이다 — 깨진 인용·틀린 숫자·검수
  거짓은 그 문장만 제거/강등하고, 나머지 문장·장·보고서는 살아서 렌더와
  출고 검증(validate_v2)까지 도달한다. 어떤 조합에서도 「보고서 전체 차단」은
  없다. validate_v2의 3검사 실패만 예외인데(그건 정당한 fail-closed),
  그 경로는 test_pipeline.py의 «본문이_통째로_비면» 시험이 이미 못 박았으므로
  여기서 중복하지 않는다.
★ test_verify.py와의 역할 구분 — 그쪽은 verify_report 단품에 작은 합성
  보고서를 넣어 처분 규칙 하나하나를 보고, 여기는 골든 샘플 규모의 fixture로
  composer 전체(run_v2)를 돌려 처분이 «보고서 수준으로 번지지 않음»을 본다.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Final, Optional, Sequence

from src.features.composer.constants import SECTION_GUIDES, SECTION_IDS
from src.features.composer.diagram_check import FLOW_REVIEW_PROMPT_HEADER
from src.features.composer.logic import AskFn, SUMMARY_PROMPT_HEADER
from src.features.composer.pipeline import V2RunOutput, run_v2
from src.features.composer.render import (
    ENGINE_V2_SCHEMA_VERSION,
    INTERPRETATION_MARKER,
)
from src.features.composer.verify import (
    REVIEW_PROMPT_HEADER,
    REWRITE_PROMPT_HEADER,
)
from src.features.pipeline.port import Report

COMPANY_NAME: Final[str] = "제이와이피엔터테인먼트"

_FIXTURE_DIR: Final[Path] = Path(__file__).resolve().parent / "fixtures"
_FRAGMENTS_FIXTURE: Final[dict[str, Any]] = json.loads(
    (_FIXTURE_DIR / "jyp_fragments.json").read_text(encoding="utf-8")
)
_RESPONSES_FIXTURE: Final[dict[str, Any]] = json.loads(
    (_FIXTURE_DIR / "jyp_ask_responses.json").read_text(encoding="utf-8")
)

#: 검수 프롬프트의 대조 항목 한 줄 — 번호와 문장 원문을 같이 읽는다
#: (verify._build_review_prompt가 만드는 「[n] (인용: …)\\n  문장: …」 모양)
_REVIEW_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"\[(\d+)\] \(인용: [^\n]*\)\n  문장: (.+)"
)


def _fixture_fragments() -> dict[int, dict[str, str]]:
    """fixture JSON을 real.py 원시 조각 dict[int, dict] 모양으로 바꾼다."""
    return {
        int(number): dict(fields)
        for number, fields in _FRAGMENTS_FIXTURE.items()
        if number.isdigit()
    }


def _golden_sections() -> dict[str, Any]:
    """장별 골든 응답의 깊은 복사본 — 시험이 문장 하나만 바꿔치기한다."""
    return copy.deepcopy(_RESPONSES_FIXTURE["장별_응답"])


def _expected_total() -> int:
    """fixture가 약속한 초안 문장 수 (본문 9장 + 요약) — 매직 넘버 대신 실측."""
    body = sum(
        len(payload["문장들"])
        for payload in _RESPONSES_FIXTURE["장별_응답"].values()
    )
    return body + len(_RESPONSES_FIXTURE["핵심요약_응답"]["문장들"])


#: 골든 fixture 자체에 «같은 사실이 두 장에 든» 대목이 하나 있다 — 1장이 쓴
#: 회사 표어를 8장이 다시 쓴다. 정본 §4에서 공식 가치는 8장 소유이므로
#: 장 간 중복 제거(dedupe)가 1장 쪽 한 문장을 8장으로 모은다. 그 결과 최종
#: 문장 수가 초안보다 «이만큼 더» 줄어든다. 검수와 무관한 감소분이라
#: 검수 시험의 기대값에서 따로 뺀다.
DEDUPE_MOVED_IN_FIXTURE: int = 1

#: 그 한 문장이 어느 장에서 빠지는가 — 1장이 쓴 회사 표어가 8장으로 간다.
#: 장별 문장 수를 단정하는 곳에서 이 값을 빼 준다.
#: 장 간 중복 제거가 «소유 장»으로 옮기는 문장 수 (소실이 아니라 이동이다).
#: fixture가 회사 표어를 1장과 8장에 둘 다 실었다 → 8장이 소유한다.
DEDUPE_MOVED_BY_SECTION: dict[str, int] = {"identity": 1}


# ══════════════════════════════════════════════════════════
# 가짜 작가·검수 — AI·네트워크 호출 0회
# ══════════════════════════════════════════════════════════


class _GoldenWriter:
    """fixture 응답을 돌려주는 가짜 작가 — 시험이 장별 응답을 바꿔칠 수 있다."""

    def __init__(self, sections: Optional[dict[str, Any]] = None) -> None:
        self._sections = (
            sections if sections is not None else _golden_sections()
        )

    def __call__(self, prompt: str) -> str:
        if SUMMARY_PROMPT_HEADER in prompt:
            return json.dumps(
                _RESPONSES_FIXTURE["핵심요약_응답"], ensure_ascii=False
            )
        for section_id in SECTION_IDS:
            if SECTION_GUIDES[section_id] in prompt:
                return json.dumps(self._sections[section_id], ensure_ascii=False)
        raise AssertionError("작가가 알 수 없는 프롬프트를 받았다")


class _ScriptedReviewer:
    """대조 문장 «원문»을 보고 판정하는 가짜 검수.

    시험이 지정한 문장만 «거짓», 나머지는 전부 «참»이다. 재작성 요청에는
    시험이 준 고친 문장을 돌려준다 — 재검수 프롬프트에는 고친 문장이
    실리므로(원문과 달라) 자연히 «참»으로 판정된다.
    """

    def __init__(
        self, false_texts: Sequence[str] = (), rewrite_text: str = ""
    ) -> None:
        self._false_texts = frozenset(false_texts)
        self._rewrite_text = rewrite_text
        self.review_prompts: list[str] = []
        self.rewrite_prompts: list[str] = []
        self.flow_review_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if REWRITE_PROMPT_HEADER in prompt:
            self.rewrite_prompts.append(prompt)
            return self._rewrite_text
        if FLOW_REVIEW_PROMPT_HEADER in prompt:
            # ★ 도식 검수(v2-27)는 같은 검수 클로저를 쓰지만 «다른» 물음이다.
            #   이 시험들은 «문장» 판정 경계를 소유하므로 도식은 손대지 않는다.
            #   여기서 판정을 안 돌려주면 도식 검증이 「검수 불능」으로 보고
            #   경로를 그대로 남긴다 — 이 시험의 관심사가 아니다.
            self.flow_review_prompts.append(prompt)
            return ""
        assert REVIEW_PROMPT_HEADER in prompt, "검수가 알 수 없는 프롬프트를 받았다"
        self.review_prompts.append(prompt)
        verdicts = [
            {
                "번호": int(number),
                "결과": "거짓" if text.strip() in self._false_texts else "참",
            }
            for number, text in _REVIEW_ITEM_RE.findall(prompt)
        ]
        return json.dumps({"판정": verdicts}, ensure_ascii=False)


class _DeadReviewer:
    """호출마다 죽는 가짜 검수 — «검수 AI 완전 불능» 상황."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("검수 AI 불능 리허설")


def _run(writer: _GoldenWriter, reviewer: AskFn) -> V2RunOutput:
    """composer 전체(run_v2)를 실적표 없이 한 번 돌린다."""
    return run_v2(
        COMPANY_NAME,
        _fixture_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )


def _section_texts(report: Report, section_id: str) -> list[str]:
    """장 하나의 렌더 본문 텍스트 목록."""
    section = next(item for item in report.sections if item.cell == section_id)
    return [text for text, _cite in section.prose_lines]


def _assert_other_sections_intact(
    report: Report, sections: dict[str, Any], touched_section_id: str
) -> None:
    """건드린 장 밖의 모든 장이 초안 문장 수 그대로 생존했는지 단정한다.

    ★ 장 간 중복 제거로 «다른 장으로 옮겨간» 문장은 검수가 지운 것이 아니므로
      기대값에서 빼 준다. 옮김은 소실이 아니다 — 그 문장은 소유 장에 그대로 있다.
    """
    for section_id in SECTION_IDS:
        if section_id == touched_section_id:
            continue
        expected = len(sections[section_id]["문장들"]) - DEDUPE_MOVED_BY_SECTION.get(
            section_id, 0
        )
        assert len(_section_texts(report, section_id)) == expected, section_id


# ══════════════════════════════════════════════════════════
# 4-B-1. 깨진 인용 1개 → 그 문장만 제거
# ══════════════════════════════════════════════════════════


def test_깨진_인용_문장만_제거되고_나머지는_전부_생존한다() -> None:
    sections = _golden_sections()
    target = sections["identity"]["문장들"][1]
    target["인용"] = ["99"]  # 수집 목록에 없는 조각 id
    broken_text = str(target["글"])

    output = _run(_GoldenWriter(sections), _ScriptedReviewer())
    report = output.report

    # 그 문장만 사라졌다
    identity_texts = _section_texts(report, "identity")
    assert len(identity_texts) == len(sections["identity"]["문장들"]) - 1 - DEDUPE_MOVED_BY_SECTION["identity"]
    assert all(broken_text not in text for text in identity_texts)
    # 나머지 장·요약·보고서는 그대로 살아 렌더·출고 검증까지 도달했다
    _assert_other_sections_intact(report, sections, "identity")
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert output.composed_sentences == _expected_total()
    assert output.verified_sentences == _expected_total() - 1 - DEDUPE_MOVED_IN_FIXTURE


# ══════════════════════════════════════════════════════════
# 4-B-2. 틀린 숫자 1개 → 그 문장만 제거(단위 숫자) 또는 강등(맨 숫자)
# ══════════════════════════════════════════════════════════


def test_틀린_단위_숫자_문장만_제거되고_장은_생존한다() -> None:
    sections = _golden_sections()
    wrong = sections["business_model"]["문장들"][2]
    # 인용한 조각 2 원문에 없는 단위 숫자 — 금액·비율 주장 자체가 틀린 경우
    wrong["글"] = "2025년 수출 매출 비중은 99.9%다."

    output = _run(_GoldenWriter(sections), _ScriptedReviewer())
    report = output.report

    business_texts = _section_texts(report, "business_model")
    assert len(business_texts) == len(sections["business_model"]["문장들"]) - 1
    assert all("99.9%" not in text for text in business_texts)
    # 같은 장의 다른 «확인» 문장(8,219억 원)은 강등 없이 생존했다
    survivors = [text for text in business_texts if "8,219억" in text]
    assert survivors
    assert all(
        not text.endswith(INTERPRETATION_MARKER) for text in survivors
    )
    _assert_other_sections_intact(report, sections, "business_model")
    assert output.verified_sentences == _expected_total() - 1 - DEDUPE_MOVED_IN_FIXTURE


def test_틀린_맨_숫자_문장은_제거가_아니라_해석_강등이고_장은_생존한다() -> None:
    sections = _golden_sections()
    wrong = sections["portfolio"]["문장들"][3]
    # 인용한 조각 10 원문에 없는 맨 숫자(연도) — 서술의 부수 정보가 틀린 경우
    wrong["글"] = "ITZY·NMIXX는 2031년 기준 차세대 그룹으로 성장 단계에 있다."

    output = _run(_GoldenWriter(sections), _ScriptedReviewer())
    report = output.report

    portfolio_texts = _section_texts(report, "portfolio")
    # 문장 수 그대로 — 제거가 아니다
    assert len(portfolio_texts) == len(sections["portfolio"]["문장들"])
    demoted = [text for text in portfolio_texts if "2031년" in text]
    assert len(demoted) == 1
    assert demoted[0].endswith(INTERPRETATION_MARKER)
    _assert_other_sections_intact(report, sections, "portfolio")
    assert output.verified_sentences == _expected_total() - DEDUPE_MOVED_IN_FIXTURE


# ══════════════════════════════════════════════════════════
# 4-B-3. 검수 «거짓» → 재작성 1회 → 재검수 경로
# ══════════════════════════════════════════════════════════


def test_검수_거짓_문장은_재작성_1회와_재검수를_거쳐_확인으로_남는다() -> None:
    target_text = str(
        _RESPONSES_FIXTURE["장별_응답"]["culture"]["문장들"][0]["글"]
    )
    rewritten_text = (
        "회사는 공식 표어로 'Think Brilliant, Act Efficient!'를 제시한다."
    )
    reviewer = _ScriptedReviewer(
        false_texts=(target_text,), rewrite_text=rewritten_text
    )

    output = _run(_GoldenWriter(), reviewer)
    report = output.report

    # 재작성은 정확히 1회 — 프롬프트에 불합격 문장과 인용 근거 원문이 실렸다
    assert len(reviewer.rewrite_prompts) == 1
    assert target_text in reviewer.rewrite_prompts[0]
    assert _FRAGMENTS_FIXTURE["8"]["원문"] in reviewer.rewrite_prompts[0]
    # 검수는 본문 → 재검수 → 요약 순서로 3회 — 재검수 대상은 고친 문장이다
    assert len(reviewer.review_prompts) == 3
    assert rewritten_text in reviewer.review_prompts[1]
    # 문장은 제거되지 않고 고쳐진 «확인»으로 남았다 — 장·보고서 생존
    culture_texts = _section_texts(report, "culture")
    assert len(culture_texts) == len(
        _RESPONSES_FIXTURE["장별_응답"]["culture"]["문장들"]
    )
    assert any(
        text.startswith(rewritten_text)
        and not text.endswith(INTERPRETATION_MARKER)
        for text in culture_texts
    )
    assert all(target_text not in text for text in culture_texts)
    assert output.verified_sentences == _expected_total() - DEDUPE_MOVED_IN_FIXTURE


# ══════════════════════════════════════════════════════════
# 4-B-4. 검수 AI 완전 불능 → «확인» 전원 해석 강등 (제거 아님, 예외 무전파)
# ══════════════════════════════════════════════════════════


def test_검수가_완전_불능이면_확인_전원이_해석_강등되고_예외는_새지_않는다() -> None:
    reviewer = _DeadReviewer()

    # 검수가 호출마다 죽어도 run_v2는 예외 없이 보고서를 돌려줘야 한다
    output = _run(_GoldenWriter(), reviewer)
    report = output.report

    assert reviewer.calls >= 2  # 실제로 검수를 불렀고, 그때마다 죽었다
    # 제거된 문장이 하나도 없다 — 강등이지 차단이 아니다
    assert output.verified_sentences == _expected_total() - DEDUPE_MOVED_IN_FIXTURE
    for section_id in SECTION_IDS:
        texts = _section_texts(report, section_id)
        expected = len(
            _RESPONSES_FIXTURE["장별_응답"][section_id]["문장들"]
        ) - DEDUPE_MOVED_BY_SECTION.get(section_id, 0)
        assert len(texts) == expected, section_id
        # 검증 못 한 문장을 «확인»으로 내보내지 않는다 — 전 문장 해석 표지
        assert all(text.endswith(INTERPRETATION_MARKER) for text in texts)
    assert report.summary_items
    assert all(
        item.text.endswith(INTERPRETATION_MARKER)
        for item in report.summary_items
    )


# ══════════════════════════════════════════════════════════
# 4-B-5. 역경이 겹쳐도 «보고서 전체 차단»은 없다
# ══════════════════════════════════════════════════════════


def test_역경_조합에서도_보고서_전체_차단은_없다() -> None:
    """깨진 인용 + 틀린 단위 숫자 + 검수 불능이 겹쳐도 문장 단위 처분뿐이다."""
    sections = _golden_sections()
    sections["identity"]["문장들"][1]["인용"] = ["99"]
    sections["business_model"]["문장들"][2]["글"] = (
        "2025년 수출 매출 비중은 99.9%다."
    )

    output = _run(_GoldenWriter(sections), _DeadReviewer())
    report = output.report

    # 보고서는 렌더와 출고 검증(validate_v2)까지 통과해 나왔다 — 전체 차단 없음
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    # 처분은 딱 두 문장(제거 2) — 나머지는 강등으로 전부 생존했다
    assert output.composed_sentences == _expected_total()
    assert output.verified_sentences == _expected_total() - 2 - DEDUPE_MOVED_IN_FIXTURE
    assert len(_section_texts(report, "identity")) == -DEDUPE_MOVED_BY_SECTION[
        "identity"
    ] + len(
        sections["identity"]["문장들"]
    ) - 1
    assert len(_section_texts(report, "business_model")) == len(
        sections["business_model"]["문장들"]
    ) - 1
    assert len(report.summary_items) >= 3

# -*- coding: utf-8 -*-
"""근거 결속 탈락 «확인» 문장을 묶어 고쳐 쓰고 되살리는 단계를 못 박는다.

★ 여기서 지키는 것:
  ① 끄면 예전과 «완전히» 같다 — 탈락 문장은 제거되고 AI 호출도 늘지 않는다.
  ② 켜면 묶음 재작성 1회 + 재검수 1회다. 거짓 재작성이 함께 돌아도 재검수는
     여전히 «총 1회»다(호출이 2회 늘지 않는다).
  ③ 고쳐 쓴 글도 그냥 믿지 않는다 — 수치 검사와 근거 결속 재검사를 다시 받는다.
  ④ 형식 실패·예산 부족은 «전부 제거»로 닫히고, 그 사실이 진단에 남는다.
  ⑤ 진단 기록의 칸 이름은 공유 상수와 «글자까지» 같다(저장 정화기가 닫힌
     목록으로 거르기 때문에 한 글자만 달라도 그 칸이 통째로 사라진다).
  ⑥ 평문·packet «두» 경로에서 다 돈다. 운영 FULL 이 타는 쪽은 packet 이라,
     평문만 배선하면 시험은 초록인데 운영 효과는 0이다.

⚠️ 여기서는 가짜 호출자로 «실제» `verify_report` 를 돌린다. 검증기 안쪽 함수를
   직접 부르면 배선이 끊겨도 초록불이 된다.
"""

from __future__ import annotations

import contextlib
import json
import re

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.grounding_rewrite_constants import (
    GROUNDING_REWRITE_MAX_SENTENCES,
    GROUNDING_REWRITE_PROMPT_HEADER,
    GROUNDING_REWRITE_REASON_TEXTS,
    GROUNDING_REWRITE_RETRY_GUIDE,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.prose_own_source_constants import (
    PROSE_OWN_SOURCE_UNSUPPORTED,
)
from src.features.composer.verify import (
    REWRITE_PROMPT_HEADER,
    VERDICT_FALSE,
    VERDICT_TRUE,
    VERDICT_UNCLEAR,
    verify_report,
)
from src.shared.report_quality.composition_diagnostic_constants import (
    GROUNDING_REWRITE_COUNT_KEYS,
    GROUNDING_REWRITE_STATE_CALL_ABORTED,
    GROUNDING_REWRITE_STATE_DONE,
    GROUNDING_REWRITE_STATE_FORMAT_FAILED,
    GROUNDING_REWRITE_STEP,
)

# ══════════════════════════════════════════════════════════
# 시험 재료
# ══════════════════════════════════════════════════════════
#
# ★ 「근거 결속 탈락」을 «실제로» 만드는 방법 — 인용은 실존하고 수치도 없지만
#   그 문장이 스스로 단 인용 원문과 낱말이 거의 겹치지 않는 «확인» 산문이다.
#   그러면 `prose_own_source_problem` 이 걸려 판정이 REVIEW_GROUNDING_REJECTED
#   로 바뀐다(사유 코드 `prose_own_source_unsupported`). 회사 이름·숫자를 넣지
#   않아 다른 가드가 먼저 걸리지 않는다.

#: 조각 원문 — 고쳐 쓴 문장이 «기댈 수 있는» 사실이 들어 있다.
_SOURCES = {
    1: "가람전자의 본사는 경기도 성남시에 있습니다.",
    2: "나래산업의 주요 거래처는 국내 완성차 회사입니다.",
    3: "다온소재의 공장은 충청북도 청주시에 있습니다.",
    4: "마루기업의 지난해 매출액은 120억원입니다.",
}
#: 그 조각을 인용했지만 원문이 뒷받침하지 않는 «확인» 문장.
_REJECTED = {
    1: "미르전자는 위성 통신 장비를 수출한다.",
    2: "바람테크는 항공 부품을 설계한다.",
    3: "사랑물산은 해운 사업을 운영한다.",
    4: "아람통상은 목재를 유통한다.",
}
#: 작가가 «제대로» 고쳐 쓴 문장 — 인용 원문에 그대로 있는 사실만 남겼다.
_FIXED = {
    1: "가람전자의 본사는 경기도 성남시에 있다.",
    2: "나래산업의 주요 거래처는 국내 완성차 회사이다.",
    3: "다온소재의 공장은 충청북도 청주시에 있다.",
}


def _raw_fragments(numbers=(1,)) -> dict[int, dict[str, str]]:
    return {
        number: {"종류": "사업내용", "원문": _SOURCES[number]}
        for number in numbers
    }


def _report(numbers=(1,), extra: tuple[ComposedSentence, ...] = ()) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=tuple(
                    ComposedSentence(
                        text=_REJECTED[number],
                        citations=(str(number),),
                        grade=GRADE_CONFIRMED,
                    )
                    for number in numbers
                )
                + extra,
            ),
        ),
        summary=(),
    )


def _verdicts(results: dict[int, str]) -> str:
    return json.dumps(
        {"판정": [{"번호": number, "결과": result}
                 for number, result in results.items()]},
        ensure_ascii=False,
    )


def _rewrites(rows: list[dict[str, object]]) -> str:
    return json.dumps({"문장들": rows}, ensure_ascii=False)


class _FakeAI:
    """세 종류의 프롬프트(검수·거짓재작성·묶음재작성)를 갈라 답하는 가짜 AI.

    머리말로 가른다 — 프롬프트 길이·호출 순번으로 짐작하면 배선이 바뀔 때
    조용히 다른 답을 주게 된다.
    """

    def __init__(
        self,
        review_responses: list[str],
        *,
        batch_responses: list[object] | None = None,
        rewrite_responses: list[object] | None = None,
        grouped_auto: bool = False,
    ):
        self.review_responses = list(review_responses)
        self.batch_responses = list(batch_responses or [])
        self.rewrite_responses = list(rewrite_responses or [])
        #: 참이면 묶음(packet) 검수 프롬프트를 알아보고 전부 «참»으로 답한다.
        #: 그 호출은 `grouped_prompts` 에만 센다 — 평문 검수와 «따로» 세야
        #: 「어느 경로가 실제로 돌았나」를 시험이 가릴 수 있다.
        self.grouped_auto = grouped_auto
        self.review_prompts: list[str] = []
        self.grouped_prompts: list[str] = []
        self.batch_prompts: list[str] = []
        self.rewrite_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if prompt.startswith(GROUNDING_REWRITE_PROMPT_HEADER):
            self.batch_prompts.append(prompt)
            return self._next(self.batch_responses, len(self.batch_prompts))
        if prompt.startswith(REWRITE_PROMPT_HEADER):
            self.rewrite_prompts.append(prompt)
            return self._next(self.rewrite_responses, len(self.rewrite_prompts))
        if self.grouped_auto and _GROUPED_LINE_RE.search(prompt):
            self.grouped_prompts.append(prompt)
            return _grouped_all_true(prompt)
        self.review_prompts.append(prompt)
        return self._next(self.review_responses, len(self.review_prompts))

    @staticmethod
    def _next(responses: list[object], used: int) -> str:
        if used > len(responses):
            return ""  # 준비된 답이 떨어지면 빈 답(파싱 실패로 흐른다)
        answer = responses[used - 1]
        if isinstance(answer, BaseException):
            raise answer
        return str(answer)

    @property
    def total_calls(self) -> int:
        return (
            len(self.review_prompts)
            + len(self.grouped_prompts)
            + len(self.batch_prompts)
            + len(self.rewrite_prompts)
        )


#: packet(묶음) 검수 프롬프트의 후보 줄 — `verify._build_grouped_review_prompt` 모양.
_GROUPED_LINE_RE = re.compile(
    r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)"
)


def _grouped_all_true(prompt: str) -> str:
    """묶음 검수 프롬프트에 실린 번호 전부를 «참»으로 답한다.

    ★ 프롬프트를 읽어서 답한다 — 번호를 시험에 박으면 후보 수가 달라질 때
      조용히 범위 밖이 되어 배선이 끊겨도 초록불이 된다.
    """
    rows = _GROUPED_LINE_RE.findall(prompt)
    return json.dumps(
        {"판정": [{"번호": int(number), "장": section_id,
                 "근거": re.findall(r"조각 (\d+)", citations), "결과": VERDICT_TRUE}
                for number, section_id, _kind, citations in rows]},
        ensure_ascii=False,
    )


def _record(protocol: list[dict]) -> dict | None:
    found = [item for item in protocol if item.get("step") == GROUNDING_REWRITE_STEP]
    assert len(found) <= 1, found
    return found[0] if found else None


def _texts(report: ComposedReport) -> list[str]:
    return [sentence.text for sentence in report.sections[0].sentences]


# ══════════════════════════════════════════════════════════
# ① 끄면 예전과 같다
# ══════════════════════════════════════════════════════════


def test_끄면_근거결속_탈락_문장은_제거되고_추가_호출도_없다():
    ask = _FakeAI([_verdicts({1: VERDICT_TRUE})])
    protocol: list[dict] = []

    verified = verify_report(
        _report(), _raw_fragments(), None, ask, protocol_diagnostics=protocol,
    )

    assert _texts(verified) == []
    assert ask.total_calls == 1  # 검수 1회뿐 — 재작성도 재검수도 없다
    assert ask.batch_prompts == []
    assert _record(protocol) is None  # 기록 자체를 남기지 않는다


def test_켜도_탈락_문장이_없으면_아무_호출도_기록도_없다():
    """«대상이 없으면 부르지 않는다» — 켜는 것만으로 호출이 늘지 않는다."""

    report = ComposedReport(
        sections=(ComposedSection(
            section_id="identity",
            sentences=(ComposedSentence(
                text=_FIXED[1], citations=("1",), grade=GRADE_CONFIRMED,
            ),),
        ),),
        summary=(),
    )
    ask = _FakeAI([_verdicts({1: VERDICT_TRUE})])
    protocol: list[dict] = []

    verified = verify_report(
        report, _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    assert _texts(verified) == [_FIXED[1]]
    assert ask.total_calls == 1
    assert _record(protocol) is None


# ══════════════════════════════════════════════════════════
# ② 켜면 묶음 재작성 1회 + 재검수 1회
# ══════════════════════════════════════════════════════════


def test_켜면_재작성_1회_재검수_1회로_참_애매_거짓을_각각_처분한다():
    ask = _FakeAI(
        [
            _verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE, 3: VERDICT_TRUE}),
            _verdicts({1: VERDICT_TRUE, 2: VERDICT_UNCLEAR, 3: VERDICT_FALSE}),
        ],
        batch_responses=[_rewrites([
            {"번호": 1, "글": _FIXED[1], "포기": False},
            {"번호": 2, "글": _FIXED[2], "포기": False},
            {"번호": 3, "글": _FIXED[3], "포기": False},
        ])],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((1, 2, 3)), _raw_fragments((1, 2, 3)), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    # 호출: 첫 검수 1 + 묶음 재작성 1 + 재검수 1 = 3
    assert len(ask.batch_prompts) == 1
    assert len(ask.review_prompts) == 2
    assert ask.total_calls == 3

    sentences = verified.sections[0].sentences
    assert [sentence.text for sentence in sentences] == [_FIXED[1], _FIXED[2]]
    # 참 → «확인»으로 확정, 애매 → «해석» 강등, 거짓 → 제거
    assert sentences[0].grade == GRADE_CONFIRMED
    assert sentences[0].verification_state == "verified"
    assert sentences[1].grade == GRADE_INTERPRETED
    assert sentences[1].verification_state == "unverified"

    record = _record(protocol)
    assert record["상태"] == GROUNDING_REWRITE_STATE_DONE
    assert record["대상"] == 3
    assert record["재작성수신"] == 3
    assert record["기계검사통과"] == 3
    assert record["재검수참"] == 1
    assert record["재검수애매"] == 1
    assert record["최종반영"] == 2


def test_재작성_프롬프트에_원문장과_사유_문구가_있고_조각은_한_번만_나온다():
    """같은 조각을 두 문장이 인용해도 원문은 프롬프트에 한 번만 실린다."""

    report = ComposedReport(
        sections=(ComposedSection(
            section_id="identity",
            sentences=(
                ComposedSentence(text=_REJECTED[1], citations=("1",),
                                 grade=GRADE_CONFIRMED),
                ComposedSentence(text=_REJECTED[2], citations=("1",),
                                 grade=GRADE_CONFIRMED),
            ),
        ),),
        summary=(),
    )
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE}), _verdicts({})],
        batch_responses=[_rewrites([{"번호": 1, "글": _FIXED[1], "포기": False}])],
    )

    verify_report(
        report, _raw_fragments(), None, ask, grounding_rewrite_enabled=True,
    )

    prompt = ask.batch_prompts[0]
    assert _REJECTED[1] in prompt and _REJECTED[2] in prompt
    assert prompt.count(_SOURCES[1]) == 1  # 조각 원문은 한 번씩만
    assert GROUNDING_REWRITE_REASON_TEXTS[PROSE_OWN_SOURCE_UNSUPPORTED] in prompt
    assert PROSE_OWN_SOURCE_UNSUPPORTED not in prompt  # 코드 글자가 아니라 사람 문구


# ══════════════════════════════════════════════════════════
# ③ 고쳐 쓴 글도 다시 검사받는다
# ══════════════════════════════════════════════════════════


def test_재작성문의_근거에_없는_수치는_재검수_전에_제거된다():
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE})],
        batch_responses=[_rewrites([
            {"번호": 1, "글": "마루기업의 지난해 매출액은 950억원이다.", "포기": False},
        ])],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((4,)), _raw_fragments((4,)), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    assert _texts(verified) == []
    # 재검수를 «부르지도» 않았다 — 기계 검사에서 이미 떨어졌기 때문이다.
    assert len(ask.review_prompts) == 1
    record = _record(protocol)
    assert record["재작성수신"] == 1
    assert record["기계검사통과"] == 0
    assert record["재검수참"] == 0
    assert record["최종반영"] == 0


def test_재작성문이_수치_강등_판정을_받으면_해석으로_살리지_않고_제거한다():
    """★ «거짓» 재작성 경로와 일부러 다른 자리다.

    그쪽은 검수 AI 가 내용을 «거짓»이라 본 문장이라 해석 등급으로 내려 남긴다.
    이쪽은 «기계 가드»가 근거 결속으로 떨어뜨린 글의 새 판본이고, 두 번째 기계
    검사에서도 숫자를 원문에 결속하지 못했다. 강등된 문장은 재검수에 넣지
    않으므로, 해석으로 살리면 근거 결속 가드를 «한 번도 다시 지나지 않은»
    문장이 본문에 남는다.
    """
    ask = _FakeAI(
        [
            _verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE}),
            _verdicts({1: VERDICT_TRUE}),
        ],
        batch_responses=[_rewrites([
            {"번호": 1, "글": _FIXED[1], "포기": False},
            # 조각 2 원문에는 연도가 하나도 없다 → «강등» 처분이 된다(제거 아님).
            {"번호": 2,
             "글": "나래산업은 2019년부터 국내 완성차 회사를 주요 거래처로 두고 있다.",
             "포기": False},
        ])],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((1, 2)), _raw_fragments((1, 2)), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    # 강등 판정을 받은 글은 어떤 등급으로도 남지 않는다.
    assert _texts(verified) == [_FIXED[1]]
    assert all(
        "2019" not in sentence.text for sentence in verified.sections[0].sentences
    )
    # 재검수 프롬프트에 그 번호가 아예 없다 — 가드가 다시 돌 기회 자체가 없다.
    재검수 = ask.review_prompts[1]
    assert "[1] (등급: " in 재검수
    assert "[2] (등급: " not in 재검수
    record = _record(protocol)
    assert record["재작성수신"] == 2
    assert record["기계검사통과"] == 1  # 강등은 «통과»로 세지 않는다
    assert record["최종반영"] == 1


def test_재검수의_근거결속이_다시_탈락시키면_제거된다():
    """고쳐 쓴 글이 여전히 자기 인용에 기대지 못하면 살아나지 않는다."""

    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE}), _verdicts({1: VERDICT_TRUE})],
        batch_responses=[_rewrites([
            {"번호": 1, "글": "미르전자는 위성 통신 장비를 계속 수출한다.", "포기": False},
        ])],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report(), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    # 재검수 AI 는 «참»이라고 답했지만 근거 결속 기계 검사가 다시 걸렀다.
    assert len(ask.review_prompts) == 2
    assert _texts(verified) == []
    record = _record(protocol)
    assert record["기계검사통과"] == 1
    assert record["재검수참"] == 0
    assert record["최종반영"] == 0


def test_포기와_빈_글과_요청밖_번호는_제거되고_포기_수에_센다():
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE})],
        batch_responses=[_rewrites([
            {"번호": 1, "글": "", "포기": True},
            {"번호": 2, "글": "   ", "포기": False},
            {"번호": 99, "글": "요청하지 않은 번호의 문장이다.", "포기": False},
        ])],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((1, 2)), _raw_fragments((1, 2)), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    assert _texts(verified) == []
    assert len(ask.review_prompts) == 1  # 재검수할 항목이 없으니 부르지 않는다
    record = _record(protocol)
    assert record["대상"] == 2
    assert record["재작성수신"] == 0
    assert record["포기"] == 2  # 요청 밖 번호는 «포기»로 세지 않는다
    assert record["기계검사통과"] == 0
    assert record["최종반영"] == 0


# ══════════════════════════════════════════════════════════
# ④ 형식 실패·예산 부족은 «전부 제거»로 닫는다
# ══════════════════════════════════════════════════════════


def test_두_번_다_못_읽으면_작성형식실패로_전부_제거된다():
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE})],
        batch_responses=["이건 JSON이 아니다", "여전히 JSON이 아니다"],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report(), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    assert _texts(verified) == []
    assert len(ask.batch_prompts) == 2  # 첫 호출 + 재요청 1회가 상한이다
    # 재요청 프롬프트에는 형식 요구가 덧붙는다.
    assert len(ask.batch_prompts[1]) > len(ask.batch_prompts[0])
    assert ask.batch_prompts[1].startswith(ask.batch_prompts[0])
    record = _record(protocol)
    assert record["상태"] == GROUNDING_REWRITE_STATE_FORMAT_FAILED
    assert record["대상"] == 1
    assert record["응답꼴"] == ["읽기실패", "읽기실패"]
    assert "재검수참" not in record  # 오지 않은 관문의 칸은 적지 않는다


def test_예산이_부족하면_호출중단_기록만_남기고_나머지_문장은_그대로_낸다():
    성한문장 = ComposedSentence(
        text=_FIXED[1], citations=("1",), grade=GRADE_CONFIRMED,
    )
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE})],
        batch_responses=[AskFatalError(RuntimeError("한도"), call_limit=True)],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((1,), extra=(성한문장,)), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    assert _texts(verified) == [_FIXED[1]]  # 나머지 보고서는 그대로 나온다
    record = _record(protocol)
    assert record["상태"] == GROUNDING_REWRITE_STATE_CALL_ABORTED
    assert record["대상"] == 1
    assert record["오류종류"] == "호출한도"


def test_호출이_죽으면_형식_재요청_없이_바로_닫는다():
    """★ 유료 호출을 한 번 더 쓰지 않는다.

    형식 재요청은 「답은 왔는데 모양이 틀렸다」를 고치는 수단이다. 호출 «자체»가
    죽었으면 고칠 답이 없어서, 한 번 더 불러도 같은 자리에 선다.
    이 시험은 그 조기 종료를 지우면 빨개진다(호출이 2회가 되고 두 번째
    프롬프트에 재요청 안내가 붙는다).
    ⚠️ 여기서 죽는 것은 «비치명» 실패다. 요청 전역 장애(AskFatalError)는 다른
      갈래이며 바로 위 시험이 따로 못 박는다.
    """
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE})],
        batch_responses=[RuntimeError("작가 호출이 죽었다")],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report(), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    assert len(ask.batch_prompts) == 1, "죽은 호출에 재요청까지 써서 유료 호출이 늘었다"
    assert all(
        GROUNDING_REWRITE_RETRY_GUIDE not in prompt for prompt in ask.batch_prompts
    )
    assert len(ask.review_prompts) == 1  # 재검수할 것이 없다
    assert _texts(verified) == []
    record = _record(protocol)
    assert record["상태"] == GROUNDING_REWRITE_STATE_FORMAT_FAILED
    assert record["응답꼴"] == ["읽기실패"]


def test_먼저_난_호출중단_기록을_재검수_실패가_덮지_않는다():
    """★ 먼저 난 사유가 진짜 원인이고, 재검수 실패는 그 결과다.

    시나리오: «거짓» 재작성은 성공해 재검수 항목이 남았는데, 묶음 재작성 호출이
    «호출한도»로 죽었고 이어진 합친 재검수마저 «요청예산»으로 죽는다. 이때 기록은
    처음 사유(호출한도)여야 한다 — 두 번째 사유로 덮으면 「왜 이 단계가 멈췄나」를
    되짚을 때 원인이 아니라 결과를 보게 된다.
    이 시험은 덮어쓰기 방지 조건을 지우면 빨개진다(오류종류가 «요청예산»이 된다).
    """
    거짓문장 = ComposedSentence(
        text=_REJECTED[2], citations=("1",), grade=GRADE_CONFIRMED,
    )
    ask = _FakeAI(
        [
            _verdicts({1: VERDICT_TRUE, 2: VERDICT_FALSE}),
            # 합친 재검수 — 이쪽은 «요청예산»으로 죽는다.
            AskFatalError(RuntimeError("예약액 소진"), request_budget=True),
        ],
        # 묶음 재작성 — 먼저 «호출한도»로 죽는다.
        batch_responses=[AskFatalError(RuntimeError("호출 수 상한"), call_limit=True)],
        rewrite_responses=[_FIXED[1]],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((1,), extra=(거짓문장,)), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    # 두 호출이 다 죽었어도 보고서는 나온다(문장만 빠진다).
    assert _texts(verified) == []
    assert len(ask.rewrite_prompts) == 1  # 거짓 재작성은 실제로 돌았다
    assert len(ask.batch_prompts) == 1
    assert len(ask.review_prompts) == 2  # 첫 검수 + 합친 재검수(죽은 호출)
    record = _record(protocol)  # 기록이 정확히 하나임을 함께 본다
    assert record["상태"] == GROUNDING_REWRITE_STATE_CALL_ABORTED
    assert record["오류종류"] == "호출한도", "나중에 난 재검수 실패가 원래 사유를 덮었다"


def test_돈_계정_장애는_삼키지_않고_그대로_올려_보낸다():
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE})],
        batch_responses=[AskFatalError(RuntimeError("예산 소진"))],
    )

    with pytest.raises(AskFatalError):
        verify_report(
            _report(), _raw_fragments(), None, ask,
            grounding_rewrite_enabled=True,
        )


# ══════════════════════════════════════════════════════════
# ⑤ 상한·합친 재검수·기록 계약
# ══════════════════════════════════════════════════════════


def test_대상이_상한을_넘으면_앞쪽만_보내고_대상에는_전체_수가_남는다():
    개수 = GROUNDING_REWRITE_MAX_SENTENCES + 1
    문장들 = tuple(
        ComposedSentence(
            text=f"미르전자는 {'가나다라마바사아자차카타파하'[index]} 사업을 운영한다.",
            citations=("1",),
            grade=GRADE_CONFIRMED,
        )
        for index in range(개수)
    )
    report = ComposedReport(
        sections=(ComposedSection(section_id="identity", sentences=문장들),),
        summary=(),
    )
    ask = _FakeAI(
        [_verdicts({number: VERDICT_TRUE for number in range(1, 개수 + 1)})],
        batch_responses=[_rewrites([])],
    )
    protocol: list[dict] = []

    verify_report(
        report, _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    prompt = ask.batch_prompts[0]
    보낸번호 = [
        number for number in range(1, 개수 + 1)
        if f"번호 {number} · 인용 조각" in prompt
    ]
    assert 보낸번호 == list(range(1, GROUNDING_REWRITE_MAX_SENTENCES + 1))
    record = _record(protocol)
    assert record["대상"] == 개수  # 보내지 못한 것까지 센다


def test_거짓_재작성이_함께_있어도_재검수는_한_번이다():
    거짓문장 = ComposedSentence(
        text="가람전자의 본사는 서울특별시에 있다.",
        citations=("1",),
        grade=GRADE_CONFIRMED,
    )
    ask = _FakeAI(
        [
            _verdicts({1: VERDICT_TRUE, 2: VERDICT_FALSE}),
            _verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE}),
        ],
        batch_responses=[_rewrites([{"번호": 1, "글": _FIXED[1], "포기": False}])],
        rewrite_responses=[_FIXED[1]],
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report((1,), extra=(거짓문장,)), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    # 거짓 재작성 1회 + 묶음 재작성 1회 + 검수 2회(첫 검수·합친 재검수 각 1)
    assert len(ask.rewrite_prompts) == 1
    assert len(ask.batch_prompts) == 1
    assert len(ask.review_prompts) == 2
    # 합친 재검수 한 번에 두 문장이 «같이» 실렸다(`_build_review_prompt` 의 줄 모양).
    재검수 = ask.review_prompts[1]
    assert "[1] (등급: " in 재검수 and "[2] (등급: " in 재검수
    assert _texts(verified) == [_FIXED[1], _FIXED[1]]
    assert _record(protocol)["최종반영"] == 1


def test_완료_기록의_칸_이름은_공유_상수와_정확히_같다():
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE}), _verdicts({1: VERDICT_TRUE})],
        batch_responses=[_rewrites([{"번호": 1, "글": _FIXED[1], "포기": False}])],
    )
    protocol: list[dict] = []

    verify_report(
        _report(), _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    record = _record(protocol)
    assert record["상태"] == GROUNDING_REWRITE_STATE_DONE
    assert set(record) == (
        {"step", "상태", "대상장", "응답꼴"} | set(GROUNDING_REWRITE_COUNT_KEYS)
    )
    assert record["대상장"] == ["identity"]
    assert record["응답꼴"] == "계약"


# ══════════════════════════════════════════════════════════
# ⑥ packet(묶음) 경로 — 운영 FULL 이 타는 쪽
# ══════════════════════════════════════════════════════════
#
# ★ 왜 따로 시험하나 (독립 검토가 찾은 P0) — 운영 FULL 은
#   `allowed_fragment_ids_by_section` 이 채워진 packet 경로
#   (`_semantic_review_grouped`)를 탄다. 평문 경로에만 배선하면 여기 시험은
#   전부 초록인데 운영에서는 고쳐 쓰기가 «한 번도» 돌지 않는다.


def _packet_allowed() -> dict[str, frozenset[str]]:
    return {"identity": frozenset({"1"})}


def test_packet_경로에서도_재작성_1회_재검수_1회로_되살린다():
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE})],  # 합친 재검수(평문 꼴) 한 번
        batch_responses=[_rewrites([{"번호": 1, "글": _FIXED[1], "포기": False}])],
        grouped_auto=True,
    )
    protocol: list[dict] = []

    verified = verify_report(
        _report(), _raw_fragments(), None, ask,
        allowed_fragment_ids_by_section=_packet_allowed(),
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    # 묶음 검수 1회 + 묶음 재작성 1회 + 재검수 1회 = 3
    assert len(ask.grouped_prompts) == 1, "packet 경로를 타지 않았다"
    assert len(ask.batch_prompts) == 1
    assert len(ask.review_prompts) == 1
    assert ask.total_calls == 3

    sentences = verified.sections[0].sentences
    assert [sentence.text for sentence in sentences] == [_FIXED[1]]
    assert sentences[0].grade == GRADE_CONFIRMED
    assert sentences[0].verification_state == "verified"
    record = _record(protocol)
    assert record["상태"] == GROUNDING_REWRITE_STATE_DONE
    assert record["대상"] == 1
    assert record["최종반영"] == 1


def test_packet_경로도_끄면_제거되고_추가_호출이_없다():
    ask = _FakeAI([], grouped_auto=True)
    protocol: list[dict] = []

    verified = verify_report(
        _report(), _raw_fragments(), None, ask,
        allowed_fragment_ids_by_section=_packet_allowed(),
        protocol_diagnostics=protocol,
    )

    assert _texts(verified) == []
    assert len(ask.grouped_prompts) == 1
    assert ask.total_calls == 1  # 묶음 검수 1회뿐 — packet 계약 그대로다
    assert _record(protocol) is None


def test_packet_경로의_거짓_판정은_여전히_재작성하지_않는다():
    """packet 의 «거짓 재작성 없음» 계약은 이 기능을 켜도 그대로다."""

    ask = _FakeAI([], grouped_auto=False, batch_responses=[])
    # 묶음 검수가 «거짓»을 답하게 직접 만든다(자동 참 응답을 쓰지 않는다).
    ask.review_responses = [json.dumps(
        {"판정": [{"번호": 1, "장": "identity", "근거": ["1"], "결과": VERDICT_FALSE}]},
        ensure_ascii=False,
    )]

    verified = verify_report(
        _report(), _raw_fragments(), None, ask,
        allowed_fragment_ids_by_section=_packet_allowed(),
        grounding_rewrite_enabled=True,
    )

    assert _texts(verified) == []
    assert ask.rewrite_prompts == []  # 문장당 재작성 호출이 없다
    assert ask.batch_prompts == []    # 근거 결속 탈락이 아니므로 묶음 재작성도 없다
    assert ask.total_calls == 1


# ══════════════════════════════════════════════════════════
# ⑦ 요약 그룹은 고쳐 쓰지 않는다
# ══════════════════════════════════════════════════════════


def test_요약_그룹의_탈락_문장은_재작성_프롬프트에_실리지_않는다():
    """요약은 «본문에서 고른» 문장을 글자 그대로 싣는 자리다.

    여기서 새 글자를 만들면 같은 사실이 본문과 요약에서 다른 문장이 된다.
    이 시험은 대상 수집에서 본문 조건을 빼면 빨개진다.
    """
    report = ComposedReport(
        sections=(ComposedSection(
            section_id="identity",
            sentences=(ComposedSentence(text=_REJECTED[1], citations=("1",),
                                        grade=GRADE_CONFIRMED),),
        ),),
        summary=(ComposedSentence(text=_REJECTED[2], citations=("1",),
                                  grade=GRADE_CONFIRMED),),
    )
    ask = _FakeAI(
        [_verdicts({1: VERDICT_TRUE, 2: VERDICT_TRUE}), _verdicts({1: VERDICT_TRUE})],
        batch_responses=[_rewrites([{"번호": 1, "글": _FIXED[1], "포기": False}])],
    )
    protocol: list[dict] = []

    verified = verify_report(
        report, _raw_fragments(), None, ask,
        protocol_diagnostics=protocol, grounding_rewrite_enabled=True,
    )

    prompt = ask.batch_prompts[0]
    assert "번호 1 · 인용 조각" in prompt
    assert "번호 2 · 인용 조각" not in prompt, "요약 문장이 고쳐 쓰기 대상에 들어갔다"
    assert _REJECTED[2] not in prompt
    # 요약 문장은 예전처럼 «제거»되고, 본문 문장만 되살아난다.
    assert verified.summary == ()
    assert _texts(verified) == [_FIXED[1]]
    record = _record(protocol)
    assert record["대상"] == 1
    assert record["대상장"] == ["identity"]


# ══════════════════════════════════════════════════════════
# ⑧ 파이프라인 배선 — «실제 호출 인자»를 본다
# ══════════════════════════════════════════════════════════


def test_run_v2가_켜짐을_verify_report의_실제_호출_인자로_넘긴다(monkeypatch):
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer,
        _FakeWriter,
        _raw_fragments as _pipeline_fragments,
    )

    calls: list[dict] = []
    진짜 = pipeline_module.verify_report

    def 엿듣는_verify_report(*args, **kwargs):
        calls.append(dict(kwargs))
        return 진짜(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "verify_report", 엿듣는_verify_report)

    run_v2(
        "가나다전자",
        _pipeline_fragments(),
        None,
        writer_ask=_FakeWriter(),
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
        as_of_date="2026-08-24",
        grounding_rewrite_enabled=True,
    )

    assert calls, "run_v2가 verify_report를 한 번도 부르지 않았다"
    assert calls[0].get("grounding_rewrite_enabled") is True


class _PacketWriter:
    """packet 경로에서 «근거 결속에 걸릴» 확인 문장 하나씩을 아홉 장에 쓴다.

    ★ 인용한 공식 자료와 낱말이 겹치지 않는 문장이라 `prose_own_source` 가
      걸린다 — 실제 작가가 만들어 내는 결함 모양과 같은 자리다.
    ★ 장마다 표지를 달리해 장 간 중복 제거가 여덟 장을 지우지 않게 한다.
    """

    #: 숫자 없는 장 표지 — 숫자를 넣으면 수치 검증이 근거에 없는 숫자로 본다.
    MARKS = "가나다라마바사아자"

    def __init__(self):
        self.prompts: list[str] = []
        self.section_calls = 0

    def __call__(self, prompt: str) -> str:
        from src.features.composer.constants import SECTION_IDS
        from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION

        self.prompts.append(prompt)
        section_id = SECTION_IDS[self.section_calls]
        mark = self.MARKS[self.section_calls]
        self.section_calls += 1
        return json.dumps(
            {"문장들": [{
                "글": f"미르전자는 {mark} 위성 통신 장비를 수출한다.",
                "인용": ["1"],
                "등급": GRADE_CONFIRMED,
                "주장슬롯": CLAIM_SLOTS_BY_SECTION[section_id][0],
            }]},
            ensure_ascii=False,
        )


def _packet_reviewer_class():
    """`_FakeReviewer` 에 «묶음 재작성 응답»만 얹은 검수자.

    run_v2 는 `rewrite_ask` 를 따로 주지 않으므로 재작성 호출도 검수 ask 로 간다
    (`verify._grounding_rewrite_pass` 의 ``rewrite_ask or ask``).
    """
    from src.features.composer.tests.test_pipeline import _FakeReviewer

    class _PacketReviewer(_FakeReviewer):
        #: 고쳐 쓴 글 — 공식 자료 원문의 낱말을 그대로 써서 결속을 통과한다.
        FIXED = "가나다전자는 공식 자료에서 회사 사업 실적을 설명한다."

        def __init__(self):
            super().__init__()
            self.batch_prompts: list[str] = []

        def __call__(self, prompt: str) -> str:
            if prompt.startswith(GROUNDING_REWRITE_PROMPT_HEADER):
                self.batch_prompts.append(prompt)
                numbers = [
                    int(number)
                    for number in re.findall(r"번호 (\d+) · 인용 조각", prompt)
                ]
                return json.dumps(
                    {"문장들": [{"번호": number, "글": self.FIXED, "포기": False}
                             for number in numbers]},
                    ensure_ascii=False,
                )
            return super().__call__(prompt)

    return _PacketReviewer


@pytest.mark.parametrize("켬", [True, False])
def test_run_v2_packet_경로에서_재작성_프롬프트가_실제로_나간다(켬):
    """★ 독립 검토가 찾은 P0 의 회귀 방지 — 운영 FULL 이 타는 경로다.

    이 시험이 막는 것: 평문 경로에만 배선해 두면 단위 시험은 전부 초록인데
    운영에서는 고쳐 쓰기가 한 번도 돌지 않는다.
    ⚠️ 이 fixture 는 품질 하한 미달로 끝에서 V2ValidationError 가 난다. 그건 이
      시험이 보는 것이 아니다 — 그 «전»에 어떤 호출이 나갔는지를 본다.
    """
    from src.features.composer.tests.test_pipeline import (
        _strict_fragments,
        _strict_packet_set,
    )
    from src.features.composer.validate import V2ValidationError

    writer = _PacketWriter()
    reviewer = _packet_reviewer_class()()

    with contextlib.suppress(V2ValidationError):
        run_v2(
            "가나다전자",
            _strict_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            section_evidence_packets=_strict_packet_set(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
            grounding_rewrite_enabled=켬,
        )

    assert len(writer.prompts) == 9  # 장 작성 9회는 양쪽 같다
    if not 켬:
        # 묶음 검수 1회뿐 — packet 계약이 예전 그대로다.
        assert reviewer.batch_prompts == []
        assert len(reviewer.prompts) == 1
        return
    # 묶음 재작성 1회 + 그 뒤 합친 재검수 1회가 «실제로» 나갔다.
    assert len(reviewer.batch_prompts) == 1
    assert len(reviewer.prompts) == 2
    보낸번호 = re.findall(r"번호 (\d+) · 인용 조각", reviewer.batch_prompts[0])
    assert 보낸번호 == [str(number) for number in range(1, 10)]


def test_run_v2가_packet_경로의_verify_report에도_같은_값을_넘긴다(monkeypatch):
    """호출 자리가 둘이다 — 한쪽만 배선하면 그 경로에서만 조용히 꺼진다.

    ⚠️ 이 fixture 는 품질 하한 미달로 마지막에 V2ValidationError 로 끝난다.
      그건 이 시험이 보는 것이 아니다 — 그 «전»에 verify_report 가 실제로 받은
      인자를 본다.
    """
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer,
        _FakeWriter,
        _strict_fragments,
        _strict_packet_set,
    )
    from src.features.composer.validate import V2ValidationError

    calls: list[dict] = []
    진짜 = pipeline_module.verify_report

    def 엿듣는_verify_report(*args, **kwargs):
        calls.append(dict(kwargs))
        return 진짜(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "verify_report", 엿듣는_verify_report)

    with pytest.raises(V2ValidationError):
        run_v2(
            "가나다전자",
            _strict_fragments(),
            None,
            writer_ask=_FakeWriter(),
            reviewer_ask=_FakeReviewer(),
            section_evidence_packets=_strict_packet_set(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
            grounding_rewrite_enabled=True,
        )

    assert calls
    # packet 경로로 갔다는 증거와, 그 호출에도 값이 실렸다는 증거를 함께 본다.
    assert "allowed_fragment_ids_by_section" in calls[0]
    assert calls[0].get("grounding_rewrite_enabled") is True


def test_run_v2의_기본값은_꺼짐이다(monkeypatch):
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer,
        _FakeWriter,
        _raw_fragments as _pipeline_fragments,
    )

    calls: list[dict] = []
    진짜 = pipeline_module.verify_report

    def 엿듣는_verify_report(*args, **kwargs):
        calls.append(dict(kwargs))
        return 진짜(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "verify_report", 엿듣는_verify_report)

    run_v2(
        "가나다전자",
        _pipeline_fragments(),
        None,
        writer_ask=_FakeWriter(),
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
        as_of_date="2026-08-24",
    )

    assert calls
    assert calls[0].get("grounding_rewrite_enabled") is False

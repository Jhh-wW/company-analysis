"""`select_spans`가 1판 `generate_and_check`와 «바꿔 끼울 수 있는가» 시험.

★ 진짜 AI를 부르지 않는다 (0원). 모의 응답으로만 돌린다.
★ 원문 대조 검사(W1~W4)는 **1판 진짜 코드**를 파일에서 그대로 불러 쓴다 —
  흉내 낸 검사로 통과시키면 「지어낸 문장이 걸리는가」를 확인한 것이 아니다.

여기서 확인하는 것:
  ① 자리 인자와 반환 모양이 1판과 같은가
  ② AI 호출이 딱 1회인가 (늘리면 그만큼 돈이 나간다)
  ③ 지어낸 번호·지어낸 문장이 버려지는가
  ④ 단계 기록 이름이 1판과 같은가 (화면 지표가 이 이름을 읽는다)
  ⑤ 엔진에 «실제로 있는» 이름만 부르는가
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
import sys
from typing import Any, Optional

import pytest

from src.core import paths
from src.features.spanselect import logic as spanselect
from src.features.spanselect.constants import GENERATION_STEP, VERIFY_STEP

ENGINE_PATH = paths.PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py"
DRAFT_CHECK_PATH = (
    paths.PROJECT_ROOT / "analysis_engine" / "src" / "features" / "draft_check"
    / "logic.py"
)


def _load_draft_check() -> Any:
    """1판의 W1~W4 검사 모듈을 «파일에서 직접» 불러온다.

    ★ `import run_pilot`을 하지 않는다 — 그건 `anthropic`·`presidio`를 요구한다.
      이 모듈은 dataclasses만 쓰므로 그대로 불러도 안전하다.
    """
    name = "analysis_engine_draft_check"
    spec = importlib.util.spec_from_file_location(name, DRAFT_CHECK_PATH)
    assert spec is not None and spec.loader is not None, f"찾지 못함: {DRAFT_CHECK_PATH}"
    module = importlib.util.module_from_spec(spec)
    # dataclass가 자기 모듈을 sys.modules에서 되찾으므로 «실행 전»에 등록해야 한다.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_draft_check = _load_draft_check()

# ── 전수 검사용 문장 쪼개기 ──────────────────────────────
# 1판 `run_pilot.split_sentences`(:398-409)와 같은 규칙이다. 1판을 import 하면
# `anthropic`이 딸려 와 시험이 느려지므로 규칙만 그대로 옮겨 적는다.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MIN_SENT_CHARS = 20
_MAX_SENTS_PER_FRAG = 12
_SENT_ENDERS = (".", "!", "?", "다", "음", "됨", ")")


def _문장쪼개기(text: str) -> list[str]:
    pieces = [
        s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) >= _MIN_SENT_CHARS
    ]
    if pieces and not pieces[-1].rstrip().endswith(_SENT_ENDERS):
        pieces = pieces[:-1]
    return pieces[:_MAX_SENTS_PER_FRAG]


class 모의엔진:
    """1판 엔진 «흉내». 부품 중 AI를 부르는 `_ask`만 가짜다.

    나머지(`check_draft`·`DraftItem`)는 1판 진짜 코드를 그대로 쓴다.
    """

    BLOCK_ORDER = ("1", "2", "3", "4-1", "4-2", "4-3", "5", "6", "7", "8", "9")
    GEN_MAX_TOKENS = 3000
    DraftItem = _draft_check.DraftItem
    check_draft = staticmethod(_draft_check.check_draft)

    def __init__(self, 답: Optional[dict[str, Any]]) -> None:
        self.답 = 답
        self.호출수 = 0
        self.받은_프롬프트 = ""
        self.받은_최대토큰: Optional[int] = None

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        return [s.strip() for s in text.split(". ") if len(s.strip()) >= 20]

    def _ask(
        self, client: Any, prompt: str, schema: dict[str, Any], max_tokens: int = 700
    ) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
        self.호출수 += 1
        self.받은_프롬프트 = prompt
        self.받은_최대토큰 = max_tokens
        return self.답, {"in": 10, "out": 5, "usd": 0.0, "elapsed": 0.0}


조각 = {
    1: {
        "종류": "MD&A",
        "원문": (
            "회사는 원가 상승으로 수익성이 나빠지는 어려움을 겪고 있습니다. "
            "이에 대응해 2026년 자동화 설비 투자를 진행하고 있습니다."
        ),
    },
    2: {
        "종류": "뉴스",
        "원문": (
            "(2026-08-14 보도 · www.example.co.kr) 언론은 이 회사의 해외 수주 지연을 "
            "지적했다. 회사는 하반기 동남아 신규 법인 설립을 준비하고 있다고 밝혔다."
        ),
    },
}
요구역량 = ["파이썬으로 데이터 파이프라인을 만들어 보신 분", "SQL 활용 경험이 있으신 분"]


def _돌린다(답: Optional[dict[str, Any]]) -> tuple[list[Any], list[Any], list[dict], 모의엔진]:
    engine = 모의엔진(답)
    steps: list[dict[str, Any]] = []
    kept, deleted = spanselect.select_spans(
        client=None, frags=조각, requirements=요구역량, job="데이터분석가",
        steps=steps, engine=engine,
    )
    return kept, deleted, steps, engine


# ══════════════════════════════════════════════════════════
# ① 1판과 같은 계약인가
# ══════════════════════════════════════════════════════════

def _엔진_함수(이름: str) -> ast.FunctionDef:
    tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 이름:
            return node
    raise AssertionError(f"1판 엔진에 {이름}(이)가 없습니다: {ENGINE_PATH}")


def test_자리인자가_1판_generate_and_check와_같다():
    """★ 바꿔 끼우려면 자리 인자가 같아야 한다. 엔진은 «글자로 읽어» 대조한다(실행 0원)."""
    엔진_인자 = [a.arg for a in _엔진_함수("generate_and_check").args.args]
    내_인자 = [
        name
        for name, param in inspect.signature(spanselect.select_spans).parameters.items()
        if param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert 내_인자 == 엔진_인자 == ["client", "frags", "requirements", "job", "steps"]


def test_엔진_모듈은_키워드로만_받는다():
    """돈이 나가는 길을 하나로 유지하려고 «주입»받는다 — 이 파일은 엔진을 직접 안 부른다."""
    engine_param = inspect.signature(spanselect.select_spans).parameters["engine"]
    assert engine_param.kind is inspect.Parameter.KEYWORD_ONLY
    assert engine_param.default is inspect.Parameter.empty, (
        "기본값이 생기면 «엔진을 몰래 불러오는» 두 번째 길이 열립니다."
    )
    source = inspect.getsource(spanselect)
    assert "import run_pilot" not in source


def test_1판처럼_kept와_deleted_두_묶음을_돌려준다():
    kept, deleted, _steps, _engine = _돌린다(
        {"items": [{"block": "4-1", "sid": "1-1"}, {"block": "4-2", "sid": "2-2"}]}
    )
    assert isinstance(kept, list) and isinstance(deleted, list)
    assert [i.block for i in kept][:2] == ["4-1", "4-2"]
    assert all(isinstance(i, _draft_check.DraftItem) for i in kept)


def test_AI가_답을_못_주면_빈_두_묶음을_돌려준다():
    kept, deleted, steps, _engine = _돌린다(None)
    assert (kept, deleted) == ([], [])
    assert steps[0]["step"] == GENERATION_STEP     # 실패도 기록에는 남는다


# ══════════════════════════════════════════════════════════
# ② 돈 — 호출 횟수는 그대로여야 한다
# ══════════════════════════════════════════════════════════

def test_AI_호출은_딱_1회다():
    _kept, _deleted, _steps, engine = _돌린다({"items": [{"block": "1", "sid": "1-1"}]})
    assert engine.호출수 == 1
    assert engine.받은_최대토큰 == 모의엔진.GEN_MAX_TOKENS


# ══════════════════════════════════════════════════════════
# ③ 지어내기가 걸리는가 (이 도구의 존재 이유)
# ══════════════════════════════════════════════════════════

def test_없는_번호를_고르면_버린다():
    kept, _deleted, steps, _engine = _돌린다(
        {"items": [{"block": "4-1", "sid": "9-9"}, {"block": "1", "sid": "1-1"}]}
    )
    assert steps[1]["없는번호"] == 1
    assert all(i.block != "4-1" for i in kept)


def test_원문에_없는_문장은_W3로_걸린다():
    """★ 원문 대조가 «실제로 돌고 있는가»를 본다.

    번호만 고르는 구조에서는 원래 지어낸 문장이 못 들어온다. 그래도 안전망이 살아
    있는지 확인해야 하므로, 조각을 쪼갠 결과가 원문과 다른 «고장 상황»을 만들어 본다.
    """
    class 고장난엔진(모의엔진):
        @staticmethod
        def split_sentences(text: str) -> list[str]:
            return ["회사는 화성에 공장을 지어 매출 1조원을 올렸습니다."]

    engine = 고장난엔진({"items": [{"block": "3", "sid": "1-1"}]})
    steps: list[dict[str, Any]] = []
    kept, deleted = spanselect.select_spans(
        None, 조각, [], "데이터분석가", steps, engine=engine
    )
    assert kept == []
    assert len(deleted) == 1
    assert deleted[0][1].startswith("W3")


def test_요구역량은_배치_안_되면_5번에_되살아난다():
    """조용한 누락 금지."""
    kept, _deleted, steps, _engine = _돌린다({"items": [{"block": "1", "sid": "1-1"}]})
    복원된 = [i.sentence for i in kept if i.block == "5"]
    assert 복원된 == 요구역량
    assert steps[1]["요구역량_복원"] == len(요구역량)


def test_공고_블록은_요구역량_원문_그대로여야_통과한다():
    """W4 — 공고 문장은 한 글자도 안 바뀐다 (정본 규칙).

    한계: 번호만 고르는 구조라 «다듬어진 공고 문장»은 애초에 못 들어온다.
    여기서 확인하는 것은 「원문 그대로면 통과한다」쪽이다.
    """
    kept, _deleted, _steps, _engine = _돌린다(
        {"items": [{"block": "7", "sid": "R-1"}, {"block": "5", "sid": "R-2"}]}
    )
    assert [i.sentence for i in kept if i.block == "7"] == [요구역량[0]]
    assert [i.sentence for i in kept if i.block == "5"] == [요구역량[1]]


# ══════════════════════════════════════════════════════════
# ③-2 앞머리를 뗀 문장이 «원문 대조»를 통과하는가
# ══════════════════════════════════════════════════════════
# ★ 여기가 이 작업에서 가장 위험한 지점이다. 다듬은 문장이 W3(원문 대조)에
#   걸리면 「지어낸 문장」으로 판정돼 **통째로 사라진다.** 그러면 4번 칸이 다시 빈다.
#   그래서 흉내가 아니라 **1판 진짜 `check_draft`**로 확인한다.

#: 조각 원문에 기사 말머리가 붙어 있는 실측 모양 (`재수집-p003.json` 조각 8).
말머리_조각 = {
    1: {
        "종류": "뉴스",
        "원문": (
            "(2026-07-22 보도 · www.edaily.co.kr) [편집자주] 우리엔, 동물 전용 "
            "제품으로 차별화 바텍의 관계사 레이언스의 자회사인 우리엔은 2019년 "
            "동물병원 전용 CT와 치과용 파노라마 장비를 출시했다."
        ),
    }
}


def test_앞머리를_뗀_문장이_W3_원문대조를_통과한다():
    """★ 조각 원문은 손대지 않으므로, 남은 낱말은 원문 낱말의 «부분수열»이다.

    `draft_check.sentence_in_source`는 앞에서부터 순서대로 찾으므로 앞을 잘라낸
    문장은 반드시 통과한다. 그 성질을 1판 진짜 코드로 확인한다.
    """
    engine = 모의엔진({"items": [{"block": "4-2", "sid": "1-1"}]})
    steps: list[dict[str, Any]] = []
    kept, deleted = spanselect.select_spans(
        None, 말머리_조각, [], "의료기기 개발 PM/RA", steps, engine=engine
    )
    assert deleted == [], f"다듬은 문장이 버려졌습니다: {deleted}"
    assert len(kept) == 1
    assert "[편집자주]" not in kept[0].sentence
    # ★ 출처 표기 「(2026-07-22 보도 · www.edaily.co.kr) 」도 뗀다.
    #   날짜·주소는 출처 목록에 이미 있다 — 문장에 남기면 자소서에 그대로 못 쓴다.
    #   ⚠️ 그래도 W3 원문대조는 통과해야 한다(위 `deleted == []`) — 그것이 이 시험의 핵심이다.
    assert "보도 ·" not in kept[0].sentence
    assert kept[0].sentence.startswith("우리엔,")
    assert steps[1]["유지"] == 1 and steps[1]["삭제"] == 0


def test_저장된_조각_전수에서_다듬은_문장이_전부_원문대조를_통과한다():
    """★ 예시 하나가 아니라 **저장된 조각 전수**로 확인한다 (AI 0회·0원).

    1판이 실제로 돌려 남긴 조각(`analysis_engine/data/pilot/fragments/`)을 전부
    문장으로 쪼개고, 앞머리를 뗀 것마다 1판 W3 대조를 돌린다.
    실측: 문장 1,519개 중 34개가 다듬어졌고 **대조 실패 0건**.
    """
    fragments_dir = paths.PILOT_DIR / "fragments"
    if not fragments_dir.is_dir():
        pytest.skip(f"저장된 조각이 없습니다: {fragments_dir}")

    다듬은수 = 0
    실패: list[str] = []
    for path in sorted(fragments_dir.glob("*.json")):
        for frag in json.loads(path.read_text(encoding="utf-8")).values():
            원문 = frag["원문"]
            for 문장 in _문장쪼개기(원문):
                다듬음 = spanselect.strip_leading_noise(문장)
                if 다듬음 == 문장:
                    continue
                다듬은수 += 1
                if not _draft_check.sentence_in_source(다듬음, 원문):
                    실패.append(f"{path.name}: {다듬음[:60]}")
    assert not 실패, f"다듬은 문장이 원문 대조에 걸립니다: {실패[:3]}"
    assert 다듬은수 > 0, "전수 검사가 «아무것도 안 세고» 통과하고 있습니다"


# ══════════════════════════════════════════════════════════
# ④ 단계 기록 — 화면 지표가 이 이름을 읽는다
# ══════════════════════════════════════════════════════════

def test_단계_기록_이름과_칸이_1판과_같다():
    """`pipeline/demo.py`가 「10_검증_W대조」의 유지·삭제로 지표를 센다. 이름을 바꾸면 0이 된다."""
    _kept, _deleted, steps, _engine = _돌린다({"items": [{"block": "1", "sid": "1-1"}]})
    생성, 검증 = steps
    assert 생성["step"] == GENERATION_STEP == "8·9_생성(스팬선택)"
    assert set(생성) >= {"step", "usage", "문장후보수", "선택수"}
    assert 검증["step"] == VERIFY_STEP == "10_검증_W대조"
    assert set(검증) >= {"step", "유지", "삭제", "없는번호", "요구역량_복원", "삭제사유"}
    assert 생성["usage"]["usd"] == 0.0     # 모의라 0원 — 진짜 호출이 없었다는 증거


def test_걸러낸_문장_수를_기록에_남긴다():
    """왜 후보가 줄었는지 나중에 셀 수 있어야 한다."""
    engine = 모의엔진({"items": []})
    steps: list[dict[str, Any]] = []
    frags = dict(조각)
    frags[3] = {"종류": "뉴스", "원문": "(2026-08-14 보도 · a.kr) 회사 주가, 8월 14일 하락 마감."}
    spanselect.select_spans(None, frags, [], "직무", steps, engine=engine)
    # 시세 기사 1문장만 빠진다. 남은 조각(MD&A 2문장 + 뉴스 2문장)은 그대로 후보다.
    assert steps[0]["제외후보수"] == 1
    assert steps[0]["문장후보수"] == 4


# ══════════════════════════════════════════════════════════
# ⑤ 엔진에 실제로 있는 이름만 부르는가
# ══════════════════════════════════════════════════════════

def _엔진이_내주는_이름() -> set[str]:
    names: set[str] = set()
    for node in ast.parse(ENGINE_PATH.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
    return names


def _내가_부르는_이름() -> set[str]:
    tree = ast.parse(inspect.getsource(spanselect))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "engine"
    }


def test_엔진에_없는_이름을_부르지_않는다():
    """★ 이름이 틀리면 «진짜로 돌리는 날» 처음 터진다. 그때는 이미 돈이 나간 뒤다."""
    missing = sorted(_내가_부르는_이름() - _엔진이_내주는_이름())
    assert not missing, f"1판 엔진에 없는 이름을 부르고 있습니다: {missing}"


def test_실제로_엔진_부품을_빌려_쓰고_있다():
    """검사가 «아무것도 안 세는» 상태로 조용히 통과하는 걸 막는다."""
    부품 = _내가_부르는_이름()
    assert {"_ask", "split_sentences", "check_draft", "DraftItem"} <= 부품


@pytest.mark.parametrize("이름", ["_ask", "split_sentences", "check_draft", "DraftItem"])
def test_모의엔진도_같은_부품_이름을_갖고_있다(이름):
    """모의가 진짜와 다른 이름을 쓰면 시험이 통과해도 실제로는 터진다."""
    assert hasattr(모의엔진, 이름)

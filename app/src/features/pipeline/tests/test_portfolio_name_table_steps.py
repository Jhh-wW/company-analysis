"""3장 대표 이름 결과가 «실행 기록(steps)»까지 실제로 닿는지 본다.

★ 왜 필요한가 — composer가 결과 객체에 필드를 실어도 그것을 steps로 옮기는
  한 줄이 없으면 운영 진단에는 영영 안 남는다. 「필드는 맞는데 기록이 없다」는
  실패는 단위 시험만으로는 안 잡힌다. 그래서 «가짜 결과»가 아니라 진짜
  `run_v2` 결과를 그대로 실행 기록 헬퍼에 넣어 본다.

★ AI·네트워크 0회. 가짜 작가가 이름을 하나도 안 쓴 부문 카드만 낸다 —
  2026-09-06 운영 실측에서 실제 작가가 낸 모양이다.
★ 픽스처의 이름은 «가공 이름»이다. 실존 회사·그룹·상품 이름을 시험에 박으면
  지워도 되돌아온다.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from src.features.composer.constants import PORTFOLIO_TABLE_HEADERS
from src.features.composer.diagram_check import (
    FLOW_REVIEW_PROMPT_HEADER,
    FLOW_REVIEW_ROW_NUMBER_PATTERN,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.tests.test_pipeline import (
    _summary_selection_json,
    summary_candidate_filler,
)
from src.features.composer.portfolio_name_table import (
    BLOCKED_NAME_NOT_IN_SOURCE,
    PORTFOLIO_NAME_TABLE_BLOCKED_STEP,
    PORTFOLIO_NAME_TABLE_STEP,
)
from src.features.composer.portfolio_names import (
    UNUSED_REPRESENTATIVE_NAMES_STEP,
)
from src.features.pipeline import real
from src.shared.name_fragments.constants import (
    NAME_KIND_IP,
    NAME_KIND_LABELS,
    compose_name_location,
)


_TITLE = "나. 주요 아티스트 전속계약"
_NAMES = ("하늘소년단", "바다소녀들", "별무리")
_ROW_TEXTS = (
    "㈜가나다뮤직 | 하늘소년단",
    "㈜가나다뮤직 | 바다소녀들",
    "㈜라마바뮤직 | 별무리",
)
_IP_LABEL = NAME_KIND_LABELS[NAME_KIND_IP]


def _fragments(*, names_in_text: bool) -> dict[int, dict[str, str]]:
    """이름 조각이 든 flat 조각.

    ``names_in_text``가 거짓이면 원문위치는 이름을 말하는데 원문에는 그 이름이
    없다 — 상류(이름 표 파서)가 보장을 잃은 상태를 재현한다. 그러면 표가
    fail-closed로 막히고 「표 불가」 사유와 기존 「미사용」 기록이 남아야 한다.
    """

    frags: dict[int, dict[str, str]] = {
        1: {"종류": "사업내용", "원문": "가나다회사는 사업부문 하나를 운영한다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객 존중을 핵심 가치로 삼는다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
    }
    for index, name in enumerate(_NAMES):
        frags[index + 3] = {
            "종류": "사업내용",
            "원문": _ROW_TEXTS[index] if names_in_text else "㈜가나다뮤직 | 소속",
            "원문위치": compose_name_location(
                f"{_TITLE} · {index * 3 + 2}행", NAME_KIND_IP, name
            ),
        }
    return frags


def _writer(prompt: str) -> str:
    if "핵심 요약" in prompt:
        # 요약은 이제 «고르기»다 — 문장을 지어내지 않고 후보 번호만 답한다.
        return _summary_selection_json(prompt)
    payload: dict[str, object] = {
        "문장들": [
            {
                "글": "가나다회사는 사업부문 하나를 운영한다.",
                "인용": ["1"],
                "등급": "확인",
            }
        ]
    }
    # 장마다 다른 문장을 하나 더 둬야 요약 후보가 3개 이상 생긴다
    # (근거는 `summary_candidate_filler` docstring).
    문장들 = payload["문장들"]
    보충 = summary_candidate_filler(prompt)
    if isinstance(문장들, list) and 보충 is not None:
        문장들.append(보충)
    if PORTFOLIO_TABLE_HEADERS[0] in prompt:
        # ★ 이름을 한 글자도 안 쓴 «부문 카드»만 낸다.
        payload["경로표"] = [
            {"칸": ["사업부문 하나", "부문 설명", "부문을 운영한다", "주력"], "인용": ["1"]}
        ]
    return json.dumps(payload, ensure_ascii=False)


def _reviewer(prompt: str) -> str:
    if FLOW_REVIEW_PROMPT_HEADER in prompt:
        numbers = re.findall(
            FLOW_REVIEW_ROW_NUMBER_PATTERN, prompt, flags=re.MULTILINE
        )
    else:
        numbers = re.findall(r"\[(\d+)\] \(등급: [^,\n]+, 인용:", prompt)
    return json.dumps(
        {"판정": [{"번호": int(value), "결과": "참"} for value in numbers]},
        ensure_ascii=False,
    )


def _steps(*, names_in_text: bool) -> list[dict[str, object]]:
    """진짜 `run_v2` 결과를 그대로 실행 기록 헬퍼에 넣는다."""

    output = run_v2(
        "가나다회사",
        _fragments(names_in_text=names_in_text),
        None,
        writer_ask=_writer,
        reviewer_ask=_reviewer,
        corp_type="상장사",
        as_of_date="2026-09-06",
    )
    return real._unused_name_steps(output)  # noqa: SLF001


def test_표가_생기면_steps에_이름수_종류별_표제목이_남는다() -> None:
    steps = _steps(names_in_text=True)

    table_steps = [
        step for step in steps if step["step"] == PORTFOLIO_NAME_TABLE_STEP
    ]
    assert table_steps == [
        {
            "step": PORTFOLIO_NAME_TABLE_STEP,
            "이름수": len(_NAMES),
            "종류별": {_IP_LABEL: len(_NAMES)},
            "표제목": [_TITLE],
        }
    ], steps
    assert all(
        step["step"] != PORTFOLIO_NAME_TABLE_BLOCKED_STEP for step in steps
    )


def test_작가가_이름을_안_쓰면_미사용도_함께_남는다() -> None:
    """두 표식은 «배타적이지 않다» — 서로 다른 것을 재기 때문이다.

    ★ 이 시험이 지키는 것 — 표를 항상 만들게 바꾼 뒤에도 「작가가 안내문을
      지켰나」를 재는 기존 지표가 살아 있어야 한다. 표가 생겼다고 미사용
      표식을 끄면 작가 순응이 나빠져도 아무도 모른다.
    """

    steps = _steps(names_in_text=True)
    names = [step["step"] for step in steps]

    assert PORTFOLIO_NAME_TABLE_STEP in names, steps
    assert UNUSED_REPRESENTATIVE_NAMES_STEP in names, steps
    unused = next(
        step for step in steps if step["step"] == UNUSED_REPRESENTATIVE_NAMES_STEP
    )
    assert unused == {
        "step": UNUSED_REPRESENTATIVE_NAMES_STEP,
        "이름수": len(_NAMES),
        "종류별": {_IP_LABEL: len(_NAMES)},
    }


def test_표가_막히면_steps에_사유가_남는다() -> None:
    """표가 항상 성공하는 픽스처만 있으면 «불가» 경로가 죽어도 초록불이다."""

    steps = _steps(names_in_text=False)

    assert {
        "step": PORTFOLIO_NAME_TABLE_BLOCKED_STEP,
        "사유": BLOCKED_NAME_NOT_IN_SOURCE,
    } in steps, steps
    assert all(step["step"] != PORTFOLIO_NAME_TABLE_STEP for step in steps)
    # 이름은 왔는데 아무도 안 썼다는 기존 표식도 함께 남는다.
    assert any(
        step["step"] == UNUSED_REPRESENTATIVE_NAMES_STEP for step in steps
    )


def test_실행기록_헬퍼가_composer_정본을_부른다() -> None:
    """단계 이름·필드를 real.py가 손으로 다시 적으면 조용히 어긋난다.

    ★ 소스를 «다시 읽어» 확인한다 — import한 모듈 객체는 옛 바이트코드를
      쓸 수 있어 같은 길이의 수정을 못 본다.
    """

    tree = ast.parse(
        Path(real.__file__).read_text(encoding="utf-8"), filename=real.__file__
    )
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_unused_name_steps"
    )
    called = {
        node.func.id
        for node in ast.walk(helper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "portfolio_name_table_steps" in called

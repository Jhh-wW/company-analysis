"""데모의 «수집 현황»이 사실을 말하는지 못 박는다 (문제로그 P-78).

★ 이 시험이 잡는 것 — **기능은 붙었는데 화면이 옛말을 하는 경우** (P-49·P-63과 같은 부류).
  홈페이지 수집은 이미 붙어 있는데(P-35 해소, `features/homepage/`), 데모 화면은
  「아직 연결되지 않음」이라고 말하고 있었다. 그러면 사용자는
  **「이 도구는 홈페이지를 못 본다」**고 잘못 안다. 없는 것은 «기능»이 아니라
  이 저장 기록이다 — 초기 조사 엔진이 기록을 만들 때는 수집 기능이 없었을 뿐이다.

★ ❌(없음)로 적어도 안 된다 — 「회사에 홈페이지가 없다」는 뜻이 되어 버린다 (P-45).
  ⚠️(우리 쪽에서 못 가져옴)를 유지하되 사유만 사실대로 적는다.
"""

from __future__ import annotations

import pytest

from src.features.pipeline import demo

#: 홈페이지 수집이 실제로 붙어 있는지 확인할 모듈 — 있으면 「미연결」은 거짓말이다.
_홈페이지_기능 = "src.features.homepage.logic"


def _홈페이지_현황(회사: str):
    record = demo._find_record(회사)
    assert record is not None, f"데모 기록이 없습니다: {회사}"
    sources = demo._sources_of(record)
    found = [s for s in sources if "홈페이지" in s.name]
    assert found, "수집 현황에 홈페이지 줄이 없습니다"
    return found[0]


def test_홈페이지_수집_기능은_실제로_붙어_있다():
    """이게 참이면 「아직 연결되지 않음」은 거짓 안내다."""
    import importlib

    module = importlib.import_module(_홈페이지_기능)

    assert hasattr(module, "collect_homepage_fragments")


@pytest.mark.parametrize("회사", ["루트로닉", "우리엔", "파마리서치"])
def test_데모는_홈페이지가_미연결이라고_말하지_않는다(회사: str):
    """★ P-78 그 자체. 「연결」이라는 말로 기능의 부재를 암시하면 안 된다."""
    status = _홈페이지_현황(회사)

    assert "연결" not in status.detail, (
        f"기능은 붙어 있는데 「미연결」이라고 말합니다: {status.detail}"
    )


@pytest.mark.parametrize("회사", ["루트로닉", "우리엔", "파마리서치"])
def test_데모_홈페이지_사유는_기록_탓임을_밝힌다(회사: str):
    """사용자가 «도구의 한계»와 «이 기록의 한계»를 구별할 수 있어야 한다."""
    status = _홈페이지_현황(회사)

    assert "데모" in status.detail
    assert "진짜 조사" in status.detail


@pytest.mark.parametrize("회사", ["루트로닉", "우리엔", "파마리서치"])
def test_데모_홈페이지는_없음이_아니라_못_가져옴이다(회사: str):
    """❌(회사에 자료 없음)로 적으면 멀쩡한 회사를 포기하게 만든다 (P-45)."""
    status = _홈페이지_현황(회사)

    assert status.state == "failed", (
        "❌(없음)로 적으면 「이 회사는 홈페이지가 없다」는 뜻이 된다"
    )

# -*- coding: utf-8 -*-
"""엔진 지문이 «원문을 모으는 코드»까지 보는지 못 박는다.

★ 왜 이 파일이 생겼나 (2026-08-28)
  ─────────────────────────────────────────────────────────
  v2 캐시 열쇠에는 「지금 코드의 지문」이 들어간다. `real.py` 주석이 그 이유를
  이렇게 적어 두었다:

    「코드가 그대로면 적중해 900원을 아끼고, **한 글자라도 바뀌면 저절로
      불일치라 옛 결과가 절대 안 나온다 — 「고쳤는데 화면이 그대로」를 막는다.**」

  **그 약속이 깨져 있었다.** 지문은 `composer/` 9개 파일만 봤는데, 원문을
  «모으는» 코드는 그 밖에 있다.

  실제로 커밋 `3f28b58`(v2-90)이 `analysis_engine/tools/run_pilot.py` 를 고쳐
  비상장 회사의 공시 원문을 **0자 → 369,310자**로 늘렸는데,
  지문이 안 바뀌어 **옛 껍데기 보고서가 계속 나왔다.**
  사용자가 재배포하고 다시 눌러도 화면이 그대로였다 — 「이거 예전 캐시 아니야?」

★ 이 시험이 지키는 것
  ① 수집 코드가 바뀌면 지문도 «반드시» 바뀐다 (옛 보고서가 안 나온다)
  ② 목록에 적힌 파일이 실제로 존재한다 (없으면 캐시가 영영 꺼진다)
"""

from __future__ import annotations

import pytest

from src.core import paths
from src.features.composer import build_id


@pytest.fixture(autouse=True)
def _지문_기억을_지운다():
    """`engine_build_id()` 는 한 번 계산하고 기억한다 — 시험마다 비운다."""
    build_id._cached_build_id = None
    yield
    build_id._cached_build_id = None


# ══════════════════════════════════════════════════════════
# ① 수집 코드가 바뀌면 지문이 바뀌는가  ← 이게 핵심이다
# ══════════════════════════════════════════════════════════


def test_원문_수집_코드가_바뀌면_지문도_바뀐다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """★ 이 시험이 2026-08-28 수정의 «이유»다.

    되돌리면 「고쳐도 옛 보고서가 그대로 나오는」 상태로 돌아간다.
    """
    원래 = build_id.engine_build_id()
    assert 원래 != build_id.UNKNOWN_BUILD_ID, "준비 실패 — 파일을 못 읽고 있다"

    # 1판 엔진 파일이 «한 글자» 바뀐 상황을 만든다.
    가짜뿌리 = tmp_path / "repo"
    for 이름 in build_id._CONTENT_MODULES + tuple(
        f"app/src/features/composer/{n}" for n in build_id._SHAPING_MODULES
    ):
        원본 = paths.PROJECT_ROOT / 이름
        사본 = 가짜뿌리 / 이름
        사본.parent.mkdir(parents=True, exist_ok=True)
        사본.write_bytes(원본.read_bytes())

    바뀐파일 = 가짜뿌리 / "analysis_engine/tools/run_pilot.py"
    바뀐파일.write_bytes(바뀐파일.read_bytes() + b"\n# v2-90\n")

    monkeypatch.setattr(paths, "PROJECT_ROOT", 가짜뿌리)
    build_id._cached_build_id = None

    바뀐뒤 = build_id.engine_build_id()

    assert 바뀐뒤 != 원래, (
        "★ 수집 코드를 고쳤는데 지문이 그대로다 — 옛 껍데기 보고서가 계속 나온다"
    )


def test_1판_엔진이_지문에_들어간다() -> None:
    """★ 이 파일을 목록에서 빼면 v2-90 과 «똑같은» 사고가 다시 난다."""
    assert "analysis_engine/tools/run_pilot.py" in build_id._CONTENT_MODULES


def test_수집_흐름_파일도_들어간다() -> None:
    """`real.py` 는 어느 공시를 쓸지·조각을 어떻게 붙일지를 정한다."""
    assert "app/src/features/pipeline/real.py" in build_id._CONTENT_MODULES
    for 조각모듈 in ("logic.py", "extra.py", "relationships.py"):
        assert f"app/src/features/filingclean/{조각모듈}" in build_id._CONTENT_MODULES


def test_새_composer모듈과_공유품질정본은_손목록없이_지문에_들어간다() -> None:
    assert "structured_claims.py" in build_id._SHAPING_MODULES
    assert "app/src/features/company_performance/logic.py" in build_id._CONTENT_MODULES
    assert "app/src/shared/report_quality/fact_binding.py" in build_id._CONTENT_MODULES
    assert "app/src/shared/report_quality/numeric.py" in build_id._CONTENT_MODULES


# ══════════════════════════════════════════════════════════
# ② 목록이 «실제로 있는» 파일을 가리키는가
# ══════════════════════════════════════════════════════════


def test_목록의_파일이_전부_존재한다() -> None:
    """★ 하나라도 없으면 지문이 UNKNOWN 이 되어 캐시가 «영영» 꺼진다.

    그러면 같은 회사를 볼 때마다 900원이 다시 나간다 — 조용히 돈이 샌다.
    """
    없는것 = [
        이름 for 이름 in build_id._CONTENT_MODULES
        if not (paths.PROJECT_ROOT / 이름).is_file()
    ]

    assert not 없는것, f"★ 목록에 있는데 파일이 없다: {없는것}"


def test_파일을_못_읽으면_캐시를_끈다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """★ 「모르는 상태」를 캐시 적중으로 바꾸지 않는다 — fail-closed."""
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "없는폴더")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID


def test_지문은_같은_코드에서_늘_같다() -> None:
    """★ 흔들리면 캐시가 영영 안 맞아 매번 900원을 쓴다."""
    첫번째 = build_id.engine_build_id()
    build_id._cached_build_id = None
    두번째 = build_id.engine_build_id()

    assert 첫번째 == 두번째
    assert len(첫번째) == build_id._DIGEST_CHARS


def test_배포_커밋이_바뀌면_손목록밖_변경이어도_지문이_바뀐다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """배포 revision은 손으로 적은 파일 목록의 마지막 안전망이다.

    새 품질·claim 모듈을 목록에 넣는 것을 사람이 잊어도, 다른 커밋으로 배포되면
    이전 생성기의 캐시를 새 생성 결과처럼 꺼내면 안 된다.
    """
    monkeypatch.setenv(
        "RENDER_GIT_COMMIT", "1111111111111111111111111111111111111111"
    )
    처음 = build_id.engine_build_id()

    monkeypatch.setenv(
        "RENDER_GIT_COMMIT", "2222222222222222222222222222222222222222"
    )
    build_id._cached_build_id = None
    바뀐뒤 = build_id.engine_build_id()

    assert 처음 != 바뀐뒤


def test_오염된_배포_커밋은_지문_재료로_신뢰하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "1234567-not-a-commit")

    오염값 = build_id.engine_build_id()
    build_id._cached_build_id = None
    monkeypatch.delenv("RENDER_GIT_COMMIT")

    assert build_id.engine_build_id() == 오염값

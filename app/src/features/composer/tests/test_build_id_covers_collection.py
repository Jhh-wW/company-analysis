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


def _생산모듈을_가짜뿌리로_복사한다(가짜뿌리) -> None:
    """현재 자동 발견된 content 모듈을 임시 프로젝트에 같은 구조로 복사한다."""

    for 이름 in build_id._content_modules(paths.PROJECT_ROOT):
        원본 = paths.PROJECT_ROOT / 이름
        사본 = 가짜뿌리 / 이름
        사본.parent.mkdir(parents=True, exist_ok=True)
        사본.write_bytes(원본.read_bytes())


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
    _생산모듈을_가짜뿌리로_복사한다(가짜뿌리)

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
    assert "analysis_engine/tools/run_pilot.py" in build_id._REQUIRED_CONTENT_MODULES


def test_수집_흐름_파일도_들어간다() -> None:
    """`real.py` 는 어느 공시를 쓸지·조각을 어떻게 붙일지를 정한다."""
    모듈 = build_id._content_modules(paths.PROJECT_ROOT)
    assert "app/src/features/pipeline/real.py" in 모듈
    for 조각모듈 in ("logic.py", "extra.py", "relationships.py"):
        assert f"app/src/features/filingclean/{조각모듈}" in 모듈


def test_생산패키지의_현재모듈은_손목록없이_지문에_들어간다() -> None:
    모듈 = build_id._content_modules(paths.PROJECT_ROOT)
    assert "structured_claims.py" in build_id._SHAPING_MODULES
    assert "app/src/features/company_performance/logic.py" in 모듈
    assert "app/src/features/homepage/logic.py" in 모듈
    assert "app/src/features/revenuemix/logic.py" in 모듈
    assert "app/src/shared/report_evidence/logic.py" in 모듈
    assert "app/src/shared/report_quality/fact_binding.py" in 모듈
    assert "app/src/shared/report_quality/numeric.py" in 모듈


@pytest.mark.parametrize(
    "새모듈",
    (
        "app/src/features/homepage/new_official_source.py",
        "app/src/features/revenuemix/new_table_source.py",
        "analysis_engine/src/features/evidence_collection/new_collector.py",
        "app/src/features/chapter_evidence/new_adapter.py",
        "app/src/features/company_comparison/new_comparator.py",
        "app/src/features/company_specificity/new_filter.py",
        "app/src/features/provenance/new_ledger.py",
        "app/src/features/spanselect/new_selector.py",
        "app/src/shared/report_evidence/new_contract.py",
    ),
)
def test_생산패키지에_새_py가_생기면_목록수정없이_지문이_바뀐다(
    새모듈: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """새 feature가 병합돼도 사람이 지문 파일 목록을 고칠 필요가 없다."""

    가짜뿌리 = tmp_path / "repo"
    _생산모듈을_가짜뿌리로_복사한다(가짜뿌리)
    monkeypatch.setattr(paths, "PROJECT_ROOT", 가짜뿌리)

    추가전 = build_id.engine_build_id()
    assert 추가전 != build_id.UNKNOWN_BUILD_ID

    추가파일 = 가짜뿌리 / 새모듈
    추가파일.parent.mkdir(parents=True, exist_ok=True)
    추가파일.write_text("OUTPUT_RULE = '새 근거 규칙'\n", encoding="utf-8")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != 추가전


@pytest.mark.parametrize(
    "무관파일",
    (
        "app/src/features/homepage/tests/test_new_source.py",
        "app/src/features/homepage/__pycache__/generated.py",
        "app/src/features/homepage/__init__.py",
        "app/src/features/homepage/conftest.py",
        "app/src/features/homepage/test_accidental.py",
        "app/src/features/homepage/readme.txt",
    ),
)
def test_시험과_패키지표식은_추가돼도_지문이_바뀌지_않는다(
    무관파일: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    가짜뿌리 = tmp_path / "repo"
    _생산모듈을_가짜뿌리로_복사한다(가짜뿌리)
    monkeypatch.setattr(paths, "PROJECT_ROOT", 가짜뿌리)

    추가전 = build_id.engine_build_id()
    추가파일 = 가짜뿌리 / 무관파일
    추가파일.parent.mkdir(parents=True, exist_ok=True)
    추가파일.write_text("시험 또는 패키지 표식\n", encoding="utf-8")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() == 추가전


def test_자동발견_결과는_중복없이_이름순이다() -> None:
    모듈 = build_id._content_modules(paths.PROJECT_ROOT)

    assert 모듈 == tuple(sorted(set(모듈)))


def test_비교와_회사고유성_내용생산자는_지문에_들어간다() -> None:
    """경쟁력 장과 일반론 제거 규칙이 바뀌면 같은 내용 캐시를 재사용하지 않는다."""

    모듈 = build_id._content_modules(paths.PROJECT_ROOT)

    assert "app/src/features/company_comparison/logic.py" in 모듈
    assert "app/src/features/company_comparison/official_sources.py" in 모듈
    assert "app/src/features/company_specificity/logic.py" in 모듈


def test_근거선별과_출처장부_내용생산자는_지문에_들어간다() -> None:
    """선택된 원문과 공개 인용 장부가 달라지면 새 보고서로 생성해야 한다."""

    모듈 = build_id._content_modules(paths.PROJECT_ROOT)

    assert "app/src/features/spanselect/canonical.py" in 모듈
    assert "app/src/features/spanselect/logic.py" in 모듈
    assert "app/src/features/provenance/citations.py" in 모듈
    assert "app/src/features/provenance/sources.py" in 모듈


def test_공식IR과_캐시_출처신원_정본은_필수파일이다() -> None:
    """공식 문서 판정·사전 source digest·사후 snapshot은 캐시 신원의 일부다."""

    필수 = set(build_id._REQUIRED_CONTENT_MODULES)

    assert "app/src/shared/official_ir.py" in 필수
    assert "app/src/shared/report_source_identity.py" in 필수
    assert "app/src/shared/generation_cache_identity.py" in 필수
    assert "app/src/features/report_delivery/source_identity.py" in 필수


@pytest.mark.parametrize(
    "내용생산자",
    (
        "app/src/features/company_comparison/logic.py",
        "app/src/features/company_specificity/logic.py",
        "app/src/features/spanselect/logic.py",
        "app/src/features/provenance/citations.py",
        "app/src/shared/official_ir.py",
        "app/src/shared/report_source_identity.py",
        "app/src/shared/generation_cache_identity.py",
        "app/src/features/report_delivery/source_identity.py",
    ),
)
def test_내용과_캐시신원_생산자_변경은_지문을_바꾼다(
    내용생산자: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """경로가 포함됐다는 주장 대신 실제 파일 변경→지문 변경을 끝까지 증명한다."""

    가짜뿌리 = tmp_path / "repo"
    _생산모듈을_가짜뿌리로_복사한다(가짜뿌리)
    monkeypatch.setattr(paths, "PROJECT_ROOT", 가짜뿌리)

    변경전 = build_id.engine_build_id()
    변경파일 = 가짜뿌리 / 내용생산자
    변경파일.write_bytes(변경파일.read_bytes() + b"\n# fingerprint-regression\n")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != 변경전


# ══════════════════════════════════════════════════════════
# ② 목록이 «실제로 있는» 파일을 가리키는가
# ══════════════════════════════════════════════════════════


def test_자동발견한_파일이_전부_존재한다() -> None:
    """★ 하나라도 없으면 지문이 UNKNOWN 이 되어 캐시가 «영영» 꺼진다.

    그러면 같은 회사를 볼 때마다 900원이 다시 나간다 — 조용히 돈이 샌다.
    """
    없는것 = [
        이름 for 이름 in build_id._content_modules(paths.PROJECT_ROOT)
        if not (paths.PROJECT_ROOT / 이름).is_file()
    ]

    assert not 없는것, f"★ 목록에 있는데 파일이 없다: {없는것}"


def test_파일을_못_읽으면_캐시를_끈다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """★ 「모르는 상태」를 캐시 적중으로 바꾸지 않는다 — fail-closed."""
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "없는폴더")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID


def test_필수_단일파일_하나가_없어도_캐시를_끈다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    가짜뿌리 = tmp_path / "repo"
    _생산모듈을_가짜뿌리로_복사한다(가짜뿌리)
    (가짜뿌리 / "analysis_engine/tools/run_pilot.py").unlink()
    monkeypatch.setattr(paths, "PROJECT_ROOT", 가짜뿌리)

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

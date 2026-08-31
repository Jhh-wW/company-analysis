"""v2 캐시를 못 박는다 — «돈은 아끼되 옛 결과는 절대 안 나온다».

★ 왜 이 시험이 있나 (오늘 실측으로 당한 사고 2건)
  ① 1층 캐시가 v2 분기보다 앞에 있어서, ENGINE_V2=1을 켜도 그 회사의 v1
     저장본이 살아 있으면 «옛 v1 보고서»가 그대로 반환됐다. 화면에는
     「이전에 조사한 결과입니다」만 뜨므로 「엔진을 고쳐도 하나도 안
     고쳐졌다」로 보였다. → v2-26에서 v2가 v1 캐시를 안 읽게 막았다.
  ② 그 대가로 같은 회사를 두 번 조사하면 두 번 다 900원이 나갔다.

★ 그래서 v2 전용 캐시를 «검증된 배포 commit»과 함께 만든다:
  - 같은 full commit이면 적중 → 돈을 아낀다
  - 배포 commit이 바뀌면 저절로 미적중 → 옛 결과가 절대 안 나온다
  - full commit이 없는 가변 로컬 트리는 캐시를 쓰지 않는다
  사람이 「이번엔 캐시를 비워야지」를 기억할 필요가 없다.
  기억에 의존하는 안전장치는 반드시 잊힌다 — 이 프로젝트가 네 번 증명했다.

★ 여기서 지키는 것:
  ① v1 캐시와 v2 캐시는 열쇠가 달라 서로의 보고서를 못 꺼낸다.
  ② 배포 commit이나 실제 DART 출처가 바뀌면 캐시가 저절로 무효가 된다.
  ③ 지문을 못 만들었으면(«모르는 상태») 읽지도 쓰지도 않는다.
  ④ v1 보고서가 v2 열쇠 아래로 들어가지 않는다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from src.core import deployment_identity
from src.shared.engine_build_identity import (
    ENGINE_BUILD_ID_CONTRACT_VERSION,
    EngineBuildIdentity,
    UNKNOWN_BUILD_ID,
    build_id_is_usable,
    capture_engine_build_identity,
    engine_build_id,
)
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.pipeline.port import Grade, Report
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.storage import cache as cache_store
from src.features.storage import db as storage_db
from src.shared import engine_build_identity

CORP_ID = "00126380"
FISCAL_YEAR = 2025
SOURCE_IDENTITY_DIGEST = "a" * 64
BUILD_A = f"{ENGINE_BUILD_ID_CONTRACT_VERSION}:{'a' * 40}"
BUILD_B = f"{ENGINE_BUILD_ID_CONTRACT_VERSION}:{'b' * 40}"
BUILD_IDENTITY_A = EngineBuildIdentity("a" * 40, BUILD_A)
UNKNOWN_BUILD_IDENTITY = EngineBuildIdentity("", UNKNOWN_BUILD_ID)


@pytest.fixture(autouse=True)
def _verified_deployment_commit(monkeypatch: pytest.MonkeyPatch):
    """캐시 통합 시험은 immutable 배포 namespace에서 돈다."""

    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * deployment_identity.COMMIT_FULL_LEN)


def _v2_report() -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        schema_version=ENGINE_V2_SCHEMA_VERSION,
        generated_at="2026-08-24",
    )


def _v1_report() -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        schema_version=CANONICAL_SCHEMA_VERSION,
        generated_at="2026-08-24",
    )


def _save(
    report: Report,
    build_identity: EngineBuildIdentity,
    source_identity_digest: str = SOURCE_IDENTITY_DIGEST,
):
    with storage_db.connect() as conn:
        return cache_store.save_v2_report(
            conn,
            corp_id=CORP_ID,
            report=report,
            build_identity=build_identity,
            source_identity_digest=source_identity_digest,
            fiscal_year=FISCAL_YEAR,
        )


def _hit(
    build_id: str,
    source_identity_digest: str = SOURCE_IDENTITY_DIGEST,
):
    with storage_db.connect() as conn:
        return cache_store.get_v2_report_hit(
            conn,
            corp_id=CORP_ID,
            build_id=build_id,
            source_identity_digest=source_identity_digest,
            current_fiscal_year=FISCAL_YEAR,
            today=dt.date(2026, 8, 24),
        )


# ══════════════════════════════════════════════════════════
# ② 배포가 바뀌면 캐시가 저절로 무효가 된다 (가장 중요)
# ══════════════════════════════════════════════════════════


def test_같은_배포commit이면_적중해서_돈을_아낀다():
    _save(_v2_report(), BUILD_IDENTITY_A)

    적중 = _hit(BUILD_A)

    assert 적중 is not None
    assert 적중.schema_version == ENGINE_V2_SCHEMA_VERSION


def test_배포commit이_바뀌면_옛_결과가_안_나온다():
    """★ 「고쳤는데 화면이 그대로」를 구조적으로 불가능하게 만든다."""
    _save(_v2_report(), BUILD_IDENTITY_A)

    assert _hit(BUILD_B) is None, (
        "배포 commit이 바뀌었는데 옛 캐시가 나왔습니다"
    )


@pytest.mark.parametrize("current_commit", ("b" * 40, ""))
def test_v2_실제저장직전_A가_B나unknown이면_행을_남기지_않는다(
    current_commit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if current_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", current_commit)
    else:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    with pytest.raises(engine_build_identity.EngineBuildIdentityChangedError):
        _save(_v2_report(), BUILD_IDENTITY_A)

    with storage_db.connect() as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache_store.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache_store.TABLE_REPORTS}"
        ).fetchone()[0] == 0


def test_v2_unknown생성뒤_commit이_생겨도_행을_남기지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    frozen = capture_engine_build_identity()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)

    assert _save(_v2_report(), frozen) is None
    with storage_db.connect() as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache_store.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0


def test_DART_출처가_바뀌면_배포가_같아도_옛_결과가_안_나온다():
    """정정공시나 재무값 정정 뒤 옛 본문을 재사용하지 않는다."""

    _save(_v2_report(), BUILD_IDENTITY_A, "a" * 64)

    assert _hit(BUILD_A, "b" * 64) is None


def test_로컬_소스가_바뀌어도_full_commit이_없으면_UNKNOWN이다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """파일 scan 지문을 없앤 것은 시험 삭제가 아니라 fail-closed 약속 교체다."""

    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    대상 = tmp_path / "dedupe.py"
    대상.write_text("VALUE = 1\n", encoding="utf-8")
    처음 = engine_build_id()

    대상.write_text("VALUE = 2\n", encoding="utf-8")

    assert 처음 == engine_build_id() == UNKNOWN_BUILD_ID
    assert not build_id_is_usable(처음)


def test_배포_commit이_바뀌면_옛_v2_캐시에_적중하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
):
    처음 = engine_build_id()
    _save(_v2_report(), capture_engine_build_identity())

    monkeypatch.setenv("RENDER_GIT_COMMIT", "2" * deployment_identity.COMMIT_FULL_LEN)
    바뀐뒤 = engine_build_id()

    assert 바뀐뒤 != 처음
    assert _hit(바뀐뒤) is None


# ══════════════════════════════════════════════════════════
# ①④ v1과 v2는 서로의 보고서를 못 꺼낸다
# ══════════════════════════════════════════════════════════


def test_v1_보고서는_v2_열쇠_아래로_안_들어간다():
    """★ 들어가면 다음 조사에서 v1이 v2인 척 나온다."""
    assert _save(_v1_report(), BUILD_IDENTITY_A) is None
    assert _hit(BUILD_A) is None


def test_v1_열쇠로_저장한_것을_v2가_못_꺼낸다():
    """★ 열쇠(namespace)가 다르다는 것을 저장 계층에서 직접 확인한다.

    save_company_report는 v1 출고 게이트(validate_publishable)를 먼저 태우므로
    여기서는 그 게이트를 지나지 않는 «저수준» 저장으로 v1 열쇠를 만든다 —
    이 시험이 보려는 것은 게이트가 아니라 «열쇠 분리»다.
    """
    with storage_db.connect() as conn:
        cache_store.save_layer1(
            conn,
            corp_id=CORP_ID,
            job=cache_store._COMPANY_ANALYSIS_PRODUCT_KEY,
            requirements=list(cache_store._COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS),
            report=_v1_report(),
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=FISCAL_YEAR,
        )

    assert _hit(engine_build_id()) is None, (
        "v1 열쇠로 저장한 보고서를 v2가 꺼냈습니다 — v1이 v2인 척 나갑니다"
    )


def test_v2_캐시에_저장한_것을_v1이_못_꺼낸다():
    _save(_v2_report(), BUILD_IDENTITY_A)

    with storage_db.connect() as conn:
        v1적중 = cache_store.get_company_report_hit(
            conn,
            corp_id=CORP_ID,
            build_id=BUILD_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=FISCAL_YEAR,
            today=dt.date(2026, 8, 24),
        )

    assert v1적중 is None


# ══════════════════════════════════════════════════════════
# ③ 「모르겠다」를 「같다」로 바꾸지 않는다
# ══════════════════════════════════════════════════════════


def test_지문을_못_만들면_읽지도_쓰지도_않는다():
    assert not build_id_is_usable(UNKNOWN_BUILD_ID)
    assert not build_id_is_usable("")

    assert _save(_v2_report(), UNKNOWN_BUILD_IDENTITY) is None
    assert _hit(UNKNOWN_BUILD_ID) is None
    assert _save(_v2_report(), BUILD_IDENTITY_A, "") is None
    assert _hit(BUILD_A, "") is None


def test_사업연도가_바뀌면_적중하지_않는다():
    """신선도(O9)는 v2에서도 그대로다 — 작년 보고서를 올해 것으로 주지 않는다."""
    _save(_v2_report(), BUILD_IDENTITY_A)

    with storage_db.connect() as conn:
        적중 = cache_store.get_v2_report_hit(
            conn,
            corp_id=CORP_ID,
            build_id=BUILD_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=FISCAL_YEAR + 1,
            today=dt.date(2026, 8, 24),
        )

    assert 적중 is None

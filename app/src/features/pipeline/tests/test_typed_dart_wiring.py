"""typed 공식 근거 수집기의 운영 배선 — FULL + kill switch 둘 다일 때만 돈다.

★ 무엇을 막는가 (34장 ch04 §3 「하면 안 되는 설계 1·2」 실측)
  브랜치 판 배선은 엔진모드·릴리즈모드·kill switch 없이 꽂혀 **v1(기본값)
  실행까지** 새 수집기의 더 좁은 판정에 걸리게 만들었다. 그리고 호출부에
  예외 경계가 없어 미검증 수집기의 결함 하나가 보고서를 강등 없이 통째로
  ``Outcome.FAILED``로 떨어뜨렸다. 이 파일은 그 둘을 실제 ``real._collect``
  경로에서 확인한다.

★ 실제 네트워크·DART·AI 호출 0건. DART 조회 경계만 가짜로 바꾸고 수집기·
  변환기·배선은 «진짜 생산 코드»가 돈다(생산 경로를 통째로 monkeypatch하지
  않는다).
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

from src.core import typed_collector_switch as switch
from src.features.pipeline import engine_mode, real
from src.features.pipeline.port import UserInput
from src.shared.report_evidence.release_mode import REPORT_RELEASE_MODE_ENV_NAME

# 가짜 1판 엔진은 파이프라인 시험이 이미 갖고 있다. 두 벌 만들면 한쪽만
# 고쳐져 조용히 어긋난다(test_collect_transport_counts.py와 같은 재사용 관례).
from src.features.pipeline.tests.test_real_cache import (  # noqa: F401
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
)

_ENGINE_SRC = pathlib.Path(__file__).resolve().parents[4].parent / "analysis_engine" / "src"
_ENGINE_FEATURE = _ENGINE_SRC / "features" / "evidence_collection"

pytestmark = pytest.mark.skipif(
    not _ENGINE_FEATURE.is_dir(),
    reason=f"엔진 typed 수집기가 이 트리에 없습니다: {_ENGINE_FEATURE}",
)

if _ENGINE_FEATURE.is_dir() and str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))

TYPED_RCEPT_NO = "20250315000001"


@pytest.fixture(autouse=True)
def _fresh_process_typed_collector_switch():
    """시험끼리 프로세스 동결 상태가 새지 않게 격리한다."""

    switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001


class _TypedDartFakeEngine(FakeEngine):
    """DART 목록·원문 조회만 진짜처럼 답하는 가짜 1판 엔진.

    ``list.json``은 pblntf_ty별로 한 번씩만 답하고, 원문은 실제 파일로 떨어뜨린다
    (``DartRuntimeFetcher``가 ``Path.read_bytes()``로 읽기 때문).
    """

    def __init__(self, raw_dir: pathlib.Path, *, limit_reached: bool = False) -> None:
        super().__init__()
        self.RAW_DIR = raw_dir
        self.limit_reached = limit_reached
        self.list_calls: list[str] = []
        self.document_calls: list[str] = []

    def get_json(
        self, endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        if endpoint == "list.json" and "pblntf_ty" in params:
            if self.limit_reached:
                from core.dart_client import DartLimitReached  # noqa: PLC0415

                raise DartLimitReached("가짜 일일 한도 소진")
            pblntf_ty = str(params["pblntf_ty"])
            self.list_calls.append(pblntf_ty)
            if pblntf_ty != "A":
                return {"status": "013"}
            return {
                "status": "000",
                "list": [
                    {
                        "rcept_no": TYPED_RCEPT_NO,
                        "report_nm": "사업보고서 (2024.12)",
                        "rcept_dt": "20250315",
                        "corp_code": CORP_ID,
                    }
                ],
            }
        return super().get_json(endpoint, params, counter)

    def download_document(
        self, rcept_no: str, raw_dir: Any, counter: Any
    ) -> pathlib.Path:
        self.document_calls.append(rcept_no)
        from features.evidence_collection.tests.fixtures import (  # noqa: PLC0415
            synthetic_documents,
        )

        directory = pathlib.Path(raw_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{rcept_no}.xml"
        path.write_text(
            synthetic_documents.LISTED_BUSINESS_REPORT_TEXT, encoding="utf-8"
        )
        return path


def _profile() -> dict[str, Any]:
    #: hm_url을 비운다 — 홈페이지·IR 수집이 네트워크를 타지 않는다.
    return {
        "status": "000",
        "corp_code": CORP_ID,
        "corp_name": "가나다전자",
        "hm_url": "",
    }


def _run_collect(
    engine: _TypedDartFakeEngine,
    *,
    generation_mode: engine_mode.EngineMode | None,
    corp_code: str = CORP_ID,
) -> tuple[dict[int, dict[str, str]], list[dict[str, Any]]]:
    steps: list[dict[str, Any]] = []
    counter = engine.UsageCounter()
    financials, years = engine.fetch_financials(CORP_ID, counter)
    frags, _tables, _text = real._collect(  # noqa: SLF001 - 생산 수집 경로 그대로
        engine,
        engine._client(),  # noqa: SLF001
        _profile(),
        UserInput(
            company="가나다전자", job=JOB, region="서울 강남구", posting_text=POSTING
        ),
        counter,
        steps,
        financials=financials,
        fin_years=years,
        filing=None,
        generation_mode=generation_mode,
        corp_code=corp_code,
    )
    return frags, steps


def _typed_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            step
            for step in steps
            if step.get("step") == real.TYPED_DART_COLLECT_STEP
        ),
        None,
    )


def _typed_fragments(frags: dict[int, dict[str, str]]) -> list[dict[str, str]]:
    return [
        fragment
        for fragment in frags.values()
        if str(fragment.get("문서ID") or "") == TYPED_RCEPT_NO
    ]


def test_v1은_typed_switch를_보지_않는다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1 경로는 ``TYPED_DART_COLLECTOR``를 **읽지도 않는다.**

    읽지 않으면 프로세스 동결 상태가 비어 있다는 성질
    (``test_typed_collector_switch.py::test_읽지_않으면_동결되지_않는다``)을
    증거로 쓴다. 같은 입력을 v2·FULL로 돌리면 동결이 실제로 일어나므로
    이 단정은 «항상 참인 공허한 단정»이 아니다.
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw")

    frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V1)

    assert switch.frozen_typed_collector_switch() is None
    assert _typed_step(steps) is None
    assert _typed_fragments(frags) == []
    assert engine.list_calls == []
    assert engine.document_calls == []

    # 양성 대조 — 같은 환경에서 v2·FULL이면 실제로 동결되고 조각이 들어온다.
    frags_v2, steps_v2 = _run_collect(
        _TypedDartFakeEngine(tmp_path / "raw2"),
        generation_mode=engine_mode.EngineMode.V2,
    )
    assert switch.frozen_typed_collector_switch() is switch.TypedCollectorSwitch.ON
    assert _typed_step(steps_v2) is not None
    assert _typed_fragments(frags_v2)


def test_switch_off이면_v2도_legacy_collect만_탄다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """스위치를 안 켜면 FULL·v2에서도 새 경로는 한 번도 안 돈다."""

    monkeypatch.delenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, raising=False)
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw")

    frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V2)

    assert _typed_step(steps) is None
    assert _typed_fragments(frags) == []
    assert engine.list_calls == []
    assert engine.document_calls == []


@pytest.mark.parametrize("mode", ["SHADOW", "ENFORCE_NO_PARTIAL"])
def test_비FULL에서는_typed_경로가_0회다(
    mode: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHADOW·ENFORCE는 사용자 결과가 불변이어야 한다(I9) — 새 수집도 0회다.

    스위치가 켜져 있어도 release mode를 «먼저» 보므로 스위치 동결조차 하지
    않는다 — 비FULL 프로세스는 이 값을 아예 안 읽는다.
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, mode)
    engine = _TypedDartFakeEngine(tmp_path / "raw")

    frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V2)

    assert _typed_step(steps) is None
    assert _typed_fragments(frags) == []
    assert engine.list_calls == []
    assert switch.frozen_typed_collector_switch() is None


def test_release_mode가_없거나_알_수_없으면_typed_경로가_0회다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """release mode 계약 위반은 v2 composer가 GATE_STOPPED로 다룬다.

    수집 단계가 그보다 앞서 «모르는 값이니 FULL로 치자»고 나가면 안 된다.
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    for raw in ("", "full", "FULL_MODE", "알수없음"):
        monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, raw)
        engine = _TypedDartFakeEngine(tmp_path / f"raw-{len(raw)}")

        frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V2)

        assert _typed_step(steps) is None, raw
        assert _typed_fragments(frags) == [], raw

    monkeypatch.delenv(REPORT_RELEASE_MODE_ENV_NAME, raising=False)
    engine = _TypedDartFakeEngine(tmp_path / "raw-none")
    frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V2)
    assert _typed_step(steps) is None
    assert _typed_fragments(frags) == []


def test_typed_경로_예외는_FAILED가_아니라_강등이다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """미검증 수집기의 예외가 조사 전체를 죽이지 않는다.

    DART 일일 한도(``DartLimitReached``)는 typed 수집기가 «일부러» 위로
    올려 보내는 예외다. 이 배선이 그것을 잡지 않으면 ``run()``의 바깥
    ``except``가 보고서를 강등 없이 ``Outcome.FAILED``로 떨어뜨린다.
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw", limit_reached=True)

    frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V2)

    step = _typed_step(steps)
    assert step is not None
    assert "DartLimitReached" in str(step.get("오류") or "")
    assert "없음" not in step, "수집 장애를 «자료 없음»으로 적으면 안 된다"
    # legacy 조각은 그대로 남아 보고서가 계속 만들어진다.
    assert _typed_fragments(frags) == []
    assert len(frags) == len(
        _run_collect(
            _TypedDartFakeEngine(tmp_path / "raw-off"), generation_mode=None
        )[0]
    )


def test_corp_code가_없으면_typed_경로가_0회다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """회사 식별자가 확정되지 않았으면 회사별 수집을 시작하지 않는다."""

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw")

    frags, steps = _run_collect(
        engine, generation_mode=engine_mode.EngineMode.V2, corp_code="   "
    )

    assert _typed_step(steps) is None
    assert _typed_fragments(frags) == []
    assert engine.list_calls == []


def test_typed_조각은_legacy_조각_뒤에_붙고_전자공시_개수에_포함된다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """typed 조각도 전자공시 근거다 — 출처 현황이 개수를 빠뜨리면 안 된다."""

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw")

    frags, steps = _run_collect(engine, generation_mode=engine_mode.EngineMode.V2)

    typed = _typed_fragments(frags)
    assert typed
    collect_step = next(step for step in steps if step.get("step") == "6_수집")
    assert collect_step["전자공시조각수"] == len(frags)
    sources = real._sources_from(steps)  # noqa: SLF001
    filing_source = next(source for source in sources if source.name == "전자공시")
    assert filing_source.state != "failed"

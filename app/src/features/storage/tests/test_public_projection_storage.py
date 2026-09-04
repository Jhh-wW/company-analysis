"""공개 봉인 projection을 «보고서 payload와 다른 표»에 저장하는 계약을 지킨다.

projection을 payload 안에 넣었더니 저장 JSON의 노드 수가
1.98배가 되어(5,318 → 10,512) 자원 상한(``MAX_DOCUMENT_NODES`` 20,000)의 여유가
절반으로 줄고, 관리자 「수정 원본 JSON」 폼의 250,000자 상한을 넘었다. 별도 표로
옮기면 payload 바이트가 원래대로 돌아가고 봉인의 성질은 그대로 남는다.

이 파일이 못 박는 것:

  · payload에는 projection이 «한 글자도» 들어가지 않는다 (바이트 동일).
  · 보고서 저장과 봉인 저장은 «한 거래»다. 봉인 저장이 실패하면 보고서도
    남지 않는다.
  · 로드는 저장된 digest를 믿지 않고 재계산해 대조하고, 생성 증거의 지문과도
    맞춘다. 어긋나면 거부한다.
  · FULL 저장본에 봉인 행이 없으면 그건 예외가 아니라 「봉인 없음」이라는
    정의된 상태다 — 화면(S5)이 그 사실을 보고 판단할 수 있어야 한다.

★ 기대값은 리터럴로 적는다. 생산 상수를 import해 같은 값끼리 비교하면 상한이
  낮아져도 시험이 따라 낮아져 아무것도 안 지킨다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from src.features.composer.tests.test_section_public_manifest import _run_full
from src.features.pipeline.port import Grade, Report
from src.features.provenance.sources import seal_collected_source
from src.features.storage import db, reports
from src.features.storage.constants import (
    TABLE_REPORT_PUBLIC_PROJECTIONS,
    TABLE_REPORTS as TABLE_REPORTS_NAME,
)
from src.features.storage.reports import report_to_dict, report_to_json
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.public_projection import build_report_digest
from src.shared.report_quality.constants import (
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    STRICT_QUALITY_CONTRACT_VERSION,
)


#: ``core/persisted_json.py``의 ``MAX_DOCUMENT_NODES``는 20,000이다. 그 절반을
#: 넘기면 보고서 하나가 상한의 대부분을 먹는다는 뜻이라 여유가 없다.
_PAYLOAD_NODE_CEILING = 10_000

#: ``web/routers/dashboard.py``의 관리자 「수정 원본 JSON」 폼 상한과 같은 값.
#: 이 숫자를 넘으면 관리자가 FULL 보고서를 수동 수정할 수 없다.
_ADMIN_CORRECTED_PAYLOAD_LIMIT = 250_000

_CORP_ID = "00123456"


def _full_report() -> Report:
    """실제 파이프라인을 통과한, 봉인이 붙은 FULL 보고서 한 벌."""

    output, _writer, _reviewer, _diagram = _run_full()
    report = output.report
    assert report.public_projection is not None
    assert report.generation_evidence is not None
    return report


def _count_nodes(value: object) -> int:
    """``persisted_json.validate_persisted_json_text``와 같은 방식으로 센다."""

    stack: list[object] = [value]
    nodes = 0
    while stack:
        item = stack.pop()
        nodes += 1
        if type(item) is dict:
            stack.extend(item.values())
        elif type(item) is list:
            stack.extend(item)
    return nodes


def _saved(tmp_path: Path, report: Report, *, report_id: str = "r1") -> Path:
    path = tmp_path / "reports.sqlite3"
    with db.connect(path) as conn:
        reports.save(conn, report_id, _CORP_ID, "분석", report)
    return path


def _enforce_report_from_full(report: Report) -> Report:
    """같은 생산 결과를 ENFORCE가 실제 저장하는 strict v2 모양으로 낮춘다."""

    assert report.quality_observation is not None
    return replace(
        report,
        release_mode=ReleaseMode.ENFORCE_NO_PARTIAL.value,
        quality_contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
        quality_observation=replace(
            report.quality_observation,
            contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
        ),
        generation_evidence=None,
        public_structure_manifest="",
        public_projection=None,
    )


def _write_report(
    writer: str,
    conn: sqlite3.Connection,
    *,
    report_id: str,
    report: Report,
) -> None:
    if writer == "save":
        reports.save(conn, report_id, _CORP_ID, "분석", report)
        return
    assert writer == "insert_new"
    assert reports.insert_new(
        conn,
        report_id,
        _CORP_ID,
        "분석",
        report,
        engine_epoch_digest="a" * 64,
    )


def _stored_row_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    reports_count = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_REPORTS_NAME}"
    ).fetchone()[0]
    projections_count = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}"
    ).fetchone()[0]
    return int(reports_count), int(projections_count)


# ══════════════════════════════════════════════════════════
# ① payload는 예전 바이트 그대로 (별도 표에 나눠 둔 목적)
# ══════════════════════════════════════════════════════════


def test_report_payload는_projection을_싣지_않아_바이트가_기존과_같다() -> None:
    """같은 보고서를 봉인 «있는 채»와 «없는 채»로 직렬화해 바이트를 맞댄다.

    이게 참이면 payload는 봉인을 별도 표로 나누기 전과 한 글자도 다르지 않다 — 이미 승인된
    PDF 출고 기록의 ``report_sha256`` 입력이 바뀌지 않는다는 뜻이기도 하다.
    """

    report = _full_report()
    without = replace(report, public_projection=None)

    assert report_to_json(report) == report_to_json(without)
    assert "public_projection" not in report_to_dict(report)


def test_FULL_payload_노드_수는_저장_상한의_절반_미만이다() -> None:
    report = _full_report()

    nodes = _count_nodes(report_to_dict(report))

    assert nodes < _PAYLOAD_NODE_CEILING, (
        f"payload 노드 {nodes}개는 상한 20,000의 절반을 넘었다 — "
        "보고서 하나가 저장 자원 상한의 대부분을 먹는다"
    )


def test_FULL_payload는_관리자_수정_폼_상한_안에_들어간다() -> None:
    report = _full_report()

    assert len(report_to_json(report)) < _ADMIN_CORRECTED_PAYLOAD_LIMIT


# ══════════════════════════════════════════════════════════
# ② 저장·로드 왕복과 digest 대조
# ══════════════════════════════════════════════════════════


def test_저장과_로드가_별도_표의_projection을_붙이고_digest를_대조한다(
    tmp_path: Path,
) -> None:
    report = _full_report()
    path = _saved(tmp_path, report)

    with db.connect(path) as conn:
        row = conn.execute(
            f"""SELECT report_id, content_sha256, display_sha256
            FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}"""
        ).fetchall()
        loaded = reports.load(conn, "r1")

    digest = build_report_digest(report.public_projection)
    assert len(row) == 1
    assert row[0]["report_id"] == "r1"
    assert row[0]["content_sha256"] == digest.content_sha256
    assert row[0]["display_sha256"] == digest.display_sha256

    assert loaded is not None
    assert loaded.public_projection == report.public_projection
    assert build_report_digest(loaded.public_projection) == digest


def test_insert_new도_projection을_같은_거래에_저장한다(tmp_path: Path) -> None:
    report = _full_report()
    path = tmp_path / "reports.sqlite3"

    with db.connect(path) as conn:
        inserted = reports.insert_new(
            conn,
            "r1",
            _CORP_ID,
            "분석",
            report,
            engine_epoch_digest=report.generation_evidence.build_identity_sha256,
        )
    assert inserted is True

    with db.connect(path) as conn:
        loaded = reports.load(conn, "r1")
    assert loaded is not None
    assert loaded.public_projection == report.public_projection


@pytest.mark.parametrize("writer", ("save", "insert_new"))
def test_쓰기경계는_재로드할수없는_엄격보고서를_SQL전에_거부한다(
    tmp_path: Path,
    writer: str,
) -> None:
    """저장은 성공하지만 다음 ``load``가 실패하는 독성 행을 만들 수 없다.

    ENFORCE는 보존된 strict v2만 받을 수 있고, FULL은 v2/v3 계약이라도 생성
    증거가 필요하다. 직렬화 가능하다는 이유만으로 이 세 객체를 넣으면 같은
    공개 ID를 다시 쓸 수 없는 append-only 경로까지 망가진다.
    """

    full = _full_report()
    enforce = _enforce_report_from_full(full)
    assert enforce.quality_observation is not None
    invalid_reports = (
        (
            "enforce-v1",
            replace(
                enforce,
                quality_contract_version=QUALITY_CONTRACT_VERSION,
                quality_observation=replace(
                    enforce.quality_observation,
                    contract_version=QUALITY_CONTRACT_VERSION,
                ),
            ),
        ),
        (
            "enforce-v3",
            replace(
                enforce,
                quality_contract_version=STRICT_QUALITY_CONTRACT_VERSION,
                quality_observation=replace(
                    enforce.quality_observation,
                    contract_version=STRICT_QUALITY_CONTRACT_VERSION,
                ),
            ),
        ),
        ("full-without-generation-evidence", replace(full, generation_evidence=None)),
    )

    for label, invalid in invalid_reports:
        path = tmp_path / f"{writer}-{label}.sqlite3"
        with db.connect(path) as conn:
            with pytest.raises(ValueError):
                _write_report(
                    writer,
                    conn,
                    report_id=f"{writer}-{label}",
                    report=invalid,
                )
            assert _stored_row_counts(conn) == (0, 0)


@pytest.mark.parametrize("writer", ("save", "insert_new"))
def test_쓰기전_재로드검증은_과거와_현재의_정상모드를_그대로_보존한다(
    tmp_path: Path,
    writer: str,
) -> None:
    """새 write 검증이 legacy·SHADOW·ENFORCE·과거 FULL을 소급 차단하지 않는다."""

    full = _full_report()
    enforce = _enforce_report_from_full(full)
    assert enforce.quality_observation is not None
    shadow = replace(
        enforce,
        release_mode="",
        quality_contract_version=QUALITY_CONTRACT_VERSION,
        quality_observation=replace(
            enforce.quality_observation,
            contract_version=QUALITY_CONTRACT_VERSION,
        ),
    )
    legacy = Report(
        company="과거기업",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
    )
    # projection 별도 표 도입 전에 저장된 FULL은 generation evidence 안의
    # 지문을 보존하되 projection 행 자체는 없을 수 있다는 기존 읽기 정책이다.
    full_without_projection = replace(full, public_projection=None)
    valid_reports = (
        ("legacy", legacy),
        ("shadow", shadow),
        ("enforce-v2", enforce),
        ("full-without-projection", full_without_projection),
    )

    for label, candidate in valid_reports:
        path = tmp_path / f"{writer}-{label}.sqlite3"
        report_id = f"{writer}-{label}"
        with db.connect(path) as conn:
            _write_report(
                writer,
                conn,
                report_id=report_id,
                report=candidate,
            )
            restored = reports.load(conn, report_id)
        assert restored == candidate


@pytest.mark.parametrize("writer", ("save", "insert_new"))
def test_쓰기경계는_JSON을_한번만_만들어_그_문자열을_그대로_저장한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer: str,
) -> None:
    candidate = Report(
        company="직렬화확인기업",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
    )
    expected = report_to_json(candidate)
    original = reports.report_to_json
    calls: list[Report] = []

    def counted(report: Report) -> str:
        calls.append(report)
        return original(report)

    monkeypatch.setattr(reports, "report_to_json", counted)
    path = tmp_path / f"{writer}-single-json.sqlite3"
    with db.connect(path) as conn:
        _write_report(writer, conn, report_id="single-json", report=candidate)
        stored = conn.execute(
            f"SELECT payload_json FROM {TABLE_REPORTS_NAME} WHERE report_id = ?",
            ("single-json",),
        ).fetchone()[0]

    assert calls == [candidate]
    assert stored == expected


def test_저장된_projection_행의_display_digest를_바꾸면_로드가_거부된다(
    tmp_path: Path,
) -> None:
    path = _saved(tmp_path, _full_report())

    with db.connect(path) as conn:
        conn.execute(
            f"UPDATE {TABLE_REPORT_PUBLIC_PROJECTIONS} SET display_sha256 = ?",
            ("0" * 64,),
        )

    with db.connect(path) as conn:
        with pytest.raises(ValueError):
            reports.load(conn, "r1")


def test_저장된_projection_본문을_바꾸면_로드가_거부된다(tmp_path: Path) -> None:
    """digest 열은 그대로 두고 «보이는 글자»만 바꾼 위조."""

    path = _saved(tmp_path, _full_report())

    with db.connect(path) as conn:
        stored = conn.execute(
            f"SELECT projection_json FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}"
        ).fetchone()
        payload = json.loads(stored["projection_json"])
        display = payload["sections"][0]["display"]
        assert display["sentences"], "첫 장에 문장이 있어야 이 위조가 성립한다"
        display["sentences"][0][0] = "위조된 문장이다."
        conn.execute(
            f"UPDATE {TABLE_REPORT_PUBLIC_PROJECTIONS} SET projection_json = ?",
            (json.dumps(payload, ensure_ascii=False),),
        )

    with db.connect(path) as conn:
        with pytest.raises(ValueError):
            reports.load(conn, "r1")


def test_저장된_projection이_생성_증거의_지문과_다르면_로드가_거부된다(
    tmp_path: Path,
) -> None:
    """다른 실행의 봉인을 digest 열까지 통째로 갈아 끼운 바꿔치기.

    ★ 이 위조는 봉인 자체의 앞뒤가 맞아 재계산 대조를 통과한다. 생성 증거와
      맞대보는 검사만이 잡을 수 있다.
    """

    victim = _full_report()
    other, _w, _r, _d = _run_full(flow=True)
    assert other.report.public_projection != victim.public_projection

    path = _saved(tmp_path, victim)
    other_digest = build_report_digest(other.report.public_projection)
    with db.connect(path) as conn:
        conn.execute(
            f"""UPDATE {TABLE_REPORT_PUBLIC_PROJECTIONS}
            SET projection_json = ?, content_sha256 = ?, display_sha256 = ?""",
            (
                json.dumps(
                    reports.public_projection_payload(
                        other.report.public_projection
                    ),
                    ensure_ascii=False,
                ),
                other_digest.content_sha256,
                other_digest.display_sha256,
            ),
        )

    with db.connect(path) as conn:
        with pytest.raises(ValueError):
            reports.load(conn, "r1")


# ══════════════════════════════════════════════════════════
# ③ 「봉인 없음」은 예외가 아니라 정의된 상태
# ══════════════════════════════════════════════════════════


def test_FULL인데_projection_행이_없으면_봉인_없음_상태로_읽힌다(
    tmp_path: Path,
) -> None:
    """S5가 「이 보고서에는 봉인이 없다」를 «보고» 판단할 수 있어야 한다.

    여기서 예외를 던지면 옛 저장본이 화면에서 통째로 안 열린다. 봉인이 없다는
    사실은 감춰지지 않고 ``public_projection is None``으로 그대로 드러난다.
    """

    path = _saved(tmp_path, _full_report())
    with db.connect(path) as conn:
        conn.execute(f"DELETE FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}")

    with db.connect(path) as conn:
        loaded = reports.load(conn, "r1")

    assert loaded is not None
    assert loaded.release_mode == "FULL"
    assert loaded.public_projection is None
    assert loaded.generation_evidence is not None


def test_projection_표가_없는_옛_DB도_봉인_없음으로_읽힌다(tmp_path: Path) -> None:
    """이 표가 생기기 전에 만들어진 읽기 전용 DB를 흉내낸다."""

    path = _saved(tmp_path, _full_report())
    with db.connect(path) as conn:
        conn.execute(f"DROP TABLE {TABLE_REPORT_PUBLIC_PROJECTIONS}")

    with sqlite3.connect(path) as raw:
        raw.row_factory = sqlite3.Row
        loaded = reports.load(raw, "r1")

    assert loaded is not None
    assert loaded.public_projection is None


def test_projection_없는_보고서를_다시_저장하면_옛_봉인_행이_남지_않는다(
    tmp_path: Path,
) -> None:
    """덮어쓴 본문에 옛 봉인이 붙어 있으면 화면과 장부가 어긋난다."""

    report = _full_report()
    path = _saved(tmp_path, report)

    with db.connect(path) as conn:
        reports.save(
            conn, "r1", _CORP_ID, "분석", replace(report, public_projection=None)
        )

    with db.connect(path) as conn:
        remaining = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}"
        ).fetchone()[0]
        loaded = reports.load(conn, "r1")

    assert remaining == 0
    assert loaded is not None
    assert loaded.public_projection is None


# ══════════════════════════════════════════════════════════
# ④ 한 거래
# ══════════════════════════════════════════════════════════


def test_projection_저장_실패는_보고서_저장을_되돌린다(tmp_path: Path) -> None:
    """봉인이 안 남으면 보고서도 안 남는다.

    ★ 봉인 표를 거래 «안에서» 없애 저장을 실패시킨다. 생산 코드를 가짜로
      바꿔치기하지 않고 실제 SQLite가 내는 오류로 경로를 태운다.
    """

    report = _full_report()
    path = tmp_path / "reports.sqlite3"
    with db.connect(path):
        pass  # schema만 만들어 둔다

    with pytest.raises(sqlite3.OperationalError):
        with db.connect(path) as conn:
            conn.execute(f"DROP TABLE {TABLE_REPORT_PUBLIC_PROJECTIONS}")
            reports.save(conn, "r1", _CORP_ID, "분석", report)

    with db.connect(path) as conn:
        assert reports.exists(conn, "r1") is False
        assert reports.load(conn, "r1") is None


# ══════════════════════════════════════════════════════════
# ⑤ 봉인을 «누가» 붙이는가 — S4~S6이 먼저 읽어야 할 계약
# ══════════════════════════════════════════════════════════


def test_payload_문자열에서_되살린_보고서에는_봉인이_붙지_않는다() -> None:
    """봉인은 표에 있으므로 payload만으로는 절대 되살아나지 않는다.

    ★ 이건 결함이 아니라 봉인을 별도 표에 둔 데서 오는 «직접적인 결과»다. 그런데 그 결과가
      화면까지 이어지면 「봉인이 있는데도 없다고 그리는」 보고서가 생긴다.
      payload 문자열에서 Report를 다시 만드는 생산 경로가 실제로 있다
      (실측, 전부 web 계층):

        · ``web/report_delivery_adapter.py`` ``load_public_delivery`` —
          공개 결과 화면이 그리는 본문이 여기서 온다
          (``routers/reports.py`` ``_stored_public_delivery`` →
          ``_render_result_page(report=stored_delivery.report)``).
        · ``web/routers/reports.py`` ``_approved_report`` 와
          ``web/job_runtime.py`` 의 관리자 승인 snapshot 갈래.
        · ``web/generation_singleflight.py`` 의 캐시 재사용 갈래.

      그 경로들은 ``attach_public_projection(conn, report_id, report)``을
      명시적으로 불러야 한다. 저장층이 대신 해 줄 수 없다 — payload 문자열만
      가진 쪽은 ``report_id``와 연결을 함께 들고 있는 호출부뿐이다.
    """

    report = _full_report()
    assert report.public_projection is not None

    restored = reports.report_from_json(report_to_json(report))

    assert restored.public_projection is None


def test_attach_public_projection이_봉인을_붙이고_digest를_대조한다(
    tmp_path: Path,
) -> None:
    """payload에서 되살린 보고서에 봉인을 다시 붙이는 공용 입구."""

    report = _full_report()
    path = _saved(tmp_path, report)
    restored = reports.report_from_json(report_to_json(report))

    with db.connect(path) as conn:
        attached = reports.attach_public_projection(conn, "r1", restored)

    assert attached.public_projection == report.public_projection


def test_attach_public_projection도_증거_지문과_어긋나면_거부한다(
    tmp_path: Path,
) -> None:
    """봉인을 붙이는 입구가 하나면 검사도 한 곳에서 끝난다."""

    victim = _full_report()
    other, _w, _r, _d = _run_full(flow=True)
    path = _saved(tmp_path, victim)
    other_digest = build_report_digest(other.report.public_projection)

    with db.connect(path) as conn:
        conn.execute(
            f"""UPDATE {TABLE_REPORT_PUBLIC_PROJECTIONS}
            SET projection_json = ?, content_sha256 = ?, display_sha256 = ?""",
            (
                json.dumps(
                    reports.public_projection_payload(
                        other.report.public_projection
                    ),
                    ensure_ascii=False,
                ),
                other_digest.content_sha256,
                other_digest.display_sha256,
            ),
        )

    restored = reports.report_from_json(report_to_json(victim))
    with db.connect(path) as conn:
        with pytest.raises(ValueError):
            reports.attach_public_projection(conn, "r1", restored)


# ══════════════════════════════════════════════════════════
# ⑤-2 봉인 안 출처의 수집 도장도 읽을 때 다시 본다
# ══════════════════════════════════════════════════════════


def _with_projection(report: Report, projection) -> Report:
    """봉인을 갈아 끼우고 생성 증거의 지문도 함께 맞춘 저장본 재료.

    저장소에 직접 쓸 수 있는 쪽이 실제로 만들 수 있는 상태다 — 열쇠 없는
    해시는 전부 다시 계산해 앞뒤가 맞는다.
    """

    evidence = replace(
        report.generation_evidence,
        public_projection_sha256=build_report_digest(projection).content_sha256,
    )
    return replace(report, public_projection=projection, generation_evidence=evidence)


def _sealed_citation_report() -> Report:
    """부록 행마다 «실제 수집 도장»이 찍힌 FULL 저장본.

    시험 재료를 만드는 조립기는 도장 없이 부록을 만든다. 운영 수집 경계는
    모든 출처에 도장을 찍으므로, 읽는 경계의 도장 점검을 확인하려면 재료
    쪽을 운영과 같은 상태로 올려 둬야 한다.
    """

    report = _full_report()
    projection = report.public_projection
    rows = []
    for row in projection.citations:
        source = reports._citation_from_dict(dict(row.source))  # noqa: SLF001
        rows.append(
            replace(
                row,
                source={
                    **dict(row.source),
                    "provenance_seal": seal_collected_source(source).provenance_seal,
                },
            )
        )
    return _with_projection(report, replace(projection, citations=tuple(rows)))


def _tampered_first_citation(report: Report, **changes: str) -> Report:
    first = report.public_projection.citations[0]
    rows = (
        replace(first, source={**dict(first.source), **changes}),
        *report.public_projection.citations[1:],
    )
    return _with_projection(
        report, replace(report.public_projection, citations=rows)
    )


def test_도장이_찍힌_봉인은_그대로_붙는다(tmp_path: Path) -> None:
    """반대 경우 시험 — 새 점검이 정상 저장본을 막지 않는다."""

    report = _sealed_citation_report()
    path = _saved(tmp_path, report)
    restored = reports.report_from_json(report_to_json(report))

    with db.connect(path) as conn:
        attached = reports.attach_public_projection(conn, "r1", restored)

    assert attached.public_projection == report.public_projection


def test_봉인_속_출처를_고치면_지문을_다시_계산해도_붙이지_않는다(
    tmp_path: Path,
) -> None:
    """★ 왜 필요한가 — 여기까지의 검사는 전부 열쇠 없는 해시라, 저장소에 직접
    쓸 수 있는 쪽은 출처를 고친 뒤 지문 세 개를 다시 계산해 통과시킬 수 있다.
    수집 도장만 저장소 밖 열쇠로 찍혀 있으므로 읽을 때 그 도장을 다시 본다.
    """

    forged = _tampered_first_citation(
        _sealed_citation_report(), host="news.example"
    )
    path = _saved(tmp_path, forged, report_id="forged")
    restored = reports.report_from_json(report_to_json(forged))

    with db.connect(path) as conn:
        # 위조가 성립하는지 먼저 확인한다 — 봉인 자체의 앞뒤는 맞는다.
        assert reports.load_public_projection(conn, "forged") is not None
        with pytest.raises(ValueError):
            reports.attach_public_projection(conn, "forged", restored)


def test_봉인_속_전체문서지문을_추가변조해도_공개봉인을_붙이지_않는다(
    tmp_path: Path,
) -> None:
    """공개 projection digest를 다시 맞춰도 수집 때 없던 문서 지문은 못 만든다."""

    forged = _tampered_first_citation(
        _sealed_citation_report(),
        document_content_sha256="e" * 64,
    )
    path = _saved(tmp_path, forged, report_id="forged-document-hash")
    restored = reports.report_from_json(report_to_json(forged))

    with db.connect(path) as conn:
        assert reports.load_public_projection(conn, "forged-document-hash") is not None
        with pytest.raises(ValueError):
            reports.attach_public_projection(
                conn,
                "forged-document-hash",
                restored,
            )


def test_봉인_속_출처의_도장만_지워도_붙이지_않는다(tmp_path: Path) -> None:
    """한 줄만 도장을 비워 점검을 피해 가는 길도 함께 막는다."""

    forged = _tampered_first_citation(_sealed_citation_report(), provenance_seal="")
    path = _saved(tmp_path, forged, report_id="blanked")
    restored = reports.report_from_json(report_to_json(forged))

    with db.connect(path) as conn:
        with pytest.raises(ValueError):
            reports.attach_public_projection(conn, "blanked", restored)


# ══════════════════════════════════════════════════════════
# ⑥ 증거가 없으면 봉인을 붙이지 않는다
# ══════════════════════════════════════════════════════════


def _strip_strict_fields(payload: dict) -> dict:
    """FULL payload에서 엄격 생산 증거 흔적만 걷어낸 «약한» 저장본.

    `report_from_dict`가 받아들이는 비-FULL 모양이어야 한다 — 그래야 이 위조가
    저장층의 다른 검사에 먼저 걸리지 않고 봉인 부착 자리까지 도달한다.
    """

    weak = dict(payload)
    for key in (
        "release_mode",
        "generation_evidence",
        "public_structure_manifest",
        "quality_contract_version",
    ):
        weak.pop(key, None)
    return weak


def test_생성_증거가_없는_저장본에_봉인_행이_있으면_로드가_거부된다(
    tmp_path: Path,
) -> None:
    """봉인을 «자기 정합만 보고» 붙이면 아무도 지목하지 않은 봉인이 붙는다.

    ★ 왜 위험한가 — 봉인의 진짜 권위는 생성 증거의
      ``public_projection_sha256``이다. 증거가 없으면 「이 봉인이 이 보고서의
      것」이라고 말해 주는 것이 아무것도 없고, 남는 검사는 봉인 스스로의 앞뒤가
      맞는지뿐이다. 그건 DB에 직접 넣은 봉인도 통과한다. 그래서 붙이지 않고
      닫는다.

    ★ 정상 SHADOW 저장본은 애초에 봉인 행이 없어 이 자리에 오지 않는다 —
      아래 시험이 그 사실을 함께 못 박는다.
    """

    report = _full_report()
    path = _saved(tmp_path, report)

    with db.connect(path) as conn:
        conn.execute(
            f"UPDATE {TABLE_REPORTS_NAME} SET payload_json = ? WHERE report_id = ?",
            (
                json.dumps(
                    _strip_strict_fields(report_to_dict(report)),
                    ensure_ascii=False,
                ),
                "r1",
            ),
        )
        # 위조가 성립하는지 먼저 확인한다 — 봉인 행은 그대로 남아 있고
        # 자기 정합성도 유지된다.
        assert reports.load_public_projection(conn, "r1") is not None

    with db.connect(path) as conn:
        with pytest.raises(ValueError):
            reports.load(conn, "r1")


def test_봉인_행이_없는_약한_저장본은_그대로_봉인_없음으로_읽힌다(
    tmp_path: Path,
) -> None:
    """반대 경우 시험 — 위 규칙이 정상 SHADOW 저장본을 막지 않는다."""

    report = _full_report()
    path = _saved(tmp_path, report)

    with db.connect(path) as conn:
        conn.execute(
            f"UPDATE {TABLE_REPORTS_NAME} SET payload_json = ? WHERE report_id = ?",
            (
                json.dumps(
                    _strip_strict_fields(report_to_dict(report)),
                    ensure_ascii=False,
                ),
                "r1",
            ),
        )
        conn.execute(f"DELETE FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}")

    with db.connect(path) as conn:
        loaded = reports.load(conn, "r1")

    assert loaded is not None
    assert loaded.public_projection is None

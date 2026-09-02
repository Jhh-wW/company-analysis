"""공개 봉인 projection을 «보고서 payload와 다른 표»에 저장하는 계약을 지킨다.

root 결정 C(2026-09-02) — projection을 payload 안에 넣었더니 저장 JSON의 노드 수가
1.98배가 되어(5,318 → 10,512) 자원 상한(``MAX_DOCUMENT_NODES`` 20,000)의 여유가
절반으로 줄고, 관리자 「수정 원본 JSON」 폼의 250,000자 상한을 넘었다. 별도 표로
옮기면 payload 바이트가 원래대로 돌아가고 봉인의 성질은 그대로 남는다.

이 파일이 못 박는 것:

  · payload에는 projection이 «한 글자도» 들어가지 않는다 (바이트 동일).
  · 보고서 저장과 봉인 저장은 «한 거래»다 (I11). 봉인 저장이 실패하면 보고서도
    남지 않는다.
  · 로드는 저장된 digest를 믿지 않고 재계산해 대조하고, 생성 증거의 지문과도
    맞춘다 (I3). 어긋나면 거부한다.
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
from src.features.pipeline.port import Report
from src.features.storage import db, reports
from src.features.storage.constants import TABLE_REPORT_PUBLIC_PROJECTIONS
from src.features.storage.reports import report_to_dict, report_to_json
from src.shared.report_generation.public_projection import build_report_digest


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


# ══════════════════════════════════════════════════════════
# ① payload는 예전 바이트 그대로 (root 결정 C의 목적)
# ══════════════════════════════════════════════════════════


def test_report_payload는_projection을_싣지_않아_바이트가_기존과_같다() -> None:
    """같은 보고서를 봉인 «있는 채»와 «없는 채»로 직렬화해 바이트를 맞댄다.

    이게 참이면 payload는 이 티켓 이전과 한 글자도 다르지 않다 — 이미 승인된
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
# ② 저장·로드 왕복과 digest 대조 (I3)
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
# ④ 한 거래 (I11)
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

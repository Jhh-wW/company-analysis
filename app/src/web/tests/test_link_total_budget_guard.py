"""`/run`이 링크의 «수명 전체 누적 상한»을 실제로 막는지 못 박는다.

★ 이 시험이 지키는 것 — **판단이 맞아도 `/run`에 안 걸려 있으면 소용없다.**
  `sharelink/tests/test_link_total_budget.py`는 판단만 본다. 그 판단이 진짜
  요청 경로에 연결됐는지는 여기서 본다.

★ 소진 뒤에도 «미리 준비된 보고서»는 계속 열린다 — 그건 파이프라인을 안 거쳐
  0원이고 예산 검사 밖이다. 막는 것은 «새로 AI를 부르는 일»뿐이다.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import CompanyCard
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    PUBLIC_BUCKET,
)
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.web import job_runtime, main, paid_runtime, request_helpers, runtime
from src.web.routers import reports as reports_router
from src.web.tests._visible_text import visible_text

_열쇠 = "b1b2c3d4e5f60718b1b2c3d4e5f60718"
_시각 = "2026-09-02T09:00:00+09:00"

#: 누적 상한값과 그 경계. ★ 생산 상수를 import해 비교하면 값이 몰래 낮아져도
#:  시험이 통과한다. 여기서는 리터럴로 못 박고, 상수 자체는 sharelink 시험이 본다.
_누적상한 = 3000.0
_상한직전 = 2999.0

#: 누적 소진 화면이 반드시 말하는 두 가지. 화면은 앞 문장을 제목으로, 뒤 문장을
#: 본문으로 «나눠» 그린다 — 이어 붙인 한 문장으로 찾으면 화면이 맞아도 못 찾는다.
_누적소진_제목 = "이 링크의 이용 한도를 모두 사용했습니다"
_누적소진_안내 = "미리 준비된 회사 보고서는 계속 볼 수 있습니다."


def _누적소진화면인가(응답본문: str) -> bool:
    """제목이 «한 번만» 나오고 「그래도 볼 수 있는 것」 안내가 같이 있는가.

    ★ 제목 1회를 못 박는 이유 — 제목과 본문이 같은 문장을 되풀이하면 손님은
      위아래로 같은 말을 두 번 읽고 그 아래의 안내를 놓친다.
    """
    보이는_글 = visible_text(응답본문)
    return 보이는_글.count(_누적소진_제목) == 1 and _누적소진_안내 in 보이는_글


class _돈이드는가짜파이프라인:
    """`DemoPipeline`이 아니어야 «돈이 드는» 것으로 본다."""

    def run(self, *args, **kwargs):                # pragma: no cover - 안 부른다
        raise AssertionError("막혔어야 하는데 조사가 시작됐습니다")


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app, base_url="https://testserver") as test_client:
        yield test_client


def _링크발급(key: str = _열쇠, company: str = "카카오", report_id: str = "") -> None:
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=key,
            company=company,
            job="마케팅",
            report_id=report_id,
            now_iso=_시각,
        )


def _결속보고서를_굽는다(key: str = _열쇠) -> str:
    """이 링크에 «미리 준비된 회사 보고서»를 하나 붙여 둔다."""
    report_id = uuid.uuid4().hex
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
    reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="link-total-budget-test",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        engine_build_identity=(
            build_identity_contract.process_engine_build_identity()
        ),
    )
    _링크발급(key, report.company, report_id=report_id)
    return report_id


def _끝난조사를_넣는다(*, key: str = _열쇠, run_id: str, 원가: float) -> None:
    """실측 원가가 확정된 종결 실행 한 건."""
    with storage_db.connect() as conn:
        assert share_store.start_run(
            conn,
            key=key,
            run_id=run_id,
            started_at=_시각,
            input_company="카카오",
            confirmed_company="카카오",
            company_id="corp-1",
        )
        assert share_store.finish_run(
            conn,
            run_id=run_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at=_시각,
            report_id=run_id,
            internal_ai_cost_krw=원가,
        )


def _진행중조사를_넣는다(
    *,
    key: str = _열쇠,
    run_id: str,
    예약액: float,
    state: str = "ACTIVE",
) -> None:
    """아직 안 끝난 실행 하나와 그 실행이 잡아 둔 예약 원장 행."""
    활성 = state == "ACTIVE"
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        assert share_store.start_run(
            conn,
            key=key,
            run_id=run_id,
            started_at=_시각,
            input_company="네이버",
            confirmed_company="네이버",
            company_id="corp-2",
        )
        conn.execute(
            """
            INSERT INTO budget_phase_accounts (
                run_id, phase, day, bucket_id, state, reservation_krw,
                lease_owner_id, lease_expires_at, started_at, updated_at,
                version
            )
            VALUES (?, 'report', ?, 'bucket-1', ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                run_id,
                clock.today_kst().isoformat(),
                state,
                예약액 if 활성 else 0.0,
                "owner-1" if 활성 else None,
                "2026-09-02T10:00:00+09:00" if 활성 else None,
                _시각,
                _시각,
            ),
        )
        conn.commit()


def _조사시작(client: TestClient):
    """열쇠 손님이 «새 조사»를 누른 것과 같은 요청."""
    form = {
        "company": "카카오",
        "job": "마케팅",
        "region": "서울",
        "posting_text": "x",
        "legal_name": "카카오",
        "ref": "재수집-p003",
        "address": "-",
    }
    비밀 = client.cookies.get(KEY_COOKIE_NAME) or ""
    if 비밀:
        form["csrf_token"] = auth_logic.csrf_token_for_session(비밀)
    token = uuid.uuid4().hex
    사용자입력 = request_helpers.company_analysis_input(
        company=form["company"], region=form["region"]
    )
    job_runtime._PAID_ATTEMPTS[token] = job_runtime.PaidAttempt(
        token=token,
        run_id=f"link-total-budget-{token}",
        user_input=사용자입력,
        card=CompanyCard(
            legal_name=사용자입력.company,
            typed_name=사용자입력.company,
            address="서울",
            ceo="",
            founded="",
            ref="link-total-budget",
        ),
        share_key=비밀 or PUBLIC_BUCKET,
        bucket_id=spend_store.bucket_id(비밀 or PUBLIC_BUCKET),
        lookup_cost_krw=0.0,
        models=(),
        elapsed_sec=0.0,
        created_at=time.monotonic(),
    )
    form["paid_attempt_token"] = token
    return client.post("/run", data=form, follow_redirects=False)


def _요청() -> Request:
    """`_guard_run`에 바로 넘길 최소 요청 하나."""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "scheme": "http",
            "method": "POST",
            "path": "/run",
            "raw_path": b"/run",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8020")],
            "server": ("127.0.0.1", 8020),
            "client": ("127.0.0.1", 50123),
        }
    )


def _하루를_다_쓴다(통장: str = _열쇠, 금액: float = 3000.0) -> None:
    오늘 = clock.today_kst()
    paid_runtime._LINK_SPEND = share_logic.add_spend(
        share_logic.DailySpend(day=오늘), 통장, 오늘, 금액
    )


# ══════════════════════════════════════════════════════════
# ① 누적이 차면 새 조사를 막고, 준비된 보고서는 그대로 연다
# ══════════════════════════════════════════════════════════


def test_링크_누적원가가_상한에_닿으면_새조사를_막고_결속보고서는_연다(
    client: TestClient, monkeypatch
):
    """★ 이 검사의 핵심. 하루 예산이 멀쩡해도 누적이 차면 새 조사는 없다."""
    report_id = _결속보고서를_굽는다()
    _끝난조사를_넣는다(run_id="run-1", 원가=_누적상한)
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    막힘 = _조사시작(client)
    열림 = client.get(f"/k/{_열쇠}", follow_redirects=False)
    보고서 = client.get(f"/result/{report_id}", follow_redirects=False)

    assert 막힘.status_code == 429
    assert _누적소진화면인가(막힘.text)
    # ★ 하루 소진 문구를 대신 보여 주면 「내일 다시 열린다」는 거짓말이 된다.
    assert "내일 다시 열립니다" not in 막힘.text
    # ★ 준비된 보고서는 계속 열린다 (제품 결정).
    # ★ 기대값 이전: 도착지가 결과에서 첫 화면(랜딩)으로 바뀌었다.
    #   한도를 다 써도 랜딩의 보고서 버튼과 그 결과 화면은 그대로 열려야 한다.
    assert 열림.status_code == 303
    assert 열림.headers["location"] == "/"
    assert f'href="/result/{report_id}"' in client.get("/").text
    assert 보고서.status_code == 200


def test_누적_소진화면은_내부용어를_보여주지_않는다(
    client: TestClient, monkeypatch
):
    """★ 손님은 통장·원장·LINK 같은 코드 용어를 알 필요가 없다."""
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=_누적상한)
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    본문 = _조사시작(client).text

    for 금지어 in ("bucket", "internal_ai_cost", "total_budget_krw", "KRW"):
        assert 금지어 not in 본문
    # 막다른 길은 만들지 않는다 — 기존 차단 화면 계약 그대로다.
    assert "다른 회사 둘러보기" in 본문


def test_누적_소진은_고장이_아니라고_말한다(client: TestClient, monkeypatch):
    """★ 정상 동작이다. 「오류」로 보이면 못 쓰는 물건이라 판단하고 떠난다."""
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=_누적상한)
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    본문 = _조사시작(client).text

    assert "고장이 아닙니다" in 본문
    assert "문의 번호" not in 본문


# ══════════════════════════════════════════════════════════
# ② 경계 — 2,999 / 3,000
# ══════════════════════════════════════════════════════════


def _링크갈래() -> tuple:
    return (share_tracks.Track.LINK, _열쇠, 3000.0)


def test_누적이_상한_직전이면_새조사가_열린다(monkeypatch):
    """★ 반대 경우 시험 — 상한에 안 닿았는데 막으면 그냥 고장이다."""
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=_상한직전)
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())

    assert request_helpers._guard_run(
        _요청(), count_start=False, resolved_track=_링크갈래()
    ) is None


def test_누적이_정확히_상한이면_막는다(monkeypatch):
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=_누적상한)
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())

    막힘 = request_helpers._guard_run(
        _요청(), count_start=False, resolved_track=_링크갈래()
    )

    assert 막힘 is not None
    assert 막힘.status_code == 429
    assert _누적소진화면인가(막힘.body.decode("utf-8"))


# ══════════════════════════════════════════════════════════
# ③ 진행 중 예약도 누적에 센다
# ══════════════════════════════════════════════════════════


def test_진행중_예약을_더해_상한에_닿으면_새조사를_막는다(monkeypatch):
    """★ 이게 없으면 조사가 도는 «동안» 새 조사가 계속 통과해 천장을 넘는다."""
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=2100.0)
    _진행중조사를_넣는다(run_id="run-2", 예약액=900.0)
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())

    막힘 = request_helpers._guard_run(
        _요청(), count_start=False, resolved_track=_링크갈래()
    )

    assert 막힘 is not None
    assert _누적소진화면인가(막힘.body.decode("utf-8"))


def test_끝난_단계의_예약은_누적에_두번_세지_않는다(monkeypatch):
    """★ 반대 경우 시험 — 정산된 단계까지 더하면 실제보다 비싸게 막는다."""
    _링크발급()
    _진행중조사를_넣는다(run_id="run-1", 예약액=0.0, state="SUCCEEDED")
    with storage_db.connect() as conn:
        assert share_store.finish_run(
            conn,
            run_id="run-1",
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at=_시각,
            report_id="run-1",
            internal_ai_cost_krw=2100.0,
        )
        conn.commit()
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())

    assert request_helpers._guard_run(
        _요청(), count_start=False, resolved_track=_링크갈래()
    ) is None


# ══════════════════════════════════════════════════════════
# ④ 하루 상한은 그대로 작동한다
# ══════════════════════════════════════════════════════════


def test_하루_상한은_그대로_작동한다(client: TestClient, monkeypatch):
    """★ 누적을 넣느라 하루 상한을 무력화하지 않았다는 반대 경우 시험."""
    _링크발급()                                   # 누적 사용액 0원
    _하루를_다_쓴다()
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    막힘 = _조사시작(client)

    assert 막힘.status_code == 429
    # 하루 소진은 «내일 열린다»가 사실이므로 문구가 달라야 한다.
    assert "이 링크로 돌릴 수 있는 새 조사를 모두 사용" in 막힘.text
    assert "내일 다시 열립니다" in 막힘.text
    assert _누적소진_제목 not in visible_text(막힘.text)


def test_하루와_누적이_함께_소진되면_누적_문구를_보여준다(
    client: TestClient, monkeypatch
):
    """★ 둘 다 막혔을 때 「내일 다시 열립니다」는 거짓말이다.

    하루치는 자정에 되살아나지만 누적은 안 되살아난다. 둘 다 소진이면
    사실이 더 강한 쪽(누적)을 말해야 손님이 헛되이 기다리지 않는다.
    """
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=_누적상한)   # 누적 소진
    _하루를_다_쓴다()                                     # 하루도 소진
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    막힘 = _조사시작(client)

    assert 막힘.status_code == 429
    assert _누적소진화면인가(막힘.text)
    assert "내일 다시 열립니다" not in 막힘.text


# ══════════════════════════════════════════════════════════
# ⑤ 다른 갈래는 누적 상한을 보지 않는다
# ══════════════════════════════════════════════════════════


def test_MEMBER_ADMIN_PUBLIC_갈래는_누적_상한을_보지_않는다(monkeypatch):
    """★ 「수명 전체」는 링크에만 있는 개념이다. 사람 통장에는 없다.

    같은 서버에 누적을 다 쓴 링크가 있어도 회원·관리자는 멀쩡히 돌아야 한다.
    """
    _링크발급()
    _끝난조사를_넣는다(run_id="run-1", 원가=_누적상한)
    with storage_db.connect() as conn:
        share_allow.invite(
            conn, email="friend@example.com", note="시험", now_iso=_시각
        )
        conn.commit()
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())

    회원 = request_helpers._guard_run(
        _요청(),
        count_start=False,
        resolved_track=(
            share_tracks.Track.MEMBER, "user:friend@example.com", 3000.0
        ),
    )
    관리자 = request_helpers._guard_run(
        _요청(),
        count_start=False,
        resolved_track=(
            share_tracks.Track.ADMIN, "user:admin@example.com", 50000.0
        ),
    )
    손님 = request_helpers._guard_run(
        _요청(),
        count_start=False,
        resolved_track=(share_tracks.Track.PUBLIC, PUBLIC_BUCKET, 0.0),
    )

    assert 회원 is None
    assert 관리자 is None
    # PUBLIC은 원래 0원이라 막히지만, «다 썼다»가 아니라 «초대받은 분만»이다.
    assert 손님 is not None
    본문 = 손님.body.decode("utf-8")
    assert "초대 링크로 들어오신 분만" in 본문
    assert _누적소진_제목 not in visible_text(본문)


# ══════════════════════════════════════════════════════════
# ⑥ 누적을 못 읽으면 열지 않는다
# ══════════════════════════════════════════════════════════


def test_누적을_못_읽으면_새조사를_열지_않는다(monkeypatch):
    """★ 「읽기 실패 = 통과」로 두면 저장소 장애가 곧 무제한 지출이 된다."""
    _링크발급()
    monkeypatch.setattr(runtime, "_PIPELINE", _돈이드는가짜파이프라인())

    def 못_읽는다(*_args, **_kwargs):
        raise RuntimeError("시험용 누적 원장 읽기 실패")

    monkeypatch.setattr(share_store, "link_total_spent_krw", 못_읽는다)

    막힘 = request_helpers._guard_run(
        _요청(), count_start=False, resolved_track=_링크갈래()
    )

    assert 막힘 is not None
    assert 막힘.status_code == 429
    # 이건 정상 동작이 아니라 «고장»이다 — 화면이 그렇게 말해야 한다.
    본문 = 막힘.body.decode("utf-8")
    assert "고장이 아닙니다" not in 본문
    assert "문의 번호" in 본문


def test_고장으로_보는_차단_종류는_그대로다() -> None:
    """★ 누적 소진을 여기에 넣으면 정상 동작을 고장이라고 말하게 된다."""
    assert request_helpers.THROTTLE_FAULT_KINDS == frozenset(
        {"budget-store", "budget-unresolved", "member-usage-store"}
    )

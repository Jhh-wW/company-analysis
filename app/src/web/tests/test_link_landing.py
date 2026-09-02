"""초대 링크로 들어온 사람이 보는 «랜딩 한 장»을 못 박는다 (티켓 G-S6).

★ 무엇을 바꾸는가 — 지금은 결속 보고서가 있으면 `/k/`가 **결과 화면으로 직행**한다.
  그러면 인사팀은 「이 도구로 다른 회사도 돌려 볼 수 있다」는 것을 영영 못 본다.
  그래서 첫 화면에 버튼 두 개를 보여 준다 (사람 결정 D-G10).

    ① 「{회사명} 보고서 보기」  — 미리 만들어 둔 보고서. 0원.
    ② 「다른 회사 분석해 보기」 — 기존 회사 입력 폼.

★ 결속 보고서가 없거나 신선도 기한이 지났으면 ①의 자리에 「준비 중」만 보인다.
  자동으로 다시 조사하지 않는다 — 사람 승인 없이 돈을 쓰는 일이기 때문이다 (D-G11).

⚠️ 이 화면의 독자는 개발자가 아니라 인사팀이다. 내부 용어(LINK·통장·원장 …)와
  만든 쪽 사정(「우리」·「내 회사」)은 한 글자도 나오면 안 된다 (설계 03장 §5, D-G1).
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import constants as share_constants
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import main, paid_runtime, runtime
from src.web.tests._visible_text import visible_text

_열쇠 = "b7c1d2e3f4a5b6c7b7c1d2e3f4a5b6c7"
_없는열쇠 = "0123456789abcdef0123456789abcdef"
_짧은열쇠 = "b7c1d2e3f4a5b6c7"


@pytest.fixture
def client():
    """★ 반드시 `with` — 아니면 뒤에서 도는 조사가 취소된다 (P-92 교훈)."""
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app, base_url="https://testserver") as client:
        yield client


def _링크발급(
    company: str,
    *,
    key: str = _열쇠,
    report_id: str = "",
    now_iso: str = "2026-08-16T10:00:00",
) -> None:
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=key,
            company=company,
            job="",
            report_id=report_id,
            now_iso=now_iso,
        )


def _보고서를_저장한다(company: str = "하이브") -> str:
    """결속 보고서 한 건을 저장소에 바로 넣는다 (파이프라인 0회·0원)."""
    report_id = uuid.uuid4().hex
    report = dataclasses.replace(build_demo_report(), company=company)
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
    return report_id


def _랜딩(client: TestClient) -> str:
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)
    return visible_text(client.get("/").text)


# ══════════════════════════════════════════════════════════
# ① 결과 직행 대신 랜딩 (D-G10)
# ══════════════════════════════════════════════════════════


def test_결속보고서가_있는_LINK는_결과직행_대신_두버튼_랜딩을_본다(
    client: TestClient,
):
    """★ G-S6의 핵심. 결과로 바로 보내면 「다른 회사도 된다」를 못 본다."""
    report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)

    열림 = client.get(f"/k/{_열쇠}", follow_redirects=False)
    랜딩 = client.get("/")
    본문 = visible_text(랜딩.text)

    assert 열림.status_code == 303
    assert 열림.headers["location"] == "/"
    assert 랜딩.status_code == 200
    # 버튼 A와 B가 둘 다, 한 화면에 있다.
    assert "하이브 보고서 보기" in 본문
    assert "다른 회사 분석해 보기" in 본문
    # 버튼 A는 그 결속 보고서로 간다.
    assert f'href="/result/{report_id}"' in 랜딩.text
    # 버튼 B가 가리키는 회사 입력 폼은 그대로 남아 있다.
    assert 'id="analysisForm"' in 랜딩.text


def test_랜딩_버튼A는_회사명을_그대로_쓰고_우리를_쓰지_않는다(
    client: TestClient,
):
    """★ 「우리 회사」는 만든 쪽 사정이다. 인사팀이 보는 것은 «자기 회사 이름»이다."""
    report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)

    본문 = _랜딩(client)

    assert "하이브 보고서 보기" in 본문
    assert "우리" not in 본문
    assert "내 회사" not in 본문
    assert "내 보고서" not in 본문


def test_랜딩_버튼A의_회사명은_HTML로_해석되지_않는다(client: TestClient):
    """★ 회사명은 관리자가 손으로 넣는 표시값이다. 그대로 그리면 화면에 코드가 심긴다."""
    report_id = _보고서를_저장한다("하이브")
    _링크발급("<script>alert(1)</script>", report_id=report_id)

    client.cookies.set(KEY_COOKIE_NAME, _열쇠)
    랜딩 = client.get("/")

    assert "<script>alert(1)</script>" not in 랜딩.text
    # 버튼은 그려지되 회사명은 «글자»로만 보인다.
    assert "&lt;script&gt;alert(1)&lt;/script&gt; 보고서 보기" in 랜딩.text


def test_랜딩은_결속보고서를_만든_날짜를_보여준다(client: TestClient):
    """★ 「언제 자료인지」를 안 알리면 인사팀이 옛 숫자를 오늘 숫자로 읽는다."""
    report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)

    본문 = _랜딩(client)

    # build_demo_report()의 생성일은 2026-08-19(KST)다.
    assert "2026년 8월 19일에 만든 보고서" in 본문


# ══════════════════════════════════════════════════════════
# ② 결속이 없거나 신선도가 지났으면 「준비 중」 (D-G11)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("사유", "report_id", "만료"),
    [
        ("아직 안 묶었다", "", False),
        ("묶인 보고서가 사라졌다", "a" * 32, False),
        ("신선도 기한이 지났다", "실제보고서", True),
    ],
)
def test_결속이_없거나_만료면_카드A가_준비중이다(
    client: TestClient, monkeypatch, 사유: str, report_id: str, 만료: bool
):
    """★ 자동 재조사는 사람 승인 없이 돈을 쓴다. 「준비 중」만 말한다 (D-G11)."""
    del 사유
    if report_id == "실제보고서":
        report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)
    if 만료:
        monkeypatch.setattr(
            "src.web.job_runtime._link_expired", lambda _report: True
        )

    본문 = _랜딩(client)

    assert "하이브 보고서는 준비 중입니다" in 본문
    assert "하이브 보고서 보기" not in 본문
    # 준비 중이어도 «다른 회사»는 지금 바로 할 수 있어야 한다.
    assert "다른 회사 분석해 보기" in 본문


# ══════════════════════════════════════════════════════════
# ③ 열쇠가 잘못됐을 때의 기존 동작은 그대로다
# ══════════════════════════════════════════════════════════


def test_잘못된_열쇠는_기존_응답_그대로다(client: TestClient):
    """★ 랜딩을 넣는다고 「없는 열쇠」가 새 정보를 흘리면 안 된다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))

    모양오류 = client.get(f"/k/{_짧은열쇠}", follow_redirects=False)
    없는열쇠 = client.get(f"/k/{_없는열쇠}", follow_redirects=False)

    assert 모양오류.status_code == 404
    assert 없는열쇠.status_code == 303
    assert 없는열쇠.headers["location"] == "/?share_status=missing"


def test_철회되거나_만료된_링크는_랜딩을_보여주지_않는다(client: TestClient):
    """★ 닫힌 링크가 회사 이름과 보고서 버튼을 계속 보여주면 철회가 무의미하다."""
    report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)
    with storage_db.connect() as conn:
        share_store.delete(conn, _열쇠, revoked_at="2026-09-02T10:00:00")

    열림 = client.get(f"/k/{_열쇠}", follow_redirects=False)
    본문 = _랜딩(client)

    assert 열림.headers["location"] == "/?share_status=revoked"
    assert "하이브 보고서 보기" not in 본문


def test_랜딩응답은_브라우저에_저장되지_않는다(client: TestClient):
    """★ 링크 하나로 여러 사람이 같은 기기를 쓸 수 있다. 화면이 남으면 안 된다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    랜딩 = client.get("/")

    assert "no-store" in 랜딩.headers["cache-control"]


def test_결속보고서_상수는_회사명을_그대로_넣는_자리를_둔다():
    """★ 문구를 라우터가 지어내면 채널마다 달라진다. 상수 한 곳에서만 만든다."""
    assert (
        share_constants.LANDING_REPORT_BUTTON_TEMPLATE.format(company="하이브")
        == "하이브 보고서 보기"
    )
    assert share_constants.LANDING_OTHER_COMPANY_BUTTON == "다른 회사 분석해 보기"


# ══════════════════════════════════════════════════════════
# ④ 인사팀 눈높이 — 내부 용어와 만든 쪽 사정은 0건 (설계 03장 §5)
# ══════════════════════════════════════════════════════════

#: 화면에 나오면 안 되는 내부 용어. 코드에서만 쓰는 말이다.
_내부용어 = (
    "capability",
    "hash",
    "bucket",
    "audience",
    "provider",
    "통장",
    "원장",
    "갈래",
    "쿼터",
    "철회",
)

#: 대문자 그대로일 때만 내부 용어인 말. HTML의 `<link rel=…>`·class 이름과
#: 섞이지 않게 원문 대소문자로만 찾는다.
_대문자_내부용어 = ("LINK", "KRW")


def test_랜딩은_내부용어를_쓰지않는다(client: TestClient):
    """★ 이 화면의 독자는 개발자가 아니다. 코드 용어를 보면 「고장 났나」 싶다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    랜딩 = client.get("/")
    본문 = visible_text(랜딩.text)

    for 용어 in _내부용어:
        assert 용어.casefold() not in 본문.casefold(), 용어
    for 용어 in _대문자_내부용어:
        assert 용어 not in 랜딩.text, 용어
    # 만든 쪽 사정도 쓰지 않는다 (2026-09-02 사용자 지시, D-G1·D-G10).
    assert "우리" not in 본문
    assert "내 회사" not in 본문


def test_준비중_화면도_내부용어를_쓰지않는다(client: TestClient):
    """★ 예외 상황일수록 코드 용어가 새기 쉽다. 준비 중 화면도 같이 본다."""
    _링크발급("하이브")
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    랜딩 = client.get("/")
    본문 = visible_text(랜딩.text)

    assert "하이브 보고서는 준비 중입니다" in 본문
    for 용어 in _내부용어:
        assert 용어.casefold() not in 본문.casefold(), 용어
    for 용어 in _대문자_내부용어:
        assert 용어 not in 랜딩.text, 용어
    assert "우리" not in 본문


# ══════════════════════════════════════════════════════════
# ⑤ 다른 손님의 첫 화면은 한 글자도 안 바뀐다
# ══════════════════════════════════════════════════════════

#: ★ 폴더 이름에 ``data``를 쓰지 않는다 — `app/.gitignore`가 `data/`를 통째로
#:   무시해서 골든 파일이 커밋되지 않는다(실측). 그러면 다른 사람 환경에서는
#:   시험이 파일을 못 찾아 아예 안 돈다.
_스냅샷_폴더 = pathlib.Path(__file__).parent / "input_page_snapshots"


def _글자줄만(본문: str) -> tuple[str, ...]:
    """사람이 읽는 «글자 줄»만 남긴다.

    ★ 왜 빈 줄을 지우나 — 화면 틀에 조건 블록을 하나 넣으면 그 손님에게 아무것도
      안 그려져도 빈 줄 수는 달라진다. 빈 줄까지 비교하면 「글자가 바뀌었다」와
      「빈 줄이 하나 늘었다」를 구분하지 못해, 진짜 문구 유출을 놓치거나 반대로
      아무 일도 아닌 것에 계속 걸린다. 지키려는 것은 **읽는 글자**다.
    """
    return tuple(줄.strip() for 줄 in 본문.splitlines() if 줄.strip())


def _스냅샷(이름: str) -> tuple[str, ...]:
    """G-S6 수정 «전»에 찍어 둔 첫 화면 글자.

    ★ 다시 찍는 법 — 첫 화면을 일부러 바꾸는 티켓에서만, 그 커밋의 코드로
      `_글자줄만(visible_text(client.get("/").text))`을 한 줄에 하나씩 덮어쓴다.
      G-S6은 초대 링크 손님의 화면만 바꾸므로 이 세 파일은 그대로여야 한다.
    """
    return _글자줄만((_스냅샷_폴더 / f"{이름}.txt").read_text(encoding="utf-8"))


def _로그인(client: TestClient, email: str, *, is_admin: bool) -> None:
    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)


def test_PUBLIC_MEMBER_ADMIN_화면은_바뀌지_않는다(client: TestClient):
    """★ 초대 링크 손님만 바꾼다. 나머지 세 손님의 첫 화면은 그대로다.

    수정 전 코드로 찍은 글자와 «똑같아야» 통과한다. 랜딩 문구가 한 줄이라도
    다른 손님에게 새면 여기서 걸린다.
    """
    공개 = _글자줄만(visible_text(client.get("/").text))

    with storage_db.connect() as conn:
        share_allow.invite(
            conn, email="friend@example.com", note="시험",
            now_iso="2026-08-16T10:00:00",
        )
        conn.commit()
    _로그인(client, "friend@example.com", is_admin=False)
    회원 = _글자줄만(visible_text(client.get("/").text))

    _로그인(client, "admin@example.com", is_admin=True)
    관리자 = _글자줄만(visible_text(client.get("/").text))

    assert 공개 == _스냅샷("public")
    assert 회원 == _스냅샷("member")
    assert 관리자 == _스냅샷("admin")


# ══════════════════════════════════════════════════════════
# ⑥ 남은 이용 한도 — 얼마까지 되는지 첫 화면에서 말한다
# ══════════════════════════════════════════════════════════

#: 링크 한 개의 하루 상한과 수명 전체 상한. ★ 생산 상수를 import해 비교하면
#:  값이 몰래 낮아져도 시험이 통과한다. 여기서는 리터럴로 못 박는다.
_하루상한 = 3000.0
_누적상한 = 3000.0
_시각 = "2026-09-02T09:00:00+09:00"

_하루소진문구 = (
    "오늘 이 링크로 돌릴 수 있는 새 조사를 모두 사용했습니다. "
    "내일 다시 열립니다. "
    "이미 만들어 둔 보고서는 지금도 그대로 보실 수 있습니다."
)
_누적소진문구 = (
    "이 링크의 이용 한도를 모두 사용했습니다. "
    "미리 준비된 회사 보고서는 계속 볼 수 있습니다."
)


def _오늘_쓴다(금액: float) -> None:
    """오늘 장부에 이 링크가 쓴 돈을 넣는다 (제품과 같은 통장 지문 키)."""
    오늘 = clock.today_kst()
    paid_runtime._LINK_SPEND = share_logic.add_spend(
        share_logic.DailySpend(day=오늘),
        spend_store.bucket_id(_열쇠),
        오늘,
        금액,
    )


def _수명동안_쓴다(금액: float, *, run_id: str = "run-1") -> None:
    """실측 원가가 확정된 종결 실행 한 건을 이 링크 앞으로 넣는다."""
    with storage_db.connect() as conn:
        assert share_store.start_run(
            conn,
            key=_열쇠,
            run_id=run_id,
            started_at=_시각,
            input_company="하이브",
            confirmed_company="하이브",
            company_id="corp-1",
        )
        assert share_store.finish_run(
            conn,
            run_id=run_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at=_시각,
            report_id=run_id,
            internal_ai_cost_krw=금액,
        )


def test_랜딩은_남은_한도를_보여준다(client: TestClient):
    """★ 「얼마까지 되는지」를 첫 화면에서 말한다 (설계 03장 §1)."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))

    본문 = _랜딩(client)

    assert "남은 이용 한도: 오늘 3,000원 · 전체 3,000원" in 본문


def test_랜딩의_남은_한도는_쓴만큼_줄어든다(client: TestClient):
    """★ 경계 2,999/3,000 — 1원만 써도 화면 숫자가 따라 내려가야 한다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    _오늘_쓴다(1.0)
    _수명동안_쓴다(2999.0)

    본문 = _랜딩(client)

    # 하루는 3,000-1=2,999원, 수명 전체는 3,000-2,999=1원 남는다.
    assert "남은 이용 한도: 오늘 2,999원 · 전체 1원" in 본문
    assert _하루소진문구 not in 본문
    assert _누적소진문구 not in 본문


def test_랜딩은_누적_소진이면_누적_소진문구를_보인다(client: TestClient):
    """★ 누적 소진은 «내일도» 안 열린다. 하루 소진 문구를 쓰면 거짓말이 된다."""
    report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)
    _수명동안_쓴다(_누적상한)

    본문 = _랜딩(client)

    assert _누적소진문구 in 본문
    assert "내일 다시 열립니다" not in 본문
    assert "남은 이용 한도" not in 본문
    # ★ 한도를 다 써도 미리 준비된 보고서는 계속 열린다 (2026-09-02 사용자 결정).
    assert "하이브 보고서 보기" in 본문


def test_랜딩은_하루_소진이면_하루_소진문구를_보인다(client: TestClient):
    """★ 하루 소진은 내일 열린다. 누적 소진과 «다른 말»을 해야 한다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    _오늘_쓴다(_하루상한)

    본문 = _랜딩(client)

    assert _하루소진문구 in 본문
    assert _누적소진문구 not in 본문
    assert "남은 이용 한도" not in 본문


def test_남은_한도_문구도_내부용어를_쓰지않는다(client: TestClient):
    """★ 돈 이야기에서 코드 용어(통장·원장·KRW)가 가장 새기 쉽다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    _오늘_쓴다(1.0)
    _수명동안_쓴다(1.0)
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    랜딩 = client.get("/")
    본문 = visible_text(랜딩.text)

    assert "남은 이용 한도" in 본문
    for 용어 in _내부용어:
        assert 용어.casefold() not in 본문.casefold(), 용어
    for 용어 in _대문자_내부용어:
        assert 용어 not in 랜딩.text, 용어


# ══════════════════════════════════════════════════════════
# ⑦ 폰에서 보는 화면 — QR은 폰으로 찍는다 (설계 03장 §4)
# ══════════════════════════════════════════════════════════


def test_랜딩_버튼은_이_사이트의_전체너비_버튼_모양을_쓴다(client: TestClient):
    """★ QR은 폰으로 찍는다. 버튼 둘이 세로로 쌓여 엄지로 눌려야 한다.

    ★ 이 화면만의 새 버튼 모양을 만들지 않는다 — `static/style.css`에 없는 class를
      적으면 화면에서는 아무 일도 안 일어나고, 「했다」는 착각만 남는다.
      실측: 첫 화면에서 쓰는 전체너비 버튼 class는 `btn wide`다.
    """
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    본문 = client.get("/").text
    버튼 = re.findall(r'<a class="([^"]*)" href="([^"]*)"', 본문)
    랜딩버튼 = [
        (클래스, 주소)
        for 클래스, 주소 in 버튼
        if "보고서 보기" in 본문 or 주소 == "#analysisForm"
    ]

    assert ("btn wide", "#analysisForm") in 랜딩버튼
    assert any(
        클래스 == "btn wide" and 주소.startswith("/result/")
        for 클래스, 주소 in 랜딩버튼
    )
    # 카드는 각각 자기 상자 안에 있어야 세로로 쌓인다.
    assert 본문.count('class="link-landing-choice"') == 2


def test_보고서_버튼은_스크린리더에도_회사명을_읽어준다(client: TestClient):
    """★ 「보고서 보기」만 읽으면 어느 회사인지 모른다 (설계 03장 §4)."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    본문 = client.get("/").text

    assert 'aria-label="하이브 보고서 보기"' in 본문


# ══════════════════════════════════════════════════════════
# ⑧ 안내문은 «지금 보이는 화면»을 설명해야 한다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("report_id", "상태", "안내조각"),
    [
        ("a" * 32, "report-missing", "기존 보고서를 찾을 수 없어"),
        ("실제보고서", "report-expired", "기존 보고서의 공유 기간이 지나"),
    ],
)
def test_보고서_안내문은_없어진_배너를_설명하지_않는다(
    client: TestClient, monkeypatch, report_id: str, 상태: str, 안내조각: str
):
    """★ 안내문이 화면에 없는 것을 설명하면 손님은 없는 것을 찾는다.

    G-S6 전에는 이 안내문이 「지원 맥락이 표시된 입력 화면」을 열었다고 말했고
    실제로 그 배너가 있었다. 이제 초대 링크 손님에게는 그 배너 대신 랜딩이
    보이므로, 안내문도 지금 보이는 화면을 말해야 한다.
    """
    if report_id == "실제보고서":
        report_id = _보고서를_저장한다("하이브")
        monkeypatch.setattr(
            "src.web.job_runtime._link_expired", lambda _report: True
        )
    _링크발급("하이브", report_id=report_id)

    열림 = client.get(f"/k/{_열쇠}", follow_redirects=False)
    본문 = visible_text(client.get(열림.headers["location"]).text)

    assert 열림.headers["location"] == f"/?share_status={상태}"
    assert 안내조각 in 본문
    assert "지원 맥락" not in 본문
    # 같은 화면이 무엇을 할 수 있는지도 말한다.
    assert "하이브 보고서는 준비 중입니다" in 본문
    assert "다른 회사 분석해 보기" in 본문


def test_랜딩_화면에는_열쇠_원문이_없다(client: TestClient):
    """★ 열쇠는 그 자체가 권한이다. 화면에 한 번 찍히면 어깨너머·스크린샷으로 샌다.

    랜딩은 회사명·보고서 주소·남은 한도만 그린다. 열쇠는 HttpOnly 쿠키로만
    오간다. 이 시험은 나중에 「편하니까」 주소나 숨은 입력칸에 열쇠를 넣는 변경을
    막는다 (설계 07장 §3 완료 기준: 열쇠 원문이 DB·로그·HTML에 없을 것).
    """
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    랜딩 = client.get("/")

    assert "하이브 보고서 보기" in 랜딩.text
    assert _열쇠 not in 랜딩.text
    assert _열쇠 not in str(랜딩.headers)


# ══════════════════════════════════════════════════════════
# ⑨ 카드는 «유효한 링크 쿠키»가 있으면 보인다 (root 결정 2026-09-02)
# ══════════════════════════════════════════════════════════


def test_링크_쿠키가_있는_관리자도_랜딩_카드를_본다(client: TestClient):
    """★ 만든 사람이 자기 QR로 시연해야 한다 (root 결정 2026-09-02).

    카드를 보이는 조건은 「비용 갈래가 LINK인가」가 아니라 «유효한 초대 링크
    쿠키가 있는가»다. 관리자가 자기 링크를 찍었을 때 카드가 안 보이면
    「받는 사람에게 무엇이 보이나」를 직접 확인할 방법이 없다.

    ★ 돈이 나가는 통장은 그대로 관리자 몫이다 — 보이는 것과 세는 것은 다른 문제다.
    """
    report_id = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=report_id)
    _로그인(client, "admin@example.com", is_admin=True)

    열림 = client.get(f"/k/{_열쇠}", follow_redirects=False)
    랜딩 = client.get("/")
    본문 = visible_text(랜딩.text)

    assert 열림.headers["location"] == "/"
    assert "하이브 보고서 보기" in 본문
    assert "다른 회사 분석해 보기" in 본문
    assert f'href="/result/{report_id}"' in 랜딩.text
    # 이 링크의 남은 한도를 그대로 보여 준다 — 보는 사람이 관리자여도 링크 값이다.
    assert "남은 이용 한도: 오늘 3,000원 · 전체 3,000원" in 본문


def test_쿠키_없는_관리자_화면은_그대로다(client: TestClient):
    """★ 링크를 안 찍은 관리자에게는 카드가 한 장도 안 보인다.

    카드를 여는 조건은 「관리자인가」가 아니라 「유효한 링크 쿠키가 있는가」다.
    조건을 잘못 넓히면 아무 관계 없는 화면에 남의 회사 이름이 뜬다.
    """
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    _로그인(client, "admin@example.com", is_admin=True)

    본문 = _글자줄만(visible_text(client.get("/").text))

    assert "하이브 보고서 보기" not in "\n".join(본문)
    assert "다른 회사 분석해 보기" not in "\n".join(본문)
    assert 본문 == _스냅샷("admin")


def test_철회된_링크_쿠키를_가진_관리자에게도_카드가_안_보인다(client: TestClient):
    """★ 닫힌 링크는 누가 봐도 닫힌 것이다. 관리자라고 되살아나면 안 된다."""
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    with storage_db.connect() as conn:
        share_store.delete(conn, _열쇠, revoked_at="2026-09-02T10:00:00")
    _로그인(client, "admin@example.com", is_admin=True)
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    본문 = visible_text(client.get("/").text)

    assert "하이브 보고서 보기" not in 본문
    assert "남은 이용 한도" not in 본문


# ══════════════════════════════════════════════════════════
# ⑩ 링크가 안 열릴 때의 안내문 (설계 03장 §3-7)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "상태", ["invalid", "missing", "expired", "revoked"]
)
def test_초대링크_안내문은_내부용어를_쓰지_않는다(client: TestClient, 상태: str):
    """★ 링크가 안 열린 사람이 처음 읽는 글이다. 코드 용어부터 보면 안 된다.

    ★ 특히 「철회」는 쓰지 않는다 — 받는 사람에게 만료와 철회는 같은 뜻이고,
      「누가 나를 잘랐나」로 읽힌다. 구분은 관리자 화면에서만 한다 (설계 03장 §3-7).
    """
    본문 = visible_text(client.get(f"/?share_status={상태}").text)

    assert "초대 링크" in 본문 or "주소가 올바르지" in 본문
    for 용어 in _내부용어:
        assert 용어.casefold() not in 본문.casefold(), 용어
    for 용어 in _대문자_내부용어:
        assert 용어 not in client.get(f"/?share_status={상태}").text, 용어
    assert "우리" not in 본문


def test_사용기간_끝남과_중단은_받는_사람에게_같은_말이다(client: TestClient):
    """★ 두 안내가 다르면 받는 사람은 「내가 잘렸나」를 추측하게 된다.

    할 일은 둘 다 같다 — 연락해서 새 링크를 받는 것이다 (설계 03장 §3-7).
    """
    끝남 = visible_text(client.get("/?share_status=expired").text)
    중단 = visible_text(client.get("/?share_status=revoked").text)

    assert "사용이 중단되어" in 끝남
    assert 끝남 == 중단
    assert "연락처로 알려 주시면" in 끝남


def test_안내문은_다음에_할_일을_알려준다(client: TestClient):
    """★ 「안 됩니다」로 끝내지 않는다. 지금 할 수 있는 일을 말한다."""
    없음 = visible_text(client.get("/?share_status=missing").text)
    잘못된주소 = visible_text(client.get("/?share_status=invalid").text)

    assert "다시 확인해" in 없음
    assert "다시 확인해" in 잘못된주소

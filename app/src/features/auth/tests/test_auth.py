"""구글 로그인 · 관리자 판정 시험.

정본: 90_운영기록/03_결정기록_03_구현중.md (D15),
      07_출력/4_근거/01_출력근거.md §4 「버튼을 숨기는 것은 권한이 아니다」

★ 진짜 구글 서버에 접속하지 않는다. `google.handle_callback`에는 가짜
  `exchange`·`fetch` 함수를 주입하고, 저수준 `_default_*` 함수는 `urlopen`
  자체를 가짜로 바꿔 검증한다.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from src.features.auth import constants, google, logic


# ══════════════════════════════════════════════════════════
# 공통 도구
# ══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _clean_sessions():
    """세션 저장소가 시험끼리 서로 오염되지 않게 매 시험 전후로 비운다."""
    # 세션은 이제 파일 저장소에 있다. ★ 시험이 «진짜 DB»를 건드리면 안 되므로
    #   시험마다 임시 파일을 쓴다 — 안 그러면 돌릴 때마다 실제 기록이 더러워진다.
    import os
    import tempfile

    from src.features.storage import constants as storage_constants

    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)
    os.environ[storage_constants.ENV_DB_PATH] = path
    try:
        yield
    finally:
        os.environ.pop(storage_constants.ENV_DB_PATH, None)
        if os.path.exists(path):
            os.unlink(path)


@pytest.fixture
def credentials_env(monkeypatch):
    """정상적인 구글 환경변수 셋을 심어준다."""
    monkeypatch.setenv(constants.ENV_CLIENT_ID, "test-client-id")
    monkeypatch.setenv(constants.ENV_CLIENT_SECRET, "test-client-secret")
    monkeypatch.setenv(constants.ENV_REDIRECT_URI, "https://example.com/auth/callback")
    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "admin@example.com")


class _FakeHTTPResponse:
    """`urllib.request.urlopen`이 돌려주는 응답 흉내 (with문으로 쓸 수 있어야 한다)."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return self._body


# ══════════════════════════════════════════════════════════
# 이메일 정규화 · 관리자 판정
# ══════════════════════════════════════════════════════════

def test_이메일은_대소문자와_공백을_정리한다():
    assert logic.normalize_email("  Admin@Example.COM ") == "admin@example.com"


def test_플러스_별칭은_다른_이메일로_본다():
    """관대한 정규화를 하지 않는다 — «+별칭»으로 관리자를 사칭할 수 없어야 한다."""
    assert logic.is_admin_email("admin+etc@gmail.com", ["admin@gmail.com"]) is False


def test_점을_지우지_않는다():
    """지메일은 점을 무시하지만, 여기서는 점도 문자 그대로 비교한다."""
    assert logic.is_admin_email("admin.name@gmail.com", ["adminname@gmail.com"]) is False


def test_대소문자_공백이_달라도_같은_이메일로_본다():
    assert logic.is_admin_email("  ADMIN@EXAMPLE.COM  ", ["admin@example.com"]) is True


def test_관리자_아닌_이메일은_거부된다():
    assert logic.is_admin_email("nobody@example.com", constants.DEFAULT_ADMIN_EMAILS) is False


def test_환경변수가_없으면_관리자는_아무도_없다(monkeypatch):
    monkeypatch.delenv(constants.ENV_ADMIN_EMAILS, raising=False)
    assert constants.DEFAULT_ADMIN_EMAILS == ()
    assert logic.check_admin("admin@example.com") is False


def test_환경변수로_관리자_목록을_덮어쓸_수_있다(monkeypatch):
    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "a@x.com, B@Y.com")
    assert logic.admin_emails_from_env() == ("a@x.com", "b@y.com")
    assert logic.check_admin("a@x.com") is True
    assert logic.check_admin("admin@example.com") is False


def test_환경변수가_비어있으면_빈목록을_쓴다(monkeypatch):
    monkeypatch.delenv(constants.ENV_ADMIN_EMAILS, raising=False)
    assert logic.admin_emails_from_env() == constants.DEFAULT_ADMIN_EMAILS


def test_관리자전용_시험공개는_정확히_0일때만_꺼진다(monkeypatch):
    monkeypatch.delenv(constants.ENV_BETA_ADMIN_ONLY, raising=False)
    assert logic.beta_admin_only_from_env() is True

    for value in ("", "1", "true", "yes", "오타"):
        monkeypatch.setenv(constants.ENV_BETA_ADMIN_ONLY, value)
        assert logic.beta_admin_only_from_env() is True

    monkeypatch.setenv(constants.ENV_BETA_ADMIN_ONLY, " 0 ")
    assert logic.beta_admin_only_from_env() is False


# ══════════════════════════════════════════════════════════
# CSRF state
# ══════════════════════════════════════════════════════════

def test_state는_매번_다르게_만들어진다():
    assert logic.make_state() != logic.make_state()


def test_state가_같으면_통과한다():
    state = logic.make_state()
    assert logic.state_matches(state, state) is True


def test_state가_다르면_거부한다():
    assert logic.state_matches(logic.make_state(), logic.make_state()) is False


def test_폼_csrf토큰은_같은_세션에서만_맞는다():
    token = "session-secret"
    csrf = logic.csrf_token_for_session(token)

    assert csrf and csrf != token
    assert logic.csrf_token_matches(token, csrf)
    assert not logic.csrf_token_matches("another-session", csrf)
    assert not logic.csrf_token_matches(token, "")


@pytest.mark.parametrize(
    "received",
    [
        "한글",
        "é",
        "g" * 64,
        "A" * 64,
        "0" * 63,
        "0" * 65,
        None,
    ],
)
def test_폼_csrf토큰은_정확한_소문자_64자리_16진수만_받는다(received):
    """공개 폼 값이 비ASCII여도 예외나 500이 아니라 안전한 불일치여야 한다."""
    assert logic.csrf_token_matches("session-secret", received) is False


@pytest.mark.parametrize("expected, received", [("", "abc"), ("abc", ""), ("", "")])
def test_state가_비어있으면_무조건_거부한다(expected, received):
    assert logic.state_matches(expected, received) is False


# ══════════════════════════════════════════════════════════
# 구글 사용자 정보에서 이메일 꺼내기
# ══════════════════════════════════════════════════════════

def test_확인된_이메일은_꺼낼_수_있다():
    email = logic.extract_verified_email({"email": "Person@Example.com", "email_verified": True})
    assert email == "person@example.com"


def test_확인된_계정은_이메일과_구글_sub를_함께_보존한다():
    identity = logic.extract_verified_identity(
        {
            "email": "Alias@Example.com",
            "email_verified": True,
            "sub": "109876543210",
        }
    )
    assert identity.email == "alias@example.com"
    assert identity.subject == "google:109876543210"


def test_구글_sub가_없으면_로그인_신원을_거부한다():
    with pytest.raises(logic.UnverifiedIdentityError):
        logic.extract_verified_identity(
            {"email": "person@example.com", "email_verified": True}
        )


def test_email_verified가_문자열_true여도_통과한다():
    """구글 응답은 email_verified가 문자열("true")로 올 때가 있다."""
    email = logic.extract_verified_email({"email": "a@b.com", "email_verified": "true"})
    assert email == "a@b.com"


def test_email_verified가_false면_거부한다():
    with pytest.raises(logic.UnverifiedEmailError):
        logic.extract_verified_email({"email": "a@b.com", "email_verified": False})


def test_email_verified가_없으면_거부한다():
    with pytest.raises(logic.UnverifiedEmailError):
        logic.extract_verified_email({"email": "a@b.com"})


def test_email_자체가_없으면_거부한다():
    with pytest.raises(logic.UnverifiedEmailError):
        logic.extract_verified_email({"email_verified": True})


# ══════════════════════════════════════════════════════════
# 세션
# ══════════════════════════════════════════════════════════

def test_세션을_만들면_바로_조회된다(monkeypatch):
    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "a@b.com")
    session = logic.create_session("a@b.com", is_admin=False)
    found = logic.get_session(session.token)
    assert found is not None
    assert found.email == "a@b.com"
    assert found.is_admin is True


def test_없는_토큰은_None():
    assert logic.get_session("없는-토큰") is None


def test_빈_토큰은_None():
    assert logic.get_session(None) is None
    assert logic.get_session("") is None


def test_만료된_세션은_None이고_지워진다():
    session = logic.create_session("a@b.com", is_admin=False, now=1_000.0)
    # 유효기간(SESSION_MAX_AGE_SEC)이 다 지난 시점으로 조회한다
    later = 1_000.0 + constants.SESSION_MAX_AGE_SEC + 1
    assert logic.get_session(session.token, now=later) is None
    # 만료된 세션은 «몇 번을 물어도» 안 나와야 한다 (저장소가 걸러낸다)
    assert logic.get_session(session.token, now=later) is None
    assert logic.is_admin_session(session.token, now=later) is False


def test_로그아웃하면_세션이_사라진다():
    session = logic.create_session("a@b.com", is_admin=True)
    logic.delete_session(session.token)
    assert logic.get_session(session.token) is None


def test_로그아웃은_없는_토큰이어도_에러가_안_난다():
    logic.delete_session("없는-토큰")
    logic.delete_session(None)


def test_관리자_세션만_is_admin_session이_참이다(monkeypatch):
    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "admin@x.com")
    # 저장 당시 플래그와 반대로 넣어도 현재 환경변수가 최종 권한을 정한다.
    admin_session = logic.create_session("admin@x.com", is_admin=False)
    normal_session = logic.create_session("user@x.com", is_admin=True)
    assert logic.is_admin_session(admin_session.token) is True
    assert logic.is_admin_session(normal_session.token) is False
    assert logic.is_admin_session("없는-토큰") is False
    assert logic.is_admin_session(None) is False


def test_ADMIN_EMAILS를_바꾸면_기존_세션_권한도_즉시_바뀐다(monkeypatch):
    session = logic.create_session("admin@x.com", is_admin=False)

    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "admin@x.com")
    assert logic.get_session(session.token).is_admin is True

    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "new-admin@x.com")
    assert logic.get_session(session.token).is_admin is False

    monkeypatch.setenv(constants.ENV_ADMIN_EMAILS, "admin@x.com,new-admin@x.com")
    assert logic.get_session(session.token).is_admin is True


def test_current_email은_세션의_이메일을_돌려준다():
    session = logic.create_session("a@b.com", is_admin=False)
    assert logic.current_email(session.token) == "a@b.com"
    assert logic.current_email("없는-토큰") is None


def test_같은_provider_sub는_이메일_별칭이_달라도_같은_person_id다():
    first = logic.create_session(
        "old-alias@example.com", False, subject="google:immutable-person-1"
    )
    second = logic.create_session(
        "new-alias@example.com", False, subject="google:immutable-person-1"
    )
    assert logic.current_subject(first.token) == logic.current_subject(second.token)
    assert logic.person_id_for_subject(first.subject) == logic.person_id_for_subject(
        second.subject
    )


def test_이메일호환_세션은_PDF승인_신원으로_인정하지_않는다():
    session = logic.create_session("legacy@example.com", False)
    assert logic.is_approval_identity_subject(session.subject) is False


def test_DB의_손상된_subject는_이메일로_대체하지_않고_세션을_폐기한다():
    from src.features.storage import db  # noqa: PLC0415

    session = logic.create_session(
        "person@example.com", False, subject="google:immutable-person"
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE sessions SET subject=? WHERE token=?",
            ("person@example.com", session.token),
        )
    assert logic.get_session(session.token) is None
    assert logic.current_email(session.token) is None


def test_PDF참여자설정은_작성생산자와_서로다른_3검수자를_요구한다(monkeypatch):
    monkeypatch.setenv(
        constants.ENV_PDF_RELEASE_PARTICIPANTS,
        '{"author":"google:author","producer":"google:producer",'
        '"fact":"google:fact","editorial":"google:editorial",'
        '"visual":"google:visual"}',
    )
    participants = logic.pdf_release_participant_ids_from_env()
    assert set(participants) == {"author", "producer", "fact", "editorial", "visual"}
    assert len({participants[role] for role in ("fact", "editorial", "visual")}) == 3


@pytest.mark.parametrize(
    "payload",
    (
        "",
        "{}",
        '{"author":"author@example.com","producer":"google:p",'
        '"fact":"google:f","editorial":"google:e","visual":"google:v"}',
        '{"author":"google:same","producer":"google:p",'
        '"fact":"google:same","editorial":"google:e","visual":"google:v"}',
        '{"author":"google:a","producer":"google:p",'
        '"fact":"google:f","editorial":"google:f","visual":"google:v"}',
    ),
)
def test_PDF참여자설정이_누락되거나_역할분리가_깨지면_거부한다(monkeypatch, payload):
    monkeypatch.setenv(constants.ENV_PDF_RELEASE_PARTICIPANTS, payload)
    with pytest.raises(logic.UnverifiedIdentityError):
        logic.pdf_release_participant_ids_from_env()


# ══════════════════════════════════════════════════════════
# 인증 URL 만들기
# ══════════════════════════════════════════════════════════

def test_인증_URL에_필요한_값이_다_들어간다():
    creds = google.GoogleCredentials(
        client_id="cid", client_secret="secret", redirect_uri="https://example.com/cb"
    )
    url = google.build_auth_url(creds, state="the-state")
    assert url.startswith(constants.GOOGLE_AUTHORIZE_ENDPOINT)
    assert "client_id=cid" in url
    assert "state=the-state" in url
    assert "redirect_uri=" in url
    # 비밀키는 인증 URL에 절대 들어가면 안 된다 (브라우저 주소창에 노출된다)
    assert "secret" not in url


# ══════════════════════════════════════════════════════════
# 비밀키(환경변수) 없을 때
# ══════════════════════════════════════════════════════════

def test_환경변수가_하나도_없으면_에러_메시지에_이름이_다_나온다(monkeypatch):
    monkeypatch.delenv(constants.ENV_CLIENT_ID, raising=False)
    monkeypatch.delenv(constants.ENV_CLIENT_SECRET, raising=False)
    monkeypatch.delenv(constants.ENV_REDIRECT_URI, raising=False)
    with pytest.raises(google.MissingCredentialError) as exc_info:
        google.load_credentials()
    message = str(exc_info.value)
    assert constants.ENV_CLIENT_ID in message
    assert constants.ENV_CLIENT_SECRET in message
    assert constants.ENV_REDIRECT_URI in message


def test_비밀키만_없으면_그것만_지목한다(monkeypatch, credentials_env):
    monkeypatch.delenv(constants.ENV_CLIENT_SECRET, raising=False)
    with pytest.raises(google.MissingCredentialError) as exc_info:
        google.load_credentials()
    message = str(exc_info.value)
    assert constants.ENV_CLIENT_SECRET in message
    assert constants.ENV_CLIENT_ID not in message


def test_환경변수가_다_있으면_읽어온다(credentials_env):
    creds = google.load_credentials()
    assert creds.client_id == "test-client-id"
    assert creds.client_secret == "test-client-secret"
    assert creds.redirect_uri == "https://example.com/auth/callback"


def test_start_login은_비밀키_없으면_에러(monkeypatch):
    monkeypatch.delenv(constants.ENV_CLIENT_ID, raising=False)
    monkeypatch.delenv(constants.ENV_CLIENT_SECRET, raising=False)
    monkeypatch.delenv(constants.ENV_REDIRECT_URI, raising=False)
    with pytest.raises(google.MissingCredentialError):
        google.start_login()


def test_start_login은_URL과_state를_같이_돌려준다(credentials_env):
    started = google.start_login()
    assert started.state in started.auth_url
    assert started.auth_url.startswith(constants.GOOGLE_AUTHORIZE_ENDPOINT)


# ══════════════════════════════════════════════════════════
# 콜백 처리 (handle_callback) — 가짜 exchange/fetch 주입, 네트워크 없음
# ══════════════════════════════════════════════════════════

def _fake_exchange_ok(code, redirect_uri, client_id, client_secret):
    del code, redirect_uri, client_id, client_secret
    return {"access_token": "fake-access-token", "token_type": "Bearer"}


def _fake_fetch_verified(access_token):
    del access_token
    return {
        "email": "person@example.com",
        "email_verified": True,
        "sub": "person-subject-1",
    }


def test_정상_로그인은_세션을_만든다(credentials_env):
    state = logic.make_state()
    result = google.handle_callback(
        code="auth-code",
        state_received=state,
        state_expected=state,
        exchange=_fake_exchange_ok,
        fetch=_fake_fetch_verified,
    )
    assert result.email == "person@example.com"
    assert result.is_admin is False
    assert logic.get_session(result.session.token) is not None


def test_관리자_이메일이면_is_admin이_참이다(credentials_env):
    state = logic.make_state()
    result = google.handle_callback(
        code="auth-code",
        state_received=state,
        state_expected=state,
        exchange=_fake_exchange_ok,
        fetch=lambda token: {
            "email": "admin@example.com",
            "email_verified": True,
            "sub": "admin-subject-1",
        },
    )
    assert result.is_admin is True


def test_state가_다르면_로그인을_거부한다(credentials_env):
    with pytest.raises(logic.StateMismatchError):
        google.handle_callback(
            code="auth-code",
            state_received="attacker-state",
            state_expected="server-state",
            exchange=_fake_exchange_ok,
            fetch=_fake_fetch_verified,
        )


def test_state_expected가_없으면_거부한다(credentials_env):
    """state 쿠키가 아예 없이 콜백이 들어오면(쿠키 조작·직접 접근 의심) 거부한다."""
    with pytest.raises(logic.StateMismatchError):
        google.handle_callback(
            code="auth-code",
            state_received="anything",
            state_expected=None,
            exchange=_fake_exchange_ok,
            fetch=_fake_fetch_verified,
        )


def test_state_불일치면_교환_함수를_아예_부르지_않는다(credentials_env):
    """CSRF 의심이면 구글에 코드를 보내기 전에 먼저 막는다."""
    calls = []

    def _tracking_exchange(code, redirect_uri, client_id, client_secret):
        calls.append(code)
        return _fake_exchange_ok(code, redirect_uri, client_id, client_secret)

    with pytest.raises(logic.StateMismatchError):
        google.handle_callback(
            code="auth-code",
            state_received="wrong",
            state_expected="right",
            exchange=_tracking_exchange,
            fetch=_fake_fetch_verified,
        )
    assert calls == []


def test_이메일_미확인이면_로그인을_거부한다(credentials_env):
    state = logic.make_state()
    with pytest.raises(logic.UnverifiedEmailError):
        google.handle_callback(
            code="auth-code",
            state_received=state,
            state_expected=state,
            exchange=_fake_exchange_ok,
            fetch=lambda token: {"email": "person@example.com", "email_verified": False},
        )


def test_토큰_응답에_access_token이_없으면_거부한다(credentials_env):
    state = logic.make_state()
    with pytest.raises(google.GoogleAuthError):
        google.handle_callback(
            code="auth-code",
            state_received=state,
            state_expected=state,
            exchange=lambda *args: {"token_type": "Bearer"},  # access_token 없음
            fetch=_fake_fetch_verified,
        )


def test_토큰_응답이_이상한_형태여도_거부한다(credentials_env):
    """access_token이 빈 문자열인 경우도 「없음」으로 취급한다."""
    state = logic.make_state()
    with pytest.raises(google.GoogleAuthError):
        google.handle_callback(
            code="auth-code",
            state_received=state,
            state_expected=state,
            exchange=lambda *args: {"access_token": ""},
            fetch=_fake_fetch_verified,
        )


def test_비밀키_없이_콜백이_오면_거부한다(monkeypatch):
    monkeypatch.delenv(constants.ENV_CLIENT_ID, raising=False)
    monkeypatch.delenv(constants.ENV_CLIENT_SECRET, raising=False)
    monkeypatch.delenv(constants.ENV_REDIRECT_URI, raising=False)
    state = logic.make_state()
    with pytest.raises(google.MissingCredentialError):
        google.handle_callback(
            code="auth-code",
            state_received=state,
            state_expected=state,
            exchange=_fake_exchange_ok,
            fetch=_fake_fetch_verified,
        )


# ══════════════════════════════════════════════════════════
# 기본 네트워크 구현 — urlopen 자체를 가짜로 바꿔 실제 접속 없이 검증
# ══════════════════════════════════════════════════════════

def test_기본_교환_함수는_필요한_값을_담아_POST한다(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return _FakeHTTPResponse({"access_token": "tok", "token_type": "Bearer"})

    monkeypatch.setattr(google.urllib.request, "urlopen", _fake_urlopen)

    result = google._default_exchange_code(
        code="the-code",
        redirect_uri="https://example.com/cb",
        client_id="cid",
        client_secret="csecret",
    )

    assert result == {"access_token": "tok", "token_type": "Bearer"}
    assert captured["url"] == constants.GOOGLE_TOKEN_ENDPOINT
    assert captured["method"] == "POST"
    assert "code=the-code" in captured["body"]
    assert "client_secret=csecret" in captured["body"]
    assert captured["timeout"] == constants.HTTP_TIMEOUT_SEC


def test_기본_사용자정보_함수는_인증_헤더를_담아_GET한다(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth_header"] = request.get_header("Authorization")
        return _FakeHTTPResponse({"email": "a@b.com", "email_verified": True})

    monkeypatch.setattr(google.urllib.request, "urlopen", _fake_urlopen)

    result = google._default_fetch_userinfo("the-access-token")

    assert result == {"email": "a@b.com", "email_verified": True}
    assert captured["url"] == constants.GOOGLE_USERINFO_ENDPOINT
    assert captured["auth_header"] == "Bearer the-access-token"


def test_통신_실패시_한국어_예외로_감싼다(monkeypatch):
    def _raise_url_error(request, timeout=None):
        raise urllib.error.URLError("연결 거부")

    monkeypatch.setattr(google.urllib.request, "urlopen", _raise_url_error)

    with pytest.raises(google.GoogleAuthError):
        google._default_exchange_code("code", "https://example.com/cb", "cid", "csecret")


def test_이상한_응답이면_한국어_예외로_감싼다(monkeypatch):
    class _BadJSONResponse(_FakeHTTPResponse):
        def read(self) -> bytes:
            return "이건 JSON이 아니다".encode("utf-8")

    def _fake_urlopen(request, timeout=None):
        return _BadJSONResponse({})

    monkeypatch.setattr(google.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(google.GoogleAuthError):
        google._default_fetch_userinfo("token")

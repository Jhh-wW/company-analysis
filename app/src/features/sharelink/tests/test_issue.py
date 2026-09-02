"""열쇠 링크 «발급»을 못 박는다.

★ 여기서 지키는 것 셋
  ① 열쇠를 **추측할 수 없다** — 예측 가능하면 남의 링크 예산을 쓸 수 있다
  ② 주소가 **배포 뒤에도 맞는다** — 상수로 박으면 배포 후 localhost를 가리킨다
  ③ QR이 **HTML 안에 그대로 들어간다** — XML 선언이 섞이면 화면이 깨진다
"""

from __future__ import annotations

import pytest

from src.features.sharelink.constants import KEY_HEX_CHARS
from src.features.sharelink.issue import (
    canonical_public_base_url,
    link_url,
    new_key,
    qr_svg,
    safe_local_base_url,
)
from src.features.sharelink.logic import is_valid_key


# ══════════════════════════════════════════════════════════
# ① 열쇠 — 추측할 수 없어야 한다
# ══════════════════════════════════════════════════════════


def test_만든_열쇠는_유효한_모양이다():
    assert is_valid_key(new_key())


def test_열쇠_길이가_정해진_대로다():
    assert len(new_key()) == KEY_HEX_CHARS


def test_열쇠는_실제로_16바이트_CSPRNG에서_발급한다(monkeypatch):
    calls = []

    def fake_token_hex(nbytes):
        calls.append(nbytes)
        return "a" * (nbytes * 2)

    monkeypatch.setattr("src.features.sharelink.issue.secrets.token_hex", fake_token_hex)

    assert new_key() == "a" * 32
    assert calls == [16]


def test_만들_때마다_다르다():
    """★ 겹치면 «남의 링크 예산»을 쓰게 된다."""
    열쇠들 = {new_key() for _ in range(200)}

    assert len(열쇠들) == 200


# ══════════════════════════════════════════════════════════
# ② 주소 — 배포한 뒤에도 맞아야 한다
# ══════════════════════════════════════════════════════════


def test_주소를_만든다():
    assert link_url("https://example.com", "abcd1234") == "https://example.com/k/abcd1234"


def test_끝에_슬래시가_있어도_안_깨진다():
    """★ 안 떼면 `//k/...`가 되어 주소가 깨진다."""
    assert link_url("https://example.com/", "abcd1234") == "https://example.com/k/abcd1234"


@pytest.mark.parametrize(
    ("설정", "기대"),
    [
        ("https://demo.example", "https://demo.example"),
        ("https://demo.example/", "https://demo.example"),
        ("https://demo.example:8443", "https://demo.example:8443"),
    ],
)
def test_검증된_공개_HTTPS_origin만_정본주소로_쓴다(설정: str, 기대: str):
    assert canonical_public_base_url(설정) == 기대


@pytest.mark.parametrize(
    "설정",
    [
        "http://demo.example",
        "https://localhost:8000",
        "https://preview.localhost",
        "https://192.168.0.10",
        "https://127.0.0.2",
        "https://user:pass@demo.example",
        "https://demo.example/path",
        "https://demo.example?next=1",
        "https://demo.example#part",
        "https://demo.example:bad",
    ],
)
def test_위험하거나_공개origin이_아닌_설정은_거절한다(설정: str):
    assert canonical_public_base_url(설정) == ""


def test_로컬_origin은_loopback_HTTP만_허용한다():
    assert safe_local_base_url("http://127.0.0.1:8000/admin/link/x") == (
        "http://127.0.0.1:8000"
    )
    assert safe_local_base_url("http://evil.example/admin/link/x") == ""
    assert safe_local_base_url("https://127.0.0.1/admin/link/x") == ""
    assert safe_local_base_url("http://testserver/admin/link/x") == ""


# ══════════════════════════════════════════════════════════
# ③ QR — HTML 안에 그대로 들어가야 한다
# ══════════════════════════════════════════════════════════


def test_QR이_SVG_글자로_나온다():
    svg = qr_svg("https://example.com/k/abcd1234")

    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_QR에_XML_선언이_없다():
    """★ HTML 한가운데에 `<?xml …?>`가 들어가면 브라우저가 문서를 잘못 읽는다."""
    assert "<?xml" not in qr_svg("https://example.com/k/abcd1234")


def test_주소가_길어도_QR이_만들어진다():
    """배포 주소가 길 수 있다 — 여기서 터지면 발급 자체가 막힌다."""
    긴주소 = "https://" + "a" * 60 + ".example.com/k/" + "b" * 32

    assert qr_svg(긴주소).startswith("<svg")


def test_다른_주소는_다른_QR이다():
    """★ 같은 그림이 나오면 어느 회사 링크인지 구별이 안 된다."""
    가 = qr_svg("https://example.com/k/1111111111111111")
    나 = qr_svg("https://example.com/k/2222222222222222")

    assert 가 != 나


# ══════════════════════════════════════════════════════════
# ④ 「배포된 주소인가」 — 죽은 링크를 포폴에 넣지 않게
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "주소",
    [
        "http://localhost:8000/k/abc",       # 내 컴퓨터
        "http://127.0.0.1:8000/k/abc",       # ★ 글자만 보면 놓친다
        "http://testserver/k/abc",
        "http://내도구.com/k/abc",            # https가 아니다
        "",
        "그냥글자",
    ],
)
def test_배포_안_된_주소는_경고_대상이다(주소: str):
    """★ 이 주소로 발급해 포폴에 넣으면 인사팀에게는 «안 열리는 링크»가 된다.

    아무것도 없는 것보다 나쁘다 — 「만들었다는데 안 되네」가 되기 때문이다.
    """
    from src.features.sharelink.issue import looks_deployed

    assert not looks_deployed(주소)


@pytest.mark.parametrize(
    "주소",
    ["https://내도구.com/k/abc", "https://xyz.onrender.com/k/abc"],
)
def test_배포된_주소는_경고_안_한다(주소: str):
    """★ 반대 방향 — 멀쩡한 주소에 매번 경고하면 아무도 안 읽게 된다."""
    from src.features.sharelink.issue import looks_deployed

    assert looks_deployed(주소)

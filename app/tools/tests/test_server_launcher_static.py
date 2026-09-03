"""서버 켜기 실행기가 «화면에 적은 대로» 동작하게 하는 정적 계약.

이 실행기는 개발 지식이 없는 사람이 그대로 따라 하는 안내문이기도 하다. 그래서
「n을 고르면 조사 기능만 쓴다」 같은 문장이 사실이 아니면, 사람은 원인을 알 수
없는 빈 화면을 보게 된다. 실제로 켜 보는 시험은 유료 경로라 여기서 하지 않고,
파일을 읽어 아래 네 가지를 못 박는다.

1. 로그인 벽(``BETA_ADMIN_ONLY``)을 명시한다 — 앱은 정확히 ``"0"``일 때만 끄고,
   안 정하면 켜진 채로 남아 어떤 화면도 열리지 않는다.
2. 관리자 이메일(``ADMIN_EMAILS``)을 묻는다 — 비어 있으면 관리자는 0명이라
   로그인에 성공해도 관리 화면·노션 보내기에 닿지 못한다.
3. 저장소 가상환경의 파이썬을 먼저 찾는다.
4. 지금은 사실이 아닌 안내 문구가 남아 있지 않다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = APP_ROOT / "서버켜기.ps1"
SCRIPT = LAUNCHER.read_text(encoding="utf-8-sig")
#: 「이 문자열이 있으면 안 된다」는 검사는 실행되는 코드만 본다 — 주석은 왜 그렇게
#: 했는지 설명하려고 금지 문자열을 인용해야 할 때가 있고, 주석은 실행되지 않는다.
SCRIPT_CODE = "\n".join(
    line for line in SCRIPT.splitlines() if not line.lstrip().startswith("#")
)
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def test_launcher_sets_the_login_wall_explicitly() -> None:
    """앱은 정확히 "0"일 때만 로그인 벽을 끈다 — 안 정하면 켜진 채로 남는다.

    근거: ``src/features/auth/logic.py``의 ``beta_admin_only_from_env``. 값이
    없거나 오타면 잠긴 상태를 유지한다. 그 상태에서 구글 값이 없으면
    ``/auth/login``이 503으로 끝나 첫 화면부터 아무것도 열리지 않는다.
    """
    assert '$env:BETA_ADMIN_ONLY = "1"' in SCRIPT, (
        "구글 로그인을 켠 갈래는 배포와 같은 로그인 벽을 명시해야 한다"
    )
    assert '$env:BETA_ADMIN_ONLY = "0"' in SCRIPT, (
        "로그인을 안 켠 갈래가 벽을 안 끄면 「조사 화면만 씁니다」가 거짓이 된다"
    )


def test_launcher_asks_for_the_admin_email() -> None:
    """관리자 목록이 비면 로그인에 성공해도 관리자가 0명이다.

    근거: ``src/features/auth/constants.py``의 기본 관리자 목록은 빈 튜플이고,
    ``admin_emails_from_env``는 값이 없으면 그대로 아무도 허용하지 않는다.
    """
    assert "ADMIN_EMAILS" in SCRIPT_CODE, "실행기가 관리자 이메일을 아예 안 넘긴다"
    assert "$env:ADMIN_EMAILS" in SCRIPT_CODE
    ask_lines = [
        line
        for line in SCRIPT.splitlines()
        if "Read-Host" in line and "관리자" in line
    ]
    assert ask_lines, "관리자 이메일을 사람에게 물어보는 자리가 없다"


def test_launcher_prefers_the_repository_virtual_environment_python() -> None:
    """PATH의 `python`은 의존성이 없는 다른 파이썬일 수 있다."""
    assert ".venv\\Scripts\\python.exe" in SCRIPT
    assert "Python314" not in SCRIPT, (
        "특정 버전 설치 경로를 찍어 두면 다음 버전에서 조용히 어긋난다"
    )


def test_launcher_refuses_real_pipeline_without_the_boot_required_secret() -> None:
    """진짜 조사는 출처 도장 비밀이 없으면 서버가 아예 뜨지 않는다.

    근거: ``src/web/runtime.py``가 ``PIPELINE=real``에서 32바이트 이상의
    ``PROVENANCE_SEAL_SECRET``을 요구하고, 없으면 시작 자체를 막는다.
    """
    real_branch = SCRIPT.split('$env:PIPELINE = "real"')[0]
    assert "PROVENANCE_SEAL_SECRET" in real_branch, (
        "진짜 조사를 켜기 전에 기동 필수값을 확인해야 한다"
    )
    assert "GetByteCount" in SCRIPT, "32바이트 하한을 실제로 재야 한다"


@pytest.mark.parametrize(
    "stale",
    [
        "워드로 내려받기",
        "뉴스 조사",
        "60~250원",
        "82원",
        "Python314",
    ],
)
def test_launcher_has_no_stale_promise(stale: str) -> None:
    """지금은 사실이 아닌 안내가 화면에 남아 있으면 안 된다.

    워드 주소는 410으로 닫혔고(``src/web/routers/reports.py``의 ``download_docx``),
    공식 근거 보고서는 뉴스를 쓰지 않으며(``src/features/pipeline/real.py``가
    수집 자체를 생략한다), 1건 비용은 회사·자료량마다 달라 실행기가 숫자로
    약속할 수 없다.
    """
    assert stale not in SCRIPT, f"현재 동작과 다른 안내가 남아 있습니다: {stale}"


def test_launcher_has_no_dead_reference_to_missing_documents() -> None:
    """저장소에 없는 문제로그·기획 문서를 가리키는 주석은 죽은 참조다."""
    for dead in ("문제로그", "기획서", "P-91", "P-97", "P-104"):
        assert dead not in SCRIPT, f"없는 문서를 가리킵니다: {dead}"


def test_launcher_is_utf8_with_bom_so_powershell_5_1_shows_korean() -> None:
    """PowerShell 5.1은 BOM이 없으면 .ps1을 ANSI로 읽어 한국어 안내가 깨진다."""
    assert LAUNCHER.read_bytes().startswith(b"\xef\xbb\xbf")


def test_launcher_parses_under_windows_powershell(tmp_path: Path) -> None:
    """문법 오류를 커밋 전에 잡는다. 실제로 켜지는 않는다 — 유료 경로다."""
    if WINDOWS_POWERSHELL is None:
        pytest.skip("Windows PowerShell 5.1 문법 검사")
    probe = tmp_path / "parse.ps1"
    literal = "'" + str(LAUNCHER).replace("'", "''") + "'"
    probe.write_bytes(
        b"\xef\xbb\xbf"
        + (
            "$errors = $null\n"
            "$tokens = $null\n"
            "$null = [System.Management.Automation.Language.Parser]::ParseFile(\n"
            f"    {literal}, [ref]$tokens, [ref]$errors)\n"
            "if ($errors.Count -gt 0) { Write-Output 'PARSE ERROR' } "
            "else { Write-Output 'PARSE OK' }\n"
        ).encode("utf-8")
    )
    result = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert b"PARSE OK" in result.stdout, result.stdout + result.stderr

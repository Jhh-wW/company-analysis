"""배포 리허설 실행기의 계약 — 「배포와 같은 조건 + 관리자 게이트 켬」을 못 박는다.

이 실행기가 지켜야 하는 것은 두 가지다.
1. 배포(render.yaml)와 같은 곳: PIPELINE=real, BETA_ADMIN_ONLY=1, 관리자 로그인 게이트.
2. 로컬이라 다를 수밖에 없는 곳: 127.0.0.1 loopback, http 쿠키 예외, 로컬 배포 계약.
   배포의 ``render-admin-real-no-forwarded-v1``을 로컬에 그대로 쓰면 모든 POST가
   Origin 불일치로 거부된다(src/web/request_helpers.py:727-741).

그리고 어느 쪽이든 «비밀값을 화면·파일에 남기지 않는» 경계는 동일하다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = APP_ROOT / "배포리허설켜기.ps1"
SCRIPT = LAUNCHER.read_text(encoding="utf-8-sig")
#: 「이 문자열이 있으면 안 된다」는 검사는 «실행되는 코드»만 본다. 주석은 왜 그렇게
#: 했는지를 설명하려고 금지 문자열을 인용해야 할 때가 있고, 주석은 실행되지 않는다.
SCRIPT_CODE = "\n".join(
    line for line in SCRIPT.splitlines() if not line.lstrip().startswith("#")
)
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
DEPLOYMENT_SECRET_NAMES = (
    "ANTHROPIC_API_KEY",
    "DART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
)
ADMIN_EMAIL_ARGUMENT = "Operator@Example.com"
PARENT_ADMIN_EMAIL_SENTINEL = "parent-must-not-win@example.com"
#: 부모 환경에 심어 두는 «다른» 출시 모드. 자식에 이 값이 보이면 부모가 이긴 것이다.
PARENT_RELEASE_MODE_SENTINEL = "ENFORCE_NO_PARTIAL"


# ══════════════════════════════════════════════════════════
# 1. 정적 계약 — 파일을 읽어 확인한다
# ══════════════════════════════════════════════════════════

def test_launcher_matches_deployment_pipeline_and_admin_gate() -> None:
    """배포와 «같아야» 하는 값. 하나라도 빠지면 리허설이 배포가 아니게 된다."""
    assert '$childEnvironment["PIPELINE"] = "real"' in SCRIPT
    assert '$childEnvironment["BETA_ADMIN_ONLY"] = "1"' in SCRIPT, (
        "BETA_ADMIN_ONLY가 1이 아니면 관리자 로그인 게이트가 꺼져 배포와 달라진다"
    )
    assert '$childEnvironment["ENGINE_V2"] = "1"' in SCRIPT
    assert '$childEnvironment["ANALYSIS_ENGINE_DISABLE_DOTENV"] = "1"' in SCRIPT
    assert '$childEnvironment["BUSINESS_CANDIDATE_PROVIDER"] = "disabled"' in SCRIPT
    assert "PROVENANCE_SEAL_SECRET" in SCRIPT, (
        "PIPELINE=real은 32바이트 출처 도장을 요구한다 (src/web/runtime.py:110-114)"
    )


def test_launcher_binds_loopback_only_with_deployment_uvicorn_flags() -> None:
    """주소만 배포와 다르게 loopback으로 고정하고 나머지 실행 옵션은 배포와 같다."""
    assert "--host 127.0.0.1" in SCRIPT
    assert "--workers 1" in SCRIPT
    assert "--no-proxy-headers" in SCRIPT
    assert "--limit-concurrency" in SCRIPT
    assert "--timeout-graceful-shutdown" in SCRIPT
    assert "0.0.0.0" not in SCRIPT_CODE, (
        "loopback 전용 실행기의 실행 코드에 외부 바인딩 주소가 있으면 안 된다"
    )


def test_launcher_uses_local_contract_not_the_render_narrow_contract() -> None:
    """배포의 좁은 계약을 로컬에 그대로 쓰면 모든 POST가 거부된다.

    근거: 그 계약에서는 POST의 Origin이 고정 «https» PUBLIC_ORIGIN과 정확히 같아야
    한다(src/web/request_helpers.py:727-741 + src/web/deployment_mode.py:98-110).
    로컬 브라우저는 http://127.0.0.1:<포트>를 보내므로 절대 같아질 수 없다.
    """
    assert '$childEnvironment["DEPLOYMENT_RUNTIME_CONTRACT"] = "local-web-v1"' in SCRIPT
    assert "render-admin-real-no-forwarded-v1" not in SCRIPT_CODE
    assert "render-admin-demo-no-forwarded-v1" not in SCRIPT_CODE
    assert '$childEnvironment["DEPLOYMENT_EXPOSURE"] = "local"' in SCRIPT
    assert '$childEnvironment["DEPLOYMENT_PLATFORM"] = "local"' in SCRIPT


def test_admin_emails_come_from_the_argument_and_never_from_parent_environment() -> None:
    """관리자 목록은 인자로만 들어온다. 부모 환경의 같은 이름은 자식에게 안 간다."""
    assert "[Parameter(Mandatory = $true)]" in SCRIPT
    assert "[string]$AdminEmails" in SCRIPT
    parent_allowlist = SCRIPT.split("$allowedParentNames = ")[1].split("\n")[0]
    assert "ADMIN_EMAILS" not in parent_allowlist
    parent_secret_block = SCRIPT.split("$deploymentSecretEnvironmentNames = @(")[1]
    assert "ADMIN_EMAILS" not in parent_secret_block.split(")")[0]
    assert '$childEnvironment["ADMIN_EMAILS"] = $normalizedAdminEmails' in SCRIPT


def test_google_redirect_uri_is_restricted_to_this_launchers_loopback_address() -> None:
    """배포용 https 콜백 주소를 실수로 넣어 운영 쪽으로 새는 길을 막는다."""
    assert '"http://127.0.0.1:$Port/auth/callback"' in SCRIPT
    assert '"http://localhost:$Port/auth/callback"' in SCRIPT
    assert "$allowedRedirectUris -notcontains $GoogleRedirectUri" in SCRIPT
    assert "https://" not in SCRIPT_CODE, "실행기가 외부 https 주소를 만들어내면 안 된다"


def test_launcher_enforces_the_child_environment_allowlist() -> None:
    """허용하지 않은 이름이 남아 있으면 시작을 거부한다."""
    assert "$allowedChildEnvironmentNames -notcontains [string]$name" in SCRIPT
    assert "허용하지 않은 환경" in SCRIPT
    assert "$AllowedNames -notcontains [string]$name" in SCRIPT
    assert "허용하지 않은 부모 환경이 남아 있어 시작하지 않습니다." in SCRIPT
    for name in ("ADMIN_EMAILS", "GOOGLE_REDIRECT_URI", "ENGINE_V2"):
        child_allowlist = SCRIPT.split("$allowedChildEnvironmentNames = ")[1]
        assert f'"{name}"' in child_allowlist, (
            f"{name}이 자식 허용 목록에 없으면 실행기가 시작을 거부한다"
        )


def test_launcher_never_reads_or_prints_secret_files() -> None:
    """비밀 파일을 «몰래» 읽지 않고, 비밀값을 화면·파일에 남기지 않는다.

    ★ 적대 검수가 잡은 «가짜 잣대» — 예전 이 시험은 `Get-Content`가
      없다는 것만 봤다. 그런데 이 실행기는 파일을 `[System.IO.File]::
      ReadAllLines`로 읽으므로 그 단언은 «항상 참»이었다. 지키는 것이
      하나도 없는 시험이었다.

    ★ 지금 지키는 것 — 이 실행기는 파일을 «읽어도 된다». 사람이
      -ProviderEnvFile로 «직접 지정한» 파일만 읽는 것이 계약이다.
      금지되는 것은 ① 저장소 안 비밀 경로를 코드에 박아 두는 것
      ② 값을 화면에 찍는 것 ③ 값을 어딘가에 저장하는 것이다.
    """
    # ① 비밀 파일 경로를 코드에 박아 두지 않는다 — 사람이 지정한 것만 읽는다.
    for hardcoded in ("analysis_engine/.env", "app/.env", ".env\"", "'.env'"):
        assert hardcoded not in SCRIPT_CODE, hardcoded
    # 파일을 읽는 곳은 «사람이 지정한 경로»에서만 온다.
    # PowerShell은 인자를 여러 줄에 걸쳐 쓰므로 그 줄부터 몇 줄을 함께 본다.
    코드줄 = SCRIPT_CODE.splitlines()
    _ARG_WINDOW = 4
    for reader in ("ReadAllLines", "ReadAllText", "Get-Content", "StreamReader"):
        for index, line in enumerate(코드줄):
            if reader not in line:
                continue
            창 = "\n".join(코드줄[index : index + _ARG_WINDOW])
            assert "ProviderEnvFile" in 창, (
                f"사람이 지정하지 않은 경로를 읽습니다: {reader}"
            )

    # ② 값을 «어디에도 저장하지 않는다» — 이 실행기에는 파일 쓰기가 없다.
    for writer in ("Out-File", "Set-Content", "Add-Content", "Export-Clixml"):
        assert writer not in SCRIPT_CODE, f"비밀값이 파일로 샐 수 있습니다: {writer}"

    # ③ 값을 화면에 찍지 않는다 — 비밀 변수 이름이 출력문에 등장하면 안 된다.
    출력문 = [
        line
        for line in SCRIPT_CODE.splitlines()
        if "Write-Host" in line or "Write-Output" in line
    ]
    assert 출력문, "출력문이 하나도 없습니다(시험이 헛돈 것)"
    for line in 출력문:
        for name in DEPLOYMENT_SECRET_NAMES:
            assert f"${name}" not in line and f"$env:{name}" not in line, (
                f"비밀값을 화면에 찍습니다: {name}"
            )
        assert "$secureValue" not in line
        assert "$plainValue" not in line

    # ④ 물어볼 때는 화면에 안 보이게 받는다.
    assert "-AsSecureString" in SCRIPT_CODE
    assert "값은 표시하지 않음" in SCRIPT


def test_launcher_isolates_run_data_and_can_delete_it() -> None:
    assert ".local_deployment_rehearsal_runs" in SCRIPT
    assert "RandomNumberGenerator" in SCRIPT
    assert "-DeleteDataOnExit" in SCRIPT
    assert "ReparsePoint" in SCRIPT
    assert "app 폴더 밖을 가리켜 중단합니다" in SCRIPT


def test_engine_v2_child_always_gets_the_report_release_mode() -> None:
    """v2를 켜면서 출시 모드를 안 넘기면 조사가 AI 호출 전에 전부 멈춘다.

    근거: 값이 비면 ``src/features/pipeline/real.py:3510-3514``가 ValueError를
    던지고 그 갈래는 ``GATE_STOPPED``로 끝난다. 컨테이너 검증기도 같은 조합
    (real + ENGINE_V2=1 + 값 없음)을 부팅 거부한다.
    """
    assert '[ValidateSet("FULL", "ENFORCE_NO_PARTIAL", "SHADOW", IgnoreCase = $false)]' in SCRIPT, (
        "허용 값은 ReleaseMode 계약"
        "(src/shared/report_evidence/constants.py:81-87)과 같아야 한다"
    )
    assert '[string]$ReleaseMode = "FULL"' in SCRIPT, (
        "기본값이 배포와 같은 FULL이 아니면 리허설이 배포가 아니게 된다"
    )
    v2_branch = SCRIPT.split('$childEnvironment["ENGINE_V2"] = "1"')[1]
    assert '$childEnvironment["REPORT_RELEASE_MODE"] = $ReleaseMode' in v2_branch, (
        "ENGINE_V2=1을 켜는 갈래가 REPORT_RELEASE_MODE를 함께 넘겨야 한다"
    )
    child_allowlist = SCRIPT.split("$allowedChildEnvironmentNames = ")[1]
    assert '"REPORT_RELEASE_MODE"' in child_allowlist, (
        "자식 허용 목록에 없으면 실행기가 시작을 거부한다"
    )


def test_report_release_mode_never_comes_from_the_parent_environment() -> None:
    """부모 값이 이기면 FULL을 켰다고 믿는 동안 다른 정책이 돈다."""
    parent_allowlist = SCRIPT.split("$allowedParentNames = ")[1].split("\n")[0]
    assert "REPORT_RELEASE_MODE" not in parent_allowlist


def test_launcher_requires_explicit_spending_confirmation() -> None:
    """진짜 조사는 돈이 든다 — 사람이 확인하지 않으면 열리지 않는다."""
    assert "[switch]$ConfirmRealSpending" in SCRIPT
    assert "if (-not $ConfirmRealSpending) {" in SCRIPT


# ══════════════════════════════════════════════════════════
# 2. 실제 PowerShell 5.1 자식 환경 — 가짜 uvicorn으로 확인한다
# ══════════════════════════════════════════════════════════

def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _environment(tmp_path: Path) -> dict[str, str]:
    assert WINDOWS_POWERSHELL is not None
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    user_root = tmp_path / "isolated user"
    temp = user_root / "Temp"
    appdata = user_root / "AppData" / "Roaming"
    localappdata = user_root / "AppData" / "Local"
    for path in (temp, appdata, localappdata):
        path.mkdir(parents=True, exist_ok=True)
    ps_home = Path(WINDOWS_POWERSHELL).resolve().parent
    python_home = Path(sys.executable).resolve().parent
    drive = system_root.drive or "C:"
    program_files = Path(drive + "\\Program Files")
    environment = {
        "SystemRoot": str(system_root),
        # Windows는 이름 대소문자를 구분하지 않지만 ProcessStartInfo는 실제 casing을
        # 보존할 수 있다. 실제 셸에서 발견한 `windir` 변형을 회귀시험한다.
        "windir": str(system_root),
        "SystemDrive": drive,
        "ComSpec": str(system_root / "System32" / "cmd.exe"),
        "PATH": os.pathsep.join(
            (str(python_home), str(ps_home), str(system_root / "System32"))
        ),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PSModulePath": str(ps_home / "Modules"),
        "TEMP": str(temp),
        "TMP": str(temp),
        "USERPROFILE": str(user_root),
        "HOMEDRIVE": user_root.drive,
        "HOMEPATH": str(user_root)[len(user_root.drive) :],
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(localappdata),
        "ALLUSERSPROFILE": str(Path(drive + "\\ProgramData")),
        "ProgramData": str(Path(drive + "\\ProgramData")),
        "ProgramFiles": str(program_files),
        "ProgramFiles(x86)": str(Path(drive + "\\Program Files (x86)")),
        "ProgramW6432": str(program_files),
        "CommonProgramFiles": str(program_files / "Common Files"),
        "CommonProgramFiles(x86)": str(
            Path(drive + "\\Program Files (x86)\\Common Files")
        ),
        "CommonProgramW6432": str(program_files / "Common Files"),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(tmp_path / "must-not-inherit-pythonpath"),
        # 부모가 관리자 목록을 심어도 자식은 인자 값만 써야 한다.
        "ADMIN_EMAILS": PARENT_ADMIN_EMAIL_SENTINEL,
        # 배포 계약을 부모가 흉내 내도 자식은 로컬 계약만 받아야 한다.
        "DEPLOYMENT_RUNTIME_CONTRACT": "render-admin-real-no-forwarded-v1",
        # 부모가 출시 모드를 심어도 자식은 인자·기본값만 써야 한다.
        "REPORT_RELEASE_MODE": PARENT_RELEASE_MODE_SENTINEL,
        "PUBLIC_ORIGIN": "https://parent-must-not-win.example.com",
        "REHEARSAL_TEST_COMPANY": "JYP-sensitive-test-input",
    }
    for name in DEPLOYMENT_SECRET_NAMES:
        environment[name] = f"secret-sentinel-{name.lower()}"
    return environment


def _copy_fake_app(tmp_path: Path) -> Path:
    app_copy = tmp_path / "한글 공백 배포 리허설" / "app"
    app_copy.mkdir(parents=True)
    shutil.copy2(LAUNCHER, app_copy / LAUNCHER.name)
    (app_copy / "uvicorn.py").write_text(
        """
import json
import os
import pathlib
import sys

secret_names = (
    "ANTHROPIC_API_KEY", "DART_API_KEY", "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
)
root = pathlib.Path(os.environ["APP_DATA_ROOT"]).resolve()
root.mkdir(parents=True, exist_ok=True)
seal = os.environ.get("PROVENANCE_SEAL_SECRET", "")
payload = {
    "secret_presence": {name: name in os.environ for name in secret_names},
    "pipeline": os.environ.get("PIPELINE"),
    "beta_admin_only": os.environ.get("BETA_ADMIN_ONLY"),
    "engine_v2": os.environ.get("ENGINE_V2"),
    "release_mode": os.environ.get("REPORT_RELEASE_MODE"),
    "admin_emails": os.environ.get("ADMIN_EMAILS"),
    "google_redirect_uri": os.environ.get("GOOGLE_REDIRECT_URI"),
    "cookie_insecure": os.environ.get("AUTH_COOKIE_INSECURE"),
    "runtime_contract": os.environ.get("DEPLOYMENT_RUNTIME_CONTRACT"),
    "exposure": os.environ.get("DEPLOYMENT_EXPOSURE"),
    "platform": os.environ.get("DEPLOYMENT_PLATFORM"),
    "forwarded_allow_ips": os.environ.get("FORWARDED_ALLOW_IPS"),
    "public_origin_absent": "PUBLIC_ORIGIN" not in os.environ,
    "dotenv_disabled": os.environ.get("ANALYSIS_ENGINE_DISABLE_DOTENV"),
    "candidate_provider": os.environ.get("BUSINESS_CANDIDATE_PROVIDER"),
    "seal_is_fresh_hex": len(seal) == 64 and all(c in "0123456789abcdef" for c in seal),
    "loopback_flags": all(value in sys.argv for value in (
        "--host", "127.0.0.1", "--workers", "1", "--no-proxy-headers",
    )),
    "no_public_bind": "0.0.0.0" not in " ".join(sys.argv),
    "sensitive_parent_inputs_absent": all(name not in os.environ for name in (
        "REHEARSAL_TEST_COMPANY", "PYTHONPATH",
    )),
    "paths_inside_run_root": all(
        pathlib.Path(os.environ[name]).resolve().is_relative_to(root)
        for name in (
            "STORAGE_DB_PATH", "OBSERVABILITY_RECORDS_PATH", "TLDEXTRACT_CACHE",
            "TEMP", "TMP",
        )
    ),
}
(root / "child-environment.json").write_text(
    json.dumps(payload), encoding="utf-8"
)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return app_copy


def _run_fake(
    app_copy: Path,
    environment: dict[str, str],
    *,
    port: int,
    admin_emails: str = ADMIN_EMAIL_ARGUMENT,
    confirm_spending: bool = True,
    google_redirect_uri: str | None = None,
    disable_engine_v2: bool = False,
    release_mode: str | None = None,
    delete_data_on_exit: bool = False,
) -> tuple[subprocess.CompletedProcess[bytes], list[Path]]:
    assert WINDOWS_POWERSHELL is not None
    launcher = app_copy / LAUNCHER.name
    switch = f" -AdminEmails {_ps_literal(admin_emails)} -Port {port}"
    if confirm_spending:
        switch += " -ConfirmRealSpending"
    if google_redirect_uri is not None:
        switch += f" -GoogleRedirectUri {_ps_literal(google_redirect_uri)}"
    if disable_engine_v2:
        switch += " -DisableEngineV2"
    if release_mode is not None:
        switch += f" -ReleaseMode {_ps_literal(release_mode)}"
    if delete_data_on_exit:
        switch += " -DeleteDataOnExit"
    command = (
        f"try {{ & {_ps_literal(launcher)}{switch} }} "
        "catch { Write-Output $_.Exception.Message }"
    )
    result = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=app_copy,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    return result, list(app_copy.rglob("child-environment.json"))


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_child_gets_deployment_conditions_with_admin_gate_on(tmp_path: Path) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)
    port = _available_port()

    result, records = _run_fake(app_copy, environment, port=port)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["pipeline"] == "real"
    assert payload["beta_admin_only"] == "1"
    assert payload["engine_v2"] == "1"
    assert payload["release_mode"] == "FULL", (
        "v2를 켜면서 출시 모드를 안 넘기면 모든 조사가 AI 호출 전에 멈춘다"
    )
    assert payload["cookie_insecure"] == "1"
    assert payload["dotenv_disabled"] == "1"
    assert payload["candidate_provider"] == "disabled"
    assert payload["seal_is_fresh_hex"] is True
    assert payload["secret_presence"] == {name: True for name in DEPLOYMENT_SECRET_NAMES}
    assert payload["loopback_flags"] is True
    assert payload["no_public_bind"] is True
    assert payload["sensitive_parent_inputs_absent"] is True
    assert payload["paths_inside_run_root"] is True
    assert records[0].parent.parent == app_copy / ".local_deployment_rehearsal_runs"


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_child_admin_list_and_contract_come_from_launcher_not_parent(
    tmp_path: Path,
) -> None:
    """부모가 심어둔 ADMIN_EMAILS·배포 계약이 자식으로 새지 않는다."""
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)
    port = _available_port()

    result, records = _run_fake(app_copy, environment, port=port)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["admin_emails"] == ADMIN_EMAIL_ARGUMENT.lower()
    assert PARENT_ADMIN_EMAIL_SENTINEL not in payload["admin_emails"]
    assert payload["runtime_contract"] == "local-web-v1"
    assert payload["exposure"] == "local"
    assert payload["platform"] == "local"
    assert payload["forwarded_allow_ips"] == "127.0.0.1"
    assert payload["public_origin_absent"] is True
    assert payload["google_redirect_uri"] == f"http://127.0.0.1:{port}/auth/callback"


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_console_never_prints_secret_values(tmp_path: Path) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, _records = _run_fake(app_copy, environment, port=_available_port())

    console = (result.stdout + result.stderr).decode(errors="replace")
    assert "secret-sentinel" not in console
    for name in DEPLOYMENT_SECRET_NAMES:
        assert f"{name}: yes" in console


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_without_spending_confirmation_no_child_and_no_run_folder(
    tmp_path: Path,
) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy, environment, port=_available_port(), confirm_spending=False
    )

    assert result.returncode == 0
    assert records == []
    assert not (app_copy / ".local_deployment_rehearsal_runs").exists()
    console = (result.stdout + result.stderr).decode(errors="replace")
    assert "-ConfirmRealSpending" in console


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://company-analysis-beta.onrender.com/auth/callback",
        "http://127.0.0.1:1/auth/callback",
        "http://example.com/auth/callback",
    ],
    ids=("deployed-https", "wrong-port", "not-loopback"),
)
def test_non_loopback_redirect_uri_stops_before_child(
    tmp_path: Path, redirect_uri: str
) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy,
        environment,
        port=_available_port(),
        google_redirect_uri=redirect_uri,
    )

    assert result.returncode == 0
    assert records == []
    assert not (app_copy / ".local_deployment_rehearsal_runs").exists()


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_missing_deployment_secret_stops_before_child(tmp_path: Path) -> None:
    """구글 OAuth 값이 없으면 조용히 열리지 않고 멈춘다 — 로그인 자체가 불가능하다."""
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)
    environment.pop("GOOGLE_CLIENT_SECRET")

    result, records = _run_fake(app_copy, environment, port=_available_port())

    assert result.returncode == 0
    assert records == []
    assert not (app_copy / ".local_deployment_rehearsal_runs").exists()
    console = (result.stdout + result.stderr).decode(errors="replace")
    assert "GOOGLE_CLIENT_SECRET: no" in console
    assert "secret-sentinel" not in console


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
@pytest.mark.parametrize(
    "admin_emails",
    ["", "   ", "not-an-email", "ok@example.com,broken"],
    ids=("empty", "blank", "no-at-sign", "one-broken"),
)
def test_bad_admin_emails_stop_before_child(tmp_path: Path, admin_emails: str) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy, environment, port=_available_port(), admin_emails=admin_emails
    )

    assert result.returncode == 0
    assert records == []
    assert not (app_copy / ".local_deployment_rehearsal_runs").exists()


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_engine_v2_can_be_turned_off_for_comparison(tmp_path: Path) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy, environment, port=_available_port(), disable_engine_v2=True
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    # real.py:209는 정확히 "1"일 때만 v2로 분기한다 — "0"은 v1과 같다.
    assert payload["engine_v2"] == "0"
    # v1 경로는 이 값을 읽지 않는다. 부모가 심어 둔 값도 따라오면 안 된다.
    assert payload["release_mode"] is None


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_release_mode_argument_reaches_the_child_and_beats_the_parent(
    tmp_path: Path,
) -> None:
    """인자로 고른 출시 모드가 자식에 그대로 닿고, 부모 값은 무시된다."""
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy, environment, port=_available_port(), release_mode="SHADOW"
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["release_mode"] == "SHADOW"
    assert payload["release_mode"] != PARENT_RELEASE_MODE_SENTINEL


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_unknown_release_mode_stops_before_child(tmp_path: Path) -> None:
    """계약 밖 문자열을 다른 모드로 «추측»하지 않고 시작 자체를 거부한다."""
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy, environment, port=_available_port(), release_mode="full"
    )

    assert records == []
    assert not (app_copy / ".local_deployment_rehearsal_runs").exists()


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_delete_data_on_exit_removes_only_the_generated_run_directory(
    tmp_path: Path,
) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(
        app_copy, environment, port=_available_port(), delete_data_on_exit=True
    )

    assert result.returncode == 0
    assert records == []
    runs_root = app_copy / ".local_deployment_rehearsal_runs"
    assert runs_root.is_dir()
    assert list(runs_root.iterdir()) == []


def test_launcher_is_utf8_with_bom_so_powershell_5_1_shows_korean() -> None:
    """PowerShell 5.1은 BOM이 없으면 .ps1을 ANSI로 읽어 한국어 안내가 깨진다."""
    assert LAUNCHER.read_bytes().startswith(b"\xef\xbb\xbf")


def test_launcher_parses_under_windows_powershell(tmp_path: Path) -> None:
    """문법 오류를 커밋 전에 잡는다. 실제로 켜지는 않는다."""
    if WINDOWS_POWERSHELL is None:
        pytest.skip("Windows PowerShell 5.1 문법 검사")
    probe = tmp_path / "parse.ps1"
    probe.write_bytes(
        b"\xef\xbb\xbf"
        + (
            "$errors = $null\n"
            "$tokens = $null\n"
            "$null = [System.Management.Automation.Language.Parser]::ParseFile(\n"
            f"    {_ps_literal(LAUNCHER)}, [ref]$tokens, [ref]$errors)\n"
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


def test_documentation_exists_and_hides_real_values() -> None:
    """사용법 문서는 있어야 하고, 실제 키·이메일이 적혀 있으면 안 된다."""
    guide = APP_ROOT / "docs" / "배포리허설_사용법.md"
    assert guide.is_file()
    text = guide.read_text(encoding="utf-8")
    assert "배포리허설켜기.ps1" in text
    assert "-ConfirmRealSpending" in text
    assert "-AdminEmails" in text
    assert "-ReleaseMode" in text, "출시 모드를 고르는 법이 문서에 없다"
    assert re.search(r"GOCSPX-[A-Za-z0-9_\-]{5,}", text) is None
    assert re.search(r"sk-ant-[A-Za-z0-9_\-]{5,}", text) is None
    assert (
        re.search(r"\d{6,}-[a-z0-9]{15,}\.apps\.googleusercontent\.com", text) is None
    )

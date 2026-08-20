"""로컬 데모 실행기의 정적 경계와 Windows PowerShell 5.1 실제 실행을 고정한다."""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlencode

import pytest


APP_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = APP_ROOT / "로컬데모켜기.ps1"
SCRIPT = LAUNCHER_PATH.read_text(encoding="utf-8")
AUTH_CONSTANTS_PATH = APP_ROOT / "src" / "features" / "auth" / "constants.py"
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
CAPABILITY_URL_RE = re.compile(
    rb"http://127\.0\.0\.1:(\d+)/auth/local-demo/start\?token=([0-9a-f]{64})"
)
PROVIDER_VARIABLES = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "ANTHROPIC_API_KEY",
    "DART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "NOTION_TOKEN",
    "NOTION_PARENT_PAGE_ID",
)
UNRELATED_CREDENTIAL_VARIABLES = (
    "OPENAI_API_KEY",
    "AZURE_CLIENT_SECRET",
    "DATABASE_PASSWORD",
    "SENTRY_AUTH_TOKEN",
    "STRIPE_SECRET_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GITHUB_TOKEN",
    "CI_JOB_TOKEN",
    "CLOUDFLARE_API_TOKEN",
)
PARENT_CREDENTIAL_VARIABLES = PROVIDER_VARIABLES + UNRELATED_CREDENTIAL_VARIABLES
REQUIRED_CHILD_OS_VARIABLES = (
    "SystemRoot",
    "WINDIR",
    "ComSpec",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)


def test_로컬데모는_명시적인_무료_관리자조건만_자식환경에_넣는다() -> None:
    expected = {
        "PIPELINE": "demo",
        "BETA_ADMIN_ONLY": "0",
        "LOCAL_DEMO_AUTH": "1",
        "AUTH_COOKIE_INSECURE": "1",
        "ADMIN_EMAILS": "local-demo-admin@example.invalid",
    }

    for name, value in expected.items():
        assert f'$childEnvironment["{name}"] = "{value}"' in SCRIPT

    assert '$childEnvironment["LOCAL_DEMO_AUTH_TOKEN"] = $localDemoAuthToken' in SCRIPT
    assert "RandomNumberGenerator" in SCRIPT
    assert "New-Object byte[] 32" in SCRIPT
    assert '.Replace("-", "").ToLowerInvariant()' in SCRIPT
    assert "Get-CompatibleChildEnvironment -StartInfo $startInfo" in SCRIPT
    assert "$StartInfo.EnvironmentVariables" in SCRIPT
    assert "$StartInfo.Environment" in SCRIPT
    assert '$probeName = "LOCAL_DEMO_LAUNCHER_ENV_PROBE"' in SCRIPT
    assert "if ($null -eq $environment)" in SCRIPT
    assert "Reset-ChildEnvironmentToAllowlist" in SCRIPT
    assert "$Environment.Clear()" in SCRIPT
    for name in REQUIRED_CHILD_OS_VARIABLES:
        assert f'"{name}"' in SCRIPT
    for name in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED"):
        assert f'$childEnvironment["{name}"]' in SCRIPT
    assert "$env:" not in SCRIPT.casefold()


def test_로컬데모는_외부로그인과_유료공급자설정을_자식환경에서_뺀다() -> None:
    for name in PROVIDER_VARIABLES:
        assert f'$childEnvironment.Remove("{name}")' in SCRIPT

    forbidden_secret_reads = (
        "Get-Content",
        "GetEnvironmentVariable",
        "[Environment]::Get",
        "app/.env",
        "analysis_engine/.env",
    )
    assert not any(marker in SCRIPT for marker in forbidden_secret_reads)


def test_로컬데모는_loopback과_격리저장소만_사용한다() -> None:
    assert "--host 127.0.0.1" in SCRIPT
    assert "0.0.0.0" not in SCRIPT
    assert "--workers 1" in SCRIPT
    assert "--no-access-log" in SCRIPT
    assert "Assert-LoopbackPortAvailable -RequestedPort $Port" in SCRIPT
    assert "Wait-ForLoopbackListener" in SCRIPT
    assert '[ValidateRange(1024, 65535)]' in SCRIPT
    assert 'Join-Path $appRoot ".venv\\Scripts\\python.exe"' in SCRIPT
    assert 'Get-Command "python" -CommandType Application' in SCRIPT

    assert '$demoRoot = Join-Path $appRoot ".local_demo"' in SCRIPT
    expected_paths = {
        "APP_DATA_ROOT": "$demoRoot",
        "STORAGE_DB_PATH": "$storageDatabase",
        "OBSERVABILITY_RECORDS_PATH": "$recordsPath",
        "TLDEXTRACT_CACHE": "$tldextractCache",
    }
    for name, value in expected_paths.items():
        assert f'$childEnvironment["{name}"] = {value}' in SCRIPT

    assert "서버켜기.ps1" not in SCRIPT


def test_로컬데모는_capability가_든_전용로그인주소를_안내한다() -> None:
    assert '$loginUrl = "$url/auth/local-demo/start?token=$localDemoAuthToken"' in SCRIPT
    assert "Write-Host $loginUrl" in SCRIPT
    assert "우측 상단 로그인" not in SCRIPT


def test_root_capability와_짧은_1회용_교환권을_주석이_구분한다() -> None:
    constants_text = AUTH_CONSTANTS_PATH.read_text(encoding="utf-8")
    assert "로컬 실행기 수명 동안 재진입에 쓰는 root capability" in constants_text
    assert "grant/state만 2분·1회용" in constants_text
    assert "1회용 관리자 진입 capability" not in constants_text


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _isolated_windows_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    assert WINDOWS_POWERSHELL is not None
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    isolated_home = tmp_path / "격리 사용자"
    temp_dir = isolated_home / "Temp"
    appdata = isolated_home / "AppData" / "Roaming"
    localappdata = isolated_home / "AppData" / "Local"
    for path in (temp_dir, appdata, localappdata):
        path.mkdir(parents=True, exist_ok=True)

    powershell_home = Path(WINDOWS_POWERSHELL).resolve().parent
    python_home = Path(sys.executable).resolve().parent
    system_drive = system_root.drive or "C:"
    program_files = Path(system_drive + "\\Program Files")
    program_files_x86 = Path(system_drive + "\\Program Files (x86)")
    safe_path = os.pathsep.join(
        (str(python_home), str(powershell_home), str(system_root / "System32"))
    )
    danger_root = tmp_path / "상속되면 실패하는 부모 경로"
    environment = {
        "SystemRoot": str(system_root),
        "WINDIR": str(system_root),
        "ComSpec": str(system_root / "System32" / "cmd.exe"),
        "PATH": safe_path,
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PSModulePath": str(powershell_home / "Modules"),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "USERPROFILE": str(isolated_home),
        "HOME": str(isolated_home),
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(localappdata),
        "SystemDrive": system_drive,
        "HOMEDRIVE": isolated_home.drive,
        "HOMEPATH": str(isolated_home)[len(isolated_home.drive) :],
        "ALLUSERSPROFILE": str(Path(system_drive + "\\ProgramData")),
        "ProgramData": str(Path(system_drive + "\\ProgramData")),
        "ProgramFiles": str(program_files),
        "ProgramFiles(x86)": str(program_files_x86),
        "ProgramW6432": str(program_files),
        "CommonProgramFiles": str(program_files / "Common Files"),
        "CommonProgramFiles(x86)": str(program_files_x86 / "Common Files"),
        "CommonProgramW6432": str(program_files / "Common Files"),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(tmp_path / "부모가 주입한 모듈 경로"),
        "PYTHONHOME": str(tmp_path / "부모가 주입한 Python 홈"),
        "UVICORN_HOST": "0.0.0.0",
        "UVICORN_WORKERS": "4",
        "UVICORN_ACCESS_LOG": "true",
        "APP_DATA_ROOT": str(danger_root),
        "STORAGE_DB_PATH": str(danger_root / "real-storage.db"),
        "OBSERVABILITY_RECORDS_PATH": str(danger_root / "real-runs.jsonl"),
        "TLDEXTRACT_CACHE": str(danger_root / "real-tldextract"),
        "LOCAL_DEMO_AUTH_TOKEN": "parent-token-must-be-replaced",
    }
    for name in PARENT_CREDENTIAL_VARIABLES:
        environment[name] = f"parent-{name.lower()}-sentinel"
    return environment, danger_root


def _copy_launcher(tmp_path: Path, name: str) -> Path:
    app_copy = tmp_path / name / "app"
    app_copy.mkdir(parents=True)
    shutil.copy2(LAUNCHER_PATH, app_copy / LAUNCHER_PATH.name)
    return app_copy


def _copy_runnable_app(tmp_path: Path) -> Path:
    app_copy = _copy_launcher(tmp_path, "한글과 공백 경로 실제 서버")
    shutil.copytree(APP_ROOT / "src", app_copy / "src")
    pilot_source = APP_ROOT.parent / "analysis_engine" / "data" / "pilot"
    pilot_target = app_copy.parent / "analysis_engine" / "data" / "pilot"
    shutil.copytree(pilot_source, pilot_target)
    # 실제 web child가 받은 argv와 환경 key 이름만 격리 폴더에 남긴다. 값이나
    # capability는 읽지 않으며, 운영 소스가 아닌 이 임시 복제본에서만 동작한다.
    probe_module = app_copy / "src" / "web" / "_launcher_environment_probe.py"
    probe_module.write_text(
        """
import json
import os
import pathlib
import sys

root = pathlib.Path(os.environ["APP_DATA_ROOT"])
root.mkdir(parents=True, exist_ok=True)
credential_suffixes = ("_KEY", "_SECRET", "_TOKEN", "PASSWORD")
allowed_secret_name = "LOCAL_DEMO_AUTH_TOKEN"
dummy_names = {
    "OPENAI_API_KEY", "AZURE_CLIENT_SECRET", "DATABASE_PASSWORD",
    "SENTRY_AUTH_TOKEN", "STRIPE_SECRET_KEY", "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "GITHUB_TOKEN", "CI_JOB_TOKEN",
    "CLOUDFLARE_API_TOKEN",
}
(root / "child-process.json").write_text(
    json.dumps({
        "pid": os.getpid(),
        "argv": sys.argv,
        "dummy_credential_keys": sorted(dummy_names.intersection(os.environ)),
        "unexpected_secret_keys": sorted(
            name for name in os.environ
            if name != allowed_secret_name and name.upper().endswith(credential_suffixes)
        ),
        "required_os_keys_present": all(name in os.environ for name in (
            "SystemRoot", "WINDIR", "ComSpec", "PATH", "PATHEXT", "TEMP",
            "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        )),
        "parent_python_config_absent": all(
            name not in os.environ for name in ("PYTHONPATH", "PYTHONHOME")
        ),
    }),
    encoding="utf-8",
)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    main_path = app_copy / "src" / "web" / "main.py"
    main_path.write_text(
        main_path.read_text(encoding="utf-8")
        + "\nfrom src.web import _launcher_environment_probe as _launcher_environment_probe\n",
        encoding="utf-8",
    )
    return app_copy


def _decode_console(value: bytes) -> str:
    for encoding in ("utf-8", "cp949"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def _powershell_version(environment: dict[str, str]) -> tuple[int, str]:
    assert WINDOWS_POWERSHELL is not None
    result = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$PSVersionTable.PSVersion.Major; [Environment]::Version.ToString()",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=15,
    )
    lines = [line.strip() for line in _decode_console(result.stdout).splitlines() if line.strip()]
    return int(lines[0]), lines[1]


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 프로세스 전용 시험",
)
def test_windows_powershell_5_1에서_child환경은_격리되고_부모는_그대로다(
    tmp_path: Path,
) -> None:
    assert len(UNRELATED_CREDENTIAL_VARIABLES) == 10
    app_copy = _copy_launcher(tmp_path, "한글 공백 환경 사전")
    environment, danger_root = _isolated_windows_environment(tmp_path)
    major, clr = _powershell_version(environment)
    assert major == 5, "PowerShell Core로 Windows PowerShell 5.1 시험을 대체할 수 없습니다"
    assert clr.startswith("4.")

    probe_module = app_copy / "uvicorn.py"
    probe_module.write_text(
        """
import json
import os
import pathlib
import re
import sys

root = pathlib.Path(os.environ["APP_DATA_ROOT"]).resolve()
expected = (pathlib.Path.cwd() / ".local_demo").resolve()
dummy_names = {
    "OPENAI_API_KEY", "AZURE_CLIENT_SECRET", "DATABASE_PASSWORD",
    "SENTRY_AUTH_TOKEN", "STRIPE_SECRET_KEY", "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "GITHUB_TOKEN", "CI_JOB_TOKEN",
    "CLOUDFLARE_API_TOKEN",
}
allowed_names = {
    "SystemRoot", "WINDIR", "SystemDrive", "ComSpec", "PATH", "PATHEXT",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "ALLUSERSPROFILE", "ProgramData", "ProgramFiles",
    "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
    "CommonProgramFiles(x86)", "CommonProgramW6432", "PYTHONUTF8",
    "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PIPELINE", "BETA_ADMIN_ONLY",
    "LOCAL_DEMO_AUTH", "LOCAL_DEMO_AUTH_TOKEN", "AUTH_COOKIE_INSECURE",
    "ADMIN_EMAILS", "PORT", "APP_DATA_ROOT", "STORAGE_DB_PATH",
    "OBSERVABILITY_RECORDS_PATH", "TLDEXTRACT_CACHE",
}
allowed_upper = {name.upper() for name in allowed_names}
payload = {
    "flags_ok": all((
        os.environ.get("PIPELINE") == "demo",
        os.environ.get("BETA_ADMIN_ONLY") == "0",
        os.environ.get("LOCAL_DEMO_AUTH") == "1",
        os.environ.get("AUTH_COOKIE_INSECURE") == "1",
        os.environ.get("ADMIN_EMAILS") == "local-demo-admin@example.invalid",
    )),
    "capability_ok": bool(re.fullmatch(r"[0-9a-f]{64}", os.environ.get("LOCAL_DEMO_AUTH_TOKEN", ""))),
    "providers_absent": all(name not in os.environ for name in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
        "ANTHROPIC_API_KEY", "DART_API_KEY", "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET", "NOTION_TOKEN", "NOTION_PARENT_PAGE_ID",
    )),
    "dummy_credential_keys": sorted(dummy_names.intersection(os.environ)),
    "unexpected_secret_keys": sorted(
        name for name in os.environ
        if name != "LOCAL_DEMO_AUTH_TOKEN"
        and name.upper().endswith(("_KEY", "_SECRET", "_TOKEN", "PASSWORD"))
    ),
    "unexpected_environment_keys": sorted(
        name for name in os.environ if name.upper() not in allowed_upper
    ),
    "required_os_keys_present": all(name in os.environ for name in (
        "SystemRoot", "WINDIR", "ComSpec", "PATH", "PATHEXT", "TEMP",
        "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    )),
    "parent_python_config_absent": all(
        name not in os.environ for name in ("PYTHONPATH", "PYTHONHOME")
    ),
    "paths_ok": all((
        root == expected,
        pathlib.Path(os.environ["STORAGE_DB_PATH"]).resolve() == expected / "storage.db",
        pathlib.Path(os.environ["OBSERVABILITY_RECORDS_PATH"]).resolve() == expected / "observability" / "runs.jsonl",
        pathlib.Path(os.environ["TLDEXTRACT_CACHE"]).resolve() == expected / "cache" / "tldextract",
    )),
    "argv_ok": all(flag in sys.argv for flag in (
        "--host", "127.0.0.1", "--workers", "1", "--no-access-log",
    )) and not any("token" in value.casefold() for value in sys.argv),
}
root.mkdir(parents=True, exist_ok=True)
(root / "child-environment.json").write_text(json.dumps(payload), encoding="utf-8")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    parent_check = tmp_path / "parent-unchanged.txt"
    launcher = app_copy / LAUNCHER_PATH.name
    port = _available_loopback_port()
    sentinel_checks = [
        f'$env:{name} -eq "parent-{name.lower()}-sentinel"'
        for name in PARENT_CREDENTIAL_VARIABLES
    ]
    sentinel_checks.extend(
        (
            '$env:LOCAL_DEMO_AUTH_TOKEN -eq "parent-token-must-be-replaced"',
            f'$env:PYTHONPATH -eq {_powershell_literal(environment["PYTHONPATH"])}',
            f'$env:PYTHONHOME -eq {_powershell_literal(environment["PYTHONHOME"])}',
        )
    )
    sentinels = " -and ".join(sentinel_checks)
    wrapper = (
        f"try {{ & {_powershell_literal(launcher)} -Port {port} }} catch {{ }}; "
        f"$unchanged = ({sentinels}); "
        f"[IO.File]::WriteAllText({_powershell_literal(parent_check)}, "
        "$(if ($unchanged) { 'true' } else { 'false' })); "
        "if (-not $unchanged) { exit 9 }"
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
            wrapper,
        ],
        cwd=app_copy,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert result.returncode == 0, _decode_console(result.stderr)
    assert parent_check.read_text(encoding="utf-8") == "true"
    payload = json.loads(
        (app_copy / ".local_demo" / "child-environment.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload == {
        "flags_ok": True,
        "capability_ok": True,
        "providers_absent": True,
        "dummy_credential_keys": [],
        "unexpected_secret_keys": [],
        "unexpected_environment_keys": [],
        "required_os_keys_present": True,
        "parent_python_config_absent": True,
        "paths_ok": True,
        "argv_ok": True,
    }
    assert not danger_root.exists()


class _LiveLauncher:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        stdout: bytearray,
        stderr: bytearray,
        threads: tuple[threading.Thread, threading.Thread],
    ) -> None:
        self.process = process
        self.stdout = stdout
        self.stderr = stderr
        self.threads = threads


def _drain_lines(stream, target: bytearray) -> None:
    try:
        while True:
            chunk = stream.readline()
            if not chunk:
                return
            target.extend(chunk)
    finally:
        stream.close()


def _start_live_launcher(
    app_copy: Path, environment: dict[str, str], port: int
) -> _LiveLauncher:
    assert WINDOWS_POWERSHELL is not None
    process = subprocess.Popen(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(app_copy / LAUNCHER_PATH.name),
            "-Port",
            str(port),
        ],
        cwd=app_copy,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    threads = (
        threading.Thread(target=_drain_lines, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_drain_lines, args=(process.stderr, stderr), daemon=True),
    )
    for thread in threads:
        thread.start()
    return _LiveLauncher(process, stdout, stderr, threads)


def _wait_for_capability(live: _LiveLauncher, timeout: float = 25.0) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = CAPABILITY_URL_RE.search(bytes(live.stdout))
        if match:
            return int(match.group(1)), match.group(2).decode("ascii")
        if live.process.poll() is not None:
            break
        time.sleep(0.05)
    pytest.fail(
        "관리 주소가 나오기 전에 실행기가 끝났습니다.\n"
        + _decode_console(bytes(live.stdout))
        + "\n"
        + _decode_console(bytes(live.stderr))
    )


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, list[tuple[str, str]], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        connection.close()


def _cookie_from(headers: list[tuple[str, str]], name: str) -> str:
    for header, value in headers:
        if header.casefold() != "set-cookie":
            continue
        parsed = SimpleCookie()
        parsed.load(value)
        if name in parsed:
            return parsed[name].value
    return ""


def _listener_rows(port: int, system_root: Path) -> list[tuple[str, int]]:
    result = subprocess.run(
        [str(system_root / "System32" / "netstat.exe"), "-ano", "-p", "tcp"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    rows: list[tuple[str, int]] = []
    for line in _decode_console(result.stdout).splitlines():
        parts = line.split()
        if len(parts) < 5 or not parts[-1].isdigit():
            continue
        local = parts[1]
        try:
            host, raw_port = local.rsplit(":", 1)
        except ValueError:
            continue
        if raw_port != str(port):
            continue
        rows.append((host.strip("[]"), int(parts[-1])))
    return rows


def _stop_with_ctrl_break(live: _LiveLauncher) -> None:
    if live.process.poll() is None:
        live.process.send_signal(signal.CTRL_BREAK_EVENT)
    try:
        live.process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        live.process.kill()
        live.process.wait(timeout=5)
        pytest.fail("Ctrl+Break 뒤 launcher/직계 Python이 제한 시간 안에 끝나지 않았습니다")
    for thread in live.threads:
        thread.join(timeout=3)


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 프로세스 전용 시험",
)
def test_windows_powershell_5_1_실제서버는_loopback_격리_충돌_종료를_지킨다(
    tmp_path: Path,
) -> None:
    app_copy = _copy_runnable_app(tmp_path)
    environment, danger_root = _isolated_windows_environment(tmp_path)
    major, _clr = _powershell_version(environment)
    assert major == 5, "PowerShell Core로 Windows PowerShell 5.1 시험을 대체할 수 없습니다"
    port = _available_loopback_port()
    live = _start_live_launcher(app_copy, environment, port)
    stopped = False
    try:
        shown_port, capability = _wait_for_capability(live)
        assert shown_port == port
        status, _headers, body = _request(port, "GET", "/healthz")
        assert status == 200 and json.loads(body) == {"status": "ok"}

        system_root = Path(environment["SystemRoot"])
        listeners = _listener_rows(port, system_root)
        assert listeners and {host for host, _pid in listeners} == {"127.0.0.1"}
        listener_pids = {pid for _host, pid in listeners}
        assert len(listener_pids) == 1
        child_process = json.loads(
            (app_copy / ".local_demo" / "child-process.json").read_text(
                encoding="utf-8"
            )
        )
        assert child_process["pid"] in listener_pids
        assert child_process["dummy_credential_keys"] == []
        assert child_process["unexpected_secret_keys"] == []
        assert child_process["required_os_keys_present"] is True
        assert child_process["parent_python_config_absent"] is True
        child_command = " ".join(child_process["argv"])
        parent_command = " ".join(str(value) for value in live.process.args)
        for command_line in (child_command, parent_command):
            assert capability not in command_line
            assert "token=" not in command_line.casefold()
        assert "--host 127.0.0.1" in child_command
        assert "--workers 1" in child_command
        assert "--no-access-log" in child_command

        # 공개 Host와 forwarded HTTPS에서는 root capability가 있어도 흔적 없이 닫힌다.
        blocked_path = f"/auth/local-demo/start?token={capability}"
        public_status, _public_headers, public_body = _request(
            port, "GET", blocked_path, headers={"Host": "example.test"}
        )
        assert public_status == 404 and capability.encode() not in public_body
        https_status, _https_headers, https_body = _request(
            port,
            "GET",
            blocked_path,
            headers={"Host": f"127.0.0.1:{port}", "X-Forwarded-Proto": "https"},
        )
        assert https_status == 404 and capability.encode() not in https_body

        # root는 URL에서 즉시 사라지고, grant/state만 한 번 소비된다.
        start_status, start_headers, start_body = _request(port, "GET", blocked_path)
        assert start_status == 303 and start_body == b""
        locations = [v for k, v in start_headers if k.casefold() == "location"]
        assert locations == ["/auth/local-demo"]
        assert capability not in locations[0]
        grant = _cookie_from(start_headers, "local_demo_grant")
        assert grant

        landing_status, landing_headers, landing_body = _request(
            port,
            "GET",
            "/auth/local-demo",
            headers={"Cookie": f"local_demo_grant={grant}"},
        )
        assert landing_status == 200
        state_match = re.search(rb'name="state" value="([^"]+)"', landing_body)
        assert state_match is not None
        state = state_match.group(1).decode("ascii")
        state_cookie = _cookie_from(landing_headers, "local_demo_state")
        assert state_cookie == state
        form = urlencode({"state": state}).encode("ascii")
        cookie = f"local_demo_grant={grant}; local_demo_state={state_cookie}"
        login_status, login_headers, _login_body = _request(
            port,
            "POST",
            "/auth/local-demo",
            body=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(form)),
                "Cookie": cookie,
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        assert login_status == 303
        session = _cookie_from(login_headers, "auth_session")
        assert session
        admin_status, _admin_headers, _admin_body = _request(
            port, "GET", "/admin", headers={"Cookie": f"auth_session={session}"}
        )
        assert admin_status == 200
        reused_status, _reused_headers, reused_body = _request(
            port,
            "POST",
            "/auth/local-demo",
            body=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(form)),
                "Cookie": cookie,
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        assert reused_status == 404 and capability.encode() not in reused_body

        # 같은 실행의 root capability는 새 브라우저 재진입용 grant를 다시 만든다.
        reentry_status, reentry_headers, _reentry_body = _request(
            port, "GET", blocked_path
        )
        assert reentry_status == 303
        assert _cookie_from(reentry_headers, "local_demo_grant") not in ("", grant)

        # 두 번째 실행은 token을 출력하기 전에 실패하고 첫 서버는 계속 정상이다.
        second_app = _copy_launcher(tmp_path, "두 번째 한글 경로")
        collision = subprocess.run(
            [
                WINDOWS_POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(second_app / LAUNCHER_PATH.name),
                "-Port",
                str(port),
            ],
            cwd=second_app,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        collision_output = collision.stdout + collision.stderr
        assert collision.returncode != 0
        assert b"-Port" in collision_output
        assert CAPABILITY_URL_RE.search(collision_output) is None
        assert b"token=" not in collision_output.lower()
        assert _request(port, "GET", "/healthz")[0] == 200

        _stop_with_ctrl_break(live)
        stopped = True
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _listener_rows(port, system_root):
            time.sleep(0.1)
        assert _listener_rows(port, system_root) == []

        stderr = bytes(live.stderr)
        assert capability.encode() not in stderr
        assert b"token=" not in stderr.lower()
        combined_logs = (bytes(live.stdout) + stderr).decode("utf-8", errors="replace")
        assert "GET /healthz" not in combined_logs
        assert "GET /auth/local-demo" not in combined_logs

        local_demo = app_copy / ".local_demo"
        assert (local_demo / "storage.db").is_file()
        assert not danger_root.exists()
        for path in local_demo.rglob("*"):
            if not path.is_file():
                continue
            content = path.read_bytes()
            assert capability.encode() not in content
            assert b"token=" not in content.lower()
    finally:
        if not stopped and live.process.poll() is None:
            try:
                _stop_with_ctrl_break(live)
            except BaseException:
                live.process.kill()
                live.process.wait(timeout=5)

"""실시간 성능시험 실행기의 정적 경계와 PowerShell 5.1 자식 환경."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = APP_ROOT / "실시간성능시험켜기.ps1"
SCRIPT = LAUNCHER.read_text(encoding="utf-8")
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
PAID_PROVIDER_NAMES = (
    "DART_API_KEY",
    "ANTHROPIC_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
)
PROVIDER_STATUS_NAMES = PAID_PROVIDER_NAMES + (
    "GOOGLE_PLACES_API_KEY",
    "GOOGLE_PLACES_TERMS_ACK",
)


def test_launcher_has_fail_closed_real_evaluation_contract() -> None:
    required_fragments = (
        "--host 127.0.0.1",
        "--workers 1",
        "--no-access-log",
        '$childEnvironment["PIPELINE"] = "real"',
        '$childEnvironment["BETA_ADMIN_ONLY"] = "0"',
        '$childEnvironment["AUTH_COOKIE_INSECURE"] = "1"',
        '$childEnvironment["ANALYSIS_ENGINE_DISABLE_DOTENV"] = "1"',
        '$childEnvironment["BUSINESS_CANDIDATE_PROVIDER"] = "disabled"',
        '$childEnvironment["GOOGLE_PLACES_BILLING_ACK"] = "0"',
        '$childEnvironment["GOOGLE_PLACES_TERMS_ACK"] = "no"',
        "REALTIME_EVALUATION_PER_RUN_CAP_KRW",
        "REALTIME_EVALUATION_DAILY_CAP_KRW",
        ".local_evaluation_runs",
        "RandomNumberGenerator",
        "-DeleteDataOnExit",
        "ReparsePoint",
    )
    for fragment in required_fragments:
        assert fragment in SCRIPT
    assert "[double]$PerRunExpectedCostCapKrw = 1200" in SCRIPT
    assert "[double]$DailyExpectedCostCapKrw = 2200" in SCRIPT
    for name in PROVIDER_STATUS_NAMES:
        assert f'"{name}"' in SCRIPT
    assert "Get-Content" not in SCRIPT
    assert "GetEnvironmentVariable" not in SCRIPT
    assert "analysis_engine/.env" not in SCRIPT
    assert "app/.env" not in SCRIPT
    assert "0.0.0.0" not in SCRIPT


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
        "EVALUATION_TEST_COMPANY": "JYP-sensitive-test-input",
        "EVALUATION_TEST_ADDRESS": "Seoul-Gangdong-sensitive-test-input",
        "EVALUATION_TEST_POSTING": "posting-sensitive-test-input",
    }
    for name in PROVIDER_STATUS_NAMES:
        environment[name] = f"secret-sentinel-{name.lower()}"
    return environment


def _copy_fake_app(tmp_path: Path) -> Path:
    app_copy = tmp_path / "한글 공백 평가 실행" / "app"
    app_copy.mkdir(parents=True)
    shutil.copy2(LAUNCHER, app_copy / LAUNCHER.name)
    (app_copy / "uvicorn.py").write_text(
        """
import json
import os
import pathlib
import sys

provider_names = (
    "DART_API_KEY", "ANTHROPIC_API_KEY", "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET", "GOOGLE_PLACES_API_KEY",
)
root = pathlib.Path(os.environ["APP_DATA_ROOT"]).resolve()
root.mkdir(parents=True, exist_ok=True)
payload = {
    "provider_presence": {name: name in os.environ for name in provider_names},
    "paid": os.environ.get("REALTIME_EVALUATION_PAID_PROVIDERS"),
    "provider_mode": os.environ.get("BUSINESS_CANDIDATE_PROVIDER"),
    "billing_ack": os.environ.get("GOOGLE_PLACES_BILLING_ACK"),
    "terms_ack": os.environ.get("GOOGLE_PLACES_TERMS_ACK"),
    "pipeline": os.environ.get("PIPELINE"),
    "dotenv_disabled": os.environ.get("ANALYSIS_ENGINE_DISABLE_DOTENV"),
    "loopback_flags": all(value in sys.argv for value in (
        "--host", "127.0.0.1", "--workers", "1", "--no-access-log",
    )),
    "sensitive_parent_inputs_absent": all(name not in os.environ for name in (
        "EVALUATION_TEST_COMPANY", "EVALUATION_TEST_ADDRESS",
        "EVALUATION_TEST_POSTING",
    )),
    "sensitive_inputs_absent_from_argv": not any(
        marker in " ".join(sys.argv) for marker in ("JYP", "Gangdong", "posting")
    ),
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
    paid: bool,
    delete_data_on_exit: bool = False,
) -> tuple[subprocess.CompletedProcess[bytes], list[Path]]:
    assert WINDOWS_POWERSHELL is not None
    launcher = app_copy / LAUNCHER.name
    switch = " -EnablePaidProviders" if paid else ""
    if delete_data_on_exit:
        switch += " -DeleteDataOnExit"
    command = (
        f"try {{ & {_ps_literal(launcher)} -Port {_available_port()}{switch} }} "
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
        timeout=30,
    )
    return result, list(app_copy.rglob("child-environment.json"))


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
@pytest.mark.parametrize("paid", [False, True], ids=("preview", "paid"))
def test_powershell_5_1_fake_child_obeys_allowlist_and_isolated_run_root(
    tmp_path: Path, paid: bool
) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(app_copy, environment, paid=paid)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["provider_presence"] == {
        **{name: paid for name in PAID_PROVIDER_NAMES},
        "GOOGLE_PLACES_API_KEY": False,
    }
    assert payload["paid"] == ("1" if paid else "0")
    assert payload["provider_mode"] == "disabled"
    assert payload["billing_ack"] == "0"
    assert payload["terms_ack"] == "no"
    assert payload["pipeline"] == "real"
    assert payload["dotenv_disabled"] == "1"
    assert payload["loopback_flags"] is True
    assert payload["sensitive_parent_inputs_absent"] is True
    assert payload["sensitive_inputs_absent_from_argv"] is True
    assert payload["paths_inside_run_root"] is True
    assert records[0].parent.parent == app_copy / ".local_evaluation_runs"
    console = (result.stdout + result.stderr).decode(errors="replace")
    assert not any(value in console for value in environment.values() if "sentinel" in value)


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_paid_switch_with_missing_key_fails_before_child_creation(tmp_path: Path) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)
    environment.pop("ANTHROPIC_API_KEY")

    result, records = _run_fake(app_copy, environment, paid=True)

    assert result.returncode == 0
    assert records == []
    console = (result.stdout + result.stderr).decode(errors="replace")
    assert "ANTHROPIC_API_KEY" in console
    assert "secret-sentinel" not in console
    assert not (app_copy / ".local_evaluation_runs").exists()


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_launcher_derives_windir_from_systemroot_when_parent_omits_it(
    tmp_path: Path,
) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)
    environment.pop("windir")

    result, records = _run_fake(app_copy, environment, paid=False)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1


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
        app_copy,
        environment,
        paid=False,
        delete_data_on_exit=True,
    )

    assert result.returncode == 0
    assert records == []
    runs_root = app_copy / ".local_evaluation_runs"
    assert runs_root.is_dir()
    assert list(runs_root.iterdir()) == []

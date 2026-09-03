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


def test_launcher_reports_provider_environment_file_read_accurately() -> None:
    assert "키 값은 출력·파일 저장하지 않았고 .env도 읽지 않았습니다." not in SCRIPT
    assert 'if ($ProviderEnvFile) {' in SCRIPT
    assert "지정한 provider 환경 파일만 읽었으며" in SCRIPT
    assert "환경 파일도 읽지 않았습니다." in SCRIPT


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
    "engine_v2": os.environ.get("ENGINE_V2"),
    "release_mode": os.environ.get("REPORT_RELEASE_MODE"),
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
    provider_env_file: Path | None = None,
    engine_v2: bool = False,
    release_mode: str | None = None,
    delete_data_on_exit: bool = False,
) -> tuple[subprocess.CompletedProcess[bytes], list[Path]]:
    assert WINDOWS_POWERSHELL is not None
    launcher = app_copy / LAUNCHER.name
    switch = " -EnablePaidProviders" if paid else ""
    if engine_v2:
        switch += " -EngineV2"
    if release_mode is not None:
        switch += f" -ReleaseMode {_ps_literal(release_mode)}"
    if provider_env_file is not None:
        switch += f" -ProviderEnvFile {_ps_literal(provider_env_file)}"
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
def test_provider_environment_file_message_matches_actual_read(tmp_path: Path) -> None:
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)
    provider_values = {
        name: f"file-only-secret-{index}"
        for index, name in enumerate(PAID_PROVIDER_NAMES)
    }
    for name in PAID_PROVIDER_NAMES:
        environment.pop(name)
    provider_env_file = tmp_path / "provider inputs.env"
    provider_env_file.write_text(
        "\n".join(f'{name}="{value}"' for name, value in provider_values.items()),
        encoding="utf-8",
    )

    result, records = _run_fake(
        app_copy,
        environment,
        paid=True,
        provider_env_file=provider_env_file,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["provider_presence"] == {
        **{name: True for name in PAID_PROVIDER_NAMES},
        "GOOGLE_PLACES_API_KEY": False,
    }
    console = result.stdout + result.stderr
    assert b".env" not in console
    assert not any(value.encode() in console for value in provider_values.values())


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


def test_launcher_can_turn_on_engine_v2() -> None:
    """★ v2를 켜는 «유일한» 경로를 못 박는다.

    실측(v2-28 시점): 이 세 줄을 지워도 깨지는 시험이 없었다 —
    ENGINE_V2를 자식에게 넘기는 «유일한» 실행기인데 무방비였다.

    ★ 이 시험은 «로컬에서 v2를 켤 수 있는가»만 본다.
      «배포에서 v2가 켜지는가»는 render.yaml이 소유하고
      deploy/tests/test_deployment_contract.py가 따로 지킨다(v2-29에서 추가).
      두 시험이 각자 자기 경로를 지킨다 — 한 시험이 둘 다 지키면
      한쪽을 고칠 때 다른 쪽이 조용히 풀린다.
    """
    assert "[switch]$EngineV2" in SCRIPT, "-EngineV2 스위치가 사라졌습니다"
    assert '$childEnvironment["ENGINE_V2"] = "1"' in SCRIPT, (
        "스위치는 있는데 자식에게 ENGINE_V2를 안 넘깁니다"
    )
    # allowlist에 없으면 실행기가 시작을 «거부»한다 — 셋이 함께 있어야 동작한다.
    assert '"ENGINE_V2"' in SCRIPT.split("$allowedChildEnvironmentNames")[1], (
        "ENGINE_V2가 자식 환경 허용 목록에 없습니다 — 실행기가 시작을 거부합니다"
    )


def test_engine_v2_child_always_gets_the_report_release_mode() -> None:
    """v2를 켜면서 출시 모드를 안 넘기면 조사가 AI 호출 전에 전부 멈춘다.

    근거: 값이 비면 ``src/features/pipeline/real.py:3510-3514``가 ValueError를
    던지고 그 갈래는 ``GATE_STOPPED``로 끝난다 — 성능을 잴 구간까지 못 간다.
    """
    assert '[ValidateSet("FULL", "ENFORCE_NO_PARTIAL", "SHADOW", IgnoreCase = $false)]' in SCRIPT, (
        "허용 값은 ReleaseMode 계약"
        "(src/shared/report_evidence/constants.py:81-87)과 같아야 한다"
    )
    assert '[string]$ReleaseMode = "FULL"' in SCRIPT
    v2_branch = SCRIPT.split('$childEnvironment["ENGINE_V2"] = "1"')[1]
    assert '$childEnvironment["REPORT_RELEASE_MODE"] = $ReleaseMode' in v2_branch, (
        "ENGINE_V2=1을 켜는 갈래가 REPORT_RELEASE_MODE를 함께 넘겨야 한다"
    )
    child_allowlist = SCRIPT.split("$allowedChildEnvironmentNames")[1]
    assert '"REPORT_RELEASE_MODE"' in child_allowlist, (
        "자식 환경 허용 목록에 없으면 실행기가 시작을 거부합니다"
    )


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_engine_v2_switch_carries_release_mode_to_the_child(tmp_path: Path) -> None:
    """-EngineV2로 켠 자식이 ENGINE_V2와 출시 모드를 함께 받는다."""
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(app_copy, environment, paid=False, engine_v2=True)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["engine_v2"] == "1"
    assert payload["release_mode"] == "FULL"


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 실제 자식 환경 시험",
)
def test_engine_v1_child_gets_no_release_mode(tmp_path: Path) -> None:
    """v1 경로는 이 값을 읽지 않으므로 넘기지도 않는다."""
    app_copy = _copy_fake_app(tmp_path)
    environment = _environment(tmp_path)

    result, records = _run_fake(app_copy, environment, paid=False)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["engine_v2"] is None
    assert payload["release_mode"] is None

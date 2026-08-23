"""Render cron 진입점과 최소 Docker build context 계약을 고정한다."""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path

import pytest
import yaml


APP_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = APP_ROOT.parent
CONTRACT_PATH = (
    REPOSITORY_ROOT / ".github" / "scripts" / "verify_container_contract.py"
)
SPEC = importlib.util.spec_from_file_location("verify_container_contract", CONTRACT_PATH)
assert SPEC is not None and SPEC.loader is not None
container_contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = container_contract
SPEC.loader.exec_module(container_contract)


def test_dockerfile_copies_every_cron_module() -> None:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")

    for relative_path in (
        "app/tools/__init__.py",
        "app/tools/internal_trigger.py",
        "app/tools/trigger_backup.py",
        "app/tools/trigger_maintenance.py",
        "app/tools/container_entrypoint.sh",
        "deploy/container_entrypoint.sh",
        "deploy/container_healthcheck.py",
        "deploy/validate_environment.py",
        "deploy/verify_image.py",
    ):
        assert f"COPY {relative_path} " in dockerfile


def test_dockerfile_uses_only_explicit_copy_sources() -> None:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")
    instructions = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert not any(line.upper().startswith("ADD ") for line in instructions)
    assert "COPY . /srv/app" not in dockerfile
    assert "COPY app/ /srv/app" not in dockerfile
    for line in instructions:
        if not line.upper().startswith("COPY "):
            continue
        source = line.split()[1].rstrip("/")
        assert source not in {".", "app", "analysis_engine"}
        assert (REPOSITORY_ROOT / source).exists(), source


def test_dockerignore_is_allowlist_and_rejects_private_artifacts() -> None:
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    rules = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert dockerignore.lstrip().startswith("# Docker에는")
    assert "**" in rules
    for required in (
        "!app/tools/__init__.py",
        "!app/tools/internal_trigger.py",
        "!app/tools/backup_sqlite.py",
        "!app/tools/trigger_backup.py",
        "!app/tools/trigger_maintenance.py",
        "!deploy/container_entrypoint.sh",
        "!deploy/container_healthcheck.py",
        "!deploy/validate_environment.py",
        "!deploy/verify_image.py",
    ):
        assert required in rules
    for forbidden in (
        "**/.env",
        "**/.env.*",
        "**/.secure_prompt",
        "**/.secure_prompt.*",
        "**/client_secret*.json",
        "**/credentials*.json",
        "**/token*.json",
        "**/oauth*.json",
        "**/service-account*.json",
        "**/*.pem",
        "**/*.key",
        "**/*.p12",
        "**/*.pfx",
        "**/runtime-config",
        "**/runtime-config.*",
        "**/tests/",
        "**/*.db",
        "**/*.sqlite",
        "**/*.sqlite3",
        "**/*.sha256",
        "**/backups/",
        "**/raw/",
        "**/raw_filings/",
        "**/__pycache__/",
    ):
        assert forbidden in rules


def test_container_entrypoint_is_not_hidden_by_repository_allowlist() -> None:
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    rules = gitignore.splitlines()

    assert rules.index("!/app/tools/container_entrypoint.sh") > rules.index("/app/tools/*")


def test_first_render_beta_defers_cron_entrypoints() -> None:
    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )

    assert [service["name"] for service in blueprint["services"]] == [
        "company-analysis-beta"
    ]
    assert all(service["type"] == "web" for service in blueprint["services"])


def test_all_git_based_render_services_disable_automatic_deploys() -> None:
    blueprint = yaml.load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    services = blueprint["services"]
    git_based_services = [
        service
        for service in services
        if "dockerfilePath" in service or "buildCommand" in service
    ]

    assert {service["name"] for service in git_based_services} == {
        "company-analysis-beta",
    }
    assert not any(service["type"] == "cron" for service in git_based_services)
    assert all(
        service.get("autoDeployTrigger") == "off"
        for service in git_based_services
    )


def test_first_render_beta_does_not_request_backup_or_provider_secrets() -> None:
    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )
    web = blueprint["services"][0]
    names = {item["key"] for item in web["envVars"]}

    assert not names.intersection(
        {
            "BACKUP_S3_BUCKET",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "ANTHROPIC_API_KEY",
            "DART_API_KEY",
            "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET",
            "NOTION_TOKEN",
            "NOTION_PARENT_PAGE_ID",
        }
    )


def test_command_override_still_passes_through_validating_non_root_entrypoint() -> None:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (
        REPOSITORY_ROOT / "deploy" / "container_entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["company-analysis-entrypoint"]' in dockerfile
    assert "install -m 0755 /srv/deploy/container_entrypoint.sh" in dockerfile
    assert "USER appuser" in dockerfile
    assert "USER root" not in dockerfile
    assert 'if [ "$(id -u)" -eq 0 ]' in entrypoint
    assert "validate_environment.py" in entrypoint
    assert 'exec "$@"' in entrypoint
    assert 'CMD ["sh", "-c"' in dockerfile


def test_container_scan_root_matches_entrypoint_copy_destination() -> None:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "COPY app/tools/container_entrypoint.sh "
        "/srv/app/tools/container_entrypoint.sh"
    ) in dockerfile
    assert "app/tools/container_entrypoint.sh" in container_contract.REQUIRED_FILES


def test_ci_imports_cron_modules_and_scans_built_image() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "quality-gate.yml"
    ).read_text(encoding="utf-8")

    assert "컨테이너 cron·비밀 파일 계약 확인" in workflow
    assert "verify_container_contract.py" in workflow
    assert "--root /srv" in workflow
    assert "Docker 기본 사용자와 실제 UID 확인" in workflow
    assert "docker image inspect --format '{{.Config.User}}'" in workflow
    assert "--entrypoint python" in workflow
    assert "os.geteuid() == 1000" in workflow


def _make_fake_image_root(root: Path) -> None:
    for relative in container_contract.REQUIRED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")


def test_container_scan_accepts_only_required_runtime_files(tmp_path: Path) -> None:
    _make_fake_image_root(tmp_path)
    harmless = tmp_path / "app" / "src" / "feature.py"
    harmless.parent.mkdir(parents=True, exist_ok=True)
    harmless.write_text("VALUE = 1\n", encoding="utf-8")

    container_contract.verify_files(tmp_path)


@pytest.mark.parametrize(
    "relative",
    (
        "app/.env",
        "app/src/client_secret-prod.json",
        "app/src/private.key",
        "app/src/storage.sqlite3",
        "analysis_engine/src/tests/fixture.py",
    ),
)
def test_container_scan_rejects_private_and_local_artifacts(
    tmp_path: Path, relative: str
) -> None:
    _make_fake_image_root(tmp_path)
    forbidden = tmp_path / relative
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("must-not-ship\n", encoding="utf-8")

    with pytest.raises(container_contract.ContainerContractError, match="금지된"):
        container_contract.verify_files(tmp_path)

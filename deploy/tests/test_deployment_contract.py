"""클라우드 중립 컨테이너 배포 계약의 정적·환경 검증 시험."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPOSITORY_ROOT / "app" / "Dockerfile"
DEPLOY_ROOT = REPOSITORY_ROOT / "deploy"
VALIDATOR_PATH = DEPLOY_ROOT / "validate_environment.py"
SPEC = importlib.util.spec_from_file_location("deploy_validate_environment", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

VERIFIER_PATH = DEPLOY_ROOT / "verify_image.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "deploy_verify_image", VERIFIER_PATH
)
assert VERIFIER_SPEC is not None and VERIFIER_SPEC.loader is not None
image_verifier = importlib.util.module_from_spec(VERIFIER_SPEC)
sys.modules[VERIFIER_SPEC.name] = image_verifier
VERIFIER_SPEC.loader.exec_module(image_verifier)


def _base_environment() -> dict[str, str]:
    return {
        "PIPELINE": "demo",
        "BETA_ADMIN_ONLY": "0",
        "PORT": "10000",
        "LOG_LEVEL": "info",
        "GRACEFUL_SHUTDOWN_SECONDS": "300",
        "DEPLOYMENT_EXPOSURE": "local",
        "DEPLOYMENT_PLATFORM": "local",
        "FORWARDED_ALLOW_IPS": "127.0.0.1",
        "APP_DATA_ROOT": "/var/data",
        "STORAGE_DB_PATH": "/var/data/storage.db",
        "OBSERVABILITY_RECORDS_PATH": "/var/data/observability/runs.jsonl",
        "TLDEXTRACT_CACHE": "/var/data/cache/tldextract",
    }


def _yaml_documents(path: Path) -> list[dict[str, object]]:
    return [
        document
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if document
    ]


def test_image_is_non_root_and_has_runtime_health_shutdown_contract() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "USER appuser" in dockerfile
    assert "USER root" not in dockerfile
    assert "root_password" not in dockerfile
    assert "openssl rand" not in dockerfile
    assert 'VOLUME ["/var/data"]' in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert "HEALTHCHECK --interval=15s" in dockerfile
    assert "container_healthcheck.py" in dockerfile
    assert "--timeout-graceful-shutdown" in dockerfile
    assert "--no-access-log" not in dockerfile


def test_pdf_runtime_dependencies_and_fonts_are_in_the_image_context() -> None:
    requirements = (REPOSITORY_ROOT / "app" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    for package in ("reportlab==", "pypdf==", "pdfplumber==", "pypdfium2=="):
        assert package in requirements
    for font_name in ("Freesentation-Regular.ttf", "Freesentation-SemiBold.ttf"):
        assert (
            REPOSITORY_ROOT
            / "app"
            / "src"
            / "features"
            / "export_pdf"
            / "fonts"
            / font_name
        ).is_file()
    assert "COPY app/src/ /srv/app/src/" in dockerfile


def test_build_context_is_allowlist_and_includes_only_runtime_deploy_helpers() -> None:
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    rules = {line.strip() for line in dockerignore.splitlines() if line.strip()}

    assert "**" in rules
    for required in (
        "!deploy/container_entrypoint.sh",
        "!deploy/container_healthcheck.py",
        "!deploy/validate_environment.py",
        "!deploy/verify_image.py",
    ):
        assert required in rules
    for forbidden in (
        "**/.env",
        "**/.secure_prompt",
        "**/*secret*.json",
        "**/runtime-config.*",
        "**/*.pem",
        "**/*.key",
        "**/*.sqlite3",
    ):
        assert forbidden in rules


def test_demo_environment_passes_without_paid_provider_values() -> None:
    assert validator.validate(_base_environment(), "web") == []


def test_configured_backup_fails_closed_until_manifest_adapter_is_installed() -> None:
    environment = _base_environment()
    environment["BACKUP_S3_BUCKET"] = "example-private-bucket"

    joined = "\n".join(validator.validate(environment, "web"))

    for name in (
        "BACKUP_TRIGGER_SECRET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "BACKUP_DATA_BOUNDARY_ID",
        "BACKUP_DATA_AUTHORITY_ID",
        "BACKUP_MANIFEST_MIN_RETENTION_DAYS",
        "BACKUP_MANIFEST_APPENDER",
    ):
        assert name in joined

    environment.update(
        {
            "BACKUP_TRIGGER_SECRET": "x" * 32,
            "AWS_ACCESS_KEY_ID": "test-access-id",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "BACKUP_DATA_BOUNDARY_ID": "backup-data-boundary",
            "BACKUP_DATA_AUTHORITY_ID": "backup-data-writer",
            "BACKUP_RETENTION_DAYS": "35",
            "BACKUP_MANIFEST_MIN_RETENTION_DAYS": "34",
        }
    )
    retention_errors = "\n".join(validator.validate(environment, "web"))
    assert "DB 백업 보존 기간보다 짧을 수 없습니다" in retention_errors
    assert validator.PRODUCTION_BACKUP_MANIFEST_BLOCKER in retention_errors

    environment["BACKUP_MANIFEST_MIN_RETENTION_DAYS"] = "35"
    assert validator.validate(environment, "web") == [
        validator.PRODUCTION_BACKUP_MANIFEST_BLOCKER
    ]


def test_backup_examples_expose_required_names_without_a_readiness_bypass() -> None:
    variable_names = (
        "BACKUP_DATA_BOUNDARY_ID",
        "BACKUP_DATA_AUTHORITY_ID",
        "BACKUP_MANIFEST_MIN_RETENTION_DAYS",
    )
    runtime_example = (DEPLOY_ROOT / "runtime-config.example").read_text(
        encoding="utf-8"
    )
    app_example = (REPOSITORY_ROOT / "app" / ".env.example").read_text(
        encoding="utf-8"
    )
    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )
    web_service = next(
        service for service in blueprint["services"] if service["type"] == "web"
    )
    render_names = {item["key"] for item in web_service["envVars"]}

    for name in variable_names:
        assert f"{name}=" in runtime_example
        assert f"{name}=" in app_example
        assert name in render_names
    assert "BACKUP_MANIFEST_APPENDER_READY" not in runtime_example
    assert validator.PRODUCTION_BACKUP_MANIFEST_APPENDER_AVAILABLE is False

    render_guide = (
        REPOSITORY_ROOT / "app" / "docs" / "Render_배포.md"
    ).read_text(encoding="utf-8")
    assert "현재 외부 백업 배포는 BLOCKED" in render_guide
    assert "install_manifest_appender_provider(...)" in render_guide


def test_real_environment_fails_closed_without_leaking_values() -> None:
    environment = _base_environment()
    environment["PIPELINE"] = "real"
    environment["PROVENANCE_SEAL_SECRET"] = "do-not-leak"

    errors = validator.validate(environment, "web")
    joined = "\n".join(errors)

    for name in (
        "ANTHROPIC_API_KEY",
        "DART_API_KEY",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
        "PROVENANCE_SEAL_SECRET",
    ):
        assert name in joined
    assert environment["PROVENANCE_SEAL_SECRET"] not in joined


def test_admin_login_and_persistence_paths_fail_closed() -> None:
    environment = _base_environment()
    environment["BETA_ADMIN_ONLY"] = "1"
    environment["STORAGE_DB_PATH"] = "/tmp/storage.db"

    errors = "\n".join(validator.validate(environment, "web"))

    assert "ADMIN_EMAILS" in errors
    assert "GOOGLE_CLIENT_ID" in errors
    assert "GOOGLE_CLIENT_SECRET" in errors
    assert "GOOGLE_REDIRECT_URI" in errors
    assert "STORAGE_DB_PATH" in errors


def test_trigger_scopes_require_only_their_own_credentials() -> None:
    environment = _base_environment()

    backup_errors = "\n".join(validator.validate(environment, "backup-trigger"))
    maintenance_errors = "\n".join(
        validator.validate(environment, "maintenance-trigger")
    )

    assert "BACKUP_TRIGGER_URL" in backup_errors
    assert "BACKUP_TRIGGER_SECRET" in backup_errors
    assert "GOOGLE_CLIENT_SECRET" not in backup_errors
    assert "MAINTENANCE_TRIGGER_URL" in maintenance_errors
    assert "MAINTENANCE_TRIGGER_SECRET" in maintenance_errors
    assert "ANTHROPIC_API_KEY" not in maintenance_errors


def test_image_verifier_rejects_root_and_local_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    for relative in image_verifier.REQUIRED_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("시험용 런타임 파일\n", encoding="utf-8")

    monkeypatch.setattr(image_verifier.os, "geteuid", lambda: 1000, raising=False)
    assert image_verifier.verify(tmp_path) == []

    local_database = tmp_path / "app" / "storage.sqlite3"
    local_database.write_bytes(b"not-a-real-database")
    assert any("storage.sqlite3" in error for error in image_verifier.verify(tmp_path))

    monkeypatch.setattr(image_verifier.os, "geteuid", lambda: 0, raising=False)
    assert any("root" in error for error in image_verifier.verify(tmp_path))


def test_compose_has_read_only_non_root_volume_and_log_rotation() -> None:
    compose = yaml.safe_load(
        (DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    )
    web = compose["services"]["web"]

    assert web["user"] == "1000:1000"
    assert web["read_only"] is True
    assert web["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in web["security_opt"]
    assert web["stop_grace_period"] == "330s"
    assert "app-data:/var/data" in web["volumes"]
    assert web["logging"]["options"] == {"max-size": "10m", "max-file": "5"}
    assert web["environment"]["DEPLOYMENT_EXPOSURE"] == "local"
    assert web["environment"]["FORWARDED_ALLOW_IPS"] == "127.0.0.1"


def test_kubernetes_has_liveness_readiness_and_persistent_single_writer() -> None:
    documents = _yaml_documents(DEPLOY_ROOT / "kubernetes" / "base.yaml")
    by_kind = {document["kind"]: document for document in documents}

    assert "Secret" not in by_kind
    deployment = by_kind["Deployment"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["terminationGracePeriodSeconds"] == 330
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["runAsUser"] == 1000
    container = pod_spec["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert any("secretRef" in item for item in container["envFrom"])
    assert by_kind["PersistentVolumeClaim"]["spec"]["accessModes"] == [
        "ReadWriteOnce"
    ]
    config = by_kind["ConfigMap"]["data"]
    assert config["DEPLOYMENT_EXPOSURE"] == "public"
    assert config["DEPLOYMENT_PLATFORM"] == "kubernetes"
    assert config["FORWARDED_ALLOW_IPS"] != "127.0.0.1"
    policy = by_kind["NetworkPolicy"]["spec"]
    assert policy["policyTypes"] == ["Ingress"]
    assert policy["ingress"][0]["from"][0]["ipBlock"]["cidr"] == config[
        "K8S_INGRESS_PROXY_CIDRS"
    ]


def test_local_scripts_do_not_push_or_deploy_and_smoke_disables_network() -> None:
    build_script = (
        REPOSITORY_ROOT / "scripts" / "deploy" / "build-image.ps1"
    ).read_text(encoding="utf-8")
    smoke_script = (
        REPOSITORY_ROOT / "scripts" / "deploy" / "smoke-container.ps1"
    ).read_text(encoding="utf-8")

    combined = (build_script + "\n" + smoke_script).lower()
    assert "docker push" not in combined
    assert "kubectl apply" not in combined
    assert "--network none" in smoke_script
    assert "PIPELINE=demo" in smoke_script
    assert "BETA_ADMIN_ONLY=0" in smoke_script
    assert "verify_image.py" in smoke_script
    assert "--provenance=false" not in build_script
    assert "--provenance=mode=max" in build_script
    assert "--sbom=true" in build_script
    release_script = (
        REPOSITORY_ROOT / "scripts" / "deploy" / "validate-release-evidence.ps1"
    ).read_text(encoding="utf-8")
    assert "validate_release_evidence.py" in release_script
    assert "docker push" not in release_script.lower()
    for argument in ("--scan-report", "--sbom", "--provenance", "--signature-bundle"):
        assert argument in release_script
    assert "--policy" not in release_script
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_VERIFIER_AVAILABLE is False
    assert not (DEPLOY_ROOT / "release-policy.json").exists()
    assert (DEPLOY_ROOT / "release-policy.sha256").read_text(
        encoding="ascii"
    ).strip() == "BLOCKED"

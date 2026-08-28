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
        "GRACEFUL_SHUTDOWN_SECONDS": "20",
        "DEPLOYMENT_EXPOSURE": "local",
        "DEPLOYMENT_PLATFORM": "local",
        "DEPLOYMENT_RUNTIME_CONTRACT": validator.RUNTIME_CONTRACT_LOCAL_WEB,
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
    assert "--no-proxy-headers" in dockerfile
    assert "--proxy-headers" not in dockerfile
    assert "--forwarded-allow-ips" not in dockerfile
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
        assert name not in render_names
    for name in validator.BACKUP_RUNTIME_VARIABLES:
        assert name not in render_names
    assert not any(
        service["type"] == "cron" for service in blueprint["services"]
    )
    assert "BACKUP_MANIFEST_APPENDER_READY" not in runtime_example
    assert validator.PRODUCTION_BACKUP_MANIFEST_APPENDER_AVAILABLE is False

    render_guide = (
        REPOSITORY_ROOT / "app" / "docs" / "Render_배포.md"
    ).read_text(encoding="utf-8")
    assert "현재 외부 백업 배포는 BLOCKED" in render_guide
    assert "install_manifest_appender_provider(...)" in render_guide
    blueprint_text = (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "외부 백업 cron과 BACKUP_* 값은 의도적으로 선언하지 않는다" in blueprint_text
    assert "배포 버튼만 누르는 것으로 재해 복구가 완료되지 않는다" in blueprint_text


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
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")

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
    assert web["environment"]["DEPLOYMENT_RUNTIME_CONTRACT"] == (
        validator.RUNTIME_CONTRACT_LOCAL_WEB
    )
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
    assert pod_spec["enableServiceLinks"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["runAsUser"] == 1000
    container = pod_spec["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert any("secretRef" in item for item in container["envFrom"])
    assert "command" not in container
    assert "args" not in container
    direct_environment = {item["name"]: item["value"] for item in container["env"]}
    assert direct_environment["DEPLOYMENT_RUNTIME_CONTRACT"] == (
        validator.RUNTIME_CONTRACT_KUBERNETES_WEB
    )
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
    assert "DEPLOYMENT_RUNTIME_CONTRACT=local-web-v1" in smoke_script
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


def test_render_blueprint_turns_engine_v2_on_while_image_default_stays_v1() -> None:
    """배포하면 엔진 v2가 켜지는지, 그리고 이미지 기본값은 v1로 남는지 못 박는다.

    ★ 왜 값의 «글자»까지 고정하는가 — 코드는
      ``os.environ.get("ENGINE_V2") == "1"`` 하나로만 갈린다
      (app/src/features/pipeline/real.py). 정확히 문자열 "1"이 아니면
      시작 검증도 통과하고 오류도 없이 «조용히» v1 보고서가 나간다.
      따옴표가 빠져 YAML이 정수 1로 읽히거나 "true"로 바뀌는 순간
      아무도 모르게 v1로 되돌아가므로 자료형까지 함께 단언한다.
    """
    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )
    web_service = next(
        service for service in blueprint["services"] if service["type"] == "web"
    )
    render_values = {item["key"]: item.get("value") for item in web_service["envVars"]}

    assert "ENGINE_V2" in render_values, (
        "render.yaml에 ENGINE_V2가 없으면 배포된 서비스는 v1 보고서를 냅니다"
    )
    assert isinstance(render_values["ENGINE_V2"], str), (
        "따옴표 없는 1은 YAML 정수로 읽힙니다 — value: \"1\"로 적어야 합니다"
    )
    assert render_values["ENGINE_V2"] == "1"

    # render.yaml의 이름·값이 실제 분기 상수와 계속 같은지 함께 묶는다.
    # 코드에서 이름이 바뀌면 blueprint에 죽은 키만 남고 배포는 조용히 v1이 된다.
    switch_source = (
        REPOSITORY_ROOT / "app" / "src" / "features" / "pipeline" / "real.py"
    ).read_text(encoding="utf-8")
    assert 'ENGINE_V2_ENV_NAME: Final[str] = "ENGINE_V2"' in switch_source
    assert 'ENGINE_V2_ENV_ON: Final[str] = "1"' in switch_source

    # 이미지 기본값은 PIPELINE=demo와 같은 성격이다. Dockerfile은 비용이 들지
    # 않는 안전한 기본만 담고, v2로 갈지는 배포 manifest 한 곳에서만 정한다.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "PIPELINE=demo" in dockerfile
    assert "ENGINE_V2" not in dockerfile

    # 배포 설명도 실제 전용 캐시 계약을 말해야 한다. 과거의 「캐시 없음」 문구를
    # 제품 약속으로 고정하면 구현을 고친 뒤에도 문서가 거짓말하게 된다.
    render_guide = (
        REPOSITORY_ROOT / "app" / "docs" / "Render_배포.md"
    ).read_text(encoding="utf-8")
    assert "v2 전용 1층 캐시" in render_guide
    assert "실제 DART 접수번호" in render_guide
    assert "정규화한 재무 응답 지문" in render_guide
    assert "캐시 적중으로 본조사 비용을 다시 쓰지 않는다" in render_guide
    assert "v2는 1층 캐시를 쓰지 않으므로" not in render_guide
    assert "지금 배포하면 v1 보고서가 나간다" not in render_guide


#: Render 가 디스크 서비스에 주는 종료 유예 기본값(초).
#: Blueprint 로 늘릴 수 없다 — 늘리려 하면 동기화가 거부된다(2026-08-29 실측).
RENDER_DEFAULT_SHUTDOWN_SECONDS = 30


def test_render_shutdown_window_covers_serial_uvicorn_and_app_shutdown() -> None:
    """Uvicorn 요청 정리와 lifespan 정리는 직렬이므로 합계가 플랫폼보다 짧다."""

    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )
    web_service = next(
        service for service in blueprint["services"] if service["type"] == "web"
    )
    render_values = {item["key"]: item.get("value") for item in web_service["envVars"]}

    # ★ 2026-08-29 실측 — Render 는 «디스크가 붙은 서비스»에 이 값을 허용하지 않는다.
    #   Blueprint 동기화가 이 오류로 거부됐다:
    #     services[0].maxShutdownDelaySeconds
    #     max shutdown delay is not supported for services with a disk
    #   그래서 render.yaml 에 «있으면 안 된다». 다시 넣으면 배포가 통째로 막힌다.
    assert "maxShutdownDelaySeconds" not in web_service, (
        "★ Render 가 디스크 서비스에는 이 값을 거부한다 — 넣으면 배포가 막힌다"
    )
    platform_seconds = RENDER_DEFAULT_SHUTDOWN_SECONDS
    uvicorn_seconds = int(render_values["GRACEFUL_SHUTDOWN_SECONDS"])
    runtime_source = (
        REPOSITORY_ROOT / "app" / "src" / "web" / "job_runtime.py"
    ).read_text(encoding="utf-8")

    assert render_values["GRACEFUL_SHUTDOWN_SECONDS"] == "20"
    assert "_JOB_DRAIN_TIMEOUT_SEC = 240.0" in runtime_source
    assert "_JOB_CANCEL_GRACE_SEC = 1.0" in runtime_source
    # ⚠️ 앱이 기대하는 시간이 플랫폼이 주는 시간보다 «크다». 이건 지금 사실이다.
    #   Uvicorn 0.52.3 은 HTTP task 를 먼저 기다린 뒤에야 lifespan.shutdown 을
    #   부르므로 두 시간은 겹치지 않고 더해진다.
    #   → 배포·재시작 중이던 조사는 정리를 끝내기 전에 잘릴 수 있다.
    #   이 시험은 그 «어긋남을 숨기지 않기 위해» 남긴다. 숫자를 맞춰 통과시키지 마라.
    앱_기대 = uvicorn_seconds + 240 + 1
    assert 앱_기대 > platform_seconds, (
        "이 어긋남이 해소됐다면 render.yaml·런북·이 시험을 «같이» 고쳐라"
    )

    runbook = (REPOSITORY_ROOT / "ops" / "배포_운영_런북.md").read_text(
        encoding="utf-8"
    )
    assert "Render 기본 30초" in runbook
    assert "Uvicorn HTTP 요청 정리 최대 20초" in runbook
    normalized_runbook = " ".join(runbook.split())
    assert "Blueprint의 Manual Sync / Deploy Blueprint" in normalized_runbook

    render_guide = (
        REPOSITORY_ROOT / "app" / "docs" / "Render_배포.md"
    ).read_text(encoding="utf-8")
    normalized_render_guide = " ".join(render_guide.split())
    assert (
        "Blueprint에서 **Manual Sync / Deploy Blueprint**"
        in normalized_render_guide
    )

    historical_directive = (
        REPOSITORY_ROOT / "app" / "docs" / "출시전_수정_지시서.md"
    ).read_text(encoding="utf-8")
    # ★ 2026-08-28 의 「정정」은 «틀렸다». Render 가 2026-08-29 에 실측으로 뒤집었다.
    assert "종료 계약 재정정(2026-08-29)" in historical_directive


def test_render_reserves_only_half_the_persistent_disk_for_immutable_pdf_artifacts() -> None:
    """1GB 공용 디스크를 PDF가 끝까지 채우지 못하게 배포값을 고정한다."""

    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )
    web_service = next(
        service for service in blueprint["services"] if service["type"] == "web"
    )
    render_values = {item["key"]: item.get("value") for item in web_service["envVars"]}

    assert web_service["disk"]["sizeGB"] == 1
    assert render_values["REPORT_ARTIFACT_CAPACITY_BYTES"] == "536870912"
    assert isinstance(render_values["REPORT_ARTIFACT_CAPACITY_BYTES"], str)

    adapter_source = (
        REPOSITORY_ROOT / "app" / "src" / "web" / "report_delivery_adapter.py"
    ).read_text(encoding="utf-8")
    assert (
        '_ARTIFACT_CAPACITY_ENV: Final[str] = "REPORT_ARTIFACT_CAPACITY_BYTES"'
        in adapter_source
    )

    render_guide = (
        REPOSITORY_ROOT / "app" / "docs" / "Render_배포.md"
    ).read_text(encoding="utf-8")
    normalized_guide = " ".join(render_guide.split())
    assert "자동으로 과거 원본을 지우지 않는다" in normalized_guide
    assert "새 보고서 출고를 닫는다" in normalized_guide
    assert (
        "최초 승인 PDF 원본의 외부 백업은 아직 확인하지 못했다"
        in normalized_guide
    )


def test_engine_v2_rejects_values_that_silently_fall_back_to_v1() -> None:
    """★ «조용히 v1로 되돌아가는 것»을 시작 검증이 막는다.

    코드는 값이 «정확히 "1"»일 때만 v2로 간다. 그래서 true·yes·on·" 1 "처럼
    사람 눈에는 켜진 것처럼 보이는 값이 오류 없이 v1 보고서를 내보낸다.
    이 프로젝트에서 「고쳤는데 화면에 안 나온다」가 네 번 반복된 원인이
    정확히 이런 «조용한 되돌아감»이었다.

    ★ 값을 «안 넣는 것»은 정상이다(= v1). 넣었는데 못 알아듣는 값일 때만 막는다.
    """
    for value in ("true", "yes", "on", " 1 ", "V2", "2", ""):
        environment = _base_environment()
        environment["ENGINE_V2"] = value
        errors = validator.validate(environment, "web")
        assert any("ENGINE_V2" in error for error in errors), (
            f"ENGINE_V2={value!r}가 오류 없이 통과했습니다 — 배포는 조용히 v1이 됩니다"
        )

    for value in ("1", "0"):
        environment = _base_environment()
        environment["ENGINE_V2"] = value
        errors = validator.validate(environment, "web")
        assert not any("ENGINE_V2" in error for error in errors), value

    # 아예 없는 것은 오류가 아니다 — v1 경로를 쓰겠다는 정상적인 선택이다.
    errors = validator.validate(_base_environment(), "web")
    assert not any("ENGINE_V2" in error for error in errors)


def test_engine_v2_error_never_leaks_the_value() -> None:
    """오류 문구에 설정값 자체가 들어가면 안 된다 (이 모듈 전체의 계약)."""
    environment = _base_environment()
    environment["ENGINE_V2"] = "비밀처럼보이는값"

    errors = validator.validate(environment, "web")

    assert all("비밀처럼보이는값" not in error for error in errors)

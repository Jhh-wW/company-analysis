"""공개 reverse proxy 신뢰와 canary 증거의 fail-closed 계약."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPOSITORY_ROOT / "deploy" / "validate_environment.py"
SPEC = importlib.util.spec_from_file_location("deploy_forwarded_validator", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

EVIDENCE = "a" * 64


def _base() -> dict[str, str]:
    return {
        "PIPELINE": "demo",
        "BETA_ADMIN_ONLY": "0",
        "PORT": "10000",
        "LOG_LEVEL": "info",
        "GRACEFUL_SHUTDOWN_SECONDS": "300",
        "APP_DATA_ROOT": "/var/data",
        "STORAGE_DB_PATH": "/var/data/storage.db",
        "OBSERVABILITY_RECORDS_PATH": "/var/data/observability/runs.jsonl",
        "TLDEXTRACT_CACHE": "/var/data/cache/tldextract",
        "DEPLOYMENT_EXPOSURE": "local",
        "DEPLOYMENT_PLATFORM": "local",
        "DEPLOYMENT_RUNTIME_CONTRACT": validator.RUNTIME_CONTRACT_LOCAL_WEB,
        "FORWARDED_ALLOW_IPS": "127.0.0.1",
    }


def _render_public() -> dict[str, str]:
    environment = _base()
    environment.update(
        {
            "DEPLOYMENT_EXPOSURE": "public",
            "DEPLOYMENT_PLATFORM": "render",
            "DEPLOYMENT_RUNTIME_CONTRACT": validator.RUNTIME_CONTRACT_RENDER_WEB,
            "PUBLIC_ORIGIN": "https://company.example",
            "FORWARDED_ALLOW_IPS": "1.1.1.1/32",
            "HTTPS_ORIGIN_CSRF_CANARY_EVIDENCE_SHA256": EVIDENCE,
            "CLIENT_IP_CANARY_EVIDENCE_SHA256": EVIDENCE,
            "RENDER_DIRECT_ORIGIN_BLOCK_EVIDENCE_SHA256": EVIDENCE,
            "RENDER_EDGE_XFF_SANITIZE_EVIDENCE_SHA256": EVIDENCE,
        }
    )
    return environment


def _render_admin_demo() -> dict[str, str]:
    environment = _base()
    environment.update(
        {
            "BETA_ADMIN_ONLY": "1",
            "DEPLOYMENT_EXPOSURE": "public",
            "DEPLOYMENT_PLATFORM": "render",
            "DEPLOYMENT_RUNTIME_CONTRACT": (
                validator.RUNTIME_CONTRACT_RENDER_ADMIN_DEMO
            ),
            "PUBLIC_ORIGIN": "https://company.example",
            "RENDER_EXTERNAL_URL": "https://company.example",
            "FORWARDED_ALLOW_IPS": "",
            "ADMIN_EMAILS": "admin@example.com",
            "GOOGLE_CLIENT_ID": "google-client-id",
            "GOOGLE_CLIENT_SECRET": "google-client-secret",
            "GOOGLE_REDIRECT_URI": "https://company.example/auth/callback",
        }
    )
    return environment


@pytest.mark.parametrize(
    ("kubernetes_environment", "service_account_marker"),
    (
        ({"KUBERNETES_SERVICE_HOST": "10.96.0.1"}, False),
        ({}, True),
    ),
)
def test_Render_web의_내부_Kubernetes_substrate는_교차플랫폼으로_오판하지_않는다(
    kubernetes_environment: dict[str, str], service_account_marker: bool
) -> None:
    environment = _render_admin_demo()
    environment.update(
        {
            "RENDER": "true",
            "RENDER_SERVICE_TYPE": "web",
            **kubernetes_environment,
        }
    )

    assert validator.validate(
        environment,
        "web",
        kubernetes_service_account_marker=service_account_marker,
    ) == []


def test_공식_Render_runtime_marker가_없으면_교차플랫폼_모호성을_계속_거부한다() -> None:
    environment = _render_admin_demo()
    environment.update(
        {
            "RENDER_SERVICE_TYPE": "web",
            "KUBERNETES_SERVICE_HOST": "10.96.0.1",
        }
    )

    joined = "\n".join(
        validator.validate(
            environment,
            "web",
            kubernetes_service_account_marker=False,
        )
    )

    assert "PLATFORM_MARKERS: Render와 Kubernetes marker가 동시에 감지됐습니다" in joined


def test_Kubernetes_contract와_Render_marker가_충돌하면_계속_거부한다() -> None:
    environment = _kubernetes_public()
    environment.update(
        {
            "RENDER": "true",
            "RENDER_SERVICE_TYPE": "web",
            "RENDER_EXTERNAL_URL": "https://company.example",
        }
    )

    joined = "\n".join(
        validator.validate(
            environment,
            "web",
            kubernetes_service_account_marker=True,
        )
    )

    assert "PLATFORM_MARKERS: Render와 Kubernetes marker가 동시에 감지됐습니다" in joined


def _kubernetes_public() -> dict[str, str]:
    environment = _base()
    environment.update(
        {
            "DEPLOYMENT_EXPOSURE": "public",
            "DEPLOYMENT_PLATFORM": "kubernetes",
            "DEPLOYMENT_RUNTIME_CONTRACT": (
                validator.RUNTIME_CONTRACT_KUBERNETES_WEB
            ),
            "PUBLIC_ORIGIN": "https://company.example",
            "FORWARDED_ALLOW_IPS": "10.42.7.0/24",
            "K8S_INGRESS_PROXY_CIDRS": "10.42.7.0/24",
            "HTTPS_ORIGIN_CSRF_CANARY_EVIDENCE_SHA256": EVIDENCE,
            "CLIENT_IP_CANARY_EVIDENCE_SHA256": EVIDENCE,
            "K8S_NETWORK_POLICY_EVIDENCE_SHA256": EVIDENCE,
        }
    )
    return environment


def test_임의hash와_IP를_모두_채운_render도_독립검증부재로_BLOCKED다() -> None:
    joined = "\n".join(validator.validate(_render_public(), "web"))
    assert validator.RENDER_FORWARDED_TRUST_BLOCKER in joined
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined

    missing = _render_public()
    for name in validator.COMMON_PUBLIC_EVIDENCE + validator.RENDER_PUBLIC_EVIDENCE:
        missing.pop(name)
    joined = "\n".join(validator.validate(missing, "web"))
    for name in validator.COMMON_PUBLIC_EVIDENCE + validator.RENDER_PUBLIC_EVIDENCE:
        assert name in joined


@pytest.mark.parametrize(
    "value",
    ("127.0.0.1", "*", "not-a-cidr", "0.0.0.0/0", "10.0.0.0/8"),
)
def test_public은_127기본_모든주소_오형식_광범위CIDR을_거부한다(value: str) -> None:
    environment = _render_public()
    environment["FORWARDED_ALLOW_IPS"] = value
    assert any(
        error.startswith("FORWARDED_ALLOW_IPS:")
        for error in validator.validate(environment, "web")
    )


def test_kubernetes는_ingress_CIDR일치와_NetworkPolicy증거를_요구한다() -> None:
    joined = "\n".join(validator.validate(_kubernetes_public(), "web"))
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined

    mismatch = _kubernetes_public()
    mismatch["K8S_INGRESS_PROXY_CIDRS"] = "10.42.8.0/24"
    assert "정확히 같아야" in "\n".join(validator.validate(mismatch, "web"))

    missing = _kubernetes_public()
    missing.pop("K8S_NETWORK_POLICY_EVIDENCE_SHA256")
    assert "K8S_NETWORK_POLICY_EVIDENCE_SHA256" in "\n".join(
        validator.validate(missing, "web")
    )


def test_render는_outbound로_추정한_범위를_넣어도_unblock되지_않는다() -> None:
    environment = _render_public()
    environment["FORWARDED_ALLOW_IPS"] = "1.1.1.0/24"
    joined = "\n".join(validator.validate(environment, "web"))
    assert validator.RENDER_FORWARDED_TRUST_BLOCKER in joined
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined


def test_Render_관리자_demo는_forwarded를_비신뢰할_때만_통과한다() -> None:
    environment = _render_admin_demo()

    assert validator.validate(environment, "web") == []
    scope, errors = validator.validate_command(
        environment,
        [
            "sh",
            "-c",
            "exec python -m uvicorn src.web.main:app --host 0.0.0.0 "
            "--port \"${PORT:-10000}\" --workers 1 --no-proxy-headers "
            "--limit-concurrency 20 --backlog 32 --timeout-keep-alive 5 "
            "--timeout-graceful-shutdown "
            "\"${GRACEFUL_SHUTDOWN_SECONDS:-300}\" --log-level "
            "\"${LOG_LEVEL:-info}\"",
        ],
    )

    assert scope == "web"
    assert errors == []


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    (
        ("PIPELINE", "real", "PIPELINE"),
        ("BETA_ADMIN_ONLY", "0", "BETA_ADMIN_ONLY"),
        ("FORWARDED_ALLOW_IPS", "1.1.1.1/32", "FORWARDED_ALLOW_IPS"),
        ("PUBLIC_ORIGIN", "http://company.example", "PUBLIC_ORIGIN"),
        (
            "GOOGLE_REDIRECT_URI",
            "https://company.example/wrong",
            "GOOGLE_REDIRECT_URI",
        ),
    ),
)
def test_Render_관리자_demo의_범위를_넓히는_설정은_거부한다(
    name: str, value: str, expected: str
) -> None:
    environment = _render_admin_demo()
    environment[name] = value

    assert expected in "\n".join(validator.validate(environment, "web"))


def test_Render_관리자_demo는_Render기본URL과_다른_origin을_거부한다() -> None:
    environment = _render_admin_demo()
    environment["PUBLIC_ORIGIN"] = "https://other.example"
    environment["GOOGLE_REDIRECT_URI"] = "https://other.example/auth/callback"

    assert "Render 기본 외부 URL" in "\n".join(
        validator.validate(environment, "web")
    )


@pytest.mark.parametrize(
    "command",
    (
        ["python", "-m", "uvicorn", "src.web.main:app", "--proxy-headers"],
        ["python", "-m", "uvicorn", "src.web.main:app"],
        ["python", "-m", "custom.web"],
    ),
)
def test_Render_관리자_demo는_실행명령으로_proxy신뢰를_우회할수없다(
    command: list[str],
) -> None:
    _, errors = validator.validate_command(_render_admin_demo(), command)

    assert any(error.startswith("DEPLOYMENT_COMMAND:") for error in errors)


def test_public은_경로없는_HTTPS_origin과_canary를_요구한다() -> None:
    environment = _render_public()
    environment["PUBLIC_ORIGIN"] = "http://company.example/admin"
    environment["CLIENT_IP_CANARY_EVIDENCE_SHA256"] = "true"
    joined = "\n".join(validator.validate(environment, "web"))
    assert "PUBLIC_ORIGIN" in joined
    assert "CLIENT_IP_CANARY_EVIDENCE_SHA256" in joined


def test_render_blueprint는_관리자_demo_한서비스만_좁게_허용한다() -> None:
    blueprint = yaml.safe_load((REPOSITORY_ROOT / "render.yaml").read_text("utf-8"))
    assert len(blueprint["services"]) == 1
    web = next(service for service in blueprint["services"] if service["type"] == "web")
    values = {item["key"]: item for item in web["envVars"]}

    assert web["plan"] == "free"
    assert "disk" not in web
    assert values["DEPLOYMENT_EXPOSURE"]["value"] == "public"
    assert values["DEPLOYMENT_PLATFORM"]["value"] == "render"
    assert values["DEPLOYMENT_RUNTIME_CONTRACT"]["value"] == (
        validator.RUNTIME_CONTRACT_RENDER_ADMIN_DEMO
    )
    assert values["PIPELINE"]["value"] == "demo"
    assert values["BETA_ADMIN_ONLY"]["value"] == "1"
    assert values["PUBLIC_ORIGIN"] == {
        "key": "PUBLIC_ORIGIN",
        "fromService": {
            "type": "web",
            "name": "company-analysis-beta",
            "envVarKey": "RENDER_EXTERNAL_URL",
        },
    }
    assert values["FORWARDED_ALLOW_IPS"] == {
        "key": "FORWARDED_ALLOW_IPS",
        "value": "",
    }
    for name in validator.COMMON_PUBLIC_EVIDENCE + validator.RENDER_PUBLIC_EVIDENCE:
        assert name not in values
    assert not any(service["type"] == "cron" for service in blueprint["services"])


@pytest.mark.parametrize(
    "markers",
    (
        {"RENDER_SERVICE_TYPE": "web"},
        {"RENDER_EXTERNAL_URL": "https://company.example"},
        {"RENDER_EXTERNAL_HOSTNAME": "company.example"},
        {"RENDER_HOSTNAME": "company.example"},
        {
            "RENDER": "true",
            "RENDER_SERVICE_TYPE": "web",
            "RENDER_EXTERNAL_URL": "https://company.example",
        },
    ),
)
def test_Render_불변marker는_local자기선언을_public_render로_강제한다(
    markers: dict[str, str],
) -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment.update(markers)
    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "tools.trigger_backup"],
        kubernetes_service_account_marker=False,
    )
    joined = "\n".join(errors)
    assert scope == "web"
    assert "Render web marker는 public을 강제" in joined
    assert "Render marker는 render를 강제" in joined
    assert validator.RENDER_FORWARDED_TRUST_BLOCKER in joined


@pytest.mark.parametrize(
    ("service_type", "module"),
    (
        ("cron", "tools.trigger_backup"),
        ("cron_job", "tools.trigger_backup"),
        ("background_worker", "tools.trigger_maintenance"),
    ),
)
def test_RENDER_true_단독은_trigger를_web으로_오판하지_않는다(
    service_type: str, module: str
) -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment.update({"RENDER": "true", "RENDER_SERVICE_TYPE": service_type})

    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", module],
        kubernetes_service_account_marker=False,
    )

    assert scope == (
        "backup-trigger" if module == "tools.trigger_backup" else "maintenance-trigger"
    )
    assert validator.RENDER_FORWARDED_TRUST_BLOCKER not in errors
    assert any("TRIGGER_" in error for error in errors)


@pytest.mark.parametrize(
    "command",
    (
        ["sh", "-c", "python -m tools.trigger_backup"],
        ["/bin/sh", "-c", "exec python -m tools.trigger_backup"],
        ["python", "-m", "tools.trigger_maintenance", "weekly"],
    ),
)
def test_Render_shell형식도_정확한_trigger_한_문장만_허용한다(
    command: list[str],
) -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment.update({"RENDER": "true", "RENDER_SERVICE_TYPE": "cron"})

    scope, errors = validator.validate_command(
        environment,
        command,
        kubernetes_service_account_marker=False,
    )

    assert scope in {"backup-trigger", "maintenance-trigger"}
    assert validator.GENERIC_COMMAND_BLOCKER not in errors


def test_trigger뒤에_다른_shell명령을_붙이면_generic으로_BLOCKED다() -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment.update({"RENDER": "true", "RENDER_SERVICE_TYPE": "cron"})

    scope, errors = validator.validate_command(
        environment,
        ["sh", "-c", "python -m tools.trigger_backup; python -m custom.web"],
        kubernetes_service_account_marker=False,
    )

    assert scope == "generic"
    assert errors == [validator.GENERIC_COMMAND_BLOCKER]


def test_RENDER_true_단독_generic은_web_오판없이_BLOCKED다() -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment["RENDER"] = "true"

    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "custom.worker"],
        kubernetes_service_account_marker=False,
    )

    assert scope == "generic"
    assert errors == [validator.GENERIC_COMMAND_BLOCKER]
    assert validator.validate(
        environment,
        "generic",
        kubernetes_service_account_marker=False,
    )[-1] == validator.GENERIC_COMMAND_BLOCKER


@pytest.mark.parametrize(
    "markers",
    (
        {"KUBERNETES_SERVICE_HOST": "10.96.0.1"},
        {"KUBERNETES_SERVICE_PORT": "443"},
        {"KUBERNETES_PORT": "tcp://10.96.0.1:443"},
        {
            "KUBERNETES_SERVICE_HOST": "10.96.0.1",
            "KUBERNETES_SERVICE_PORT": "443",
        },
    ),
)
def test_Kubernetes_marker는_local자기선언을_public_kubernetes로_강제한다(
    markers: dict[str, str],
) -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment.update(markers)
    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "tools.trigger_backup"],
        kubernetes_service_account_marker=False,
    )
    joined = "\n".join(errors)
    assert scope == "web"
    assert "Kubernetes marker는 public을 강제" in joined
    assert "Kubernetes marker는 kubernetes를 강제" in joined
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined


def test_Kubernetes_service_account_marker도_local우회를_거부한다() -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "tools.trigger_backup"],
        kubernetes_service_account_marker=True,
    )
    joined = "\n".join(errors)
    assert scope == "web"
    assert "Kubernetes marker는 public을 강제" in joined
    assert "Kubernetes marker는 kubernetes를 강제" in joined


def test_Kubernetes_manifest_contract는_marker를_모두_shadow해도_web을_강제한다() -> None:
    environment = _base()
    environment["DEPLOYMENT_RUNTIME_CONTRACT"] = (
        validator.RUNTIME_CONTRACT_KUBERNETES_WEB
    )
    environment.update(
        {
            "KUBERNETES_SERVICE_HOST": "",
            "KUBERNETES_SERVICE_PORT": "",
            "KUBERNETES_PORT": "",
        }
    )

    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "custom.web_wrapper"],
        kubernetes_service_account_marker=False,
    )
    joined = "\n".join(errors)

    assert scope == "web"
    assert "Kubernetes marker는 public을 강제" in joined
    assert "Kubernetes marker는 kubernetes를 강제" in joined
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined


def test_Kubernetes_contract와_marker가_모두_없으면_generic_readiness를_거부한다() -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment.update(
        {
            "KUBERNETES_SERVICE_HOST": "",
            "KUBERNETES_SERVICE_PORT": "",
            "KUBERNETES_PORT": "",
        }
    )

    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "custom.web_wrapper"],
        kubernetes_service_account_marker=False,
    )
    assert scope == "generic"
    assert errors == [validator.GENERIC_COMMAND_BLOCKER]

    scope, errors = validator.validate_command(
        environment,
        ["python", "-m", "uvicorn", "src.web.main:app"],
        kubernetes_service_account_marker=False,
    )
    assert scope == "web"
    assert validator.RUNTIME_CONTRACT_REQUIRED_BLOCKER in errors


def test_direct_validate도_Render_web_marker의_scope_하향을_거부한다() -> None:
    environment = _base()
    environment.pop("DEPLOYMENT_RUNTIME_CONTRACT")
    environment["RENDER_EXTERNAL_URL"] = "https://company.example"

    for scope in ("generic", "backup-trigger"):
        joined = "\n".join(
            validator.validate(
                environment,
                scope,
                kubernetes_service_account_marker=False,
            )
        )

        assert "Render web marker는 public을 강제" in joined
        assert "Render marker는 render를 강제" in joined
        assert validator.RENDER_FORWARDED_TRUST_BLOCKER in joined


def test_direct_validate도_Kubernetes_contract의_scope_하향을_거부한다() -> None:
    environment = _base()
    environment["DEPLOYMENT_RUNTIME_CONTRACT"] = (
        validator.RUNTIME_CONTRACT_KUBERNETES_WEB
    )
    environment.update(
        {
            "KUBERNETES_SERVICE_HOST": "",
            "KUBERNETES_SERVICE_PORT": "",
            "KUBERNETES_PORT": "",
        }
    )

    for scope in ("generic", "maintenance-trigger"):
        joined = "\n".join(
            validator.validate(
                environment,
                scope,
                runtime_contract="",
                kubernetes_service_account_marker=False,
            )
        )

        assert "Kubernetes marker는 public을 강제" in joined
        assert "Kubernetes marker는 kubernetes를 강제" in joined
        assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined


def test_marker없는_정상_local과_Compose_loopback_bind만_통과한다() -> None:
    assert validator.validate(
        _base(), "web", kubernetes_service_account_marker=False
    ) == []
    compose = yaml.safe_load(
        (REPOSITORY_ROOT / "deploy" / "compose.yaml").read_text("utf-8")
    )
    assert compose["services"]["web"]["ports"] == [
        "127.0.0.1:${HOST_PORT:-10000}:10000"
    ]


def test_entrypoint는_shell_부분문자열이_아닌_validator로_scope를_결정한다() -> None:
    entrypoint = (
        REPOSITORY_ROOT / "deploy" / "container_entrypoint.sh"
    ).read_text("utf-8")

    assert "--from-command" in entrypoint
    assert "--runtime-contract" in entrypoint
    assert 'exec "$@"' in entrypoint
    assert 'case " $* "' not in entrypoint

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
        "FORWARDED_ALLOW_IPS": "127.0.0.1",
    }


def _render_public() -> dict[str, str]:
    environment = _base()
    environment.update(
        {
            "DEPLOYMENT_EXPOSURE": "public",
            "DEPLOYMENT_PLATFORM": "render",
            "PUBLIC_ORIGIN": "https://company.example",
            "FORWARDED_ALLOW_IPS": "1.1.1.1/32",
            "HTTPS_ORIGIN_CSRF_CANARY_EVIDENCE_SHA256": EVIDENCE,
            "CLIENT_IP_CANARY_EVIDENCE_SHA256": EVIDENCE,
            "RENDER_DIRECT_ORIGIN_BLOCK_EVIDENCE_SHA256": EVIDENCE,
            "RENDER_EDGE_XFF_SANITIZE_EVIDENCE_SHA256": EVIDENCE,
        }
    )
    return environment


def _kubernetes_public() -> dict[str, str]:
    environment = _base()
    environment.update(
        {
            "DEPLOYMENT_EXPOSURE": "public",
            "DEPLOYMENT_PLATFORM": "kubernetes",
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


def test_public은_경로없는_HTTPS_origin과_canary를_요구한다() -> None:
    environment = _render_public()
    environment["PUBLIC_ORIGIN"] = "http://company.example/admin"
    environment["CLIENT_IP_CANARY_EVIDENCE_SHA256"] = "true"
    joined = "\n".join(validator.validate(environment, "web"))
    assert "PUBLIC_ORIGIN" in joined
    assert "CLIENT_IP_CANARY_EVIDENCE_SHA256" in joined


def test_render_blueprint는_public진단입력을_받아도_항상_BLOCKED다() -> None:
    blueprint = yaml.safe_load((REPOSITORY_ROOT / "render.yaml").read_text("utf-8"))
    web = next(service for service in blueprint["services"] if service["type"] == "web")
    values = {item["key"]: item for item in web["envVars"]}

    assert values["DEPLOYMENT_EXPOSURE"]["value"] == "public"
    assert values["DEPLOYMENT_PLATFORM"]["value"] == "render"
    assert values["FORWARDED_ALLOW_IPS"] == {"key": "FORWARDED_ALLOW_IPS", "sync": False}
    for name in validator.COMMON_PUBLIC_EVIDENCE + validator.RENDER_PUBLIC_EVIDENCE:
        assert values[name] == {"key": name, "sync": False}


@pytest.mark.parametrize(
    "markers",
    (
        {"RENDER": "true"},
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
    environment.update(markers)
    joined = "\n".join(validator.validate(environment, "web"))
    assert "Render web marker는 public을 강제" in joined
    assert "Render marker는 render를 강제" in joined
    assert validator.RENDER_FORWARDED_TRUST_BLOCKER in joined


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
    environment.update(markers)
    joined = "\n".join(
        validator.validate(
            environment, "web", kubernetes_service_account_marker=False
        )
    )
    assert "Kubernetes marker는 public을 강제" in joined
    assert "Kubernetes marker는 kubernetes를 강제" in joined
    assert validator.PRODUCTION_FORWARDED_EVIDENCE_BLOCKER in joined


def test_Kubernetes_service_account_marker도_local우회를_거부한다() -> None:
    joined = "\n".join(
        validator.validate(
            _base(), "web", kubernetes_service_account_marker=True
        )
    )
    assert "Kubernetes marker는 public을 강제" in joined
    assert "Kubernetes marker는 kubernetes를 강제" in joined


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

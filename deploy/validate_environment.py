"""비밀값을 출력하지 않고 컨테이너 시작 환경의 구조를 검증한다."""

from __future__ import annotations

import argparse
import ipaddress
import os
import posixpath
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


WEB_AUTH_VARIABLES = (
    "ADMIN_EMAILS",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
)
REAL_PIPELINE_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "DART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
)
BACKUP_RUNTIME_VARIABLES = (
    "BACKUP_TRIGGER_SECRET",
    "BACKUP_S3_BUCKET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "BACKUP_DATA_BOUNDARY_ID",
    "BACKUP_DATA_AUTHORITY_ID",
    "BACKUP_MANIFEST_MIN_RETENTION_DAYS",
)
PRODUCTION_BACKUP_MANIFEST_APPENDER_AVAILABLE = False
PRODUCTION_BACKUP_MANIFEST_BLOCKER = (
    "BACKUP_MANIFEST_APPENDER: production-ready 구현과 시작 시 주입이 없어 "
    "외부 백업 배포가 차단됩니다"
)
BOOLEAN_TRUE = frozenset({"1", "true", "yes", "on"})
BOOLEAN_FALSE = frozenset({"0", "false", "no", "off"})
LOG_LEVELS = frozenset({"trace", "debug", "info", "warning", "error", "critical"})
SCOPES = ("web", "backup-trigger", "maintenance-trigger", "generic")
DEPLOYMENT_EXPOSURES = frozenset({"local", "public"})
DEPLOYMENT_PLATFORMS = frozenset({"local", "render", "kubernetes"})
RENDER_GRACEFUL_SHUTDOWN_SECONDS = "20"
EVIDENCE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PROXY_RANGES = 16
MIN_PROXY_PREFIX = {4: 24, 6: 64}
COMMON_PUBLIC_EVIDENCE = (
    "HTTPS_ORIGIN_CSRF_CANARY_EVIDENCE_SHA256",
    "CLIENT_IP_CANARY_EVIDENCE_SHA256",
)
RENDER_PUBLIC_EVIDENCE = (
    "RENDER_DIRECT_ORIGIN_BLOCK_EVIDENCE_SHA256",
    "RENDER_EDGE_XFF_SANITIZE_EVIDENCE_SHA256",
)
KUBERNETES_PUBLIC_EVIDENCE = ("K8S_NETWORK_POLICY_EVIDENCE_SHA256",)
PRODUCTION_FORWARDED_EVIDENCE_VERIFIER_AVAILABLE = False
PRODUCTION_FORWARDED_EVIDENCE_BLOCKER = (
    "FORWARDED_EVIDENCE_VERIFIER: 서명 canary artifact와 고정 policy를 독립 "
    "검증하는 운영 adapter가 없어 public 배포가 BLOCKED입니다"
)
RENDER_FORWARDED_TRUST_BLOCKER = (
    "RENDER_FORWARDED_TRUST: 고정 ingress peer CIDR 계약이 없어 사용자 입력이나 "
    "outbound IP로 forwarded trust를 열 수 없습니다"
)
KUBERNETES_MARKER_VARIABLES = (
    "KUBERNETES_SERVICE_HOST",
    "KUBERNETES_SERVICE_PORT",
    "KUBERNETES_PORT",
)
KUBERNETES_SERVICE_ACCOUNT_MARKERS = (
    Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace"),
    Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
    Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
)
RUNTIME_CONTRACT_LOCAL_WEB = "local-web-v1"
RUNTIME_CONTRACT_RENDER_WEB = "render-public-web-v1"
RUNTIME_CONTRACT_RENDER_ADMIN_DEMO = "render-admin-demo-no-forwarded-v1"
RUNTIME_CONTRACT_RENDER_ADMIN_REAL = "render-admin-real-no-forwarded-v1"
RUNTIME_CONTRACT_KUBERNETES_WEB = "kubernetes-public-web-v1"
RUNTIME_CONTRACTS = frozenset(
    {
        RUNTIME_CONTRACT_LOCAL_WEB,
        RUNTIME_CONTRACT_RENDER_WEB,
        RUNTIME_CONTRACT_RENDER_ADMIN_DEMO,
        RUNTIME_CONTRACT_RENDER_ADMIN_REAL,
        RUNTIME_CONTRACT_KUBERNETES_WEB,
    }
)
RUNTIME_CONTRACT_REQUIRED_BLOCKER = (
    "DEPLOYMENT_RUNTIME_CONTRACT: web readiness에는 manifest/Compose/Render가 직접 "
    "고정한 runtime contract가 필요합니다"
)
GENERIC_COMMAND_BLOCKER = (
    "DEPLOYMENT_SCOPE: 검증되지 않은 generic command는 배포 readiness를 얻을 수 없습니다"
)
TRIGGER_COMMAND_SCOPES = {
    ("python", "-m", "tools.trigger_backup"): "backup-trigger",
    ("python", "-m", "tools.trigger_maintenance"): "maintenance-trigger",
    ("python", "-m", "tools.trigger_maintenance", "weekly"): "maintenance-trigger",
    ("python", "-m", "tools.trigger_maintenance", "cleanup"): "maintenance-trigger",
}
FORBIDDEN_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "2001:db8::/32",
        "fe80::/10",
        "ff00::/8",
    )
)


def _required(environment: Mapping[str, str], names: Sequence[str]) -> list[str]:
    return [f"{name}: 값이 필요합니다" for name in names if not environment.get(name, "").strip()]


def _integer_error(name: str, raw: str, lower: int, upper: int) -> str | None:
    try:
        value = int(raw)
    except ValueError:
        return f"{name}: 정수가 필요합니다"
    if not lower <= value <= upper:
        return f"{name}: 허용 범위를 벗어났습니다"
    return None


def _normalized_absolute_path(raw: str) -> PurePosixPath | None:
    if not raw.startswith("/") or "\x00" in raw:
        return None
    return PurePosixPath(posixpath.normpath(raw))


def _validate_persistence_paths(environment: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    root_raw = environment.get("APP_DATA_ROOT", "").strip()
    root = _normalized_absolute_path(root_raw)
    if root is None or root == PurePosixPath("/"):
        return ["APP_DATA_ROOT: 루트가 아닌 절대 POSIX 경로가 필요합니다"]

    for name in ("STORAGE_DB_PATH", "OBSERVABILITY_RECORDS_PATH", "TLDEXTRACT_CACHE"):
        raw = environment.get(name, "").strip()
        path = _normalized_absolute_path(raw)
        if path is None:
            errors.append(f"{name}: 절대 POSIX 경로가 필요합니다")
            continue
        if path != root and root not in path.parents:
            errors.append(f"{name}: APP_DATA_ROOT 아래에 있어야 합니다")
        if name != "TLDEXTRACT_CACHE" and path == root:
            errors.append(f"{name}: 파일 경로가 필요합니다")
    return errors


def _validate_backup_manifest_configuration(
    environment: Mapping[str, str],
) -> list[str]:
    """외부 백업을 구성한 경우 독립 manifest 계약 없이는 닫힌다."""

    if not environment.get("BACKUP_S3_BUCKET", "").strip():
        return []

    errors = _required(environment, BACKUP_RUNTIME_VARIABLES)
    trigger_secret = environment.get("BACKUP_TRIGGER_SECRET", "")
    if trigger_secret.strip() and len(trigger_secret.encode("utf-8")) < 32:
        errors.append("BACKUP_TRIGGER_SECRET: 최소 길이를 충족하지 않습니다")

    manifest_raw = environment.get("BACKUP_MANIFEST_MIN_RETENTION_DAYS", "").strip()
    backup_raw = environment.get("BACKUP_RETENTION_DAYS", "").strip() or "35"
    manifest_error = None
    if manifest_raw:
        manifest_error = _integer_error(
            "BACKUP_MANIFEST_MIN_RETENTION_DAYS", manifest_raw, 1, 3650
        )
        if manifest_error:
            errors.append(manifest_error)
    backup_error = _integer_error("BACKUP_RETENTION_DAYS", backup_raw, 1, 3650)
    if backup_error:
        errors.append(backup_error)
    if not manifest_error and not backup_error and manifest_raw:
        if int(manifest_raw) < int(backup_raw):
            errors.append(
                "BACKUP_MANIFEST_MIN_RETENTION_DAYS: DB 백업 보존 기간보다 "
                "짧을 수 없습니다"
            )

    if not PRODUCTION_BACKUP_MANIFEST_APPENDER_AVAILABLE:
        errors.append(PRODUCTION_BACKUP_MANIFEST_BLOCKER)
    return errors


def _evidence_errors(environment: Mapping[str, str], names: Sequence[str]) -> list[str]:
    errors: list[str] = []
    for name in names:
        if not EVIDENCE_SHA256_RE.fullmatch(environment.get(name, "").strip()):
            errors.append(f"{name}: 검증된 증거 파일의 lowercase SHA-256이 필요합니다")
    return errors


def _proxy_networks(raw: str, *, label: str) -> tuple[set[str], list[str]]:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        return set(), [f"{label}: 명시적인 proxy IP/CIDR이 필요합니다"]
    if len(values) > MAX_PROXY_RANGES:
        return set(), [f"{label}: proxy 범위 개수가 상한을 넘었습니다"]

    normalized: set[str] = set()
    errors: list[str] = []
    for value in values:
        if value == "*":
            errors.append(f"{label}: 모든 주소 신뢰는 금지됩니다")
            continue
        try:
            if "/" in value:
                network = ipaddress.ip_network(value, strict=True)
            else:
                address = ipaddress.ip_address(value)
                network = ipaddress.ip_network(
                    f"{address}/{address.max_prefixlen}", strict=True
                )
        except ValueError:
            errors.append(f"{label}: IP 또는 CIDR 형식만 허용합니다")
            continue
        if network.prefixlen < MIN_PROXY_PREFIX[network.version]:
            errors.append(f"{label}: 신뢰 CIDR이 지나치게 광범위합니다")
        if any(
            network.version == forbidden.version and network.overlaps(forbidden)
            for forbidden in FORBIDDEN_PROXY_NETWORKS
        ):
            errors.append(f"{label}: loopback·예약·문서용 주소는 신뢰할 수 없습니다")
        normalized.add(str(network))
    return normalized, errors


def _local_proxy_errors(raw: str) -> list[str]:
    values = {value.strip() for value in raw.split(",") if value.strip()}
    if not values or not values <= {"127.0.0.1", "::1"}:
        return ["FORWARDED_ALLOW_IPS: 로컬 배포는 loopback host만 허용합니다"]
    return []


def _public_origin_error(raw: str) -> str | None:
    try:
        parsed = urlsplit(raw.strip())
        port = parsed.port
    except ValueError:
        return "PUBLIC_ORIGIN: 올바른 HTTPS origin이 필요합니다"
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".invalid"))
        or (port is not None and not 1 <= port <= 65535)
    ):
        return "PUBLIC_ORIGIN: 경로 없는 공개 HTTPS origin이 필요합니다"
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return "PUBLIC_ORIGIN: loopback origin은 공개 배포에 쓸 수 없습니다"
    except ValueError:
        pass
    return None


def _normalized_public_origin(raw: str) -> str | None:
    """검증된 공개 origin을 비교용 문자열로 정규화한다."""

    if _public_origin_error(raw):
        return None
    parsed = urlsplit(raw.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"https://{hostname}{port}"


def _render_admin_demo_errors(environment: Mapping[str, str]) -> list[str]:
    """forwarded header를 전혀 믿지 않는 Render 관리자 데모만 좁게 허용한다."""

    errors: list[str] = []
    if environment.get("PIPELINE", "").strip().lower() != "demo":
        errors.append("PIPELINE: Render 관리자 데모는 demo만 허용합니다")
    if environment.get("BETA_ADMIN_ONLY", "").strip().lower() not in BOOLEAN_TRUE:
        errors.append("BETA_ADMIN_ONLY: Render 관리자 데모는 관리자 전용이어야 합니다")
    if environment.get("FORWARDED_ALLOW_IPS", "").strip():
        errors.append(
            "FORWARDED_ALLOW_IPS: Render 관리자 데모는 forwarded header를 "
            "신뢰하지 않아야 합니다"
        )

    public_origin_raw = environment.get("PUBLIC_ORIGIN", "")
    origin_error = _public_origin_error(public_origin_raw)
    if origin_error:
        errors.append(origin_error)
        public_origin = None
    else:
        public_origin = _normalized_public_origin(public_origin_raw)

    render_origin_raw = environment.get("RENDER_EXTERNAL_URL", "").strip()
    if render_origin_raw and public_origin:
        render_origin = _normalized_public_origin(render_origin_raw)
        if render_origin is None or render_origin != public_origin:
            errors.append(
                "PUBLIC_ORIGIN: Render 기본 외부 URL과 정확히 같아야 합니다"
            )

    redirect_raw = environment.get("GOOGLE_REDIRECT_URI", "").strip()
    if redirect_raw and public_origin:
        if redirect_raw != f"{public_origin}/auth/callback":
            errors.append(
                "GOOGLE_REDIRECT_URI: PUBLIC_ORIGIN의 /auth/callback과 "
                "정확히 같아야 합니다"
            )
    return errors


def _render_admin_real_errors(environment: Mapping[str, str]) -> list[str]:
    """forwarded header를 믿지 않는 Render 관리자 실제 분석판만 허용한다."""

    errors: list[str] = []
    if environment.get("PIPELINE", "").strip().lower() != "real":
        errors.append("PIPELINE: Render 관리자 실제 분석판은 real만 허용합니다")
    if environment.get("BETA_ADMIN_ONLY", "").strip().lower() not in BOOLEAN_TRUE:
        errors.append(
            "BETA_ADMIN_ONLY: Render 관리자 실제 분석판은 관리자 전용이어야 합니다"
        )
    if environment.get("FORWARDED_ALLOW_IPS", "").strip():
        errors.append(
            "FORWARDED_ALLOW_IPS: Render 관리자 실제 분석판은 forwarded header를 "
            "신뢰하지 않아야 합니다"
        )

    public_origin_raw = environment.get("PUBLIC_ORIGIN", "")
    origin_error = _public_origin_error(public_origin_raw)
    if origin_error:
        errors.append(origin_error)
        public_origin = None
    else:
        public_origin = _normalized_public_origin(public_origin_raw)

    render_origin_raw = environment.get("RENDER_EXTERNAL_URL", "").strip()
    if render_origin_raw and public_origin:
        render_origin = _normalized_public_origin(render_origin_raw)
        if render_origin is None or render_origin != public_origin:
            errors.append(
                "PUBLIC_ORIGIN: Render 기본 외부 URL과 정확히 같아야 합니다"
            )

    redirect_raw = environment.get("GOOGLE_REDIRECT_URI", "").strip()
    if redirect_raw and public_origin:
        if redirect_raw != f"{public_origin}/auth/callback":
            errors.append(
                "GOOGLE_REDIRECT_URI: PUBLIC_ORIGIN의 /auth/callback과 "
                "정확히 같아야 합니다"
            )
    return errors


def _render_admin_no_forwarded_command_errors(command: Sequence[str]) -> list[str]:
    """관리자 no-forwarded 계약의 실행 명령 우회를 차단한다."""

    words = _normalized_command_words(command)
    expected = (
        "python",
        "-m",
        "uvicorn",
        "src.web.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "${PORT:-10000}",
        "--workers",
        "1",
        "--no-proxy-headers",
        "--limit-concurrency",
        "20",
        "--backlog",
        "32",
        "--timeout-keep-alive",
        "5",
        "--timeout-graceful-shutdown",
        "${GRACEFUL_SHUTDOWN_SECONDS:-20}",
        "--log-level",
        "${LOG_LEVEL:-info}",
    )
    if words != expected:
        return [
            "DEPLOYMENT_COMMAND: Render 관리자 no-forwarded 계약은 proxy 비신뢰가 고정된 "
            "기본 web 실행 명령만 허용합니다"
        ]
    return []


def _default_kubernetes_service_account_marker() -> bool:
    """내용을 읽지 않고 Kubernetes projected marker 존재만 확인한다."""

    for path in KUBERNETES_SERVICE_ACCOUNT_MARKERS:
        try:
            if path.exists():
                return True
        except OSError:
            continue
    return False


def _render_web_marker(environment: Mapping[str, str]) -> bool:
    """모든 runtime 공통 RENDER=true가 아니라 공개 web 전용 신호만 본다."""

    return environment.get("RENDER_SERVICE_TYPE", "").strip().lower() == "web" or any(
        environment.get(name, "").strip()
        for name in (
            "RENDER_EXTERNAL_URL",
            "RENDER_EXTERNAL_HOSTNAME",
            "RENDER_HOSTNAME",
        )
    )


def _kubernetes_cluster_marker(
    environment: Mapping[str, str], *, service_account_marker: bool
) -> bool:
    return service_account_marker or any(
        environment.get(name, "").strip() for name in KUBERNETES_MARKER_VARIABLES
    )


def _runtime_contract(environment: Mapping[str, str], explicit: str | None) -> str:
    # manifest가 직접 주입한 환경 계약이 호출자 인자보다 우선한다. 빈 CLI 인자로
    # Kubernetes direct env를 shadow해 scope를 낮출 수 없어야 한다.
    environment_raw = environment.get("DEPLOYMENT_RUNTIME_CONTRACT", "").strip()
    raw = environment_raw or ("" if explicit is None else explicit)
    return raw.strip().lower()


def _normalized_command_words(command: Sequence[str]) -> tuple[str, ...]:
    """Render가 shell form CMD로 넘겨도 허용한 trigger 한 문장만 해석한다."""

    tokens = tuple(str(value) for value in command)
    if (
        len(tokens) == 3
        and PurePosixPath(tokens[0]).name in {"sh", "bash"}
        and tokens[1] == "-c"
    ):
        try:
            words = tuple(shlex.split(tokens[2], posix=True))
        except ValueError:
            return tokens
        if words[:1] == ("exec",):
            words = words[1:]
        return words
    return tokens


def resolve_scope(
    environment: Mapping[str, str],
    command: Sequence[str],
    *,
    runtime_contract: str | None = None,
    kubernetes_service_account_marker: bool | None = None,
) -> str:
    """사용자 command 문자열보다 배포 contract·플랫폼 marker를 먼저 신뢰한다."""

    contract = _runtime_contract(environment, runtime_contract)
    if contract:
        # 알 수 없는 contract도 web으로 보내 검증 단계에서 fail-closed한다.
        return "web"

    if _render_web_marker(environment):
        return "web"

    service_account_marker = (
        _default_kubernetes_service_account_marker()
        if kubernetes_service_account_marker is None
        else kubernetes_service_account_marker
    )
    if _kubernetes_cluster_marker(
        environment, service_account_marker=service_account_marker
    ):
        return "web"

    tokens = tuple(str(value) for value in command)
    if any("src.web.main:app" in token for token in tokens):
        return "web"
    trigger_scope = TRIGGER_COMMAND_SCOPES.get(_normalized_command_words(command))
    if trigger_scope:
        return trigger_scope
    return "generic"


def _requires_web_validation(
    environment: Mapping[str, str],
    *,
    runtime_contract: str | None,
    kubernetes_service_account_marker: bool | None,
) -> bool:
    """호출자가 낮은 scope를 넘겨도 배포 web 불변식을 우선한다."""

    if _runtime_contract(environment, runtime_contract):
        return True
    if _render_web_marker(environment):
        return True
    service_account_marker = (
        _default_kubernetes_service_account_marker()
        if kubernetes_service_account_marker is None
        else kubernetes_service_account_marker
    )
    return _kubernetes_cluster_marker(
        environment, service_account_marker=service_account_marker
    )


def _validate_forwarded_proxy_configuration(
    environment: Mapping[str, str],
    *,
    runtime_contract: str | None = None,
    kubernetes_service_account_marker: bool | None = None,
) -> list[str]:
    errors: list[str] = []
    declared_exposure = environment.get("DEPLOYMENT_EXPOSURE", "").strip().lower()
    declared_platform = environment.get("DEPLOYMENT_PLATFORM", "").strip().lower()
    contract = _runtime_contract(environment, runtime_contract)
    if not contract:
        errors.append(RUNTIME_CONTRACT_REQUIRED_BLOCKER)
    elif contract not in RUNTIME_CONTRACTS:
        errors.append("DEPLOYMENT_RUNTIME_CONTRACT: 지원하지 않는 contract입니다")
    if declared_exposure not in DEPLOYMENT_EXPOSURES:
        errors.append("DEPLOYMENT_EXPOSURE: local 또는 public을 명시해야 합니다")
    if declared_platform not in DEPLOYMENT_PLATFORMS:
        errors.append("DEPLOYMENT_PLATFORM: 지원하는 배포 플랫폼이 아닙니다")

    service_account_marker = (
        _default_kubernetes_service_account_marker()
        if kubernetes_service_account_marker is None
        else kubernetes_service_account_marker
    )
    render_contracts = {
        RUNTIME_CONTRACT_RENDER_WEB,
        RUNTIME_CONTRACT_RENDER_ADMIN_DEMO,
        RUNTIME_CONTRACT_RENDER_ADMIN_REAL,
    }
    render_contract_detected = contract in render_contracts
    render_marker_detected = _render_web_marker(environment)
    render_detected = render_contract_detected or render_marker_detected
    kubernetes_detected = (
        contract == RUNTIME_CONTRACT_KUBERNETES_WEB
        or _kubernetes_cluster_marker(
            environment, service_account_marker=service_account_marker
        )
    )
    # Render가 공식 주입하는 runtime·web marker와 manifest 계약이 모두 일치하면,
    # 컨테이너 안의 Kubernetes 흔적은 별도 배포 플랫폼이 아니라 Render의 내부
    # substrate일 수 있다. 이 좁은 경우에만 교차 marker 충돌을 억제하고,
    # 관리자 demo의 origin·명령·proxy 비신뢰 검증은 아래에서 그대로 수행한다.
    render_hosted_substrate = (
        render_contract_detected
        and render_marker_detected
        and environment.get("RENDER", "").strip().lower() == "true"
        and declared_platform == "render"
        and declared_exposure == "public"
    )
    if render_detected and kubernetes_detected and not render_hosted_substrate:
        errors.append("PLATFORM_MARKERS: Render와 Kubernetes marker가 동시에 감지됐습니다")
    if render_detected:
        if contract and contract not in render_contracts:
            errors.append(
                "DEPLOYMENT_RUNTIME_CONTRACT: Render web은 "
                "지원하는 Render contract를 강제합니다"
            )
        if declared_exposure != "public":
            errors.append("DEPLOYMENT_EXPOSURE: Render web marker는 public을 강제합니다")
        if declared_platform != "render":
            errors.append("DEPLOYMENT_PLATFORM: Render marker는 render를 강제합니다")
        exposure = "public"
        platform = "render"
    elif kubernetes_detected:
        if contract and contract != RUNTIME_CONTRACT_KUBERNETES_WEB:
            errors.append(
                "DEPLOYMENT_RUNTIME_CONTRACT: Kubernetes web은 "
                f"{RUNTIME_CONTRACT_KUBERNETES_WEB}을 강제합니다"
            )
        if declared_exposure != "public":
            errors.append("DEPLOYMENT_EXPOSURE: Kubernetes marker는 public을 강제합니다")
        if declared_platform != "kubernetes":
            errors.append("DEPLOYMENT_PLATFORM: Kubernetes marker는 kubernetes를 강제합니다")
        exposure = "public"
        platform = "kubernetes"
    elif contract == RUNTIME_CONTRACT_LOCAL_WEB:
        if declared_exposure != "local":
            errors.append("DEPLOYMENT_EXPOSURE: local web contract는 local을 강제합니다")
        if declared_platform != "local":
            errors.append("DEPLOYMENT_PLATFORM: local web contract는 local을 강제합니다")
        exposure = "local"
        platform = "local"
    else:
        exposure = declared_exposure
        platform = declared_platform

    if exposure not in DEPLOYMENT_EXPOSURES or platform not in DEPLOYMENT_PLATFORMS:
        return errors

    forwarded_raw = environment.get("FORWARDED_ALLOW_IPS", "").strip()
    if exposure == "local":
        if platform != "local":
            errors.append("DEPLOYMENT_PLATFORM: local 노출은 local 플랫폼이어야 합니다")
        errors.extend(_local_proxy_errors(forwarded_raw))
        return errors

    if platform not in {"render", "kubernetes"}:
        errors.append("DEPLOYMENT_PLATFORM: 공개 배포는 render 또는 kubernetes여야 합니다")

    if contract == RUNTIME_CONTRACT_RENDER_ADMIN_DEMO:
        errors.extend(_render_admin_demo_errors(environment))
        return errors
    if contract == RUNTIME_CONTRACT_RENDER_ADMIN_REAL:
        errors.extend(_render_admin_real_errors(environment))
        return errors

    forwarded_networks, network_errors = _proxy_networks(
        forwarded_raw, label="FORWARDED_ALLOW_IPS"
    )
    errors.extend(network_errors)
    origin_error = _public_origin_error(environment.get("PUBLIC_ORIGIN", ""))
    if origin_error:
        errors.append(origin_error)
    errors.extend(_evidence_errors(environment, COMMON_PUBLIC_EVIDENCE))

    if platform == "render":
        errors.extend(_evidence_errors(environment, RENDER_PUBLIC_EVIDENCE))
        errors.append(RENDER_FORWARDED_TRUST_BLOCKER)
    elif platform == "kubernetes":
        ingress_raw = environment.get("K8S_INGRESS_PROXY_CIDRS", "").strip()
        ingress_networks, ingress_errors = _proxy_networks(
            ingress_raw, label="K8S_INGRESS_PROXY_CIDRS"
        )
        errors.extend(ingress_errors)
        if forwarded_networks and ingress_networks != forwarded_networks:
            errors.append(
                "K8S_INGRESS_PROXY_CIDRS: FORWARDED_ALLOW_IPS와 정확히 같아야 합니다"
            )
        errors.extend(_evidence_errors(environment, KUBERNETES_PUBLIC_EVIDENCE))
    if not PRODUCTION_FORWARDED_EVIDENCE_VERIFIER_AVAILABLE:
        errors.append(PRODUCTION_FORWARDED_EVIDENCE_BLOCKER)
    return errors


def validate(
    environment: Mapping[str, str],
    scope: str = "web",
    *,
    runtime_contract: str | None = None,
    kubernetes_service_account_marker: bool | None = None,
) -> list[str]:
    """환경 오류만 반환한다. 반환값에는 설정값 자체가 절대 들어가지 않는다."""
    errors: list[str] = []
    effective_scope = scope
    if scope in SCOPES and scope != "web" and _requires_web_validation(
        environment,
        runtime_contract=runtime_contract,
        kubernetes_service_account_marker=kubernetes_service_account_marker,
    ):
        effective_scope = "web"
    pipeline = environment.get("PIPELINE", "").strip().lower()
    if pipeline not in {"demo", "real"}:
        errors.append("PIPELINE: demo 또는 real만 허용합니다")

    # ★ 엔진 v2 스위치 — «조용히 v1로 되돌아가는 것»을 막는다.
    #   코드는 값이 «정확히 "1"»일 때만 v2로 간다
    #   (app/src/features/pipeline/real.py: _engine_v2_enabled).
    #   그래서 true·yes·on·" 1 " 같은 값은 오류 없이 v1 보고서를 내보낸다.
    #   이 프로젝트에서 「고쳤는데 화면에 안 나온다」가 반복된 원인이 정확히
    #   이런 «조용한 되돌아감»이었다. 안 넣는 것(v1)은 정상이지만, 넣었는데
    #   못 알아듣는 값이면 시작을 거부한다.
    engine_v2 = environment.get("ENGINE_V2")
    if engine_v2 is not None and engine_v2 not in {"1", "0"}:
        errors.append('ENGINE_V2: "1"(v2) 또는 "0"(v1)만 허용합니다')

    port_error = _integer_error("PORT", environment.get("PORT", ""), 1, 65535)
    if port_error:
        errors.append(port_error)
    grace_error = _integer_error(
        "GRACEFUL_SHUTDOWN_SECONDS",
        environment.get("GRACEFUL_SHUTDOWN_SECONDS", ""),
        1,
        3600,
    )
    if grace_error:
        errors.append(grace_error)
    render_runtime = (
        environment.get("DEPLOYMENT_PLATFORM", "").strip().lower() == "render"
        or _runtime_contract(environment, runtime_contract)
        in {
            RUNTIME_CONTRACT_RENDER_WEB,
            RUNTIME_CONTRACT_RENDER_ADMIN_DEMO,
            RUNTIME_CONTRACT_RENDER_ADMIN_REAL,
        }
    )
    if (
        render_runtime
        and environment.get("GRACEFUL_SHUTDOWN_SECONDS", "").strip()
        != RENDER_GRACEFUL_SHUTDOWN_SECONDS
    ):
        errors.append(
            "GRACEFUL_SHUTDOWN_SECONDS: Uvicorn HTTP 정리 뒤 앱 240초 정리가 "
            "직렬 실행되므로 Render 플랫폼 300초 안에 끝나도록 20이어야 합니다"
        )
    errors.extend(_validate_persistence_paths(environment))

    log_level = environment.get("LOG_LEVEL", "").strip().lower()
    if log_level not in LOG_LEVELS:
        errors.append("LOG_LEVEL: 지원하는 로그 수준이 아닙니다")

    if effective_scope == "web":
        errors.extend(
            _validate_forwarded_proxy_configuration(
                environment,
                runtime_contract=runtime_contract,
                kubernetes_service_account_marker=kubernetes_service_account_marker,
            )
        )
        beta_raw = environment.get("BETA_ADMIN_ONLY", "").strip().lower()
        if beta_raw not in BOOLEAN_TRUE | BOOLEAN_FALSE:
            errors.append("BETA_ADMIN_ONLY: 명시적인 불리언 값이 필요합니다")
        elif beta_raw in BOOLEAN_TRUE:
            errors.extend(_required(environment, WEB_AUTH_VARIABLES))

        if pipeline == "real":
            errors.extend(_required(environment, REAL_PIPELINE_VARIABLES))
            seal = environment.get("PROVENANCE_SEAL_SECRET", "")
            if not seal.strip():
                errors.append("PROVENANCE_SEAL_SECRET: 값이 필요합니다")
            elif len(seal.encode("utf-8")) < 32:
                errors.append("PROVENANCE_SEAL_SECRET: 최소 길이를 충족하지 않습니다")
        errors.extend(_validate_backup_manifest_configuration(environment))
    elif effective_scope == "backup-trigger":
        errors.extend(_required(environment, ("BACKUP_TRIGGER_URL", "BACKUP_TRIGGER_SECRET")))
    elif effective_scope == "maintenance-trigger":
        errors.extend(
            _required(environment, ("MAINTENANCE_TRIGGER_URL", "MAINTENANCE_TRIGGER_SECRET"))
        )
    elif effective_scope == "generic":
        errors.append(GENERIC_COMMAND_BLOCKER)
    else:
        errors.append("검증 범위가 올바르지 않습니다")

    return errors


def validate_command(
    environment: Mapping[str, str],
    command: Sequence[str],
    *,
    runtime_contract: str | None = None,
    kubernetes_service_account_marker: bool | None = None,
) -> tuple[str, list[str]]:
    """entrypoint command의 scope를 결정하고 한 번에 환경 계약을 검증한다."""

    scope = resolve_scope(
        environment,
        command,
        runtime_contract=runtime_contract,
        kubernetes_service_account_marker=kubernetes_service_account_marker,
    )
    if scope == "generic":
        return scope, [GENERIC_COMMAND_BLOCKER]
    errors = validate(
        environment,
        scope,
        runtime_contract=runtime_contract,
        kubernetes_service_account_marker=kubernetes_service_account_marker,
    )
    if _runtime_contract(environment, runtime_contract) in {
        RUNTIME_CONTRACT_RENDER_ADMIN_DEMO,
        RUNTIME_CONTRACT_RENDER_ADMIN_REAL,
    }:
        errors.extend(_render_admin_no_forwarded_command_errors(command))
    return scope, errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=SCOPES, default="web")
    parser.add_argument("--from-command", action="store_true")
    parser.add_argument("--runtime-contract")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.from_command:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        scope, errors = validate_command(
            os.environ,
            command,
            runtime_contract=args.runtime_contract,
        )
    else:
        scope = args.scope
        errors = validate(
            os.environ,
            scope,
            runtime_contract=args.runtime_contract,
        )
    if errors:
        print("배포 환경 검증 실패:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

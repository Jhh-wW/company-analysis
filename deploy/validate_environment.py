"""비밀값을 출력하지 않고 컨테이너 시작 환경의 구조를 검증한다."""

from __future__ import annotations

import argparse
import ipaddress
import os
import posixpath
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
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


def _validate_forwarded_proxy_configuration(
    environment: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    exposure = environment.get("DEPLOYMENT_EXPOSURE", "").strip().lower()
    platform = environment.get("DEPLOYMENT_PLATFORM", "").strip().lower()
    if exposure not in DEPLOYMENT_EXPOSURES:
        return ["DEPLOYMENT_EXPOSURE: local 또는 public을 명시해야 합니다"]
    if platform not in DEPLOYMENT_PLATFORMS:
        return ["DEPLOYMENT_PLATFORM: 지원하는 배포 플랫폼이 아닙니다"]

    forwarded_raw = environment.get("FORWARDED_ALLOW_IPS", "").strip()
    if exposure == "local":
        if platform != "local":
            errors.append("DEPLOYMENT_PLATFORM: local 노출은 local 플랫폼이어야 합니다")
        errors.extend(_local_proxy_errors(forwarded_raw))
        return errors

    if platform not in {"render", "kubernetes"}:
        errors.append("DEPLOYMENT_PLATFORM: 공개 배포는 render 또는 kubernetes여야 합니다")
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


def validate(environment: Mapping[str, str], scope: str = "web") -> list[str]:
    """환경 오류만 반환한다. 반환값에는 설정값 자체가 절대 들어가지 않는다."""
    errors: list[str] = []
    pipeline = environment.get("PIPELINE", "").strip().lower()
    if pipeline not in {"demo", "real"}:
        errors.append("PIPELINE: demo 또는 real만 허용합니다")

    port_error = _integer_error("PORT", environment.get("PORT", ""), 1, 65535)
    if port_error:
        errors.append(port_error)
    grace_error = _integer_error(
        "GRACEFUL_SHUTDOWN_SECONDS",
        environment.get("GRACEFUL_SHUTDOWN_SECONDS", ""),
        250,
        3600,
    )
    if grace_error:
        errors.append(grace_error)
    errors.extend(_validate_persistence_paths(environment))

    log_level = environment.get("LOG_LEVEL", "").strip().lower()
    if log_level not in LOG_LEVELS:
        errors.append("LOG_LEVEL: 지원하는 로그 수준이 아닙니다")

    if scope == "web":
        errors.extend(_validate_forwarded_proxy_configuration(environment))
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
    elif scope == "backup-trigger":
        errors.extend(_required(environment, ("BACKUP_TRIGGER_URL", "BACKUP_TRIGGER_SECRET")))
    elif scope == "maintenance-trigger":
        errors.extend(
            _required(environment, ("MAINTENANCE_TRIGGER_URL", "MAINTENANCE_TRIGGER_SECRET"))
        )
    elif scope != "generic":
        errors.append("검증 범위가 올바르지 않습니다")

    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=SCOPES, default="web")
    args = parser.parse_args(argv)
    errors = validate(os.environ, args.scope)
    if errors:
        print("배포 환경 검증 실패:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

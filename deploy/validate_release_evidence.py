"""실제 artifact와 고정 trust policy로 이미지 공개 배포를 fail-closed 판정한다."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
OCI_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
KNOWN_SCANNERS = frozenset({"trivy", "docker-scout", "grype"})
KNOWN_SBOM_FORMATS = frozenset({"spdx-json", "cyclonedx-json"})
HIGH_SEVERITIES = frozenset({"high", "critical"})
SIGNATURE_ALGORITHM = "ed25519"
POLICY_PATH = Path(__file__).with_name("release-policy.json")
POLICY_PIN_PATH = Path(__file__).with_name("release-policy.sha256")


@dataclass(frozen=True)
class VerifiedReleaseArtifacts:
    """독립 도구 어댑터가 raw artifact를 실제 검증한 뒤 내놓는 사실."""

    scan_report_sha256: str
    scan_subject_digest: str
    scanner: str
    findings: tuple[tuple[str, str, str], ...]
    approved_vex_finding_ids: tuple[str, ...]
    sbom_sha256: str
    sbom_subject_digest: str
    provenance_sha256: str
    provenance_subject_digest: str
    provenance_predicate_type: str
    provenance_builder_id: str
    provenance_source_revision: str
    signature_bundle_sha256: str
    signature_subject_digest: str
    signed_payload_sha256: str
    signing_key_spki_sha256: str


class ReleaseArtifactVerifier(ABC):
    """scanner/SBOM/provenance/서명 실제 형식을 아는 운영 주입 경계."""

    @abstractmethod
    def verify(
        self,
        *,
        artifacts: Mapping[str, bytes],
        canonical_payload: bytes,
        policy: Mapping[str, object],
    ) -> VerifiedReleaseArtifacts:
        raise NotImplementedError


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def canonical_unsigned_payload(bundle: Mapping[str, object]) -> bytes:
    """서명 artifact 자체를 빼고 모든 release claim과 artifact hash를 고정한다."""

    payload = {
        key: bundle[key]
        for key in ("schema_version", "image", "scan", "vex", "sbom", "provenance")
        if key in bundle
    }
    return _canonical_json(payload)


def _mapping(value: object, *, label: str, errors: list[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: 객체가 필요합니다")
        return {}
    return value


def _text(value: object, *, label: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: 값이 필요합니다")
        return ""
    return value.strip()


def _sha256(value: object, *, label: str, errors: list[str]) -> str:
    text = _text(value, label=label, errors=errors)
    if text and not SHA256_RE.fullmatch(text):
        errors.append(f"{label}: lowercase SHA-256이 필요합니다")
        return ""
    return text


def _oci_digest(value: object, *, label: str, errors: list[str]) -> str:
    text = _text(value, label=label, errors=errors)
    if text and not OCI_DIGEST_RE.fullmatch(text):
        errors.append(f"{label}: 최종 sha256 OCI digest가 필요합니다")
        return ""
    return text


def _nonnegative_int(value: object, *, label: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{label}: 0 이상의 정수가 필요합니다")
        return -1
    return value


def _string_list(value: object, *, label: str, errors: list[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        errors.append(f"{label}: 비어 있지 않은 문자열 목록이 필요합니다")
        return []
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        errors.append(f"{label}: 중복 값을 허용하지 않습니다")
    return normalized


def _policy(policy: Mapping[str, object], errors: list[str]) -> dict[str, object]:
    if policy.get("schema_version") != SCHEMA_VERSION:
        errors.append("policy.schema_version: 지원하지 않는 버전입니다")
    platforms = _string_list(
        policy.get("required_platforms"),
        label="policy.required_platforms",
        errors=errors,
    )
    if not {"linux/amd64", "linux/arm64"}.issubset(platforms):
        errors.append("policy.required_platforms: amd64와 arm64가 모두 필요합니다")
    scanners = _string_list(
        policy.get("allowed_scanners"),
        label="policy.allowed_scanners",
        errors=errors,
    )
    if any(scanner not in KNOWN_SCANNERS for scanner in scanners):
        errors.append("policy.allowed_scanners: 지원하지 않는 scanner가 있습니다")
    approvers = _string_list(
        policy.get("vex_approvers"), label="policy.vex_approvers", errors=errors
    )
    builder = _text(
        policy.get("trusted_builder_id"), label="policy.trusted_builder_id", errors=errors
    )
    predicate = _text(
        policy.get("provenance_predicate_type"),
        label="policy.provenance_predicate_type",
        errors=errors,
    )
    algorithm = _text(
        policy.get("signature_algorithm"),
        label="policy.signature_algorithm",
        errors=errors,
    )
    fingerprint = _sha256(
        policy.get("release_public_key_spki_sha256"),
        label="policy.release_public_key_spki_sha256",
        errors=errors,
    )
    public_key_pem = _text(
        policy.get("release_public_key_pem"),
        label="policy.release_public_key_pem",
        errors=errors,
    )
    if algorithm and algorithm != SIGNATURE_ALGORITHM:
        errors.append("policy.signature_algorithm: ed25519만 허용합니다")
    for label, value in (
        ("policy.trusted_builder_id", builder),
        ("policy.release_public_key_spki_sha256", fingerprint),
        ("policy.release_public_key_pem", public_key_pem),
    ):
        if value.upper().startswith("REPLACE_"):
            errors.append(f"{label}: placeholder를 실제 pin으로 바꿔야 합니다")
    return {
        "platforms": platforms,
        "scanners": scanners,
        "approvers": approvers,
        "builder": builder,
        "predicate": predicate,
        "algorithm": algorithm,
        "fingerprint": fingerprint,
        "public_key_pem": public_key_pem,
    }


def _finding_tuples(value: object) -> list[tuple[str, str, str]] | None:
    if not isinstance(value, list):
        return None
    findings: list[tuple[str, str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        finding_id = item.get("id")
        severity = item.get("severity")
        artifact = item.get("artifact")
        if not all(isinstance(part, str) and part.strip() for part in (finding_id, severity, artifact)):
            return None
        findings.append((finding_id.strip(), severity.strip().lower(), artifact.strip()))
    return sorted(findings)


def validate_bundle(
    bundle: Mapping[str, object], policy: Mapping[str, object]
) -> list[str]:
    """claim 구조를 검증한다. 공개 PASS에는 :func:`validate_artifacts`도 필수다."""

    errors: list[str] = []
    pins = _policy(policy, errors)
    if bundle.get("schema_version") != SCHEMA_VERSION:
        errors.append("evidence.schema_version: 지원하지 않는 버전입니다")

    image = _mapping(bundle.get("image"), label="image", errors=errors)
    index_digest = _oci_digest(
        image.get("index_digest"), label="image.index_digest", errors=errors
    )
    platform_values = _mapping(
        image.get("platform_digests"), label="image.platform_digests", errors=errors
    )
    platform_digests: dict[str, str] = {}
    for platform in pins["platforms"]:
        platform_digests[str(platform)] = _oci_digest(
            platform_values.get(platform),
            label=f"image.platform_digests.{platform}",
            errors=errors,
        )
    child_digests = [value for value in platform_digests.values() if value]
    if len(set(child_digests)) != len(child_digests):
        errors.append("image.platform_digests: 플랫폼 digest는 서로 달라야 합니다")
    if index_digest and index_digest in child_digests:
        errors.append("image.index_digest: child manifest가 아닌 multi-arch index여야 합니다")

    scan = _mapping(bundle.get("scan"), label="scan", errors=errors)
    scanner = _text(scan.get("scanner"), label="scan.scanner", errors=errors)
    _text(scan.get("scanner_version"), label="scan.scanner_version", errors=errors)
    if scanner and scanner not in pins["scanners"]:
        errors.append("scan.scanner: release policy가 허용하지 않습니다")
    scan_subject = _oci_digest(
        scan.get("subject_digest"), label="scan.subject_digest", errors=errors
    )
    if index_digest and scan_subject and scan_subject != index_digest:
        errors.append("scan.subject_digest: final multi-arch index digest와 다릅니다")
    _sha256(scan.get("report_sha256"), label="scan.report_sha256", errors=errors)
    high_count = _nonnegative_int(
        scan.get("reachable_high"), label="scan.reachable_high", errors=errors
    )
    critical_count = _nonnegative_int(
        scan.get("reachable_critical"), label="scan.reachable_critical", errors=errors
    )
    findings = _finding_tuples(scan.get("reachable_findings"))
    if findings is None:
        errors.append("scan.reachable_findings: 정규화 finding 목록이 필요합니다")
        findings = []
    if high_count >= 0 and high_count != sum(item[1] == "high" for item in findings):
        errors.append("scan.reachable_high: finding 목록과 개수가 다릅니다")
    if critical_count >= 0 and critical_count != sum(
        item[1] == "critical" for item in findings
    ):
        errors.append("scan.reachable_critical: finding 목록과 개수가 다릅니다")
    if any(item[1] not in HIGH_SEVERITIES for item in findings):
        errors.append("scan.finding.severity: high/critical만 이 목록에 둘 수 있습니다")
    finding_ids = [item[0] for item in findings]
    if len(set(finding_ids)) != len(finding_ids):
        errors.append("scan.finding.id: 중복 finding을 허용하지 않습니다")

    vex_value = bundle.get("vex")
    vex = _mapping(vex_value, label="vex", errors=errors) if vex_value is not None else {}
    approved_values = vex.get("approved_exceptions", [])
    if not isinstance(approved_values, list):
        errors.append("vex.approved_exceptions: 목록이 필요합니다")
        approved_values = []
    approved: dict[str, Mapping[str, object]] = {}
    for statement in approved_values:
        item = _mapping(statement, label="vex exception", errors=errors)
        finding_id = _text(item.get("finding_id"), label="vex.finding_id", errors=errors)
        status = _text(item.get("status"), label="vex.status", errors=errors)
        approver = _text(item.get("approved_by"), label="vex.approved_by", errors=errors)
        _sha256(item.get("statement_sha256"), label="vex.statement_sha256", errors=errors)
        if status and status != "not_affected":
            errors.append("vex.status: reachable 예외는 not_affected만 허용합니다")
        if approver and approver not in pins["approvers"]:
            errors.append("vex.approved_by: release policy의 승인자가 아닙니다")
        if finding_id in approved:
            errors.append("vex.finding_id: 중복 승인을 허용하지 않습니다")
        elif finding_id:
            approved[finding_id] = item
    if findings:
        vex_subject = _oci_digest(
            vex.get("subject_digest"), label="vex.subject_digest", errors=errors
        )
        if index_digest and vex_subject and vex_subject != index_digest:
            errors.append("vex.subject_digest: final multi-arch index digest와 다릅니다")
        _sha256(vex.get("document_sha256"), label="vex.document_sha256", errors=errors)
    for finding_id in finding_ids:
        if approved.get(finding_id, {}).get("status") != "not_affected":
            errors.append("scan.reachable_findings: 승인 VEX 없는 high/critical이 있습니다")
    if set(approved) - set(finding_ids):
        errors.append("vex.approved_exceptions: scan에 없는 finding을 승인했습니다")

    sbom = _mapping(bundle.get("sbom"), label="sbom", errors=errors)
    sbom_format = _text(sbom.get("format"), label="sbom.format", errors=errors)
    if sbom_format and sbom_format not in KNOWN_SBOM_FORMATS:
        errors.append("sbom.format: SPDX JSON 또는 CycloneDX JSON이 필요합니다")
    sbom_subject = _oci_digest(
        sbom.get("subject_digest"), label="sbom.subject_digest", errors=errors
    )
    if index_digest and sbom_subject and sbom_subject != index_digest:
        errors.append("sbom.subject_digest: final multi-arch index digest와 다릅니다")
    _sha256(sbom.get("document_sha256"), label="sbom.document_sha256", errors=errors)
    _text(sbom.get("generator"), label="sbom.generator", errors=errors)

    provenance = _mapping(bundle.get("provenance"), label="provenance", errors=errors)
    provenance_subject = _oci_digest(
        provenance.get("subject_digest"),
        label="provenance.subject_digest",
        errors=errors,
    )
    if index_digest and provenance_subject and provenance_subject != index_digest:
        errors.append("provenance.subject_digest: final multi-arch index digest와 다릅니다")
    _sha256(
        provenance.get("attestation_sha256"),
        label="provenance.attestation_sha256",
        errors=errors,
    )
    predicate = _text(
        provenance.get("predicate_type"), label="provenance.predicate_type", errors=errors
    )
    builder = _text(
        provenance.get("builder_id"), label="provenance.builder_id", errors=errors
    )
    revision = _text(
        provenance.get("source_revision"),
        label="provenance.source_revision",
        errors=errors,
    )
    mode = _text(provenance.get("build_mode"), label="provenance.build_mode", errors=errors)
    if predicate and predicate != pins["predicate"]:
        errors.append("provenance.predicate_type: release policy pin과 다릅니다")
    if builder and builder != pins["builder"]:
        errors.append("provenance.builder_id: release policy pin과 다릅니다")
    if revision and not SOURCE_REVISION_RE.fullmatch(revision):
        errors.append("provenance.source_revision: 고정 commit digest가 필요합니다")
    if mode and mode != "max":
        errors.append("provenance.build_mode: mode=max provenance가 필요합니다")

    signature = _mapping(bundle.get("signature"), label="signature", errors=errors)
    signature_subject = _oci_digest(
        signature.get("subject_digest"), label="signature.subject_digest", errors=errors
    )
    if index_digest and signature_subject and signature_subject != index_digest:
        errors.append("signature.subject_digest: final multi-arch index digest와 다릅니다")
    algorithm = _text(
        signature.get("algorithm"), label="signature.algorithm", errors=errors
    )
    if algorithm and algorithm != pins["algorithm"]:
        errors.append("signature.algorithm: release policy pin과 다릅니다")
    _sha256(
        signature.get("bundle_sha256"), label="signature.bundle_sha256", errors=errors
    )
    return errors


def validate_artifacts(
    bundle: Mapping[str, object],
    policy: Mapping[str, object],
    artifacts: Mapping[str, bytes],
    verifier: ReleaseArtifactVerifier | None,
) -> list[str]:
    """실제 bytes와 독립 verifier 결과를 claim·repository pin에 대조한다."""

    errors: list[str] = []
    pins = _policy(policy, errors)
    scan = _mapping(bundle.get("scan"), label="scan", errors=errors)
    sbom = _mapping(bundle.get("sbom"), label="sbom", errors=errors)
    provenance = _mapping(bundle.get("provenance"), label="provenance", errors=errors)
    signature = _mapping(bundle.get("signature"), label="signature", errors=errors)
    vex_value = bundle.get("vex")
    vex = _mapping(vex_value, label="vex", errors=errors) if vex_value is not None else {}
    expected_hashes = {
        "scan_report": scan.get("report_sha256"),
        "sbom": sbom.get("document_sha256"),
        "provenance": provenance.get("attestation_sha256"),
        "signature_bundle": signature.get("bundle_sha256"),
    }
    if vex:
        expected_hashes["vex"] = vex.get("document_sha256")
    for name, expected in expected_hashes.items():
        raw = artifacts.get(name)
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ARTIFACT_BYTES:
            errors.append(f"artifact.{name}: 실제 artifact bytes가 필요합니다")
            continue
        normalized = _sha256(expected, label=f"artifact.{name}.sha256", errors=errors)
        if normalized and not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), normalized):
            errors.append(f"artifact.{name}: evidence hash와 실제 bytes가 다릅니다")
    if verifier is None:
        errors.append(
            "release artifact verifier: 실제 scanner/SBOM/provenance/서명 형식 "
            "검증 구현이 없어 BLOCKED입니다"
        )
        return errors
    if not isinstance(verifier, ReleaseArtifactVerifier):
        errors.append("release artifact verifier: 닫힌 verifier 구현이 필요합니다")
        return errors

    unsigned_payload = canonical_unsigned_payload(bundle)
    payload_sha256 = hashlib.sha256(unsigned_payload).hexdigest()
    image = bundle.get("image", {})
    index_digest = image.get("index_digest") if isinstance(image, Mapping) else None
    try:
        verified = verifier.verify(
            artifacts=artifacts,
            canonical_payload=unsigned_payload,
            policy=policy,
        )
    except Exception:  # noqa: BLE001 - verifier 내부값을 로그에 노출하지 않는다.
        errors.append("release artifact verifier: 실제 artifact 검증에 실패했습니다")
        return errors
    if not isinstance(verified, VerifiedReleaseArtifacts):
        errors.append("release artifact verifier: 검증 결과 형식이 올바르지 않습니다")
        return errors

    expected_findings = tuple(_finding_tuples(scan.get("reachable_findings")) or [])
    approved_values = vex.get("approved_exceptions", [])
    expected_approved = tuple(
        sorted(
            str(item.get("finding_id"))
            for item in approved_values
            if isinstance(item, Mapping) and item.get("finding_id")
        )
    )
    comparisons = (
        (verified.scan_report_sha256, scan.get("report_sha256"), "scan report hash"),
        (verified.scan_subject_digest, index_digest, "scan final digest"),
        (verified.scanner, scan.get("scanner"), "scanner"),
        (verified.findings, expected_findings, "scan high/critical finding"),
        (verified.approved_vex_finding_ids, expected_approved, "approved VEX"),
        (verified.sbom_sha256, sbom.get("document_sha256"), "SBOM hash"),
        (verified.sbom_subject_digest, index_digest, "SBOM final digest"),
        (
            verified.provenance_sha256,
            provenance.get("attestation_sha256"),
            "provenance hash",
        ),
        (verified.provenance_subject_digest, index_digest, "provenance final digest"),
        (
            verified.provenance_predicate_type,
            provenance.get("predicate_type"),
            "provenance predicate",
        ),
        (
            verified.provenance_builder_id,
            provenance.get("builder_id"),
            "provenance builder",
        ),
        (
            verified.provenance_source_revision,
            provenance.get("source_revision"),
            "provenance revision",
        ),
        (
            verified.signature_bundle_sha256,
            signature.get("bundle_sha256"),
            "signature bundle hash",
        ),
        (verified.signature_subject_digest, index_digest, "signature final digest"),
        (verified.signed_payload_sha256, payload_sha256, "signature canonical payload"),
        (
            verified.signing_key_spki_sha256,
            pins["fingerprint"],
            "signature public-key pin",
        ),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            errors.append(f"release artifact verifier: {label} 결과가 evidence/policy와 다릅니다")
    return errors


def evaluate_release(
    *,
    bundle: Mapping[str, object],
    policy: Mapping[str, object],
    artifacts: Mapping[str, bytes],
    verifier: ReleaseArtifactVerifier | None,
) -> list[str]:
    """구조 helper 단독 PASS를 공개 승인으로 승격하지 않는 유일한 합성 gate."""

    return validate_bundle(bundle, policy) + validate_artifacts(
        bundle, policy, artifacts, verifier
    )


def _read_bytes(path: Path, *, label: str, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label}: 일반 파일이 필요합니다")
    raw = path.read_bytes()
    if not raw or len(raw) > maximum:
        raise ValueError(f"{label}: 파일 크기가 올바르지 않습니다")
    return raw


def _read_json(path: Path, *, label: str) -> tuple[Mapping[str, object], bytes]:
    raw = _read_bytes(path, label=label, maximum=MAX_DOCUMENT_BYTES)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}: UTF-8 JSON이 필요합니다") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label}: JSON 객체가 필요합니다")
    return payload, raw


def _load_repository_pinned_policy() -> Mapping[str, object]:
    """CLI 입력이 아닌 보호된 repository policy와 pin만 신뢰한다."""

    try:
        pin = POLICY_PIN_PATH.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ValueError("보호된 release policy pin이 없습니다") from exc
    if not SHA256_RE.fullmatch(pin):
        raise ValueError("release policy pin이 BLOCKED placeholder 상태입니다")
    policy, raw = _read_json(POLICY_PATH, label="repository release policy")
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin):
        raise ValueError("repository release policy가 보호된 pin과 다릅니다")
    return policy


def main(
    argv: Sequence[str] | None = None,
    *,
    artifact_verifier: ReleaseArtifactVerifier | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--scan-report", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--signature-bundle", type=Path)
    parser.add_argument("--vex", type=Path)
    arguments = parser.parse_args(argv)
    required = (
        arguments.evidence,
        arguments.scan_report,
        arguments.sbom,
        arguments.provenance,
        arguments.signature_bundle,
    )
    if any(path is None for path in required):
        print("공개 배포 BLOCKED: evidence와 실제 artifact 경로가 모두 필요합니다", file=sys.stderr)
        return 78
    try:
        policy = _load_repository_pinned_policy()
        evidence, _raw = _read_json(arguments.evidence, label="evidence")
        artifacts = {
            "scan_report": _read_bytes(
                arguments.scan_report, label="scan report", maximum=MAX_ARTIFACT_BYTES
            ),
            "sbom": _read_bytes(arguments.sbom, label="SBOM", maximum=MAX_ARTIFACT_BYTES),
            "provenance": _read_bytes(
                arguments.provenance, label="provenance", maximum=MAX_ARTIFACT_BYTES
            ),
            "signature_bundle": _read_bytes(
                arguments.signature_bundle,
                label="signature bundle",
                maximum=MAX_ARTIFACT_BYTES,
            ),
        }
        if evidence.get("vex") is not None:
            if arguments.vex is None:
                raise ValueError("승인 VEX claim에는 실제 VEX artifact가 필요합니다")
            artifacts["vex"] = _read_bytes(
                arguments.vex, label="VEX", maximum=MAX_ARTIFACT_BYTES
            )
    except (OSError, ValueError) as exc:
        print(f"공개 배포 BLOCKED: {exc}", file=sys.stderr)
        return 78
    errors = evaluate_release(
        bundle=evidence,
        policy=policy,
        artifacts=artifacts,
        verifier=artifact_verifier,
    )
    if errors:
        print("공개 배포 BLOCKED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 78
    print("공개 배포 이미지 공급망 artifact·서명 검증 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

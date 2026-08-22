"""multi-arch 공급망 gate의 artifact·독립 verifier 공격 시험."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


VALIDATOR_PATH = Path(__file__).resolve().parents[1] / "validate_release_evidence.py"
SPEC = importlib.util.spec_from_file_location("deploy_release_evidence", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

INDEX = "sha256:" + "a" * 64
AMD64 = "sha256:" + "b" * 64
ARM64 = "sha256:" + "c" * 64
KEY_FINGERPRINT = "f" * 64


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "required_platforms": ["linux/amd64", "linux/arm64"],
        "allowed_scanners": ["trivy", "docker-scout", "grype"],
        "trusted_builder_id": "https://builder.example/release/v1",
        "provenance_predicate_type": "https://slsa.dev/provenance/v1",
        "signature_algorithm": "ed25519",
        "release_public_key_spki_sha256": KEY_FINGERPRINT,
        "release_public_key_pem": "-----BEGIN PUBLIC KEY-----\nfixture\n-----END PUBLIC KEY-----",
        "vex_approvers": ["security-release"],
    }


def _bundle() -> dict[str, object]:
    return {
        "schema_version": 1,
        "image": {
            "index_digest": INDEX,
            "platform_digests": {"linux/amd64": AMD64, "linux/arm64": ARM64},
        },
        "scan": {
            "scanner": "trivy",
            "scanner_version": "fixture-1",
            "subject_digest": INDEX,
            "report_sha256": "0" * 64,
            "reachable_high": 0,
            "reachable_critical": 0,
            "reachable_findings": [],
        },
        "sbom": {
            "format": "spdx-json",
            "subject_digest": INDEX,
            "document_sha256": "0" * 64,
            "generator": "syft fixture-1",
        },
        "provenance": {
            "subject_digest": INDEX,
            "attestation_sha256": "0" * 64,
            "predicate_type": "https://slsa.dev/provenance/v1",
            "builder_id": "https://builder.example/release/v1",
            "source_revision": "e" * 40,
            "build_mode": "max",
        },
        "signature": {
            "subject_digest": INDEX,
            "algorithm": "ed25519",
            "bundle_sha256": "0" * 64,
        },
    }


def _artifacts(bundle: dict[str, object]) -> dict[str, bytes]:
    scan = bundle["scan"]
    artifacts = {
        "scan_report": _json_bytes(
            {
                "scanner": scan["scanner"],
                "subject_digest": scan["subject_digest"],
                "reachable_findings": scan["reachable_findings"],
            }
        ),
        "sbom": _json_bytes(
            {"subject_digest": bundle["sbom"]["subject_digest"], "format": "spdx-json"}
        ),
        "provenance": _json_bytes(
            {
                "subject_digest": bundle["provenance"]["subject_digest"],
                "predicate_type": bundle["provenance"]["predicate_type"],
                "builder_id": bundle["provenance"]["builder_id"],
                "source_revision": bundle["provenance"]["source_revision"],
            }
        ),
    }
    scan["report_sha256"] = hashlib.sha256(artifacts["scan_report"]).hexdigest()
    bundle["sbom"]["document_sha256"] = hashlib.sha256(artifacts["sbom"]).hexdigest()
    bundle["provenance"]["attestation_sha256"] = hashlib.sha256(
        artifacts["provenance"]
    ).hexdigest()
    if "vex" in bundle:
        artifacts["vex"] = _json_bytes(
            {
                "subject_digest": bundle["vex"]["subject_digest"],
                "approved_exceptions": bundle["vex"]["approved_exceptions"],
            }
        )
        bundle["vex"]["document_sha256"] = hashlib.sha256(artifacts["vex"]).hexdigest()
    signature = {
        "subject_digest": bundle["image"]["index_digest"],
        "payload_sha256": hashlib.sha256(
            validator.canonical_unsigned_payload(bundle)
        ).hexdigest(),
        "signing_key_spki_sha256": KEY_FINGERPRINT,
    }
    artifacts["signature_bundle"] = _json_bytes(signature)
    bundle["signature"]["bundle_sha256"] = hashlib.sha256(
        artifacts["signature_bundle"]
    ).hexdigest()
    return artifacts


class _FixtureVerifier(validator.ReleaseArtifactVerifier):
    """실제 운영 verifier가 아닌 DI 계약 시험용 parser."""

    def verify(self, *, artifacts, canonical_payload, policy):
        scan = json.loads(artifacts["scan_report"])
        sbom = json.loads(artifacts["sbom"])
        provenance = json.loads(artifacts["provenance"])
        signature = json.loads(artifacts["signature_bundle"])
        findings = tuple(
            sorted(
                (item["id"], item["severity"].lower(), item["artifact"])
                for item in scan["reachable_findings"]
            )
        )
        approved = ()
        if "vex" in artifacts:
            vex = json.loads(artifacts["vex"])
            approved = tuple(
                sorted(item["finding_id"] for item in vex["approved_exceptions"])
            )
        return validator.VerifiedReleaseArtifacts(
            scan_report_sha256=hashlib.sha256(artifacts["scan_report"]).hexdigest(),
            scan_subject_digest=scan["subject_digest"],
            scanner=scan["scanner"],
            findings=findings,
            approved_vex_finding_ids=approved,
            sbom_sha256=hashlib.sha256(artifacts["sbom"]).hexdigest(),
            sbom_subject_digest=sbom["subject_digest"],
            provenance_sha256=hashlib.sha256(artifacts["provenance"]).hexdigest(),
            provenance_subject_digest=provenance["subject_digest"],
            provenance_predicate_type=provenance["predicate_type"],
            provenance_builder_id=provenance["builder_id"],
            provenance_source_revision=provenance["source_revision"],
            signature_bundle_sha256=hashlib.sha256(
                artifacts["signature_bundle"]
            ).hexdigest(),
            signature_subject_digest=signature["subject_digest"],
            signed_payload_sha256=signature["payload_sha256"],
            signing_key_spki_sha256=signature["signing_key_spki_sha256"],
        )


def test_DI_fixture로_완전한_gate_계약만_검증한다() -> None:
    bundle = _bundle()
    artifacts = _artifacts(bundle)
    assert validator.evaluate_release(
        bundle=bundle,
        policy=_policy(),
        artifacts=artifacts,
        verifier=_FixtureVerifier(),
    ) == []


def test_구조helper_PASS는_독립verifier없이_공개PASS가_아니다() -> None:
    bundle = _bundle()
    artifacts = _artifacts(bundle)
    assert validator.validate_bundle(bundle, _policy()) == []
    joined = "\n".join(
        validator.evaluate_release(
            bundle=bundle, policy=_policy(), artifacts=artifacts, verifier=None
        )
    )
    assert "verifier" in joined
    assert "BLOCKED" in joined


def test_실제artifact와_repository_pin이_없는_CLI는_BLOCKED다(capsys) -> None:
    assert validator.main([]) == 78
    assert "BLOCKED" in capsys.readouterr().err
    assert validator.POLICY_PIN_PATH.read_text(encoding="ascii").strip() == "BLOCKED"


def test_main은_실제파일이_있어도_concrete_verifier없이는_BLOCKED다(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    bundle = _bundle()
    artifacts = _artifacts(bundle)
    policy_raw = _json_bytes(_policy())
    policy_path = tmp_path / "release-policy.json"
    pin_path = tmp_path / "release-policy.sha256"
    evidence_path = tmp_path / "evidence.json"
    policy_path.write_bytes(policy_raw)
    pin_path.write_text(hashlib.sha256(policy_raw).hexdigest(), encoding="ascii")
    evidence_path.write_bytes(_json_bytes(bundle))
    artifact_paths: dict[str, Path] = {}
    for name, raw in artifacts.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(raw)
        artifact_paths[name] = path
    monkeypatch.setattr(validator, "POLICY_PATH", policy_path)
    monkeypatch.setattr(validator, "POLICY_PIN_PATH", pin_path)
    arguments = [
        "--evidence",
        str(evidence_path),
        "--scan-report",
        str(artifact_paths["scan_report"]),
        "--sbom",
        str(artifact_paths["sbom"]),
        "--provenance",
        str(artifact_paths["provenance"]),
        "--signature-bundle",
        str(artifact_paths["signature_bundle"]),
    ]

    assert validator.main(arguments) == 78
    assert "verifier" in capsys.readouterr().err
    assert validator.main(arguments, artifact_verifier=_FixtureVerifier()) == 0


def test_완전히_꾸민_JSON도_실제artifact가_없으면_거부한다() -> None:
    bundle = _bundle()
    assert validator.validate_bundle(bundle, _policy()) == []
    joined = "\n".join(
        validator.evaluate_release(
            bundle=bundle,
            policy=_policy(),
            artifacts={},
            verifier=_FixtureVerifier(),
        )
    )
    for name in ("scan_report", "sbom", "provenance", "signature_bundle"):
        assert f"artifact.{name}" in joined


def test_tag_child_digest_singlearch를_final_index로_속일_수_없다() -> None:
    tag = _bundle()
    tag["image"]["index_digest"] = "registry.example/app:latest"
    assert "sha256 OCI digest" in "\n".join(validator.validate_bundle(tag, _policy()))

    child = _bundle()
    child["image"]["index_digest"] = AMD64
    assert "multi-arch index" in "\n".join(
        validator.validate_bundle(child, _policy())
    )

    single = _bundle()
    del single["image"]["platform_digests"]["linux/arm64"]
    assert "linux/arm64" in "\n".join(
        validator.validate_bundle(single, _policy())
    )


def test_scanner_raw목록과_evidence목록_불일치를_거부한다() -> None:
    bundle = _bundle()
    artifacts = _artifacts(bundle)
    report = json.loads(artifacts["scan_report"])
    report["reachable_findings"] = [
        {"id": "CVE-2099-0001", "severity": "critical", "artifact": "libfixture"}
    ]
    artifacts["scan_report"] = _json_bytes(report)
    bundle["scan"]["report_sha256"] = hashlib.sha256(
        artifacts["scan_report"]
    ).hexdigest()
    joined = "\n".join(
        validator.evaluate_release(
            bundle=bundle,
            policy=_policy(),
            artifacts=artifacts,
            verifier=_FixtureVerifier(),
        )
    )
    assert "scan high/critical finding" in joined


def test_JSON과_rawhash를_함께_바꿔도_서명payload가_낡으면_거부한다() -> None:
    bundle = _bundle()
    artifacts = _artifacts(bundle)
    bundle["provenance"]["source_revision"] = "1" * 40
    artifacts["provenance"] = _json_bytes(
        {
            "subject_digest": INDEX,
            "predicate_type": bundle["provenance"]["predicate_type"],
            "builder_id": bundle["provenance"]["builder_id"],
            "source_revision": "1" * 40,
        }
    )
    bundle["provenance"]["attestation_sha256"] = hashlib.sha256(
        artifacts["provenance"]
    ).hexdigest()
    joined = "\n".join(
        validator.evaluate_release(
            bundle=bundle,
            policy=_policy(),
            artifacts=artifacts,
            verifier=_FixtureVerifier(),
        )
    )
    assert "signature canonical payload" in joined


def test_reachable_high_critical은_승인_VEXartifact가_정확히_대응해야_한다() -> None:
    finding = {"id": "CVE-2099-0001", "severity": "critical", "artifact": "libfixture"}
    unapproved = _bundle()
    unapproved["scan"]["reachable_findings"] = [finding]
    unapproved["scan"]["reachable_critical"] = 1
    assert "승인 VEX 없는" in "\n".join(
        validator.validate_bundle(unapproved, _policy())
    )

    approved = copy.deepcopy(unapproved)
    approved["vex"] = {
        "subject_digest": INDEX,
        "document_sha256": "0" * 64,
        "approved_exceptions": [
            {
                "finding_id": "CVE-2099-0001",
                "status": "not_affected",
                "approved_by": "security-release",
                "statement_sha256": "d" * 64,
            }
        ],
    }
    artifacts = _artifacts(approved)
    assert validator.evaluate_release(
        bundle=approved,
        policy=_policy(),
        artifacts=artifacts,
        verifier=_FixtureVerifier(),
    ) == []


def test_SBOM_provenance_signature가_다른_subject면_모두_거부한다() -> None:
    for section in ("sbom", "provenance", "signature"):
        bundle = _bundle()
        bundle[section]["subject_digest"] = AMD64
        assert f"{section}.subject_digest" in "\n".join(
            validator.validate_bundle(bundle, _policy())
        )


def test_provenance_min모드와_placeholder_policy를_거부한다() -> None:
    bundle = _bundle()
    bundle["provenance"]["build_mode"] = "min"
    assert "mode=max" in "\n".join(validator.validate_bundle(bundle, _policy()))

    policy = _policy()
    policy["trusted_builder_id"] = "REPLACE_WITH_PINNED_BUILDER_ID"
    assert "placeholder" in "\n".join(validator.validate_bundle(_bundle(), policy))

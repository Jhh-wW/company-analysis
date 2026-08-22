"""독립 백업 manifest의 공격 경계 재검수.

운영 sink를 흉내 내어 PASS로 만들지 않는다. 로컬 sink가 증명할 수 있는 서명 체인,
재시작, 조건부 append와 fail-closed만 확인하고 외부 저장 경계는 문서에서 BLOCKED로
남긴다.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import backup_manifest as manifest


NOW = datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
CHECKSUM_DIGEST = "c" * 64


def _boundary() -> manifest.ManifestBoundary:
    return manifest.ManifestBoundary(
        boundary_id="independent-ledger",
        authority_id="independent-security",
        retention_days=35,
        append_only=True,
        signed=True,
        conditional_append=True,
        production_ready=False,
    )


def _components(path: Path):
    signer = manifest.HMACManifestSigner(
        key_id="recovery-key-v1",
        key=b"independent-test-signing-key-32-bytes-minimum",
    )
    sink = manifest.LocalAppendOnlyManifestSink(path, boundary=_boundary())
    ledger = manifest.ManifestLedger(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
        allow_test_sink=True,
    )
    gate = manifest.IndependentManifestGate(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
        allow_test_sink=True,
    )
    return signer, sink, ledger, gate


def _append(
    ledger: manifest.ManifestLedger,
    *,
    backup_id: str,
    database_name: str,
    digest: str,
    created_at: datetime,
) -> manifest.BackupManifestRecord:
    return ledger.append_backup(
        scope="storage-db",
        backup_id=backup_id,
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key=f"company-analysis/{database_name}",
        checksum_key=f"company-analysis/{database_name}.sha256",
        database_name=database_name,
        database_sha256=digest,
        database_size_bytes=4096,
        checksum_sha256=CHECKSUM_DIGEST,
        created_at=created_at,
        data_boundary_id="private-backup-bucket",
        data_authority_id="backup-writer",
    )


def _expectation(
    backup_id: str,
    minimum_sequence: int,
    database_name: str = "storage-backup.sqlite3",
) -> manifest.ManifestExpectation:
    return manifest.ManifestExpectation(
        backup_id=backup_id,
        scope="storage-db",
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key=f"company-analysis/{database_name}",
        checksum_key=f"company-analysis/{database_name}.sha256",
        data_boundary_id="private-backup-bucket",
        data_authority_id="backup-writer",
        minimum_sequence=minimum_sequence,
        now=NOW + timedelta(minutes=5),
    )


def test_재시작후_잘린_서명prefix는_최신_checkpoint로_거부한다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    _, _, ledger, _ = _components(manifest_path)
    first = _append(
        ledger,
        backup_id="backup-001",
        database_name="storage-backup-001.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    second = _append(
        ledger,
        backup_id="backup-002",
        database_name="storage-backup-002.sqlite3",
        digest=DIGEST_B,
        created_at=NOW + timedelta(seconds=1),
    )
    assert (first.sequence, second.sequence) == (1, 2)
    (tmp_path / "data").mkdir()

    # 새 프로세스를 흉내 낸 새 sink/gate도 온전한 원장에서는 최신 head를 승인한다.
    _, _, _, restarted_gate = _components(manifest_path)
    verified = restarted_gate.verify(
        expectation=_expectation("backup-002", 2, "storage-backup-002.sqlite3"),
        database_name="storage-backup-002.sqlite3",
        database_sha256=DIGEST_B,
        database_size_bytes=4096,
        checksum_sha256=CHECKSUM_DIGEST,
        data_root=tmp_path / "data",
    )
    assert verified.sequence == 2

    # 저장소 공격자가 마지막 줄을 잘라 유효하게 서명된 옛 prefix만 남긴다.
    lines = manifest_path.read_bytes().splitlines(keepends=True)
    manifest_path.write_bytes(lines[0])
    _, _, _, truncated_gate = _components(manifest_path)
    with pytest.raises(manifest.ManifestError, match="checkpoint"):
        truncated_gate.verify(
            expectation=_expectation("backup-001", 2, "storage-backup-001.sqlite3"),
            database_name="storage-backup-001.sqlite3",
            database_sha256=DIGEST_A,
            database_size_bytes=4096,
            checksum_sha256=CHECKSUM_DIGEST,
            data_root=tmp_path / "data",
        )


def test_동시_append는_중복sequence나_fork를_게시하지_않는다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    signer, sink, _, _ = _components(manifest_path)
    workers = 12
    barrier = threading.Barrier(workers)

    def attempt(index: int) -> bool:
        ledger = manifest.ManifestLedger(
            sink=manifest.LocalAppendOnlyManifestSink(
                manifest_path,
                boundary=_boundary(),
            ),
            signer=signer,
            minimum_retention_days=35,
            allow_test_sink=True,
        )
        barrier.wait()
        try:
            _append(
                ledger,
                backup_id=f"parallel-{index:02d}",
                database_name=f"storage-backup-parallel-{index:02d}.sqlite3",
                digest=f"{index + 1:064x}",
                created_at=NOW + timedelta(seconds=index + 1),
            )
        except manifest.ManifestError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(attempt, range(workers)))

    records = sink.read_records()
    manifest.validate_manifest_chain(records, boundary=_boundary(), signer=signer)
    assert 1 <= sum(outcomes) == len(records) <= workers
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))
    assert len({record.backup_id for record in records}) == len(records)


def test_DB경계안의_로컬manifest와_운영승격은_각각_fail_closed한다(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    manifest_path = data_root / "manifest.jsonl"
    signer, sink, ledger, test_gate = _components(manifest_path)
    _append(
        ledger,
        backup_id="same-boundary",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )

    with pytest.raises(manifest.ManifestError, match="데이터 경계 안"):
        test_gate.verify(
            expectation=_expectation("same-boundary", 1),
            database_name="storage-backup.sqlite3",
            database_sha256=DIGEST_A,
            database_size_bytes=4096,
            checksum_sha256=CHECKSUM_DIGEST,
            data_root=data_root,
        )
    with pytest.raises(manifest.ManifestError, match="로컬 시험"):
        manifest.IndependentManifestGate(
            sink=sink,
            signer=signer,
            minimum_retention_days=35,
        )


def test_서명변조와_객체binding오류는_거부하고_키원문은_노출하지_않는다(
    tmp_path: Path,
) -> None:
    secret = b"do-not-print-independent-secret-32-bytes"
    signer = manifest.HMACManifestSigner(key_id="key-v1", key=secret)
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    sink = manifest.LocalAppendOnlyManifestSink(manifest_path, boundary=_boundary())
    ledger = manifest.ManifestLedger(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
        allow_test_sink=True,
    )
    gate = manifest.IndependentManifestGate(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
        allow_test_sink=True,
    )
    _append(
        ledger,
        backup_id="bound-object",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    (tmp_path / "data").mkdir()

    for name, digest, size in (
        ("renamed.sqlite3", DIGEST_A, 4096),
        ("storage-backup.sqlite3", DIGEST_B, 4096),
        ("storage-backup.sqlite3", DIGEST_A, 4097),
    ):
        with pytest.raises(manifest.ManifestError):
            gate.verify(
                expectation=_expectation("bound-object", 1),
                database_name=name,
                database_sha256=digest,
                database_size_bytes=size,
                checksum_sha256=CHECKSUM_DIGEST,
                data_root=tmp_path / "data",
            )

    payload = json.loads(manifest_path.read_text(encoding="ascii"))
    payload["database_name"] = "attacker.sqlite3"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="ascii",
    )
    with pytest.raises(manifest.ManifestError, match="서명") as captured:
        gate.verify(
            expectation=_expectation("bound-object", 1),
            database_name="attacker.sqlite3",
            database_sha256=DIGEST_A,
            database_size_bytes=4096,
            checksum_sha256=CHECKSUM_DIGEST,
            data_root=tmp_path / "data",
        )
    assert secret.decode("ascii") not in repr(signer)
    assert secret not in manifest_path.read_bytes()
    assert secret.decode("ascii") not in str(captured.value)

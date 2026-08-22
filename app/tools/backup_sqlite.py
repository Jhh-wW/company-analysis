"""실행 중인 SQLite를 안전하게 백업하고 전송 무결성을 검증하는 명령줄 도구.

DB 파일을 일반 파일 복사로 가져가면 WAL에 아직 합쳐지지 않은 최신 기록을 놓칠 수
있다. 이 도구는 파이썬 표준 라이브러리의 ``sqlite3.Connection.backup`` API로
일관된 스냅샷을 만들고, 무결성 검사와 SHA-256 체크섬을 함께 남긴다.

DB와 같은 저장 경계의 체크섬만으로 운영 복구를 승인할 수는 없다. 공개 ``restore``
명령과 API는 독립 서명 manifest gate가 연결된 승인 wrapper가 마련될 때까지 항상
fail-closed하며 새 DB 파일을 게시하지 않는다.

백업에는 이메일·보고서·로그인 세션 등 개인정보가 있을 수 있다. 내용을 출력하지
않으며, 생성 파일을 공개 Git 저장소에 올려서는 안 된다.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Sequence

ENV_DB_PATH: Final[str] = "STORAGE_DB_PATH"
ENV_DATA_ROOT: Final[str] = "APP_DATA_ROOT"
DEFAULT_DB_FILENAME: Final[str] = "storage.db"
BACKUP_DIRNAME: Final[str] = "backups"
BACKUP_PREFIX: Final[str] = "storage-backup"
CHECKSUM_SUFFIX: Final[str] = ".sha256"
HASH_CHUNK_BYTES: Final[int] = 1024 * 1024
SQLITE_TIMEOUT_SEC: Final[float] = 10.0
PRIVATE_FILE_MODE: Final[int] = 0o600
PRIVATE_DIR_MODE: Final[int] = 0o700
SQLITE_COMPANION_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm", "-journal")

APP_ROOT = Path(__file__).resolve().parents[1]


class BackupError(RuntimeError):
    """안전한 백업·검증·복구를 완료하지 못했을 때 발생한다."""


@dataclass(frozen=True)
class BackupResult:
    backup_path: Path
    checksum_path: Path
    sha256: str


def default_db_path() -> Path:
    """앱이 쓰는 DB 기본 경로를 환경변수 우선순위에 맞춰 찾는다."""
    explicit = os.environ.get(ENV_DB_PATH, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    data_root = os.environ.get(ENV_DATA_ROOT, "").strip()
    if data_root:
        return Path(data_root).expanduser() / DEFAULT_DB_FILENAME
    return APP_ROOT / "data" / DEFAULT_DB_FILENAME


def checksum_path_for(database_path: Path) -> Path:
    return database_path.with_name(database_path.name + CHECKSUM_SUFFIX)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _live_readonly_connection(path: Path) -> sqlite3.Connection:
    """실행 중 source의 WAL까지 SQLite Backup API가 일관되게 읽는다."""

    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SEC)


def _assert_no_sqlite_companions(path: Path) -> None:
    for suffix in SQLITE_COMPANION_SUFFIXES:
        try:
            os.lstat(Path(str(path) + suffix))
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError("백업 산출물 SQLite sidecar 상태를 확인하지 못했습니다.") from exc
        raise BackupError("백업 산출물에 독립 manifest가 결속하지 않은 SQLite sidecar가 있습니다.")


def _remove_backup_artifacts(
    database_path: Path,
    checksum_path: Path | None = None,
) -> None:
    """실패한 백업의 정확한 산출물 경로만 지운다.

    glob이나 디렉터리 재귀 삭제를 쓰지 않는다. SQLite가 main DB 옆에 만들 수 있는
    정해진 companion과, 호출자가 명시한 체크섬 파일만 각각 unlink한다.
    한 파일 정리에 실패해도 나머지 경로의 정리는 계속 시도한다.
    """

    artifacts = [
        database_path,
        *(Path(str(database_path) + suffix) for suffix in SQLITE_COMPANION_SUFFIXES),
    ]
    if checksum_path is not None:
        artifacts.append(checksum_path)

    first_error: OSError | None = None
    for artifact in artifacts:
        try:
            artifact.unlink(missing_ok=True)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise BackupError("실패한 백업 산출물을 완전히 정리하지 못했습니다.") from first_error


def _standalone_readonly_connection(path: Path) -> sqlite3.Connection:
    """완료된 main DB 한 파일만 읽고 sidecar는 절대 적용하지 않는다."""

    _assert_no_sqlite_companions(path)
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SEC)
    try:
        _assert_no_sqlite_companions(path)
    except BaseException:
        connection.close()
        raise
    return connection


def _assert_database_is_valid(path: Path) -> None:
    """행 내용은 노출하지 않고 SQLite 자체 무결성과 외래키만 검사한다."""
    if not path.is_file() or path.stat().st_size == 0:
        raise BackupError("DB 파일이 없거나 비어 있습니다.")
    try:
        # sqlite3.Connection의 ``with``는 트랜잭션만 끝내고 연결은 닫지 않는다.
        # Windows에서도 곧바로 파일을 옮길 수 있도록 closing을 함께 쓴다.
        with closing(_standalone_readonly_connection(path)) as conn:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            if integrity_rows != [("ok",)]:
                raise BackupError("SQLite 무결성 검사를 통과하지 못했습니다.")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError("SQLite 외래키 검사를 통과하지 못했습니다.")
            share_table = conn.execute(
                "SELECT type FROM sqlite_master WHERE name='share_links'"
            ).fetchone()
            if share_table is not None:
                columns = {
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_xinfo("share_links")'
                    ).fetchall()
                }
                if "key" in columns or "key_hash" not in columns:
                    raise BackupError(
                        "공유 링크 원문 열쇠가 남은 구형 DB는 백업하지 않습니다. "
                        "앱 마이그레이션을 먼저 실행하세요."
                    )
    except sqlite3.Error as exc:
        raise BackupError("올바르고 읽을 수 있는 SQLite DB가 아닙니다.") from exc
    _assert_no_sqlite_companions(path)


def _private_chmod(path: Path) -> None:
    """POSIX에서만 소유자 읽기·쓰기로 제한한다.

    Windows의 ``chmod``는 ACL을 owner-only로 바꾸지 못한다. 성공하는 척하지 않고
    암호화 저장소 사용 책임을 CLI와 문서에서 분명히 알린다.
    """
    if os.name != "posix":
        return
    try:
        path.chmod(PRIVATE_FILE_MODE)
    except OSError as exc:
        raise BackupError("백업 파일 권한을 안전하게 제한하지 못했습니다.") from exc


def _write_checksum(path: Path, digest: str, database_name: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(
            temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            PRIVATE_FILE_MODE,
        )
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            stream.write(f"{digest}  {database_name}\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise BackupError("같은 이름의 체크섬 파일이 이미 있습니다.")
        os.replace(temp_path, path)
        _private_chmod(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_expected_checksum(path: Path) -> str:
    if not path.is_file():
        raise BackupError("체크섬 파일이 없습니다. DB와 .sha256 파일을 함께 준비하세요.")
    try:
        first_line = path.read_text(encoding="ascii").splitlines()[0]
        expected = first_line.split()[0].lower()
    except (IndexError, UnicodeError, OSError) as exc:
        raise BackupError("체크섬 파일을 읽을 수 없습니다.") from exc
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise BackupError("체크섬 파일 형식이 올바르지 않습니다.")
    return expected


def verify_backup(
    backup_path: Path,
    checksum_path: Path | None = None,
) -> str:
    """체크섬과 SQLite 내부 검사를 모두 통과하면 실제 SHA-256을 돌려준다."""
    resolved_backup = backup_path.expanduser()
    resolved_checksum = checksum_path or checksum_path_for(resolved_backup)
    expected = _read_expected_checksum(resolved_checksum)
    if not resolved_backup.is_file():
        raise BackupError("백업 DB 파일이 없습니다.")
    _assert_no_sqlite_companions(resolved_backup)
    actual = sha256_file(resolved_backup)
    _assert_no_sqlite_companions(resolved_backup)
    if not hmac.compare_digest(actual, expected):
        raise BackupError("체크섬이 맞지 않습니다. 손상되었거나 다른 백업 파일입니다.")
    _assert_database_is_valid(resolved_backup)
    _assert_no_sqlite_companions(resolved_backup)
    return actual


def _backup_filename(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{timestamp}.sqlite3"


def create_backup(
    source_path: Path,
    output_dir: Path | None = None,
    *,
    prefix: str = BACKUP_PREFIX,
) -> BackupResult:
    """열려 있을 수 있는 원본 DB를 Backup API로 일관되게 백업한다."""
    source = source_path.expanduser()
    if not source.is_file():
        raise BackupError("원본 DB 파일이 없습니다.")

    destination_dir = (output_dir or source.parent / BACKUP_DIRNAME).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    destination = destination_dir / _backup_filename(prefix)
    checksum = checksum_path_for(destination)
    temp_path = destination_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"

    if source.resolve() == destination.resolve():
        raise BackupError("원본 DB와 백업 파일 경로가 같습니다.")
    if destination.exists() or checksum.exists():
        raise BackupError("같은 이름의 백업이 이미 있습니다.")

    try:
        with closing(_live_readonly_connection(source)) as source_conn:
            with closing(
                sqlite3.connect(str(temp_path), timeout=SQLITE_TIMEOUT_SEC)
            ) as target_conn:
                source_conn.backup(target_conn)
        _assert_no_sqlite_companions(temp_path)
        _private_chmod(temp_path)
        _assert_database_is_valid(temp_path)
        _assert_no_sqlite_companions(temp_path)
        digest = sha256_file(temp_path)
        _assert_no_sqlite_companions(temp_path)
        os.replace(temp_path, destination)
        try:
            _assert_no_sqlite_companions(destination)
            if not hmac.compare_digest(sha256_file(destination), digest):
                raise BackupError("게시된 백업 main DB 지문이 검증된 산출물과 다릅니다.")
            _assert_no_sqlite_companions(destination)
            _write_checksum(checksum, digest, destination.name)
            _assert_no_sqlite_companions(destination)
            if not hmac.compare_digest(sha256_file(destination), digest):
                raise BackupError("체크섬 게시 뒤 백업 main DB 지문이 변경됐습니다.")
            _assert_no_sqlite_companions(destination)
        except Exception:
            _remove_backup_artifacts(destination, checksum)
            raise
    except sqlite3.Error as exc:
        raise BackupError("SQLite Backup API로 백업하지 못했습니다.") from exc
    finally:
        _remove_backup_artifacts(temp_path)

    return BackupResult(destination, checksum, digest)


def restore_backup(
    backup_path: Path,
    target_path: Path,
    checksum_path: Path | None = None,
) -> None:
    """sidecar만으로 새 DB를 게시하는 과거 공개 복구 경로를 차단한다.

    인자는 CLI 호환을 위해 유지하지만 어떤 파일도 읽거나 만들기 전에 닫힌다. 운영 복구는
    독립 sink의 서명 chain/head와 최신 checkpoint를 검증한 승인 manifest adapter
    wrapper 안에서만 구현해야 한다. expected hash, 로컬 receipt 파일, CLI flag는 공격자가
    DB와 함께 다시 만들 수 있으므로 이 함수의 승인 수단으로 받지 않는다.
    """

    del backup_path, target_path, checksum_path
    raise BackupError(
        "독립 서명 manifest gate가 없는 직접 복구는 차단됩니다. "
        "승인된 manifest adapter wrapper의 복구 절차를 사용하세요."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite 개인정보 DB를 백업하고 전송 무결성을 검증합니다."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    backup_parser = commands.add_parser("backup", help="실행 중인 DB를 안전하게 백업")
    backup_parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="원본 DB 경로(기본: STORAGE_DB_PATH 또는 APP_DATA_ROOT/storage.db)",
    )
    backup_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="백업 폴더(기본: 원본 DB 옆 backups 폴더)",
    )

    verify_parser = commands.add_parser("verify", help="백업 체크섬과 DB 무결성 검사")
    verify_parser.add_argument("backup", type=Path, help="검사할 .sqlite3 파일")
    verify_parser.add_argument("--checksum", type=Path, default=None, help=".sha256 파일")

    restore_parser = commands.add_parser(
        "restore",
        help="독립 manifest wrapper가 없어 항상 차단되는 과거 호환 명령",
    )
    restore_parser.add_argument("backup", type=Path, help="복구할 .sqlite3 파일")
    restore_parser.add_argument("--checksum", type=Path, default=None, help=".sha256 파일")
    restore_parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="복구할 새 DB 경로(이미 존재하는 파일은 거부)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.source or default_db_path(), args.output_dir)
            print(f"백업 완료: {result.backup_path.name}")
            print(f"검증 파일: {result.checksum_path.name}")
            print("주의: 개인정보가 들어 있을 수 있습니다. 공개 Git 업로드 금지.")
            if os.name == "nt":
                print("Windows 주의: 도구가 ACL을 제한하지 못하므로 암호화 저장소를 쓰세요.")
        elif args.command == "verify":
            verify_backup(args.backup, args.checksum)
            print("검증 통과: 체크섬과 SQLite 무결성이 정상입니다.")
        elif args.command == "restore":
            restore_backup(
                args.backup,
                args.target,
                args.checksum,
            )
        return 0
    except (BackupError, OSError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

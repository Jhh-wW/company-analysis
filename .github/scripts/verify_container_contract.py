"""빌드된 배포 이미지의 cron 진입점과 비밀 파일 부재를 검사한다."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Final, Sequence


REQUIRED_FILES: Final[tuple[str, ...]] = (
    "app/tools/backup_sqlite.py",
    "app/tools/internal_trigger.py",
    "app/tools/trigger_backup.py",
    "app/tools/trigger_maintenance.py",
    "app/tools/container_entrypoint.sh",
)
REQUIRED_MODULES: Final[tuple[str, ...]] = (
    "tools.trigger_backup",
    "tools.trigger_maintenance",
)
FORBIDDEN_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {"tests", "backups", "raw", "raw_filings", "__pycache__"}
)
FORBIDDEN_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".docx",
        ".xlsx",
        ".pdf",
        ".zip",
    }
)
SECRET_JSON_PREFIXES: Final[tuple[str, ...]] = (
    "client_secret",
    "credentials",
    "token",
    "oauth",
    "service-account",
)


class ContainerContractError(RuntimeError):
    """배포 이미지가 최소 실행·비밀 파일 계약을 어겼다."""


def _is_forbidden(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    lower_name = path.name.lower()
    return (
        lower_name == ".env"
        or lower_name.startswith(".env.")
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
        or (
            path.suffix.lower() == ".json"
            and lower_name.startswith(SECRET_JSON_PREFIXES)
        )
        or any(part in FORBIDDEN_DIR_NAMES for part in relative.parts)
    )


def verify_files(root: Path) -> None:
    """필수 cron 파일은 있고 비밀·로컬 산출물은 없는지 확인한다."""

    resolved = root.resolve()
    missing = [relative for relative in REQUIRED_FILES if not (resolved / relative).is_file()]
    if missing:
        raise ContainerContractError(f"필수 cron 모듈 누락: {missing}")

    forbidden = sorted(
        str(path.relative_to(resolved))
        for path in resolved.rglob("*")
        if path.is_file() and _is_forbidden(path, resolved)
    )
    if forbidden:
        raise ContainerContractError(f"금지된 로컬·비밀 파일: {forbidden}")


def verify_imports() -> None:
    """컨테이너의 실제 Python 경로에서 cron 진입점을 import한다."""

    for module in REQUIRED_MODULES:
        importlib.import_module(module)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/srv"))
    args = parser.parse_args(argv)
    verify_imports()
    verify_files(args.root)
    print("컨테이너 cron·비밀 파일 계약을 통과했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

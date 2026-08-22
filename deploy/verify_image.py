"""빌드된 이미지에 비밀·로컬 산출물이 없고 비-root인지 확인한다."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


SCAN_ROOT = Path("/srv")
REQUIRED_FILES = (
    Path("app/src/web/main.py"),
    Path("deploy/container_entrypoint.sh"),
    Path("deploy/container_healthcheck.py"),
    Path("deploy/validate_environment.py"),
)
FORBIDDEN = re.compile(
    r"(^|/)(?:\.env(?:\.|$)|\.secure_prompt(?:\.|$)|\.git(?:/|$)|tests?(?:/|$))"
    r"|(?:client[_-]?secret|credentials?|service[_-]?account|oauth|token).*(?:\.json$|\.ya?ml$)"
    r"|\.(?:pem|key|p12|pfx|db|sqlite|sqlite3|pdf|docx?|xlsx?)$",
    re.IGNORECASE,
)


def verify(root: Path = SCAN_ROOT) -> list[str]:
    errors: list[str] = []
    if os.geteuid() == 0:
        errors.append("컨테이너 유효 UID가 root입니다")
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"필수 런타임 파일 누락: {relative.as_posix()}")
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if FORBIDDEN.search(relative):
                errors.append(f"이미지 금지 파일: {relative}")
    return errors


def main() -> int:
    errors = verify()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("이미지 런타임 파일·비-root 계약 확인 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

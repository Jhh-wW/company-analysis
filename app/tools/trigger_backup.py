"""Render cron에서 웹 서비스의 인증된 외부 백업을 한 번 요청한다."""

from __future__ import annotations

import sys
from typing import Final, Sequence

from tools import internal_trigger


ENV_TRIGGER_URL: Final[str] = "BACKUP_TRIGGER_URL"
ENV_TRIGGER_SECRET: Final[str] = "BACKUP_TRIGGER_SECRET"
ENDPOINT_PATH: Final[str] = "/internal/backup/run"
TriggerError = internal_trigger.TriggerError


def _config_from_env() -> tuple[str, str]:
    return internal_trigger.load_exact_https_config(
        url_env=ENV_TRIGGER_URL,
        secret_env=ENV_TRIGGER_SECRET,
        endpoint_path=ENDPOINT_PATH,
    )


def trigger_once(
    url: str,
    secret: str,
    *,
    opener_factory: internal_trigger.OpenerFactory = internal_trigger.build_opener,
) -> dict:
    payload = internal_trigger.post_json(
        url=url,
        secret=secret,
        service_name="백업",
        user_agent="company-analysis-backup-cron/1",
        opener_factory=opener_factory,
    )
    if payload.get("status") != "ok":
        raise TriggerError("백업 서버가 완료 상태를 반환하지 않았습니다")
    digest = str(payload.get("sha256", ""))
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise TriggerError("백업 서버의 SHA-256 응답이 올바르지 않습니다")
    backup_id = str(payload.get("manifest_backup_id", ""))
    record_digest = str(payload.get("manifest_record_sha256", ""))
    sequence = payload.get("manifest_sequence")
    if len(backup_id) != 64 or any(
        char not in "0123456789abcdef" for char in backup_id
    ):
        raise TriggerError("백업 서버의 manifest backup_id가 올바르지 않습니다")
    if len(record_digest) != 64 or any(
        char not in "0123456789abcdef" for char in record_digest
    ):
        raise TriggerError("백업 서버의 manifest 레코드 지문이 올바르지 않습니다")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise TriggerError("백업 서버의 manifest sequence가 올바르지 않습니다")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("인자는 받지 않습니다.", file=sys.stderr)
        return 2
    try:
        url, secret = _config_from_env()
        payload = trigger_once(url, secret)
    except TriggerError as exc:
        print(f"외부 백업 실패: {exc}", file=sys.stderr)
        return 1
    print(
        "외부 백업 완료: "
        f"object={payload.get('object_key', '')} "
        f"sha256={payload['sha256']} "
        f"manifest_backup_id={payload['manifest_backup_id']} "
        f"manifest_sequence={payload['manifest_sequence']} "
        f"manifest_record_sha256={payload['manifest_record_sha256']} "
        f"deleted={int(payload.get('deleted_objects', 0))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""엔진 생성 epoch의 프로세스 단일 신원 계약.

배포 환경변수는 요청 중에 다시 읽지 않는다. 프로세스 시작 때 한 번 만든
canonical wire를 다시 파싱해 동결하고, 생성·캐시·출고는 그 객체의 digest를
영수증으로 운반한다. 이 계약 덕분에 같은 프로세스 안에서 A로 시작한 작업이
환경변수 변화만으로 B 결과처럼 저장되는 일을 막는다.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Final

from src.core import deployment_identity


ENGINE_BUILD_ID_CONTRACT_VERSION: Final[str] = "deployment-commit-v1"
ENGINE_BUILD_IDENTITY_WIRE_VERSION: Final[str] = "engine-build-identity-wire-v1"
UNKNOWN_BUILD_ID: Final[str] = "unknown"

_SEPARATOR: Final[str] = ":"
_WIRE_SEPARATOR: Final[str] = "|"
_LOWER_HEX: Final[frozenset[str]] = frozenset("0123456789abcdef")
_PROCESS_IDENTITY_LOCK = threading.RLock()
_PROCESS_IDENTITY: EngineBuildIdentity | None = None


@dataclass(frozen=True, slots=True)
class EngineBuildIdentity:
    """한 raw deployment snapshot에서 함께 파생한 final 신원."""

    deployment_revision: str
    build_id: str

    def __init_subclass__(cls, **_kwargs: object) -> None:
        """필드를 가로채는 하위 타입을 신원 영수증으로 쓰지 못하게 한다."""

        raise TypeError("EngineBuildIdentity는 상속할 수 없는 최종 신원 형식입니다")

    def __post_init__(self) -> None:
        revision = self.deployment_revision
        if revision:
            if (
                len(revision) != deployment_identity.COMMIT_FULL_LEN
                or revision != revision.lower()
                or any(letter not in _LOWER_HEX for letter in revision)
                or self.build_id != _namespace(revision)
            ):
                raise ValueError("엔진 빌드 신원과 배포 commit의 결속이 다릅니다")
        elif self.build_id != UNKNOWN_BUILD_ID:
            raise ValueError("배포 commit을 모르면 엔진 빌드 신원도 unknown이어야 합니다")

    @property
    def cache_usable(self) -> bool:
        return build_id_is_usable(self.build_id)

    @property
    def wire(self) -> str:
        """저장·전달 전에 반드시 다시 파싱할 canonical 직렬형."""

        return _WIRE_SEPARATOR.join(
            (
                ENGINE_BUILD_IDENTITY_WIRE_VERSION,
                self.deployment_revision,
                self.build_id,
            )
        )

    @property
    def epoch_digest(self) -> str:
        """캐시와 단일실행 행에 저장하는 process epoch 영수증."""

        return hashlib.sha256(self.wire.encode("ascii", errors="strict")).hexdigest()


class EngineBuildIdentityChangedError(RuntimeError):
    """생성 영수증과 현재 프로세스 epoch가 달라 권위를 줄 수 없다."""


def _namespace(full_commit: str) -> str:
    """contract version과 full commit을 손실 없이 결합한다."""

    return f"{ENGINE_BUILD_ID_CONTRACT_VERSION}{_SEPARATOR}{full_commit}"


def parse_engine_build_identity_wire(wire: str) -> EngineBuildIdentity:
    """canonical wire만 exact base 객체로 되살린다."""

    if not isinstance(wire, str):
        raise TypeError("엔진 빌드 신원 wire는 문자열이어야 합니다")
    parts = wire.split(_WIRE_SEPARATOR)
    if len(parts) != 3 or parts[0] != ENGINE_BUILD_IDENTITY_WIRE_VERSION:
        raise ValueError("엔진 빌드 신원 wire 형식이 올바르지 않습니다")
    parsed = EngineBuildIdentity(parts[1], parts[2])
    if parsed.wire != wire:
        raise ValueError("엔진 빌드 신원 wire가 canonical 형식이 아닙니다")
    return parsed


def require_exact_engine_build_identity(
    identity: EngineBuildIdentity,
) -> EngineBuildIdentity:
    """하위 타입·가짜 속성·비canonical 값을 wire 왕복으로 거절한다."""

    if type(identity) is not EngineBuildIdentity:
        raise TypeError("정확한 EngineBuildIdentity 생성 영수증이 필요합니다")
    parsed = parse_engine_build_identity_wire(identity.wire)
    if parsed != identity or parsed.epoch_digest != identity.epoch_digest:
        raise ValueError("엔진 빌드 신원 wire 왕복 검증에 실패했습니다")
    return parsed


def capture_engine_build_identity(
    snapshot: deployment_identity.DeploymentIdentitySnapshot | None = None,
) -> EngineBuildIdentity:
    """bootstrap·시험용 raw snapshot을 canonical 객체로 만든다.

    요청 처리 코드는 이 함수를 부르지 않고
    :func:`process_engine_build_identity`가 돌려주는 동결값만 써야 한다.
    """

    identity = snapshot or deployment_identity.capture_deployment_identity()
    full_commit = identity.commit
    build = _namespace(full_commit) if full_commit else UNKNOWN_BUILD_ID
    return require_exact_engine_build_identity(EngineBuildIdentity(full_commit, build))


def freeze_process_engine_build_identity(
    identity: EngineBuildIdentity | None = None,
    *,
    snapshot: deployment_identity.DeploymentIdentitySnapshot | None = None,
) -> EngineBuildIdentity:
    """프로세스가 쓸 단 하나의 epoch를 최초 호출에서 영구 동결한다."""

    if identity is not None and snapshot is not None:
        raise TypeError("신원 객체와 raw snapshot을 동시에 지정할 수 없습니다")
    global _PROCESS_IDENTITY
    with _PROCESS_IDENTITY_LOCK:
        # 인자를 생략한 idempotent bootstrap은 raw 환경을 두 번 읽지 않는다.
        # 최초 요청 두 개가 동시에 들어와도 capture 자체를 lock 안에 두어 서로
        # 다른 A/B snapshot을 후보로 만드는 틈을 없앤다.
        if identity is None and snapshot is None and _PROCESS_IDENTITY is not None:
            return _PROCESS_IDENTITY
        candidate = (
            require_exact_engine_build_identity(identity)
            if identity is not None
            else (
                capture_engine_build_identity()
                if snapshot is None
                else capture_engine_build_identity(snapshot)
            )
        )
        if _PROCESS_IDENTITY is None:
            _PROCESS_IDENTITY = candidate
        elif _PROCESS_IDENTITY != candidate:
            raise EngineBuildIdentityChangedError(
                "이미 동결된 프로세스 엔진 신원을 다른 값으로 바꿀 수 없습니다"
            )
        return _PROCESS_IDENTITY


def process_engine_build_identity() -> EngineBuildIdentity:
    """프로세스 시작 때 동결한 신원을 돌려준다.

    CLI·단위시험처럼 lifespan이 없는 진입점도 최초 호출 한 번만 raw 환경을
    읽는다. 이후 요청에서는 환경이 변해도 같은 객체를 돌려준다.
    """

    with _PROCESS_IDENTITY_LOCK:
        current = _PROCESS_IDENTITY
    return freeze_process_engine_build_identity() if current is None else current


def engine_build_id(
    snapshot: deployment_identity.DeploymentIdentitySnapshot | None = None,
) -> str:
    """프로세스 namespace를 돌려주되 명시 snapshot은 독립 해석한다."""

    if snapshot is not None:
        return capture_engine_build_identity(snapshot).build_id
    return process_engine_build_identity().build_id


def build_id_is_usable(build_id: str) -> bool:
    """값 자체도 현재 계약의 canonical namespace인지 엄격히 확인한다."""

    if not isinstance(build_id, str):
        return False
    prefix = f"{ENGINE_BUILD_ID_CONTRACT_VERSION}{_SEPARATOR}"
    if not build_id.startswith(prefix):
        return False
    commit = build_id[len(prefix) :]
    return bool(
        len(commit) == deployment_identity.COMMIT_FULL_LEN
        and commit == commit.lower()
        and all(letter in _LOWER_HEX for letter in commit)
    )


def epoch_digest_is_valid(value: str) -> bool:
    """저장된 epoch 영수증이 canonical SHA-256인지 확인한다."""

    return bool(
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(letter in _LOWER_HEX for letter in value)
    )


def assert_engine_build_identity_current(
    frozen: EngineBuildIdentity,
) -> EngineBuildIdentity:
    """생성 영수증이 현재 동결 process epoch와 exact 일치하는지 확인한다."""

    frozen_exact = require_exact_engine_build_identity(frozen)
    raw_current = process_engine_build_identity()
    current = require_exact_engine_build_identity(raw_current)
    if current == frozen_exact:
        return raw_current
    if not frozen_exact.cache_usable and current.cache_usable:
        raise EngineBuildIdentityChangedError(
            "unknown 배포에서 시작한 결과를 정상 배포 epoch에 저장할 수 없습니다"
        )
    raise EngineBuildIdentityChangedError(
        "생성 영수증과 현재 프로세스의 동결 배포 신원이 다릅니다"
    )


def _reset_process_engine_build_identity_for_tests() -> None:
    """pytest 격리 전용. production 요청에서는 절대 호출하지 않는다."""

    global _PROCESS_IDENTITY
    with _PROCESS_IDENTITY_LOCK:
        _PROCESS_IDENTITY = None

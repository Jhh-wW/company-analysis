"""공식 IR PDF를 격리된 자식 프로세스에서 파싱하는 내부 실행 파일."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

_REQUIRED_FILTER_CAPS = (
    "ZLIB_MAX_OUTPUT_LENGTH",
    "LZW_MAX_OUTPUT_LENGTH",
    "RUN_LENGTH_MAX_OUTPUT_LENGTH",
    "MAX_DECLARED_STREAM_LENGTH",
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
    "JBIG2_MAX_OUTPUT_LENGTH",
)
RESOURCE_POLICY_STRICT = "strict"
RESOURCE_POLICY_LOCAL_WINDOWS = "local-windows"
RESOURCE_LIMITS_APPLIED = "applied"
RESOURCE_LIMITS_LOCAL_WINDOWS_FALLBACK = "local-windows-fallback"
FAILURE_RESOURCE_SETUP = "resource_limit_setup_failed"
FAILURE_RESOURCE_EXCEEDED = "resource_limit_exceeded"
_WINDOWS_JOB_HANDLE: object | None = None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--max-raw-chars", type=int, required=True)
    parser.add_argument("--max-root-recovery", type=int, required=True)
    parser.add_argument("--max-stream-bytes", type=int, required=True)
    parser.add_argument("--max-address-space-bytes", type=int, required=True)
    parser.add_argument("--max-cpu-seconds", type=int, required=True)
    parser.add_argument(
        "--resource-policy",
        choices=(RESOURCE_POLICY_STRICT, RESOURCE_POLICY_LOCAL_WINDOWS),
        required=True,
    )
    parser.add_argument("--expected-version", required=True)
    return parser.parse_args()


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    sys.stdout.flush()


def _emit_failure(detail: str, *, failure_kind: str = "parse_failed") -> None:
    _emit({"state": "failed", "failure_kind": failure_kind, "detail": detail})


def _apply_posix_limit(
    resource_module: object,
    resource_kind: int,
    *,
    soft_target: int,
    hard_target: int,
) -> None:
    """상속된 더 낮은 한계는 보존하면서 요청 상한보다 높지 않게 잠근다."""

    infinity = getattr(resource_module, "RLIM_INFINITY")
    _current_soft, current_hard = resource_module.getrlimit(resource_kind)
    effective_hard = (
        hard_target
        if current_hard == infinity
        else min(int(current_hard), hard_target)
    )
    effective_soft = min(soft_target, effective_hard)
    if effective_soft < 1 or effective_hard < 1:
        raise RuntimeError("상속된 OS 자원 상한이 올바르지 않습니다")
    resource_module.setrlimit(resource_kind, (effective_soft, effective_hard))
    actual_soft, actual_hard = resource_module.getrlimit(resource_kind)
    if (
        actual_soft == infinity
        or actual_hard == infinity
        or int(actual_soft) > soft_target
        or int(actual_hard) > hard_target
    ):
        raise RuntimeError("OS 자원 상한 적용 결과를 확인하지 못했습니다")


def _configure_posix_resource_limits(
    max_address_space_bytes: int,
    max_cpu_seconds: int,
    *,
    resource_module: object | None = None,
) -> None:
    """Linux/Unix 워커에 주소공간 hard limit와 CPU soft/hard limit를 건다."""

    if resource_module is None:
        import resource as resource_module  # type: ignore[no-redef]

    if not hasattr(resource_module, "RLIMIT_AS") or not hasattr(
        resource_module, "RLIMIT_CPU"
    ):
        raise RuntimeError("필수 POSIX 자원 상한을 지원하지 않습니다")
    _apply_posix_limit(
        resource_module,
        getattr(resource_module, "RLIMIT_AS"),
        soft_target=max_address_space_bytes,
        hard_target=max_address_space_bytes,
    )
    # soft limit에서 SIGXCPU로 먼저 종료하고, 신호 처리가 훼손돼도 1초 뒤
    # hard limit이 SIGKILL로 끝낸다.
    _apply_posix_limit(
        resource_module,
        getattr(resource_module, "RLIMIT_CPU"),
        soft_target=max_cpu_seconds,
        hard_target=max_cpu_seconds + 1,
    )


def _configure_windows_job_limits(
    max_address_space_bytes: int,
    max_cpu_seconds: int,
) -> None:
    """현재 Windows 워커를 메모리·CPU hard limit Job Object에 넣는다."""

    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    information = _ExtendedLimitInformation()
    information.BasicLimitInformation.PerProcessUserTimeLimit = (
        max_cpu_seconds * 10_000_000
    )
    information.BasicLimitInformation.LimitFlags = (
        0x00000002  # JOB_OBJECT_LIMIT_PROCESS_TIME
        | 0x00000100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | 0x00000200  # JOB_OBJECT_LIMIT_JOB_MEMORY
        | 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    information.ProcessMemoryLimit = max_address_space_bytes
    information.JobMemoryLimit = max_address_space_bytes
    if not kernel32.SetInformationJobObject(
        handle,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)
    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)

    global _WINDOWS_JOB_HANDLE
    _WINDOWS_JOB_HANDLE = handle


def _configure_os_resource_limits(
    max_address_space_bytes: int,
    max_cpu_seconds: int,
    *,
    resource_policy: str,
    platform_name: str | None = None,
) -> str:
    """플랫폼별 OS 강제 상한을 적용하고 검증 가능한 상태를 반환한다."""

    if (
        max_address_space_bytes < 64 * 1024 * 1024
        or max_cpu_seconds < 1
        or resource_policy not in {
            RESOURCE_POLICY_STRICT,
            RESOURCE_POLICY_LOCAL_WINDOWS,
        }
    ):
        raise RuntimeError("PDF 워커 OS 자원 상한 인자가 올바르지 않습니다")
    platform = platform_name or os.name
    if platform == "posix":
        _configure_posix_resource_limits(
            max_address_space_bytes,
            max_cpu_seconds,
        )
        return RESOURCE_LIMITS_APPLIED
    if platform == "nt":
        try:
            _configure_windows_job_limits(
                max_address_space_bytes,
                max_cpu_seconds,
            )
        except (OSError, RuntimeError):
            # Windows 개발 PC는 상위 IDE/보안제품의 Job 안에서 중첩 할당이 막힐
            # 수 있다. 이 명시적 로컬 정책에서만 기존 wall/file 상한으로 실행하고,
            # 배포 strict 정책에서는 같은 실패를 즉시 출고 차단한다.
            if resource_policy == RESOURCE_POLICY_LOCAL_WINDOWS:
                return RESOURCE_LIMITS_LOCAL_WINDOWS_FALLBACK
            raise
        return RESOURCE_LIMITS_APPLIED
    raise RuntimeError("지원하지 않는 OS에서는 PDF 워커를 실행할 수 없습니다")


def _load_pdf_runtime() -> tuple[object, object, object]:
    """OS 한계를 먼저 건 뒤에만 pypdf와 PdfReader를 메모리에 올린다."""

    import pypdf
    import pypdf.filters
    from pypdf import PdfReader

    return pypdf, pypdf.filters, PdfReader


def _configure_filter_caps(
    max_output_bytes: int,
    *,
    filters_module: object | None = None,
) -> None:
    """고정 pypdf가 제공해야 하는 모든 스트림 출력 상한을 같은 값으로 잠근다."""

    if max_output_bytes < 1:
        raise RuntimeError("PDF 스트림 상한이 올바르지 않습니다")
    if filters_module is None:
        _pypdf, filters_module, _reader = _load_pdf_runtime()
    missing = [
        name for name in _REQUIRED_FILTER_CAPS if not hasattr(filters_module, name)
    ]
    if missing:
        raise RuntimeError("고정 pypdf 스트림 상한 계약이 완전하지 않습니다")
    for name in _REQUIRED_FILTER_CAPS:
        setattr(filters_module, name, max_output_bytes)
    if any(
        getattr(filters_module, name, None) != max_output_bytes
        for name in _REQUIRED_FILTER_CAPS
    ):
        raise RuntimeError("고정 pypdf 스트림 상한을 적용하지 못했습니다")


def main() -> int:
    args = _arguments()
    try:
        resource_limits = _configure_os_resource_limits(
            args.max_address_space_bytes,
            args.max_cpu_seconds,
            resource_policy=args.resource_policy,
        )
    except (OSError, RuntimeError, ValueError):
        _emit_failure(
            "PDF 워커 OS 자원 상한 설정 실패",
            failure_kind=FAILURE_RESOURCE_SETUP,
        )
        return 0

    try:
        pypdf, filters_module, pdf_reader = _load_pdf_runtime()
    except MemoryError:
        _emit_failure(
            "PDF 워커 OS 메모리 상한 초과",
            failure_kind=FAILURE_RESOURCE_EXCEEDED,
        )
        return 0
    except Exception:
        _emit_failure("고정된 PDF 파서 실행 환경을 불러오지 못했습니다")
        return 0

    if getattr(pypdf, "__version__", "") != args.expected_version:
        _emit({"state": "failed", "detail": "고정된 pypdf 버전이 아닙니다"})
        return 0

    try:
        _configure_filter_caps(
            args.max_stream_bytes,
            filters_module=filters_module,
        )
    except RuntimeError:
        _emit({"state": "failed", "detail": "고정 pypdf 스트림 상한 계약 실패"})
        return 0

    content = sys.stdin.buffer.read(args.max_bytes + 1)
    if not content.startswith(b"%PDF-") or len(content) > args.max_bytes:
        _emit({"state": "failed", "detail": "PDF 입력 형식 또는 바이트 상한 위반"})
        return 0

    try:
        reader = pdf_reader(
            io.BytesIO(content),
            strict=True,
            root_object_recovery_limit=args.max_root_recovery,
        )
        if reader.is_encrypted:
            _emit({"state": "failed", "detail": "암호화된 PDF"})
            return 0
        page_count = len(reader.pages)
        if page_count < 1 or page_count > args.max_pages:
            _emit({"state": "failed", "detail": "PDF 페이지 상한 위반"})
            return 0

        pages: list[str] = []
        truncated_pages: list[int] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text(extraction_mode="layout") or ""
            if not isinstance(text, str):
                raise TypeError("PDF 글자 추출 결과가 문자열이 아닙니다")
            if len(text) > args.max_raw_chars:
                text = text[: args.max_raw_chars]
                truncated_pages.append(page_number)
            pages.append(text)
    except MemoryError:
        _emit_failure(
            "PDF 워커 OS 메모리 상한 초과",
            failure_kind=FAILURE_RESOURCE_EXCEEDED,
        )
        return 0
    except Exception:
        _emit({"state": "failed", "detail": "PDF 파싱 또는 글자 추출 실패"})
        return 0

    _emit(
        {
            "state": "ok",
            "pages": pages,
            "extractor": f"pypdf {getattr(pypdf, '__version__', '')}",
            "truncated_pages": truncated_pages,
            "resource_limits": resource_limits,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

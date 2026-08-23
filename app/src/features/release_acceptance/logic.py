"""실제 loopback HTTP와 격리 SQLite를 사용하는 릴리스 수락시험."""

from __future__ import annotations

import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import asdict, dataclass, field
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Mapping, TypeVar

from src.features.release_acceptance.constants import (
    CHECKS,
    CHECK_TITLES,
    DEFAULT_STARTUP_TIMEOUT_SEC,
    DEFAULT_WORKFLOW_TIMEOUT_SEC,
    DEMO_COMPANY,
    DEMO_REGION,
    DEPENDENCY_IMPORTS,
    EGRESS_AUDIT_COUNTER_KEYS,
    EGRESS_AUDIT_ENV_NAME,
    EGRESS_AUDIT_SCHEMA_VERSION,
    EXTERNAL_PROVIDER_ENV_NAMES,
    HTTP_TIMEOUT_SEC,
    LOOPBACK_HOST,
    MAX_EGRESS_AUDIT_BYTES,
    MAX_REDIRECT_HOPS,
    POLL_INTERVAL_SEC,
    PORT_CLOSE_TIMEOUT_SEC,
    REDIRECT_STATUS_CODES,
    REQUIRED_LEDGER_TABLES,
    SAFE_PARENT_ENV_NAMES,
    SCHEMA_VERSION,
)


class CheckStatus(str, Enum):
    """거짓 합격을 막는 세 가지 판정."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    title: str
    status: CheckStatus
    evidence: str
    duration_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True)
class AcceptanceReport:
    schema_version: str
    started_at: str
    finished_at: str
    overall_status: CheckStatus
    mode: str
    external_provider_calls_allowed: bool
    checks: tuple[CheckResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_status": self.overall_status.value,
            "mode": self.mode,
            "external_provider_calls_allowed": self.external_provider_calls_allowed,
            "checks": [item.as_dict() for item in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class RunConfig:
    app_root: Path
    python_executable: str
    startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC
    workflow_timeout_sec: float = DEFAULT_WORKFLOW_TIMEOUT_SEC


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str


@dataclass(frozen=True)
class WorkflowState:
    report_id: str
    attempt_token: str
    csrf_token: str
    form: dict[str, str]
    screen_body: bytes


@dataclass(frozen=True)
class OutputState:
    pdf_sha256: str
    release_sha256: str
    share_capability: str


@dataclass(frozen=True)
class StorageSnapshot:
    missing_tables: tuple[str, ...]
    reports_total: int
    target_reports: int
    target_payload_sha256: str
    budget_event_count: int
    budget_cost_krw: float
    inflight_count: int
    reserved_cost_krw: float
    ai_event_count: int
    ai_cost_krw: float
    cost_summary_count: int
    internal_ai_cost_krw: float
    customer_charge_krw: float
    record_lines: int
    target_record_lines: int
    invalid_record_lines: int

    @property
    def complete(self) -> bool:
        return not self.missing_tables and self.invalid_record_lines == 0

    def stable_projection(self) -> tuple[object, ...]:
        return tuple(asdict(self).values())


class AcceptanceFailure(RuntimeError):
    """실제 관측이 계약과 다를 때."""


class AcceptanceBlocked(RuntimeError):
    """선행 조건이 없어 실제 관측을 수행하지 못할 때."""


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def overall_status(checks: tuple[CheckResult, ...]) -> CheckStatus:
    statuses = {item.status for item in checks}
    if CheckStatus.FAIL in statuses:
        return CheckStatus.FAIL
    if CheckStatus.BLOCKED in statuses:
        return CheckStatus.BLOCKED
    return CheckStatus.PASS


def render_korean_summary(report: AcceptanceReport) -> str:
    lines = [
        f"릴리스 수락시험: {report.overall_status.value}",
        "외부 provider 호출 정책: 금지(OS 격리 증거는 provider 항목 참조)",
    ]
    for item in report.checks:
        lines.append(f"- {item.status.value} · {item.title}: {item.evidence}")
    return "\n".join(lines)


def contains_sensitive_marker(text: str | bytes, markers: Mapping[str, str]) -> bool:
    payload = text if isinstance(text, bytes) else text.encode("utf-8", errors="ignore")
    return any(
        value.encode("utf-8") in payload
        for value in markers.values()
        if value
    )


def build_child_environment(
    parent: Mapping[str, str], explicit: Mapping[str, str]
) -> dict[str, str]:
    """부모의 비밀 이름을 열거하지 않고 OS allowlist만 복사한다."""

    child = {
        name: str(parent[name])
        for name in SAFE_PARENT_ENV_NAMES
        if parent.get(name)
    }
    child.update({str(name): str(value) for name, value in explicit.items()})
    return child


def _missing_module(text: str | bytes) -> str:
    decoded = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    matched = re.search(r"No module named ['\"]([^'\"]+)['\"]", decoded)
    return matched.group(1) if matched else ""


class _InputParser(HTMLParser):
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.value = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.value or tag.lower() != "input":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if values.get("name") == self.name:
            self.value = values.get("value", "")


class _HrefParser(HTMLParser):
    def __init__(self, expected: str):
        super().__init__()
        self.expected = expected
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        self.found = self.found or values.get("href") == self.expected


def extract_input_value(html: str, name: str) -> str:
    parser = _InputParser(name)
    parser.feed(html)
    return parser.value


def _has_href(html: str, expected: str) -> bool:
    parser = _HrefParser(expected)
    parser.feed(html)
    return parser.found


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class HttpSession:
    """외부 URL을 만들 수 없는 loopback 전용 표준 라이브러리 세션."""

    def __init__(self, port: int):
        self.port = port
        self.base_url = f"http://{LOOPBACK_HOST}:{port}"
        self.cookies = http.cookiejar.CookieJar()

    def _redirect_target(self, current_url: str, location: str) -> str:
        raw_location = location.strip()
        if (
            not raw_location
            or raw_location != location
            or raw_location.startswith("//")
            or "#" in raw_location
            or any(ord(character) < 32 or ord(character) == 127 for character in raw_location)
        ):
            raise AcceptanceFailure("redirect Location이 안전한 loopback URL이 아닙니다")
        try:
            target = urllib.parse.urljoin(current_url, raw_location)
            parsed = urllib.parse.urlsplit(target)
            target_port = parsed.port
        except ValueError as error:
            raise AcceptanceFailure("redirect Location을 해석할 수 없습니다") from error
        expected_netloc = f"{LOOPBACK_HOST}:{self.port}"
        if (
            parsed.scheme != "http"
            or parsed.netloc != expected_netloc
            or parsed.hostname != LOOPBACK_HOST
            or target_port != self.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise AcceptanceFailure("redirect가 최초 loopback origin을 벗어났습니다")
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
        )

    @staticmethod
    def _redirect_method(
        status: int,
        method: str,
        body: bytes | None,
    ) -> tuple[str, bytes | None]:
        if status == 303 and method != "HEAD":
            return "GET", None
        if status in {301, 302} and method == "POST":
            return "GET", None
        return method, body

    def request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = False,
        timeout: float = HTTP_TIMEOUT_SEC,
    ) -> HttpResponse:
        if not path.startswith("/"):
            raise ValueError("loopback 상대 경로만 허용합니다")
        body: bytes | None = None
        request_headers = {
            "User-Agent": "release-acceptance-local/1",
            **(dict(headers) if headers else {}),
        }
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        # 수락시험 HTTP는 격리 loopback만 향한다. 기본 ProxyHandler는 부모의
        # HTTP(S)_PROXY/ALL_PROXY를 읽어 로컬 실패나 응답을 프록시가 위조하게 만들 수
        # 있으므로, 빈 명시 설정으로 환경 프록시 상속을 완전히 끊는다.
        handlers: list[object] = [
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.cookies),
        ]
        # 자동 redirect는 한 hop이라도 검증 전에 socket을 열 수 있으므로 항상 끈다.
        handlers.append(_NoRedirectHandler())
        opener = urllib.request.build_opener(*handlers)
        current_url = self.base_url + path
        current_method = method.upper()
        current_body = body
        visited = {current_url}
        for redirect_count in range(MAX_REDIRECT_HOPS + 1):
            hop_headers = dict(request_headers)
            if current_body is None:
                hop_headers.pop("Content-Type", None)
            request = urllib.request.Request(
                current_url,
                data=current_body,
                headers=hop_headers,
                method=current_method,
            )
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                payload = response.read()
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                result = HttpResponse(
                    status=int(response.status),
                    headers=response_headers,
                    body=payload,
                    url=response.geturl(),
                )
            if not follow_redirects or result.status not in REDIRECT_STATUS_CODES:
                return result
            location = result.headers.get("location", "")
            target = self._redirect_target(current_url, location)
            if target in visited:
                raise AcceptanceFailure("loopback redirect 순환을 거부했습니다")
            if redirect_count >= MAX_REDIRECT_HOPS:
                raise AcceptanceFailure("loopback redirect hop 상한을 넘었습니다")
            visited.add(target)
            current_url = target
            current_method, current_body = self._redirect_method(
                result.status,
                current_method,
                current_body,
            )
        raise AcceptanceFailure("loopback redirect 상태가 완결되지 않았습니다")


@dataclass
class LeakTracker:
    markers: dict[str, str]
    leaks: list[str] = field(default_factory=list)

    def add_marker(self, label: str, value: str) -> None:
        if value:
            self.markers[label] = value

    def scan_blob(self, surface: str, payload: bytes) -> None:
        for label, value in self.markers.items():
            if value and value.encode("utf-8") in payload:
                finding = f"{surface}:{label}"
                if finding not in self.leaks:
                    self.leaks.append(finding)

    def observe(
        self,
        response: HttpResponse,
        surface: str,
        *,
        scan_response_url: bool = True,
        ignored_header_names: frozenset[str] = frozenset(),
    ) -> None:
        self.scan_blob(surface + ":body", response.body)
        self.scan_blob(
            surface + ":headers",
            "\n".join(
                value
                for name, value in response.headers.items()
                if name.lower() not in ignored_header_names
            ).encode("utf-8", errors="ignore"),
        )
        if scan_response_url:
            self.scan_blob(
                surface + ":url", response.url.encode("utf-8", errors="ignore")
            )


def _loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((LOOPBACK_HOST, 0))
        return int(listener.getsockname()[1])


def _port_accepting(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK_HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def _port_bindable(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((LOOPBACK_HOST, port))
        return True
    except OSError:
        return False


def _wait_port_closed(port: int, timeout: float = PORT_CLOSE_TIMEOUT_SEC) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_accepting(port) and _port_bindable(port):
            return True
        time.sleep(0.1)
    return not _port_accepting(port) and _port_bindable(port)


class ManagedServer:
    """로그를 임시 파일에 가두고 uvicorn을 확실히 회수한다."""

    def __init__(
        self,
        *,
        app_root: Path,
        python_executable: str,
        port: int,
        environment: Mapping[str, str],
        log_path: Path,
        startup_timeout_sec: float,
    ):
        self.app_root = app_root
        self.python_executable = python_executable
        self.port = port
        self.environment = dict(environment)
        self.log_path = log_path
        self.startup_timeout_sec = startup_timeout_sec
        self.process: subprocess.Popen[bytes] | None = None
        self._log_file = None

    def start(self) -> None:
        self._log_file = self.log_path.open("wb")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [
                self.python_executable,
                "-m",
                "src.features.release_acceptance.child_server",
            ],
            cwd=str(self.app_root),
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        deadline = time.monotonic() + self.startup_timeout_sec
        probe = HttpSession(self.port)
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AcceptanceFailure("서버 프로세스가 준비 전에 종료되었습니다")
            try:
                if probe.request("GET", "/healthz", timeout=1.0).status == 200:
                    return
            except (OSError, urllib.error.URLError, TimeoutError):
                pass
            time.sleep(0.1)
        raise AcceptanceFailure("서버 시작 제한시간을 넘겼습니다")

    def stop(self) -> bool:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._log_file is not None:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None
        return _wait_port_closed(self.port)

    def logs(self) -> bytes:
        if self._log_file is not None:
            self._log_file.flush()
        try:
            return self.log_path.read_bytes()
        except OSError:
            return b""


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    database: Path
    records: Path
    cache: Path
    temp: Path


def _runtime_paths(root: Path) -> RuntimePaths:
    paths = RuntimePaths(
        root=root,
        database=root / "storage.db",
        records=root / "observability" / "runs.jsonl",
        cache=root / "cache" / "tldextract",
        temp=root / "tmp",
    )
    paths.records.parent.mkdir(parents=True, exist_ok=True)
    paths.cache.mkdir(parents=True, exist_ok=True)
    paths.temp.mkdir(parents=True, exist_ok=True)
    return paths


def _runtime_environment(
    paths: RuntimePaths,
    *,
    port: int,
    auth_capability: str,
    provenance_seal: str,
    egress_audit_path: Path,
) -> dict[str, str]:
    closed_proxy = "http://127.0.0.1:9"
    explicit = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PIPELINE": "demo",
        "BETA_ADMIN_ONLY": "0",
        "LOCAL_DEMO_AUTH": "1",
        "LOCAL_DEMO_AUTH_TOKEN": auth_capability,
        "AUTH_COOKIE_INSECURE": "1",
        "ADMIN_EMAILS": "release-acceptance-admin@example.invalid",
        "PORT": str(port),
        EGRESS_AUDIT_ENV_NAME: str(egress_audit_path),
        "APP_DATA_ROOT": str(paths.root),
        "STORAGE_DB_PATH": str(paths.database),
        "OBSERVABILITY_RECORDS_PATH": str(paths.records),
        "TLDEXTRACT_CACHE": str(paths.cache),
        "PROVENANCE_SEAL_SECRET": provenance_seal,
        "TEMP": str(paths.temp),
        "TMP": str(paths.temp),
        "TMPDIR": str(paths.temp),
        "HTTP_PROXY": closed_proxy,
        "HTTPS_PROXY": closed_proxy,
        "ALL_PROXY": closed_proxy,
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "http_proxy": closed_proxy,
        "https_proxy": closed_proxy,
        "all_proxy": closed_proxy,
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    return build_child_environment(os.environ, explicit)


def _probe_dependencies(config: RunConfig, paths: RuntimePaths) -> str:
    imports = ", ".join(DEPENDENCY_IMPORTS)
    environment = build_child_environment(
        os.environ,
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "TEMP": str(paths.temp),
            "TMP": str(paths.temp),
            "TMPDIR": str(paths.temp),
        },
    )
    try:
        completed = subprocess.run(
            [config.python_executable, "-c", f"import {imports}"],
            cwd=str(config.app_root),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except FileNotFoundError as error:
        raise AcceptanceBlocked("지정한 Python 실행 파일을 찾지 못했습니다") from error
    except subprocess.TimeoutExpired as error:
        raise AcceptanceBlocked("로컬 의존성 확인이 제한시간을 넘겼습니다") from error
    if completed.returncode != 0:
        missing = _missing_module(completed.stderr + completed.stdout)
        detail = f"필수 로컬 패키지 '{missing}'이 없습니다" if missing else "필수 로컬 패키지를 가져오지 못했습니다"
        raise AcceptanceBlocked(detail)
    return f"서버·PDF에 필요한 로컬 패키지 {len(DEPENDENCY_IMPORTS)}개를 가져왔습니다"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _count_sum(
    conn: sqlite3.Connection, table: str, column: str
) -> tuple[int, float]:
    row = conn.execute(
        f'SELECT COUNT(*), COALESCE(SUM("{column}"), 0) FROM "{table}"'
    ).fetchone()
    return int(row[0]), float(row[1])


def storage_snapshot(
    database: Path, records: Path, report_id: str
) -> StorageSnapshot:
    """격리 DB를 query-only로 읽어 비용·중복 불변식의 근거를 만든다."""

    if not database.is_file():
        raise AcceptanceBlocked("격리 SQLite 저장본이 없습니다")
    uri = database.resolve().as_uri() + "?mode=ro"
    # sqlite3.Connection의 context manager는 transaction만 닫고 handle은 닫지
    # 않는다. Windows에서 임시 storage.db 삭제가 막히지 않게 closing을 쓴다.
    with closing(sqlite3.connect(uri, uri=True, timeout=3.0)) as conn:
        conn.execute("PRAGMA query_only=ON")
        tables = _table_names(conn)
        missing = tuple(sorted(REQUIRED_LEDGER_TABLES - tables))

        reports_total = 0
        target_reports = 0
        payload_sha256 = ""
        if "reports" in tables:
            reports_total = int(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
            rows = conn.execute(
                "SELECT payload_json FROM reports WHERE report_id = ?", (report_id,)
            ).fetchall()
            target_reports = len(rows)
            if len(rows) == 1:
                payload_sha256 = hashlib.sha256(str(rows[0][0]).encode("utf-8")).hexdigest()

        budget_event_count, budget_cost = (0, 0.0)
        if "budget_spend_events" in tables:
            budget_event_count, budget_cost = _count_sum(
                conn, "budget_spend_events", "cost_krw"
            )
        inflight_count, reserved_cost = (0, 0.0)
        if "budget_spend_inflight" in tables:
            inflight_count, reserved_cost = _count_sum(
                conn, "budget_spend_inflight", "reserved_krw"
            )
        ai_event_count, ai_cost = (0, 0.0)
        if "ai_variable_cost_events" in tables:
            ai_event_count, ai_cost = _count_sum(
                conn, "ai_variable_cost_events", "cost_krw"
            )
        summary_count = 0
        internal_cost = 0.0
        customer_charge = 0.0
        if "report_cost_summaries" in tables:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(internal_ai_cost_krw), 0), "
                "COALESCE(SUM(customer_charge_krw), 0) FROM report_cost_summaries"
            ).fetchone()
            summary_count = int(row[0])
            internal_cost = float(row[1])
            customer_charge = float(row[2])

    record_lines = 0
    target_record_lines = 0
    invalid_record_lines = 0
    if records.is_file():
        for raw in records.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record_lines += 1
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                invalid_record_lines += 1
                continue
            if isinstance(item, dict) and item.get("run_id") == report_id:
                target_record_lines += 1

    return StorageSnapshot(
        missing_tables=missing,
        reports_total=reports_total,
        target_reports=target_reports,
        target_payload_sha256=payload_sha256,
        budget_event_count=budget_event_count,
        budget_cost_krw=budget_cost,
        inflight_count=inflight_count,
        reserved_cost_krw=reserved_cost,
        ai_event_count=ai_event_count,
        ai_cost_krw=ai_cost,
        cost_summary_count=summary_count,
        internal_ai_cost_krw=internal_cost,
        customer_charge_krw=customer_charge,
        record_lines=record_lines,
        target_record_lines=target_record_lines,
        invalid_record_lines=invalid_record_lines,
    )


def _require_status(response: HttpResponse, expected: int, stage: str) -> None:
    if response.status != expected:
        raise AcceptanceFailure(
            f"{stage} 응답이 HTTP {expected}가 아니라 {response.status}입니다"
        )


def _location_path(response: HttpResponse) -> str:
    return urllib.parse.urlsplit(response.headers.get("location", "")).path


def _decode_html(response: HttpResponse) -> str:
    return response.body.decode("utf-8", errors="replace")


def _login_admin(
    port: int,
    capability: str,
    tracker: LeakTracker,
    *,
    surface_prefix: str,
) -> HttpSession:
    session = HttpSession(port)
    invalid = session.request(
        "GET",
        "/auth/local-demo/start?token=" + secrets.token_hex(32),
    )
    tracker.observe(invalid, surface_prefix + ":invalid-capability")
    _require_status(invalid, 404, "잘못된 로컬 관리자 capability")

    landing = session.request(
        "GET",
        "/auth/local-demo/start?token=" + urllib.parse.quote(capability),
        follow_redirects=True,
    )
    tracker.observe(landing, surface_prefix + ":login-landing")
    _require_status(landing, 200, "로컬 관리자 교환")
    final = urllib.parse.urlsplit(landing.url)
    if final.path != "/auth/local-demo" or final.query:
        raise AcceptanceFailure("관리자 capability가 깨끗한 로그인 주소로 제거되지 않았습니다")
    state = extract_input_value(_decode_html(landing), "state")
    if not state:
        raise AcceptanceFailure("로컬 관리자 로그인 state를 찾지 못했습니다")

    logged_in = session.request(
        "POST",
        "/auth/local-demo",
        data={"state": state},
        headers={"Origin": session.base_url},
    )
    tracker.observe(logged_in, surface_prefix + ":login-post")
    _require_status(logged_in, 303, "로컬 관리자 로그인")
    if _location_path(logged_in) != "/admin":
        raise AcceptanceFailure("로컬 관리자 로그인 뒤 /admin으로 이동하지 않았습니다")
    admin = session.request("GET", "/admin")
    tracker.observe(admin, surface_prefix + ":admin")
    _require_status(admin, 200, "관리자 화면")
    return session


def _health_action(port: int, tracker: LeakTracker) -> str:
    response = HttpSession(port).request("GET", "/healthz")
    tracker.observe(response, "health")
    _require_status(response, 200, "healthz")
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise AcceptanceFailure("healthz가 JSON을 반환하지 않았습니다") from error
    if payload != {"status": "ok"}:
        raise AcceptanceFailure("healthz liveness 상태가 ok가 아닙니다")
    return "HTTP 200과 status=ok를 실제 loopback 요청으로 확인했습니다"


def _readiness_action(port: int, tracker: LeakTracker) -> str:
    response = HttpSession(port).request("GET", "/readyz")
    tracker.observe(response, "readiness")
    _require_status(response, 200, "readyz")
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise AcceptanceFailure("readyz가 JSON을 반환하지 않았습니다") from error
    if payload != {"status": "ready"}:
        raise AcceptanceFailure("readyz 저장소 준비 상태가 ready가 아닙니다")
    return "HTTP 200과 status=ready를 격리 SQLite에서 확인했습니다"


def _authentication_action(
    port: int, capability: str, tracker: LeakTracker
) -> tuple[HttpSession, str]:
    session = _login_admin(port, capability, tracker, surface_prefix="first-start")
    return session, "잘못된 capability 404와 올바른 1회용 state 관리자 세션을 확인했습니다"


def _authorization_action(
    port: int, admin: HttpSession, tracker: LeakTracker
) -> str:
    public = HttpSession(port)
    denied = public.request("GET", "/admin")
    tracker.observe(denied, "authorization:anonymous-admin")
    _require_status(denied, 303, "비로그인 관리자 경로")
    if _location_path(denied) != "/auth/not-admin":
        raise AcceptanceFailure("비로그인 관리 요청이 권한 안내로 차단되지 않았습니다")

    access = admin.request("GET", "/admin/access")
    tracker.observe(access, "authorization:admin-access")
    _require_status(access, 200, "로그인 관리자 권한")
    rejected = admin.request(
        "POST",
        "/admin/links/new",
        data={
            "company": DEMO_COMPANY,
            "job": "",
            "note": "",
            "report_reference": "",
            "csrf_token": "invalid-csrf",
        },
        headers={"Origin": admin.base_url},
    )
    tracker.observe(rejected, "authorization:invalid-csrf")
    _require_status(rejected, 403, "잘못된 관리자 CSRF")
    return "비로그인 /admin 303 차단, 관리자 200, 잘못된 CSRF 403을 확인했습니다"


def _workflow_action(
    admin: HttpSession,
    tracker: LeakTracker,
    *,
    workflow_timeout_sec: float,
) -> tuple[WorkflowState, str]:
    home = admin.request("GET", "/")
    tracker.observe(home, "demo:home")
    _require_status(home, 200, "데모 첫 화면")
    csrf = extract_input_value(_decode_html(home), "csrf_token")
    if not csrf:
        raise AcceptanceFailure("데모 첫 화면에서 CSRF 토큰을 찾지 못했습니다")
    form = {
        "company": DEMO_COMPANY,
        "region": DEMO_REGION,
        "csrf_token": csrf,
    }
    confirm = admin.request(
        "POST",
        "/confirm",
        data=form,
        headers={"Origin": admin.base_url},
    )
    tracker.observe(confirm, "demo:confirm")
    _require_status(confirm, 200, "데모 회사 확인")
    token = extract_input_value(_decode_html(confirm), "paid_attempt_token")
    if not token:
        raise AcceptanceFailure("회사 확인 화면에서 서버 일회용 실행 토큰을 찾지 못했습니다")

    run_form = {**form, "paid_attempt_token": token}
    started = admin.request(
        "POST",
        "/run",
        data=run_form,
        headers={"Origin": admin.base_url},
    )
    tracker.observe(started, "demo:run")
    _require_status(started, 303, "데모 실행 시작")
    location = _location_path(started)
    matched = re.fullmatch(r"/progress/([0-9a-f]{32})", location)
    if matched is None:
        raise AcceptanceFailure("데모 실행이 32자리 report_id 진행 화면으로 이동하지 않았습니다")
    report_id = matched.group(1)

    replay = admin.request(
        "POST",
        "/run",
        data=run_form,
        headers={"Origin": admin.base_url},
    )
    tracker.observe(replay, "demo:immediate-replay")
    _require_status(replay, 303, "실행 토큰 즉시 재사용")
    if _location_path(replay) != "/":
        raise AcceptanceFailure("소비한 실행 토큰의 즉시 재사용이 첫 화면으로 차단되지 않았습니다")

    deadline = time.monotonic() + workflow_timeout_sec
    while time.monotonic() < deadline:
        progress = admin.request("GET", f"/api/progress/{report_id}")
        tracker.observe(progress, "demo:progress")
        _require_status(progress, 200, "데모 진행 상태")
        try:
            progress_payload = json.loads(progress.body)
        except json.JSONDecodeError as error:
            raise AcceptanceFailure("데모 진행 상태가 JSON이 아닙니다") from error
        if progress_payload.get("finished") is True:
            break
        time.sleep(POLL_INTERVAL_SEC)
    else:
        raise AcceptanceFailure("무료 데모가 제한시간 안에 끝나지 않았습니다")

    screen = admin.request(
        "GET",
        f"/result/{report_id}",
        timeout=workflow_timeout_sec,
    )
    tracker.observe(screen, "demo:result-screen")
    _require_status(screen, 200, "데모 결과 화면")
    return (
        WorkflowState(
            report_id=report_id,
            attempt_token=token,
            csrf_token=csrf,
            form=run_form,
            screen_body=screen.body,
        ),
        "회사 확인→진행→완료→결과 화면을 무료 demo로 실행했고 즉시 토큰 재사용도 차단됐습니다",
    )


def _sha256_header(response: HttpResponse, name: str, stage: str) -> str:
    value = response.headers.get(name.lower(), "")
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AcceptanceFailure(f"{stage}의 SHA-256 응답 헤더가 올바르지 않습니다")
    return value


def _output_identity_action(
    *,
    port: int,
    admin: HttpSession,
    workflow: WorkflowState,
    tracker: LeakTracker,
    workflow_timeout_sec: float,
) -> tuple[OutputState, str]:
    screen_html = workflow.screen_body.decode("utf-8", errors="replace")
    pdf_path = f"/download/pdf/{workflow.report_id}"
    if not _has_href(screen_html, pdf_path):
        raise AcceptanceFailure("결과 화면의 PDF 링크가 동일 report_id를 사용하지 않습니다")

    pdf = admin.request("GET", pdf_path, timeout=workflow_timeout_sec)
    tracker.observe(pdf, "output:pdf")
    _require_status(pdf, 200, "PDF 다운로드")
    if not pdf.headers.get("content-type", "").startswith("application/pdf"):
        raise AcceptanceFailure("PDF 다운로드의 Content-Type이 application/pdf가 아닙니다")
    if not pdf.body.startswith(b"%PDF-"):
        raise AcceptanceFailure("PDF 다운로드 바이트가 PDF 형식이 아닙니다")
    pdf_sha256 = _sha256_header(pdf, "x-pdf-sha256", "PDF")
    release_sha256 = _sha256_header(
        pdf, "x-pdf-release-record", "PDF 자동출고 레코드"
    )

    access = admin.request("GET", "/admin/access")
    tracker.observe(access, "output:admin-access")
    _require_status(access, 200, "LINK 관리 화면")
    csrf = extract_input_value(_decode_html(access), "csrf_token")
    if not csrf:
        raise AcceptanceFailure("LINK 관리 화면의 CSRF 토큰을 찾지 못했습니다")
    issued = admin.request(
        "POST",
        "/admin/links/new",
        data={
            "company": DEMO_COMPANY,
            "job": "",
            "note": "릴리스 수락시험 임시 LINK",
            "report_reference": f"/result/{workflow.report_id}",
            "csrf_token": csrf,
        },
        headers={"Origin": admin.base_url},
    )
    # LINK 원문은 이 한 응답의 의도된 산출물이다. 아직 marker에 넣지 않은 상태로
    # 다른 비밀이 섞였는지만 검사하고, 추출 직후부터 로그·DB·최종 출력에는 금지한다.
    tracker.observe(issued, "output:share-issue")
    _require_status(issued, 200, "공유 LINK 발급")
    issued_url = _decode_html(issued).strip()
    parsed = urllib.parse.urlsplit(issued_url)
    if parsed.scheme != "http" or parsed.hostname not in {LOOPBACK_HOST, "localhost"}:
        raise AcceptanceFailure("발급된 공유 LINK가 loopback HTTP 주소가 아닙니다")
    if parsed.port != port:
        raise AcceptanceFailure("발급된 공유 LINK가 수락시험 서버 포트를 가리키지 않습니다")
    key_match = re.fullmatch(r"/k/([0-9a-f]{32})", parsed.path)
    if key_match is None or parsed.query or parsed.fragment:
        raise AcceptanceFailure("발급된 공유 LINK capability 형식이 올바르지 않습니다")
    share_capability = key_match.group(1)
    tracker.add_marker("공유 LINK capability", share_capability)

    visitor = HttpSession(port)
    opened = visitor.request("GET", parsed.path)
    # 이 응답 객체의 url은 클라이언트가 의도적으로 호출한 capability 요청 주소다.
    # 요청 자체를 유출로 세지 않되 body·headers(Location 포함)는 계속 검사한다.
    tracker.observe(
        opened,
        "output:share-open",
        scan_response_url=False,
        # 살아 있는 LINK임을 증명하는 HttpOnly 권한 쿠키는 이 응답에서만
        # capability 원문을 운반한다. 본문·Location·그 밖의 헤더는 계속 검사한다.
        ignored_header_names=frozenset({"set-cookie"}),
    )
    _require_status(opened, 303, "공유 LINK 열기")
    if _location_path(opened) != f"/result/{workflow.report_id}":
        raise AcceptanceFailure("공유 LINK가 동일 report_id 결과로 이동하지 않습니다")

    return (
        OutputState(
            pdf_sha256=pdf_sha256,
            release_sha256=release_sha256,
            share_capability=share_capability,
        ),
        "결과 화면 PDF href, PDF 자동출고 지문, 공유 LINK 이동이 같은 32자리 report_id를 사용합니다",
    )


def _duplicate_cost_action(
    *,
    admin: HttpSession,
    workflow: WorkflowState,
    paths: RuntimePaths,
    tracker: LeakTracker,
) -> tuple[StorageSnapshot, str]:
    before = storage_snapshot(paths.database, paths.records, workflow.report_id)
    replay = admin.request(
        "POST",
        "/run",
        data=workflow.form,
        headers={"Origin": admin.base_url},
    )
    tracker.observe(replay, "duplicate:replay-after-finish")
    _require_status(replay, 303, "완료 뒤 실행 토큰 재사용")
    if _location_path(replay) != "/":
        raise AcceptanceFailure("완료 뒤 실행 토큰 재사용이 첫 화면으로 차단되지 않았습니다")
    time.sleep(0.3)
    after = storage_snapshot(paths.database, paths.records, workflow.report_id)

    if not before.complete or not after.complete:
        missing = sorted(set(before.missing_tables) | set(after.missing_tables))
        detail = ", ".join(missing) if missing else "깨진 관측 JSON"
        raise AcceptanceBlocked(f"비용·중복 근거를 완전하게 읽지 못했습니다: {detail}")
    if before.stable_projection() != after.stable_projection():
        raise AcceptanceFailure("소비한 실행 토큰 재사용 뒤 보고서·비용·관측 수가 바뀌었습니다")
    if before.reports_total != 1 or before.target_reports != 1:
        raise AcceptanceFailure("격리 저장소에 무료 데모 보고서가 정확히 한 건이 아닙니다")
    if before.record_lines != 1 or before.target_record_lines != 1:
        raise AcceptanceFailure("격리 관측 기록에 무료 데모 실행이 정확히 한 건이 아닙니다")
    nonzero = {
        "provider 비용": before.ai_cost_krw,
        "단계 지출": before.budget_cost_krw,
        "진행 예약": before.reserved_cost_krw,
        "내부 AI 원가": before.internal_ai_cost_krw,
        "고객 청구": before.customer_charge_krw,
    }
    if any(abs(value) > 1e-9 for value in nonzero.values()):
        raise AcceptanceFailure("무료 demo 원장에 0원이 아닌 비용이 기록됐습니다")
    if before.ai_event_count != 0 or before.inflight_count != 0:
        raise AcceptanceFailure("무료 demo에 provider 사건 또는 미정산 예약이 남았습니다")
    return (
        before,
        "보고서·관측 각 1건, provider 사건 0건, 예약 0건, 모든 비용 0원이며 재사용 전후 snapshot이 같습니다",
    )


def _restart_action(
    *,
    port: int,
    capability: str,
    workflow: WorkflowState,
    output: OutputState | None,
    baseline: StorageSnapshot,
    paths: RuntimePaths,
    tracker: LeakTracker,
    workflow_timeout_sec: float,
) -> str:
    health = HttpSession(port).request("GET", "/healthz")
    tracker.observe(health, "restart:health")
    _require_status(health, 200, "재시작 healthz")
    ready = HttpSession(port).request("GET", "/readyz")
    tracker.observe(ready, "restart:ready")
    _require_status(ready, 200, "재시작 readyz")
    admin = _login_admin(port, capability, tracker, surface_prefix="restart")

    screen = admin.request(
        "GET",
        f"/result/{workflow.report_id}",
        timeout=workflow_timeout_sec,
    )
    tracker.observe(screen, "restart:result-screen")
    _require_status(screen, 200, "재시작 저장 결과 화면")
    if not _has_href(
        _decode_html(screen), f"/download/pdf/{workflow.report_id}"
    ):
        raise AcceptanceFailure("재시작 결과 화면이 동일 report_id PDF를 가리키지 않습니다")

    pdf = admin.request(
        "GET",
        f"/download/pdf/{workflow.report_id}",
        timeout=workflow_timeout_sec,
    )
    tracker.observe(pdf, "restart:pdf")
    _require_status(pdf, 200, "재시작 저장 PDF")
    restarted_pdf_sha = _sha256_header(pdf, "x-pdf-sha256", "재시작 PDF")
    restarted_release_sha = _sha256_header(
        pdf, "x-pdf-release-record", "재시작 자동출고 레코드"
    )
    if output is not None and (
        restarted_pdf_sha != output.pdf_sha256
        or restarted_release_sha != output.release_sha256
    ):
        raise AcceptanceFailure("재시작 전후 PDF 또는 자동출고 레코드 지문이 달라졌습니다")

    restored = storage_snapshot(paths.database, paths.records, workflow.report_id)
    if restored.target_reports != 1 or not restored.target_payload_sha256:
        raise AcceptanceFailure("재시작 뒤 동일 저장 보고서를 읽지 못했습니다")
    if restored.target_payload_sha256 != baseline.target_payload_sha256:
        raise AcceptanceFailure("재시작 뒤 저장 보고서 payload 지문이 달라졌습니다")
    if restored.stable_projection() != baseline.stable_projection():
        raise AcceptanceFailure("재시작 조회 뒤 보고서·비용·관측 snapshot이 바뀌었습니다")
    return "같은 SQLite로 재기동해 결과·PDF를 열었고 payload·PDF·출고·비용 snapshot이 유지됐습니다"


def _read_egress_audit(path: Path) -> dict[str, object]:
    expected_keys = {
        "schema_version",
        "installed",
        "loopback_probe_allowed",
        *EGRESS_AUDIT_COUNTER_KEYS,
    }
    try:
        if path.is_symlink() or not path.is_file():
            raise AcceptanceFailure("자식 egress 감사 파일이 안전한 일반 파일이 아닙니다")
        raw = path.read_bytes()
    except OSError as error:
        raise AcceptanceFailure("자식 egress 감사 파일을 읽지 못했습니다") from error
    if not raw or len(raw) > MAX_EGRESS_AUDIT_BYTES:
        raise AcceptanceFailure("자식 egress 감사 파일 크기가 올바르지 않습니다")
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceFailure("자식 egress 감사 JSON이 올바르지 않습니다") from error
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise AcceptanceFailure("자식 egress 감사 필드가 정확하지 않습니다")
    if (
        payload["schema_version"] != EGRESS_AUDIT_SCHEMA_VERSION
        or payload["installed"] is not True
        or payload["loopback_probe_allowed"] is not True
    ):
        raise AcceptanceFailure("자식 egress guard 설치·loopback 자체검증 증거가 없습니다")
    for key in EGRESS_AUDIT_COUNTER_KEYS:
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AcceptanceFailure("자식 egress 감사 횟수가 올바르지 않습니다")
    if (
        payload["self_test_dns_denied"] != 1
        or payload["self_test_ip_denied"] != 1
        or payload["self_test_socket_denied"] != 0
    ):
        raise AcceptanceFailure("자식 egress DNS·IP 차단 자체검증이 정확하지 않습니다")
    if any(payload[key] != 0 for key in EGRESS_AUDIT_COUNTER_KEYS if key.startswith("runtime_")):
        raise AcceptanceFailure("자식 앱이 실행 중 외부 DNS·IP·socket 접근을 시도했습니다")
    return payload


def _provider_isolation_action(
    environments: tuple[Mapping[str, str], ...],
    *,
    audit_paths: tuple[Path, ...] = (),
) -> str:
    if not environments:
        raise AcceptanceFailure("검증할 자식 환경이 없습니다")
    for environment in environments:
        leaked_names = sorted(EXTERNAL_PROVIDER_ENV_NAMES & set(environment))
        if leaked_names:
            raise AcceptanceFailure("자식 환경에 외부 provider 자격 증명 이름이 포함됐습니다")
        if environment.get("PIPELINE") != "demo":
            raise AcceptanceFailure("자식 서버가 PIPELINE=demo로 고정되지 않았습니다")
        if environment.get("HTTP_PROXY") != "http://127.0.0.1:9":
            raise AcceptanceFailure("외부 HTTP fail-closed proxy가 설정되지 않았습니다")
    if not audit_paths:
        return "PIPELINE=demo, provider 자격 증명 0개, 자식 egress guard 경로를 고정했습니다"
    if len(environments) != len(audit_paths):
        raise AcceptanceFailure("자식 서버별 egress 감사 증거 수가 다릅니다")
    for environment, audit_path in zip(environments, audit_paths, strict=True):
        configured = environment.get(EGRESS_AUDIT_ENV_NAME, "")
        if not configured or Path(configured).resolve() != audit_path.resolve():
            raise AcceptanceFailure("자식 egress 감사 경로 결속이 다릅니다")
        _read_egress_audit(audit_path)
    probes = len(audit_paths) * 2
    raise AcceptanceBlocked(
        f"Python detector는 자식 {len(audit_paths)}개에서 외부 DNS·IP probe "
        f"{probes}건을 거부하고 앱 관측 시도 0건을 기록했지만, uvloop/libuv·native "
        "extension·ctypes·subprocess까지 닫는 Linux network namespace/firewall "
        "attestation이 없어 OS egress 격리는 검증하지 못했습니다"
    )


def _sensitive_action(
    *,
    tracker: LeakTracker,
    server_logs: tuple[bytes, ...],
    paths: RuntimePaths,
) -> str:
    for index, log in enumerate(server_logs, start=1):
        tracker.scan_blob(f"server-log-{index}", log)
    for label, path in (
        ("sqlite", paths.database),
        ("observability", paths.records),
    ):
        try:
            tracker.scan_blob(label, path.read_bytes())
        except OSError:
            continue
    if tracker.leaks:
        surfaces = ", ".join(sorted(tracker.leaks))
        raise AcceptanceFailure(f"민감 marker가 노출된 표면이 있습니다: {surfaces}")
    return "로그·일반 HTTP 응답·SQLite·관측 파일·최종 보고서에 capability/내부 seal 원문이 없습니다"


T = TypeVar("T")


def _run_check(
    results: dict[str, CheckResult],
    check_id: str,
    action: Callable[[], tuple[T, str] | str],
) -> T | None:
    started = time.monotonic()
    try:
        outcome = action()
        if isinstance(outcome, tuple):
            value, evidence = outcome
        else:
            value, evidence = None, outcome
        status = CheckStatus.PASS
    except AcceptanceBlocked as error:
        value = None
        evidence = str(error)
        status = CheckStatus.BLOCKED
    except AcceptanceFailure as error:
        value = None
        evidence = str(error)
        status = CheckStatus.FAIL
    except Exception as error:  # noqa: BLE001 - 원문 예외에는 보고서/경로가 있을 수 있다
        value = None
        evidence = f"예상하지 못한 로컬 시험 오류({type(error).__name__})"
        status = CheckStatus.FAIL
    results[check_id] = CheckResult(
        check_id=check_id,
        title=CHECK_TITLES[check_id],
        status=status,
        evidence=evidence,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
    )
    return value


def _set_result(
    results: dict[str, CheckResult],
    check_id: str,
    status: CheckStatus,
    evidence: str,
    *,
    duration_ms: int = 0,
) -> None:
    results[check_id] = CheckResult(
        check_id=check_id,
        title=CHECK_TITLES[check_id],
        status=status,
        evidence=evidence,
        duration_ms=duration_ms,
    )


def _block_missing(results: dict[str, CheckResult], reason: str) -> None:
    for check_id, _title in CHECKS:
        if check_id not in results:
            _set_result(results, check_id, CheckStatus.BLOCKED, reason)


def _server_start_problem(logs: bytes, fallback: str) -> tuple[CheckStatus, str]:
    missing = _missing_module(logs)
    if missing:
        return CheckStatus.BLOCKED, f"서버 런타임 패키지 '{missing}'이 없어 기동하지 못했습니다"
    return CheckStatus.FAIL, fallback


def _final_report(
    results: dict[str, CheckResult],
    *,
    started_at: str,
    tracker: LeakTracker,
) -> AcceptanceReport:
    _block_missing(results, "선행 수락시험 단계가 완료되지 않아 실제 검증하지 못했습니다")
    ordered = tuple(results[check_id] for check_id, _title in CHECKS)
    report = AcceptanceReport(
        schema_version=SCHEMA_VERSION,
        started_at=started_at,
        finished_at=_now_iso(),
        overall_status=overall_status(ordered),
        mode="isolated-local-demo",
        external_provider_calls_allowed=False,
        checks=ordered,
    )
    sensitive = results["sensitive_nonexposure"]
    if sensitive.status is CheckStatus.PASS:
        rendered = report.to_json() + "\n" + render_korean_summary(report)
        if contains_sensitive_marker(rendered, tracker.markers):
            _set_result(
                results,
                "sensitive_nonexposure",
                CheckStatus.FAIL,
                "최종 JSON 또는 한국어 요약에 민감 marker가 포함됐습니다",
            )
            ordered = tuple(results[check_id] for check_id, _title in CHECKS)
            report = AcceptanceReport(
                schema_version=SCHEMA_VERSION,
                started_at=started_at,
                finished_at=_now_iso(),
                overall_status=overall_status(ordered),
                mode="isolated-local-demo",
                external_provider_calls_allowed=False,
                checks=ordered,
            )
    return report


def run_acceptance(config: RunConfig) -> AcceptanceReport:
    """한 명령으로 실제 서버를 두 번 띄워 릴리스 계약을 검증한다."""

    started_at = _now_iso()
    results: dict[str, CheckResult] = {}
    app_root = config.app_root.resolve()
    if not (app_root / "src" / "web" / "main.py").is_file():
        tracker = LeakTracker(markers={})
        _set_result(
            results,
            "runtime_dependencies",
            CheckStatus.BLOCKED,
            "app/src/web/main.py를 찾지 못해 서버를 시작할 수 없습니다",
        )
        _block_missing(results, "애플리케이션 루트를 확인하지 못했습니다")
        return _final_report(results, started_at=started_at, tracker=tracker)

    with tempfile.TemporaryDirectory(
        prefix=".release_acceptance_",
        dir=str(app_root),
        ignore_cleanup_errors=True,
    ) as temporary:
        paths = _runtime_paths(Path(temporary))
        port = _loopback_port()
        first_capability = secrets.token_hex(32)
        provenance_seal = "release-acceptance-seal-" + secrets.token_hex(32)
        tracker = LeakTracker(
            markers={
                "첫 관리자 capability": first_capability,
                "내부 출처 seal": provenance_seal,
            }
        )
        first_egress_audit = paths.root / "egress-first.json"
        second_egress_audit = paths.root / "egress-second.json"
        first_environment = _runtime_environment(
            paths,
            port=port,
            auth_capability=first_capability,
            provenance_seal=provenance_seal,
            egress_audit_path=first_egress_audit,
        )

        _run_check(
            results,
            "runtime_dependencies",
            lambda: _probe_dependencies(config, paths),
        )
        _run_check(
            results,
            "provider_isolation",
            lambda: _provider_isolation_action((first_environment,)),
        )
        if results["runtime_dependencies"].status is not CheckStatus.PASS:
            _set_result(
                results,
                "provider_isolation",
                CheckStatus.BLOCKED,
                "자식 서버를 시작하지 않아 실제 socket/DNS egress 차단을 검증하지 못했습니다",
            )
            _block_missing(results, "로컬 실행 의존성이 준비되지 않아 서버 기반 항목을 검증하지 못했습니다")
            return _final_report(results, started_at=started_at, tracker=tracker)

        lifecycle_started = time.monotonic()
        server_logs: list[bytes] = []
        first_server = ManagedServer(
            app_root=app_root,
            python_executable=config.python_executable,
            port=port,
            environment=first_environment,
            log_path=paths.root / "server-first.log",
            startup_timeout_sec=config.startup_timeout_sec,
        )
        try:
            first_server.start()
        except Exception as error:  # noqa: BLE001 - 아래에서 비밀 없는 상태만 분류한다
            first_cleanup = first_server.stop()
            first_log = first_server.logs()
            server_logs.append(first_log)
            status, evidence = _server_start_problem(
                first_log,
                f"첫 서버를 준비하지 못했습니다({type(error).__name__})",
            )
            _set_result(
                results,
                "server_lifecycle",
                status,
                evidence,
                duration_ms=round((time.monotonic() - lifecycle_started) * 1000),
            )
            _set_result(
                results,
                "port_cleanup",
                CheckStatus.PASS if first_cleanup else CheckStatus.FAIL,
                "실패한 기동 뒤 loopback 포트를 회수했습니다"
                if first_cleanup
                else "실패한 기동 뒤 loopback 포트가 남았습니다",
            )
            _set_result(
                results,
                "sensitive_nonexposure",
                CheckStatus.BLOCKED,
                "전체 HTTP·LINK 흐름이 시작되지 않아 비노출 범위를 검증하지 못했습니다",
            )
            _run_check(
                results,
                "provider_isolation",
                lambda: _provider_isolation_action(
                    (first_environment,),
                    audit_paths=(first_egress_audit,),
                ),
            )
            _block_missing(results, "첫 서버가 준비되지 않아 실제 HTTP 항목을 검증하지 못했습니다")
            return _final_report(results, started_at=started_at, tracker=tracker)

        _run_check(results, "health", lambda: _health_action(port, tracker))
        _run_check(results, "readiness", lambda: _readiness_action(port, tracker))
        admin = _run_check(
            results,
            "authentication",
            lambda: _authentication_action(port, first_capability, tracker),
        )

        workflow: WorkflowState | None = None
        output: OutputState | None = None
        baseline: StorageSnapshot | None = None
        if isinstance(admin, HttpSession):
            _run_check(
                results,
                "authorization",
                lambda: _authorization_action(port, admin, tracker),
            )
            workflow = _run_check(
                results,
                "free_local_demo",
                lambda: _workflow_action(
                    admin,
                    tracker,
                    workflow_timeout_sec=config.workflow_timeout_sec,
                ),
            )
        else:
            _set_result(
                results,
                "authorization",
                CheckStatus.BLOCKED,
                "관리자 인증이 완료되지 않아 권한·CSRF를 검증하지 못했습니다",
            )
            _set_result(
                results,
                "free_local_demo",
                CheckStatus.BLOCKED,
                "관리자 인증이 완료되지 않아 무료 데모를 실행하지 못했습니다",
            )

        if isinstance(admin, HttpSession) and isinstance(workflow, WorkflowState):
            output = _run_check(
                results,
                "output_identity",
                lambda: _output_identity_action(
                    port=port,
                    admin=admin,
                    workflow=workflow,
                    tracker=tracker,
                    workflow_timeout_sec=config.workflow_timeout_sec,
                ),
            )
            baseline = _run_check(
                results,
                "duplicate_cost_invariant",
                lambda: _duplicate_cost_action(
                    admin=admin,
                    workflow=workflow,
                    paths=paths,
                    tracker=tracker,
                ),
            )
            if baseline is None:
                try:
                    candidate = storage_snapshot(
                        paths.database, paths.records, workflow.report_id
                    )
                    if candidate.target_reports == 1 and candidate.target_payload_sha256:
                        baseline = candidate
                except Exception:  # noqa: BLE001 - restart 항목에서 BLOCKED로 정직하게 남긴다
                    baseline = None
        else:
            _set_result(
                results,
                "output_identity",
                CheckStatus.BLOCKED,
                "완료된 무료 데모가 없어 화면·PDF·LINK를 비교하지 못했습니다",
            )
            _set_result(
                results,
                "duplicate_cost_invariant",
                CheckStatus.BLOCKED,
                "완료된 무료 데모가 없어 중복·비용 snapshot을 검증하지 못했습니다",
            )

        first_cleanup = first_server.stop()
        server_logs.append(first_server.logs())

        second_started = False
        second_cleanup = False
        second_environment: dict[str, str] | None = None
        if first_cleanup:
            second_capability = secrets.token_hex(32)
            tracker.add_marker("재시작 관리자 capability", second_capability)
            second_environment = _runtime_environment(
                paths,
                port=port,
                auth_capability=second_capability,
                provenance_seal=provenance_seal,
                egress_audit_path=second_egress_audit,
            )
            second_server = ManagedServer(
                app_root=app_root,
                python_executable=config.python_executable,
                port=port,
                environment=second_environment,
                log_path=paths.root / "server-second.log",
                startup_timeout_sec=config.startup_timeout_sec,
            )
            try:
                second_server.start()
                second_started = True
            except Exception as error:  # noqa: BLE001
                second_cleanup = second_server.stop()
                second_log = second_server.logs()
                server_logs.append(second_log)
                status, evidence = _server_start_problem(
                    second_log,
                    f"재시작 서버를 준비하지 못했습니다({type(error).__name__})",
                )
                _set_result(results, "server_lifecycle", status, evidence)
                _set_result(
                    results,
                    "restart_persistence",
                    CheckStatus.BLOCKED,
                    "재시작 서버가 준비되지 않아 저장본을 검증하지 못했습니다",
                )
            if second_started:
                if isinstance(workflow, WorkflowState) and baseline is not None:
                    _run_check(
                        results,
                        "restart_persistence",
                        lambda: _restart_action(
                            port=port,
                            capability=second_capability,
                            workflow=workflow,
                            output=output if isinstance(output, OutputState) else None,
                            baseline=baseline,
                            paths=paths,
                            tracker=tracker,
                            workflow_timeout_sec=config.workflow_timeout_sec,
                        ),
                    )
                else:
                    _set_result(
                        results,
                        "restart_persistence",
                        CheckStatus.BLOCKED,
                        "재시작 전 저장 보고서 snapshot이 없어 복구를 검증하지 못했습니다",
                    )
                second_cleanup = second_server.stop()
                server_logs.append(second_server.logs())
        else:
            _set_result(
                results,
                "restart_persistence",
                CheckStatus.BLOCKED,
                "첫 서버 포트가 회수되지 않아 같은 포트 재시작을 시도하지 않았습니다",
            )

        provider_environments = (first_environment,) + (
            (second_environment,) if second_environment is not None else ()
        )
        provider_audits = (first_egress_audit,) + (
            (second_egress_audit,) if second_environment is not None else ()
        )
        _run_check(
            results,
            "provider_isolation",
            lambda: _provider_isolation_action(
                provider_environments,
                audit_paths=provider_audits,
            ),
        )

        if "server_lifecycle" not in results:
            if first_cleanup and second_started and second_cleanup:
                _set_result(
                    results,
                    "server_lifecycle",
                    CheckStatus.PASS,
                    "같은 loopback 포트에서 uvicorn을 두 번 기동하고 두 프로세스를 종료했습니다",
                    duration_ms=round((time.monotonic() - lifecycle_started) * 1000),
                )
            else:
                _set_result(
                    results,
                    "server_lifecycle",
                    CheckStatus.FAIL,
                    "두 번의 서버 기동·종료 수명주기를 모두 완료하지 못했습니다",
                    duration_ms=round((time.monotonic() - lifecycle_started) * 1000),
                )

        port_clean = first_cleanup and second_cleanup and _port_bindable(port)
        _set_result(
            results,
            "port_cleanup",
            CheckStatus.PASS if port_clean else CheckStatus.FAIL,
            "두 번의 종료 뒤 같은 loopback 포트를 다시 bind할 수 있습니다"
            if port_clean
            else "종료 뒤 loopback listener 또는 포트 점유가 남았습니다",
        )

        if isinstance(workflow, WorkflowState):
            _run_check(
                results,
                "sensitive_nonexposure",
                lambda: _sensitive_action(
                    tracker=tracker,
                    server_logs=tuple(server_logs),
                    paths=paths,
                ),
            )
        else:
            _set_result(
                results,
                "sensitive_nonexposure",
                CheckStatus.BLOCKED,
                "무료 demo·LINK 전체 흐름이 없어 모든 비노출 표면을 검증하지 못했습니다",
            )
        return _final_report(results, started_at=started_at, tracker=tracker)

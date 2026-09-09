"""확인된 임의 회사 목록을 격리 평가 서버의 공식 HTTP 흐름으로 실행한다.

기본은 사전검사이며 --execute만 새 유료 경계를 연다. 불확실한 POST는
재전송하지 않고, --resume-only는 이미 접수된 작업의 GET만 재개한다.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import ctypes
from dataclasses import asdict
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
import time

import httpx

from src.features.pilot_evaluation.checkpoint import CheckpointStore
from src.features.pilot_evaluation.contract import PilotCategory
from src.features.pilot_evaluation.manifest import CanonicalPilotCase
from src.features.pilot_evaluation.runner import (
    CanonicalPilotRunner,
    _LedgerConsistencyError,
    canonical_loopback_origin,
)
from src.features.report_access.constants import PUBLIC_GRANT_COOKIE_NAME
from src.shared.company_identity import verified_official_company_names_equivalent
from tools.evaluation_constants import (
    CHECKPOINT_SCHEMA, DIAGNOSTIC_EXPORTS, FEATURE_KEYS, HTTP_TIMEOUT_SECONDS,
    LEDGER_CONSISTENCY_ERROR_CODES,
    MANIFEST_SCHEMA, MAX_CASES, MIN_DUPLICATE_LINE_CHARACTERS,
    NEWS_USAGE_STEP_NAMES,
    POLL_INTERVAL_SECONDS, POLL_TIMEOUT_SECONDS,
)


class EvaluationError(RuntimeError):
    """민감한 HTTP 본문을 포함하지 않는 평가 중단 사유."""


def interruption_error_code(exc: Exception) -> str | None:
    """실제 원장 일관성 예외의 공식 닫힌 코드만 반환한다."""
    if not isinstance(exc, _LedgerConsistencyError):
        return None
    code = exc.code
    return code if type(code) is str and code in LEDGER_CONSISTENCY_ERROR_CODES else None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_receipt() -> dict:
    """현재 소스와 설치 버전만 기록하며 환경변수·키·Git 원격 주소는 읽지 않는다."""
    source = Path(__file__).resolve().parents[1] / "src"
    fingerprint = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if "tests" in path.relative_to(source).parts or "__tests__" in path.relative_to(source).parts:
            continue
        if path.is_file() and path.suffix in {".py", ".html", ".css", ".js"}:
            fingerprint.update(str(path.relative_to(source)).replace("\\", "/").encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(path.read_bytes())
            fingerprint.update(b"\0")
    versions = {}
    for package in ("httpx", "fastapi", "pypdf", "anthropic"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"source_sha256": fingerprint.hexdigest(), "python_version": sys.version.split()[0],
            "installed_versions": versions}


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _preserve_existing_artifact(path: Path) -> None:
    """다시 쓰기 전에 있던 파일을 고유한 prior 경로로 복사해 둔다.

    원본은 그대로 두고 복사만 하며, 실제 교체는 뒤이은 쓰기(write_json 또는
    write_bytes)에 맡긴다 — 그 쓰기 자체가 원자적인지는 호출부마다 다르다.
    prior 파일은 "xb"(이미 있으면 실패)로 만든다. 최근 prior가 지금 내용과
    같으면 새로 만드는 대신 그 prior를 다시 fsync해 실제로 디스크에 반영됐는지
    재확인한다 — 예전 fsync가 실패해 바이트만 완전하고 미확정으로 남은 prior를
    "이미 끝났다"고 조용히 넘기지 않기 위해서다. 복사·fsync 어느 쪽이 실패해도
    예외가 그대로 올라가 다음 줄의 쓰기가 실행되지 않는다.
    """
    if not path.exists():
        return
    def _revision(candidate: Path) -> int:
        marker = f"{path.stem}.prior-"
        return int(candidate.name[len(marker):-len(path.suffix)])
    existing = sorted(
        path.parent.glob(f"{path.stem}.prior-*{path.suffix}"), key=_revision,
    )
    current_bytes = path.read_bytes()
    if existing and existing[-1].read_bytes() == current_bytes:
        # ★ 읽기 전용("rb")으로 연 fd는 Windows에서 os.fsync가 OSError(Bad file
        #   descriptor)로 실패한다(실측 확인). 내용은 안 바꾸되 fsync가 가능한
        #   "r+b"(있는 파일을 읽기·쓰기로, 자르지 않고)로 연다.
        with existing[-1].open("r+b") as stream:
            os.fsync(stream.fileno())
        return
    revision = (_revision(existing[-1]) if existing else 0) + 1
    prior_path = path.with_name(f"{path.stem}.prior-{revision}{path.suffix}")
    with prior_path.open("xb") as stream:
        stream.write(current_bytes)
        stream.flush()
        os.fsync(stream.fileno())


def load_manifest(path: Path) -> tuple[dict, tuple[CanonicalPilotCase, ...]]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = value.get("cases", [])
    if value.get("schema_version") != MANIFEST_SCHEMA or not 1 <= len(rows) <= MAX_CASES:
        raise EvaluationError("회사 목록 버전 또는 회사 수가 올바르지 않습니다")
    cases = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", str(row.get("case_id", "")))
            or not re.fullmatch(r"[0-9]{8}", str(row.get("corp_code", "")))
            or row.get("identity_confirmed") is not True
            or not all(isinstance(row.get(key), str) and row[key].strip()
                       for key in ("input_name", "expected_legal_name"))
        ):
            raise EvaluationError("모든 회사에 확정 법인명·DART 번호·identity_confirmed가 필요합니다")
        if not isinstance(row.get("address_hint", ""), str):
            raise EvaluationError("회사 주소 힌트는 문자열이어야 합니다")
        terms = row.get("foreign_company_terms", [])
        if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
            raise EvaluationError("회사 혼입 검토용 이름은 비어 있지 않은 문자열 목록이어야 합니다")
        if row.get("baseline_pdf"):
            baseline = Path(row["baseline_pdf"])
            baseline = baseline if baseline.is_absolute() else path.resolve().parent / baseline
            try:
                pdf_metrics(baseline, tuple(terms))
            except Exception as exc:
                raise EvaluationError("기존 비교 PDF를 읽지 못해 유료 요청 전에 중단했습니다") from exc
            row["baseline_pdf"] = str(baseline.resolve())
        cases.append(CanonicalPilotCase(
            case_id=row["case_id"], category=PilotCategory.SPARSE_OR_AMBIGUOUS,
            input_name=row["input_name"], expected_legal_name=row["expected_legal_name"],
            corp_code=row["corp_code"], address_hint=row.get("address_hint", ""),
        ))
    if len({case.case_id for case in cases}) != len(cases) or len({case.corp_code for case in cases}) != len(cases):
        raise EvaluationError("회사 ID 또는 DART 번호가 중복되었습니다")
    return value, tuple(cases)


@contextmanager
def exclusive(path: Path):
    """프로세스 종료 시 운영체제가 해제하는 잠금으로 이중 실행을 막는다."""
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise EvaluationError("같은 평가 폴더에서 다른 실행이 진행 중입니다") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def protect_session(data: bytes, *, decrypt: bool = False) -> bytes:
    """보고서 접근 쿠키는 현재 Windows 사용자 DPAPI로만 보관한다."""
    if os.name != "nt":
        raise EvaluationError("세션 복구를 포함한 유료 실행은 Windows DPAPI 환경이 필요합니다")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    operation.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise EvaluationError("현재 사용자로 평가 세션을 암호화하거나 복구하지 못했습니다")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def text_metrics(text: str, foreign_terms: tuple[str, ...] = ()) -> dict:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    repeated = Counter(line for line in lines if len(line) >= MIN_DUPLICATE_LINE_CHARACTERS)
    dates = re.findall(r"(?<!\d)(?:19|20)\d{2}[.\-/년]\s*\d{1,2}[.\-/월]\s*\d{1,2}", text)
    return {
        "characters": len(text), "nonempty_lines": len(lines),
        "date_mentions": len(dates), "distinct_date_strings": len(set(dates)),
        "repeated_line_occurrences": sum(count - 1 for count in repeated.values()),
        "foreign_company_term_hits_for_review": {term: text.count(term) for term in foreign_terms},
        "human_quality_judgment": None,
        "interpretation": "문자 패턴 계수이며 날짜 정확성·기사 독립성·회사 혼입의 확정 판정이 아님",
    }


def pdf_metrics(path: Path, foreign_terms: tuple[str, ...] = ()) -> dict:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    value = text_metrics("\n".join(page.extract_text() or "" for page in reader.pages), foreign_terms)
    return {**value, "pages": len(reader.pages), "sha256": digest(path.read_bytes())}


def diagnostic_metrics(diagnostics: dict) -> dict:
    """명시된 관측 키만 채운다. 기록되지 않은 지표는 0 대신 null이다."""
    steps = []
    for row in diagnostics.get("observability_run_steps") or []:
        decoded = json.loads(row.get("steps_json") or "[]")
        if isinstance(decoded, list):
            steps.extend(item for item in decoded if isinstance(item, dict))
    collection = [item for item in steps if item.get("step") == "5b_뉴스_수집"]
    usage = [item for item in steps if item.get("step") in NEWS_USAGE_STEP_NAMES]
    latest_collection = collection[-1] if collection else {}
    latest_usage = usage[-1] if usage else {}
    return {
        "news_search_count": latest_collection.get("검색"),
        "news_body_fetched_count": latest_collection.get("본문읽기"),
        "news_body_used_count": latest_usage.get("본문사용기사수"),
        "independent_news_article_count": latest_usage.get("목록기사수"),
        "news_distinct_event_count": latest_usage.get("실질사건수"),
        "news_period_counts": latest_usage.get("기간별기사수"),
        "news_collection_failure": latest_collection.get("실패"),
        "news_collection_completeness": latest_collection.get("완전성"),
        "news_collection_shortfall": latest_collection.get("자료부족"),
        "news_collection_unverified": latest_collection.get("검증미완료"),
        "news_collection_cap_reasons": latest_collection.get("상한사유"),
        "news_collected_independent_articles": latest_collection.get("독립기사"),
        "news_exclusions": latest_collection.get("제외"),
        "news_usage_decisions": latest_usage.get("근거별판정"),
        "news_metric_interpretation": "기사 신원 기준 관측값; 원문 독립성·회사 혼입·사건 중복은 사람 검토 필요",
    }


class HttpEvaluation:
    def __init__(self, *, origin: str, storage: Path, settings: Path, manifest: Path,
                 client: httpx.Client, poll_timeout: float = POLL_TIMEOUT_SECONDS,
                 poll_interval: float = POLL_INTERVAL_SECONDS, batch_id: str = "main"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", batch_id):
            raise EvaluationError("시험 회차 ID는 영문·숫자·밑줄·하이픈 1~48자여야 합니다")
        self.batch_id = batch_id
        self.origin = canonical_loopback_origin(origin)
        self.storage = storage.resolve()
        self.root = self.storage.parent
        self.manifest, self.cases = load_manifest(manifest)
        settings_bytes = settings.read_bytes()
        self.settings = json.loads(settings_bytes)
        expected_digest = settings.with_suffix(".sha256").read_text(encoding="utf-8-sig").strip()
        if settings.resolve().parent != self.root or digest(settings_bytes) != expected_digest:
            raise EvaluationError("평가 설정의 위치 또는 SHA-256이 일치하지 않습니다")
        if (self.settings.get("origin") != self.origin
                or self.settings.get("schema_version") != "company-evaluation-settings-v1"
                or any(self.settings.get("settings", {}).get(key) not in ("0", "1") for key in FEATURE_KEYS)):
            raise EvaluationError("평가 설정과 loopback 서버 계약이 일치하지 않습니다")
        if self.settings.get("paid_providers_enabled") and (
            self.settings.get("code_identity_verified") is not True
            or self.settings.get("execution_source_clean") is not True
            or not re.fullmatch(r"[0-9a-f]{40}", str(self.settings.get("app_git_commit", "")))
        ):
            raise EvaluationError("유료 서버의 실제 커밋 신원이 검증되지 않았습니다; 커밋 후 새 런처로 재시작하세요")
        self.client = client
        # 유료 로컬 서버는 화면 토큰과 함께 정확한 Origin을 요구한다.
        # CLI뿐 아니라 이 실행기를 직접 쓰는 호출자도 같은 HTTP 계약을 따른다.
        self.client.headers["Origin"] = self.origin
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.checkpoint_path = self.root / f"http-evaluation-{batch_id}-checkpoint.json"
        self.session_path = self.root / "http-evaluation-session.dpapi"
        self.binding = {
            "schema_version": CHECKPOINT_SCHEMA, "origin": self.origin,
            "batch_id": batch_id,
            "settings_sha256": expected_digest, "manifest_sha256": digest(manifest.read_bytes()),
            "storage_path_sha256": digest(str(self.storage).encode("utf-8")),
            "source_receipt": source_receipt(),
        }
        # 25사 전용 실행기/유료 승인 목록은 호출하지 않는다. HTTP form과 원장 검증만 재사용한다.
        self.bridge = CanonicalPilotRunner(
            origin=self.origin, storage_db_path=self.storage,
            checkpoint=CheckpointStore(self.checkpoint_path), client=client, cases=self.cases,
        )
        self.state: dict = {}

    def save(self):
        write_json(self.checkpoint_path, self.state)

    def save_session(self):
        rows = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                 "path": cookie.path} for cookie in self.client.cookies.jar
                if cookie.name == PUBLIC_GRANT_COOKIE_NAME]
        data = protect_session(json.dumps(rows).encode("utf-8"))
        temporary = self.session_path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.session_path)

    def load_session(self):
        if self.session_path.exists():
            for row in json.loads(protect_session(self.session_path.read_bytes(), decrypt=True)):
                if row.get("name") != PUBLIC_GRANT_COOKIE_NAME:
                    raise EvaluationError("평가 세션의 쿠키 종류가 올바르지 않습니다")
                self.client.cookies.set(**row)

    def operate(self, *, execute: bool = False, resume_only: bool = False) -> dict:
        with exclusive(self.root / "http-evaluation.lock"):
            self.bridge._validate_storage()
            with self.bridge._connect_storage() as connection:
                identity = connection.execute("SELECT identity FROM storage_database_identity WHERE singleton_id=1").fetchone()
                if identity is None:
                    raise EvaluationError("평가 데이터베이스의 불변 신원이 없습니다")
            self.load_session()
            workflow = self.bridge._workflow_page()
            binding = {**self.binding, "server_instance_sha256": workflow.server_instance_digest,
                       "storage_identity_sha256": digest(str(identity[0]).encode("utf-8"))}
            if self.checkpoint_path.exists():
                self.state = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
                if any(self.state.get(key) != value for key, value in binding.items()):
                    raise EvaluationError("서버·설정·회사 목록이 바뀌어 기존 실행의 자동 재개를 막았습니다")
            else:
                self.state = {**binding, "cases": {case.case_id: {"state": "pending"} for case in self.cases}}
                self.save()
            if not execute and not resume_only:
                return {"mode": "사전검사", "cases": len(self.cases), "paid_posts": 0,
                        "production_parity": self.settings.get("production_parity", "not_verified")}
            if not self.settings.get("paid_providers_enabled"):
                raise EvaluationError("유료 공급자가 비활성화된 실행 설정입니다")
            self.save_session()  # 첫 유료 경계 전에 복구 저장소를 검증한다.
            for case in self.cases:
                row = self.state["cases"][case.case_id]
                if row["state"] == "complete":
                    continue
                if row["state"] not in {"pending", "running"}:
                    raise EvaluationError(f"{case.case_id}: 이전 POST 결과가 불확실하여 재전송을 차단했습니다")
                try:
                    if row["state"] == "pending":
                        if resume_only:
                            continue
                        self.start(case, row)
                    self.finish(case, row)
                except Exception as exc:
                    if row.get("run_id"):
                        output = self.root / "http-evaluation-artifacts" / self.batch_id / case.case_id
                        output.mkdir(parents=True, exist_ok=True)
                        interruption = {
                            "state": row["state"], "run_id": row["run_id"],
                            "error_type": type(exc).__name__, "human_quality_judgment": None,
                            "automatic_paid_retry_allowed": False,
                        }
                        error_code = interruption_error_code(exc)
                        if error_code is not None:
                            interruption["error_code"] = error_code
                        _preserve_existing_artifact(output / "interruption.json")
                        write_json(output / "interruption.json", interruption)
                        self.export_evidence(row["run_id"], output)
                    raise
            return {"mode": "실행 결과", "cases": self.state["cases"], "human_quality_judgment": None}

    def post(self, path: str, data: dict, case_id: str):
        response = self.bridge._post_paid_boundary(path, data, case_id)
        # 응답 내용·토큰은 저장하지 않는다. 후속 파싱/세션 보관 실패가 나도
        # 실제로 받은 HTTP 상태를 잃어 원인 전체가 미확인으로 남지 않게 한다.
        self.state["cases"][case_id]["last_http_response"] = {
            "method": "POST", "path": path, "status": response.status_code,
        }
        self.save()
        self.save_session()
        return response

    def start(self, case: CanonicalPilotCase, row: dict):
        workflow = self.bridge._workflow_page()
        if workflow.server_instance_digest != self.state["server_instance_sha256"]:
            raise EvaluationError("회사 시작 직전에 서버가 바뀌었습니다")
        before = self.bridge._lifecycle_ids()
        row.update(state="identity_submission_uncertain", started_at=time.time())
        self.save()
        response = self.post("/confirm", {
            "csrf_token": workflow.csrf_token, "evaluation_workflow_id": workflow.workflow_id,
            "evaluation_paid_consent": "yes", "company": case.input_name, "region": case.address_hint,
        }, case.case_id)
        page = self.bridge._expect_html(response, 200, "회사 확인")
        candidates = [form for form in page.forms if form.action == "/confirm" and form.fields.get("candidate_ref")]
        if candidates:
            selected = [form for form in candidates if form.fields.get("candidate_ref") == case.corp_code
                        and form.fields.get("candidate_provider") == "DART"]
            if len(selected) != 1:
                raise EvaluationError(f"{case.case_id}: 확정 DART 후보가 유일하지 않아 중단했습니다")
            self.bridge._validate_candidate_form(selected[0], case)
            page = self.bridge._expect_html(self.post("/confirm", dict(selected[0].fields), case.case_id), 200, "후보 확인")
        forms = [form for form in page.forms if form.action == "/run"]
        if (page.confirmed_dart_refs != (case.corp_code,) or len(page.legal_names) != 1
                or len(forms) != 1 or not verified_official_company_names_equivalent(
                    page.legal_names[0], case.expected_legal_name,
                    observed_corp_code=case.corp_code, expected_corp_code=case.corp_code)):
            self.bridge._reject_if_possible(page)
            raise EvaluationError(f"{case.case_id}: 법인 확인 불일치로 본조사를 차단했습니다")
        self.bridge._validate_run_form(forms[0], case)
        run_id = self.bridge._single_new_lifecycle_id(before, required=True)
        row.update(state="run_submission_uncertain", run_id=run_id)
        self.save()
        accepted_id = self.bridge._accepted_run_id(self.post("/run", dict(forms[0].fields), case.case_id))
        if accepted_id != run_id:
            raise EvaluationError("접수 ID가 비용 원장과 일치하지 않습니다")
        row.update(state="running")
        self.save()

    def finish(self, case: CanonicalPilotCase, row: dict):
        run_id = row["run_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise EvaluationError("복구할 실행 ID가 올바르지 않습니다")
        deadline = time.monotonic() + self.poll_timeout
        progress_status = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/progress/{run_id}")
            progress_status = response.status_code
            if response.status_code == 200:
                payload = self.bridge._decode_json(response, "진행 상태")
                if payload.get("finished"):
                    if payload.get("next_url") != f"/result/{run_id}":
                        raise EvaluationError("완료 경로가 요청한 실행과 다릅니다")
                    break
            elif response.status_code in {409, 410}:
                break
            elif response.status_code != 503:
                raise EvaluationError(f"진행 상태 접근 실패: HTTP {response.status_code}")
            time.sleep(self.poll_interval)
        else:
            raise EvaluationError("대기 시간이 끝났습니다; --resume-only로 GET 조회만 재개할 수 있습니다")
        ledger = self.bridge._wait_for_ledger(run_id)
        if ledger is None:
            raise EvaluationError("최종 비용 원장 정합성을 아직 확인하지 못했습니다")
        output = self.root / "http-evaluation-artifacts" / self.batch_id / case.case_id
        output.mkdir(parents=True, exist_ok=True)
        _preserve_existing_artifact(output / "ledger.json")
        write_json(output / "ledger.json", asdict(ledger))
        diagnostics = self.export_evidence(run_id, output)
        if ledger.billing_uncertain:
            raise EvaluationError("미정산 비용이 있어 다음 회사 실행을 차단했습니다")
        result = self.client.get(f"/result/{run_id}")
        # 원본 HTML은 CSRF/접근 토큰을 포함할 수 있어 저장하지 않는다.
        metrics = {"human_quality_judgment": None, "progress_http_status": progress_status,
                   "result_http_status": result.status_code, "outcome": ledger.outcome,
                   "elapsed_wall_seconds": max(0, time.time() - row["started_at"]),
                   "internal_ai_cost_krw": ledger.cost_krw,
                   **diagnostic_metrics(diagnostics),
                   "data_absence_vs_provider_failure": "diagnostics.json을 근거로 사람이 판독",
                   "diagnostic_tables": list(diagnostics)}
        if ledger.report_id:
            if ledger.corp_id != case.corp_code or result.status_code != 200:
                raise EvaluationError("최종 보고서 회사 또는 결과 접근이 일치하지 않습니다")
            response = self.client.get(f"/download/pdf/{run_id}")
            if response.status_code != 200 or not response.content.startswith(b"%PDF-"):
                raise EvaluationError("공식 PDF 내려받기가 완료되지 않았습니다")
            _preserve_existing_artifact(output / "report.pdf")
            (output / "report.pdf").write_bytes(response.content)
            manifest_row = next(item for item in self.manifest["cases"] if item["case_id"] == case.case_id)
            terms = tuple(manifest_row.get("foreign_company_terms", ()))
            metrics["report_pdf"] = pdf_metrics(output / "report.pdf", terms)
            baseline = manifest_row.get("baseline_pdf")
            metrics["baseline_pdf"] = pdf_metrics(Path(baseline), terms) if baseline else None
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            metrics["sections"] = [{"title": section.get("title"), "cell": section.get("cell"),
                                    "empty_reason": section.get("empty_reason"),
                                    "lines": len(section.get("lines", [])),
                                    "prose_lines": len(section.get("prose_lines", [])),
                                    "tables": len(section.get("tables", []))}
                                   for section in report.get("sections", [])]
        _preserve_existing_artifact(output / "metrics.json")
        write_json(output / "metrics.json", metrics)
        row.update(state="complete", outcome=ledger.outcome, internal_ai_cost_krw=ledger.cost_krw,
                   artifacts=str(output), result_http_status=result.status_code)
        self.save()

    def export_evidence(self, run_id: str, output: Path) -> dict:
        exports = {}
        with self.bridge._connect_storage() as connection:
            connection.execute("BEGIN")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, selected in DIAGNOSTIC_EXPORTS.items():
                if table not in tables:
                    exports[table] = None
                    continue
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                usable = tuple(column for column in selected if column in columns)
                rows = connection.execute(f"SELECT {','.join(usable)} FROM {table} WHERE run_id=?", (run_id,))
                exports[table] = [dict(zip(usable, row)) for row in rows]
            report = connection.execute("SELECT payload_json FROM reports WHERE report_id=?", (run_id,)).fetchone()
            if report:
                _preserve_existing_artifact(output / "report.json")
                write_json(output / "report.json", json.loads(report[0]))
            if "report_public_projections" in tables:
                projection = connection.execute("SELECT projection_json FROM report_public_projections WHERE report_id=?", (run_id,)).fetchone()
                if projection:
                    _preserve_existing_artifact(output / "public-projection.json")
                    write_json(output / "public-projection.json", json.loads(projection[0]))
        _preserve_existing_artifact(output / "diagnostics.json")
        write_json(output / "diagnostics.json", exports)
        return exports


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="격리 서버의 공식 HTTP 기업 비교시험; 기본은 유료 호출 없는 사전검사")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--storage-db", required=True, type=Path)
    parser.add_argument("--settings-snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--batch-id", default="main", help="시험 회차; 새 값은 별도 유료 실행이므로 의도적으로만 변경")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="새 유료 요청 허용; 통합 후 별도 승인 시에만 사용")
    mode.add_argument("--resume-only", action="store_true", help="접수 완료 작업만 조회; 유료 POST 금지")
    args = parser.parse_args(argv)
    try:
        origin = canonical_loopback_origin(args.origin)
        with httpx.Client(base_url=origin, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            runner = HttpEvaluation(origin=origin, storage=args.storage_db, settings=args.settings_snapshot,
                                    manifest=args.manifest, client=client, batch_id=args.batch_id)
            print(json.dumps(runner.operate(execute=args.execute, resume_only=args.resume_only), ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print("중단했습니다. 체크포인트를 보존했으며 불확실한 유료 요청은 재전송하지 않습니다", file=sys.stderr)
        return 130
    except Exception as exc:
        # 원격 본문·토큰·경로를 포함할 수 있는 외부 예외 문자열은 출력하지 않는다.
        message = str(exc) if isinstance(exc, EvaluationError) else f"평가 중단: {type(exc).__name__}; 원본 예외와 비밀값은 출력하지 않습니다"
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

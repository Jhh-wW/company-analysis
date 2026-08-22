"""입력, 회사 확인, 조사 실행과 진행 상태 경로."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from src.core import clock, paths
from src.core.constants import MAX_RETRY_INPUT, PROGRESS_STEPS
from src.features.admin_dashboard import store as dashboard_store
from src.features.budget import spend_store
from src.features.budget.constants import (
    BUDGET_STORE_BLOCKED_MESSAGE,
    BUSY_MESSAGE,
    SPEND_PHASE_CANDIDATE,
    SPEND_PHASE_IDENTIFY,
)
from src.features.business_candidate import logic as candidate_logic
from src.features.business_candidate import providers as candidate_providers
from src.features.business_candidate.constants import CANDIDATE_ATTEMPT_TTL_SEC
from src.features.cost_tracking import store as cost_store
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.sharelink import access_control as share_access
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    ACCESS_WINDOW_SECONDS,
    KEY_COOKIE_MAX_AGE_SEC,
    KEY_COOKIE_NAME,
    KEY_PATH_PREFIX,
    PUBLIC_BUCKET,
)
from src.features.storage import db as storage_db
from src.features.storage import job_interruptions
from src.features.storage import reports as report_store
from src.web import (
    evaluation_mode,
    job_runtime,
    paid_runtime,
    public_ids,
    request_helpers,
    runtime,
)
from src.web.recording import record_end, record_run
from src.web.security import (
    ATTEMPT_TOKEN_MAX_CHARS,
    COMPANY_MAX_CHARS,
    CSRF_TOKEN_MAX_CHARS,
    REGION_MAX_CHARS,
)


logger = logging.getLogger(__name__)
router = APIRouter()
_COMPANY_ANALYSIS_PROGRESS_STEPS = tuple(
    (key, label) for key, label in PROGRESS_STEPS if key != "posting"
)
_COMPANY_ANALYSIS_PROGRESS_KEYS = frozenset(
    key for key, _label in _COMPANY_ANALYSIS_PROGRESS_STEPS
)
_PROGRESS_UNAVAILABLE_MESSAGE = (
    "진행 정보가 더 이상 남아 있지 않습니다. 서버가 다시 시작되었거나 "
    "오래된 작업의 진행 정보가 정리된 경우입니다. 결과가 저장되지 않아 "
    "이 조사는 이어서 실행할 수 없습니다."
)
_PROGRESS_INTERRUPTED_MESSAGE = (
    "서버 종료 제한시간 안에 조사를 마치지 못해 작업이 중단되었습니다. "
    "입력 오류가 아니며, 서버가 다시 열린 뒤 처음 화면에서 다시 시도해 주세요."
)


def _observe_candidate_resolution(
    resolution: candidate_logic.CandidateResolution, *, incurred_cost_krw: float = 0.0
) -> None:
    """후보 공급자의 이미 끝난 관측만 운영 상태로 옮기며 재호출하지 않는다."""
    provider = resolution.provider_name or "DART"
    if provider == "Google Maps":
        provider = "Places"
    status = resolution.status
    if status not in {
        candidate_logic.ResolutionStatus.OK,
        candidate_logic.ResolutionStatus.NO_MATCHES,
        candidate_logic.ResolutionStatus.RATE_LIMITED,
        candidate_logic.ResolutionStatus.TIMED_OUT,
        candidate_logic.ResolutionStatus.FAILED,
    }:
        return
    try:
        now_iso = clock.iso_now_kst()
        with storage_db.connect() as conn:
            if status in {candidate_logic.ResolutionStatus.OK, candidate_logic.ResolutionStatus.NO_MATCHES}:
                if resolution.provider_called:
                    dashboard_store.record_external_status(
                        conn, provider=provider, status="normal", last_success_at=now_iso, now_iso=now_iso
                    )
                return
            if status is candidate_logic.ResolutionStatus.RATE_LIMITED:
                summary = f"{provider} 후보 공급자 rate limit"
                dashboard_store.record_external_status(
                    conn, provider=provider, status="error", error_at=now_iso,
                    error_summary="rate limit", now_iso=now_iso,
                )
                dashboard_store.record_incident(
                    conn, kind=dashboard_store.INCIDENT_RATE_LIMIT, summary=summary,
                    stage="candidate_resolution", incurred_cost_krw=incurred_cost_krw, now_iso=now_iso,
                )
                return
            summary = f"{provider} 외부 서비스 응답 변경 의심"
            dashboard_store.record_external_status(
                conn, provider=provider, status="error", error_at=now_iso,
                error_summary="응답을 안전하게 해석하지 못함", now_iso=now_iso,
            )
            # 로컬 DART 후보의 profile 보강만 실패한 경우는 확인 카드 이전의 무료
            # 보조 단계다. 이 표식은 pipeline adapter가 명시적으로 붙인 경우에만
            # 허용한다. 일반 DART 장애·timeout·rate limit의 점검 경계는 유지한다.
            if (
                status is candidate_logic.ResolutionStatus.FAILED
                and resolution.local_profile_enrichment_failed
            ):
                return
            if resolution.provider_called:
                dashboard_store.record_incident(
                    conn, kind=dashboard_store.INCIDENT_PROVIDER_RESPONSE, summary=summary,
                    stage="candidate_resolution", incurred_cost_krw=incurred_cost_krw, now_iso=now_iso,
                )
    except Exception:  # noqa: BLE001 — 관측 기록이 원래의 실패 경로를 막지 않는다
        logger.exception("후보 공급자 관측을 저장하지 못했습니다")


def _was_interrupted(job_id: str) -> bool:
    with storage_db.connect() as conn:
        return job_interruptions.exists(conn, job_id)
_SHARE_NOTICE_BY_CODE = {
    "invalid": "LINK가 올바르지 않아 일반 첫 화면을 열었습니다.",
    "missing": "이 LINK는 닫혔거나 존재하지 않아 일반 첫 화면을 열었습니다.",
    "expired": "이 LINK의 사용 기간이 지나 일반 첫 화면을 열었습니다.",
    "revoked": "이 LINK가 철회되어 일반 첫 화면을 열었습니다.",
    "report-missing": (
        "연결된 기존 보고서를 찾을 수 없어 지원 맥락이 표시된 입력 화면을 열었습니다."
    ),
    "report-expired": (
        "연결된 기존 보고서의 공유 기간이 지나 지원 맥락이 표시된 입력 화면을 열었습니다."
    ),
}
_REPORT_NOTICE_BY_CODE = {
    "unavailable": (
        "요청한 보고서를 열 수 없어 일반 첫 화면을 열었습니다. "
        "주소가 오래됐거나 현재 볼 수 없는 상태일 수 있습니다."
    ),
}


def _clear_share_cookie(response, request: Request) -> None:
    """이전 LINK capability가 있을 때만 같은 범위의 만료 쿠키로 지운다."""
    if KEY_COOKIE_NAME not in request.cookies:
        return
    response.delete_cookie(
        KEY_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request_helpers._cookie_secure(request),
    )


def _share_redirect_without_cookie(request: Request, status: str):
    response = RedirectResponse(f"/?share_status={status}", status_code=303)
    response.headers["Referrer-Policy"] = "no-referrer"
    _clear_share_cookie(response, request)
    return response


def _invalid_share_key_response(request: Request) -> Response:
    """열쇠 모양 자체가 틀리면 존재 여부를 숨기고 이전 권한도 지운다."""
    response = Response(status_code=404)
    _clear_share_cookie(response, request)
    return response


def _share_store_unavailable(request: Request):
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="share_unavailable.html",
        context=request_helpers._ctx(request),
        status_code=503,
    )
    _clear_share_cookie(response, request)
    return response


def _share_rate_limited() -> Response:
    """capability 존재 정보나 원문을 로그·응답에 덧붙이지 않는 429 응답."""

    return PlainTextResponse(
        "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
        status_code=429,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Retry-After": str(ACCESS_WINDOW_SECONDS),
        },
    )


@router.get("/", response_class=HTMLResponse)
async def input_page(request: Request):
    """회사명·직무·주소·공고를 받는 첫 화면."""
    share_link = request_helpers._current_share_link(request)
    prefill = None
    if share_link is not None:
        prefill = UserInput(
            company=share_link.company,
            job=share_link.job,
            region="",
            posting_text="",
        )
    share_notice = _SHARE_NOTICE_BY_CODE.get(
        request.query_params.get("share_status", ""), ""
    )
    report_notice = _REPORT_NOTICE_BY_CODE.get(
        request.query_params.get("report_status", ""), ""
    )
    notices = " ".join(
        notice for notice in (share_notice, report_notice) if notice
    )
    return request_helpers.templates.TemplateResponse(
        request=request,
        name="input.html",
        context=request_helpers._ctx(
            request,
            demo_companies=available_companies(),
            demo_available=paths.demo_data_available(),
            share_link=share_link,
            prefill=prefill,
            share_notice=notices,
            evaluation_workflow_id=evaluation_mode.issue_workflow_id(),
        ),
    )


@router.get(KEY_PATH_PREFIX + "/{key}")
async def open_share_link(request: Request, key: str):
    """LINK 요청을 기록하고 유효하면 시작 보고서 또는 입력 화면으로 보낸다."""
    clean = (key or "").strip().lower()
    if not share_logic.is_valid_key(clean):
        return _invalid_share_key_response(request)

    link = None
    target = "/"
    try:
        with storage_db.connect() as conn:
            stored = share_store.load(conn, clean)
            if stored is None:
                return _share_redirect_without_cookie(request, "missing")
            if stored.is_revoked:
                return _share_redirect_without_cookie(request, "revoked")
            if share_logic.is_share_link_expired(stored.created_at):
                return _share_redirect_without_cookie(request, "expired")

            now_iso = clock.iso_now_kst()
            client_host = request.client.host if request.client is not None else ""
            if not share_access.allow_request(clean, client_host, now_iso):
                return _share_rate_limited()
            requester_hash = share_access.requester_hash_of(clean, client_host)
            if not share_store.mark_opened(
                conn,
                clean,
                now_iso,
                requester_hash=requester_hash,
            ):
                # DB writer lock 안에서 폐기·만료가 다시 확인된다. 사전 load 뒤
                # 상태가 바뀐 경우에는 권한 상태를, 그대로 active면 영속 cap을
                # 의미하므로 429를 돌려준다.
                latest = share_store.load(conn, clean)
                if latest is None:
                    return _share_redirect_without_cookie(request, "missing")
                if latest.is_revoked:
                    return _share_redirect_without_cookie(request, "revoked")
                if share_logic.is_share_link_expired(latest.created_at):
                    return _share_redirect_without_cookie(request, "expired")
                return _share_rate_limited()
            link = stored

            if link.report_id:
                if dashboard_store.report_is_trashed(conn, link.report_id):
                    target = "/?share_status=report-missing"
                else:
                    report = report_store.load(conn, link.report_id)
                    if report is None:
                        target = "/?share_status=report-missing"
                    elif job_runtime._link_expired(report):
                        target = "/?share_status=report-expired"
                    else:
                        target = f"/result/{link.report_id}"
    except Exception:  # noqa: BLE001 — 인가 저장소 장애에서는 이전 권한도 정리한다
        logger.exception("LINK를 확인하거나 요청 기록을 저장하지 못했습니다")
        return _share_store_unavailable(request)

    if link is None:
        return _share_redirect_without_cookie(request, "missing")

    response = RedirectResponse(target, status_code=303)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.set_cookie(
        KEY_COOKIE_NAME,
        clean,
        max_age=KEY_COOKIE_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=request_helpers._cookie_secure(request),
    )
    return response


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """검색 로봇에 capability 경로를 경로 단위로 수집하지 말라고 알린다."""
    return PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /k/\n"
        "Disallow: /result/\n"
        "Disallow: /download/\n"
    )


async def _resolve_business_candidates(
    request: Request,
    *,
    provider: object,
    user_input: UserInput,
    resolved_track: tuple[share_tracks.Track, str, float | None],
    allow_paid_provider: bool,
    analysis_run_id: str,
) -> tuple[candidate_logic.CandidateResolution, float] | Response:
    """회사 후보 공급자 하나를 slot·rate·비용 경계 안에서 정확히 한 번 실행한다."""
    slot_bucket_id = (
        paid_runtime._reserve_run_slot(resolved_track[0], resolved_track[1]) or ""
    )
    if not slot_bucket_id:
        return request_helpers._throttled(request, BUSY_MESSAGE, "candidate-busy")

    provider_is_paid = bool(getattr(provider, "costs_money", True))
    phase: Optional[paid_runtime.PaidPhase] = None
    task: Optional[asyncio.Task[candidate_logic.CandidateResolution]] = None
    settled = False
    settled_cost = 0.0
    client_host = request.client.host if request.client is not None else ""
    kwargs = dict(
        company=user_input.company,
        address_hint=user_input.region,
        # User-Agent는 사용자가 바꿀 수 있어 rate key에 절대 넣지 않는다.
        rate_key=candidate_logic.anonymous_rate_key(resolved_track[1], client_host),
        allow_paid_provider=allow_paid_provider,
    )

    def settle(result: candidate_logic.CandidateResolution | None) -> None:
        nonlocal settled, settled_cost
        if phase is None or settled:
            return
        settled = True
        if result is not None and not result.provider_called:
            paid_runtime._cancel_paid_phase(phase)
            return
        uncertain = result is None or result.status in {
            candidate_logic.ResolutionStatus.TIMED_OUT,
            candidate_logic.ResolutionStatus.FAILED,
        }
        amount = 0.0
        if not uncertain:
            try:
                amount = max(0.0, float(getattr(provider, "accounting_cost_krw", 0.0)))
            except (TypeError, ValueError, OverflowError):
                uncertain = True
                amount = 0.0
        paid_runtime._settle_paid_phase(
            phase, amount_krw=amount, billing_uncertain=uncertain
        )
        settled_cost = amount

    try:
        if provider_is_paid:
            if not allow_paid_provider:
                return candidate_logic.CandidateResolution(
                    candidate_logic.ResolutionStatus.UNCONFIGURED,
                    provider_name=candidate_logic.canonical_provider_name(
                        getattr(provider, "provider_name", "")
                    ),
                ), 0.0
            requested = float(getattr(provider, "accounting_cost_krw", 0.0))
            if requested <= 0:
                raise ValueError("유료 회사 후보 공급자의 예상비용이 없습니다")
            phase = paid_runtime._begin_paid_phase(
                run_id=analysis_run_id,
                phase=SPEND_PHASE_CANDIDATE,
                share_key=resolved_track[1],
                cap_krw=resolved_track[2],
                requested_cost_krw=requested,
            )
            if phase is None:
                return request_helpers._throttled(
                    request, BUSY_MESSAGE, "candidate-budget-store"
                )
            task = asyncio.create_task(
                asyncio.to_thread(
                    paid_runtime._call_paid_provider,
                    phase,
                    candidate_logic.resolve_candidates,
                    provider,
                    **kwargs,
                )
            )
        else:
            task = asyncio.create_task(
                asyncio.to_thread(candidate_logic.resolve_candidates, provider, **kwargs)
            )
        result = await asyncio.shield(task)
        settle(result)
        return result, settled_cost
    except asyncio.CancelledError:
        try:
            if task is None:
                raise RuntimeError("회사 후보 worker가 만들어지지 않았습니다")
            result = await job_runtime._await_worker_after_cancel(task)
        except BaseException:  # noqa: BLE001 — 호출 여부를 모르면 비용 경계를 닫는다
            settle(None)
        else:
            settle(result)
        raise
    except Exception:  # noqa: BLE001 — 공급자 세부 오류를 화면·로그에 싣지 않는다
        logger.exception("회사 후보 검색 경계를 실행하지 못했습니다")
        settle(None)
        return candidate_logic.CandidateResolution(
            candidate_logic.ResolutionStatus.FAILED,
            provider_called=provider_is_paid,
            provider_name=candidate_logic.canonical_provider_name(
                getattr(provider, "provider_name", "")
            ),
        ), settled_cost
    finally:
        if phase is not None and not settled and task is None:
            paid_runtime._cancel_paid_phase(phase)
        paid_runtime._release_run_slot(slot_bucket_id)


def _register_candidate_attempt(
    *,
    user_input: UserInput,
    candidates: tuple[candidate_logic.BusinessCandidate, ...],
    run_id: str,
    share_key: str,
    candidate_cost_krw: float,
    elapsed_sec: float,
    evaluation_paid_consent: bool,
) -> tuple[str, list[dict[str, object]]]:
    """Places 콘텐츠를 저장하지 않고 후보별 HMAC 선택값만 브라우저에 보낸다."""
    for _ in range(8):
        token = uuid.uuid4().hex
        if token not in job_runtime._CANDIDATE_ATTEMPTS:
            break
    else:
        raise public_ids.PublicIdUnavailable
    job_runtime._CANDIDATE_ATTEMPTS[token] = job_runtime.CandidateAttempt(
        token=token,
        run_id=run_id,
        user_input=user_input,
        candidate_count=len(candidates),
        share_key=share_key,
        bucket_id=spend_store.bucket_id(share_key),
        candidate_cost_krw=candidate_cost_krw,
        elapsed_sec=elapsed_sec,
        posting_image_consent=False,
        evaluation_paid_consent=evaluation_paid_consent,
        created_at=time.monotonic(),
    )
    return token, [
        {
            "candidate": candidate,
            "candidate_index": index,
            "candidate_selection_token": candidate_logic.candidate_selection_token(
                binding=f"{token}:{index}:{spend_store.bucket_id(share_key)}",
                original_company=user_input.company,
                job=user_input.job,
                address_hint=user_input.region,
                candidate_name=candidate.candidate_name,
                provider_name=candidate.provider_name,
                candidate_ref=candidate.candidate_ref,
            ),
        }
        for index, candidate in enumerate(candidates)
    ]


def _register_candidate_search_grant(
    *,
    user_input: UserInput,
    share_key: str,
    evaluation_paid_consent: bool,
) -> str:
    """Google fallback 직접 POST·재과금을 막는 5분짜리 opaque grant."""
    for _ in range(8):
        token = uuid.uuid4().hex
        if token not in job_runtime._CANDIDATE_SEARCH_GRANTS:
            break
    else:
        raise public_ids.PublicIdUnavailable
    job_runtime._CANDIDATE_SEARCH_GRANTS[token] = job_runtime.CandidateSearchGrant(
        token=token,
        user_input=user_input,
        share_key=share_key,
        bucket_id=spend_store.bucket_id(share_key),
        posting_image_consent=False,
        evaluation_paid_consent=evaluation_paid_consent,
        created_at=time.monotonic(),
    )
    return token


async def _run_paid_company_lookup(
    *,
    run_id: str,
    share_key: str,
    cap_krw: float | None,
    user_input: UserInput,
    lookup_input: UserInput,
    selected_candidate_ref: str,
) -> CompanyLookupResult | None:
    """회사 식별 provider와 비용 표식의 수명을 한 context로 묶는다."""

    with paid_runtime.paid_phase(
        run_id=run_id,
        phase=SPEND_PHASE_IDENTIFY,
        share_key=share_key,
        cap_krw=cap_krw,
    ) as phase:
        if phase.ticket is None:
            return None
        provider = (
            request_helpers._find_company_by_ref_metered
            if selected_candidate_ref
            else request_helpers._find_company_metered
        )
        provider_args = (
            (user_input, selected_candidate_ref)
            if selected_candidate_ref
            else (lookup_input,)
        )
        phase.mark_provider_started()
        worker = asyncio.create_task(
            asyncio.to_thread(
                paid_runtime._call_paid_provider,
                phase.ticket,
                provider,
                *provider_args,
            )
        )
        try:
            result = await asyncio.shield(worker)
            if not isinstance(result, CompanyLookupResult):
                raise TypeError("회사 식별 결과 계약이 올바르지 않습니다")
        except asyncio.CancelledError:
            try:
                result = await job_runtime._await_worker_after_cancel(worker)
                if not isinstance(result, CompanyLookupResult):
                    raise TypeError("회사 식별 결과 계약이 올바르지 않습니다")
            except BaseException:
                # context가 provider_started를 보고 미확정으로 정확히 한 번 닫는다.
                raise
            phase.settle(
                amount_krw=result.cost_krw,
                billing_uncertain=result.billing_uncertain,
            )
            raise
        except Exception:  # noqa: BLE001 — context가 미확정 비용으로 닫는다
            logger.exception("회사 식별 연결이 실패했습니다")
            return CompanyLookupResult(card=None, failed=True, billing_uncertain=True)

        phase.settle(
            amount_krw=result.cost_krw,
            billing_uncertain=result.billing_uncertain,
        )
        return result


@router.post("/confirm", response_class=HTMLResponse)
async def confirm_page(
    request: Request,
    company: str = Form(..., max_length=COMPANY_MAX_CHARS),
    region: str = Form("", max_length=REGION_MAX_CHARS),
    retry: int = Form(0, ge=0, le=MAX_RETRY_INPUT),
    candidate_resolution_confirmed: str = Form("", max_length=8),
    candidate_attempt_token: str = Form("", max_length=128),
    candidate_search_requested: str = Form("", max_length=8),
    candidate_selection_token: str = Form("", max_length=128),
    candidate_search_grant: str = Form("", max_length=128),
    candidate_index: int = Form(-1, ge=-1, le=2),
    candidate_name: str = Form("", max_length=120),
    candidate_provider: str = Form("", max_length=80),
    candidate_ref: str = Form("", max_length=80),
    evaluation_paid_consent: str = Form("", max_length=8),
    evaluation_consent_grant: str = Form("", max_length=128),
    evaluation_workflow_id: str = Form("", max_length=128),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """찾은 회사 하나를 보여주고 [맞습니다]/[아닙니다]를 받는다."""
    blocked = request_helpers.require_analysis_action_csrf(request, csrf_token)
    if blocked is not None:
        return blocked

    user_input = request_helpers.company_analysis_input(
        company=company,
        region=region,
    )
    if not job_runtime._accepting_jobs():
        return job_runtime._admission_unavailable_response(request)

    resolved_track = request_helpers._track_of(request)
    link_blocked = request_helpers.require_active_share_link(
        request,
        resolved_track=resolved_track,
    )
    if link_blocked is not None:
        return link_blocked
    consent_blocked, evaluation_consent_grant_value = (
        request_helpers.evaluation_consent_roundtrip(
            request,
            user_input=user_input,
            bucket=resolved_track[1],
            received=evaluation_paid_consent,
            grant=evaluation_consent_grant,
            workflow_id=evaluation_workflow_id,
            allow_issue=bool(
                candidate_resolution_confirmed != "yes"
                and candidate_search_requested != "yes"
                and not candidate_attempt_token
            ),
        )
    )
    if consent_blocked is not None:
        return consent_blocked
    evaluation_consent_ok = bool(evaluation_consent_grant_value)
    if retry >= MAX_RETRY_INPUT:
        return request_helpers._retry_screen(
            request,
            user_input,
            retry,
            rejected=False,
            evaluation_consent_grant=evaluation_consent_grant_value,
        )

    is_paid = not isinstance(runtime._PIPELINE, DemoPipeline)
    share_key = resolved_track[1] if is_paid else PUBLIC_BUCKET
    run_id = ""
    lookup_input = user_input
    lookup_started = time.perf_counter()
    candidate_upfront_cost = 0.0
    candidate_upfront_elapsed = 0.0
    slot_bucket_id = ""
    candidate_resolution: candidate_logic.CandidateResolution | None = None
    selected_candidate_ref = ""

    candidate_attempt: Optional[job_runtime.CandidateAttempt] = None
    if candidate_resolution_confirmed == "yes":
        candidate_attempt = job_runtime._CANDIDATE_ATTEMPTS.pop(
            candidate_attempt_token, None
        )
        current_bucket = spend_store.bucket_id(share_key)
        canonical_candidate_name = candidate_logic.canonical_candidate_name(
            candidate_name
        )
        canonical_candidate_provider = candidate_logic.canonical_provider_name(
            candidate_provider
        )
        canonical_candidate_ref = candidate_logic.canonical_candidate_ref(candidate_ref)
        if (
            candidate_attempt is None
            or (
                time.monotonic() - candidate_attempt.created_at
                > CANDIDATE_ATTEMPT_TTL_SEC
            )
            or candidate_attempt.user_input != user_input
            or candidate_attempt.bucket_id != current_bucket
            or candidate_attempt.posting_image_consent
            or candidate_attempt.evaluation_paid_consent
            != evaluation_consent_ok
            or not (0 <= candidate_index < candidate_attempt.candidate_count)
            # HMAC은 canonical text를 서명한다. raw form이 같은 canonical 값으로
            # 줄어드는 HTML/제어문자/과잉공백 변조라면 서명이 같아도 거부한다.
            or candidate_name != canonical_candidate_name
            or candidate_provider != canonical_candidate_provider
            or candidate_ref != canonical_candidate_ref
            or bool(
                canonical_candidate_provider == "DART"
                and (
                    len(canonical_candidate_ref) != 8
                    or not canonical_candidate_ref.isdigit()
                )
            )
            or bool(
                canonical_candidate_provider != "DART"
                and canonical_candidate_ref
            )
            or not candidate_logic.valid_candidate_selection_token(
                candidate_selection_token,
                binding=(
                    f"{candidate_attempt_token}:{candidate_index}:"
                    f"{current_bucket}"
                ),
                original_company=user_input.company,
                job=user_input.job,
                address_hint=user_input.region,
                candidate_name=canonical_candidate_name,
                provider_name=canonical_candidate_provider,
                candidate_ref=canonical_candidate_ref,
            )
        ):
            if candidate_attempt is not None:
                public_ids.release(candidate_attempt.run_id)
            return PlainTextResponse("유효하지 않거나 만료된 회사 후보 선택입니다.", status_code=403)
        lookup_input = UserInput(
            company=canonical_candidate_name,
            job=user_input.job,
            region=user_input.region,
            posting_text=user_input.posting_text,
        )
        selected_candidate_ref = canonical_candidate_ref
        run_id = candidate_attempt.run_id
        candidate_upfront_cost = candidate_attempt.candidate_cost_krw
        candidate_upfront_elapsed = candidate_attempt.elapsed_sec

    if is_paid:
        blocked = request_helpers._guard_run(
            request, count_start=False, resolved_track=resolved_track
        )
        if blocked is not None:
            if candidate_attempt is not None:
                public_ids.release(candidate_attempt.run_id)
            return blocked

        # 1단계: 돈이 들지 않는 DART corpCode 로컬 색인의 좁은 별칭 후보를 먼저 쓴다.
        # 정확한 이름은 기존 식별 경로가 처리하고, fuzzy 후보는 항상 사람이 고른다.
        candidate_assistance_allowed = True
        if (
            candidate_assistance_allowed
            and candidate_attempt is None
            and candidate_search_requested != "yes"
        ):
            local_provider = candidate_providers.configured_local_provider(runtime._PIPELINE)
            if local_provider is not None:
                try:
                    candidate_run_id = public_ids.reserve(job_runtime._JOBS)
                except public_ids.PublicIdUnavailable:
                    return job_runtime._storage_unavailable_response(request)
                candidate_started = time.perf_counter()
                local_outcome = await _resolve_business_candidates(
                    request,
                    provider=local_provider,
                    user_input=user_input,
                    resolved_track=resolved_track,
                    allow_paid_provider=False,
                    analysis_run_id=candidate_run_id,
                )
                if isinstance(local_outcome, Response):
                    public_ids.release(candidate_run_id)
                    return local_outcome
                candidate_resolution, _ = local_outcome
                candidate_elapsed = time.perf_counter() - candidate_started
                _observe_candidate_resolution(candidate_resolution)
                # 회사명·주소는 남기지 않는다. cold-start와 공급자 장애를 운영에서
                # 구분할 수 있는 최소 비식별 경계만 기록한다.
                logger.info(
                    "business_candidate provider=%s status=%s duration_ms=%d candidates=%d",
                    candidate_resolution.provider_name or "DART",
                    candidate_resolution.status.value,
                    round(candidate_elapsed * 1000),
                    len(candidate_resolution.candidates),
                )
                if candidate_resolution.candidates:
                    try:
                        attempt_token, candidate_options = _register_candidate_attempt(
                            user_input=user_input,
                            candidates=candidate_resolution.candidates,
                            run_id=candidate_run_id,
                            share_key=share_key,
                            candidate_cost_krw=0.0,
                            elapsed_sec=candidate_elapsed,
                            evaluation_paid_consent=evaluation_consent_ok,
                        )
                    except public_ids.PublicIdUnavailable:
                        public_ids.release(candidate_run_id)
                        return job_runtime._storage_unavailable_response(request)
                    return request_helpers.templates.TemplateResponse(
                        request=request,
                        name="company_candidates.html",
                        context=request_helpers._ctx(
                            request,
                            user_input=user_input,
                            candidate_options=candidate_options,
                            candidate_attempt_token=attempt_token,
                            retry=retry,
                            candidate_search_cost_krw=0.0,
                            evaluation_consent_grant=evaluation_consent_grant_value,
                        ),
                    )
                public_ids.release(candidate_run_id)
                # 실제 0건만 기존 DART identify로 진행한다. local 색인의 timeout,
                # 장애, rate-limit을 회사 없음으로 접어 유료 AI/Naver/Google을
                # 호출하면 원인도 비용도 왜곡되므로 기술 재시도 화면에서 끝낸다.
                if (
                    candidate_resolution.status
                    is not candidate_logic.ResolutionStatus.NO_MATCHES
                ):
                    return request_helpers._retry_screen(
                        request,
                        user_input,
                        retry,
                        rejected=False,
                        candidate_resolution=candidate_resolution,
                        evaluation_consent_grant=evaluation_consent_grant_value,
                    )

        # 3단계: DART 식별이 앞선 요청에서 0건이었고, 그 화면이 발급한 짧은 grant와
        # 사용자의 Google 전송 버튼/유료동의가 모두 있을 때만 Places를 한 번 부른다.
        if candidate_attempt is None and candidate_search_requested == "yes":
            grant = job_runtime._CANDIDATE_SEARCH_GRANTS.get(candidate_search_grant)
            current_bucket = spend_store.bucket_id(share_key)
            if (
                grant is None
                or (
                    time.monotonic() - grant.created_at
                    > CANDIDATE_ATTEMPT_TTL_SEC
                )
                or grant.user_input != user_input
                or grant.bucket_id != current_bucket
                or grant.posting_image_consent
                or grant.evaluation_paid_consent
                != evaluation_consent_ok
            ):
                return PlainTextResponse("회사 후보 검색 요청이 만료되었거나 올바르지 않습니다.", status_code=403)
            if grant.in_flight:
                return PlainTextResponse(
                    "같은 회사 후보 검색이 이미 진행 중입니다.", status_code=409
                )
            if grant.candidate_attempt_token:
                return PlainTextResponse(
                    "회사 후보는 이미 한 번 표시했습니다. 개인정보 보호를 위해 "
                    "검색 결과를 서버에 보관하지 않으며, Google을 다시 호출하지도 "
                    "않습니다. 처음 화면에서 다시 시작해 주세요.",
                    status_code=410,
                )
            if grant.resolution_status:
                cached_resolution = candidate_logic.CandidateResolution(
                    candidate_logic.ResolutionStatus(grant.resolution_status),
                    provider_called=True,
                    provider_name="Google Maps",
                )
                return request_helpers._retry_screen(
                    request,
                    user_input,
                    retry,
                    rejected=False,
                    candidate_resolution=cached_resolution,
                    evaluation_consent_grant=evaluation_consent_grant_value,
                )
            google_allowed = bool(
                candidate_assistance_allowed
                and evaluation_mode.paid_providers_enabled()
                and evaluation_consent_ok
                and request_helpers._strict_loopback_http_request(request)
            )
            provider = candidate_providers.configured_provider(
                runtime._PIPELINE, allow_paid_google=google_allowed
            )
            if provider is None or not bool(getattr(provider, "costs_money", True)):
                candidate_resolution = candidate_logic.CandidateResolution(
                    candidate_logic.ResolutionStatus.UNCONFIGURED
                )
                grant.resolution_status = candidate_resolution.status.value
                return request_helpers._retry_screen(
                    request,
                    user_input,
                    retry,
                    rejected=False,
                    candidate_resolution=candidate_resolution,
                    evaluation_consent_grant=evaluation_consent_grant_value,
                )
            try:
                candidate_run_id = public_ids.reserve(job_runtime._JOBS)
            except public_ids.PublicIdUnavailable:
                return job_runtime._storage_unavailable_response(request)
            candidate_started = time.perf_counter()
            grant.in_flight = True
            try:
                try:
                    external_outcome = await _resolve_business_candidates(
                        request,
                        provider=provider,
                        user_input=user_input,
                        resolved_track=resolved_track,
                        allow_paid_provider=True,
                        analysis_run_id=candidate_run_id,
                    )
                except asyncio.CancelledError:
                    # helper가 worker 완료와 비용 정산까지 기다린 뒤 전파한다. 응답을
                    # 잃은 상태에서 같은 grant로 재호출하면 이중 과금이므로 재시도는
                    # fail-closed하고 같은 analysis id 예약을 반환한다.
                    grant.resolution_status = candidate_logic.ResolutionStatus.FAILED.value
                    public_ids.release(candidate_run_id)
                    raise
            finally:
                grant.in_flight = False
            if isinstance(external_outcome, Response):
                public_ids.release(candidate_run_id)
                return external_outcome
            candidate_resolution, candidate_search_cost_krw = external_outcome
            _observe_candidate_resolution(
                candidate_resolution, incurred_cost_krw=candidate_search_cost_krw
            )
            if candidate_resolution.candidates:
                try:
                    attempt_token, candidate_options = _register_candidate_attempt(
                        user_input=user_input,
                        candidates=candidate_resolution.candidates,
                        run_id=candidate_run_id,
                        share_key=share_key,
                        candidate_cost_krw=candidate_search_cost_krw,
                        elapsed_sec=time.perf_counter() - candidate_started,
                        evaluation_paid_consent=evaluation_consent_ok,
                    )
                except public_ids.PublicIdUnavailable:
                    public_ids.release(candidate_run_id)
                    return job_runtime._storage_unavailable_response(request)
                grant.candidate_attempt_token = attempt_token
                return request_helpers.templates.TemplateResponse(
                    request=request,
                    name="company_candidates.html",
                    context=request_helpers._ctx(
                        request,
                        user_input=user_input,
                        candidate_options=candidate_options,
                        candidate_attempt_token=attempt_token,
                        retry=retry,
                        candidate_search_cost_krw=candidate_search_cost_krw,
                        evaluation_consent_grant=evaluation_consent_grant_value,
                    ),
                )
            public_ids.release(candidate_run_id)
            grant.resolution_status = candidate_resolution.status.value
            return request_helpers._retry_screen(
                request,
                user_input,
                retry,
                rejected=False,
                candidate_resolution=candidate_resolution,
                evaluation_consent_grant=evaluation_consent_grant_value,
            )

        # 후보를 고른 뒤에만 그 일시 이름을 DART에 재검증한다. 공유 범위와 최종 보고서
        # 입력은 위의 원래 user_input 그대로이며 Google 이름은 DB에 쓰지 않는다.
        blocked = request_helpers._guard_run(request, resolved_track=resolved_track)
        if blocked is not None:
            if candidate_attempt is not None:
                public_ids.release(run_id)
            return blocked
        slot_bucket_id = (
            paid_runtime._reserve_run_slot(resolved_track[0], share_key) or ""
        )
        if not slot_bucket_id:
            if candidate_attempt is not None:
                public_ids.release(run_id)
            return request_helpers._throttled(request, BUSY_MESSAGE, "busy")
        try:
            if not run_id:
                run_id = public_ids.reserve(job_runtime._JOBS)
        except public_ids.PublicIdUnavailable:
            paid_runtime._release_run_slot(slot_bucket_id)
            return job_runtime._storage_unavailable_response(request)
        except BaseException:
            public_ids.release(run_id)
            paid_runtime._release_run_slot(slot_bucket_id)
            raise

    try:
        try:
            if is_paid:
                lookup = await _run_paid_company_lookup(
                    run_id=run_id,
                    share_key=share_key,
                    cap_krw=resolved_track[2],
                    user_input=user_input,
                    lookup_input=lookup_input,
                    selected_candidate_ref=selected_candidate_ref,
                )
                if lookup is None:
                    public_ids.release(run_id)
                    return request_helpers._throttled(
                        request, BUDGET_STORE_BLOCKED_MESSAGE, "budget-store"
                    )
            else:
                lookup = await asyncio.to_thread(
                    (
                        request_helpers._find_company_by_ref_metered
                        if selected_candidate_ref
                        else request_helpers._find_company_metered
                    ),
                    *(
                        (user_input, selected_candidate_ref)
                        if selected_candidate_ref
                        else (lookup_input,)
                    ),
                )
            if not isinstance(lookup, CompanyLookupResult):
                raise TypeError("회사 식별 결과 계약이 올바르지 않습니다")
        except asyncio.CancelledError:
            public_ids.release(run_id)
            raise
        except Exception:  # noqa: BLE001 — 계량 계약 오류도 회사 없음으로 바꾸지 않는다
            logger.exception("회사 식별 연결이 실패했습니다")
            lookup = CompanyLookupResult(
                card=None,
                failed=True,
                billing_uncertain=is_paid,
            )

        lookup_elapsed = (
            time.perf_counter() - lookup_started + candidate_upfront_elapsed
        )
        total_lookup_cost = lookup.cost_krw + candidate_upfront_cost
    finally:
        if is_paid:
            paid_runtime._release_run_slot(slot_bucket_id)

    card = lookup.card
    if lookup.failed or lookup.billing_uncertain:
        public_ids.release(run_id)
        if is_paid:
            with storage_db.connect() as conn:
                cost_store.record_run_costs(
                    conn,
                    run_id=run_id,
                    outcome=Outcome.FAILED,
                    internal_ai_cost_krw=total_lookup_cost,
                    events=lookup.ai_cost_events,
                )
            record_end(
                run_id=run_id,
                job=user_input.job,
                end_step=obs.END_STEP_IDENTIFY_ERROR,
                cost_krw=total_lookup_cost,
                elapsed_sec=lookup_elapsed,
                model=lookup.model,
            )
        return request_helpers._lookup_failed_screen(request)
    if card is None:
        public_ids.release(run_id)
        if is_paid:
            with storage_db.connect() as conn:
                cost_store.record_run_costs(
                    conn,
                    run_id=run_id,
                    outcome=Outcome.NOT_FOUND,
                    internal_ai_cost_krw=total_lookup_cost,
                    events=lookup.ai_cost_events,
                )
            record_run(
                user_input,
                RunResult(
                    outcome=Outcome.NOT_FOUND,
                    cost_krw=total_lookup_cost,
                    model=lookup.model,
                ),
                lookup_elapsed,
                run_id=run_id,
            )
        google_allowed = bool(
            is_paid
            and candidate_attempt is None
            and evaluation_mode.paid_providers_enabled()
            and evaluation_consent_ok
            and request_helpers._strict_loopback_http_request(request)
        )
        google_provider = (
            candidate_providers.configured_provider(
                runtime._PIPELINE, allow_paid_google=True
            )
            if google_allowed
            else None
        )
        candidate_search_available = bool(
            google_provider is not None
            and bool(getattr(google_provider, "costs_money", True))
        )
        candidate_search_grant_value = ""
        if candidate_search_available:
            try:
                candidate_search_grant_value = _register_candidate_search_grant(
                    user_input=user_input,
                    share_key=share_key,
                    evaluation_paid_consent=evaluation_consent_ok,
                )
            except public_ids.PublicIdUnavailable:
                candidate_search_available = False
        return request_helpers._retry_screen(
            request,
            user_input,
            retry,
            rejected=False,
            candidate_resolution=candidate_resolution,
            candidate_was_selected=candidate_resolution_confirmed == "yes",
            candidate_search_available=candidate_search_available,
            candidate_search_grant=candidate_search_grant_value,
            candidate_search_cost_krw=(
                float(getattr(google_provider, "accounting_cost_krw", 0.0))
                if candidate_search_available
                else 0.0
            ),
            evaluation_consent_grant=evaluation_consent_grant_value,
        )

    attempt_token = uuid.uuid4().hex
    lookup_models: tuple[str, ...] = ()
    if is_paid:
        lookup_models = paid_runtime._model_tuple(lookup.model)
        if not paid_runtime._begin_observation_pending(
            run_id=run_id,
            job=user_input.job,
            cost_krw=total_lookup_cost,
            elapsed_sec=lookup_elapsed,
            model=paid_runtime._model_label(lookup_models),
        ):
            public_ids.release(run_id)
            with storage_db.connect() as conn:
                cost_store.record_run_costs(
                    conn,
                    run_id=run_id,
                    outcome=Outcome.FAILED,
                    internal_ai_cost_krw=total_lookup_cost,
                    events=lookup.ai_cost_events,
                )
            return request_helpers._throttled(
                request, BUSY_MESSAGE, "observation-store"
            )
    attempt_share_key = resolved_track[1]
    job_runtime._PAID_ATTEMPTS[attempt_token] = job_runtime.PaidAttempt(
        token=attempt_token,
        run_id=run_id,
        user_input=user_input,
        card=card,
        share_key=attempt_share_key,
        bucket_id=spend_store.bucket_id(attempt_share_key),
        lookup_cost_krw=total_lookup_cost if is_paid else 0.0,
        models=lookup_models,
        elapsed_sec=lookup_elapsed if is_paid else 0.0,
        created_at=time.monotonic(),
        is_paid=is_paid,
        cost_events=lookup.ai_cost_events if is_paid else (),
    )

    return request_helpers.templates.TemplateResponse(
        request=request,
        name="confirm.html",
        context=request_helpers._ctx(
            request,
            user_input=user_input,
            card=card,
            retry=retry,
            paid_attempt_token=attempt_token,
            evaluation_consent_grant=evaluation_consent_grant_value,
        ),
    )


@router.post("/reject", response_class=HTMLResponse)
async def reject_card(
    request: Request,
    company: str = Form(..., max_length=COMPANY_MAX_CHARS),
    region: str = Form("", max_length=REGION_MAX_CHARS),
    retry: int = Form(0, ge=0, le=MAX_RETRY_INPUT),
    paid_attempt_token: str = Form(
        "", max_length=ATTEMPT_TOKEN_MAX_CHARS
    ),
    evaluation_consent_grant: str = Form("", max_length=128),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """확인 카드에서 [아닙니다]를 눌렀을 때 회사명을 다시 받는다."""
    blocked = request_helpers.require_analysis_action_csrf(request, csrf_token)
    if blocked is not None:
        return blocked

    user_input = request_helpers.company_analysis_input(
        company=company,
        region=region,
    )
    resolved_track = request_helpers._track_of(request)
    consent_blocked, evaluation_consent_grant_value = (
        request_helpers.evaluation_consent_roundtrip(
            request,
            user_input=user_input,
            bucket=resolved_track[1],
            received="",
            grant=evaluation_consent_grant,
            workflow_id="",
            allow_issue=False,
        )
    )
    if consent_blocked is not None:
        return consent_blocked
    if paid_attempt_token:
        job_runtime._abandon_confirmation_attempt(paid_attempt_token)
    return request_helpers._retry_screen(
        request,
        user_input,
        retry,
        rejected=True,
        evaluation_consent_grant=evaluation_consent_grant_value,
    )


@router.post("/run")
async def start_run(
    request: Request,
    company: str = Form(..., max_length=COMPANY_MAX_CHARS),
    region: str = Form("", max_length=REGION_MAX_CHARS),
    paid_attempt_token: str = Form(
        "", max_length=ATTEMPT_TOKEN_MAX_CHARS
    ),
    evaluation_consent_grant: str = Form("", max_length=128),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """확인한 회사로 조사 작업을 시작하고 진행 화면으로 보낸다."""
    blocked = request_helpers.require_analysis_action_csrf(request, csrf_token)
    if blocked is not None:
        return blocked
    if not job_runtime._accepting_jobs():
        return job_runtime._admission_unavailable_response(request)

    original_input = request_helpers.company_analysis_input(
        company=company,
        region=region,
    )
    is_paid = not isinstance(runtime._PIPELINE, DemoPipeline)
    attempt: Optional[job_runtime.PaidAttempt] = None
    slot_bucket_id = ""

    resolved_track = request_helpers._track_of(request)
    link_blocked = request_helpers.require_active_share_link(
        request,
        resolved_track=resolved_track,
    )
    if link_blocked is not None:
        return link_blocked
    share_key = resolved_track[1]
    consent_blocked, _evaluation_consent_grant_value = (
        request_helpers.evaluation_consent_roundtrip(
            request,
            user_input=original_input,
            bucket=share_key,
            received="",
            grant=evaluation_consent_grant,
            workflow_id="",
            allow_issue=False,
        )
    )
    if consent_blocked is not None:
        return consent_blocked
    attempt_checked_at = time.monotonic()
    attempt = job_runtime._PAID_ATTEMPTS.get(paid_attempt_token)
    current_bucket = spend_store.bucket_id(share_key)
    if (
        attempt is None
        or attempt_checked_at - attempt.created_at > job_runtime.JOB_KEEP_SEC
        or attempt.is_paid != is_paid
        or attempt.user_input != original_input
        or attempt.share_key != share_key
        or attempt.bucket_id != current_bucket
    ):
        if attempt is not None:
            job_runtime._abandon_confirmation_attempt(paid_attempt_token)
        # rate admission보다 앞에서 거절하되, 기존 작업·확인 만료 sweep은 유지한다.
        job_runtime._sweep_jobs(attempt_checked_at)
        return RedirectResponse("/", status_code=303)
    blocked = request_helpers._guard_run(
        request,
        count_start=not is_paid,
        resolved_track=resolved_track,
        now=attempt_checked_at,
    )
    if blocked is not None:
        return blocked

    if is_paid:
        slot_bucket_id = (
            paid_runtime._reserve_run_slot(resolved_track[0], share_key) or ""
        )
        if not slot_bucket_id:
            return request_helpers._throttled(
                request, BUSY_MESSAGE, "busy"
            )

        if not paid_runtime._mark_observation_running(attempt.run_id):
            paid_runtime._release_run_slot(slot_bucket_id)
            job_runtime._abandon_confirmation_attempt(paid_attempt_token)
            return RedirectResponse("/", status_code=303)
        job_runtime._PAID_ATTEMPTS.pop(paid_attempt_token, None)
        card = attempt.card
        run_id = attempt.run_id
        upfront_cost = attempt.lookup_cost_krw
        upfront_models = attempt.models
        upfront_elapsed = attempt.elapsed_sec
        upfront_cost_events = attempt.cost_events
    else:
        slot_bucket_id = (
            paid_runtime._reserve_run_slot(resolved_track[0], share_key) or ""
        )
        if not slot_bucket_id:
            return request_helpers._throttled(
                request, BUSY_MESSAGE, "busy"
            )
        try:
            run_id = public_ids.reserve(job_runtime._JOBS)
        except public_ids.PublicIdUnavailable:
            paid_runtime._release_run_slot(slot_bucket_id)
            return job_runtime._storage_unavailable_response(request)
        consumed_attempt = job_runtime._PAID_ATTEMPTS.pop(
            paid_attempt_token, None
        )
        if consumed_attempt is None:
            public_ids.release(run_id)
            paid_runtime._release_run_slot(slot_bucket_id)
            return RedirectResponse("/", status_code=303)
        card = consumed_attempt.card
        upfront_cost = consumed_attempt.lookup_cost_krw
        upfront_models = consumed_attempt.models
        upfront_elapsed = consumed_attempt.elapsed_sec
        upfront_cost_events = consumed_attempt.cost_events

    return await job_runtime._start_with_reserved_slot(
        request=request,
        original_input=original_input,
        card=card,
        posting_images=[],
        posting_image_consent=False,
        is_paid=is_paid,
        resolved_track=resolved_track,
        run_id=run_id,
        upfront_cost=upfront_cost,
        upfront_models=upfront_models,
        upfront_elapsed=upfront_elapsed,
        slot_bucket_id=slot_bucket_id,
        upfront_cost_events=upfront_cost_events,
    )


@router.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress_page(request: Request, job_id: str):
    """단계별 진행을 보여주는 화면."""
    job = job_runtime._JOBS.get(job_id)
    if job is None:
        # 작업 메모리만 사라지고 보고서가 저장된 경우에는 결과로 복구한다.
        try:
            saved = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
        if saved is not None:
            return RedirectResponse(f"/result/{job_id}", status_code=303)
        try:
            interrupted = _was_interrupted(job_id)
        except Exception:  # noqa: BLE001
            return job_runtime._storage_unavailable_response(request)
        if interrupted:
            return request_helpers.templates.TemplateResponse(
                request=request,
                name="progress_unavailable.html",
                context=request_helpers._ctx(
                    request,
                    interruption_message=_PROGRESS_INTERRUPTED_MESSAGE,
                    retry_url="/",
                    retry_label="처음부터 다시 조사하기",
                ),
                status_code=409,
            )
        return request_helpers.templates.TemplateResponse(
            request=request,
            name="progress_unavailable.html",
            context=request_helpers._ctx(
                request,
                interruption_message=_PROGRESS_UNAVAILABLE_MESSAGE,
                retry_url="/",
            ),
            status_code=410,
        )
    return request_helpers.templates.TemplateResponse(
        request=request,
        name="progress.html",
        context=request_helpers._ctx(
            request, job=job, steps=_COMPANY_ANALYSIS_PROGRESS_STEPS
        ),
    )


@router.get("/api/progress/{job_id}")
async def progress_api(job_id: str):
    """진행 화면이 물어보는 곳. 끝났으면 어디로 갈지도 알려준다."""
    job = job_runtime._JOBS.get(job_id)
    if job is None:
        try:
            saved = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._retryable_response(
                JSONResponse(
                    {
                        "error": (
                            "저장된 진행 상태를 잠시 확인할 수 없습니다. "
                            "새 조사를 시작하지 말고 잠시 후 다시 확인해 주세요."
                        ),
                        "code": "progress_store_unavailable",
                        "retry_url": "",
                        "retryable": True,
                    },
                    status_code=503,
                )
            )
        if saved is not None:
            return JSONResponse(
                {
                    "done": [],
                    "current": "",
                    "finished": True,
                    "next_url": f"/result/{job_id}",
                    "recovered": True,
                }
            )
        try:
            interrupted = _was_interrupted(job_id)
        except Exception:  # noqa: BLE001
            return job_runtime._retryable_response(
                JSONResponse(
                    {
                        "error": "중단 상태를 잠시 확인할 수 없습니다.",
                        "code": "progress_store_unavailable",
                        "retry_url": "",
                        "retryable": True,
                    },
                    status_code=503,
                )
            )
        if interrupted:
            return JSONResponse(
                {
                    "error": _PROGRESS_INTERRUPTED_MESSAGE,
                    "code": "job_interrupted",
                    "retry_url": "/",
                },
                status_code=409,
            )
        return JSONResponse(
            {
                "error": _PROGRESS_UNAVAILABLE_MESSAGE,
                "code": "job_unavailable",
                "retry_url": "/",
            },
            status_code=410,
        )
    return JSONResponse(
        {
            "done": [
                key
                for key in job.done_steps
                if key in _COMPANY_ANALYSIS_PROGRESS_KEYS
            ],
            "current": (
                job.current_step
                if job.current_step in _COMPANY_ANALYSIS_PROGRESS_KEYS
                else ""
            ),
            "finished": job.finished,
            "next_url": f"/result/{job_id}" if job.finished else "",
            "persisted": job.report_persisted,
            "persistence_warning": job.persistence_warning,
        }
    )

"""附(참고 숫자) — 1인평균급여액 · 평균근속연수.

★ 상장사만 나온다. 1인평균급여액·평균근속연수는 «사업보고서»의 「임원 및 직원
  등」 항목이고, 감사보고서에는 없다. 비상장 외감은 사업보고서를 내지 않으므로
  附의 소스가 «아예 없다» (정본 §附의 한계).

여기서 만드는 것은 **순수 함수**다 — 전자공시 공시정보 API `empSttus`(직원
현황)가 돌려주는 JSON을 dict로 받아 `ReportTable`로 바꾼다. **실제 API를
부르지 않는다.**

✅ 응답 필드명을 **실제 호출로 확인했다** (2026-08-15, 로보스타·파마리서치) —
   `jan_salary_am`(1인평균급여액) · `avrg_cnwk_sdytrn`(평균근속연수)이 맞다.
   ★ 행이 **성별로 갈려서** 온다 (남/여 각각 1행). 사업부문이 여럿이면 더 늘어난다.
   ★ 평균근속연수의 «모양이 회사마다 다르다» — 「7년 3개월」 또는 「3.7」.

정본: 확정/05_생성/2_규칙/01_출력틀.md §附의 한계
"""

from __future__ import annotations

from typing import Any, Final, Optional

from src.core.constants import CELL_LABELS
from src.features.pipeline.port import ReportSection, ReportTable
from src.features.provenance.constants import (
    EMP_STATUS_CAPTION,
    EMP_STATUS_CITE,
    EMP_STATUS_HEADERS,
    EMP_STATUS_OK,
    EMPTY_REASON_FETCH_FAILED,
    EMPTY_REASON_NO_DATA,
    EMPTY_REASON_STRUCTURAL,
    TENURE_UNIT_HINTS,
)

# ── 응답 필드 후보 키 ────────────────────────────────────
# ★ 확신 없음 — 아래 parse_emp_status_table() docstring 참고.
#   실제 응답 샘플로 검증되면 후보를 정리(확정 1개만 남기기)할 것.

#: 행 목록이 담기는 최상위 키. DART OpenAPI는 대부분 "list"를 쓴다.
_ROWS_KEYS: Final[tuple[str, ...]] = ("list", "empSttus", "data")
#: 사업부문 구분.
_DIVISION_KEYS: Final[tuple[str, ...]] = ("fo_bbm", "사업부문", "business_division")
#: 성별 구분.
_GENDER_KEYS: Final[tuple[str, ...]] = ("sexdstn", "성별", "sex")
#: 1인평균급여액.
_SALARY_KEYS: Final[tuple[str, ...]] = (
    "jan_salary_am",
    "jan_salary_amount",
    "1인평균급여액",
    "salary_am",
)
#: 평균근속연수.
_TENURE_KEYS: Final[tuple[str, ...]] = (
    "avrg_cnwk_sdytrn",
    "avg_cnwk_sdytrn",
    "평균근속연수",
    "cnwk_sdytrn",
)
#: DART가 「구분 없음」을 표시할 때 쓰는 자리표시자.
_EMPTY_DIVISION_TOKENS: Final[tuple[str, ...]] = ("-", "")
#: 구분·성별이 전부 비어 있을 때 대신 쓰는 이름.
_WHOLE_COMPANY_LABEL: Final[str] = "전체"
#: 숫자 칸이 없을 때 대신 넣는 자리표시자 (공백이면 표 칸이 비어 보인다).
_MISSING_VALUE: Final[str] = "-"


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    """후보 키 중 값이 있는 첫 번째를 꺼낸다."""
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _with_year_unit(tenure: str) -> str:
    """평균근속연수에 단위를 붙인다.

    전자공시가 회사마다 다른 모양으로 준다 — 「7년 3개월」처럼 단위가 붙어 오기도 하고
    「3.7」처럼 숫자만 오기도 한다. 숫자만 오면 **무슨 단위인지 알 수 없어** 읽는 사람이 헷갈린다.

    ★ 숫자만 있을 때만 「년」을 붙인다. 이미 단위가 있으면 손대지 않는다 (원문 보존).
    """
    text = tenure.strip()
    if not text:
        return text
    if any(unit in text for unit in TENURE_UNIT_HINTS):
        return text
    try:
        float(text.replace(",", ""))
    except ValueError:
        return text          # 숫자로 못 읽으면 원문 그대로 둔다
    return f"{text}년"


def parse_emp_status_table(response: Optional[dict[str, Any]]) -> Optional[ReportTable]:
    """DART `empSttus`(직원 현황) 응답을 附 표로 바꾼다.

    ★ 순수 함수다. 실제 API를 부르지 않는다 — 응답 dict를 그대로 받는다.

    ✅ 필드명은 실제 응답으로 확인됐다 (2026-08-15) — `jan_salary_am`·`avrg_cnwk_sdytrn`.
      후보 키를 여럿 남겨 두는 이유는 **DART가 예고 없이 필드를 바꿔도 안 죽게** 하기
      위해서다. 지우지 말 것.

    Args:
        response: `empSttus` API가 돌려주는 JSON을 그대로 dict로 만든 것.
            호출하지 않았거나 실패했으면 None.

    Returns:
        표. 쓸 수 있는 행이 하나도 없으면 None
        (모양이 안 맞는데 억지로 만들면 «없는 숫자»가 화면에 생긴다).
    """
    if not response:
        return None

    status = response.get("status")
    if status is not None and status != EMP_STATUS_OK:
        # DART가 명시적으로 실패를 응답했다 — 표로 만들지 않는다.
        return None

    rows_raw: Optional[list[Any]] = None
    for key in _ROWS_KEYS:
        candidate = response.get(key)
        if isinstance(candidate, list):
            rows_raw = candidate
            break
    if not rows_raw:
        return None

    rows: list[list[str]] = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        salary = _first(item, _SALARY_KEYS)
        tenure = _first(item, _TENURE_KEYS)
        if not salary and not tenure:
            # 둘 다 없는 행은 쓸 것이 없다 — 없는 숫자를 만들지 않는다.
            continue
        division = _first(item, _DIVISION_KEYS)
        gender = _first(item, _GENDER_KEYS)
        parts = [p for p in (division, gender) if p and p not in _EMPTY_DIVISION_TOKENS]
        label = " · ".join(parts) or _WHOLE_COMPANY_LABEL
        rows.append(
            [label, salary or _MISSING_VALUE, _with_year_unit(tenure) or _MISSING_VALUE]
        )

    if not rows:
        return None

    table = ReportTable(
        caption=EMP_STATUS_CAPTION,
        headers=list(EMP_STATUS_HEADERS),
        rows=rows,
        cite=EMP_STATUS_CITE,
    )
    return table if table.is_valid else None


def is_unlisted(corp_type: str) -> bool:
    """비상장 외감 회사인가. 附의 소스가 아예 없는 경우다.

    `corp_type`은 `port.Report.corp_type`과 같은 값을 받는다
    (`"상장사"` | `"비상장 외감"`).
    """
    return "비상장" in corp_type


def emp_status_empty_reason(
    *,
    corp_type: str,
    table: Optional[ReportTable],
    fetch_failed: bool = False,
) -> str:
    """附이 비었을 때 「왜 비었는지」를 고른다.

    사유 문구는 프로그램이 붙인다 (S6) — 미리 정해진 문구 셋 중 하나만 쓴다.
    ❌(회사에 없다)와 ⚠️(우리가 못 가져왔다)를 섞으면 오거부가 되므로,
    원인이 겹치지 않게 **우선순위대로** 하나만 고른다: 구조적 부재 → 우리
    쪽 실패 → 회사에 자료 없음.

    Args:
        corp_type: `"상장사"` | `"비상장 외감"`.
        table: `parse_emp_status_table()`의 결과.
        fetch_failed: API 호출 자체가 실패했으면 True (우리 쪽 실패).

    Returns:
        표가 있으면 빈 문자열. 없으면 원인에 맞는 사유 문구.
    """
    if table is not None:
        return ""
    if is_unlisted(corp_type):
        return EMPTY_REASON_STRUCTURAL
    if fetch_failed:
        return EMPTY_REASON_FETCH_FAILED
    return EMPTY_REASON_NO_DATA


def build_extra_numbers_section(
    *,
    response: Optional[dict[str, Any]],
    corp_type: str,
    fetch_failed: bool = False,
) -> ReportSection:
    """附(참고 숫자) 항목 하나를 만든다.

    `real.py`·`demo.py`가 이 함수 하나만 부르면 附 항목이 완성된다
    (연결 지점 예시는 최종 보고 §3 참고). `ReportTable`을 그대로 쓰고, 표를
    못 만들면 정해진 사유를 붙인다 — 새 자료구조를 만들지 않는다.

    Args:
        response: `empSttus` API 응답. 안 불렀거나 실패했으면 None.
        corp_type: `"상장사"` | `"비상장 외감"`.
        fetch_failed: API 호출 자체가 실패했으면 True.

    Returns:
        칸 번호가 `"附"`인 `ReportSection`.
    """
    table = parse_emp_status_table(response)
    reason = emp_status_empty_reason(
        corp_type=corp_type, table=table, fetch_failed=fetch_failed
    )
    return ReportSection(
        cell="附",
        title=CELL_LABELS["附"],
        tables=[table] if table is not None else [],
        empty_reason=reason,
    )

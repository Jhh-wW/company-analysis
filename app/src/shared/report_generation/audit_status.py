"""감사보고서 «미감사 비교연도» 표시 문구 — 표 캡션과 표지 기간이 함께 쓴다.

★ 왜 shared인가 — 캡션은 ``features.audit_financials``(표를 만드는 쪽)가,
  표지 기간은 ``features.pipeline``이, 화면·PDF 띠는 ``features.report_standard``가
  같은 문구를 써야 한다. 한 feature에 두면 다른 feature가 그 feature를 거꾸로
  import하게 된다(feature-atomic §2-2 «feature 간 직접 import 금지»,
  §2-4 «둘 이상이 실제로 쓰면 shared»). 글자 규칙은 여기 한 벌만 둔다.
"""

from __future__ import annotations


def performance_caption_with_audit_status(caption: str, years: tuple[str, ...]) -> str:
    """원문에서 미감사로 확인한 비교연도만 표 캡션에 덧붙인다."""
    if not years:
        return caption
    suffix = f"{'·'.join(years)}년은 감사받지 않은 비교 재무제표"
    return caption if suffix in caption else f"{caption} · {suffix}"


def analysis_period_with_audit_status(period: str, table: object) -> str:
    """표지의 완료 회계연도 뒤에 미감사 비교연도를 표시한다."""
    years = tuple(getattr(table, "unaudited_years", ()) or ())
    if not years:
        return period
    suffix = f"({'·'.join(years)}년은 감사받지 않은 비교 재무제표)"
    return period if suffix in period else f"{period} {suffix}"

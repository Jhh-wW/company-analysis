"""저장 보고서와 진단의 실측 수치를 비교한다. 외부 요청은 하지 않는다."""

import argparse
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools.report_review_text import is_section_notice


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def measure(report_path, diagnostics_path):
    report, diagnostics = read(report_path), read(diagnostics_path)
    timers = {row["단계"]: row["소요ms"] for row in diagnostics if row.get("step") == "단계소요"}
    news = next((row for row in diagnostics if row.get("step") == "5b_뉴스_수집"), {})
    usage = next((row for row in diagnostics if row.get("step") == "8_뉴스_본문활용"), {})
    search = next((row for row in diagnostics if row.get("step") == "5b_뉴스_검색스냅샷"), {})
    sections = [{"장": row["cell"], "안내포함줄": len(row.get("prose_lines", [])),
                 "본문줄": sum(not is_section_notice(line[0]) for line in row.get("prose_lines", [])),
                 "별도안내줄": len(row.get("guidance_lines", [])),
                 "미제공사유": row.get("empty_reason", "")} for row in report["sections"]]
    return {
        "회사": report["company"], "생성일": report["generated_at"],
        "단계합산초": round(sum(timers.values()) / 1000, 3), "단계별ms": timers,
        "본문줄합계": sum(row["본문줄"] for row in sections), "장별": sections,
        "요약": [row["text"] for row in report.get("summary_items", [])],
        "검증사실수": len(report.get("fact_records", [])),
        "공식약칭": [row.get("alias") for row in search.get("공식약칭근거", [])],
        "수집기사수": usage.get("수집기사수"), "본문사용기사수": usage.get("본문사용기사수"),
        "뉴스제외": news.get("제외", {}), "뉴스신원검증": news.get("법인검증상세", {}),
        "최초검수응답": [{key: row.get(key) for key in ("경로", "시도", "요청번호수", "응답행수", "미응답번호수")}
                         for row in diagnostics if row.get("step") == "8_본문검수_응답판독"],
        "품질관측": report.get("quality_observation", {}),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("diagnostics")
    parser.add_argument("output")
    args = parser.parse_args()
    result = measure(args.report, args.diagnostics)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in ("품질관측", "뉴스제외", "요약")}, ensure_ascii=False))

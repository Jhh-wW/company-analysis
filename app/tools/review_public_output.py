"""실제 저장 보고서의 공개 문장과 요약 결속을 검수용으로 정리한다."""

import argparse
import json
import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools.report_review_text import is_section_notice, normalized_public_claim


def plain(text):
    return normalized_public_claim(text)


def inspect(report):
    facts = {row["fact_id"]: row for row in report.get("fact_records", [])}
    sections = []
    for section in report["sections"]:
        owned = [facts[key] for key in section.get("fact_ids", []) if key in facts]
        claims = {plain(row.get("claim", "")) for row in owned}
        lines = []
        for line in section.get("prose_lines", []):
            text = line[0]
            lines.append({"text": text, "bound_to_fact": plain(text) in claims,
                          "citation_ids": re.findall(r"\[(\d+)\]", text),
                          "interpretation_label": bool(re.search(r"—\s+해석\s*$", text)),
                          "notice": is_section_notice(text)})
        sections.append({"section": section["cell"], "title": section["title"],
                         "fact_count": len(owned), "lines": lines,
                         "table_count": len(section.get("tables", []))})
    summaries = []
    for item in report.get("summary_items", []):
        keys = item.get("fact_ids", [])
        summaries.append({"text": item["text"], "section": item.get("section_id"),
                          "verification_status": item.get("verification_status"),
                          "has_existing_facts": bool(keys) and all(key in facts for key in keys),
                          "fact_ids": keys})
    quality = report.get("quality_observation", {})
    return {"company": report["company"], "summaries": summaries,
            "verified_fact_count": sum(row.get("verification_status") == "verified" for row in facts.values()),
            "sections": sections, "strict_observation_release_allowed": quality.get("release_allowed"),
            "strict_observation_shortfalls": quality.get("quality_shortfalls", [])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("output")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8-sig"))
    result = inspect(report)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

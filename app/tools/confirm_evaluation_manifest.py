"""무료 DART 기업목록·기업개황으로 비교시험 회사 신원을 확인한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

import httpx

from src.shared.company_identity import verified_official_company_names_equivalent
from tools.evaluate_companies import EvaluationError, write_json

DART_ORIGIN = "https://opendart.fss.or.kr"
DART_TIMEOUT_SECONDS = 60.0
DISCLOSURE_LOOKBACK_DAYS = 3 * 365


def read_dart_key(env_file: Path | None) -> str:
    value = os.environ.get("DART_API_KEY", "").strip()
    if env_file:
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            name, separator, candidate = line.partition("=")
            if separator and name.strip() == "DART_API_KEY":
                value = candidate.strip().strip("\"'")
    if not value:
        raise EvaluationError("DART 키를 찾지 못했습니다; 값 대신 승인된 환경 파일 경로를 지정하세요")
    return value


def confirm_cases(proposals: dict, corporations: list[dict], lookup, disclosures=None) -> dict:
    confirmed = []
    held = []
    for proposal in proposals["cases"]:
        case = dict(proposal)
        code = case.get("corp_code", "")
        candidates = [row for row in corporations if
                      (row.get("corp_code") == code if code else row.get("corp_name") == case["expected_legal_name"])]
        if len(candidates) != 1:
            case.update(identity_confirmed=False, confirmation_status="기업목록의 단일 법인 확인 필요")
            held.append(case)
            continue
        code = candidates[0]["corp_code"]
        overview = lookup(code)
        legal_name = str(overview.get("corp_name", ""))
        if (overview.get("status") != "000" or overview.get("corp_code") != code
                or not verified_official_company_names_equivalent(
                    legal_name, case["expected_legal_name"], observed_corp_code=code, expected_corp_code=code)):
            case.update(identity_confirmed=False, confirmation_status="기업개황 법인명 대조 보류")
            held.append(case)
            continue
        case.update(
            corp_code=code, expected_legal_name=legal_name,
            address_hint=str(overview.get("adres", "")), identity_confirmed=True,
            identity_evidence={
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "corporation_list_name": candidates[0]["corp_name"],
                "corporation_list_modified_on": candidates[0].get("modify_date", ""),
                "corp_class": overview.get("corp_cls"), "industry_code": overview.get("induty_code"),
                "stock_code": overview.get("stock_code"), "homepage": overview.get("hm_url"),
                "official_address": overview.get("adres"),
                "source": DART_ORIGIN + "/api/company.json",
                "source_guide": DART_ORIGIN + "/guide/detail.do?apiGrpCd=DS001&apiId=2019002",
                "availability_assessed": False,
            },
        )
        if disclosures is not None:
            case["identity_evidence"]["disclosure_availability"] = disclosures(code)
        confirmed.append(case)
    return {"schema_version": proposals["schema_version"], "cases": confirmed, "held_cases": held,
            "confirmation_scope": "DART 법인 신원만 확인; 규모·자료 가용성·품질 평가는 별도"}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="무료 DART 신원 확인; AI·뉴스 유료 호출 없음")
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider-env-file", type=Path)
    args = parser.parse_args(argv)
    try:
        key = read_dart_key(args.provider_env_file)
        proposals = json.loads(args.proposals.read_text(encoding="utf-8-sig"))
        with httpx.Client(base_url=DART_ORIGIN, timeout=DART_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            response = client.get("/api/corpCode.xml", params={"crtfc_key": key})
            if response.status_code != 200 or not response.content.startswith(b"PK"):
                raise EvaluationError("DART 기업목록 응답을 확인하지 못했습니다")
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                root = ET.fromstring(archive.read("CORPCODE.xml"))
            corporations = [{child.tag: child.text or "" for child in row} for row in root.findall("list")]

            def lookup(code):
                result = client.get("/api/company.json", params={"crtfc_key": key, "corp_code": code})
                if result.status_code != 200:
                    raise EvaluationError("DART 기업개황 HTTP 조회를 완료하지 못했습니다")
                return result.json()

            def disclosures(code):
                end = datetime.now(timezone.utc).date()
                begin = end - timedelta(days=DISCLOSURE_LOOKBACK_DAYS)
                output = {"begin_date": begin.isoformat(), "end_date": end.isoformat(),
                          "source": DART_ORIGIN + "/api/list.json"}
                for kind, label in (("A", "periodic_reports"), ("F", "external_audit_related")):
                    response = client.get("/api/list.json", params={
                        "crtfc_key": key, "corp_code": code, "bgn_de": begin.strftime("%Y%m%d"),
                        "end_de": end.strftime("%Y%m%d"), "pblntf_ty": kind, "page_count": 1,
                    })
                    value = response.json() if response.status_code == 200 else {}
                    status = value.get("status")
                    output[label] = {"status": status, "http_status": response.status_code,
                                     "count": int(value["total_count"]) if status == "000" else (0 if status == "013" else None)}
                return output

            result = confirm_cases(proposals, corporations, lookup, disclosures)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
        print(json.dumps({"확정회사수": len(result["cases"]), "보류회사수": len(result["held_cases"]),
                          "저장경로": str(args.output)}, ensure_ascii=False))
        return 0 if not result["held_cases"] else 2
    except Exception as exc:
        # API URL에는 인증키가 있으므로 HTTP 예외/요청/응답 문자열을 출력하지 않는다.
        print(str(exc) if isinstance(exc, EvaluationError) else f"무료 법인 확인 중단: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

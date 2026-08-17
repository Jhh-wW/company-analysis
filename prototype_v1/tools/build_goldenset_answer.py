# -*- coding: utf-8 -*-
"""골든셋 20곳 답안지 생성 — DART 기업개황 실측 (착수 순서: 핸드오프 할 일 3).

후보 정본: 기획서.ver1/검증/05_골든셋_후보.md
원칙: 답안지는 도구 실행 **전에** 사람이 확정한다 — 이 스크립트는 「제안」 상태의 답안지를 만들고,
사용자 승인 후 확정으로 바뀐다. 모든 DART 호출은 계수기(tick)와 로그를 거친다.
"""
from __future__ import annotations

import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))

from core.dart_client import UsageCounter, download_corpcode, get_json  # noqa: E402
from core.env import load_env  # noqa: E402
from core.logging_util import log_step  # noqa: E402
from features.name_match.logic import build_index, match_layer1, normalize_name  # noqa: E402

RUN_ID = "골든셋_답안지_생성"
CORPCODE_DIR = BASE / "data" / "corpcode"
OUT_MD = BASE.parent / "기획서.ver1" / "검증" / "07_골든셋_답안지.md"
CALL_DELAY_SEC = 0.3  # 연속 호출 간 예의 지연
CORP_CLS_KO = {"Y": "유가(상장)", "K": "코스닥(상장)", "N": "코넥스(상장)", "E": "기타(비상장)"}

# (번호, 입력할 이름, 답안 조회용 법인명 목록 — 빈 목록 = 「DART 부재」가 예상 정답, 비고)
GOLDEN: list[tuple[int, str, list[str], str]] = [
    (1, "삼성전자", ["삼성전자"], "통칭=법인명 · 층1 즉시"),
    (2, "네이버", ["네이버", "NAVER"], "⚠️ 실측: DART 등록명은 영문 「NAVER」 — 한글 통칭으로 층1 못 잡음 (검사 목적 재분류 필요)"),
    (3, "현대자동차", ["현대자동차"], "통칭=법인명 · 대형 상장"),
    (4, "11번가", ["십일번가"], "숫자 표기 → 층1 변형"),
    (5, "LG Electronics", ["LG전자"], "영문명 대조 · ⚠️ 실측: DART 표기는 「LG전자」(로마자 혼합) — 후보 문서의 「엘지전자」는 corpCode에 없음"),
    (6, "SK hynix", ["SK하이닉스"], "영문명 대조 · ⚠️ 실측: DART 표기는 「SK하이닉스」 — 「에스케이하이닉스」는 corpCode에 없음"),
    (7, "배달의민족", ["우아한형제들"], "브랜드≠법인 → 층2 AI"),
    (8, "당근", ["당근마켓"], "브랜드≠법인 → 층2 AI"),
    (9, "카카오", ["카카오"], "계열사 혼동(카카오페이와 구분) · ⚠️ 실측: 동명 2곳(상장+비상장) — 주소 좁히기 케이스 추가"),
    (10, "카카오페이", ["카카오페이"], "계열사 혼동 반대 방향"),
    (11, "에스엠 (서울 성동구)", ["에스엠"], "동명 다수 → 주소 좁히기 · 정답=상장 에스엠"),
    (12, "에스엠 (타지역)", ["에스엠"], "동명 + 주소 불일치 경고 · 정답=비상장 중 1곳(승인 때 선택)"),
    (13, "한국전력", ["한국전력공사"], "상장 공기업 → 거부 A (조건 0) · 층1 「공사」 접미 미결(📌14) 실측 재확인"),
    (14, "강원랜드", ["강원랜드"], "공공기관 명단 회사형태 표기 (조건 0)"),
    (15, "디와이오토", ["디와이오토"], "비상장 외감"),
    (16, "상응무역", ["상응무역"], "무명 비상장 → 게이트 중단 C"),
    (17, "푸른안전산업", ["푸른안전산업"], "무명 비상장 · 홈페이지 실측 사례"),
    (18, "진코스텍", ["진코스텍"], "코넥스"),
    (19, "토스랩", [], "대상 아님 — 「못 찾음」 처리 검증 (부재 예상)"),
    (20, "서울교통공사", [], "지방공기업 — DART 0건 경로 (부재 예상)"),
]


def parse_corpcode(xml_path: Path) -> list[tuple[str, str, str]]:
    """CORPCODE.xml → [(고유번호, 법인명, 종목코드)]."""
    root = ET.parse(xml_path).getroot()
    rows = []
    for node in root.iter("list"):
        code = (node.findtext("corp_code") or "").strip()
        name = (node.findtext("corp_name") or "").strip()
        stock = (node.findtext("stock_code") or "").strip()
        if code and name:
            rows.append((code, name, stock))
    return rows


def fetch_profile(corp_code: str, counter: UsageCounter,
                  cache: dict[str, dict]) -> dict:
    """기업개황 1건 (캐시로 중복 호출 방지)."""
    if corp_code in cache:
        return cache[corp_code]
    payload = get_json("company.json", {"corp_code": corp_code}, counter)
    log_step(RUN_ID, "기업개황 조회", {"corp_code": corp_code}, payload)
    cache[corp_code] = payload
    time.sleep(CALL_DELAY_SEC)
    return payload


def main() -> None:
    load_env()
    if not os.environ.get("DART_API_KEY", "").strip():
        raise SystemExit("DART_API_KEY 없음 — prototype_v1/.env 확인")
    counter = UsageCounter()
    시작_사용량 = counter.today_count()

    xml_path = download_corpcode(CORPCODE_DIR, counter)
    corps = parse_corpcode(xml_path)
    log_step(RUN_ID, "corpCode 적재", {"파일": str(xml_path)}, {"법인 수": len(corps)})
    index = build_index([(c, n) for c, n, _ in corps])
    stock_by_code = {c: s for c, n, s in corps}
    name_by_code = {c: n for c, n, _ in corps}

    profile_cache: dict[str, dict] = {}
    답안: list[dict] = []
    for no, user_input, targets, note in GOLDEN:
        입력_원형 = user_input.split(" (")[0]
        stage, preview_hits = match_layer1(입력_원형, index)
        row: dict = {"no": no, "input": user_input, "note": note,
                     "layer1_stage": stage, "layer1_count": len(preview_hits),
                     "candidates": [], "absent_expected": not targets}
        검색어들 = targets or [입력_원형]
        seen: set[str] = set()
        for 검색어 in 검색어들:
            for code in index.get(normalize_name(검색어), []):
                if code in seen:
                    continue
                seen.add(code)
                p = fetch_profile(code, counter, profile_cache)
                row["candidates"].append({
                    "corp_code": code,
                    "corp_name": p.get("corp_name") or name_by_code.get(code, ""),
                    "corp_cls": p.get("corp_cls", ""),
                    "stock_code": p.get("stock_code") or stock_by_code.get(code, ""),
                    "bizr_no": p.get("bizr_no", ""),
                    "adres": p.get("adres", ""),
                    "status": p.get("status", ""),
                })
        답안.append(row)
        print(f"[{no:>2}] {user_input}: 후보 {len(row['candidates'])}곳"
              f" (층1 미리보기: {stage or '못 찾음'} {len(preview_hits)}건)")

    사용량 = counter.today_count()
    log_step(RUN_ID, "완료", {"골든셋": len(GOLDEN)},
             {"DART 호출(오늘 누계)": 사용량, "이번 실행 호출": 사용량 - 시작_사용량})
    write_md(답안, 사용량 - 시작_사용량)
    print(f"\n답안지 초안: {OUT_MD}")
    print(f"DART 호출 이번 실행 {사용량 - 시작_사용량}건 / 오늘 누계 {사용량}건 (한도 20,000)")


def write_md(답안: list[dict], 호출수: int) -> None:
    오늘 = time.strftime("%Y-%m-%d")
    L: list[str] = [
        "# 골든셋 답안지 — DART 기업개황 실측 (상태: **제안 · 사용자 승인 대기**)",
        "",
        f"> 생성 {오늘} | 도구: `prototype_v1/tools/build_goldenset_answer.py` | 후보 정본: [05_골든셋_후보.md](05_골든셋_후보.md)",
        f"> DART 호출 {호출수}건 (계수기 `prototype_v1/logs/dart_usage.json` · 실행 로그 `logs/{RUN_ID}.jsonl`)",
        "> **승인 규칙**: 사람이 확정해야 답안지가 된다. 승인 후 상태를 「확정」으로 바꾸고 착수 4(실데이터 대조)에 쓴다.",
        "",
        "## 답안지 본표",
        "",
        "| # | 입력 | 정답 법인 (DART) | 고유번호 | 법인구분 | 사업자번호 | 주소 |",
        "|:--:|---|---|---|---|---|---|",
    ]
    에스엠_후보: list[dict] = []
    for row in 답안:
        cands = row["candidates"]
        if row["no"] == 11:
            에스엠_후보 = cands
        if row["absent_expected"]:
            결과 = f"**DART {len(cands)}건** — " + ("부재 확인 ✓ (예상대로)" if not cands else "⚠️ 예상과 다름! 아래 §부재 확인")
            L.append(f"| {row['no']} | {row['input']} | {결과} | — | — | — | — |")
            continue
        if row["no"] == 12:
            L.append(f"| 12 | {row['input']} | (11번과 동일 후보군 — §동명 후보에서 비상장 1곳 선택) | — | — | — | — |")
            continue
        if row["no"] == 11:
            상장 = [c for c in cands if c["corp_cls"] in ("Y", "K", "N")]
            c = 상장[0] if 상장 else (cands[0] if cands else None)
            표기 = "(§동명 후보 참조 — 상장 에스엠)" if c else "⚠️ 0건"
            if c:
                L.append(f"| 11 | {row['input']} | {c['corp_name']} {표기} | {c['corp_code']} | {CORP_CLS_KO.get(c['corp_cls'], c['corp_cls'])} | {c['bizr_no']} | {c['adres']} |")
            else:
                L.append(f"| 11 | {row['input']} | ⚠️ 후보 0건 | — | — | — | — |")
            continue
        if not cands:
            L.append(f"| {row['no']} | {row['input']} | ⚠️ **0건 — 후보 문서 재검토 필요** | — | — | — | — |")
            continue
        for i, c in enumerate(cands):
            머리 = f"| {row['no']} | {row['input']} | " if i == 0 else "| | ⮑ 동명 | "
            L.append(f"{머리}{c['corp_name']} | {c['corp_code']} | {CORP_CLS_KO.get(c['corp_cls'], c['corp_cls'])} | {c['bizr_no']} | {c['adres']} |")
    L += ["", f"## 동명 「에스엠」 후보 전부 ({len(에스엠_후보)}곳) — 11번(성동구=정답)·12번(타지역) 선택용", "",
          "| 고유번호 | 법인구분 | 종목코드 | 사업자번호 | 주소 |", "|---|---|---|---|---|"]
    for c in 에스엠_후보:
        L.append(f"| {c['corp_code']} | {CORP_CLS_KO.get(c['corp_cls'], c['corp_cls'])} | {c['stock_code'] or '—'} | {c['bizr_no']} | {c['adres']} |")
    L += ["", "## 부재 예상 확인 (19·20)", ""]
    for row in 답안:
        if row["absent_expected"]:
            if row["candidates"]:
                L.append(f"- **{row['input']}**: ⚠️ corpCode에 {len(row['candidates'])}건 존재 — 「부재 = 정답」 전제가 깨짐. 승인 때 처리 결정 필요:")
                for c in row["candidates"]:
                    L.append(f"  - {c['corp_name']} ({c['corp_code']} · {CORP_CLS_KO.get(c['corp_cls'], c['corp_cls'])} · {c['adres']})")
            else:
                L.append(f"- **{row['input']}**: corpCode 0건 — 부재 확인 ✓")
    L += ["", "## 층1 미리보기 (입력 이름 그대로 넣었을 때 — 참고용, 답안 아님)", "",
          "| # | 입력 | 층1 결과 | 비고(예상 경로) |", "|:--:|---|---|---|"]
    for row in 답안:
        stage = row["layer1_stage"]
        결과 = f"{stage} · {row['layer1_count']}건" if stage else "못 찾음 → 층2 AI 몫"
        L.append(f"| {row['no']} | {row['input']} | {결과} | {row['note']} |")
    L += ["", "## 다음 할 일", "",
          "1. 사용자: 본표·에스엠 12번 선택·부재 확인의 ⚠️ 항목을 승인/수정",
          "2. 승인되면 문서 머리 상태를 「확정」으로 변경 + 변경이력 기록",
          "3. 착수 4 — 층1 이름대조·판정 사다리를 이 답안지 20건과 대조 실행", ""]
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()

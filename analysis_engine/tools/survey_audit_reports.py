# -*- coding: utf-8 -*-
"""감사보고서 표본 20~30곳 조사 (M1 · M3 · 판관비 명세, 한 표본으로 같이).

확인하려는 질문:
  ① M1  영업부문 주석이 있나 — 1번 블록이 숫자로 채워지는 비율 (의무 아님 → 회사마다 갈림)
  ② M3  특수관계자 주석에 거래처가 실명으로 나오나 — 9번 블록의 값어치
  ③ 판관비 명세 주석이 있나 — 존재율 미확정 항목

표본: DART list.json (pblntf_ty=F 외부감사관련 · corp_cls=E 기타법인=비상장)에서
「감사보고서」(연결 제외)를 모아 고정 시드 무작위 30곳. 원본은 document.xml로 내려받아
로컬에 보관하고(공시 원문 — 공개 자료), 판정 근거 문구(스니펫)를 체크포인트에 남긴다.

판정은 키워드 휴리스틱이다 — 근거 스니펫을 전부 저장해 검증자가 대조할 수 있게 한다.
DART는 무료지만 계수기(UsageCounter)를 반드시 거친다. 회사 1곳 끝날 때마다 체크포인트.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.env import load_env  # noqa: E402
from core.logging_util import log_step  # noqa: E402
from core.dart_client import UsageCounter, download_document, get_json  # noqa: E402

OUT_DIR = PROJECT_ROOT / "data" / "audit_sample"
RAW_DIR = OUT_DIR / "raw"
CHECKPOINT_PATH = OUT_DIR / "survey.jsonl"
RESULT_MD = PROJECT_ROOT / "local_output" / "감사보고서표본.md"
RUN_ID = "감사보고서표본조사"

SEED = 20260815               # 재현 가능한 무작위 표본 (시드 고정)
SAMPLE_SIZE = 30
WINDOW = ("20260302", "20260410")  # 12월 결산 감사보고서가 몰리는 구간
MAX_LIST_PAGES = 10
SNIPPET_HALF = 90             # 근거 스니펫 앞뒤 글자 수

# 판정 키워드 — 공시 원문은 띄어쓰기가 제각각이라 글자 사이 공백을 허용해 찾는다
def _loose(word: str) -> re.Pattern[str]:
    return re.compile(r"\s*".join(map(re.escape, word)))


M1_PATTERNS = [_loose(w) for w in ("영업부문", "부문정보", "보고부문", "부문별정보")]
RELATED_RE = _loose("특수관계자")
# 판관비 표제 변형 — 「영업비용」 표제 감사보고서가 실재 (독립 검증 발견·삼인골든스톤)
SGA_HEAD_RES = [_loose(w) for w in ("판매비와관리비", "판매비및관리비", "판매관리비", "영업비용")]
# 회사명 포착 — 표기(㈜·(주)·주식회사) 앞뒤의 이름을 「잡아서」 자기 자신·법조문을 걸러낸다.
# 단순 표기 개수 세기는 ① 모든 감사보고서에 인용되는 「주식회사 등의 외부감사에 관한 법률」
# ② 쪽마다 반복되는 자기 회사명을 거래처로 오인한다 (독립 검증 발견 — 오탐 7건).
CORP_NAME_RES = (
    re.compile(r"(?:㈜|\(주\)|주식회사)\s*([가-힣A-Za-z0-9&·]{2,20})"),
    re.compile(r"([가-힣A-Za-z0-9&·]{2,20})\s*(?:㈜|\(주\))"),
)
_EXAW_LAW_RE = re.compile(r"주식회사\s*등의\s*외부\s*감사에\s*관한\s*법률")
# 이름이 아닌 상투어 — 표기 앞뒤에 자주 붙는 일반명사·조사 조각과,
# 특수관계자 거래처가 아니라 지급보증 문단의 상투 언급인 보증기관을 걸러낸다
_NAME_STOP = frozenset({
    "등의", "외부감사", "기타", "당사", "회사", "이사회", "감사대상", "지배기업",
    "종속기업", "관계기업", "관계회사", "매출", "매입", "로부터", "보고기간",
    "당기", "전기", "신원", "서울보증보험", "기술보증기금", "신용보증기금",
})
_SELF_PREFIX_LEN = 6          # 앞 6글자가 같으면 자기 이름의 오탈자 변형으로 본다 (보수 방향)
# 「특수관계자」 출현이 주석(제목·도입)인지 판별 — 바로 뒤 30자 안에 거래·현황류가 따라와야 창을 연다
_NOTE_CONTEXT_RE = re.compile(r".{0,20}(거래|현황|내역|잔액|채권|채무)")
SGA_ITEMS = ("급여", "퇴직급여", "복리후생", "감가상각", "임차료", "접대비", "기업업무추진비",
             "광고선전", "운반비", "지급수수료", "세금과공과", "소모품비", "차량유지비")
SGA_MIN_ITEMS = 3             # 명세로 인정할 최소 항목 수
RELATED_REGION = 12_000       # 특수관계자 언급 1곳당 살펴보는 범위(글자)
SGA_REGION = 4_000            # 판관비 명세로 보는 범위(글자)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def read_filing_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text))


def snippet(text: str, idx: int) -> str:
    return text[max(0, idx - SNIPPET_HALF): idx + SNIPPET_HALF].strip()


def _norm_name(name: str) -> str:
    return re.sub(r"㈜|\(주\)|주식회사|\s+", "", name)


def _related_names(region: str, self_norm: str) -> list[tuple[str, int]]:
    """구간에서 자기 자신·법조문을 뺀 거래처 실명 후보를 (이름, 위치)로 모은다."""
    region = _EXAW_LAW_RE.sub(" " * 10, region)  # 길이 유지 삭제 — 위치 보존
    found: list[tuple[str, int]] = []
    for rx in CORP_NAME_RES:
        for m in rx.finditer(region):
            cand = _norm_name(m.group(1))
            if len(cand) < 2 or cand.isdigit() or cand in _NAME_STOP:
                continue
            if cand in self_norm or self_norm in cand:  # 자기 자신 (부분 표기 포함, 보수 방향)
                continue
            if (len(cand) >= _SELF_PREFIX_LEN and
                    cand[:_SELF_PREFIX_LEN] == self_norm[:_SELF_PREFIX_LEN]):
                continue  # 원문 오탈자로 갈라진 자기 이름 변형
            found.append((cand, m.start()))
    return found


def judge(text: str, corp_name: str) -> dict[str, Any]:
    """감사보고서 본문 1건에서 M1·M3·판관비 명세를 판정한다 (근거 스니펫 포함).

    M3는 「특수관계자」의 모든 출현 지점을 훑는다 — 첫 출현만 보면 주주현황 표의
    언급을 앵커로 잡아 진짜 주석(더 뒤)을 놓친다 (독립 검증 발견 · 예진에프앤지).
    """
    r: dict[str, Any] = {}
    m1 = next((m for p in M1_PATTERNS if (m := p.search(text))), None)
    r["m1_부문주석"] = bool(m1)
    r["m1_근거"] = snippet(text, m1.start()) if m1 else None

    self_norm = _norm_name(corp_name)
    note_found = False
    names: list[str] = []
    m3_snip: Optional[str] = None
    for rel in RELATED_RE.finditer(text):
        note_found = True
        # 주석 성격의 출현만 탐색 창을 연다 — 「…의 특수관계자로 지분 소유」 같은 일반 문장이
        # 창을 열면 무관 섹션(차입금 표의 은행명 등)까지 훑어 오탐한다 (독립 검증 2라운드 발견)
        if not _NOTE_CONTEXT_RE.match(text[rel.end(): rel.end() + 30]):
            continue
        region = text[rel.start(): rel.start() + RELATED_REGION]
        for cand, pos in _related_names(region, self_norm):
            names.append(cand)
            if m3_snip is None:
                m3_snip = snippet(region, pos)
    r["m3_특수관계자주석"] = note_found
    r["m3_실명거래처"] = bool(names)
    r["m3_거래처예시"] = sorted(set(names))[:5]
    r["m3_근거"] = m3_snip

    sga_found = False
    sga_snip = None
    for head in SGA_HEAD_RES:
        for m in head.finditer(text):
            region = text[m.start(): m.start() + SGA_REGION]
            items = {it for it in SGA_ITEMS if it in region}
            if len(items) >= SGA_MIN_ITEMS:
                sga_found, sga_snip = True, snippet(text, m.start())
                break
        if sga_found:
            break
    r["판관비_명세"] = sga_found
    r["판관비_근거"] = sga_snip
    return r


def collect_candidates(counter: UsageCounter) -> list[dict[str, Any]]:
    """비상장(E) 감사보고서(연결 제외) 접수 목록 — 최신 접수만 남기고 회사별 1건."""
    by_corp: dict[str, dict[str, Any]] = {}
    for page in range(1, MAX_LIST_PAGES + 1):
        payload = get_json("list.json", {
            "bgn_de": WINDOW[0], "end_de": WINDOW[1], "pblntf_ty": "F",
            "corp_cls": "E", "page_no": page, "page_count": 100}, counter)
        if payload.get("status") != "000":
            break
        for row in payload.get("list", []):
            name = row.get("report_nm", "")
            if "감사보고서" not in name or "연결" in name:
                continue
            prev = by_corp.get(row["corp_code"])
            if prev is None or row["rcept_no"] > prev["rcept_no"]:
                by_corp[row["corp_code"]] = row
        if page >= int(payload.get("total_page", 1)):
            break
    return list(by_corp.values())


def load_checkpoint() -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    if CHECKPOINT_PATH.exists():
        for ln in CHECKPOINT_PATH.read_text(encoding="utf-8").splitlines():
            row = json.loads(ln)
            done[row["corp_code"]] = row
    return done


def run_survey(sample_size: int) -> None:
    loaded = load_env()
    if "DART_API_KEY" not in loaded:
        print("중단: .env에 DART_API_KEY가 없다", flush=True)
        sys.exit(1)
    counter = UsageCounter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sample_path = OUT_DIR / "sample.json"
    if sample_path.exists():
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
    else:
        candidates = collect_candidates(counter)
        print(f"후보 {len(candidates)}곳 (비상장 감사보고서 · {WINDOW[0]}~{WINDOW[1]})", flush=True)
        rng = random.Random(SEED)
        sample = rng.sample(candidates, min(sample_size, len(candidates)))
        sample_path.write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")

    done = load_checkpoint()
    todo = [row for row in sample if row["corp_code"] not in done]
    print(f"표본 {len(sample)}곳 · 완료 {len(done)}곳 · 이번에 {len(todo)}곳", flush=True)
    for i, row in enumerate(todo, start=1):
        try:
            path = download_document(row["rcept_no"], RAW_DIR, counter)
            result = judge(read_filing_text(path), row["corp_name"])
            record = {"corp_code": row["corp_code"], "corp_name": row["corp_name"],
                      "rcept_no": row["rcept_no"], "rcept_dt": row["rcept_dt"],
                      "success": True, **result}
        except Exception as exc:  # 회사 1곳 실패가 전체를 멈추지 않게 — 사유는 기록
            record = {"corp_code": row["corp_code"], "corp_name": row["corp_name"],
                      "rcept_no": row["rcept_no"], "rcept_dt": row["rcept_dt"],
                      "success": False, "error": str(exc)[:200]}
        with CHECKPOINT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        flag = "OK" if record["success"] else "실패"
        print(f"[{i}/{len(todo)}] {row['corp_name']} → {flag}", flush=True)
    log_step(RUN_ID, "표본 조사 실행", {"표본": len(sample)},
             {"완료": len(load_checkpoint()), "DART_오늘_사용량": counter.today_count()})


def aggregate() -> None:
    rows = [r for r in load_checkpoint().values()]
    ok = [r for r in rows if r["success"]]
    fail = [r for r in rows if not r["success"]]
    n = len(ok)
    m1 = sum(1 for r in ok if r["m1_부문주석"])
    m3_note = sum(1 for r in ok if r["m3_특수관계자주석"])
    m3_named = sum(1 for r in ok if r["m3_실명거래처"])
    sga = sum(1 for r in ok if r["판관비_명세"])

    lines = [
        "# 감사보고서 표본 조사 (M1 · M3 · 판관비)",
        "",
        f"> 도구: `analysis_engine/tools/survey_audit_reports.py` · 표본: DART 비상장(기타법인) 감사보고서",
        f"> 접수 {WINDOW[0]}~{WINDOW[1]} · 연결 제외 · **고정 시드({SEED}) 무작위 {len(rows)}곳** (성공 {n} · 실패 {len(fail)})",
        "> 판정: 키워드 휴리스틱 — **근거 스니펫 전건 보존** (`data/audit_sample/survey.jsonl`) · 원문 zip 보관",
        "",
        "## 집계",
        "",
        "| 항목 | 있음 | 존재율 | 걸린 것 |",
        "|---|:---:|:---:|---|",
        f"| **M1** 영업부문 주석 | {m1}/{n} | **{m1 / n:.0%}** | 1번 블록이 숫자로 채워지는 비율 |",
        f"| **M3** 특수관계자 주석 자체 | {m3_note}/{n} | {m3_note / n:.0%} | — |",
        f"| **M3** 그중 거래처 실명 | {m3_named}/{n} | **{m3_named / n:.0%}** | 9번 블록의 값어치 |",
        f"| 판관비 명세 주석 | {sga}/{n} | **{sga / n:.0%}** | 항목별 글자 수 재료 |",
        "",
        "## 회사별 결과",
        "",
        "| 회사 | 접수일 | M1 부문 | M3 실명 | 판관비 명세 |",
        "|---|:---:|:---:|:---:|:---:|",
    ]
    for r in sorted(ok, key=lambda x: x["corp_name"]):
        lines.append(f"| {r['corp_name']} | {r['rcept_dt']} | {'⭕' if r['m1_부문주석'] else '—'} "
                     f"| {'⭕' if r['m3_실명거래처'] else '—'} | {'⭕' if r['판관비_명세'] else '—'} |")
    for r in fail:
        lines.append(f"| {r['corp_name']} | {r['rcept_dt']} | ⚠️ 실패 | | {r['error'][:60]} |")
    lines += [
        "",
        "## 해석 한계 (정직 고지)",
        "",
        "1. **키워드 휴리스틱 판정**이다 — 주석 제목·본문에 해당 표현이 있는지를 본 것이지, 표의 숫자가",
        "   실제로 블록을 채우는지는 첫 20건에서 재확인. 근거 스니펫을 전건 보존해 대조 가능.",
        "2. 표본은 사실상 **2026-04-10 하루 접수분**이다 — 목록 조회가 최신 접수부터 페이지 상한(1,000행)까지",
        "   채워져 창(3/2~4/10)의 마지막 날에 몰렸다. 12월 결산 막바지 제출 집단이라는 편향 가능.",
        "3. M3 「실명」= 특수관계자 언급 전 지점을 훑어 회사표기 앞뒤 **이름을 포착**하고 자기 자신·",
        "   법조문(외감법 인용)을 걸러낸 결과다 (1차 판정의 표기 개수 세기는 독립 검증이 오탐 7건을 찾아",
        "   교체). 잔여 한계: 개인 이름·해외 법인 표기는 못 세고, 자기 이름을 부분 포함한 계열사는 보수적으로 제외.",
        "4. **M1 0% 교차 검산**: 「부문」 글자 자체가 30건 중 2건에만 출현하고 둘 다 무관 문맥(회사분할·",
        "   부문감사인) — 추출·판정 고장이 아니라 실제로 부문 주석이 없는 것 (일반기업회계기준에 부문 챕터",
        "   부재라는 M1 가설과 정합).",
        "",
        "관련 블록: 1번(M1 부문 주석) · 9번(M3 특수관계자)",
    ]
    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:16]), flush=True)
    print(f"... 결과 문서: {RESULT_MD}", flush=True)
    log_step(RUN_ID, "집계 완료", {"성공": n, "실패": len(fail)},
             {"M1": m1, "M3_실명": m3_named, "판관비": sga})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=SAMPLE_SIZE)
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    if not args.aggregate_only:
        run_survey(args.sample)
    aggregate()


if __name__ == "__main__":
    main()

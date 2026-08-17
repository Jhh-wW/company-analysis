"""진짜 알맹이 — 1판 시제품 엔진(`prototype_v1/tools/run_pilot.py`)을 화면에 연결한다.

════════════════════════════════════════════════════════════════════
★★ 이 파일을 돌리면 «돈이 나간다». 고치기 전에 반드시 읽을 것 ★★

1. **돈이 나간다.** 1건당 AI 최대 15회 (이름찾기 5 + 공고판별 1 + 요구역량 1 +
   생성·검사 2×3회 + 작가 1 + 근거 대조 1). 기획서 상한 15회와 같다.
   **최근 실측(2026-08-16)**: 작가 전 188원 → 작가·근거 대조 포함 212원.
2. **실제로 돌려봤다** (2026-08-15~16, 표본 5건·실비 약 292원).
   ~~한 번도 안 돌려봤다~~ — 그때 나온 것이 문제로그 P-43·P-66·P-67·P-74·P-76이다.
3. **1판 엔진과 2판 기획서가 어긋나는 곳이 있다** → 아래 「어긋난 곳」 표.
   고치지 않고 적어만 두었다. 사용자가 정할 일이다.
4. 켜는 법 — 코드를 고치지 않는다. **환경변수 `PIPELINE=real`** 하나면 된다
   (`web/main.py`의 `make_pipeline()`). 코드로 켜면 «켜 둔 채 잊어버려» 돈이 샌다.
   그 전에 `prototype_v1/.env`에 열쇠(DART·네이버·Anthropic)가 있어야 하고,
   `prototype_v1`의 의존 프로그램(anthropic·presidio 등)이 깔려 있어야 한다.
════════════════════════════════════════════════════════════════════

# 1판 엔진과 2판 기획서가 어긋난 곳 (고치지 않고 기록만)

| # | 1판이 하는 일 | 2판 기획서 | 문제로그 |
|---|---|---|---|
| 1 | 6·7·8번을 만들지 않고 **요구역량을 임의 분배** | 6·7·8은 각각 다른 항목 | **P-31** |
| 2 | 표 덩어리를 그대로 채움 | **비운다** (D12) | **P-29** |
| 3 | 보고서에 **출처 목록 절이 없다** | 맨 아래 출처 목록 필수 | **P-24** |
| 4 | 확인 카드를 **자동 [맞습니다]** 처리 | 사람이 눌러야 함 | — (이 파일이 갈라 놓음) |
| 5 | 附(참고 숫자)·어려운 말 풀이 **미구현** | 기획서에 있음 | — |
| 6 | ~~홈페이지 소스 미연결~~ → **홈페이지는 붙였다** (`features/homepage/`). IR·기술블로그는 아직 | 소스 목록에 있음 | **P-35 해소** |
| 7 | 캐시·할당량 차감 **없음** | 4단계·1단계에 있음 | — (7-a 참고) |
| 8 | **문장 고르는 지시문이 4축에서 뉴스를 배제** | 4-1은 「언론이 지적한 과제」 포함 | **P-43** → `spanselect/`로 교체 |
| 9 | **비용 단가가 모델을 안 가린다**(haiku 값 고정) | — | **P-76** ⚠️ 모델 올리면 지표가 3배 어긋난다 |

## 7-a. 캐시 — 지금 어디까지 꽂혔나

| 층 | 상태 |
|---|---|
| **1층** (회사 × 직무 × 공고 지문 → 완성 보고서) | ✅ **읽기·쓰기 다 꽂힘** — 이 파일 |
| **2층** (회사 → 수집 자료 재사용) | ❌ 아직. 저장소 함수(`cache.save_layer2`)는 있다 |
| **별칭**(통칭 → 고유번호) | ❌ 아직 |
| **할당량 차감** | ❌ 아직 (1층 히트는 `charged=False`로 «0 차감»만 지킨다) |

**1층 히트여도 공짜는 아니다.** 공고 지문은 5.5(글자 추출) 뒤에야 계산되므로
5.5의 AI 2회는 그대로 나간다. 대신 «가장 비싼» 8·9 생성 + 10 검증(AI 6회)이
통째로 빠진다. 정본이 이 맞바꿈을 명시적으로 감수했다 —
「남의 옛 공고 블록이 나가는 사고는 되돌릴 수 없고, 글자추출은 소액이다」
(확정/03_수집/2_규칙/03_캐시와저장.md §1층 확정 시점은 5.5 뒤다).

# 이 파일이 하는 일

1판 엔진은 **혼자 도는 배치 스크립트**다. 회사 찾기부터 보고서 쓰기까지 한 번에 한다.
화면은 그 사이에 **사람의 확인**을 끼워야 하므로 둘로 갈라야 한다.

```
find_company()  = 2 식별 → 3 확인 카드          ← 여기서 멈추고 사람에게 보여준다
   ↓ 사람이 [맞습니다]
run()           = 5 판정 → 5.5 공고 → 6 수집 → 7 게이트 → 8·9 생성 → 10 검증 → 13 출력
```

**엔진 코드는 고치지 않는다.** 여기서 부르기만 한다.
1판이 실제로 53건을 돌려 얻은 것(무엇이 걸리는지)을 버리지 않으려는 것이다.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import itertools
import logging
import math
import re
import sys
import threading
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from src.core import paths
from src.core.constants import (
    AI_COST_KRW_PER_USD,
    AUDIT_WINDOW_YEARS,
    CACHE_HIT_LAYER1,
    CACHE_HIT_MESSAGE,
    CACHE_HIT_UNKNOWN_DATE,
    CELL_LABELS,
    EMP_REPORT_CODE,
    EMPTY_REASON_HOMEPAGE,
    GENERATION_MODEL,
    MODEL_PRICES_USD_PER_MTOK,
    MODEL_LABEL_SEPARATOR,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
    EMPTY_REASON_JOINER,
    EMPTY_REASON_KIND_NONE,
    EMPTY_REASON_MATERIAL_UNUSED,
    EMPTY_REASON_NEWS_FAILED,
    EMPTY_REASON_NEWS_NONE,
    EMPTY_REASON_NO_MATERIAL,
    HIDDEN_CELLS,
    HOMEPAGE_GATE_CELLS,
    REVENUE_CITE,
    REVENUE_TABLE_CELL,
    REPORT_CELL_ORDER,
    SITUATION_CELLS,
    SUBSTANCE_FAILED_REASON,
    TABLE_DUMP_REASON,
    VOTE_MIN,
    VOTE_ROUNDS,
)
from src.features.grading.constants import ACCOUNTING_POLICY_REASON
from src.features.blocks678.logic import build_block6, build_block7, build_block8
from src.features.grading.financial import parse_financial_table
from src.features.homepage import link as homepage_link
from src.features.homepage.constants import FRAGMENT_KIND as HOMEPAGE_FRAGMENT_KIND
from src.features.homepage.logic import collect_homepage_fragments
from src.features.provenance.citations import build_citations
from src.features.readable import logic as readable_logic
from src.features.provenance.extra_numbers import build_extra_numbers_section
from src.features.provenance.freshness import append_direction_warning
from src.features.spanselect.constants import NEWS_FRAGMENT_KIND, USAGE_MODEL_KEY
from src.features.spanselect.logic import select_spans
from src.features.filingclean import extra as filing_extra
from src.features.filingclean import logic as filing_clean
from src.features.newspick import constants as newspick
from src.features.newspick import logic as newspick_logic
from src.features.revenuemix import logic as revenuemix
from src.features.writer import constants as writer
from src.features.writer import logic as writer_logic
from src.features.writer import verify as writer_verify
from src.features.grading.logic import grade_of, is_accounting_policy, is_table_dump
from src.features.pipeline.constants import DART_SUCCESS_STATUS
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    Report,
    ReportSection,
    ReportTable,
    RunResult,
    SourceStatus,
    StepReporter,
    UserInput,
)
from src.features.storage import cache as cache_store
from src.features.storage import db as storage_db

logger = logging.getLogger(__name__)


#: 1판은 모듈 전역 `_spent_usd`에 비용을 더한다. 보통 `import run_pilot`은
#: `sys.modules`의 같은 객체를 돌려주므로 서버가 살아 있는 내내 모든 요청이 그 값을
#: 공유한다(P-144). 요청마다 다른 이름으로 원본 파일을 실행해 module namespace 자체를
#: 갈라 놓는다. 잠금은 짧은 이름 발급에만 쓰며 조사 본체를 직렬화하지 않는다.
_ENGINE_INSTANCE_IDS = itertools.count(1)
_ENGINE_INSTANCE_ID_LOCK = threading.Lock()


def _load_isolated_engine_module(engine_path: Path) -> Any:
    """1판 엔진 파일을 독립 module namespace로 한 번 실행한다.

    같은 파일을 읽더라도 반환 module마다 `_spent_usd`가 따로 생긴다. 원본 파일과
    `_ask()`는 한 줄도 바꾸지 않으므로 1판의 호출·거부·파싱 계약은 그대로다.

    ★ 실행하는 동안만 `sys.modules`에 넣고 반드시 뺀다. 일부 decorator는 실행 중
      자기 module을 되찾아야 하지만, 요청마다 고유 이름을 영구 등록하면 끝난 조사
      수만큼 엔진 module 객체가 쌓인다.
    """
    with _ENGINE_INSTANCE_ID_LOCK:
        instance_id = next(_ENGINE_INSTANCE_IDS)
    module_name = f"_app_run_pilot_request_{instance_id}"
    spec = importlib.util.spec_from_file_location(module_name, engine_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"1판 엔진을 불러올 수 없습니다: {engine_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # source 실행이 실패해도 반쯤 만들어진 module을 다음 요청이 집어 들면 안 된다.
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
    return module


class _MeteredEngine:
    """1판 엔진을 고치지 않고 이 요청의 API 응답 사용량만 모으는 얇은 껍데기.

    ★ 이 껍데기가 받는 1판 module은 요청마다 독립 namespace다. 따라서 1판
      `_ask()`의 module 전역 `_spent_usd`와 `$8` 가드도 이 요청 안에서만 돈다.
      그래도 `_ask`만 감싸서는 usage를 전부 셀 수 없다 — `identify()` 같은 1판 함수는
      자기 module 전역 `_ask`를 직접 부른다. 그래서 더 아래 경계인 이 요청 client의
      `messages.create` 응답을 모은다. `MODEL`도 요청 로컬로 두고 provider 직전 경계에서
      덮어쓴다.
    """

    def __init__(self, engine: Any):
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_usages", [])
        # ★ 1판 모듈의 MODEL을 직접 바꾸면 겹쳐 도는 다른 요청의 모델도 바뀐다.
        # 요청마다 자기 값을 들고 client 경계에서 덮어써야 Sonnet/Haiku가 섞이지 않는다.
        object.__setattr__(self, "_model", str(getattr(engine, "MODEL", "")))
        object.__setattr__(self, "_billing_uncertain", False)

    def __getattr__(self, name: str) -> Any:
        if name == "MODEL":
            return self._model
        return getattr(self._engine, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_engine", "_usages", "_model", "_billing_uncertain"}:
            object.__setattr__(self, name, value)
        elif name == "MODEL":
            object.__setattr__(self, "_model", str(value))
        else:
            setattr(self._engine, name, value)

    def meter_client(self, client: Any) -> Any:
        """이 요청의 응답만 세는 client 껍데기를 만든다."""
        return _MeteredClient(client, self._usages, self)

    @property
    def usages(self) -> list[dict[str, Any]]:
        return list(self._usages)

    @property
    def billing_uncertain(self) -> bool:
        return bool(self._billing_uncertain)


class _MeteredMessages:
    """Anthropic `messages.create`의 성공 응답 사용량을 요청별로 모은다."""

    def __init__(
        self,
        messages: Any,
        usages: list[dict[str, Any]],
        metered: _MeteredEngine,
    ):
        self._messages = messages
        self._usages = usages
        self._metered = metered

    def create(self, *args: Any, **kwargs: Any) -> Any:
        call_kwargs = dict(kwargs)
        # 1판 `_ask`는 모듈 전역 MODEL을 읽지만 그 값은 다른 요청과 공유된다.
        # provider에 나가는 마지막 경계에서 이 요청의 로컬 모델로 바로잡는다.
        if self._metered.MODEL:
            call_kwargs["model"] = self._metered.MODEL
        try:
            response = self._messages.create(*args, **call_kwargs)
        except Exception:
            # timeout/API 예외는 서버가 요청을 처리했는지 알 수 없다. 응답이 없다고
            # 0원으로 마감하면 재시작 뒤 예산이 다시 열리므로 표식을 남길 신호를 보낸다.
            self._metered._billing_uncertain = True
            raise
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None) if usage is not None else None
        tokens_out = getattr(usage, "output_tokens", None) if usage is not None else None
        if tokens_in is None or tokens_out is None:
            # 응답은 왔지만 usage가 없으면 실제 비용을 확정할 수 없다. 0원으로
            # 적지 않고 같은 통장의 진행 중 표식을 남긴다.
            self._metered._billing_uncertain = True
            return response
        try:
            clean_in, clean_out = int(tokens_in), int(tokens_out)
        except (TypeError, ValueError, OverflowError):
            self._metered._billing_uncertain = True
            return response
        if clean_in < 0 or clean_out < 0:
            self._metered._billing_uncertain = True
            return response
        self._usages.append(
            {
                "in": clean_in,
                "out": clean_out,
                USAGE_MODEL_KEY: str(
                    getattr(response, "model", "") or call_kwargs.get("model", "")
                ),
            }
        )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


class _MeteredClient:
    """원본 client의 나머지 기능은 그대로 두고 messages만 감싼다."""

    def __init__(
        self,
        client: Any,
        usages: list[dict[str, Any]],
        metered: _MeteredEngine,
    ):
        self._client = client
        # messages 경계가 없으면 유료 호출을 0원으로 통과시킬 수 있으므로 여기서
        # 즉시 실패시킨다. 시험 가짜도 실제 계약을 갖춰야 이 구멍을 숨기지 않는다.
        self.messages = _MeteredMessages(client.messages, usages, metered)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

# ── 1판 엔진 종료 코드 → 화면 종료 종류 ─────────────────
# 왼쪽은 `run_pilot.py`의 `fin(...)` 인자다. 데모와 같은 표를 쓰지 않는 이유는
# 데모는 «저장된 기록»을, 여기는 «지금 도는 엔진»을 읽기 때문이다.
_OUTCOME_MAP: dict[str, Outcome] = {
    "완료": Outcome.REPORT,
    "중단_식별실패": Outcome.NOT_FOUND,
    "중단_기업개황실패": Outcome.NOT_FOUND,
    "중단_게이트미달": Outcome.GATE_STOPPED,
    "중단_요구역량없음": Outcome.POSTING_DISCARDED,
    "중단_생성실패": Outcome.FAILED,
    "거부_거부A": Outcome.REJECT_PUBLIC,
    "거부_거부B": Outcome.REJECT_NO_DISCLOSURE,
    "폐기": Outcome.POSTING_DISCARDED,
}


def _engine() -> Any:
    """1판 엔진을 요청마다 독립 module namespace로 불러온다.

    ★ 파일 맨 위에서 부르지 않는다. 엔진은 `anthropic`·`presidio` 같은
      무거운 프로그램을 요구하는데, 그게 안 깔려 있어도 **데모 화면은 떠야 한다.**
    ★ 평범한 `import run_pilot`을 쓰지 않는다. 그 방식은 서버 수명 동안 같은 module을
      돌려줘 1판의 `_spent_usd`가 요청 사이에 누적된다(P-144).
    """
    root = paths.PROJECT_ROOT / "prototype_v1"
    for extra in (root / "src", root / "tools"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    return _load_isolated_engine_module(root / "tools" / "run_pilot.py")


@lru_cache(maxsize=1)
def _company_index() -> dict[str, list[str]]:
    """전자공시 전체 법인 이름 색인.

    ★ 한 번만 만든다. 10만 곳 넘는 XML을 파싱하므로 요청마다 하면 화면이 멈춘다.
    """
    engine = _engine()
    from build_goldenset_answer import parse_corpcode  # type: ignore  # noqa: PLC0415

    xml = engine.download_corpcode(engine.CORPCODE_DIR, engine.UsageCounter())
    corps = parse_corpcode(xml)
    return engine.build_index([(code, name) for code, name, _ in corps])


# ══════════════════════════════════════════════════════════
# 보고서 만들기 — 엔진의 결과를 화면이 아는 모양으로 옮긴다
# ══════════════════════════════════════════════════════════

def _sections_from(
    kept: list[Any],
    frags: dict[int, dict[str, str]],
    engine: Any,
    engine_cells: Optional[dict[str, bool]] = None,
) -> tuple[list[ReportSection], list[str]]:
    """엔진이 고른 문장들을 항목별로 담는다.

    ★ 표 덩어리는 여기서 버린다 (D12 · 문제로그 P-29).
      엔진을 고치지 않고 «화면에 내보내기 직전»에 거른다.
    ★ 회계기준 설명 문구도 여기서 버린다 (문제로그 P-40).
    ★ 알맹이 검사(①-b) 결과도 여기서 반영한다 (문제로그 P-66).
      전에는 3회 다수결까지 내고 **결과를 통째로 버렸다.**

    Args:
        engine_cells: 칸 → 알맹이 검사 통과 여부. None이면 검사 결과 없이 담는다.
    """
    by_cell: dict[str, list[tuple[str, str]]] = {}
    tables: dict[str, list[ReportTable]] = {}
    requirements: list[str] = []
    dumped: set[str] = set()
    policy_dropped: set[str] = set()

    for item in kept:
        cite = (
            f"조각 {item.fragment_id}·{frags[item.fragment_id]['종류']}"
            if item.fragment_id in frags
            else "공고"
        )
        if cite == "공고":
            requirements.append(item.sentence)
            continue
        # ★ 재무·회계 수치는 «표 그대로» 낸다 (결정기록 D13). 버리지 않는다.
        table = parse_financial_table(item.sentence)
        if table is not None:
            tables.setdefault(item.block, []).append(table)
            continue
        if is_table_dump(item.sentence):
            dumped.add(item.block)
            continue
        # 회계기준 설명 문구는 회사 이름을 바꿔도 말이 된다 (문제로그 P-40).
        if is_accounting_policy(item.sentence):
            policy_dropped.add(item.block)
            continue
        by_cell.setdefault(item.block, []).append((item.sentence, cite))

    empty_reasons = getattr(engine, "EMPTY_REASONS", {})
    cell_substance = engine_cells or {}
    sections: list[ReportSection] = []
    for cell in REPORT_CELL_ORDER:
        if cell == "5":
            # 5번은 공고 원문이라 문장이 아니라 요구역량 목록으로 화면이 그린다.
            sections.append(ReportSection(cell="5", title=CELL_LABELS["5"]))
            continue
        lines = by_cell.get(cell, [])
        cell_tables = tables.get(cell, [])
        # ★★ 알맹이 검사(①-b) 반영 — 문장이 «실제로 남아 있는» 칸에만 건다.
        #   ⚠️ ①-b의 False는 「내용이 나쁘다」가 아니라 «그 칸이 비어 있었다»인
        #     경우가 많다 — 엔진 프롬프트가 「문장이 없는 칸은 false」로 지시한다.
        #     빈 칸에까지 걸면 나중에 새로 채워진 문장이 통째로 다시 숨겨진다.
        if lines and cell_substance.get(cell, True) is False:
            lines = []
        reason = ""
        if not lines and not cell_tables:
            if by_cell.get(cell) and cell_substance.get(cell, True) is False:
                reason = SUBSTANCE_FAILED_REASON
            elif cell in dumped:
                reason = TABLE_DUMP_REASON
            elif cell in policy_dropped:
                reason = ACCOUNTING_POLICY_REASON
            else:
                reason = empty_reasons.get(cell, "수집 자료에 해당 재료 없음")
        sections.append(
            ReportSection(
                cell=cell,
                title=CELL_LABELS.get(cell, cell),
                lines=lines,
                empty_reason=reason,
                tables=cell_tables,
            )
        )
    return sections, requirements


def _fill_blocks678(
    sections: list[ReportSection],
    job: str,
    requirements: list[str],
) -> list[ReportSection]:
    """6·7·8번을 «처음부터» 만들어 덮어쓴다 (문제로그 P-31 · 결정 D14-3).

    ★ 1판 엔진은 이 세 칸을 만들지 않고 요구역량을 5·6·7·8에 임의로 흩뿌렸다.
      그 결과는 의미가 없으므로 **버리고 새로 만든다.**
    ★ AI를 부르지 않는다. 전부 코드다.
    """
    by_cell = {s.cell: s for s in sections}
    cell1_lines = by_cell["1"].lines if "1" in by_cell else []
    cell4_lines = {c: (by_cell[c].lines if c in by_cell else []) for c in SITUATION_CELLS}

    made = {
        "6": build_block6(cell1_lines, job),
        "7": build_block7(requirements),
        "8": build_block8(cell4_lines, requirements),
    }
    return [made.get(s.cell, s) for s in sections]


#: 1판 대응표에 칸이 없을 때 `_sections_from`이 붙이는 사유. 이것도 갈아 끼울 대상이다.
_ENGINE_FALLBACK_REASON = "수집 자료에 해당 재료 없음"

#: 단가가 「100만 토큰당」이라 나눠 줄 값.
_USD_PER_MTOK = 1_000_000

#: 「⚠️ 못 가져옴 = 우리 쪽 실패」를 가리키는 `SourceStatus.state` 값 (port.py 정의).
_SOURCE_STATE_FAILED = "failed"


def _has_failed_source(sources: list[SourceStatus]) -> bool:
    """소스 중 하나라도 «우리 쪽 실패»(⚠️)가 있는가.

    ★ 정본 03_수집/1_흐름/02_실패처리.md — 「⚠️ 못 가져옴 → ❌ 저장 안 함」.
      「홈페이지가 그날만 죽었을 수 있다. 캐시하면 다음 사람도, 그다음 사람도
      영영 X를 본다. 그 회사가 「자료 없는 회사」로 굳어버린다.」
      ❌ 없음(회사의 사실)은 저장해도 된다 — 실패와 섞지 않는다.
    """
    return any(source.state == _SOURCE_STATE_FAILED for source in sources)


def _homepage_reason(homepage_state: str, homepage_detail: str) -> str:
    """홈페이지가 «실제로» 어떻게 됐는지 한 줄."""
    text = EMPTY_REASON_HOMEPAGE.get(homepage_state, "")
    if text and homepage_state == "failed" and homepage_detail:
        return f"{text} — {homepage_detail}"
    return text


def _source_details(
    kinds_for_cell: tuple[str, ...],
    collected_kinds: set[str],
    news_step: dict[str, Any],
    homepage_reason: str,
    uses_homepage: bool,
) -> list[str]:
    """이 칸이 쓰는 소스마다 «지금 실제 상태»를 한 줄씩 만든다."""
    details: list[str] = []
    for kind in kinds_for_cell:
        if kind in collected_kinds:
            continue
        if kind == NEWS_FRAGMENT_KIND:
            details.append(
                EMPTY_REASON_NEWS_FAILED
                if news_step.get("오류")
                else EMPTY_REASON_NEWS_NONE.format(found=news_step.get("검색결과", 0))
            )
        else:
            details.append(EMPTY_REASON_KIND_NONE.format(kind=kind))
    if uses_homepage and HOMEPAGE_FRAGMENT_KIND not in collected_kinds and homepage_reason:
        details.append(homepage_reason)
    return details


def _refresh_empty_reasons(
    sections: list[ReportSection],
    homepage_state: str,
    homepage_detail: str,
    *,
    engine: Any,
    collected_kinds: set[str],
    news_step: dict[str, Any],
) -> list[ReportSection]:
    """빈칸 사유를 «실제 수집 결과»로 다시 쓴다 (문제로그 P-67).

    ★ 1판 엔진은 칸마다 고정 문구를 **조건 없이** 붙인다. 그래서 뉴스를 6건
      모아 놓고도 「채택된 기사 없음」이라고 말했다. 있는 것을 없다고 하면
      사용자는 멀쩡한 회사를 포기한다 — 절대 규칙 5를 반대 방향으로 어긴 것이다.

    ★ 「재료가 없다」와 「재료는 있는데 안 뽑혔다」를 **반드시 갈라 말한다.**
      사용자가 할 일이 다르다 — 앞은 회사를 바꿔야 하고, 뒤는 다시 돌리면 된다.

    ⚠️ 앱이 «직접» 붙인 사유(표 덩어리·회계 문구·알맹이 미달)는 이미 사실이므로
      건드리지 않는다. 1판이 붙인 고정 문구만 갈아 끼운다.
    """
    engine_reasons = getattr(engine, "EMPTY_REASONS", {})
    replaceable = set(engine_reasons.values()) | {_ENGINE_FALLBACK_REASON}
    homepage_reason = _homepage_reason(homepage_state, homepage_detail)

    fixed: list[ReportSection] = []
    for section in sections:
        if section.lines or section.tables or section.empty_reason not in replaceable:
            fixed.append(section)
            continue

        kinds_for_cell = tuple(getattr(engine, "CELL_SOURCES", {}).get(section.cell, ()))
        uses_homepage = section.cell in HOMEPAGE_GATE_CELLS
        gathered = [k for k in kinds_for_cell if k in collected_kinds]
        if uses_homepage and HOMEPAGE_FRAGMENT_KIND in collected_kinds:
            gathered.append(HOMEPAGE_FRAGMENT_KIND)

        if gathered:
            reason = EMPTY_REASON_MATERIAL_UNUSED.format(
                materials=EMPTY_REASON_JOINER.join(gathered)
            )
        else:
            details = _source_details(
                kinds_for_cell, collected_kinds, news_step, homepage_reason, uses_homepage
            )
            reason = (
                EMPTY_REASON_NO_MATERIAL.format(
                    details=EMPTY_REASON_JOINER.join(details)
                )
                if details
                else section.empty_reason
            )

        fixed.append(
            ReportSection(
                cell=section.cell,
                title=section.title,
                lines=section.lines,
                empty_reason=reason,
                tables=section.tables,
            )
        )
    return fixed


def _step_usage_spent_krw(
    steps: list[dict[str, Any]], *, model: str, krw_per_usd: float
) -> float:
    """이 단계 목록에 직접 달린 AI 사용량만 원화로 센다.

    ★ 엔진 전역 `_spent_usd`의 전후 차이를 쓰면 동시에 본조사가 돌 때 남의 비용까지
      섞인다. client 응답에서 요청별로 모은 입력·출력 토큰만 다시 계산한다.
    """
    spent_usd = 0.0
    for step in steps:
        usage = step.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens_in, tokens_out = usage.get("in"), usage.get("out")
        if not isinstance(tokens_in, (int, float)) or not isinstance(
            tokens_out, (int, float)
        ):
            continue
        used_model = str(usage.get(USAGE_MODEL_KEY) or model)
        price_in, price_out = MODEL_PRICES_USD_PER_MTOK.get(
            used_model, UNKNOWN_MODEL_PRICE_USD_PER_MTOK
        )
        spent_usd += (tokens_in * price_in + tokens_out * price_out) / _USD_PER_MTOK
    return round(spent_usd * krw_per_usd, 2)


def _metered_client(metered: _MeteredEngine, client: Any) -> Any:
    """계약 검사(AST)가 1판 이름과 앱 껍데기 이름을 섞지 않게 감싼다."""
    return metered.meter_client(client)


def _request_spent_krw(metered: _MeteredEngine) -> float:
    """계량 껍데기가 이 요청에서 직접 받은 모든 AI 응답 비용."""
    steps = [{"usage": usage} for usage in metered.usages]
    exchange = getattr(metered, "KRW_PER_USD", AI_COST_KRW_PER_USD)
    try:
        exchange = float(exchange)
    except (TypeError, ValueError):
        exchange = AI_COST_KRW_PER_USD
    if not math.isfinite(exchange) or exchange <= 0:
        exchange = AI_COST_KRW_PER_USD
    return _step_usage_spent_krw(
        steps,
        model=str(getattr(metered, "MODEL", "")),
        krw_per_usd=exchange,
    )


def _request_model_label(metered: _MeteredEngine) -> str:
    """기본값을 지어내지 않고 실제 응답한 모델만 호출 순서대로 남긴다."""
    models = tuple(
        dict.fromkeys(
            str(usage.get(USAGE_MODEL_KEY, "")).strip()
            for usage in metered.usages
            if str(usage.get(USAGE_MODEL_KEY, "")).strip()
        )
    )
    return MODEL_LABEL_SEPARATOR.join(models)


def _request_billing_uncertain(metered: _MeteredEngine) -> bool:
    """provider 예외를 받은 요청인지 감춘 껍데기에서 읽는다."""
    return metered.billing_uncertain


def _cited_fragment_count(kept: list[Any]) -> int:
    """실제로 «인용된» 조각이 몇 개인가 — 수집 활용률의 분자."""
    return len({i.fragment_id for i in kept if i.fragment_id is not None})


def _sources_from(steps: list[dict[str, Any]]) -> list[SourceStatus]:
    """엔진이 남긴 단계 기록에서 소스별 수집 현황을 뽑는다.

    ⭕ 찾음 / ❌ 없음 / ⚠️ 못 가져옴 — 셋을 섞으면 오거부가 된다.
    """
    def step(name: str) -> Optional[dict[str, Any]]:
        return next((s for s in steps if s.get("step") == name), None)

    sources: list[SourceStatus] = []

    fail = step("6_수집_원문")           # 엔진은 «실패했을 때만» 이 단계를 남긴다
    collect = step("6_수집")
    if fail is not None:
        sources.append(SourceStatus("전자공시", "failed", "공시 원문을 가져오지 못했습니다"))
    elif collect and collect.get("원문"):
        sources.append(
            SourceStatus("전자공시", "ok", f"{collect['원문']} · 조각 {collect.get('조각수', 0)}개")
        )
    else:
        sources.append(SourceStatus("전자공시", "none", "최근 3년 안에 낸 보고서가 없습니다"))

    news = step("6_수집_뉴스")
    if news is None:
        sources.append(SourceStatus("뉴스", "none", "여기까지 오지 못함"))
    elif news.get("오류"):
        sources.append(SourceStatus("뉴스", "failed", "뉴스 검색에 실패했습니다"))
    else:
        taken, found = news.get("채택", 0), news.get("검색결과", 0)
        sources.append(
            SourceStatus("뉴스", "ok", f"검색 {found}건 중 {taken}건 채택")
            if taken
            else SourceStatus("뉴스", "none", f"검색 {found}건 · 채택 조건 통과 0건")
        )

    home = step("6_수집_홈페이지")
    if home is None:
        sources.append(SourceStatus("회사 홈페이지", "none", "여기까지 오지 못함"))
    elif home.get("오류"):
        # ⚠️ 우리 쪽 실패다. ❌(회사에 자료가 없음)와 섞으면 오거부가 된다.
        sources.append(SourceStatus("회사 홈페이지", "failed", str(home["오류"])))
    elif home.get("조각수"):
        sources.append(
            SourceStatus("회사 홈페이지", "ok", f"페이지에서 조각 {home['조각수']}개")
        )
    else:
        sources.append(SourceStatus("회사 홈페이지", "none", str(home.get("없음", "자료 없음"))))
    return sources


class RealPipeline:
    """1판 엔진을 `port.Pipeline` 약속에 맞춰 감싼 것.

    ★ 상태를 들고 있지 않는다. 확인 카드와 실행 사이에 필요한 것은
      `CompanyCard.ref`(전자공시 고유번호)로만 넘긴다.
      서버가 여러 대로 늘어나도 그대로 돈다.
    """

    def find_company(self, user_input: UserInput) -> Optional[CompanyCard]:
        """2 식별 → 3 확인 카드까지만 한다. 여기서 멈추고 사람에게 보여준다."""
        return self.find_company_metered(user_input).card

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        """회사 확인 카드와 식별 AI 비용을 한 번에 돌려준다.

        ★ 비용을 브라우저의 숨은 입력칸으로 보내지 않는다. 웹 서버가 이 결과를
          곧바로 원장에 적고, 확인 카드와 본조사를 같은 요청 번호로 잇는다.
        """
        engine = _MeteredEngine(_engine())
        engine.load_env()

        client = _metered_client(engine, engine._client())
        counter = engine.UsageCounter()
        index = _company_index()
        steps: list[dict[str, Any]] = []
        card: Optional[CompanyCard] = None
        failed = False

        try:
            region = user_input.region.strip() or "모름"
            corp_code = engine.identify(
                client, user_input.company, region, index, counter, steps
            )
            if corp_code is not None:
                profile = engine.get_json(
                    "company.json", {"corp_code": corp_code}, counter
                )
                if profile.get("status") == DART_SUCCESS_STATUS:
                    address = (profile.get("adres") or "").strip()
                    same_name = max(
                        (s.get("후보수", 1) for s in steps if s.get("후보수")),
                        default=1,
                    )
                    warning = ""
                    if region != "모름" and not _region_matches(region, address):
                        warning = (
                            f"입력하신 지역({region})과 본사 주소가 다릅니다. "
                            "지사·공장이거나 최근 이전했을 수 있습니다."
                        )
                    card = CompanyCard(
                        legal_name=profile.get("corp_name", user_input.company),
                        typed_name=user_input.company,
                        address=address,
                        ceo=profile.get("ceo_nm", ""),
                        founded=profile.get("est_dt", ""),
                        homepage=profile.get("hm_url", ""),
                        homepage_url=homepage_link.workable_url(
                            profile.get("hm_url", "")
                        ),
                        region_warning=warning,
                        same_name_count=int(same_name or 1),
                        ref=corp_code,
                    )
                else:
                    # 법인 코드는 찾았는데 DART 회사 응답이 실패한 것이다. 이를
                    # 「그 회사가 없음」으로 기록하면 반대 방향의 거짓말이 된다.
                    failed = True
        except Exception:  # noqa: BLE001 — 앞에서 쓴 비용을 버리지 않고 실패로 돌려준다
            logger.exception("회사 식별 중 실패했습니다")
            failed = True

        return CompanyLookupResult(
            card=card,
            cost_krw=_request_spent_krw(engine),
            model=_request_model_label(engine),
            failed=failed or _request_billing_uncertain(engine),
            billing_uncertain=_request_billing_uncertain(engine),
        )

    def run(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[StepReporter] = None,
    ) -> RunResult:
        """5 판정부터 13 출력까지 돌리고 예외 때도 이미 쓴 비용을 보존한다."""
        engine = _MeteredEngine(_engine())
        try:
            result = self._run_metered(user_input, card, on_step, engine=engine)
        except Exception:  # noqa: BLE001 — AI 뒤 후속 코드가 터져도 쓴 돈은 0원이 아니다
            logger.exception("본조사 중 예기치 않은 실패가 발생했습니다")
            result = RunResult(
                outcome=Outcome.FAILED,
                message=_message(Outcome.FAILED),
            )
        # 어느 조기 종료로 나왔든 비용·모델은 한 요청의 client 응답을 정본으로 삼는다.
        # 단계별 return에 따로 적으면 새 종료가 추가될 때 한 곳은 반드시 빠진다.
        return replace(
            result,
            cost_krw=_request_spent_krw(engine),
            model=_request_model_label(engine),
            billing_uncertain=_request_billing_uncertain(engine),
        )

    def _run_metered(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[StepReporter],
        *,
        engine: _MeteredEngine,
    ) -> RunResult:
        """본조사 본체. `_MeteredEngine`이 이 요청의 AI 사용량만 모은다.

        ★ 식별(2)은 다시 하지 않는다. `card.ref`에 이미 답이 있다.
          다시 하면 AI 5회가 통째로 또 나간다.
        """
        def tell(key: str) -> None:
            if on_step is not None:
                on_step(key)

        engine.load_env()
        client = _metered_client(engine, engine._client())
        counter = engine.UsageCounter()
        steps: list[dict[str, Any]] = []
        model = getattr(engine, "MODEL", "")

        tell("identify")   # 이미 끝났다 — 화면에는 지나간 단계로 표시된다
        corp_code = card.ref
        if not corp_code:
            return RunResult(outcome=Outcome.NOT_FOUND, message=_message(Outcome.NOT_FOUND))
        profile = engine.get_json("company.json", {"corp_code": corp_code}, counter)
        if not isinstance(profile, dict) or profile.get("status") != DART_SUCCESS_STATUS:
            # 법인 코드를 사람이 확인한 뒤에도 DART가 한도·인증 오류를 돌려줄 수 있다.
            # 빈 회사정보로 계속 가면 기술 실패를 실제 기업 성격으로 오판한다.
            raise RuntimeError("DART 회사정보 응답이 정상 상태가 아닙니다")

        # ── 5 판정 (전부 코드 · AI 0회) ──────────────────
        tell("judge")
        registry = engine.load_public_org_registry(engine.PUBLIC_ORG_REGISTRY)
        end = dt.date.today()
        audit = engine.get_json(
            "list.json",
            {
                "corp_code": corp_code,
                "bgn_de": end.replace(year=end.year - AUDIT_WINDOW_YEARS).strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "F",
                "page_count": "100",
            },
            counter,
        )
        if not isinstance(audit, dict) or audit.get("status") != DART_SUCCESS_STATUS:
            # 오류 응답에는 목록이 없지만 그것은 「감사보고서가 없음」의 증거가 아니다.
            # 빈 목록으로 접으면 비상장 회사를 거부B로 거짓 분류하므로 즉시 실패한다.
            raise RuntimeError("DART 공시목록 응답이 정상 상태가 아닙니다")
        has_audit = any("감사보고서" in (r.get("report_nm") or "") for r in audit.get("list", []))
        judgment = engine.decide(
            profile.get("corp_cls", ""),
            has_audit,
            profile.get("bizr_no"),
            lambda b: engine.match_public_org(b, registry),
        )
        if judgment.status != "대상":
            outcome = _OUTCOME_MAP.get(f"거부_{judgment.status}", Outcome.REJECT_NO_DISCLOSURE)
            return RunResult(
                outcome=outcome,
                message=_message(outcome),
                corp_type=judgment.corp_type,
                cost_krw=_request_spent_krw(engine),
                model=model,
            )

        # ── 5.5 공고 판별 3층 (AI 2회) ───────────────────
        tell("posting")
        posting = user_input.posting_text
        if not posting.strip():
            return RunResult(
                outcome=Outcome.POSTING_DISCARDED,
                message="채용공고 내용이 비어 있습니다. 공고를 붙여넣어 주세요.",
                corp_type=judgment.corp_type,
                cost_krw=_request_spent_krw(engine),
                model=model,
            )
        requirements, discarded = _read_posting(engine, client, posting, steps)
        if discarded:
            return RunResult(
                outcome=Outcome.POSTING_DISCARDED,
                message=_message(Outcome.POSTING_DISCARDED),
                corp_type=judgment.corp_type,
                cost_krw=_request_spent_krw(engine),
                model=model,
            )

        # ── 4 캐시 확인 → 1층 확정 (정본 03_수집/2_규칙/03_캐시와저장.md) ──
        # ★ 왜 여기인가 — 1층 키에는 «공고 지문»이 들어가고, 지문은 5.5가
        #   요구역량을 뽑은 «뒤»에야 계산된다. 앞당기면 회사×직무만으로 히트해
        #   「남의 옛 공고로 만든 보고서」가 나간다 (정본 §★ 사고 시나리오).
        # ★ 재무 API·최신 공시를 여기서 미리 부르는 이유 — 신선도(O9)가
        #   「저장 당시 사업연도 == 지금 최신 사업연도」를 보기 때문이다.
        #   둘 다 전자공시 조회일 뿐 **AI는 안 부른다**(0원). 미적중이면
        #   6 수집에 그대로 넘겨 같은 것을 두 번 받지 않는다.
        financials, fin_years = engine.fetch_financials(corp_code, counter)
        filing = engine.latest_report_rcept(corp_code, judgment.corp_type, counter)
        current_fiscal_year = _current_fiscal_year(fin_years, filing)
        cached = _cache_lookup(
            corp_id=corp_code,
            job=user_input.job,
            requirements=requirements,
            current_fiscal_year=current_fiscal_year,
        )
        if cached is not None:
            tell("output")   # 6~10을 통째로 건너뛴다
            return RunResult(
                outcome=Outcome.REPORT,
                report=cached,
                # ★ 「방금 조사한 것」처럼 보이면 안 된다. 언제 만든 것인지 밝힌다.
                message=CACHE_HIT_MESSAGE.format(
                    generated_at=cached.generated_at or CACHE_HIT_UNKNOWN_DATE
                ),
                # 수집 현황은 «그때 실제로 모은 것»을 그대로 보여준다.
                sources=list(cached.sources),
                # 정본 00_공통/2_규칙/04_할당량.md — 캐시 반환은 0 차감·무제한.
                charged=False,
                corp_type=cached.corp_type or judgment.corp_type,
                # 이번 요청에서 실제로 쓴 돈 — 5.5 글자추출분은 남는다(0이 아니다).
                cost_krw=_request_spent_krw(engine),
                model=model,
                # 화면 배지와 대시보드 ⑤가 이 값을 읽는다. 안 실으면 캐시가
                # 돌아도 「재사용 0건」으로 보인다 (P-63과 같은 사고).
                cache_hit=CACHE_HIT_LAYER1,
            )

        # ── 6 수집 (AI 0회) ──────────────────────────────
        tell("collect")
        frags, revenue_tables = _collect(
            engine, client, profile, user_input, counter, steps,
            financials=financials, fin_years=fin_years, filing=filing,
        )
        sources = _sources_from(steps)

        # ── 7 사전 게이트 — 미달이면 생성 «전에» 멈춘다 (0원) ──
        tell("gate")
        kinds = {f["종류"] for f in frags.values()}
        rough = {c: any(k in kinds for k in srcs) for c, srcs in engine.CELL_SOURCES.items()}
        # ★ 홈페이지 조각을 게이트가 «세게» 한다 (문제로그 P-72).
        #   1판의 CELL_SOURCES에는 「홈페이지」가 어느 칸에도 없어서, 홈페이지에서
        #   재료를 실제로 가져와도 게이트가 모르고 「미달」로 멈춰 세웠다.
        #   정본은 2·4-2·4-3의 비상장 기본 소스로 홈페이지를 지정한다.
        if HOMEPAGE_FRAGMENT_KIND in kinds:
            for cell in HOMEPAGE_GATE_CELLS:
                if cell in rough:
                    rough[cell] = True
        passed, _reasons = engine.establishment(rough)
        if not passed:
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=_message(Outcome.GATE_STOPPED),
                sources=sources,
                corp_type=judgment.corp_type,
                fragments_collected=len(frags),
                cost_krw=_request_spent_krw(engine),
                model=model,
            )

        # ── 8·9 생성 + 10 검증 — 3회 반복 후 다수결 (AI 6회) ──
        tell("generate")
        rounds: list[dict[str, bool]] = []
        kept: list[Any] = []
        sentences_made = 0   # 검사 «전» 생성 문장 수 — 원문 일치율의 분모
        for _round in range(VOTE_ROUNDS):
            # ★ 1판의 배치 지시문 대신 «정본에 맞춘» 지시문을 쓴다 (문제로그 P-43).
            #   1판 지시문은 4축에 「회사가 직접 말한 문장만」이라 정본이 4-1의
            #   기본 소스로 지정한 «언론 보도»를 통째로 배제했다. 그래서 뉴스
            #   후보 106문장이 올라가고도 인용이 0회였다.
            #   ⚠️ 1판 코드는 한 줄도 안 고쳤다 — 부품만 빌려 쓰는 감싸기다.
            picked, _deleted = select_spans(
                client, frags, requirements, user_input.job, steps,
                engine=engine, model=GENERATION_MODEL,
            )
            if not picked:
                continue
            ai_ok = engine.substance_check(client, picked, steps)
            pattern_ok = engine.cell_pattern_ok(picked)
            rounds.append(
                {
                    c: (
                        any(i.block == c for i in picked)
                        and ai_ok.get(c, True)
                        and pattern_ok.get(c, True)
                    )
                    for c in engine.CELL_SOURCES
                }
            )
            sentences_made = max(sentences_made, len(picked) + len(_deleted))
            if len(picked) > len(kept):
                kept = picked
        if not rounds:
            return RunResult(
                outcome=Outcome.FAILED,
                message=_message(Outcome.FAILED),
                sources=sources,
                corp_type=judgment.corp_type,
                fragments_collected=len(frags),
                cost_krw=_request_spent_krw(engine),
                model=model,
            )

        tell("verify")
        # 다수결 — 절대 4칸 문턱 근처에서 실행마다 판정이 흔들리던 문제 대응 (P-23)
        engine_cells = {
            c: sum(1 for r in rounds if r.get(c)) >= VOTE_MIN for c in engine.CELL_SOURCES
        }

        # ── 13 출력 ──────────────────────────────────────
        tell("output")
        # ★ engine_cells를 반드시 넘긴다 — 안 넘기면 AI 3회를 써서 낸 알맹이
        #   판정이 통째로 버려진다 (문제로그 P-66).
        sections, extracted = _sections_from(kept, frags, engine, engine_cells)
        final_requirements = extracted or requirements
        home_step = next((s for s in steps if s.get("step") == "6_수집_홈페이지"), {})
        home_state = (
            "failed" if home_step.get("오류")
            else "ok" if home_step.get("조각수")
            else "none" if home_step else ""
        )
        # ★ 빈칸 사유를 «실제로 모은 것»으로 다시 쓴다 (문제로그 P-67).
        #   1판의 고정 문구는 뉴스를 6건 모아 놓고도 「채택된 기사 없음」이라 말했다.
        sections = _refresh_empty_reasons(
            sections,
            home_state,
            str(home_step.get("오류") or home_step.get("없음") or ""),
            engine=engine,
            collected_kinds=kinds,
            news_step=next((s for s in steps if s.get("step") == "6_수집_뉴스"), {}),
        )
        sections = _fill_blocks678(sections, user_input.job, final_requirements)

        # ★ 매출 구성 비중 표를 1번 칸에 붙인다 (P-112).
        #   ⚠️ 등급 계산 «전»이다 — 표만 있어도 그 칸은 «채워진» 것이다(D13).
        #     문장이 없어 비던 1번이 이 표로 채워질 수 있다.
        if revenue_tables:
            매출표 = [ReportTable(**t) for t in revenue_tables]
            sections = [
                replace(s, tables=list(s.tables) + 매출표)
                if s.cell == REVENUE_TABLE_CELL else s
                for s in sections
            ]

        # ★ 보고서에서 아예 빼는 칸 (P-111 · P-115) — 전부 사용자가 화면을 보고 지시한 것.
        #   ⚠️ **등급 계산 «전»에** 뺀다. 아래 `cells`를 이 목록에서 만들기 때문에
        #     여기서 빼면 등급에서도 같이 빠진다 — 거의 항상 비던 칸이라 그게 맞다.
        sections = [s for s in sections if s.cell not in HIDDEN_CELLS]

        # 附(참고 숫자) — 상장사만 나온다. 비상장은 빈칸 + 사유 (D14-4).
        # ★ 附을 빼기로 했으면 **전자공시를 부르지도 않는다** — 안 쓸 자료를 받으려고
        #   DART 일일 한도를 깎을 이유가 없다 (2026-08-16).
        if "附" not in HIDDEN_CELLS:
            emp_failed = False
            emp_response: Optional[dict[str, Any]] = None
            try:
                emp_response = engine.get_json(
                    "empSttus.json",
                    {
                        "corp_code": corp_code,
                        "bsns_year": str(dt.date.today().year - 1),
                        "reprt_code": EMP_REPORT_CODE,
                    },
                    counter,
                )
            except Exception:  # noqa: BLE001 — 附은 «표시 전용»이라 보고서를 죽이지 않는다
                emp_failed = True
            extra = build_extra_numbers_section(
                response=emp_response, corp_type=judgment.corp_type, fetch_failed=emp_failed
            )
            sections = [extra if s.cell == "附" else s for s in sections]

        # 4-3(앞으로 어디로 가나)에 날짜·변경 경고를 붙인다 (W6 · D14-5).
        sections = [append_direction_warning(s) if s.cell == "4-3" else s for s in sections]

        # 맨 아래 출처 목록 (P-24 · D14-1).
        # ★ 날짜·출처를 못 구한 항목은 «내보내지 않는다». 반쪽짜리 출처는
        #   있는 것보다 나쁘다 — 사용자가 확인하러 갔다가 못 찾는다.
        citations = [c for c in build_citations(
            frags, filing=filing, collected_on=dt.date.today()
        ) if c.is_valid]

        # ★ 등급은 «화면에 실제로 나가는 것»으로 센다. 표 덩어리를 버린 칸이
        #   엔진 판정에는 남아 있어 그대로 쓰면 화면과 어긋난다 (D12).
        #   6·7·8은 공고 블록이라 세지 않는다 — engine_cells에 없으므로 자동으로 빠진다.
        #   ★ `in engine_cells`는 **키 검사**다 (「세는 칸인가」). 알맹이 판정의
        #     «값»은 위 `_sections_from`이 이미 반영해 `is_filled`에 녹아 있다 (P-66).
        cells = {s.cell: s.is_filled for s in sections if s.cell in engine_cells}
        grade, shortfall = grade_of(cells)

        # ★ 근거를 «하나의 글»로 잇는다 (P-110) — 사용자 지적: 「지금 이건 글이 아니야」.
        #   ⚠️ 반드시 **판정(`cells`·`grade`)까지 끝난 뒤**에 한다 —
        #     칸이 찼는지는 «원문»으로 세야 하고, 작가가 실패해도 등급이 흔들리면 안 된다.
        #   ★ 원문(`lines`)은 **그대로 남긴다.** 화면에서 「원문 보기」로 접어 같이 낸다.
        sections, written_cells = _write_prose(
            engine, client, user_input, sections, steps, model
        )

        # ★ 공시 문투를 «읽히는 말»로 바꾼다 (P-107) — 「당사는 …습니다」 → 「하이브는 …다」.
        #   ⚠️ 공고에서 온 문장은 안 건드린다 — `readable`이 출처를 보고 스스로 뺀다.
        #   ★ AI를 안 쓴다. 정해 둔 낱말·어미를 바꿀 뿐이라 환각이 생길 자리가 없다.
        #   ★ **작가가 쓴 칸에는 안 돌린다** (2026-08-16 사용자 선택 「작가가 흡수」) —
        #     작가가 처음부터 3인칭 평서형으로 쓰므로 두 번 손댈 이유가 없다.
        #     작가가 «실패한» 칸에는 여전히 필요하다.
        sections = [
            replace(s, lines=readable_logic.rewrite_lines(s.lines, user_input.company))
            if s.lines and s.cell not in written_cells else s
            for s in sections
        ]

        report = Report(
            company=user_input.company,
            job=user_input.job,
            corp_type=judgment.corp_type,
            grade=grade,
            sections=sections,
            requirements=final_requirements,
            sources=sources,
            cells=cells,
            shortfall_reasons=shortfall,
            citations=citations,
            generated_at=dt.date.today().isoformat(),
        )

        # ── 14 저장 — 1층 캐시 (정본 §3 저장 구간) ────────
        # ★ 키에 쓰는 목록은 «5.5가 뽑은 requirements»다. 화면에 보이는
        #   `final_requirements`가 아니다 — 다음 요청은 5.5 결과로 조회하므로
        #   여기서 다른 목록을 쓰면 지문이 어긋나 영원히 미적중이 된다.
        # ★ 우리 쪽 수집 실패(⚠️)가 낀 결과는 «저장하지 않는다» —
        #   그날만 죽은 소스 때문에 그 회사가 「자료 없는 회사」로 굳는다.
        if _has_failed_source(sources):
            logger.info(
                "수집 실패(⚠️)가 껴 1층 캐시에 저장하지 않습니다 — corp_id=%s", corp_code
            )
        else:
            _cache_save(
                corp_id=corp_code,
                job=user_input.job,
                requirements=requirements,
                report=report,
                fiscal_year=current_fiscal_year,
            )

        return RunResult(
            outcome=Outcome.REPORT,
            report=report,
            sources=sources,
            charged=True,  # 보고서가 나가면 1 차감 (3분법 · D5 — 부분 보고서도 1)
            corp_type=judgment.corp_type,
            fragments_collected=len(frags),
            fragments_cited=_cited_fragment_count(kept),
            sentences_made=sentences_made,
            sentences_passed=len(kept),
            cost_krw=_request_spent_krw(engine),
            model=model,
        )


# ══════════════════════════════════════════════════════════
# 단계별 조각 — run()이 길어지지 않게 갈라 둔다
# ══════════════════════════════════════════════════════════

def _read_posting(
    engine: Any,
    client: Any,
    posting: str,
    steps: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    """5.5 공고 판별 3층 + 요구역량 뽑기.

    Returns:
        (요구역량 목록, 폐기했는가)

    ★ 3층 지우개를 반드시 통과시킨다. 개인정보가 그대로 AI로 넘어가면 안 된다.
    ★ 공고 «원문은 저장하지 않는다» (S2). 요구역량 목록과 지문만 남긴다.
    """
    schema = {
        "type": "object",
        "properties": {"is_job_posting": {"type": "boolean"}},
        "required": ["is_job_posting"],
        "additionalProperties": False,
    }
    first, usage = engine._ask(
        client,
        (
            "아래 글이 채용공고인가? 채용공고란 모집 주체인 회사가 사람을 고용하려고 지원자에게 "
            "직접 알리는 공고 본문이다. 채용 소식을 제3자가 전하거나 논평하는 글, 고용 목적이 "
            "아닌 모집 글은 채용공고가 아니다. 애매하면 false.\n\n---\n" + posting
        ),
        schema,
    )
    steps.append({"step": "5.5_1층AI", "usage": usage})
    if not (first and first.get("is_job_posting")):
        return [], True

    second = engine.layer2(posting)
    if not second.passed:
        return [], True

    erased = engine.erase(posting, run_id="웹")
    steps.append({"step": "5.5_3층지우개", "검출건수": erased.counts})

    schema_req = {
        "type": "object",
        "properties": {"requirements": {"type": "array", "items": {"type": "string"}}},
        "required": ["requirements"],
        "additionalProperties": False,
    }
    payload, usage = engine._ask(
        client,
        (
            "아래 채용공고에서 지원자에게 요구·우대하는 역량 문장을 **원문 그대로** 목록으로 뽑아라. "
            "다듬거나 요약하지 마라.\n\n---\n" + erased.text
        ),
        schema_req,
        max_tokens=1200,
    )
    steps.append({"step": "5.5_요구역량추출", "usage": usage})
    requirements = (payload or {}).get("requirements", [])
    return requirements, not requirements


#: 전자공시 보고서 이름 끝에 붙는 결산 기간 — 「감사보고서 (2025.12)」·
#: 「사업보고서 (2025.12)」. 1판이 실제로 받아 온 116건 전부 이 모양이었다
#: (`prototype_v1/data/pilot/runs*.jsonl` 실측 · 예외 0건).
_FILING_PERIOD_PATTERN = re.compile(r"\((\d{4})\.\d{2}\)")


def _filing_fiscal_year(filing: Optional[dict[str, Any]]) -> Optional[int]:
    """공시 보고서 이름에서 사업연도를 읽는다. 「감사보고서 (2025.12)」 → 2025.

    ★ 접수일(`rcept_dt`)을 쓰지 않는다. 접수일은 «언제 냈나»라 결산 연도와
      다르고, 매년 올라가기만 해서 「사업연도가 바뀌었다」를 못 가른다.
    """
    match = _FILING_PERIOD_PATTERN.search((filing or {}).get("report_nm") or "")
    return int(match.group(1)) if match else None


def _current_fiscal_year(
    fiscal_years: list[int], filing: Optional[dict[str, Any]]
) -> Optional[int]:
    """지금 기준 「최신 사업연도」 — 신선도(O9)의 비교 기준값.

    두 군데서 읽어 «더 최신»을 택한다.
      ① 재무 API가 실제로 자료를 준 사업연도들
      ② 최신 공시(사업보고서·감사보고서) 이름 안의 결산 연도

    ★ 왜 둘 다 보나 — 재무 API(단일회사 주요계정)는 **비상장 외감에서 자주
      빈손이다.** 1판 실측 28건 중 13건(전부 비상장 외감)이 그랬다. ①만 쓰면
      그 회사들은 「사업연도를 모름」이 되어 **캐시가 영영 적중하지 않는다.**
    ★ 왜 «더 최신»인가 — 한쪽이 옛 연도에 멈춰 있어도(실측: 재무 API가
      2023에 멈춘 회사) 다른 쪽이 새 자료를 알아채면 캐시가 만료된다.
      「작년 보고서가 계속 나가는」 구멍(정본 §2)을 막는 쪽으로 기운다.
    ★ `dt.date.today().year - 1`로 **추측하지 않는다.** 사업보고서는 결산 뒤
      몇 달 지나야 올라오므로 1~3월에는 작년 자료가 없는 것이 정상이다.
      추측하면 그 기간 내내 오판해 캐시가 통째로 무효가 된다.

    Returns:
        최신 사업연도. 두 군데 다 모르면 `None` — 그때는 캐시가
        «신선하다고 우기지 않는다» (`cache.get_layer1_hit` 참고).
    """
    candidates = [year for year in fiscal_years if year]
    from_filing = _filing_fiscal_year(filing)
    if from_filing is not None:
        candidates.append(from_filing)
    return max(candidates) if candidates else None


def _cache_lookup(
    *,
    corp_id: str,
    job: str,
    requirements: list[str],
    current_fiscal_year: Optional[int],
) -> Optional[Report]:
    """1층 캐시를 본다 (정본 §1 — 회사 고유번호 × 직무 × 공고 지문).

    ★ 캐시가 깨졌거나·잠겼거나·아직 없어도 **조사 전체를 죽이지 않는다.**
      캐시는 «빠른 길»일 뿐이므로, 실패하면 조용히 원래 길(재조사)로 간다.

    Returns:
        지문이 같고 O9 신선도를 통과한 보고서. 없으면 `None`(= 미적중).
    """
    if not corp_id or not requirements:
        # 고유번호나 요구역량이 없으면 키를 만들 수 없다. 회사명으로 대신
        # 찾지 않는다 — 이름이 같은 다른 법인의 보고서가 나갈 수 있다.
        return None
    try:
        with storage_db.connect() as conn:
            hit = cache_store.get_layer1_hit(
                conn,
                corp_id=corp_id,
                job=job,
                requirements=requirements,
                current_fiscal_year=current_fiscal_year,
            )
    except Exception:  # noqa: BLE001 — 캐시 실패가 조사를 막으면 안 된다
        logger.exception("1층 캐시 조회 실패 — 새로 조사합니다 (corp_id=%s)", corp_id)
        return None

    if hit is None:
        logger.info(
            "1층 캐시 미적중 — 새로 만듭니다 (corp_id=%s · 직무=%s · 최신사업연도=%s)",
            corp_id, job, current_fiscal_year,
        )
    else:
        logger.info(
            "1층 캐시 적중 — 생성·검증 AI를 건너뜁니다 (corp_id=%s · 직무=%s · 조사일=%s)",
            corp_id, job, hit.generated_at,
        )
    return hit


def _cache_save(
    *,
    corp_id: str,
    job: str,
    requirements: list[str],
    report: Report,
    fiscal_year: Optional[int],
) -> None:
    """완성된 보고서를 1층 캐시에 넣는다 (파이프라인 14 저장).

    ★ `requirements`는 **조회에 쓴 것과 같은 목록**이어야 한다. 여기서 다른
      목록(예: 생성 결과에서 다시 뽑은 문장)을 쓰면 지문이 어긋나 다음 요청이
      «영원히» 미적중이 된다 — 캐시가 있는데도 매번 돈이 나가는 상태가 된다.
    ★ 저장이 실패해도 사용자의 보고서를 막지 않는다 (화면은 이미 만들어졌다).
    """
    if not corp_id or not requirements:
        return
    try:
        with storage_db.connect() as conn:
            cache_store.save_layer1(
                conn,
                corp_id=corp_id,
                job=job,
                requirements=requirements,
                report=report,
                fiscal_year=fiscal_year,
            )
    except Exception:  # noqa: BLE001 — 저장 실패가 사용자를 막으면 안 된다
        logger.exception("1층 캐시 저장 실패 (보고서는 정상) — corp_id=%s", corp_id)


def _write_prose(
    engine: Any,
    client: Any,
    user_input: UserInput,
    sections: list[ReportSection],
    steps: list[dict[str, Any]],
    model: str,
) -> tuple[list[ReportSection], set[str]]:
    """11 작성 — 근거를 «하나의 글»로 잇는다 (P-110). AI 2회.

    Args:
        engine: 1판 엔진 (`_ask`를 빌려 쓴다).
        client: Anthropic 클라이언트.
        user_input: 회사·직무.
        sections: 원문 문장이 담긴 보고서 칸들.
        steps: 단계 기록.
        model: 이 요청에서 쓰는 모델.

    Returns:
        (글이 붙은 칸들, 작가가 «실제로 쓴» 칸 번호들).

    ★★ **작가와 검증은 한 벌이다.** 검증이 죽거나 통과가 0이면 그 칸은
      **원문 나열 그대로** 둔다 — 검사 없이 새 글을 내보내지 않는다.
    ★ 원문(`lines`)을 지우지 않는다. 화면이 「원문 보기」로 같이 낸다.
    ⚠️ 여기서 터져도 보고서 전체가 죽으면 안 된다 — 글은 «덤»이고 원문이 본체다.
    """
    lines_by_cell = {s.cell: list(s.lines) for s in sections if s.lines}
    evidence = writer_logic.collect_evidence(lines_by_cell)
    if not evidence:
        return sections, set()

    def ask(prompt: str, schema: dict[str, Any], max_tokens: int):
        engine_model = getattr(engine, "MODEL", "")
        try:
            if model:
                engine.MODEL = model
            payload, usage = engine._ask(client, prompt, schema, max_tokens=max_tokens)
        finally:
            if model:
                engine.MODEL = engine_model
        # ★ 단계 기록에도 어느 모델인지 남긴다. 비용 정본은 동시성에 안전한 client
        #   응답 계량이고, 이 값은 나중에 단계별 결과를 재현할 때 쓴다.
        if isinstance(usage, dict):
            usage = {**usage, writer.USAGE_MODEL_KEY: model or engine_model}
        return payload, usage

    try:
        written, write_step = writer_logic.write_with_ai(
            lambda p, s: ask(p, s, writer.WRITE_MAX_TOKENS),
            company=user_input.company,
            job=user_input.job,
            evidence=evidence,
        )
        steps.append({"step": writer.WRITE_STEP, **write_step})
        if not written:
            return sections, set()

        # ★ 다른 호출·다른 지시문이다 (정본 Generator/Evaluator 분리) —
        #   같은 대화에서 이어 물으면 «자기가 쓴 것»을 감싸게 된다.
        passed, verify_step = writer_verify.verify_with_ai(
            lambda p, s: ask(p, s, writer_verify.VERIFY_MAX_TOKENS),
            written=written,
            evidence=evidence,
        )
        steps.append({"step": writer_verify.VERIFY_STEP, **verify_step})
    except Exception as exc:  # noqa: BLE001 — 글은 «덤»이다. 원문 보고서를 죽이면 안 된다
        steps.append({"step": writer.WRITE_STEP, "오류": f"{type(exc).__name__}: {str(exc)[:80]}"})
        return sections, set()

    # ★ 검증 뒤에도 문장별 근거를 버리지 않는다(P-118). 문자열 하나로 합치면
    #   내부 sid와 실제 출처의 연결이 끊겨 화면에서 근거 번호를 못 붙인다.
    prose_lines_by_cell = {
        cell: writer_logic.to_cited_lines(sents, evidence.get(cell, []))
        for cell, sents in passed.items()
    }
    출처연결버림 = sum(
        len(sents) - len(prose_lines_by_cell.get(cell, []))
        for cell, sents in passed.items()
    )
    if 출처연결버림:
        # 조용히 지우면 출처 연결 결함이 다시 생겨도 실측에서 알 수 없다.
        steps.append({"step": writer.CITE_LINK_STEP, "버림": 출처연결버림})
    prose_lines_by_cell = {c: lines for c, lines in prose_lines_by_cell.items() if lines}
    if not prose_lines_by_cell:
        return sections, set()
    out = [
        replace(s, prose_lines=prose_lines_by_cell[s.cell])
        if s.cell in prose_lines_by_cell else s
        for s in sections
    ]
    return out, set(prose_lines_by_cell)


def _collect_news(
    engine: Any,
    client: Any,
    company: str,
    profile: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """6 수집 — 뉴스. **AI가 번호를 고르고 프로그램이 원문을 복사한다** (P-108).

    Args:
        engine: 1판 엔진 (`search_news`·`_ask`를 빌려 쓴다).
        client: 1판이 만든 Anthropic 클라이언트.
        company: 회사 이름.
        profile: 기업개황 — «동명 타사»를 가르는 단서가 여기서 나온다.
        steps: 단계 기록 (여기에 결과를 남긴다).

    Returns:
        뉴스 조각 목록. 실패하면 빈 목록.

    ★ 단계 이름과 열쇠(`검색결과`·`채택`·`오류`)를 **1판과 똑같이** 남긴다 —
      출처 목록과 빈칸 사유가 이 열쇠들을 읽는다. 바꾸면 조용히 어긋난다.
    ★ 모델을 갈아 끼우지 «않는다» — 엔진 기본값(하이쿠)을 그대로 쓴다.
      번호만 고르는 일이라 충분하고, 단가가 어긋나지 않아 비용이 정확하다.
    """
    step: dict[str, Any] = {"step": "6_수집_뉴스"}
    # ★ 주제별로 나눠 검색한다 — 회사 이름만 최신순으로 찾으면 «오늘 기사»만 잡힌다.
    #   실측(하이브): 30건 전부 그날 나온 걸그룹 차트 기사였다. 취준생에게 쓸모가 없다.
    groups: list[list[Any]] = []
    검색별: dict[str, int] = {}
    첫오류 = ""
    for 검색어, 정렬, 개수 in newspick_logic.search_terms(
        company, profile, newspick.SEARCH_QUERIES
    ):
        try:
            found = engine.search_news(검색어, display=개수, sort=정렬)
        except Exception as exc:  # noqa: BLE001 — 한도·인증·네트워크. 나머지 검색은 계속한다
            첫오류 = 첫오류 or f"{type(exc).__name__}: {str(exc)[:60]}"
            검색별[검색어] = 0
            continue
        groups.append(found)
        검색별[검색어] = len(found)

    items = newspick_logic.interleave(groups, newspick.MAX_CANDIDATES * 2)
    if not items:
        # ★ 「검색이 막혔다(⚠️)」와 「기사가 없다(❌)」를 구분한다 — 섞으면 오거부가 된다.
        step.update({"검색결과": 0, "채택": 0, "검색별": 검색별})
        if 첫오류:
            step["오류"] = 첫오류
        steps.append(step)
        return []

    candidates, dropped = newspick_logic.prefilter(
        items, company=company, today=dt.date.today()
    )
    step.update({"검색결과": len(items), "검색별": 검색별, "사전거름": dropped})
    if 첫오류:
        step["일부검색실패"] = 첫오류

    try:
        picked, 기록 = newspick_logic.pick_with_ai(
            lambda prompt, schema: engine._ask(
                client, prompt, schema, max_tokens=newspick.PICK_MAX_TOKENS
            ),
            company=company,
            profile=profile,
            candidates=candidates,
        )
    except Exception as exc:  # noqa: BLE001 — AI가 막혀도 보고서 전체가 멈추면 안 된다
        step.update({"채택": 0, "오류": f"{type(exc).__name__}: {str(exc)[:80]}"})
        steps.append(step)
        return []

    step.update(기록)
    step.setdefault("채택", len(picked))
    steps.append(step)
    return newspick_logic.to_fragments(picked)


def _collect(
    engine: Any,
    client: Any,
    profile: dict[str, Any],
    user_input: UserInput,
    counter: Any,
    steps: list[dict[str, Any]],
    *,
    financials: Optional[dict[str, Any]],
    fin_years: list[int],
    filing: Optional[dict[str, Any]],
) -> tuple[dict[int, dict[str, str]], list[dict]]:
    """6 수집 — 공시 원문 + 재무 API + 뉴스 + 홈페이지를 조각으로 만든다. AI 0회.

    Args:
        financials: 재무 API 응답. ★ 여기서 다시 부르지 않는다 — 캐시 신선도를
            보려고 `run()`이 «이미» 불렀다. 두 번 부르면 DART 일일 한도만 깎는다.
        fin_years: 그때 실제로 자료가 있던 사업연도 목록 (단계 기록용).
        filing: 최신 공시 1건(보고서 이름·접수번호). 이것도 `run()`이 이미 받았다.
            **출처 목록을 만들 때 쓴다** (P-24). 공시를 못 찾았으면 None.

    Returns:
        조각 목록.
    """
    filing_text = ""
    if filing:
        try:
            path = engine.download_document(filing["rcept_no"], engine.RAW_DIR, counter)
            filing_text = engine.read_filing_text(path)
        except (RuntimeError, OSError) as exc:
            # 못 가져온 사실을 남긴다 — 조용히 넘어가면 「회사에 자료가 없다」로 잘못 읽힌다.
            steps.append({"step": "6_수집_원문", "오류": str(exc)[:120]})

    frags = engine.make_fragments(filing_text, financials)
    # ★ 1판은 절 표제의 «첫 출현»만 본다. 그런데 사업보고서 첫 장이 «목차»라,
    #   「사업의 내용」의 첫 출현이 목차 줄이고 거기서 1,200자를 뜨면 통째로 목차가 된다.
    #   실측 — 하이브 조각 9개 중 3개가 목차였고, 그래서 1·3·4번 칸이 비었다 (P-99).
    #   1판은 안 고치고, 나온 조각 중 목차인 것만 «다음 출현»으로 다시 뜬다.
    # ⚠️ `getattr`로 받는다 — 1판이 이름을 바꾸거나 시험용 가짜 엔진을 끼울 때
    #   여기서 터지면 **조사 전체가 멈춘다.** 못 받으면 «고치지 않고» 넘어갈 뿐이다.
    frags, repaired = filing_clean.repair(
        frags,
        filing_text,
        getattr(engine, "SECTION_HEADS", {}),
        getattr(engine, "FRAG_CHARS", 0),
    )
    if repaired:
        steps.append({"step": "6_수집_목차보정", "고친조각": repaired})

    # ★ 1판이 «안 뜨는» 절을 더 모은다 (P-105) — 신규사업 전망·시장 특성·소송.
    #   사용자 지적(「그냥 DART 뜯어온 거라 이럴 거면 DART를 직접 보지」)의 핵심 원인이
    #   **정작 필요한 절을 안 뜨던 것**이었다. 1판은 0줄 고치지 않고 «더할» 뿐이다.
    frags, added = filing_extra.add_to(
        frags, filing_text, getattr(engine, "FRAG_CHARS", filing_extra.DEFAULT_FRAG_CHARS)
    )
    if added:
        steps.append({"step": "6_수집_추가절", "더한조각": added})

    # ★ 뉴스는 «AI가 번호로» 고른다 (P-108). 1판은 «제목에 회사 이름이 글자 그대로»
    #   있어야 채택해서, 유명한 회사일수록 0건이었다 (하이브 20건 중 0건 실측).
    #   기사가 브랜드·아티스트·제품 이름으로 나가기 때문이다.
    #   ⚠️ 글자 맞추기를 그냥 넓히면 엉뚱한 기사가 딸려 온다 (P-98 실측) —
    #      그래서 «판단»은 AI가 하고, **원문 복사는 프로그램이 한다** (정본 방식).
    for news_frag in _collect_news(engine, client, user_input.company, profile, steps):
        frags[max(frags, default=0) + 1] = news_frag

    # 회사 홈페이지 — 2번(뭘 잘하나)이 만성적으로 비는 원인이었다 (문제로그 P-35 · D14-7).
    # ★ 실패를 「없음」과 반드시 구분한다. 섞으면 「이 회사는 자료가 없다」로 잘못 읽힌다.
    homepage = collect_homepage_fragments(profile.get("hm_url", ""))
    if homepage.state == "ok":
        for frag in homepage.fragments:
            frags[max(frags, default=0) + 1] = {
                "종류": frag["종류"],
                "원문": frag["원문"],
                # ★ 실제로 읽은 주소를 버리지 않는다 — 출처 목록이 이걸 쓴다 (P-24)
                "출처": frag.get("출처", ""),
            }
        steps.append({"step": "6_수집_홈페이지", "조각수": len(homepage.fragments)})
    elif homepage.state == "failed":
        steps.append({"step": "6_수집_홈페이지", "오류": homepage.detail})
    else:
        steps.append({"step": "6_수집_홈페이지", "없음": homepage.detail})

    # ★ 매출 구성 비중 표 (P-112) — 사용자가 리포트 11건에서 고른 항목 ①.
    #   **11건이 «전부» 실은 유일한 만장일치 항목**이다.
    #   ⚠️ 지어낼 자리가 없다 — 공시가 비중을 이미 계산해 놓았고 우리는 베낄 뿐이다.
    revenue_tables = revenuemix.build(filing_text, cite=REVENUE_CITE)
    if revenue_tables:
        steps.append({"step": "6_수집_매출구성", "표": len(revenue_tables)})

    steps.append(
        {
            "step": "6_수집",
            "원문": (filing or {}).get("report_nm"),
            "조각수": len(frags),
            "재무API연도": fin_years,
        }
    )
    return frags, revenue_tables


def _region_matches(typed: str, address: str) -> bool:
    """입력 지역이 주소와 같은 곳인가.

    「강원 강릉시」와 「강원도 강릉시 …」는 같은 곳이다 (문제로그 P-26).
    데모와 같은 규칙이라 그쪽 함수를 그대로 쓴다 — 두 벌로 나뉘면 반드시 어긋난다.
    """
    from src.features.pipeline.demo import _region_matches as shared  # noqa: PLC0415

    return shared(typed, address)


def _message(outcome: Outcome) -> str:
    """실패했을 때 사용자에게 보여줄 말. 데모와 같은 문장을 쓴다."""
    from src.features.pipeline.demo import _OUTCOME_MESSAGE  # noqa: PLC0415

    return _OUTCOME_MESSAGE.get(outcome, "")

"""저장소(SQLite) 표 이름·기본 경로·캐시 상한값을 한곳에 모은다.

★ 규칙 — 표 이름·상한값·기본 경로를 코드 여기저기에 문자열·숫자로 박지 않는다.
   여기만 고치면 db.py·reports.py·cache.py·sessions.py가 전부 맞춰진다.

정본:
  - 확정/03_수집/2_규칙/03_캐시와저장.md    (캐시 키 · 저장 목록 9종 · 보관 상한)
  - 확정/00_공통/2_규칙/01_도구정의.md §4   (S2 — 공고 원문 미저장)
  - 확정/99_기준/2_규칙/01_안전과가드레일.md (S1·S2 「0건」)
"""

from __future__ import annotations

from typing import Final

# ── DB 파일 ──────────────────────────────────────────────
#: `db_path`를 안 넘겼을 때 쓰는 기본 위치 — `core/paths.APP_ROOT` 기준 상대 경로.
#: `data/observability/`(이력 파일)와 같은 자리에 둔다 — 둘 다 "런타임에 생기는,
#: 커밋 대상이 아닌 자료"이기 때문이다.
DEFAULT_DB_RELATIVE_PATH: Final[str] = "data/storage.db"

#: 다른 연결이 쓰는 중일 때 몇 초까지 기다렸다 포기하나 ("database is locked" 방지).
DB_BUSY_TIMEOUT_SEC: Final[float] = 5.0

#: 첫 schema bootstrap을 다른 프로세스와 직렬화할 때 기다리는 상한.
#: 일반 쿼리 잠금과 달리 배포 직후 한 번만 잡는 파일 잠금이라, 느린 영속
#: 디스크에서도 정상 migration이 끝날 여유를 주되 영원히 매달리지는 않는다.
DB_SCHEMA_LOCK_TIMEOUT_SEC: Final[float] = 30.0

# SQLite 공식 문서의 WAL-reset 결함 수정 경계.
# 3.7.0~3.51.2 기본 계열은 영향을 받으며, 3.44.6·3.50.7에는 수정이
# 별도로 역이식되었다. 패치되지 않은 런타임은 rollback journal을 쓴다.
SQLITE_WAL_FIXED_VERSION: Final[tuple[int, int, int]] = (3, 51, 3)
SQLITE_WAL_FIXED_BACKPORT_RANGES: Final[
    tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...]
] = (
    ((3, 44, 6), (3, 45, 0)),
    ((3, 50, 7), (3, 51, 0)),
)
SQLITE_JOURNAL_MODE_WAL: Final[str] = "WAL"
SQLITE_JOURNAL_MODE_FALLBACK: Final[str] = "DELETE"

# SQLite 공식 ``PRAGMA synchronous`` 숫자 계약. 영향을 받는 런타임이 DELETE
# rollback journal을 쓰므로 FULL(2)에 맡기지 않고 EXTRA(3)를 명시한다. EXTRA는
# commit 때 journal을 지운 부모 directory까지 동기화해 직후 전원 중단의 내구성을
# 보강한다. WAL에서는 EXTRA와 FULL의 동기화 계약이 같다.
SQLITE_SYNCHRONOUS_MODE: Final[str] = "EXTRA"
SQLITE_SYNCHRONOUS_LEVEL: Final[int] = 3

# ── 표 이름 ──────────────────────────────────────────────
TABLE_REPORTS: Final[str] = "reports"
#: 공개 봉인 projection은 보고서 payload와 «다른 표»에 둔다.
#: payload 안에 넣었더니 저장 JSON 노드 수가 1.98배가 되어
#: `core/persisted_json.py`의 `MAX_DOCUMENT_NODES` 여유가 절반으로 줄고 관리자
#: 수정 폼 상한(250,000자)을 넘었다. 나눠 두면 두 문서가 각자 상한 아래에 있고
#: 옛 payload 바이트도 한 글자도 안 바뀐다.
TABLE_REPORT_PUBLIC_PROJECTIONS: Final[str] = "report_public_projections"
TABLE_LAYER1_CACHE: Final[str] = "layer1_cache"
TABLE_LAYER2_CACHE: Final[str] = "layer2_cache"
TABLE_ALIAS_CACHE: Final[str] = "alias_cache"
TABLE_SESSIONS: Final[str] = "sessions"

# ── 1층 캐시 보관 상한 (정본 §보관 상한) ─────────────────
#: 회사×직무당 최근 몇 건까지 남기나. 넘으면 지운다.
#: 축출 순서는 `cache.py`의 `_evict_layer1_overflow` — 사업연도가 다른(신선도
#: 만료로 보이는) 것을 먼저, 그다음 오래된 것부터.
LAYER1_MAX_ENTRIES_PER_JOB: Final[int] = 5


#: DB 파일 위치를 바꾸는 환경변수. 시험과 배포에서 쓴다.
ENV_DB_PATH: Final[str] = "STORAGE_DB_PATH"

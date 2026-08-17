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
#: ⚠️ 팀장 확인 필요 — `.gitignore`가 지금 `data/observability/`만 가리고
#:   `data/storage.db`는 안 가린다. `.gitignore`는 storage/ 밖이라 여기서
#:   고치지 않았다 (최종 보고 §위험 요소 참고).
DEFAULT_DB_RELATIVE_PATH: Final[str] = "data/storage.db"

#: 다른 연결이 쓰는 중일 때 몇 초까지 기다렸다 포기하나 ("database is locked" 방지).
DB_BUSY_TIMEOUT_SEC: Final[float] = 5.0

# ── 표 이름 ──────────────────────────────────────────────
TABLE_REPORTS: Final[str] = "reports"
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

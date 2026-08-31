"""이 폴더만 pytest 대상으로 줘도 core·features 절대 import가 되게 한다.

`analysis_engine/src`에는 `__init__.py`가 없다. pytest를
`analysis_engine/src` 전체로 돌리면 그 디렉터리가 sys.path에 실리지만,
`analysis_engine/src/features/evidence_collection`처럼 하위 폴더만 대상으로
주면 실리지 않는다(실측 확인 — `from core...`/`from features...`가
ModuleNotFoundError를 낸다). 이 파일은 호출 방식과 무관하게 항상
`analysis_engine/src`를 sys.path에 넣어 둘 결과를 같게 만든다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

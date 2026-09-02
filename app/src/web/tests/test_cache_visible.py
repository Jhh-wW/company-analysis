"""캐시가 «돌았다는 사실»이 사용자 눈에 보이는지 못 박는다.

이 시험이 잡는 것 — **기능은 붙였는데 화면·이력이 옛말을 하는 경우**.
캐시를 붙여도 이력이 「없음」 고정이면 대시보드 ⑤는 영영 0건이고,
화면에 안내가 없으면 사용자는 방금 새로 조사한 줄 안다.

★ 여기서 두 곳을 한꺼번에 본다 — 하나만 고치면 나머지가 조용히 어긋난다.
  ① `RunResult.cache_hit` → ② 이력 1행 → ③ 결과 화면 안내

  대시보드 ⑤ 집계 시험은 뺐다. 그 식이 살던 `dashboard.html` 은 어떤 라우트도
  렌더하지 않아 지웠고, 남겨 두면 시험이 «자기가 복사해 둔 문자열을 자기가 렌더해
  자기와 비교»하는 꼴이 되어 아무것도 못 지키면서 영원히 통과한다.
"""

from __future__ import annotations

from src.core.constants import CACHE_HIT_LAYER1, CACHE_HIT_MESSAGE
from src.features.observability import constants as obs
from src.features.pipeline.port import Outcome, RunResult, UserInput
from src.web import recording

# ══════════════════════════════════════════════════════════
# ① 이력 — 파이프라인이 실은 값이 그대로 실려야 한다
# ══════════════════════════════════════════════════════════


def test_캐시로_돌려준_요청은_이력에_1층으로_남는다(tmp_path, monkeypatch):
    monkeypatch.setattr(recording.paths, "APP_ROOT", tmp_path)
    result = RunResult(
        outcome=Outcome.REPORT,
        message=CACHE_HIT_MESSAGE.format(generated_at="2026-08-15"),
        cache_hit=CACHE_HIT_LAYER1,
    )

    recording.record_run(UserInput(company="가나다", job="영업", region=""), result, 1.0)

    written = recording.records_path().read_text(encoding="utf-8")
    assert f'"cache_hit": "{obs.CACHE_HIT_L1}"' in written


def test_새로_조사한_요청은_이력에_없음으로_남는다(tmp_path, monkeypatch):
    """캐시를 안 썼는데 「썼다」고 적으면 비용 지표가 통째로 거짓이 된다."""
    monkeypatch.setattr(recording.paths, "APP_ROOT", tmp_path)
    result = RunResult(outcome=Outcome.REPORT)

    recording.record_run(UserInput(company="가나다", job="영업", region=""), result, 1.0)

    written = recording.records_path().read_text(encoding="utf-8")
    assert f'"cache_hit": "{obs.CACHE_HIT_NONE}"' in written


# ══════════════════════════════════════════════════════════
# ② 결과 화면 — 「저장해 둔 것」이라고 말해야 한다
# ══════════════════════════════════════════════════════════


def test_캐시_안내_문구는_조사한_날짜를_반드시_담는다():
    """날짜 없이 「저장된 결과」라고만 하면 «언제 것인지» 알 수 없다.

    3년 지난 자료로 자소서를 쓰면 안 된다 — 신선도는 사용자가 판단할 몫이다.
    """
    message = CACHE_HIT_MESSAGE.format(generated_at="2026-08-15")

    assert "2026-08-15" in message


def test_결과화면_템플릿이_캐시일_때만_안내를_그린다():
    """조건을 빼먹으면 새로 조사한 보고서에도 「저장해 둔 결과」가 붙는다."""
    from src.core import paths

    template = (
        paths.APP_ROOT / "src" / "web" / "templates" / "result.html"
    ).read_text(encoding="utf-8")

    assert "result.cache_hit" in template, (
        "결과 화면이 캐시 여부를 안 읽습니다 — 캐시가 돌아도 사용자가 모릅니다 (P-63)"
    )
    assert "result.message" in template


def test_결과화면_출처표는_공통_공개citation_목록만_순회한다() -> None:
    from src.core import paths

    template = (
        paths.APP_ROOT / "src" / "web" / "templates" / "result.html"
    ).read_text(encoding="utf-8")

    assert "{% if public_citations %}" in template
    assert "{% for c in public_citations %}" in template
    assert "{% for c in report.citations %}" not in template

"""1층 캐시가 진짜 알맹이(`real.py`)에 «제대로» 꽂혔는지 검사한다.

★ 진짜 엔진을 부르지 않는다. `_engine()`이 돌려주는 것을 **가짜 엔진**으로
  바꿔 끼워, AI 호출 0회·0원으로 파이프라인 전체를 돌린다.
  (진짜로 돌리면 1건당 AI 최대 13회 = 실제 비용이 나간다.)

★ 저장소는 `conftest.py`가 시험마다 임시 폴더로 바꿔 놓는다 (P-62) —
  이 시험은 진짜 DB를 건드리지 않는다.

이 시험이 잡는 것:
  - 같은 회사·같은 공고를 다시 조사할 때 **생성·검증 AI가 또 나가는 것**
  - 공고가 다른데 **남의 옛 보고서가 나가는 것** (정본 §★ 사고 시나리오)
  - 사업연도가 바뀌었는데 **작년 보고서가 계속 나가는 것** (O9 신선도)
  - 캐시가 깨졌을 때 **조사 전체가 같이 죽는 것**
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, UserInput
#: 8·9 생성 지시문임을 알아보는 표시. 글자를 베끼지 않고 «상수를 그대로» 쓴다 —
#: 지시문이 바뀌어도 이 시험이 조용히 어긋나지 않는다.
from src.features.spanselect.constants import PROMPT_PICK

# ── 가짜 엔진이 쓰는 고정값 ───────────────────────────────
CORP_ID = "00126380"
JOB = "백엔드 개발자"
POSTING = "3년 이상 경력\n파이썬 실무 경험"
OTHER_POSTING = "신입 가능\n자바 경험 우대"
#: 재무 API가 「이 연도 자료가 있다」고 답하는 값 — 신선도(O9) 비교 기준이 된다.
FISCAL_YEARS = [2025, 2024]
#: 최신 공시 이름에 찍히는 결산 연도 — 「사업보고서 (2025.12)」의 2025.
#: (1판 실측 116건 전부 이 모양이었다 — `prototype_v1/data/pilot/runs*.jsonl`)
FILING_YEAR = 2025


class _FakeCounter:
    """엔진의 `UsageCounter` 흉내 — 몇 번 불렀는지만 센다."""

    def __init__(self) -> None:
        self.count = 0


class _FakeJudgment:
    """`engine.decide()` 결과 흉내 — 항상 「대상」."""

    status = "대상"
    corp_type = "상장사"


#: 가짜 지우개가 지우는 것 — 휴대전화 번호 모양. 진짜 지우개는 이름·생년월일도 본다.
_PHONE_PATTERN = re.compile(r"\d{2,3}-\d{3,4}-\d{4}")
_MASK = "[삭제:연락처]"


class _FakeDraftItem:
    """엔진이 고른 문장 하나 흉내."""

    def __init__(self, block: str, sentence: str, fragment_id: Optional[int]) -> None:
        self.block = block
        self.sentence = sentence
        self.fragment_id = fragment_id


class _FakeMessages:
    """요청별 계량 client 계약을 지키되 네트워크는 전혀 쓰지 않는다."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            model=kwargs.get("model", "가짜모델"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class FakeEngine:
    """1판 엔진 흉내. `real.py`가 부르는 이름만 갖고 있다.

    ★ AI를 부르는 자리(`_ask`·`generate_and_check`·`substance_check`)에서
      호출 수를 센다. 캐시가 먹었는지는 «이 숫자»로 판정한다.
    """

    MODEL = "가짜모델"
    KRW_PER_USD = 1400
    RAW_DIR = "(가짜)"
    PUBLIC_ORG_REGISTRY = "(가짜)"
    #: 칸 → 그 칸을 채울 수 있는 조각 종류. 가짜라 두 칸만 둔다.
    CELL_SOURCES = {"1": ("사업",), "4-1": ("사업",)}
    EMPTY_REASONS: dict[str, str] = {}
    UsageCounter = _FakeCounter
    _spent_usd = 0.0

    def __init__(self, fiscal_years: Optional[list[int]] = None) -> None:
        #: 5.5(공고 판별·요구역량 추출) AI 호출 수
        self.posting_ai_calls = 0
        #: 8·9 생성 + 10 검증 AI 호출 수 — **캐시가 먹으면 0이어야 한다**
        self.generate_ai_calls = 0
        self.fiscal_years = list(FISCAL_YEARS if fiscal_years is None else fiscal_years)
        #: 최신 공시 이름에 찍히는 결산 연도. None이면 연도가 안 적힌 이름을 준다.
        self.filing_year: Optional[int] = FILING_YEAR
        #: True면 뉴스 수집이 «우리 쪽 실패»(⚠️)로 끝난 것처럼 군다.
        self.news_fails = False
        self.client = SimpleNamespace(messages=_FakeMessages())

    # ── 준비 ─────────────────────────────────────────────
    def load_env(self) -> None:
        return None

    def _client(self) -> SimpleNamespace:
        return self.client

    # ── DART ─────────────────────────────────────────────
    def get_json(self, endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        counter.count += 1
        if endpoint == "company.json":
            return {
                "status": "000",
                "corp_name": "가나다전자",
                "adres": "서울특별시 강남구 테헤란로 1",
                "ceo_nm": "홍길동",
                "est_dt": "20000101",
                "hm_url": "",           # 빈 값 → 홈페이지 수집이 네트워크를 안 탄다
                "corp_cls": "Y",
                "bizr_no": "1234567890",
            }
        if endpoint == "list.json":
            return {"status": "000", "list": [{"report_nm": "감사보고서"}]}
        if endpoint == "empSttus.json":
            return {"status": "013"}     # 자료 없음 → 附은 사유만 붙는다
        return {"status": "000"}

    def load_public_org_registry(self, path: Any) -> dict[str, Any]:
        return {}

    def match_public_org(self, bizr_no: Any, registry: Any) -> None:
        return None

    def decide(
        self, corp_cls: str, has_audit: bool, bizr_no: Any, matcher: Any
    ) -> _FakeJudgment:
        return _FakeJudgment()

    # ── 5.5 공고 판별 (AI) · 8·9 생성 (AI) ────────────────
    def _ask(
        self, client: Any, prompt: str, schema: dict[str, Any], max_tokens: int = 0
    ) -> tuple[dict[str, Any], dict[str, int]]:
        # 실제 1판 `_ask`와 같이 provider client 경계를 지난다. 이 한 줄이 없으면
        # 파이프라인은 돌아도 새 요청별 비용 계량기는 아무것도 시험하지 못한다.
        response = client.messages.create(model=self.MODEL)
        usage = {
            "in": response.usage.input_tokens,
            "out": response.usage.output_tokens,
        }
        # ★ 8·9 생성은 이제 `spanselect`가 «같은 창구»(`_ask`)로 부른다 (P-43).
        #   5.5와 «다른 통»에 세야 「캐시가 먹으면 생성이 0회」를 잴 수 있다.
        if PROMPT_PICK in prompt:
            self.generate_ai_calls += 1
            return (
                {"items": [{"block": "1", "sid": "1-1"}, {"block": "5", "sid": "R-1"}]},
                usage,
            )
        self.posting_ai_calls += 1
        if "채용공고인가" in prompt:
            return {"is_job_posting": True}, usage
        # 요구역량 추출 — 프롬프트 끝에 붙은 공고 본문을 줄 단위로 돌려준다.
        body = prompt.split("---\n", 1)[-1]
        return {
            "requirements": [ln.strip() for ln in body.splitlines() if ln.strip()]
        }, usage

    def layer2(self, posting: str) -> SimpleNamespace:
        return SimpleNamespace(passed=True)

    def erase(self, text: str, run_id: str = "") -> SimpleNamespace:
        """3층 개인정보 지우개 흉내 — **실제로 지운다.**

        ★ 예전에는 원문을 그대로 돌려주는 껍데기였다. 그때
          `test_공고_원문은_저장소에_들어가지_않는다`가 통과한 것은 지우개 덕이
          아니라, 가짜 생성기가 배치 안 된 요구역량을 **조용히 버렸기** 때문이다.
          정본 「조용한 누락 금지」(05_생성/1_흐름/01_문장스팬선택.md:123-132)를
          지키자 그 구멍이 드러났다 — **시험이 엉뚱한 이유로 통과하고 있었다.**
          진짜 경로는 요구역량을 뽑기 «전에» 지우개를 돌린다 (`real.py` 5.5).
        """
        erased, hits = _PHONE_PATTERN.subn(_MASK, text)
        return SimpleNamespace(text=erased, counts={"연락처": hits} if hits else {})

    # ── 6 수집 ───────────────────────────────────────────
    def latest_report_rcept(
        self, corp_code: str, corp_type: str, counter: Any
    ) -> dict[str, Any]:
        year = self.filing_year
        name = "사업보고서" if year is None else f"사업보고서 ({year}.12)"
        return {
            "report_nm": name,
            "rcept_no": "20260315000123",
            "rcept_dt": "20260315",
        }

    def download_document(self, rcept_no: str, raw_dir: Any, counter: Any) -> str:
        return "(가짜 경로)"

    def read_filing_text(self, path: str) -> str:
        return "회사는 반도체 검사 장비를 만들어 국내외 제조사에 판다."

    def fetch_financials(
        self, corp_code: str, counter: Any
    ) -> tuple[dict[str, Any], list[int]]:
        return {"list": []}, list(self.fiscal_years)

    def make_fragments(
        self, filing_text: str, financials: Optional[dict[str, Any]]
    ) -> dict[int, dict[str, str]]:
        return {1: {"종류": "사업", "원문": filing_text, "출처": ""}}

    def search_news(
        self, query: str, display: int = 10, sort: str = "date"
    ) -> list[SimpleNamespace]:
        """네이버 뉴스 검색 흉내 (P-108).

        ★ 1판 `collect_news`를 대신한다 — 이제 `real.py`가 «검색»과 «고르기»를
          나눠서 한다. 고르기는 AI가 하므로 여기서는 «검색 결과»만 준다.
        ⚠️ 이름·인자가 진짜(`core/naver_client.search_news`)와 달라지면
          실행 시점에 터진다. 여기서 맞춰 둔다.
        """
        if self.news_fails:
            raise RuntimeError("타임아웃")
        return []

    def collect_news(
        self, company: str, profile: dict[str, Any], homonym: int, steps: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """1판 방식(제목 일치). ★ `real.py`는 더 이상 이걸 부르지 않는다 (P-108).

        1판에 «아직 남아 있는» 함수라 모양만 유지한다.
        """
        steps.append({"step": "6_수집_뉴스", "채택": 0, "검색결과": 0})
        return []

    # ── 7 게이트 · 8·9 생성 · 10 검증 ────────────────────
    def establishment(self, rough: dict[str, bool]) -> tuple[bool, list[str]]:
        return True, []

    # ★ 8·9 생성은 이제 `spanselect.select_spans`가 맡는다 (P-43). `real.py`는
    #   `generate_and_check`를 더 이상 부르지 않는다. 아래 셋은 그 대체 경로가
    #   1판에서 «빌려 쓰는» 부품들이다 — 이름이 틀리면 실행 시점에 터진다.
    BLOCK_ORDER = ("1", "2", "3", "4-1", "4-2", "4-3", "5", "6", "7", "8", "9")
    GEN_MAX_TOKENS = 3000
    DraftItem = _FakeDraftItem

    def split_sentences(self, text: str) -> list[str]:
        """마침표로 자르는 간단판. 진짜 규칙(절단면 꼬리 제거 등)은 1판 것을 쓴다."""
        return [s.strip() for s in text.split(".") if s.strip()]

    def check_draft(
        self,
        items: list[_FakeDraftItem],
        originals: dict[int, str],
        requirements: list[str],
    ) -> SimpleNamespace:
        """W1~W4 원문 대조 흉내 — 여기서는 전부 통과시킨다.

        ★ 진짜 대조 규칙은 `spanselect/tests/`가 **1판 코드를 직접 불러** 검증한다.
          이 파일이 재는 것은 「캐시가 먹었나」이지 대조 정확도가 아니다.
        """
        return SimpleNamespace(kept=list(items), deleted=[])

    def substance_check(
        self, client: Any, kept: list[_FakeDraftItem], steps: list[dict[str, Any]]
    ) -> dict[str, bool]:
        client.messages.create(model=self.MODEL)
        self.generate_ai_calls += 1
        return {cell: True for cell in self.CELL_SOURCES}

    def cell_pattern_ok(self, kept: list[_FakeDraftItem]) -> dict[str, bool]:
        return {cell: True for cell in self.CELL_SOURCES}


# ══════════════════════════════════════════════════════════
# 준비물
# ══════════════════════════════════════════════════════════


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """진짜 엔진 대신 가짜를 끼운다 — 이 시험에서 돈이 나갈 길이 없다."""
    fake = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    return fake


def _card() -> CompanyCard:
    return CompanyCard(
        legal_name="가나다전자",
        typed_name="가나다전자",
        address="서울특별시 강남구 테헤란로 1",
        ceo="홍길동",
        founded="20000101",
        ref=CORP_ID,
    )


def _run(posting: str = POSTING, job: str = JOB, card: Optional[CompanyCard] = None):
    user_input = UserInput(company="가나다전자", job=job, region="서울 강남구", posting_text=posting)
    return real.RealPipeline().run(user_input, card or _card())


# ══════════════════════════════════════════════════════════
# 적중 — 같은 회사 · 같은 공고
# ══════════════════════════════════════════════════════════


def test_본조사_DART_회사정보오류는_거부가_아니라_AI전_기술실패다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.get_json

    def dart_error(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        if endpoint == "company.json":
            return {"status": "999", "message": "DART 한도 오류"}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", dart_error)

    result = _run()

    assert result.outcome is Outcome.FAILED
    assert result.outcome is not Outcome.REJECT_NO_DISCLOSURE
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


def test_본조사_DART_공시목록오류는_감사보고서없음으로_거부하지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.get_json

    def dart_error(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        if endpoint == "list.json":
            return {"status": "999", "message": "DART 인증 오류"}
        return original(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", dart_error)

    result = _run()

    assert result.outcome is Outcome.FAILED
    assert result.outcome is not Outcome.REJECT_NO_DISCLOSURE
    assert engine.client.messages.calls == 0
    assert result.cost_krw == 0


def test_같은_공고를_다시_조사하면_생성AI를_한_번도_안_부른다(engine: FakeEngine) -> None:
    """★ 이 시험이 캐시의 존재 이유다 — 두 번째 요청에서 «비싼 쪽»이 0이어야 한다."""
    first = _run()
    assert first.outcome is Outcome.REPORT
    calls_after_first = engine.generate_ai_calls
    assert calls_after_first > 0, "첫 조사에서는 생성 AI가 돌아야 한다(시험이 헛돈 것)"

    second = _run()

    assert second.outcome is Outcome.REPORT
    assert engine.generate_ai_calls == calls_after_first, (
        "두 번째 조사에서 생성·검증 AI가 또 나갔습니다 — 캐시가 안 먹었습니다."
    )


def test_캐시로_돌려준_보고서가_처음_만든_것과_같다(engine: FakeEngine) -> None:
    first = _run()
    second = _run()
    assert second.report == first.report


def test_캐시_적중은_할당량을_안_깎는다(engine: FakeEngine) -> None:
    """정본 00_공통/2_규칙/04_할당량.md — 「캐시 반환(1층 히트) → 0 · 무제한」."""
    first = _run()
    second = _run()
    assert first.charged is True
    assert second.charged is False


def test_캐시_적중이면_저장된_결과라고_밝힌다(engine: FakeEngine) -> None:
    """사용자가 「방금 새로 조사한 것」으로 오해하면 안 된다 (P-63 교훈)."""
    first = _run()
    second = _run()

    assert first.message == "", "새로 만든 요청에는 캐시 안내가 붙으면 안 된다"
    assert "이미 조사해 둔" in second.message
    assert first.report is not None
    assert first.report.generated_at in second.message, "언제 조사한 것인지 밝혀야 한다"


def test_적중해도_5_5_글자추출_비용은_남는다(engine: FakeEngine) -> None:
    """정본이 감수한 맞바꿈 — 지문은 5.5 뒤에야 나오므로 공고 판별 AI는 계속 나간다.

    「캐시 히트 = 완전 0원」이라고 잘못 알리면 비용 예측이 어긋난다.
    """
    _run()
    calls_after_first = engine.posting_ai_calls
    _run()
    assert engine.posting_ai_calls > calls_after_first


# ══════════════════════════════════════════════════════════
# 미적중 — 공고가 다르다 · 회사가 다르다 · 직무가 다르다
# ══════════════════════════════════════════════════════════


def test_공고가_다르면_남의_보고서를_돌려주지_않는다(engine: FakeEngine) -> None:
    """★ 정본 §★ — 회사·직무가 같아도 공고가 다르면 반드시 다시 만든다."""
    _run(posting=POSTING)
    calls_after_first = engine.generate_ai_calls

    other = _run(posting=OTHER_POSTING)

    assert other.outcome is Outcome.REPORT
    assert engine.generate_ai_calls > calls_after_first, (
        "다른 공고인데 캐시가 먹었습니다 — 남의 옛 공고 기반 보고서가 나갑니다."
    )
    assert other.charged is True
    assert other.message == ""


def test_회사가_다르면_캐시가_안_섞인다(engine: FakeEngine) -> None:
    _run()
    calls_after_first = engine.generate_ai_calls

    other_card = CompanyCard(
        legal_name="가나다전자",   # 이름은 같지만
        typed_name="가나다전자",
        address="부산광역시 해운대구",
        ceo="김철수",
        founded="20100101",
        ref="00999999",             # 고유번호가 다른 «다른 법인»
    )
    _run(card=other_card)

    assert engine.generate_ai_calls > calls_after_first


def test_직무가_다르면_다시_만든다(engine: FakeEngine) -> None:
    _run(job="백엔드 개발자")
    calls_after_first = engine.generate_ai_calls
    _run(job="영업 관리")
    assert engine.generate_ai_calls > calls_after_first


def test_직무_표기가_공백_대소문자만_달라도_같은_것으로_본다(engine: FakeEngine) -> None:
    """정규화 규칙(`cache.normalize_job`)이 파이프라인까지 이어지는지 본다."""
    _run(job="Backend Engineer")
    calls_after_first = engine.generate_ai_calls
    _run(job="  backend   engineer ")
    assert engine.generate_ai_calls == calls_after_first


def test_공고_문장_순서만_바뀐_것은_같은_공고로_본다(engine: FakeEngine) -> None:
    """지문은 정렬 뒤 해시다 — AI가 문장을 뽑는 순서는 매번 같다는 보장이 없다."""
    _run(posting="3년 이상 경력\n파이썬 실무 경험")
    calls_after_first = engine.generate_ai_calls
    _run(posting="파이썬 실무 경험\n3년 이상 경력")
    assert engine.generate_ai_calls == calls_after_first


# ══════════════════════════════════════════════════════════
# 만료 — O9 신선도 (정본 §2)
# ══════════════════════════════════════════════════════════


def test_사업연도가_바뀌면_저장된_보고서를_안_쓴다(engine: FakeEngine) -> None:
    """★ 없으면 「작년 보고서」가 영원히 나간다 (정본 §2가 막으려던 구멍)."""
    _run()
    calls_after_first = engine.generate_ai_calls

    engine.fiscal_years = [2026, 2025]   # 새 사업연도 자료가 올라왔다
    again = _run()

    assert engine.generate_ai_calls > calls_after_first, "만료된 보고서를 재사용했습니다"
    assert again.charged is True


def test_재무API가_빈손이어도_공시_이름으로_캐시가_적중한다(engine: FakeEngine) -> None:
    """★ 비상장 외감 회사의 실제 모습이다.

    1판 실측 28건 중 13건(전부 비상장 외감)은 재무 API가 사업연도를 못 줬다.
    재무 API만 보면 그 회사들은 캐시가 **영영 적중하지 않는다.**
    """
    engine.fiscal_years = []             # 재무 API 빈손
    engine.filing_year = 2025            # 감사보고서 (2025.12)는 있다
    _run()
    calls_after_first = engine.generate_ai_calls

    _run()

    assert engine.generate_ai_calls == calls_after_first


def test_공시_사업연도가_바뀌면_만료된다(engine: FakeEngine) -> None:
    """재무 API가 빈손인 회사도 새 감사보고서가 올라오면 다시 만들어야 한다."""
    engine.fiscal_years = []
    engine.filing_year = 2025
    _run()
    calls_after_first = engine.generate_ai_calls

    engine.filing_year = 2026            # 새 결산 보고서가 올라왔다
    _run()

    assert engine.generate_ai_calls > calls_after_first


def test_사업연도를_아예_모르면_신선하다고_우기지_않는다(engine: FakeEngine) -> None:
    """재무 API도 공시 이름도 연도를 안 주면 «모르는 상태»다 — 다시 만든다(보수적)."""
    _run()
    calls_after_first = engine.generate_ai_calls

    engine.fiscal_years = []
    engine.filing_year = None            # 이름에 결산 기간이 안 적힌 공시
    _run()

    assert engine.generate_ai_calls > calls_after_first


def test_최신_사업연도는_추측하지_않고_전자공시가_답한_값을_쓴다() -> None:
    """1~3월에는 작년 자료가 아직 없는 것이 정상이다 — `올해-1`로 추측하면 안 된다."""
    filing = {"report_nm": "감사보고서 (2025.12)"}
    assert real._current_fiscal_year([2024, 2023], None) == 2024
    assert real._current_fiscal_year([], filing) == 2025
    # 한쪽이 옛 연도에 멈춰 있어도 다른 쪽이 새 자료를 알아채면 만료되게 «더 최신»을 쓴다.
    assert real._current_fiscal_year([2023], filing) == 2025
    assert real._current_fiscal_year([], {"report_nm": "감사보고서"}) is None
    assert real._current_fiscal_year([], None) is None


def test_공시_사업연도는_접수일이_아니라_결산연도를_읽는다() -> None:
    """접수일(2026-03 제출)로 읽으면 「사업연도가 바뀌었다」를 못 가른다."""
    filing = {"report_nm": "감사보고서 (2025.12)", "rcept_dt": "20260315"}
    assert real._filing_fiscal_year(filing) == 2025


# ══════════════════════════════════════════════════════════
# 캐시가 깨졌을 때 — 조용히 원래 길로 (AC 4)
# ══════════════════════════════════════════════════════════


def test_캐시_조회가_실패해도_조사는_끝까지_간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("저장소가 잠겼습니다")

    monkeypatch.setattr(real.cache_store, "get_layer1_hit", boom)
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None


def test_캐시_저장이_실패해도_보고서는_나간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("디스크가 가득 찼습니다")

    monkeypatch.setattr(real.cache_store, "save_layer1", boom)
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.charged is True


def test_DB를_아예_못_열어도_조사는_끝까지_간다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """저장소 파일 자체가 없거나 권한이 없는 경우 — 조사는 계속돼야 한다."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("저장소를 열 수 없습니다")

    monkeypatch.setattr(real.storage_db, "connect", boom)
    result = _run()

    assert result.outcome is Outcome.REPORT
    assert result.report is not None


# ══════════════════════════════════════════════════════════
# 저장 규칙 — S2 · 키 일치
# ══════════════════════════════════════════════════════════


def test_보고서를_만들면_1층_캐시에_한_줄이_남는다(engine: FakeEngine) -> None:
    """저장이 «실제로» 됐는지 DB를 직접 열어 확인한다 (되는 줄 알았는데 안 되는 사고 방지)."""
    from src.features.storage import db as storage_db

    _run()

    with storage_db.connect() as conn:
        rows = conn.execute(
            "SELECT corp_id, job_key, fiscal_year FROM layer1_cache"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["corp_id"] == CORP_ID
    assert rows[0]["fiscal_year"] == max(FISCAL_YEARS)


def test_수집_실패가_끼면_캐시에_저장하지_않는다(engine: FakeEngine) -> None:
    """★ 정본 03_수집/1_흐름/02_실패처리.md — 「⚠️ 못 가져옴 → ❌ 저장 안 함」.

    그날만 죽은 소스 때문에 그 회사가 「자료 없는 회사」로 굳어버리면 안 된다.
    """
    from src.features.storage import db as storage_db

    engine.news_fails = True
    first = _run()
    calls_after_first = engine.generate_ai_calls

    with storage_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM layer1_cache").fetchone()["n"]

    second = _run()

    assert first.outcome is Outcome.REPORT      # 보고서 자체는 나간다
    assert count == 0, "우리 쪽 실패가 낀 결과를 캐시에 저장했습니다"
    assert engine.generate_ai_calls > calls_after_first  # 다시 시도하면 새로 만든다
    assert second.message == ""


def test_자료가_없는_것은_실패가_아니므로_캐시한다(engine: FakeEngine) -> None:
    """❌ 없음(회사의 사실)과 ⚠️ 못 가져옴(우리 실패)을 섞으면 캐시가 영영 안 찬다."""
    engine.news_fails = False       # 뉴스 0건 = ❌ 없음
    _run()
    calls_after_first = engine.generate_ai_calls
    _run()
    assert engine.generate_ai_calls == calls_after_first


def test_공고_원문은_저장소에_들어가지_않는다(engine: FakeEngine) -> None:
    """S2 = 0건. 캐시에 남는 것은 요구역량 목록과 지문뿐이다."""
    from src.features.storage import db as storage_db

    secret = "지원자 홍길동 010-1234-5678"
    _run(posting=f"{POSTING}\n{secret}")

    with storage_db.connect() as conn:
        dump = "\n".join(
            str(row[0])
            for row in conn.execute("SELECT payload_json FROM reports").fetchall()
        )
    assert "3년 이상 경력" in dump, "보고서가 저장되지 않았다면 이 시험은 아무것도 안 본 것이다"
    assert "010-1234-5678" not in dump


def test_캐시_없이는_매번_생성AI가_돈다(engine: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 위 시험들이 «캐시 덕분에» 통과한 것인지 확인하는 대조군.

    캐시 조회를 항상 미적중으로 만들면 두 번째 요청에서도 생성 AI가 돌아야 한다.
    안 돈다면 위 시험들은 캐시가 아니라 다른 이유로 통과한 것이다.
    """
    monkeypatch.setattr(real.cache_store, "get_layer1_hit", lambda *a, **k: None)
    _run()
    calls_after_first = engine.generate_ai_calls
    _run()
    assert engine.generate_ai_calls > calls_after_first

"""★ 알맹이(파이프라인)를 «붙일 자리».

화면·실제 조사·데모·내보내기가 함께 쓰는 데이터 계약이다.
canonical(v4) 보고서는 의미 기반 섹션 ID, 원자 사실 장부, 검증된 출처와 기간
메타데이터를 이 모양으로 전달한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Protocol

if TYPE_CHECKING:
    from src.features.cost_tracking.store import AiCostEvent
else:
    # 런타임에는 feature 경계를 넘는 import를 만들지 않되 introspection은 닫힌다.
    AiCostEvent = Any

from src.core.constants import COUNTED_CELLS
from src.shared.report_generation.models import (
    GenerationProducerEvidence,
    GenerationRunMetrics,
)
from src.shared.report_generation.public_projection import PublicReportProjection
from src.shared.report_quality.generation import GenerationQualityObservation
from src.shared.span_selection_diagnostics import SpanSelectionRoundDiagnostic

#: 진행 상황을 알리는 함수. 단계 키 하나를 받아 「이 단계를 시작했다」를 뜻한다.
#: 키 목록은 `core/constants.py`의 PROGRESS_STEPS가 정본이다.
StepReporter = Callable[[str], None]


class Outcome(str, Enum):
    """요청이 어떻게 끝났는가.

    정본: 확정/00_공통/1_흐름/01_전체흐름.md
    사람이 떨어지는 지점 6개 + 성공 1개.
    """

    #: 보고서가 나갔다 (완성·부분 완성·미완성 전부 포함)
    REPORT = "보고서"
    #: 회사를 못 찾았다 — 「대상 아님」이 아니다
    NOT_FOUND = "회사_못찾음"
    #: 공공기관이라 다루지 않는다
    REJECT_PUBLIC = "거부_공공기관"
    #: 공시 자료가 없어 근거를 못 모은다
    REJECT_NO_DISCLOSURE = "거부_공시없음"
    #: 올린 것이 채용공고가 아니다
    POSTING_DISCARDED = "공고_폐기"
    #: 출고 전 자동 게이트가 멈췄다 — 자료 부족만이 아니라 응답 절단·검증 거절·
    #: 비교 중단도 포함한다. 실제 사유는 ``final_gate_reason``이 구분한다.
    #: 과거 저장본(checkpoint/SQLite)에는 옛 이름 "자료부족_중단"으로 남아 있으며
    #: 읽는 쪽이 두 이름을 모두 인식한다.
    GATE_STOPPED = "게이트_중단"
    #: 만들다 실패했다 — 항상 결함
    FAILED = "생성_실패"


def outcome_for(raw: str, table: Mapping[str, Outcome]) -> Outcome:
    """종료 문자열을 «화면 종류»로 옮긴다 — 앞부분 맞추기.

    ★ 왜 이 함수가 여기 있나 (적대 검수 권고)
      ─────────────────────────────────────────────────────────
      진짜 파이프라인(`real.py`)과 데모(`demo.py`)가 **같은 뜻을 다른 방법으로**
      옮기고 있었다. 데모는 앞부분 맞추기, 진짜는 정확일치였다.
      그 차이가 운영 결함을 만들었다 — 판정이 내놓는 값이 「거부A」에서
      「거부A_공공기관」으로 길어지자 정확일치가 못 찾아 기본값으로 떨어졌고,
      **공공기관이 「공개된 재무 자료가 없습니다」 화면**을 봤다.
      6개월간 아무도 몰랐고, 데모 쪽은 멀쩡했다.

    ★ 왜 «표»는 안 합치나
      두 표의 열쇠는 애초에 다른 생산자가 낸다 — `real` 쪽은 1판 엔진의
      `fin(...)` 이름을, `demo` 쪽은 데모가 스스로 쓰는 종료 문자열을 옮긴다.
      겹치는 열쇠는 절반뿐이라, 하나로 합치면 「어느 쪽이 무엇을 내는지」를
      읽는 사람이 알 수 없게 된다. **갈라져 있던 것은 표가 아니라 규칙이었다.**

    ★ 앞부분 맞추기인 이유
      엔진이 내는 이름은 「거부_거부A」처럼 «부류»로 시작하고 뒤에 사유가 붙는다.
      사유가 늘어나도 부류는 그대로이므로 앞부분으로 잡는 것이 맞다.

    ★ 못 찾으면 「실패」다 — 「자료 없음」으로 접지 않는다.
      모르는 것을 아는 것처럼 말하는 화면이 바로 위 결함의 정체였다.

    Args:
        raw: 엔진·데모가 내놓은 종료 문자열 (예: "거부_거부A_공공기관").
        table: 그 문자열의 «앞부분» → 화면 종류 표. 부르는 쪽이 자기 것을 넘긴다.

    Returns:
        맞는 화면 종류. 아무 열쇠와도 안 맞으면 ``Outcome.FAILED``.
    """
    for prefix, outcome in table.items():
        if raw.startswith(prefix):
            return outcome
    return Outcome.FAILED


class Grade(str, Enum):
    """보고서를 얼마나 채웠는가.

    정본: 확정/06_검증/1_흐름/02_성립판정과부분보고서.md
    과거 저장본 호환을 위해 세 값을 유지한다. canonical(v4)는 검증된 1~8장과
    조건부 9장까지 있으면 ``COMPLETE``, 9장만 빠지면 ``PARTIAL``로 출고한다.
    """

    COMPLETE = "완성"
    PARTIAL = "부분 완성"
    INCOMPLETE = "미완성"


@dataclass(frozen=True)
class UserInput:
    """사용자가 입력 화면에서 넣은 것.

    정본: 확정/01_식별/1_흐름/01_회사확정.md §입력 항목
    """

    company: str
    job: str
    #: 시/도 + 구/군까지만. 동명 법인 11.3%를 가르는 유일한 입력이다.
    region: str
    #: 붙여넣은 공고 텍스트. 이미지 경로는 2단계에서 붙인다.
    posting_text: str = ""


@dataclass(frozen=True)
class CompanyCard:
    """확인 카드에 보여줄 회사 하나.

    ★ 이 화면이 「AI가 실재하는 다른 회사를 답하는 것」의 유일한 방어선이다.
      코드로는 못 막는다. 그래서 사람에게 반드시 확인받는다.

    정본: 확정/01_식별/2_규칙/01_이름대조.md §확인 카드
    """

    #: 정식 법인명
    legal_name: str
    #: 사용자가 입력한 이름. 「11번가」를 넣었는데 「십일번가」만 보이면 오거부가 된다.
    typed_name: str
    address: str
    ceo: str
    #: YYYYMMDD 또는 사람이 읽는 형태
    founded: str
    homepage: str = ""
    #: 실제로 «열리는» 주소 (P-114). 못 열면 빈 문자열이고 화면은 링크를 안 건다.
    #: ★ `homepage`는 공시에 적힌 글자 그대로, 이것은 브라우저가 열 수 있는 모양이다.
    homepage_url: str = ""
    #: 주소가 안 맞을 때 띄울 경고. 차단하지 않는다.
    region_warning: str = ""
    #: 공고 속 회사명이 다를 때 띄울 경고. 차단하지 않는다.
    posting_warning: str = ""
    #: 같은 이름 회사가 몇 곳인지. 2 이상이면 화면에 개수를 알린다.
    same_name_count: int = 1
    #: 알맹이가 이 회사를 다시 찾을 때 쓰는 내부 열쇠 (예: 전자공시 고유번호).
    #: ★ 화면은 쓰지 않는다. 확인 카드에서 [맞습니다]를 누른 뒤 같은 회사를
    #:   다시 찾느라 AI를 또 부르는 낭비를 막으려고 들고 다닌다.
    ref: str = ""

    @property
    def founded_display(self) -> str:
        """설립일을 사람이 읽는 형태로 바꾼다."""
        raw = self.founded.strip()
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}년 {int(raw[4:6])}월 {int(raw[6:])}일"
        return raw


@dataclass(frozen=True)
class CompanyLookupResult:
    """회사 확인 카드와 그 카드를 찾는 데 실제로 쓴 AI 비용.

    ★ 확인 카드는 본조사보다 먼저 만들어진다. 비용을 카드 안에 넣어 브라우저로
      왕복시키면 사용자가 값을 바꿀 수 있으므로, 웹 서버가 따로 들고 있을 모양으로
      분리한다. 데모·층1 이름 대조처럼 AI를 안 부르면 비용은 0원이다.
    """

    card: Optional[CompanyCard]
    cost_krw: float = 0.0
    model: str = ""
    #: 내부 오류로 카드를 못 만들었는가. False+card 없음은 실제 「회사 못 찾음」이다.
    failed: bool = False
    #: API 예외 때문에 과금 여부를 확정할 수 없는가. True면 비용 표식을 닫으면 안 된다.
    billing_uncertain: bool = False
    #: 원문 없이 stage/model/token/cache/batch/원가만 담은 내부 비용 이벤트.
    ai_cost_events: tuple["AiCostEvent", ...] = ()


@dataclass(frozen=True)
class ReportTable:
    """숫자 표 하나.

    ★ 재무·회계 수치는 «문장으로 바꾸지 않고 표 그대로» 낸다 (결정기록 D13).
      숫자를 억지로 문장으로 만들면 읽기만 나빠진다.
    """

    #: 표 위에 붙는 설명 (예: 「전자공시 주요 재무계정」)
    caption: str
    #: 열 이름
    headers: list[str]
    #: 행. 각 행의 길이는 headers와 같아야 한다.
    rows: list[list[str]]
    #: 어디서 왔는지 (예: 「전자공시 재무 API」)
    cite: str = ""
    #: 첫 열을 뺀 나머지가 «숫자»인가.
    #: 숫자면 오른쪽 정렬 + 줄바꿈 금지, 글자면 왼쪽 정렬 + 줄바꿈 허용.
    #: ★ 이걸 안 나누면 글자 표에도 「줄바꿈 금지」가 걸려 첫 열이 한 글자 폭으로 찌그러진다.
    numeric: bool = False
    #: 공개 표에는 넣지 않는 검증용 원값 행. 있으면 ``rows``와 같은 크기여야 한다.
    raw_rows: list[list[str]] = field(default_factory=list)
    #: 원값을 공개 표시값으로 바꿀 때 나눈 수와 소수 자릿수.
    scale_divisor: str = ""
    scale_places: int = 0
    #: 표 캡션에 명시할 공개 단위(예: ``억원``).
    display_unit: str = ""
    #: 수집기가 받은 실제 원문 payload. 공개 렌더링·보고서 직렬화에는 넣지 않고,
    #: 조립 순간 각 공개 행의 FactRecord.state_evidence를 만드는 데만 쓴다.
    #: 행마다 하나씩 있어야 하며 공개 ``rows``를 다시 이어 붙여 만든 문자열은
    #: 원문 payload로 인정하지 않는다.
    evidence_rows: list[str] = field(default_factory=list, repr=False, compare=False)
    #: 공개 채널의 권장 표현. 사실 원장과 행은 그대로 두고 렌더러만 바꾼다.
    #: ``table``(기본)·``composition``(100% 구성비)·``trend``(시계열)·
    #: ``flow``(행마다 왼쪽→오른쪽 흐름)를 지원한다. 렌더러가 안전하게 해석하지
    #: 못하는 값이나 데이터는 표로 되돌아가며, 그래프와 표를 동시에 반복하지 않는다.
    #: 기존 positional 생성자의 ``evidence_rows`` 위치를 바꾸지 않도록 맨 뒤에 둔다.
    presentation: str = "table"
    #: 구조화 수치 claim이 표 제목을 추측하지 않고 읽는 원자료 이름표.
    #: 값이 없으면 파생 claim을 만들지 않는다.
    entity_scope: str = ""
    raw_unit: str = ""
    unit_dimension: str = ""
    #: 대표 cite와 별도로 표의 모든 행이 실제로 사용한 공개 출처 번호.
    #: flow처럼 행마다 출처가 다른 구조에서 부분 집합 누락을 막는다.
    source_cites: list[str] = field(default_factory=list)
    #: 검증된 pre-render manifest 안의 이 표 항목을 가리키는 SHA-256 참조.
    #: FULL 새 생성물은 비어 있으면 출고·재로드 모두 fail-closed 된다.
    manifest_ref: str = ""
    #: 원문 직접 결속 대신 검증된 프로그램 사실로 주입된 행의 fact id.
    #: 비어 있으면 각 행은 evidence_rows와 source_cites로 직접 결속돼야 한다.
    row_fact_ids: list[str] = field(default_factory=list)
    #: 저장·cache에는 원문 evidence_rows 대신 이 행별 exact hash만 남긴다.
    row_evidence_refs: list[str] = field(default_factory=list)
    #: manifest의 typed 행 결속을 가리키는 행별 canonical SHA-256.
    row_binding_refs: list[str] = field(default_factory=list)
    #: 머리글·열 위치·자료형·원값 필드까지 잠근 셀별 canonical SHA-256.
    cell_binding_refs: list[list[str]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """열 개수가 안 맞는 표는 화면을 깨뜨리므로 내보내지 않는다."""
        width = len(self.headers)
        raw_valid = not self.raw_rows or (
            len(self.raw_rows) == len(self.rows)
            and all(len(row) == width for row in self.raw_rows)
        )
        evidence_valid = not self.evidence_rows or (
            len(self.evidence_rows) == len(self.rows)
            and all(str(value).strip() for value in self.evidence_rows)
        )
        return (
            bool(self.rows)
            and width > 0
            and all(len(r) == width for r in self.rows)
            and raw_valid
            and evidence_valid
        )


@dataclass(frozen=True)
class ReportSection:
    """보고서의 항목 하나 (블록 1개)."""

    #: canonical(v4)는 의미 ID, 과거 저장본은 "1", "4-1" 같은 칸 번호
    cell: str
    title: str
    #: 내부 감사용 근거 원문. 공개 렌더러는 이 목록을 반복 출력하지 않는다.
    lines: list[tuple[str, str]] = field(default_factory=list)
    #: 비었을 때 「왜 비었는지」. 프로그램이 붙인다 (AI 아님).
    empty_reason: str = ""
    #: 숫자 표. 문장 대신 이걸로 채울 수 있다 (D13).
    tables: list[ReportTable] = field(default_factory=list)
    #: 작가와 독립 검토를 통과한 공개 문장 및 실제 출처 표기.
    #: canonical 렌더러는 이것과 표만 표시하며 원문 ``lines``로 대체하지 않는다.
    prose_lines: list[tuple[str, str]] = field(default_factory=list)
    #: 화면·PDF가 «문단»을 만드는 단위. 한 항목이 한 문단이다.
    #: ★ 왜 prose_lines와 따로 두나 — prose_lines는 «문장» 단위이고 출고
    #:   검증·저장이 그 단위를 쓴다. 표시용 묶음을 같은 필드에 섞으면
    #:   그 계약이 깨진다. 비어 있으면 소비하는 쪽이 예전처럼 prose_lines를
    #:   이어 붙인다(뒤로 호환).
    prose_paragraphs: list[str] = field(default_factory=list)
    #: 회사 사실과 분리해 보여 주는 프로그램 작성 안내·준비 질문.
    #: 출처가 있는 사실이 아니므로 ``lines``/``prose_lines``에 섞지 않는다.
    #: 옛 저장 payload에는 이 키가 없으며 빈 목록으로 읽는다.
    guidance_lines: list[str] = field(default_factory=list)
    #: canonical(v4)에서는 ``cell``이 숫자가 아니라 semantic section ID다.
    #: 화면 번호와 내부 ID를 분리해야 레거시 5·6·7·8 정규화와 충돌하지 않는다.
    display_number: str = ""
    #: 시간 장에만 붙는 표시 태그. 예: ``#과거``·``#현재``·``#미래``.
    tag: str = ""
    #: 이 섹션이 표시하는 잠긴 사실 장부의 ID. canonical 출력의 근거 단위다.
    fact_ids: list[str] = field(default_factory=list)

    @property
    def is_filled(self) -> bool:
        """근거 원문이나 표가 있으면 채워진 것이다 — 표시용 글은 등급에 안 센다."""
        return bool(self.lines) or bool(self.tables) or bool(self.fact_ids)


@dataclass(frozen=True)
class SourceStatus:
    """소스별 수집 결과 한 줄.

    ⭕ 찾음 / ❌ 없음 / ⚠️ 못 가져옴 — 셋을 섞으면 오거부가 된다.
    정본: 확정/03_수집/1_흐름/02_실패처리.md
    """

    name: str
    #: "ok" | "none" | "failed"
    state: str
    detail: str = ""


@dataclass(frozen=True)
class SummaryItem:
    """0장 핵심 요약 한 항목.

    요약은 새 사실을 소유하지 않으며 검증 완료 본문 문장과 관련 장만 가리킨다.
    """

    text: str = ""
    section_id: str = ""
    #: 요약 결론을 직접 뒷받침하는 원자 사실. 장 번호만 연결하는 것은 금지한다.
    fact_ids: list[str] = field(default_factory=list)
    #: ``fact_ids``의 ``FactRecord.claim``을 정해진 형식으로 잠근 근거 묶음.
    evidence_text: str = ""
    #: 독립 검수 완료 본문을 글자 그대로 재사용했음을 나타내는 상태. Reviewer 통과
    #: 또는 원문 완전일치 코드 검증을 상속한 canonical 공개본만 허용한다.
    verification_status: str = ""
    #: 요약문·근거 묶음·검증 판정을 함께 잠그는 SHA-256 지문.
    verification_binding: str = ""
    #: 요약문과 근거 claim 양쪽에 실제로 나타나는 핵심 근거어.
    support_terms: list[str] = field(default_factory=list)

    @property
    def related_section_id(self) -> str:
        """문서 용어와 맞춘 읽기 전용 별칭."""

        return self.section_id


@dataclass(frozen=True)
class FactRecord:
    """canonical 보고서의 원자화된 사실 한 건.

    문자열 기본값은 기존 생성자와 단계적 마이그레이션을 깨지 않기 위한 것이다.
    출고 게이트는 필요한 필드가 빈 레코드를 근거로 인정하지 않는다.
    """

    fact_id: str = ""
    legal_entity: str = ""
    subject_scope: str = ""
    relationship_or_action: str = ""
    claim: str = ""
    claim_type: str = ""
    section_owner: str = ""
    time_state: str = ""
    as_of: str = ""
    source_id: str = ""
    source_type: str = ""
    source_title: str = ""
    source_publisher: str = ""
    source_host: str = ""
    source_url: str = ""
    source_document_id: str = ""
    location: str = ""
    #: 검증 상태. ``verified``·``partial``·``insufficient`` 중 하나.
    #: 확정 본문에는 verified만 들어갈 수 있다.
    status: str = ""
    #: 사실 자체의 상태. 검증 상태와 분리한다.
    #: actual|provisional|planned|estimated|scope_undisclosed
    fact_status: str = ""
    #: 원문 대조 판정. canonical 공개본은 verified만 허용한다.
    verification_status: str = ""
    state_evidence: str = ""
    #: 원문 발행·공시·확인일. 사실의 기준시점 ``as_of``와 섞지 않는다.
    source_date: str = ""
    #: claim과 원문 근거 양쪽에 나타나는 핵심 근거어. 최소 두 개가 필요하다.
    evidence_support_terms: list[str] = field(default_factory=list)
    #: claim·state_evidence·출처·시점·구조 필드를 함께 잠그는 SHA-256 지문.
    evidence_binding: str = ""
    raw_value: str = ""
    calculation: str = ""
    display_value: str = ""
    rounding_rule: str = ""
    #: ``원시값|나눗수|소수자리|표시값`` 형식의 결정론적 수치 검산식.
    numeric_checks: list[str] = field(default_factory=list)
    #: 4장의 완료 사업연도 실적에만 쓰는 연도. 그 밖의 사실은 0이다.
    fiscal_year: int = 0
    #: 완료 실행이 실제로 일어난 날짜 또는 원문에 적힌 연도(YYYY / YYYY-MM-DD).
    #: 원문 발표일·수집일과 섞지 않는다.
    event_date: str = ""
    #: 초기 v3 저장본의 고객·지역 우선순위. 새 보고서는 쓰지 않으며
    #: ``market_stage``와 ``market_observation``으로 의미를 분리한다.
    market_priority: str = ""
    #: 핵심·성장·진입 중 공식 전략과 실행 근거가 함께 있을 때만 붙이는 단계.
    market_stage: str = ""
    #: 원문에서 직접 확인한 고객·지역·산업·용도·채널 상태. 단순 매출 발생은
    #: 여기까지만 보존하고 시장 단계로 승격하지 않는다.
    market_observation: str = ""
    #: 3장의 제품·서비스가 사업에서 맡는 근거 기반 기능적 역할.
    product_role: str = ""
    #: 신규·성장·주력·안정 중 근거가 충분할 때만 붙이는 보고서 선택 단계.
    portfolio_stage: str = ""
    #: 3장 제품이 참조하는 2장 ``revenue_model`` 원자 사실 ID.
    revenue_model_fact_id: str = ""
    #: 원문에서 각각 확인된 현재 중점 추진 신호(출시·운영, 투자·증설 등).
    priority_signals: list[str] = field(default_factory=list)
    #: 4장의 변화 해석이 직접 참조하는 완료 실행·실적 fact_id.
    basis_fact_ids: list[str] = field(default_factory=list)
    #: 5장의 실제 대응이 연결되는 미해결 문제 fact_id.
    response_to_fact_id: str = ""
    #: 5장에서 원문에 직접 적힌 대응 행동 발췌. 초기 진척·효과와 분리한다.
    response_action: str = ""
    #: 5장 대응 착수 뒤 원문에서 별도로 확인된 초기 진척·결과. 대응 행동 자체를
    #: 같은 문구로 반복하지 않으며, 없으면 효과 미확인으로 남긴다.
    initial_signal: str = ""
    #: 5장 문제의 해결 여부를 다음 공식 자료에서 확인할 객관적 지표명.
    next_check_metric: str = ""
    #: 6장 미실행 계획의 공개 상태. announced|approved|conditional
    plan_status: str = ""
    #: 원문에 직접 적힌 예정 시점. 없으면 일정 미공개로 표시한다.
    plan_timing: str = ""
    #: 원문에 직접 적힌 인허·수요·자금·파트너 등 선행 조건.
    plan_condition: str = ""
    #: 회사가 원문에서 직접 제시한 예상 효과. 작성자 추정은 넣지 않는다.
    plan_expected_effect: str = ""
    #: 향후 실제 실행 여부를 확인할 원문 기반 사건·행동 신호.
    plan_execution_signal: str = ""
    #: 7장의 기획·개발·조달·생산·유통·판매·사후서비스 단계 코드.
    value_chain_stage: str = ""
    #: 7장의 내부운영·종속회사·공급·유통·라이선스·공동사업 등 관계 코드.
    relationship_type: str = ""
    #: canonical 이름. ``limitation``은 초기 v3 초안과의 저장 호환용이다.
    limitations: str = ""
    limitation: str = ""
    #: 인과 동사를 쓸 수 있다고 원문이 직접 뒷받침했는가.
    supports_causality: bool = False
    #: 인과 주장을 허용할 때 필요한 구조화된 네 필드.
    causal_subject: str = ""
    causal_mechanism: str = ""
    causal_outcome: str = ""
    causal_evidence: str = ""
    #: 9장 동일 조건 비교에 필요한 별도 축.
    comparison_target: str = ""
    comparison_metric: str = ""
    comparison_definition: str = ""
    comparison_basis: str = ""
    comparison_period: str = ""
    comparison_scope: str = ""
    #: 동일 조건 원수치로 비교 조립기가 확정한 공개 판정.
    #: competitive_advantage|operating_characteristic
    comparison_judgment: str = ""
    comparator_source_id: str = ""
    #: 비교사 원문에서 직접 보존한 별도 근거 조각과 공통 근거어.
    comparator_state_evidence: str = ""
    comparator_evidence_support_terms: list[str] = field(default_factory=list)
    #: 비교 조건을 양사별로 분리한 닫힌 구조. 고객·제품·시장과 양쪽의 기간·
    #: 지표 정의·회계 범위가 모두 같음을 게이트가 직접 검산한다.
    comparison_conditions: dict[str, str] = field(default_factory=dict)
    #: 새 생성 품질 계약의 구조화 claim 이름표. 모두 additive이며, 빈 값인
    #: 과거 JSON은 예전과 같은 FactRecord로 복원된다.
    claim_slot: str = ""
    metric: str = ""
    period_start: str = ""
    period_end: str = ""
    sign: str = ""
    unit: str = ""
    unit_dimension: str = ""
    formula: str = ""
    #: 한 문장을 함께 뒷받침한 모든 조각 출처. 첫 항목은 ``source_id``와
    #: 같아야 한다. 여러 자료를 본 문장을 첫 자료 하나에만 묶어 나머지
    #: 근거가 바뀌어도 검사를 통과하는 일을 막는 additive v2 계약이다.
    supporting_source_ids: list[str] = field(default_factory=list)
    #: ``supporting_source_ids``와 같은 순서의 독립 문서 identity.
    supporting_source_identities: list[str] = field(default_factory=list)
    #: 같은 순서의 정확한 원문 조각 SHA-256. 문서 주소만 같고 실제 인용
    #: 범위가 바뀐 경우도 결속 지문이 달라지게 한다.
    supporting_evidence_hashes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Report:
    """완성된 보고서 하나."""

    company: str
    job: str
    #: "상장사" | "비상장 외감"
    corp_type: str
    grade: Grade
    sections: list[ReportSection]
    #: 요구역량 목록 — 공고에서 뽑은 원문 문장 그대로
    requirements: list[str] = field(default_factory=list)
    #: 소스별 수집 현황 (⭕ 찾음 / ❌ 없음 / ⚠️ 못 가져옴)
    sources: list[SourceStatus] = field(default_factory=list)
    #: 맨 아래 출처 목록. 본문 문장 뒤 번호가 여기를 가리킨다.
    #: ★ 위 `sources`와 다른 것이다 — 저건 「어느 소스가 성공했나」, 이건 「이 문장이 어디서 왔나」.
    #:   타입은 `features/provenance/sources.py`의 `Source`. 순환 참조를 피해 여기서는 열어 둔다.
    citations: list[object] = field(default_factory=list)
    #: 칸별 채움 여부
    cells: dict[str, bool] = field(default_factory=dict)
    #: 왜 성립하지 못했는지 (부분·미완성일 때만)
    shortfall_reasons: list[str] = field(default_factory=list)
    generated_at: str = ""
    #: 빈 문자열은 기존(v2 이하) payload다. canonical 보고서는 정본 버전을 명시한다.
    schema_version: str = ""
    #: 본문을 모두 확정한 뒤 글자 변경 없이 재사용하는 핵심 요약 3~5개.
    summary_items: list[SummaryItem] = field(default_factory=list)
    #: 최종 문장·표·도식이 참조하는 잠긴 사실 장부.
    fact_records: list[FactRecord] = field(default_factory=list)
    #: **엔진 v2 전용** — 부록 번호(문자열) → 그 번호를 근거로 쓴 문장들의 등급 목록.
    #: 예: ``{"1": ["확인", "해석"], "11": ["확인"]}``
    #:
    #: ★ 왜 필요한가 (적대 검수가 재현한 결함) — 부록의 「사실 검증」
    #:   칸은 「이 자료에서 온 문장이 전부 «확인»인가」를 물어야 하는데, v2는
    #:   등급을 «화면 글자»(문장 끝 " — 해석" 표지)로만 내보낸다. 그런데
    #:   ``render.py`` 의 절충안 인용 규칙상 «해석» 문장의 ``[n]`` 은 숨겨지고,
    #:   같은 번호를 «확인» 문장이 이미 보여주고 있으면 끝까지 숨은 채로 남는다.
    #:   그래서 화면 글자만 되짚으면 「해석도 이 자료를 썼다」는 사실을 못 보고
    #:   「사실 검증 완료」로 넘어간다 — 실제로 재현된 오표시다.
    #:   ★ 「사실 언급 → 그 사실의 해석」은 이 엔진이 기본으로 만드는 문장 배열이라
    #:     (``render.py`` 의 문단 묶기 주석) 드문 일이 아니다.
    #:
    #: ★ 왜 «화면 글자»가 아니라 여기로 나르나 — 등급은 «사실»이고 인용 번호를
    #:   보일지 말지는 «표시 방식»이다. 표시 방식이 사실을 가리면 안 된다.
    #:   번호를 보였는지와 무관하게 render가 아는 값을 그대로 싣는다.
    #:
    #: v1(canonical) 보고서는 ``fact_records`` 로 같은 일을 하므로 비워 둔다.
    #: 옛 저장본에도 이 키가 없으며 빈 사전으로 읽힌다(뒤로 호환).
    source_grades: dict[str, list[str]] = field(default_factory=dict)
    #: 보고서 전체 사실을 판정한 기준일(ISO 날짜 권장).
    as_of_date: str = ""
    #: 과거 분석 범위. 예: ``2023~2025 완료 회계연도``.
    analysis_period: str = ""
    #: 가장 최신 실적의 기간·확정 상태. 예: ``2026년 2분기 잠정``.
    latest_performance_period: str = ""
    #: 새 생성 시점에 적용한 품질 계약과 안전 판정. 빈 값은 이 필드가 생기기 전
    #: 과거 보고서이며, 조회 때 현재 계약으로 소급 판정하지 않는다.
    quality_contract_version: str = ""
    safety_decision: str = ""
    #: 안전 판정과 실제 공개 행동을 분리한 정책 ID. ``legacy-shadow-exception``은
    #: 안전 통과를 뜻하지 않으며 화면·PDF가 그 사실을 반드시 알려야 한다.
    publication_policy: str = ""
    #: FULL 새 생성물의 공개 section/table/row/cell 구조를 pre-render 입력에
    #: 봉인한 canonical JSON. SHADOW·과거 저장본은 빈 문자열을 유지한다.
    public_structure_manifest: str = ""
    #: 표시 이름이 아닌 DART gen8 법인 식별자. FULL 생성물만 필수다.
    company_id: str = ""
    #: 새 생성 운영 모드. 빈 값은 이 필드가 생기기 전 SHADOW/과거 결과다.
    release_mode: str = ""
    #: 실제 평가·packet·content·AI ledger를 운반하는 비권위 생산 증거.
    #: 공개·차감 권한은 없으며 shared release receipt가 이 값을 소비한다.
    generation_evidence: GenerationProducerEvidence | None = None
    #: cache·재시작 뒤에도 생성 지표를 0으로 꾸미지 않기 위한 원 실행값.
    generation_metrics: GenerationRunMetrics | None = None
    #: 엄격 생성 당시 평가의 표시 projection. 권위는 generation_evidence 안의
    #: exact GenerationAssessment이며 이 값에서 평가를 역으로 꾸미지 않는다.
    quality_observation: GenerationQualityObservation | None = None
    #: 웹·PDF·Notion이 «그대로 배치만» 하면 되는 공개 봉인 블록.
    #: 생성 시점에 딱 한 번 만들어 여기 실린다 — 렌더러가 문자열을 새로
    #: 만들지 않게 하려는 것이다. FULL 새 생성물만 채운다(SHADOW·
    #: ENFORCE_NO_PARTIAL·옛 저장본은 ``None``).
    #:
    #: ★ 이 값은 보고서 payload에 **직렬화되지 않는다**(payload 노드 수가 두 배가
    #:   되어 저장 자원 상한 여유를 반으로 깎았다). 저장 자리는 별도 표이고,
    #:   채워지는 경로는 둘뿐이다:
    #:     ① composer가 방금 만든 결과
    #:     ② ``storage.reports.load()`` 또는
    #:        ``storage.reports.attach_public_projection()``
    #:   payload 문자열에서 되살린 ``Report``(delivery content snapshot·관리자
    #:   승인 snapshot·캐시 재사용)는 ``None``이다. 그 경로에서 화면을 그리려면
    #:   ②를 먼저 불러야 한다. ``None``은 「봉인 없음」이라는 정의된 상태이지
    #:   오류가 아니다.
    public_projection: PublicReportProjection | None = None

    @property
    def fact_ledger(self) -> list[FactRecord]:
        """정본 문서의 용어와 맞춘 읽기 전용 별칭."""

        return self.fact_records

    @property
    def filled_count(self) -> int:
        """등급·안내에 쓰는 여섯 칸만 세는다.

        데모와 옛 저장값은 `cells`에 공고 블록(5·8)이나 숨긴
        9번을 담을 수 있다. 값 전체를 세면 보이지 않는 칸 때문에
        「6개 중 N개」 안내가 부풀어 오른다(P-119).
        """
        if self.schema_version:
            # report_standard는 port를 사용하므로 top-level import로 연결하면 순환한다.
            # 속성을 실제로 읽을 때만 canonical 식별자를 가져온다.
            from src.features.report_standard.constants import (  # noqa: PLC0415
                CANONICAL_SCHEMA_VERSION,
                CANONICAL_SECTION_IDS,
            )

            if self.schema_version == CANONICAL_SCHEMA_VERSION:
                by_id = {section.cell: section for section in self.sections}
                return sum(
                    1
                    for section_id in CANONICAL_SECTION_IDS
                    if section_id in by_id and by_id[section_id].is_filled
                )
        return sum(1 for cell in COUNTED_CELLS if self.cells.get(cell, False))


@dataclass(frozen=True)
class RunResult:
    """파이프라인을 한 번 돌린 결과."""

    outcome: Outcome
    #: outcome이 REPORT일 때만 채워진다
    report: Optional[Report] = None
    #: 실패했을 때 사용자에게 보여줄 사유 (뜻이 담긴 한국어 문장)
    message: str = ""
    #: 실패했을 때도 수집 현황은 보여준다 — 뭘 못 구했는지는 알아야 한다
    sources: list[SourceStatus] = field(default_factory=list)
    #: 할당량을 깎았는가. 보고서가 나가면 1, 아니면 0 (3분법)
    charged: bool = False
    elapsed_sec: float = 0.0

    # ── 이력 1행에 실릴 값 (기획서 08_관측/1_흐름/01_지표수집.md 「13종」) ──
    # ★ 여기 없으면 대시보드가 «영영 못 재는 지표»가 된다. 실패로 끝난 요청도
    #   여기까지는 채워야 「어디서 몇 개 모으다 멈췄나」를 알 수 있다.
    #: 회사 유형. 보고서가 안 나와도(거부·중단) 판정까지 갔으면 안다.
    corp_type: str = ""
    #: 수집한 조각 수 · 그중 실제로 인용된 조각 수 → 「수집 활용률」
    fragments_collected: int = 0
    fragments_cited: int = 0
    #: 생성한 문장 수 · 검사를 통과한 문장 수 → 「원문 일치율」
    sentences_made: int = 0
    sentences_passed: int = 0
    #: 성공·실패와 무관하게 이 요청에서 실제 발생한 내부 AI 변동원가(원).
    #: 고객 청구액이 아니며 서버 월 고정비도 포함하지 않는다.
    cost_krw: float = 0.0
    #: 쓴 AI 모델 이름. 코드를 안 바꿨는데 결과가 달라지는 «유일한 경로»다.
    model: str = ""
    #: API 예외 때문에 마지막 호출의 과금 여부를 확정할 수 없는가.
    #: 관측 13필드에는 넣지 않고, 비용 원장의 진행 중 표식을 닫을지에만 쓴다.
    billing_uncertain: bool = False
    #: 저장해 둔 보고서를 재사용했나. "1층" | "2층" | ""(재사용 안 함)
    #: ★ 이 값이 없으면 대시보드 ⑤ 「캐시 재사용 N건」이 «영영 못 재는 지표»가 된다.
    #:   화면 표시와 이력 기록이 둘 다 이 값을 읽는다 — 기능만 붙이고 화면을
    #:   안 고치면 사용자는 캐시가 도는지 모른다 (문제로그 P-63).
    cache_hit: str = ""
    #: 원문·개인정보 없이 stage/model/token/cache/batch/실제 원가만 담는다.
    ai_cost_events: tuple["AiCostEvent", ...] = ()
    #: 원문·프롬프트 없이 각 span-selection 호출의 token/종료/집계만 담는다.
    span_selection_diagnostics: tuple["SpanSelectionRoundDiagnostic", ...] = ()
    #: 사실 선택이 비었거나 통과한 이유를 나타내는 닫힌 기계 코드.
    span_selection_result_reason: str = ""
    #: GATE_STOPPED의 후단 원인을 원문 없이 구분하는 닫힌 기계 코드.
    #: 고정 RunRecord 13종에는 넣지 않고 별도 SQLite 부속 원장으로만 보낸다.
    final_gate_reason: str = ""
    #: 실제 수집 때 확인한 DART 본문·정정공시 접수번호. 원문은 싣지 않는다.
    dart_receipt_numbers: tuple[str, ...] = ()
    #: 실제 DART 재무 응답을 정규화한 SHA-256. 재무 원값은 싣지 않는다.
    financial_payload_digest: str = ""
    #: single-flight/cache가 실제로 재사용한 불변 보고서 원본 ID.
    #: 단순히 같은 Report 값을 다시 직렬화한 경우에는 비워 둔다.
    reused_content_snapshot_id: str = ""
    #: 위 원본에 최초 승인되어 저장된 PDF artifact ID.
    #: content와 둘 중 하나만 채워진 결과는 웹 출고 경계가 거절한다.
    reused_artifact_id: str = ""
    #: 이 결과를 이후 새 조사 요청의 장기 생성 캐시로 재사용해도 되는가.
    #: 현재 사용자의 불변 링크·PDF 저장이나 같은 순간 waiter의 짧은 fan-out과는
    #: 별개다. 파이프라인이 명시적으로 증명하지 않으면 fail-closed로 끈다.
    generation_cache_eligible: bool = False
    #: V2RunOutput→Report/storage→RunResult에서 손실 없이 나르는 생산 증거.
    generation_evidence: GenerationProducerEvidence | None = None
    #: 생성 당시 정확한 지표. cache hit은 Report에 봉인된 이 값만 재사용한다.
    generation_metrics: GenerationRunMetrics | None = None
    #: 새 생성 경로의 관측값. 문자열에서 평가를 재구성하는 권위는 아니다.
    quality_observation: GenerationQualityObservation | None = None


class Pipeline(Protocol):
    """알맹이가 지켜야 하는 약속.

    `demo.py`도 이걸 지키고, 나중에 만들 진짜 파이프라인도 이걸 지킨다.
    """

    #: 공고 이미지 OCR 결과를 실제 보고서 입력으로 쓰는가. 웹은 이 값을
    #: 확인하기 전에는 OCR provider를 열지 않는다.
    supports_posting_image_input: bool

    def find_company(self, user_input: UserInput) -> Optional[CompanyCard]:
        """입력한 이름으로 회사 하나를 찾는다.

        Args:
            user_input: 사용자가 넣은 것.

        Returns:
            찾았으면 확인 카드에 쓸 회사 하나. 못 찾았으면 None.
            ★ 못 찾은 것과 「대상 아님」은 다르다. 여기서는 대상 여부를 판정하지 않는다.
        """
        ...

    def run(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[StepReporter] = None,
    ) -> RunResult:
        """확인받은 회사로 끝까지 돌린다.

        ★ 이 함수는 **오래 걸리고 중간에 막힌다** (최대 5분). 화면을 멈추지 않으려면
          부르는 쪽이 별도 실행 흐름에서 돌려야 한다.

        Args:
            user_input: 사용자가 넣은 것.
            card: 사람이 [맞습니다]를 누른 회사.
            on_step: 단계를 시작할 때마다 부를 함수. 진행 화면이 이걸로 갱신된다.
                없으면 아무 데도 알리지 않는다.

        Returns:
            어떻게 끝났는지 + (보고서가 나왔으면) 보고서.
        """
        ...

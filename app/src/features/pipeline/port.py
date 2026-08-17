"""★ 알맹이(파이프라인)를 «붙일 자리».

화면은 이 파일에 적힌 모양으로만 알맹이와 이야기한다.
지금은 `demo.py`가 저장된 파일럿 기록을 돌려주고,
나중에 진짜 파이프라인을 만들면 같은 모양으로 답하는 파일을 하나 더 만들어 갈아끼운다.

**화면 코드는 그때 한 줄도 안 바뀐다.** 그게 이 파일을 따로 두는 이유다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol

from src.core.constants import COUNTED_CELLS

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
    #: 자료가 너무 없어 만들기 전에 멈췄다
    GATE_STOPPED = "자료부족_중단"
    #: 만들다 실패했다 — 항상 결함
    FAILED = "생성_실패"


class Grade(str, Enum):
    """보고서를 얼마나 채웠는가.

    정본: 확정/06_검증/1_흐름/02_성립판정과부분보고서.md
    셋 다 보고서는 나간다. 아무것도 못 주는 종료는 GATE_STOPPED뿐이다.
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

    @property
    def is_valid(self) -> bool:
        """열 개수가 안 맞는 표는 화면을 깨뜨리므로 내보내지 않는다."""
        width = len(self.headers)
        return bool(self.rows) and width > 0 and all(len(r) == width for r in self.rows)


@dataclass(frozen=True)
class ReportSection:
    """보고서의 항목 하나 (블록 1개)."""

    #: "1", "4-1", "9" 같은 칸 번호
    cell: str
    title: str
    #: 채워진 근거 원문들. 각 항목은 (문장, 출처표기).
    #: ★ `prose_lines`가 있으면 이것은 «원문 보기»로 들어간다 — 버리지 않는다.
    lines: list[tuple[str, str]] = field(default_factory=list)
    #: 비었을 때 「왜 비었는지」. 프로그램이 붙인다 (AI 아님).
    empty_reason: str = ""
    #: 숫자 표. 문장 대신 이걸로 채울 수 있다 (D13).
    tables: list[ReportTable] = field(default_factory=list)
    #: 작가 AI가 쓴 표시용 문장과 그 문장이 가리킨 «실제 출처 표기» (P-110·P-118).
    #: ★ 검증을 통과한 문장만 들어온다. 문자열 하나로 합치면 문장별 근거가 사라지므로
    #:   `(문장, 출처표기)`를 유지한다. 검사가 죽으면 비어 있고 `lines`를 그대로 낸다.
    prose_lines: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_filled(self) -> bool:
        """근거 원문이나 표가 있으면 채워진 것이다 — 표시용 글은 등급에 안 센다."""
        return bool(self.lines) or bool(self.tables)


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

    @property
    def filled_count(self) -> int:
        """등급·안내에 쓰는 여섯 칸만 세는다.

        데모와 옛 저장값은 `cells`에 공고 블록(5·8)이나 숨긴
        9번을 담을 수 있다. 값 전체를 세면 보이지 않는 칸 때문에
        「6개 중 N개」 안내가 부풀어 오른다(P-119).
        """
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
    #: 이 요청에 쓴 AI 비용(원)
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


class Pipeline(Protocol):
    """알맹이가 지켜야 하는 약속.

    `demo.py`도 이걸 지키고, 나중에 만들 진짜 파이프라인도 이걸 지킨다.
    """

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

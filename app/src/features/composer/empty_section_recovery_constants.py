"""확보 근거 보고서의 한 번짜리 빈 장 복구 한도."""
from src.shared.report_quality.composition_diagnostic_constants import EMPTY_RECOVERY_STEP

MAX_EMPTY_RECOVERY_SECTIONS = 2
MAX_EMPTY_RECOVERY_SENTENCES = 3
MAX_EMPTY_RECOVERY_FRAGMENTS = 6
MAX_EMPTY_RECOVERY_EVIDENCE_CHARS = 12_000
EMPTY_RECOVERY_GUIDE = (
    "첫 검수에서 본문이 비어진 장만 공식 원문으로 새로 작성한다. "
    "장마다 확인 가능한 사실을 최대 3문장으로 간결하게 쓴다. 해석·추정·새 도식은 쓰지 않는다. "
    "인용 원문이 명시한 주체·대상·관계·시점만 옮기고, 전체 집단의 수를 일부 집단에 붙이지 않는다. "
    "숫자·단위·연도·비교 기준을 정확히 결속할 수 없으면 숫자를 쓰지 않는다. "
    "회사 사업 목표나 투자 계획을 인재상·조직문화로 바꾸지 않는다. "
    "쓸 수 있는 사실이 없으면 문장들 배열을 비운다. 검수 탈락 문장을 그대로 되살리지 않는다. "
    "각 장에 제공된 조각 ID만 인용하고, 다른 장을 추가하거나 기존 보고서를 재작성하지 않는다.\n"
    'JSON만 출력한다: {"장들":{"<요청 장 ID>":{"문장들":'
    '[{"글":"짧은 사실 문장","인용":["조각 ID"],"등급":"확인"}]}}}\n'
)

"""직접 회사 비중의 기존 수치 증명 작성을 돕는 좁은 문법."""
import re

DIRECT_SHARE_COMMON_METRIC_RE = re.compile(r'매출(?:액)?(?:은|는|이|가)\s*전체\s*매출(?:액)?')
DIRECT_SHARE_DENOMINATOR_METRIC_RE = re.compile(r'전체\s*매출(?:액)?')
DIRECT_SHARE_PERCENT_RE = re.compile(r'[0-9]+(?:\.[0-9]+)?%')
DIRECT_SHARE_NUMERIC_HINT_GUIDE = (
    '  직접 비중 수치증명 작성 참고(JSON): '
)
DIRECT_SHARE_NUMERIC_HINT_TAIL = (
    '  참고는 자동 승인이나 선택 ID가 아니다. 주체·품목·개별/연결 기준과 의미를 검수한 뒤 '
    '참이면 검증근거.수치에 직접 작성한다. 표현·원문 속 약 표지는 유지하고 값에는 '
    '기존 원문과 후보에 있는 숫자 토큰만 쓴다. 다른 비율이나 분모로 바꾸지 않는다.\n'
)

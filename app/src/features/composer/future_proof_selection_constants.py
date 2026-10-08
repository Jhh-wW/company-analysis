"""미래 산문 증명의 요청별 원문 선택 계약."""
import re
from src.features.composer.future_plan_constants import PARTICLE_TAIL

FUTURE_SELECTION_KEY = "미래증명선택"
FUTURE_SELECTION_VERSION = "future-proof-selection-v1"
FUTURE_SELECTION_STAGE = "future_proof_selection_unbound"
FUTURE_SELECTION_SECTION = "future_strategy"
LEADING_CONNECTIVE_RE = re.compile(r"(?:및|과|와|그러나|하지만)(?:\s|$)")
MAX_SOURCE_CHARS = 2400
MAX_SENTENCE_CHARS = 600
MAX_WORDS = 64
MAX_SHARED_PHRASES = 80
MAX_PROOF_CHECKS = 800
MAX_OPTIONS = 4
WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")
TRAILING_PARTICLE_RE = re.compile(r"(.{2,}?)(?:" + PARTICLE_TAIL + r")\Z")
MODALITY_GAP_RE = re.compile(r"\A\s*(?:" + PARTICLE_TAIL + r")?\s*\Z")
SELECTION_GUIDE = (
    "  미래증명선택은 이 번호의 자기 원문 연속 구간에서 만든 증명 선택지다. "
    "후보의 주체·범위·시점·내용을 검수한 뒤 맞는 ID 하나를 검증근거의 "
    "'미래증명선택'에 그대로 넣는다. 이때 '미래근거' 배열은 함께 넣지 않는다. "
    "선택은 참 판정을 보장하지 않는다. 다른 번호·요청의 ID를 쓰거나 선택지의 "
    "구절을 바꾸지 않는다. 선택하지 않으면 기존 미래근거 형식을 사용한다.\n"
)

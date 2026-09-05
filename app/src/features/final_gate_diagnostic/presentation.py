"""최종 게이트의 닫힌 사유 코드를 사용자 안내로 번역한다.

파이프라인의 ``message``는 실행 시점의 자유 문장이라 사유 코드와 어긋날 수
있다. 중단 화면은 이 모듈의 닫힌 번역만 사용해 회사 자료 부족과 내부 결함을
서로 잘못 안내하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping

from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_MISSING_IDENTITY,
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
    FINAL_GATE_REASON_MISSING_REVENUE,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
)


class StoppedGuidanceState(str, Enum):
    """사용자가 다음 행동을 고를 수 있는 아홉 가지 중단 상태."""

    INTERNAL_EVIDENCE_ERROR = "internal_evidence_error"
    EVIDENCE_CLASSIFICATION_UNDETERMINED = "evidence_classification_undetermined"
    OFFICIAL_SOURCE_CONFIGURATION = "official_source_configuration"
    TRANSIENT_COLLECTION_ISSUE = "transient_collection_issue"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    COMPARISON_EVIDENCE_INSUFFICIENT = "comparison_evidence_insufficient"
    GENERATION_QUALITY_SHORTFALL = "generation_quality_shortfall"
    REQUEST_BUDGET_EXHAUSTED = "request_budget_exhausted"
    OTHER = "other"


@dataclass(frozen=True)
class StoppedGuidance:
    """중단 화면 한 상태의 상호배타적인 사용자 안내."""

    state: StoppedGuidanceState
    title: str
    summary: str
    meaning: str
    actions: tuple[str, ...]
    primary_button_label: str


_GUIDANCE_BY_STATE: Final[Mapping[StoppedGuidanceState, StoppedGuidance]] = (
    MappingProxyType(
        {
            StoppedGuidanceState.INTERNAL_EVIDENCE_ERROR: StoppedGuidance(
                state=StoppedGuidanceState.INTERNAL_EVIDENCE_ERROR,
                title="회사 자료가 아니라 시스템 내부 연결 문제입니다",
                summary=(
                    "모은 자료를 보고서의 장과 표에 연결하는 내부 검사를 "
                    "통과하지 못해 출고 전에 멈췄습니다."
                ),
                meaning=(
                    "회사의 강점이나 자료가 부족하다는 판정이 아닙니다. "
                    "같은 회사라도 내부 연결을 고치면 결과가 달라질 수 있습니다."
                ),
                actions=(
                    "같은 조건으로 바로 반복하지 말아 주세요. 같은 내부 문제가 다시 생길 수 있습니다.",
                    "화면 아래의 오류 신고로 알려주시면 운영자가 내부 기록을 확인합니다.",
                    "시스템의 자료 연결 문제가 수정된 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="입력 화면으로 돌아가기",
            ),
            StoppedGuidanceState.EVIDENCE_CLASSIFICATION_UNDETERMINED: StoppedGuidance(
                state=StoppedGuidanceState.EVIDENCE_CLASSIFICATION_UNDETERMINED,
                title="공식 자료의 뜻을 자동으로 끝까지 확인하지 못했습니다",
                summary=(
                    "공식 자료는 읽었지만 필요한 내용이 실제로 없는지, 자동 분류가 "
                    "표현을 알아보지 못한 것인지 구분할 수 없어 안전하게 멈췄습니다."
                ),
                meaning=(
                    "회사 자료 부족이나 시스템 내부 연결 오류로 단정한 결과가 아닙니다. "
                    "확인하지 못한 내용을 억지로 보고서 근거로 쓰지 않았습니다."
                ),
                actions=(
                    "회사명이나 주소를 바꿀 필요는 없습니다.",
                    "같은 조건으로 바로 반복하면 같은 자동 확인 한계가 이어질 수 있습니다.",
                    "자료 분류 범위가 보완된 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="입력 화면으로 돌아가기",
            ),
            StoppedGuidanceState.OFFICIAL_SOURCE_CONFIGURATION: StoppedGuidance(
                state=StoppedGuidanceState.OFFICIAL_SOURCE_CONFIGURATION,
                title="공식 자료 접근 설정을 운영자가 확인해야 합니다",
                summary=(
                    "공식 자료에 접근하는 시스템 인증이나 권한 설정을 확인하지 "
                    "못해 AI 작성 전에 멈췄습니다."
                ),
                meaning=(
                    "회사 자료가 부족하거나 입력이 틀렸다는 판정이 아닙니다. "
                    "같은 상태에서는 사용자가 다시 시도해도 해결되지 않습니다."
                ),
                actions=(
                    "회사명이나 주소를 바꾸지 말아 주세요.",
                    "같은 조건으로 반복하지 말고 화면 아래의 오류 신고로 알려 주세요.",
                    "운영자가 공식 자료 접근 설정을 고친 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="입력 화면으로 돌아가기",
            ),
            StoppedGuidanceState.TRANSIENT_COLLECTION_ISSUE: StoppedGuidance(
                state=StoppedGuidanceState.TRANSIENT_COLLECTION_ISSUE,
                title="공식 자료를 가져오는 중 잠시 문제가 생겼습니다",
                summary=(
                    "공식 자료가 없다고 결론 낸 것이 아니라, 자료 제공처와의 "
                    "연결을 끝까지 확인하지 못했습니다."
                ),
                meaning=(
                    "회사에 자료가 없거나 강점이 없다는 판정이 아닙니다. "
                    "일시적인 접속·응답 문제 때문에 안전하게 멈춘 것입니다."
                ),
                actions=(
                    "회사명이나 주소를 바꿀 필요는 없습니다.",
                    "곧바로 여러 번 반복하면 같은 접속 문제가 이어질 수 있습니다.",
                    "잠시 뒤 자료 제공처가 정상화되면 다시 조사해 주세요.",
                ),
                primary_button_label="잠시 후 다시 시작",
            ),
            StoppedGuidanceState.EVIDENCE_INSUFFICIENT: StoppedGuidance(
                state=StoppedGuidanceState.EVIDENCE_INSUFFICIENT,
                title="완성 보고서에 필요한 공식 근거가 부족합니다",
                summary=(
                    "확인 가능한 공식 자료를 모두 모았지만, 검증된 완성 보고서의 "
                    "최소 근거를 채우지 못했습니다."
                ),
                meaning=(
                    "회사에 강점이나 경쟁우위가 없다는 판정이 아닙니다. "
                    "현재 공개된 공식 자료만으로는 확인할 수 없다는 뜻입니다."
                ),
                actions=(
                    "입력한 정식 법인명과 시·군·구 주소가 맞는지 확인해 주세요.",
                    "입력과 공식 자료 상태가 그대로라면 반복해도 결과가 달라지지 않습니다.",
                    "회사의 공식 공시·홈페이지 자료가 추가된 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="회사·주소 확인하고 다시 시작",
            ),
            StoppedGuidanceState.COMPARISON_EVIDENCE_INSUFFICIENT: StoppedGuidance(
                state=StoppedGuidanceState.COMPARISON_EVIDENCE_INSUFFICIENT,
                title="같은 조건으로 비교할 경쟁사 공식 근거가 부족합니다",
                summary=(
                    "자사와 비교사의 같은 사업연도·회계 범위·지표를 함께 "
                    "확인하지 못해 경쟁우위 장을 검증할 수 없었습니다."
                ),
                meaning=(
                    "회사의 경쟁력이 없다는 판정이 아닙니다. 서로 다른 기준의 "
                    "숫자를 억지로 비교하지 않고 보고서 출고를 멈춘 것입니다."
                ),
                actions=(
                    "이미 확인한 회사명이나 주소를 바꿀 필요는 없습니다.",
                    "같은 공식 자료 상태에서 바로 반복하면 결과가 달라지지 않습니다.",
                    "동일 조건의 양사 공식 자료가 확보되거나 비교 수집 범위가 보완된 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="입력 화면으로 돌아가기",
            ),
            StoppedGuidanceState.GENERATION_QUALITY_SHORTFALL: StoppedGuidance(
                state=StoppedGuidanceState.GENERATION_QUALITY_SHORTFALL,
                title="만든 초안이 품질 기준을 통과하지 못했습니다",
                summary=(
                    "자료를 바탕으로 초안을 만들었지만 내용의 양이나 검증 비율이 "
                    "완성 보고서 기준에 미치지 못했습니다."
                ),
                meaning=(
                    "회사 자료가 없다는 뜻이 아닙니다. 확인되지 않은 내용을 억지로 "
                    "채우는 대신 보고서를 내보내지 않았습니다."
                ),
                actions=(
                    "회사명이나 주소를 바꿀 필요는 없습니다.",
                    "같은 조건으로 바로 반복하면 비슷한 초안이 만들어질 수 있습니다.",
                    "생성 품질 보완이 적용된 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="입력 화면으로 돌아가기",
            ),
            StoppedGuidanceState.REQUEST_BUDGET_EXHAUSTED: StoppedGuidance(
                state=StoppedGuidanceState.REQUEST_BUDGET_EXHAUSTED,
                title="이 조사에 배정된 AI 예산을 다 써서 멈췄습니다",
                summary=(
                    "회사 자료가 많아 정해진 예산 안에서 보고서를 끝내지 "
                    "못했습니다."
                ),
                meaning=(
                    "회사 문제도, 자료 부족도 아닙니다. 조사 1건에 허용된 "
                    "AI 비용 한도에 닿았습니다."
                ),
                actions=(
                    "관리자에게 알려 주세요(한도 조정 대상).",
                    "같은 회사를 바로 다시 시도하면 같은 결과가 날 수 있습니다.",
                ),
                primary_button_label="회사·주소 다시 입력",
            ),
            StoppedGuidanceState.OTHER: StoppedGuidance(
                state=StoppedGuidanceState.OTHER,
                title="안전 검사를 통과하지 못해 보고서를 내보내지 않았습니다",
                summary=(
                    "저장된 안전한 사유만으로는 회사 자료 문제인지 시스템 문제인지 "
                    "정확히 나눌 수 없습니다."
                ),
                meaning=(
                    "회사의 강점이나 자료가 부족하다고 판단한 것은 아닙니다. "
                    "확인하지 못한 내용을 정상 보고서처럼 보여주지 않기 위해 멈췄습니다."
                ),
                actions=(
                    "같은 조건으로 바로 반복하지 말아 주세요.",
                    "화면 아래의 오류 신고로 알려주시면 운영자가 중단 기록을 확인합니다.",
                    "원인이 확인된 뒤 다시 조사해 주세요.",
                ),
                primary_button_label="입력 화면으로 돌아가기",
            ),
        }
    )
)

_EVIDENCE_INSUFFICIENT_REASONS: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    }
)
_GENERATION_QUALITY_REASONS: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_REASON_MISSING_IDENTITY,
        FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
        FINAL_GATE_REASON_MISSING_REVENUE,
        FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    }
)


def guidance_for_final_gate_reason(reason_code: str) -> StoppedGuidance:
    """닫힌 최종 사유 하나를 안전한 사용자 안내 하나로 바꾼다.

    ``publish_blocked``는 과거 구조·안전·품질 실패를 한 코드에 섞었으므로 어느
    쪽이라고 단정하지 않는다. 빈 값·오래된 값·미등록 값도 같은 보수적인 기타
    안내로 닫고 원문 값은 화면에 내보내지 않는다.
    """

    normalized = reason_code.strip() if type(reason_code) is str else ""
    if normalized == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT:
        state = StoppedGuidanceState.INTERNAL_EVIDENCE_ERROR
    elif normalized == FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED:
        state = StoppedGuidanceState.EVIDENCE_CLASSIFICATION_UNDETERMINED
    elif normalized == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION:
        state = StoppedGuidanceState.OFFICIAL_SOURCE_CONFIGURATION
    elif normalized == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT:
        state = StoppedGuidanceState.TRANSIENT_COLLECTION_ISSUE
    elif normalized == FINAL_GATE_REASON_COMPARISON_BLOCKED:
        state = StoppedGuidanceState.COMPARISON_EVIDENCE_INSUFFICIENT
    elif normalized == FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED:
        # 운영 한도 문제를 «자료 부족»이나 «품질 미달»로 안내하면 사용자가
        # 멀쩡한 회사를 탓하게 된다. 별도 상태로 떼어 둔다.
        state = StoppedGuidanceState.REQUEST_BUDGET_EXHAUSTED
    elif normalized in _EVIDENCE_INSUFFICIENT_REASONS:
        state = StoppedGuidanceState.EVIDENCE_INSUFFICIENT
    elif normalized in _GENERATION_QUALITY_REASONS:
        state = StoppedGuidanceState.GENERATION_QUALITY_SHORTFALL
    else:
        # publish_blocked와 other_gate는 더 구체적인 사용자 귀책 사유로
        # 승격할 근거가 없으므로 빈 값·미등록 값과 함께 보수적으로 닫는다.
        state = StoppedGuidanceState.OTHER
    return _GUIDANCE_BY_STATE[state]

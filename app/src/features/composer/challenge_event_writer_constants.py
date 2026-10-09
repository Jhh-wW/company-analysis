"""사건 표의 작성 보조 입력 한도와 지침."""
from typing import Final

EVENT_WRITER_MAX_ROWS: Final[int] = 8
EVENT_WRITER_MAX_CHARS: Final[int] = 8000
EVENT_WRITER_MAX_ROW_CHARS: Final[int] = 2000
EVENT_WRITER_SLOT_IDS: Final[frozenset[str]] = frozenset({
    'current_challenges:issue', 'current_challenges:response',
})
EVENT_WRITER_UNKNOWN_ACTORS: Final[frozenset[str]] = frozenset({'미상', '불명', '미정', '확인불가', 'N/A'})
EVENT_WRITER_HEADER: Final[str] = '\n사건 행별 작성 안내 — 아래는 같은 원문을 읽기 쉽게 펼친 보조 보기이며 새 근거나 검수 통과 표시가 아닙니다.\n'
EVENT_WRITER_GUIDE: Final[str] = (
    '각 문장과 대응표의 과제 칸에 그 행의 주체 이름과 날짜를 원문 그대로 다시 적으세요. '
    '㈜ 등 법인 표기를 빼지 말고, 주체가 섞인 표에서 «회사는»으로 대신하지 마세요. '
    '한 문장은 한 행의 사건과 그 행의 조치·상태만 사용하세요. 완료·진행·예정·부정 상태를 바꾸지 마세요. '
    '«설비 점검 및 개선»처럼 명사만 적힌 조치는 대응 항목에 기재됐다고 쓰고, «이행했다»나 «완료했다»를 추가하지 마세요. '
    '같은 행의 «과태료 납부 완료»는 납부의 상태이며 뒤에 나열된 다른 조치의 완료를 뜻하지 않습니다. '
    '법정 처벌 가능성을 회사가 실제 취한 대응으로 쓰지 마세요. '
    '행의 숫자·날짜·단위는 원문 표기를 그대로 쓰세요. 숫자 «1»에 원문에 없는 «명»을 붙이지 마세요. '
    '항목 이름과 값을 원문대로 쓸 수 없으면 수량을 생략하고 사건·조치만 쓰세요. '
    '선택 가능한 지원쌍은 원래 조각의 의미칸 한 종류만 고르며 보조 보기의 행 번호는 인용 번호가 아닙니다.\n'
)
EVENT_WRITER_OMISSION: Final[str] = '보조 보기에서 생략한 행이 있습니다. 이는 근거 없음 판정이 아니며 원문 조각과 원래 지원쌍이 정본입니다.\n'

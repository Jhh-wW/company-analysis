"""사고·제재 표의 같은 행 관계를 확인하는 범위."""
from __future__ import annotations

import re
from typing import Final

ROW_SEPARATOR_RE = re.compile(r"[;\n]")
EVENT_DATE_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?:[./-]\d{1,2}[./-]\d{1,2}|년\d{1,2}월\d{1,2}일)(?!\d)")
HYPOTHETICAL_RE = re.compile(r"예정|계획|발생할|발생하는경우|발생한경우|발생시|할수있")
ACCIDENT_RE = re.compile(r"(?:끼임|추락|충돌)사고|추락(?:함|했|하였|하여)|충돌(?:함|했|하였)|사망(?:함|했|하였)|화재(?:발생|로)|폭발(?:발생|로)")
SANCTION_RE = re.compile(r"과태료|벌금|시정명령|개선명령|영업정지|작업중지명령|허가취소")
MIN_TABLE_CELLS: Final[int] = 3
MAX_TABLE_CELLS: Final[int] = 24
ACTOR_HEADERS: Final[tuple[str, ...]] = ("재해발생회사", "처벌또는조치대상자", "처분대상자", "제재대상자")
DATE_HEADERS: Final[tuple[str, ...]] = ("중대재해발생일자", "재해발생일자", "제재조치일", "처분일자")
ACCIDENT_HEADERS: Final[tuple[str, ...]] = ("재해내용", "사고내용")
SANCTION_HEADERS: Final[tuple[str, ...]] = ("처벌또는조치내용", "처분내용", "제재내용")
PLACE_HEADERS: Final[tuple[str, ...]] = ("발생장소", "사고장소")
UNKNOWN_ACTORS: Final[frozenset[str]] = frozenset({"", "-", "해당없음", "없음", "미확인", "미상", "알수없음", "N/A", "n/a"})
OTHER_TABLE_HEADERS: Final[frozenset[str]] = frozenset({"구분", "품목", "사업부문", "매출액", "비중", "수량", "회사명", "일자", "내용", "금액"})
MIN_OTHER_HEADER_CELLS: Final[int] = 2
RESPONSE_HEADERS: Final[tuple[str, ...]] = ("조치및전망", "이행및재발방지대책", "이행및재발방지조치", "조치내용", "개선대책", "재발방지대책")
RESPONSE_ACTION_RE = re.compile(r"설치|개선|납부|지급|도입|운영|교체|시정|정지|안전센서|동작금지")

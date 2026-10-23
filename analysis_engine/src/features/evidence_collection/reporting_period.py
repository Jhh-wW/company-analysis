"""동일 법인 XML의 유일한 명시 기간만 운반하고 제목·공시일로 추정하지 않는다."""

from datetime import date
import xml.etree.ElementTree as ET

from features.evidence_collection.reporting_period_constants import (
    CORP_CODE_RE, DATE_TEXT_RE, DATE_VALUE_RE, REPORTING_PERIOD_RE,
    UNSAFE_XML_DECLARATION_RE,
)


def require_reporting_period(value: str) -> None:
    """빈 값은 미확인이고, 명시 값은 실제 날짜의 오름차순 닫힌 구간이어야 한다."""
    if type(value) is not str:
        raise TypeError('보고기간은 문자열이어야 합니다')
    if value == '':
        return
    match = REPORTING_PERIOD_RE.fullmatch(value)
    if match is None:
        raise ValueError('보고기간은 YYYY-MM-DD/YYYY-MM-DD 형식이어야 합니다')
    try:
        start, end = (date.fromisoformat(part) for part in match.groups())
    except ValueError as error:
        raise ValueError('보고기간은 실제 달력에 존재하는 날짜여야 합니다') from error
    if start > end:
        raise ValueError('보고기간의 시작일은 종료일보다 늦을 수 없습니다')


def _date_value(node: ET.Element) -> str:
    value = node.get('AUNITVALUE', '')
    visible = DATE_TEXT_RE.fullmatch(''.join(node.itertext()).strip())
    if DATE_VALUE_RE.fullmatch(value) is None or visible is None:
        return ''
    try:
        declared = date(int(value[:4]), int(value[4:6]), int(value[6:]))
        shown = date(*(int(part) for part in visible.groups()))
    except ValueError:
        return ''
    return declared.isoformat() if declared == shown else ''


def reporting_period_from_xml(markup: str, *, document_corp_code: str) -> str:
    """같은 XML 표제의 검증 법인과 기간을 묶는다. 요청법인 대조는 수집 경계가 맡는다."""
    if (CORP_CODE_RE.fullmatch(document_corp_code) is None
            or UNSAFE_XML_DECLARATION_RE.search(markup)):
        return ''
    try:
        root = ET.fromstring(markup)
    except (ET.ParseError, ValueError):
        return ''
    companies = root.findall('COMPANY-NAME')
    if (root.tag != 'DOCUMENT' or len(companies) != 1
            or companies[0].get('AREGCIK') != document_corp_code
            or not ''.join(companies[0].itertext()).strip()):
        return ''
    starts = root.findall('.//*[@AUNIT="PERIODFROM"]')
    ends = root.findall('.//*[@AUNIT="PERIODTO"]')
    if len(starts) != 1 or len(ends) != 1:
        return ''
    start, end = _date_value(starts[0]), _date_value(ends[0])
    return f'{start}/{end}' if start and end and start <= end else ''

"""원문을 바꾸지 않고 동일 법인·공시 XML의 명시 보고기간만 운반한다."""

from datetime import date
from pathlib import Path

from lxml import etree

from src.features.filingclean import litigation_constants as c


def inline_litigation_start(text: str, start: int) -> int:
    """문장 안의 소송 키워드 앞에 붙은 시점·소송 당사자를 잃지 않는다."""
    offset = max(0, start - c.LITIGATION_PREFIX_LOOKBACK)
    match = c.LITIGATION_PREFIX_RE.search(text[offset:start])
    return offset + match.start() if match else start


def _date_value(node) -> str:
    value = node.get("AUNITVALUE", "")
    visible = c.DATE_TEXT_RE.fullmatch("".join(node.itertext()).strip())
    if not c.DATE_VALUE_RE.fullmatch(value) or visible is None:
        return ""
    try:
        declared = date(int(value[:4]), int(value[4:6]), int(value[6:]))
        shown = date(*(int(part) for part in visible.groups()))
    except ValueError:
        return ""
    return declared.isoformat() if declared == shown else ""


def attach_litigation_period(
    fragments, *, filing_text: str, raw_path: str, receipt_number: str,
    corp_code: str, document_title: str, disclosed_at: str,
):
    """수집한 파일·평문·법인코드가 모두 같을 때만 legacy 메타를 보충한다.

    신뢰 등급·typed 신원·검수 판정은 만들지 않는다. 제목 연도나 공시일에서
    당기말을 추정하지 않으며 모호한 XML, 다른 파일/법인은 그대로 둔다.
    """
    if (not c.RECEIPT_RE.fullmatch(receipt_number)
            or not c.CORP_CODE_RE.fullmatch(corp_code)
            or Path(raw_path).stem != receipt_number):
        return fragments
    try:
        raw = Path(raw_path).read_bytes()
        root = etree.fromstring(raw, parser=etree.XMLParser(
            resolve_entities=False, no_network=True,
        ))
        decoded = None
        for encoding in c.XML_ENCODINGS:
            try:
                decoded = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None or c.WHITESPACE_RE.sub(" ", c.XML_TAG_RE.sub(" ", decoded)).strip() != filing_text.strip():
            return fragments
        companies = root.findall("COMPANY-NAME")
        if len(companies) != 1 or companies[0].get("AREGCIK") != corp_code:
            return fragments
        starts = root.xpath('//*[@AUNIT="PERIODFROM"]')
        ends = root.xpath('//*[@AUNIT="PERIODTO"]')
        if len(starts) != 1 or len(ends) != 1:
            return fragments
        start, end = _date_value(starts[0]), _date_value(ends[0])
        if not start or not end or start > end:
            return fragments
        publisher = "".join(companies[0].itertext()).strip()
    except (OSError, ValueError, etree.XMLSyntaxError):
        return fragments
    try:
        disclosed = date.fromisoformat(disclosed_at).isoformat()
    except ValueError:
        disclosed = ""
    out = dict(fragments)
    for number, fragment in fragments.items():
        if (fragment.get("종류") not in c.PERIOD_FRAGMENT_KINDS
                or not fragment.get("원문") or fragment["원문"] not in filing_text
                or fragment.get("출처")
                or fragment.get("문서ID", receipt_number) != receipt_number):
            continue
        out[number] = {
            **fragment, "문서ID": receipt_number, "문서명": document_title,
            "문서일": disclosed, "발행주체": publisher,
            "원문위치": fragment["종류"], "보고기간": f"{start}/{end}",
        }
    return out

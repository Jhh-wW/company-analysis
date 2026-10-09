"""실습 모드의 원문·문서·조각 결속과 정상 회사 실행 경계."""

import hashlib
import json

import pytest

from src.shared.report_evidence.practice_context import (
    build_practice_context,
    parse_practice_context,
    practice_context_fingerprint,
    practice_range_scopes,
)

MARKER = "예를 들어 입사 후 온보딩 개선 업무를 맡았다고 해보겠습니다."
RULE = "# Project Rules - 데이터로 확인된 사실과 AI가 만든 가설을 구분한다."
QA = "사용자가 이전 화면으로 돌아가거나 중간에 이탈할 때 흐름이 자연스러운지 확인합니다."


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def context(ranges=(MARKER, RULE, QA), index=2):
    return build_practice_context(
        ranges=ranges, document_id="doc", document_sha256=sha("\n".join(ranges)),
        fragment_index=index, fragment_location=f"https://example.org/blog/demo · 목록 {index+1}번째 항목",
    )


def test_예시_표지와_원문_조각을_별도_범위로_묶는다():
    raw = context()
    parsed = parse_practice_context(raw, document_id="doc", document_text="\n".join((MARKER,RULE,QA)), fragment_text=QA)
    assert parsed["text"] == MARKER and parsed["mode"] == "example"
    assert parsed["fragment_sha256"] == sha(QA)
    assert practice_context_fingerprint(raw) == sha(raw)


@pytest.mark.parametrize("key,value", [
    ("document_id", "other"), ("document_sha256", "b"*64),
    ("fragment_sha256", "c"*64), ("fragment_location", "other"),
    ("text", "교육 상품을 판매합니다."), ("mode", "actual"),
    ("location", "2-1"), ("scope_location", "0-5"),
    ("fragment_range", "0-1"), ("text_sha256", "d"*64),
])
def test_문맥_필드_변조는_정확_원문과_일치하지_않는다(key, value):
    item = json.loads(context())
    item[key] = value
    changed = json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    with pytest.raises(ValueError):
        parse_practice_context(changed,document_id="doc",document_sha256=sha("\n".join((MARKER,RULE,QA))),
                               fragment_location="https://example.org/blog/demo · 목록 3번째 항목",fragment_text=QA)


@pytest.mark.parametrize("actual", [
    "실제로 회사는 AI 도구를 도입했습니다.",
    "실제로 당사 직원은 사실과 가설을 구분하는 업무 원칙을 시행합니다.",
    "실제로 고객 A사에서는 PoC를 구현했습니다.",
    "실제 고객 적용 사례",
])
def test_학습자료_안의_실제_회사_사례와_직원원칙은_예시_범위에서_제외한다(actual):
    ranges = (MARKER,RULE,actual,QA)
    scopes = practice_range_scopes(ranges)
    assert set(scopes)=={0,1}
    assert context(ranges,2)==""


def test_예시_속_가정한_회사의_실행은_실제_경계로_쓰지_않는다():
    ranges=(MARKER,"예를 들어 회사는 AI를 도입했다고 가정합니다.",QA)
    assert set(practice_range_scopes(ranges))=={0,1,2}


@pytest.mark.parametrize("ranges,index", [
    (("예를 들어 이렇게 요청합니다. 당사는 현재 온보딩 서비스를 운영합니다.",),0),
    (("예를 들어 이렇게 요청합니다.","현재 온보딩 안을 구현한다. 당사는 현재 기업용 API를 운영합니다."),1),
    (("예를 들어 이렇게 요청합니다. 당사는 현재 정밀부품을 제조·판매합니다.",),0),
])
def test_같은_구간에_섞인_명시_회사_실행을_예시로_표시하지_않는다(ranges,index):
    assert context(ranges,index)==""


def test_회사_고유명칭의_실제_실행은_같은_문서_사례를_보존한다():
    ranges=(MARKER,"실제로 새봄소프트는 AI 검수 도구를 도입했습니다.",QA)
    assert set(practice_range_scopes(ranges,company_name="새봄소프트 주식회사"))=={0}


def test_표지없는_교육_제목_및_일반_QA_원칙은_모드를_추정하지_않는다():
    assert practice_range_scopes(("AI 교육 성공 사례",QA))=={}
    assert context(("AI 교육 성공 사례",QA),1)==""


def test_구형_빈_문맥은_값과_지문을_유지한다():
    assert parse_practice_context("")=={}
    assert practice_context_fingerprint("")==""


@pytest.mark.parametrize("claim", [
    "회사는 2024년 검수 시스템을 도입했습니다.",
    "회사는 오류를 확인합니다.",
])
def test_앞_구간의_가상회사_가정은_다음_일반회사_서술에서_종료되지_않는다(claim):
    marker = "예를 들어 가상의 회사 업무를 수행했다고 가정합니다."
    assert set(practice_range_scopes((marker, claim))) == {0, 1}
    assert context((marker, claim), 1)


def test_가상회사_가정이_없는_일반_회사_사례는_기존_경계로_보존한다():
    ranges = ("예를 들어 이렇게 요청합니다.", "회사는 AI 도구를 도입했습니다.")
    assert set(practice_range_scopes(ranges)) == {0}


@pytest.mark.parametrize("boundary", [
    "실제 도입 사례", "실제로 회사는 검수 시스템을 도입했습니다.",
    "실제로 당사는 검수 시스템을 도입했습니다.",
])
def test_가상회사_예시_뒤의_명시_실제사례와_자기주체_실행은_보존한다(boundary):
    marker = "예를 들어 가상의 회사 업무를 수행했다고 가정합니다."
    assert set(practice_range_scopes((marker, boundary))) == {0}


def test_실제_완료절_뒤의_다른_계획절이_완료근거를_예시로_바꾸지_않는다():
    ranges = ("예를 들어 가상의 회사 업무를 수행했다고 가정합니다.",
              "실제로 회사는 2024년 검수 시스템을 도입했으며, 향후 기능을 확장할 계획입니다.")
    assert set(practice_range_scopes(ranges)) == {0}
    assert context(ranges, 1) == ""


@pytest.mark.parametrize("subject", ["당사", "본사", "우리 회사", "새봄주식회사"])
def test_강한_가정은_자기호칭이나_가상법인명_서술만으로_끝나지_않는다(subject):
    ranges = ("예를 들어 가상의 회사 업무를 수행했다고 가정합니다.",
              f"{subject}는 2024년 검수 시스템을 도입했습니다.")
    assert set(practice_range_scopes(ranges, company_name="새봄주식회사")) == {0, 1}


@pytest.mark.parametrize("raw", [None,{},"{}"])
def test_잘못된_스키마는_자동_복구하지_않는다(raw):
    with pytest.raises((ValueError,TypeError)):
        parse_practice_context(raw)

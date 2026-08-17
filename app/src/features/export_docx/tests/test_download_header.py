"""내려받기 파일명이 «어느 브라우저에서도» 살아남는지 못 박는다 (문제로그 P-77).

★ 실제로 겪은 사고 — 크롬에서 파일이 **이름도 확장자도 없이** 받아졌다.
  (`f10b169b-2803-4e51-acfe-20a58642be10`, 37.3KB). 그 상태로는 더블클릭해도
  워드가 안 열린다.

★ 원인 — HTTP 헤더에는 한글을 못 넣어서, 한글 이름은 `filename*`에 넣고
  ASCII 이름을 `filename=`에 하나 더 넣는다. 그런데 그 ASCII 이름을
  «한글만 지우는» 방식으로 만들다 보니 찌꺼기가 남았다:
      루트로닉_고전압 파워 R&D 연구원_2026-08-15.docx
      → `_  R&D _2026-08-15.docx`   (밑줄로 시작 · 겹공백 · `&`)
  이런 값은 클라이언트에 따라 **헤더 전체를 무시**하게 만들고, 그러면
  파일명 정보가 통째로 사라진다.

★ 그래서 «남기는» 규칙으로 뒤집었다 — 영문·숫자·점·밑줄·붙임표만 남긴다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.features.export_docx.constants import DOCX_SUFFIX
from src.features.export_docx.logic import (
    build_ascii_filename,
    build_content_disposition,
    build_download_filename,
)
from src.features.pipeline.port import Grade, Report

#: 실제 사고를 낸 그 파일명.
_사고_파일명 = "루트로닉_고전압 파워 R&D 연구원_2026-08-15.docx"

#: ASCII 이름에 절대 있으면 안 되는 글자.
#: 큰따옴표는 `filename="..."`를 조기 종료시키고, 나머지는 파서를 헷갈리게 한다.
_위험한_글자 = ('"', "&", ";", ",", "\\", "  ")


def _보고서(company: str, job: str) -> Report:
    return Report(
        company=company,
        job=job,
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        generated_at=dt.date(2026, 8, 15).isoformat(),
    )


# ══════════════════════════════════════════════════════════
# ① ASCII 대체 이름 자체
# ══════════════════════════════════════════════════════════


def test_한글을_지우고_남은_찌꺼기를_쓰지_않는다():
    """★ 이 시험이 P-77 그 자체다."""
    ascii_name = build_ascii_filename(_사고_파일명)

    assert ascii_name != "_  R&D _2026-08-15.docx"
    assert not ascii_name.startswith(("_", "-", "."))
    for 글자 in _위험한_글자:
        assert 글자 not in ascii_name, f"위험한 글자가 남았습니다: {글자!r} in {ascii_name}"


@pytest.mark.parametrize(
    "company, job",
    [
        ("루트로닉", "고전압 파워 R&D 연구원"),
        ("우리엔", "영업 관리"),                    # 영문이 하나도 없는 경우
        ("카카오", "마케팅"),
        ('회사"이름', "직무;세미콜론"),              # 헤더를 깨뜨리는 글자
        ("   ", "   "),                            # 공백뿐
    ],
)
def test_어떤_회사_직무라도_ASCII_이름이_안전하다(company: str, job: str):
    ascii_name = build_ascii_filename(build_download_filename(_보고서(company, job)))

    ascii_name.encode("ascii")   # 한글이 섞이면 여기서 터진다
    assert ascii_name.endswith(DOCX_SUFFIX), "확장자가 없으면 워드가 안 열린다"
    assert len(ascii_name) > len(DOCX_SUFFIX), "이름이 확장자뿐이면 안 된다"
    for 글자 in _위험한_글자:
        assert 글자 not in ascii_name


def test_영문이_하나도_없으면_자리표시자를_쓴다():
    """★ 예전에는 한글 「보고서.docx」를 넣었다 — HTTP 헤더에 한글은 못 들어간다."""
    ascii_name = build_ascii_filename("보고서.docx")

    ascii_name.encode("ascii")
    assert ascii_name.endswith(DOCX_SUFFIX)


def test_한글뿐이면_날짜만_남기지_않는다():
    """「2026-08-15.docx」는 이름 구실을 못 한다 — 무슨 파일인지 알 수 없다."""
    ascii_name = build_ascii_filename("우리엔_영업 관리_2026-08-15.docx")

    assert any(char.isalpha() for char in ascii_name[: -len(DOCX_SUFFIX)]), (
        f"글자가 하나도 없는 이름입니다: {ascii_name}"
    )


def test_쓸_만한_영문이_있으면_살린다():
    """뜻이 남으면 자리표시자로 뭉개지 않는다."""
    ascii_name = build_ascii_filename("Kakao_Backend Engineer_2026-08-15.docx")

    assert "Kakao" in ascii_name
    assert "2026-08-15" in ascii_name


# ══════════════════════════════════════════════════════════
# ② 헤더 전체
# ══════════════════════════════════════════════════════════


def test_헤더는_순수_ASCII다():
    """한 글자라도 한글이 섞이면 HTTP 헤더로 못 나간다."""
    header = build_content_disposition(_사고_파일명)

    header.encode("ascii")


def test_헤더에_한글_이름이_그대로_실린다():
    """`filename*`이 있어야 최신 브라우저가 «한글 이름»으로 저장한다."""
    header = build_content_disposition(_사고_파일명)

    assert "filename*=UTF-8''" in header
    assert "%EB%A3%A8%ED%8A%B8%EB%A1%9C%EB%8B%89" in header   # 「루트로닉」


def test_따옴표가_헤더를_깨뜨리지_않는다():
    """회사명에 큰따옴표가 있으면 `filename="..."`가 중간에 끊긴다."""
    filename = build_download_filename(_보고서('삼"성', "개발"))

    header = build_content_disposition(filename)

    앞부분 = header.split("filename*=")[0]
    assert 앞부분.count('"') == 2, f"따옴표가 짝이 안 맞습니다: {앞부분}"

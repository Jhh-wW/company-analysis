"""본문 실패 시에도 검색 전송 관측을 잃거나 0회로 가장하지 않는다."""

from types import SimpleNamespace

from src.features.pipeline import real


def _collect(session):
    steps = []
    fragments = real._collect_grounded_news(
        session=session, analyze=lambda *_args: {}, fetch_text=lambda _url: None,
        corp_id="00123456", official_web_documents=0, collected_on="2026-09-08",
        steps=steps,
    )
    assert fragments == []
    return steps[-1]


def test_검색준비_결과가_없으면_검색0회로_단정하지_않는다():
    step = _collect(None)
    assert step["검색"] is None
    assert step["검색호출"] is None
    assert step["검색실제전송"] is None
    assert step["검색전송관측완료"] is False
    assert step["자료부족"] is False


def test_본문분석이_실패해도_검색의_논리호출과_실제전송을_보존한다():
    def fail(**_kwargs):
        raise RuntimeError("모의 본문 분석 실패")

    session = SimpleNamespace(
        collect=fail,
        snapshot=SimpleNamespace(
            query_attempts=(SimpleNamespace(returned_count=3),),
            transport_diagnostics={
                "검색호출": 1, "검색논리호출": 1, "검색실제전송": 2,
                "검색전송관측완료": True, "검색재시도복구": 1,
            },
        ),
    )
    step = _collect(session)
    assert step["검색"] == 3
    assert step["검색호출"] == step["검색논리호출"] == 1
    assert step["검색실제전송"] == 2
    assert step["검색재시도복구"] == 1
    assert step["검색전송관측완료"] is True
    assert step["캐시재사용가능"] is False

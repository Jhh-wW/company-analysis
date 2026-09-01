# 배포 교체(cutover) 계약

이 문서는 **운영 인스턴스를 새 버전으로 갈아끼울 때 지켜야 하는 조건**을 정의한다.
날짜 기록이 아니라 계약이다. `render.yaml`과 저장소 시험이 이 문서의 문장을 함께 고정한다.

관련 문서: [시스템 개요](system-overview.md) · [기능별 책임 지도](feature-map.md)

## 1. 왜 계약이 필요한가

이 서비스는 SQLite 한 파일과 인메모리 작업 상태를 쓴다. 옛 프로세스와 새 프로세스가
잠깐이라도 같이 살아 있으면 **두 프로세스가 같은 DB에 쓰는 순간**이 생기고, 옛 프로세스가
발급한 보고서 링크를 새 프로세스가 영구 404로 만드는 고아 출고가 발생할 수 있다.

## 2. 현재 구성이 이 문제를 막는 방식

`render.yaml`은 1GB 영속 disk와 `numInstances: 1`을 함께 고정한다. Render 공식 disk
계약상 이 구성은 zero-downtime 교체가 꺼지고 **기존 인스턴스를 완전히 멈춘 뒤
새 인스턴스를 시작**한다. 따라서 운영 순서는 다음 하나로 고정된다.

```text
graceful drain → 기존 프로세스 종료 → 새 프로세스 시작 → cutover snapshot → 새 요청 수락
```

cutover snapshot은 새 프로세스의 첫 SQLite bootstrap에서 딱 한 번 만든다.

## 3. 시험이 강제하는 것

`app/src/features/report_access/tests/test_deployment_cutover.py`가 다음을 고정한다.

- `render.yaml`의 web 서비스가 `numInstances: 1`인지
- 1GB 영속 disk가 `/var/data`에 붙어 있는지
- `maxShutdownDelaySeconds`가 **없는지** — Render는 disk가 붙은 서비스에 이 값을 거부한다.
  넣으면 Blueprint 동기화가 실패해 배포 자체가 막힌다
- 이 문서가 위 순서와 구성 변경 조건을 숨기지 않는지

## 4. 알려진 한계

플랫폼의 실제 배포 사건과 drain 완료 여부는 저장소 안에서 **확인 못 함**이다.
배포 담당자는 실행 중 조사가 0인지 확인한 뒤 배포해야 한다.

## 5. 구성을 바꾸려면 무엇을 먼저 해야 하나

앞으로 persistent disk를 없애고 **공유 DB 또는 다중 instance**로 바꾸면 §2의 전제가
사라진다. 그때는 생성 intent와 grant 결속 없이 들어오는
**bare report INSERT를 DB에서 막는 intent fence**를 먼저 구현해야 한다.
그렇지 않으면 옛 프로세스가 성공 링크를 발급한 뒤 새 프로세스가 그 링크를 영구 404로
만드는 고아 출고가 다시 생긴다.

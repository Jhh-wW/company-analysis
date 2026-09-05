> 상위 인덱스: [README.md](README.md)
> 챕터: 환경변수 시작 검증과 외부 백업 구성

## 시작 검증이 요구하는 값

`deploy/validate_environment.py`가 컨테이너 시작 전에 다음을 확인한다. 검증 오류에는
변수명만 나오고 값은 출력되지 않는다.

| 조건 | 반드시 있어야 하는 값 |
|---|---|
| 기본(`BETA_ADMIN_ONLY=1`) | `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| `PIPELINE=real` | 위에 더해 `PROVENANCE_SEAL_SECRET`(UTF-8 32바이트 이상), `ANTHROPIC_API_KEY`, `DART_API_KEY` |
| `PIPELINE=real` **이면서** `NEWS_INTAKE="1"` | 위에 더해 `NCP_APIGW_API_KEY_ID`, `NCP_APIGW_API_KEY` |
| `BACKUP_S3_BUCKET`을 설정한 경우 | 아래 「외부 백업 구성」의 값 전부 |

- NAVER API HUB 두 키는 `NEWS_INTAKE`가 **정확히 `"1"`**일 때만 필수다. 하나라도 빠지면 시작 검증이 실패한다.
- `NEWS_INTAKE`가 꺼져 있으면 두 키 없이 통과하며, 「뉴스 검색 자격 증명 없이 시작합니다」 경고 한 줄만 남긴다.
- `PROVENANCE_SEAL_SECRET`은 재배포 뒤에도 같은 값을 보존한다. 값이 바뀌면 기존 보고서가 안전하게 출고 차단된다.

| 환경변수 | 용도 |
|---|---|
| `NCP_APIGW_API_KEY_ID` | NAVER API HUB Client ID |
| `NCP_APIGW_API_KEY` | NAVER API HUB Client Secret |

실제 값은 Render 환경변수로만 주입하고 저장소·문서·로그에 남기지 않는다.

## 외부 백업 구성

`BACKUP_S3_BUCKET`을 설정하면 외부 백업 구성으로 간주한다. 이 경우
`BACKUP_TRIGGER_SECRET`, S3 전용 자격증명, `BACKUP_DATA_BOUNDARY_ID`,
`BACKUP_DATA_AUTHORITY_ID`, `BACKUP_MANIFEST_MIN_RETENTION_DAYS`가 모두 필요하다.
현재 저장소에는 production-ready `BackupManifestAppender` 구현과 앱 시작 시 provider
설치 호출이 없으므로 환경 검증은 구성된 외부 백업 배포를 의도적으로 차단한다. DB와
그 snapshot이 참조하는 불변 PDF를 한 recovery generation object set으로 저장하는 bucket,
그 bucket과 다른 권한·보존 경계의 append-only sink, signer, 최신 checkpoint 통제 경로를 구현하고
`install_manifest_appender_provider(...)`로 주입한 코드가 포함되기 전에는 이 차단을
환경 표식만으로 해제하지 않는다.

기존 manifest v1/HMAC 원장은 운영 v2로 자동 승격하지 않는다. 운영 전환 시 v2 레코드를
새 attested COMPLIANCE WORM sink에서 다시 발급하고, 별도 권한의 signed latest checkpoint에
결속해야 한다. 현재 production sink/signer/checkpoint adapter가 모두 없으므로 외부 백업과
복구 운영 판정은 BLOCKED다.

"""배포·운영용 명령행 도구.

Render 작업은 ``python -m tools.<module>``로 실행한다. 도구 간 공통 HTTP 보안 경계는
``internal_trigger``에만 두어 URL·리디렉션·응답 크기 검사가 서로 달라지지 않게 한다.
"""

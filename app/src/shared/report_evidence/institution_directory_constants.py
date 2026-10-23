"""기관 게시 계보의 닫힌 공급자·버전 계약. 회사별 등록부가 아니다."""
from typing import Final

DIRECTORY_PROVIDER_ID: Final[str] = "fintech-sandbox-enterprise"
DIRECTORY_POLICY_VERSION: Final[str] = "institution-directory-discovery-v1"
DIRECTORY_ORIGIN: Final[str] = "https://sandbox.fintech.or.kr"
DIRECTORY_SEARCH_PATH: Final[str] = "/business/enterprise_intro.do"
DIRECTORY_RECORD_PATH: Final[str] = "/business/enterprise.do"
DIRECTORY_SEARCH_CAPTION: Final[str] = "지정기업 목록"
DIRECTORY_RECORD_CAPTION: Final[str] = "기업정보"
DIRECTORY_COMPANY_LABEL: Final[str] = "기업명"
DIRECTORY_HOMEPAGE_LABEL: Final[str] = "홈페이지 주소"
DIRECTORY_INFORMATION_BASIS: Final[str] = "institution_published_company_supplied_information"
DIRECTORY_PROVENANCE_PREFIX: Final[str] = "institution-directory-url-v1:"
DIRECTORY_BINDING_PREFIX: Final[str] = "institution-directory-official-web-v1:"
DIRECTORY_MAX_AGE_DAYS: Final[int] = 1
DIRECTORY_MAX_ROWS: Final[int] = 10
DIRECTORY_MAX_PROOF_CHARS: Final[int] = 12000
DIRECTORY_MAX_PAGE_BYTES: Final[int] = 256 * 1024

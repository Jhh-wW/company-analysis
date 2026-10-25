# PDF font assets

`Freesentation-Regular.ttf`와 `Freesentation-SemiBold.ttf`는 웹에서 이미 사용하는
동명의 WOFF2를 PDF 임베딩용 TrueType으로 형식 변환한 파일입니다. PDF는 운영체제
글꼴에 기대지 않고 이 파일을 subset-embed합니다.

- Font copyright metadata: `Copyright © 2024 PT& / 피티앤`
- License: SIL Open Font License 1.1 (`OFL.txt`)
- Upstream license URL: <https://openfontlicense.org>

형식 변환된 글꼴도 OFL 1.1로 함께 배포합니다.

`NotoSansKR-Regular.ttf`는 Freesentation에 없는 한자와 그 밖의 글리프를
대체하는 Noto Sans KR Regular입니다. 파일 내부 버전은 `2.004-H2`이며,
Noto CJK Sans 2.004의 한국어 글꼴을 정적 TrueType으로 패키징한 빌드입니다.

- Font copyright metadata: `Copyright (c) 2014-2021 Adobe`, Reserved Font Name `Source`
- License: SIL Open Font License 1.1 (`OFL.txt`)
- Upstream release: <https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004>
- Upstream license: <https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE>

ReportLab 런타임에는 별도 `fontTools` 의존성을 추가하지 않고 TTF `cmap`을
읽어, 기본 글꼴에 없는 문자 구간에만 이 대체 글꼴을 적용합니다.

`NotoSansMath-Regular.ttf`는 앞의 두 글꼴에 없는 수학 기호를 보완합니다.
원문의 `∙`(U+2219 BULLET OPERATOR)를 다른 점으로 바꾸지 않고 그대로
검색 가능한 문자로 보존합니다. 기본 글꼴과 Noto Sans KR에 없는 문자에만 적용합니다.

- 저작권: `Copyright 2018 The Noto Project Authors`
- 라이선스: SIL Open Font License 1.1 (`NotoSansMath-OFL.txt`)
- 원본: <https://github.com/notofonts/noto-fonts/blob/main/hinted/ttf/NotoSansMath/NotoSansMath-Regular.ttf>
- SHA-256: `80b61fd613d3519197e64fff6f7e71fdc7f3e6526440ea4115b554ef7fd59af7`

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

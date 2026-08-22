[CmdletBinding()]
param(
    [string]$Image = "company-analysis:release-readiness",
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI를 찾을 수 없습니다."
}

& docker version --format "{{.Server.Version}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker 데몬에 연결할 수 없습니다."
}

$arguments = @(
    "build",
    "--file", (Join-Path $repositoryRoot "app\Dockerfile"),
    "--tag", $Image,
    "--provenance=false"
)
if ($NoCache) {
    $arguments += "--no-cache"
}
$arguments += $repositoryRoot

& docker @arguments
if ($LASTEXITCODE -ne 0) {
    throw "컨테이너 이미지 빌드에 실패했습니다."
}

$configuredUser = (& docker image inspect --format "{{.Config.User}}" $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $configuredUser -ne "appuser") {
    throw "이미지 기본 사용자가 appuser가 아닙니다."
}

$history = & docker history --no-trunc --format "{{.CreatedBy}}" $Image
if ($LASTEXITCODE -ne 0) {
    throw "이미지 이력을 확인하지 못했습니다."
}
if (($history -join "`n") -match "(?i)(API[_-]?KEY|SECRET|TOKEN|PASSWORD)\s*=") {
    throw "이미지 빌드 이력에서 비밀값 주입 형태를 발견했습니다."
}

Write-Host "로컬 이미지 빌드·기본 사용자·이력 검증 완료: $Image"

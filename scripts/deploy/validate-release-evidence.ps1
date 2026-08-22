[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Evidence,
    [Parameter(Mandatory = $true)]
    [string]$ScanReport,
    [Parameter(Mandatory = $true)]
    [string]$Sbom,
    [Parameter(Mandatory = $true)]
    [string]$Provenance,
    [Parameter(Mandatory = $true)]
    [string]$SignatureBundle,
    [string]$Vex = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$validator = Join-Path $repositoryRoot "deploy\validate_release_evidence.py"

$validatorArguments = @(
    $validator,
    "--evidence", $Evidence,
    "--scan-report", $ScanReport,
    "--sbom", $Sbom,
    "--provenance", $Provenance,
    "--signature-bundle", $SignatureBundle
)
if (-not [string]::IsNullOrWhiteSpace($Vex)) {
    $validatorArguments += @("--vex", $Vex)
}

& python @validatorArguments
if ($LASTEXITCODE -ne 0) {
    throw "공개 배포 이미지 공급망 증거가 BLOCKED 상태입니다."
}

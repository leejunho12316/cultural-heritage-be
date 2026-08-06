$ErrorActionPreference = "Stop"

$Root = Join-Path $HOME "dev\vca-real"
$BeRepo = "https://github.com/sukim920406-create/cultural-heritage-be-private.git"
$FeRepo = "https://github.com/sukim920406-create/cultural-heritage-fe-private.git"

$BeDir = Join-Path $Root "cultural-heritage-be-private"
$FeDir = Join-Path $Root "cultural-heritage-fe-private"
$OpenAiKey = $env:OPENAI_API_KEY
$PostgresPassword = "postgres"
$VcaAccessToken = "local-vca-token"

if ([string]::IsNullOrWhiteSpace($OpenAiKey)) {
  $OpenAiKey = "missing-openai-key"
}

function Require-Command($Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Missing required command: $Name"
  }
}

function Clone-Or-Pull($Repo, $Dir) {
  if (Test-Path $Dir) {
    Write-Host "Updating $Dir"
    git -C $Dir pull
  } else {
    Write-Host "Cloning $Repo"
    git clone $Repo $Dir
  }
}

Require-Command git
Require-Command gh
Require-Command docker
Require-Command node
Require-Command npm

Write-Host "Checking GitHub auth"
gh auth status

New-Item -ItemType Directory -Force -Path $Root | Out-Null

Clone-Or-Pull $BeRepo $BeDir
Clone-Or-Pull $FeRepo $FeDir

$BeEnvPath = Join-Path $BeDir ".env"

@"
OPENAI_API_KEY=$OpenAiKey
POSTGRES_PASSWORD=$PostgresPassword

VCA_ACCESS_TOKEN=$VcaAccessToken
VCA_RUN_MODE=real
VCA_DEVICE=auto
VCA_MAX_IMAGES=1
VCA_BOOTSTRAP_MODELS=true
VCA_SKIP_VISUAL_CUES=false
VCA_S3_OBJECT_PREFIX=vca/images

AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin-vca-20260805
AWS_S3_BUCKET=conservation-local
AWS_S3_ENDPOINT=http://minio:9000
AWS_S3_PRESIGN_ENDPOINT=http://localhost:9000
AWS_S3_PATH_STYLE_ACCESS_ENABLED=true
"@ | Set-Content -Encoding UTF8 $BeEnvPath

New-Item -ItemType Directory -Force -Path (Join-Path $BeDir "shared\vca\input-store") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BeDir "shared\vca\engine-output") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BeDir "shared\vca\document-corpus") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BeDir "shared\jobs") | Out-Null

$FeEnvPath = Join-Path $FeDir ".env"

@"
VITE_ARTIFACT_STORAGE_MODE=local
VITE_API_BASE_URL=http://localhost:8080
VITE_ARTIFACTS_API_PATH=/api/artifacts

VITE_USE_XRAY_MOCK=false
VITE_USE_GUIDE_MOCK=false
VITE_USE_VCA_MOCK=false
VITE_VCA_ACCESS_TOKEN=$VcaAccessToken

VITE_XRAY_STITCH_API_BASE=http://localhost:8080/api/xray/stitch
VITE_XRAY_INSPECTION_API_BASE=http://localhost:8001
VITE_VIA_SPRING=true
VITE_XRAY_SPRING_INSPECTION_API_BASE=http://localhost:8080/api/xray
"@ | Set-Content -Encoding UTF8 $FeEnvPath

Write-Host "Installing frontend dependencies"
Push-Location $FeDir
npm install
Pop-Location

Write-Host "Starting backend stack"
Push-Location $BeDir
docker compose up --build -d
docker compose ps
Pop-Location

Write-Host "Starting frontend"
$FrontendCmd = "Set-Location -LiteralPath `"$FeDir`"; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $FrontendCmd

Write-Host "Frontend: http://localhost:5174"
Write-Host "Backend:  http://localhost:8080"
Write-Host "MinIO:    http://localhost:9001"

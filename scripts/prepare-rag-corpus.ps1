param(
  [Parameter(Mandatory = $true)]
  [string]$SourcePath,

  [string]$TargetPath = "",

  [switch]$Clean
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if ([string]::IsNullOrWhiteSpace($TargetPath)) {
  $TargetPath = Join-Path $RepoRoot "shared\vca\document-corpus"
}

function Resolve-InputPath($Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Source path does not exist: $Path"
  }
  return (Resolve-Path -LiteralPath $Path).Path
}

function New-TemporaryWorkDir() {
  $TempRoot = [System.IO.Path]::GetTempPath()
  $Name = "vca-rag-corpus-" + [System.Guid]::NewGuid().ToString("N")
  $WorkDir = Join-Path $TempRoot $Name
  New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
  return $WorkDir
}

function Expand-SourceIfNeeded($ResolvedSource) {
  if ((Test-Path -LiteralPath $ResolvedSource -PathType Leaf) -and $ResolvedSource.EndsWith(".zip", [System.StringComparison]::OrdinalIgnoreCase)) {
    $WorkDir = New-TemporaryWorkDir
    Expand-Archive -LiteralPath $ResolvedSource -DestinationPath $WorkDir -Force
    return @{ Root = $WorkDir; Temporary = $true }
  }
  return @{ Root = $ResolvedSource; Temporary = $false }
}

function Copy-CorpusFiles($SourceRoot, $DestinationRoot) {
  $PdfFiles = Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Filter "*.pdf" | Sort-Object FullName

  if ($PdfFiles.Count -eq 0) {
    throw "No PDF files found under: $SourceRoot"
  }

  $Manifest = Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Filter "nrich_preservation_manifest.jsonl" | Select-Object -First 1
  $SeenNames = @{}

  foreach ($Pdf in $PdfFiles) {
    if ($SeenNames.ContainsKey($Pdf.Name)) {
      throw "Duplicate PDF filename detected: $($Pdf.Name). Rename files before preparing the corpus."
    }
    $SeenNames[$Pdf.Name] = $true
    Copy-Item -LiteralPath $Pdf.FullName -Destination (Join-Path $DestinationRoot $Pdf.Name)
  }

  if ($null -ne $Manifest) {
    Copy-Item -LiteralPath $Manifest.FullName -Destination (Join-Path $DestinationRoot "nrich_preservation_manifest.jsonl")
  }

  return @{ PdfCount = $PdfFiles.Count; HasManifest = $null -ne $Manifest }
}

$ResolvedSource = Resolve-InputPath $SourcePath
$ExpandedSource = Expand-SourceIfNeeded $ResolvedSource
$ResolvedTarget = $TargetPath

try {
  if ($Clean -and (Test-Path -LiteralPath $ResolvedTarget)) {
    Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
  }

  New-Item -ItemType Directory -Force -Path $ResolvedTarget | Out-Null
  $Result = Copy-CorpusFiles $ExpandedSource.Root $ResolvedTarget

  Write-Host "Prepared VCA RAG BYOD corpus"
  Write-Host "Target: $ResolvedTarget"
  Write-Host "PDF files: $($Result.PdfCount)"
  Write-Host "Manifest copied: $($Result.HasManifest)"
}
finally {
  if ($ExpandedSource.Temporary) {
    Remove-Item -LiteralPath $ExpandedSource.Root -Recurse -Force
  }
}

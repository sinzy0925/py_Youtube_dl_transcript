# YouTube 文字起こし CLI のラッパー
# CDP Chrome で YouTube を開き cookies.txt を更新してから main.py を実行する。
#
# 例: .\main.ps1 "https://youtu.be/tHshAPtdmRs"
#     .\main.ps1 -SkipCookieRefresh "https://youtu.be/tHshAPtdmRs"
#     .\main.ps1 "URL" --cookies cookies.txt   # Cookie 指定時は CDP 更新をスキップ

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$MainPy = Join-Path $Root "main.py"
$LaunchChrome = Join-Path $Root "scripts\launch_chrome_cdp.py"
$ExportCookies = Join-Path $Root "scripts\export_cookies_cdp.py"
$CookieFile = Join-Path $Root "cookies.txt"
$CdpEndpoint = if ($env:CDP_ENDPOINT) { $env:CDP_ENDPOINT.Trim() } else { "http://127.0.0.1:9222" }

if (-not (Test-Path $Python)) {
    Write-Error ".venv が見つかりません。先に venv を作成し依存関係をインストールしてください: $Python"
    exit 1
}

if (-not (Test-Path $MainPy)) {
    Write-Error "main.py が見つかりません: $MainPy"
    exit 1
}

$SkipCookieRefresh = $false
$PassArgs = [System.Collections.Generic.List[string]]::new()
foreach ($a in $args) {
    if ($a -eq "-SkipCookieRefresh" -or $a -eq "--SkipCookieRefresh") {
        $SkipCookieRefresh = $true
    } else {
        [void]$PassArgs.Add($a)
    }
}

function Test-ArgsHasCookieOption {
    param([string[]]$ArgList)
    for ($i = 0; $i -lt $ArgList.Count; $i++) {
        $a = $ArgList[$i]
        if ($a -eq "--cookies" -or $a -eq "--cookies-from-browser") { return $true }
        if ($a -like "--cookies=*" -or $a -like "--cookies-from-browser=*") { return $true }
    }
    return $false
}

function Get-FirstYouTubeUrl {
    param([string[]]$ArgList)
    foreach ($a in $ArgList) {
        if ($a -match '^https?://') {
            if ($a -match 'youtube\.com|youtu\.be') { return $a }
        }
    }
    foreach ($a in $ArgList) {
        if ($a -match '^https?://') { return $a }
    }
    return $null
}

function Wait-YouTubeCdpTab {
    param(
        [string]$Endpoint,
        [int]$TimeoutSec = 120
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $base = $Endpoint.TrimEnd("/")
    while ((Get-Date) -lt $deadline) {
        try {
            $pages = Invoke-RestMethod -Uri "$base/json/list" -TimeoutSec 5
            foreach ($p in $pages) {
                $u = [string]$p.url
                if ($u -match 'youtube\.com|youtu\.be') {
                    return $true
                }
            }
        } catch {
            # CDP 未準備
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Test-CookieFileHasYouTubeAuth {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    $text = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if (-not $text) { return $false }
    return ($text -match "`tLOGIN_INFO`t") -or ($text -match "`tSID`t") -or ($text -match "`t__Secure-1PSID`t")
}

$shouldRefreshCookies = -not $SkipCookieRefresh -and -not (Test-ArgsHasCookieOption -ArgList $PassArgs)

if ($shouldRefreshCookies) {
    if (-not (Test-Path $LaunchChrome)) {
        Write-Error "launch_chrome_cdp.py が見つかりません: $LaunchChrome"
        exit 1
    }
    if (-not (Test-Path $ExportCookies)) {
        Write-Error "export_cookies_cdp.py が見つかりません: $ExportCookies"
        exit 1
    }

    $startUrl = Get-FirstYouTubeUrl -ArgList $PassArgs
    if (-not $startUrl) {
        $startUrl = "https://www.youtube.com/"
    }

    Write-Host "[1/4] CDP Chrome を起動します (URL: $startUrl)"
    & $Python $LaunchChrome --url $startUrl
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[2/4] YouTube タブの表示を待ちます (最大 120 秒)..."
    if (-not (Wait-YouTubeCdpTab -Endpoint $CdpEndpoint -TimeoutSec 120)) {
        Write-Error "CDP 上に YouTube タブが現れませんでした。Chrome / プロファイルを確認してください。"
        exit 1
    }
    Start-Sleep -Seconds 3

    Write-Host "[3/4] cookies.txt を CDP から書き出します..."
    & $Python $ExportCookies --out $CookieFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not (Test-CookieFileHasYouTubeAuth -Path $CookieFile)) {
        Write-Warning @"
YouTube ログイン Cookie (LOGIN_INFO 等) が cookies.txt にありません。
CDP プロファイルで YouTube にログインしてから再実行してください。
（初回のみ手動ログインが必要な場合があります）
"@
    }

    Write-Host "[4/4] main.py を実行します (--cookies $CookieFile)"
    & $Python $MainPy @PassArgs --cookies $CookieFile
} else {
    if ($SkipCookieRefresh) {
        Write-Host "Cookie の CDP 更新をスキップします (-SkipCookieRefresh)。"
    } else {
        Write-Host "Cookie オプション指定のため CDP 更新をスキップします。"
    }
    & $Python $MainPy @PassArgs
}

exit $LASTEXITCODE

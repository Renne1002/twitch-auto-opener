param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,
    [string]$OutputDir,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingOutputDirParts
)

$ErrorActionPreference = 'Stop'

function Resolve-PythonCommand {
    $wingetCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'),
        'C:\Users\dtl13\AppData\Local\Microsoft\WindowsApps\winget.exe'
    )

    $winget = $null
    foreach ($w in $wingetCandidates) {
        if (Test-Path $w) {
            $winget = $w
            break
        }
    }

    $pythonCandidates = @(
        'python3',
        'python',
        'py -3',
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        'C:\Users\dtl13\AppData\Local\Programs\Python\Python312\python.exe',
        'C:\Python311\python.exe',
        'C:\Program Files\Streamlink\Python\python.exe'
    )

    $resolved = $null
    foreach ($candidate in $pythonCandidates) {
        if (Test-Path $candidate) {
            $resolved = $candidate
            break
        }

        if ($candidate.Contains(' ')) {
            $parts = $candidate.Split(' ', 2)
            $cmd = Get-Command $parts[0] -ErrorAction SilentlyContinue
            if ($cmd) {
                $resolved = $candidate
                break
            }
            continue
        }

        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $resolved = $candidate
            break
        }
    }

    if ($resolved) {
        return $resolved
    }

    if (-not $winget) {
        throw 'python3/python/py was not found and winget is unavailable.'
    }

    & $winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity

    $postInstallCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        'C:\Users\dtl13\AppData\Local\Programs\Python\Python312\python.exe',
        'python3',
        'python',
        'py -3'
    )

    foreach ($candidate in $postInstallCandidates) {
        if (Test-Path $candidate) {
            return $candidate
        }

        if ($candidate.Contains(' ')) {
            $parts = $candidate.Split(' ', 2)
            $cmd = Get-Command $parts[0] -ErrorAction SilentlyContinue
            if ($cmd) {
                return $candidate
            }
            continue
        }

        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            return $candidate
        }
    }

    throw 'Python could not be resolved even after winget installation.'
}

function Invoke-PythonModule {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCommand,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($PythonCommand -eq 'py -3') {
        & py -3 @Arguments
        return
    }

    & $PythonCommand @Arguments
}

Set-Location $RepoPath

if ($RemainingOutputDirParts -and $RemainingOutputDirParts.Count -gt 0) {
    if ([string]::IsNullOrWhiteSpace($OutputDir)) {
        $OutputDir = ($RemainingOutputDirParts -join ' ')
    }
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = $RepoPath
}

$python = Resolve-PythonCommand

if (-not (Test-Path $OutputDir)) {
    New-Item -Path $OutputDir -ItemType Directory -Force | Out-Null
}

$OutputFile = Join-Path $OutputDir 'TwitchAutoOpener.exe'

Invoke-PythonModule -PythonCommand $python -Arguments @('-m', 'ensurepip', '--upgrade')
Invoke-PythonModule -PythonCommand $python -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-PythonModule -PythonCommand $python -Arguments @('-m', 'pip', 'install', '-e', '.')
Invoke-PythonModule -PythonCommand $python -Arguments @('-m', 'pip', 'install', 'pyinstaller')

# config.toml is loaded at runtime from file path; it is not embedded into the exe.
Invoke-PythonModule -PythonCommand $python -Arguments @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--name', 'TwitchAutoOpener',
    'src/twitch_auto_opener/main.py'
)

Copy-Item -Path 'dist\TwitchAutoOpener.exe' -Destination $OutputFile -Force

$debugLauncher = Join-Path $OutputDir 'run_twitch_auto_opener_debug.cmd'
$debugContent = @'
@echo off
setlocal
cd /d "%~dp0"

echo Running TwitchAutoOpener.exe with config.toml from this folder...
"%~dp0TwitchAutoOpener.exe" --config "%~dp0config.toml" 1>"%~dp0run_stdout.log" 2>"%~dp0run_stderr.log"
set "exit_code=%ERRORLEVEL%"

echo.
echo ExitCode: %exit_code%
echo.
if exist "%~dp0run_stderr.log" (
  echo --- stderr ---
  type "%~dp0run_stderr.log"
)
if exist "%~dp0run_stdout.log" (
  echo --- stdout ---
  type "%~dp0run_stdout.log"
)

echo.
pause
'@
Set-Content -Path $debugLauncher -Value $debugContent -Encoding ASCII

Write-Host ('Built exe: ' + $OutputFile)
Write-Host ('Debug launcher: ' + $debugLauncher)

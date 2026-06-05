param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,
    [string]$OutputDir,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingOutputDirParts
)

$ErrorActionPreference = 'Stop'

function Test-PythonCommandCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate
    )

    try {
        if ($Candidate -eq 'py -3') {
            & py -3 --version *> $null
            return ($LASTEXITCODE -eq 0)
        }

        if ($Candidate.Contains(' ') -and -not (Test-Path $Candidate)) {
            $parts = $Candidate.Split(' ', 2)
            if ($parts.Count -eq 2) {
                & $parts[0] $parts[1] --version *> $null
            }
            else {
                & $parts[0] --version *> $null
            }
            return ($LASTEXITCODE -eq 0)
        }

        & $Candidate --version *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Resolve-PythonCommand {
    $userProfile = $env:USERPROFILE
    $localAppData = $env:LOCALAPPDATA

    $wingetCandidates = @(
        (Join-Path $localAppData 'Microsoft\WindowsApps\winget.exe')
    )

    if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
        $wingetCandidates += (Join-Path $userProfile 'AppData\Local\Microsoft\WindowsApps\winget.exe')
    }

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
        (Join-Path $localAppData 'Programs\Python\Python312\python.exe'),
        'C:\Python311\python.exe',
        'C:\Program Files\Streamlink\Python\python.exe'
    )

    if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
        $pythonCandidates += (Join-Path $userProfile 'AppData\Local\Programs\Python\Python312\python.exe')
    }

    foreach ($candidate in $pythonCandidates) {
        if (Test-PythonCommandCandidate -Candidate $candidate) {
            return $candidate
        }
    }

    if (-not $winget) {
        throw 'python3/python/py was not found and winget is unavailable.'
    }

    & $winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "winget python install failed with exit code $LASTEXITCODE"
    }

    $postInstallCandidates = @(
        (Join-Path $localAppData 'Programs\Python\Python312\python.exe'),
        'python3',
        'python',
        'py -3'
    )

    if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
        $postInstallCandidates += (Join-Path $userProfile 'AppData\Local\Programs\Python\Python312\python.exe')
    }

    foreach ($candidate in $postInstallCandidates) {
        if (Test-PythonCommandCandidate -Candidate $candidate) {
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
        if ($LASTEXITCODE -ne 0) {
            throw "Python command failed: py -3 $($Arguments -join ' ') (exit code=$LASTEXITCODE)"
        }
        return
    }

    & $PythonCommand @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $PythonCommand $($Arguments -join ' ') (exit code=$LASTEXITCODE)"
    }
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

$distExe = Join-Path (Join-Path $RepoPath 'dist') 'TwitchAutoOpener.exe'
if (-not (Test-Path $distExe)) {
    throw "PyInstaller output not found: $distExe"
}

Copy-Item -Path $distExe -Destination $OutputFile -Force

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

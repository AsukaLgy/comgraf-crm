param([switch]$ForceDependencies)

$ErrorActionPreference = 'Stop'
$ComgrafRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ComgrafRoot

function Test-TcpPort([int]$Port) {
    $Client = [System.Net.Sockets.TcpClient]::new()
    try { $Client.Connect('127.0.0.1', $Port); return $true }
    catch { return $false }
    finally { $Client.Dispose() }
}

if (-not (Test-Path -LiteralPath '.env')) { throw 'Falta .env. Cópialo desde .env.example y configura credenciales antes de actualizar.' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Falta .venv. Consulta la instalación Windows en README.md.' }
if (Test-TcpPort 8000) { throw 'Comgraf sigue ejecutándose en el puerto 8000. Detén con Ctrl+C su terminal start-local.ps1, vuelve a ejecutar esta actualización y luego inicia el sistema.' }

$Runtime = Join-Path $ComgrafRoot '.runtime'
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$MySQLBase = Get-ChildItem -LiteralPath $Runtime -Directory -Filter 'mysql-*-winx64' | Select-Object -First 1
if (-not $MySQLBase) { throw 'No se encuentra MySQL portátil. Usa Docker Compose según README.md.' }
if (-not (Test-TcpPort 3307)) {
    $MySQLArguments = @('--basedir=' + $MySQLBase.FullName, '--datadir=' + (Join-Path $Runtime 'mysql-data'), '--bind-address=127.0.0.1', '--port=3307', '--mysqlx=OFF', '--log-error=' + (Join-Path $Runtime 'mysql.log'))
    Start-Process -FilePath (Join-Path $MySQLBase.FullName 'bin/mysqld.exe') -ArgumentList $MySQLArguments -WindowStyle Hidden
    for ($Attempt = 0; $Attempt -lt 60 -and -not (Test-TcpPort 3307); $Attempt++) { Start-Sleep -Seconds 1 }
}
if (-not (Test-TcpPort 3307)) { throw 'MySQL no quedó disponible en 127.0.0.1:3307. Revisa .runtime/mysql.log; no se modificaron ni borraron los datos.' }

$LockFiles = @('backend/requirements.lock.txt', 'backend/requirements-dev.txt', 'frontend/package-lock.json')
$Fingerprint = ($LockFiles | ForEach-Object { (Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash }) -join ':'
$FingerprintPath = Join-Path $Runtime 'dependency-fingerprint.txt'
$PreviousFingerprint = if (Test-Path -LiteralPath $FingerprintPath) { Get-Content -Raw -LiteralPath $FingerprintPath } else { '' }
$DependenciesChanged = $ForceDependencies -or ($PreviousFingerprint.Trim() -ne $Fingerprint) -or -not (Test-Path -LiteralPath 'frontend/node_modules')
if ($DependenciesChanged) {
    & '.venv/Scripts/python.exe' -m pip install -r backend/requirements.lock.txt -r backend/requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falló la actualización de dependencias Python.' }
    & 'npm.cmd' ci --prefix frontend
    if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación de dependencias del frontend.' }
    Set-Content -LiteralPath $FingerprintPath -Value $Fingerprint -NoNewline
}

& 'npm.cmd' --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Falló la compilación del frontend; no continúes con el arranque.' }
Push-Location -LiteralPath (Join-Path $ComgrafRoot 'backend')
try {
    & (Join-Path $ComgrafRoot '.venv/Scripts/python.exe') -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Falló la migración MySQL. Conserva la base y revisa el mensaje antes de iniciar Comgraf.' }
} finally { Pop-Location }

Write-Host 'Actualización terminada: dependencias verificadas, frontend compilado y migraciones aplicadas.'
Write-Host 'No se borraron la base MySQL, los adjuntos ni la configuración.'
Write-Host 'Inicia o reinicia Comgraf con: powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1'

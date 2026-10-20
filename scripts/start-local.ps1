$ErrorActionPreference = 'Stop'
$ComgrafRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ComgrafRoot
function Test-TcpPort([int]$Port) {
    $Client = [System.Net.Sockets.TcpClient]::new()
    try { $Client.Connect('127.0.0.1', $Port); return $true }
    catch { return $false }
    finally { $Client.Dispose() }
}
$MySQLBase = Get-ChildItem -LiteralPath (Join-Path $ComgrafRoot '.runtime') -Directory -Filter 'mysql-*-winx64' | Select-Object -First 1
if (-not $MySQLBase) { throw 'No se encuentra MySQL portátil. Usa Docker Compose según README.' }
if (Test-TcpPort 8000) { throw 'El puerto 8000 ya está ocupado. Detén la aplicación anterior antes de iniciar otra.' }
if (-not (Test-TcpPort 3307)) {
    $MySQLArguments = @('--basedir=' + $MySQLBase.FullName, '--datadir=' + (Join-Path $ComgrafRoot '.runtime/mysql-data'), '--bind-address=127.0.0.1', '--port=3307', '--mysqlx=OFF', '--log-error=' + (Join-Path $ComgrafRoot '.runtime/mysql.log'))
    Start-Process -FilePath (Join-Path $MySQLBase.FullName 'bin/mysqld.exe') -ArgumentList $MySQLArguments -WindowStyle Hidden
}
for ($Attempt = 0; $Attempt -lt 60 -and -not (Test-TcpPort 3307); $Attempt++) { Start-Sleep -Seconds 1 }
if (-not (Test-TcpPort 3307)) { throw 'MySQL no quedó disponible en 127.0.0.1:3307. Revisa .runtime/mysql.log.' }
Set-Location -LiteralPath (Join-Path $ComgrafRoot 'backend')
$env:APP_HOST = '127.0.0.1'
$env:APP_WORKERS = '1'
& (Join-Path $ComgrafRoot '.venv/Scripts/python.exe') -m app.start

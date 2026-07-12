@echo off
REM run.bat - One-click run for the I/O Anomaly Detection experiment.
REM
REM Prerequisites: Docker Desktop must be installed and running.
REM This script builds the container, runs the full experiment,
REM and displays the results.

echo ============================================
echo  I/O Anomaly Detector - Project Runner
echo ============================================
echo.

REM Check if docker is available
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker is not installed or not in PATH.
    echo         Install Docker Desktop from https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

REM Check if Docker daemon is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker Desktop is not running.
    echo         Please start Docker Desktop and wait for it to finish loading.
    pause
    exit /b 1
)

echo [OK] Docker is running.
echo.
echo [*] Building container and running experiment...
echo     This will take a few minutes on first run (downloads Ubuntu image).
echo     Subsequent runs are much faster (cached layers).
echo.

REM Run the experiment
docker-compose up --build

echo.
echo ============================================
echo  Results
echo ============================================
echo.

REM Show alert summary
if exist logs\alerts.json (
    echo [*] Alert summary from logs\alerts.json:
    echo.
    python -c "import json; alerts=json.load(open('logs/alerts.json')); types={}; [types.update({a['type']: types.get(a['type'],0)+1}) for a in alerts]; print(f'  Total alerts: {len(alerts)}'); [print(f'    {k}: {v}') for k,v in sorted(types.items())]" 2>nul
    if %errorlevel% neq 0 (
        echo     (Install Python to see alert summary, or open logs\alerts.json directly)
    )
) else (
    echo [!] No alerts.json found. Something may have gone wrong.
)

echo.
echo [*] Full results are in:
echo     logs\alerts.json         - Structured alert data
echo     logs\anomaly_detector.log - Full monitoring trace
echo.
echo [*] To view the full container output again:
echo     docker logs io_anomaly_detector
echo.
pause

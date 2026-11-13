@echo off
rem Manual refresh of the 50%% dataset: scrape -> rebuild docs/ -> publish.
rem Optional argument = delay between requests in seconds (default 0.45).
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=py"
set "DELAY=%~1"
if "%DELAY%"=="" set "DELAY=0.45"

echo [1/2] scraping the most relevant 50%% of every brand (delay %DELAY%s, ~35 min)...
"%PY%" -m scripts.scrape_catalog --share 0.5 --delay %DELAY%
if errorlevel 1 goto error

echo.
echo [2/2] rebuilding docs/ ...
"%PY%" "%ROOT%scripts\build-static.py" --force
if errorlevel 1 goto error

echo.
echo Done. Upload these to GitHub (Add file - Upload files):
echo   data\cars.json.gz
echo   docs\   (whole folder)
start "" explorer /select,"%ROOT%data\cars.json.gz"
pause
exit /b 0

:error
echo.
echo FAILED - see the messages above.
pause
exit /b 1

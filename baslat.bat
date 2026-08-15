@echo off
setlocal enabledelayedexpansion

rem ===================================================================
rem  CNC Log System - Windows baslatici
rem
rem  Kullanim (CMD veya PowerShell):
rem      baslat.bat                  normal calistir
rem      baslat.bat --tara           tezgahi agda ara
rem      baslat.bat --test-baglanti  baglanmayi dene
rem      baslat.bat --rapor bugun    gun raporu
rem      baslat.bat --python-bilgi   hangi Python kullanilacak
rem
rem  Windows'ta esas kullanim amaci: TNC 640 Programming Station
rem  (simulator) ile programi denemek. Tezgahin kendisinde Linux
rem  tarafi kullanilir, orada baslat.sh calisir.
rem
rem  Sisteme hicbir sey KURULMAZ. Python gerekiyorsa pakete gomulu
rem  tasinabilir surum bir klasore acilir.
rem ===================================================================

rem Turkce karakterlerin konsolda dogru gorunmesi icin UTF-8 kod sayfasi.
chcp 65001 >nul 2>&1

cd /d "%~dp0"
set "KLASOR=%CD%"
set "GOMULU=%KLASOR%\cnclog\vendor\python"
set "PY="

rem --- 1) Sistemde Python 3.7+ var mi? -------------------------------
rem Not: 'call :LABEL && set' kalibi bazi Windows surumlerinde beklendigi
rem gibi davranmaz; errorlevel acikca kontrol edilir.
for %%C in (python.exe python3.exe) do (
    if not defined PY (
        for /f "delims=" %%P in ('where %%C 2^>nul') do (
            if not defined PY (
                call :SURUM_UYGUN "%%P"
                if not errorlevel 1 set "PY=%%P"
            )
        )
    )
)
if not defined PY (
    where py.exe >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
            if not defined PY (
                call :SURUM_UYGUN "%%P"
                if not errorlevel 1 set "PY=%%P"
            )
        )
    )
)

rem --- 2) Daha once acilmis gomulu Python var mi? --------------------
if not defined PY (
    for %%K in ("%GOMULU%\rt" "%USERPROFILE%\.cnclog\python") do (
        if not defined PY (
            if exist "%%~K\python\python.exe" (
                call :SURUM_UYGUN "%%~K\python\python.exe"
                if not errorlevel 1 set "PY=%%~K\python\python.exe"
            )
        )
    )
)

rem --- 3) Arsivden ac ------------------------------------------------
if not defined PY (
    set "ARSIV=%GOMULU%\cpython-windows.tar.gz"
    if not exist "!ARSIV!" (
        echo HATA: Gomulu Python arsivi bulunamadi:
        echo       !ARSIV!
        echo       Paketin eksiksiz kopyalandigindan emin olun.
        goto :HATA
    )
    where tar.exe >nul 2>&1
    if errorlevel 1 (
        echo HATA: 'tar' komutu bulunamadi.
        echo       Windows 10 1803 ve sonrasinda tar hazir gelir.
        echo       Alternatif: arsivi elle acin ^(7-Zip ile^):
        echo         !ARSIV!
        echo       icindeki python klasorunu suraya cikarin:
        echo         %GOMULU%\rt\
        goto :HATA
    )

    set "HEDEF=%GOMULU%\rt"
    if not exist "!HEDEF!" mkdir "!HEDEF!" 2>nul
    if not exist "!HEDEF!" set "HEDEF=%USERPROFILE%\.cnclog\python"
    if not exist "!HEDEF!" mkdir "!HEDEF!" 2>nul
    if not exist "!HEDEF!" (
        echo HATA: Python'u acacak yazilabilir bir klasor bulunamadi.
        goto :HATA
    )

    echo Python 3 hazirlaniyor... ilk calistirmada bir defa yapilir.
    if exist "!HEDEF!\python" rmdir /s /q "!HEDEF!\python" 2>nul
    tar -xzf "!ARSIV!" -C "!HEDEF!" 2>nul
    if exist "!HEDEF!\python\python.exe" (
        call :SURUM_UYGUN "!HEDEF!\python\python.exe"
        if not errorlevel 1 set "PY=!HEDEF!\python\python.exe"
    )
    if defined PY (
        echo Hazir: !PY!
    ) else (
        echo Gomulu Python acildi ama calistirilamadi.
    )
)

rem --- 4) Hicbiri olmadi ---------------------------------------------
if not defined PY (
    echo ============================================================
    echo  HATA: Calisir bir Python 3 bulunamadi.
    echo.
    echo  Sistemde Python 3.7+ yok ve gomulu surum de acilamadi.
    echo  Su komutu calistirip ciktisini gonderin:
    echo      baslat.bat --python-bilgi
    echo ============================================================
    goto :HATA
)

if "%~1"=="--python-bilgi" (
    echo Kullanilacak Python: %PY%
    "%PY%" --version
    echo Program klasoru    : %KLASOR%
    goto :SON
)

rem Python'un kendi ciktisini da UTF-8 yapar; Windows konsolu varsayilan
rem olarak cp1254 kullanir ve Turkce karakterler bozulur.
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

"%PY%" -m cnclog %*
goto :SON

rem -------------------------------------------------------------------
:SURUM_UYGUN
rem %1 = python yolu. Surum ve modul kontrolu; uygunsa errorlevel 0.
"%~1" -c "import sys;(sys.version_info>=(3,7)) or sys.exit(1);import sqlite3,socket,json,threading,configparser,csv,http.server" >nul 2>&1
exit /b %errorlevel%

:HATA
endlocal
exit /b 1

:SON
endlocal

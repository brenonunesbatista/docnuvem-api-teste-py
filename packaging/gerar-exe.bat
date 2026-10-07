@echo off
rem Gera dist\docnuvem-web.exe (um arquivo so, sem precisar instalar Python na maquina de destino).
chcp 65001 >nul
cd /d "%~dp0\.."
python -m pip install -q pyinstaller httpx
if errorlevel 1 goto erro
rem O caminho do --add-data precisa ser absoluto: o PyInstaller o resolve a partir do --specpath.
python -m PyInstaller --noconfirm --onefile --name docnuvem-web ^
  --paths src ^
  --add-data "%CD%\src\docnuvem_tester\webapp;docnuvem_tester\webapp" ^
  --distpath dist --workpath build\pyinstaller --specpath build\pyinstaller ^
  packaging\launcher.py
if errorlevel 1 goto erro
echo.
echo Pronto: dist\docnuvem-web.exe
echo Coloque o config.json na mesma pasta do .exe (veja o README) e de dois cliques nele.
pause
exit /b 0
:erro
echo.
echo Falhou. Veja a mensagem acima.
pause
exit /b 1

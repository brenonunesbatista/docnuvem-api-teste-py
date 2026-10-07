@echo off
rem Inicia o Docnuvem API Tester. Dê dois cliques neste arquivo.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
echo Iniciando o Docnuvem API Tester...
echo (feche esta janela ou pressione Ctrl+C para encerrar)
echo.
python -m docnuvem_tester.web
echo.
pause

@echo off
title Monitor Wall Street ELT + Orquestador DAG (1928 - 2031)
echo ============================================================
echo   INICIANDO MONITOR WALL STREET ELT + DAG (1928 - 2031)
echo ============================================================

if not exist venv (
    echo [1/3] Creando entorno virtual Python...
    python -m venv venv
)

echo [2/3] Activando entorno e instalando dependencias...
call venv\Scripts\activate.bat
pip install -q -r requirements.txt

echo [3/3] Levantando servidor Web en http://127.0.0.1:5000 ...
start http://127.0.0.1:5000
python app.py
pause

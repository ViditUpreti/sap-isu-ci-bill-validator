@echo off
title SAP IS-U & CI Automated Bill Validator - Streamlit Dashboard
cd /d "%~dp0"
echo =====================================================================
echo Launching SAP IS-U & CI Bill Print Validator Dashboard (Streamlit)...
echo =====================================================================
"C:\pinokio\bin\miniconda\python.exe" -m streamlit run app.py
pause

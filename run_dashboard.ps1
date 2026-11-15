# PowerShell launcher for Streamlit Dashboard
Set-Location $PSScriptRoot
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "Launching SAP IS-U & CI Bill Print Validator Dashboard (Streamlit)..." -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Cyan
& "C:\pinokio\bin\miniconda\python.exe" -m streamlit run app.py

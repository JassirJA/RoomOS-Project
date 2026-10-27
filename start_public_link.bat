@echo off
title Room OS - Public Web Tunnel
echo ===================================================
echo Starting Public Cloudflare Tunnel for Room OS...
echo Pointing to http://localhost:8050
echo ===================================================
.\tools\cloudflared.exe tunnel --url http://localhost:8050
pause

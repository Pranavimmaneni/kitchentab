@echo off
rem Double-click: connects the KitchenTab Gmail to https://kitchentab.vercel.app (scripts/deploy/set_email.py).
rem It asks for the Gmail address and its app password (typing is hidden), redeploys, then emails a
rem code to that Gmail for you to type in. Add --provider other --smtp-host ... for a non-Gmail relay.
cd /d "%~dp0.."
set KT_VERCEL_ENVIRONMENTS=production
"apps\api\.venv\Scripts\python.exe" scripts\deploy\set_email.py %*
echo.
pause

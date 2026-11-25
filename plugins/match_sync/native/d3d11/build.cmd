@echo off
setlocal
set "source=%~dp0"
if not "%VSCMD_ARG_TGT_ARCH%"=="x64" (
    call "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
    if errorlevel 1 exit /b 1
)
cl /nologo /std:c++17 /EHsc /W4 /WX /O2 /MT "%source%presenter.cpp" /Fe:"%source%presenter.exe" d3d11.lib dxgi.lib user32.lib
if errorlevel 1 exit /b 1
cl /nologo /LD /W4 /WX /O2 /MT "%source%mailbox.cpp" /Fe:"%source%mailbox.dll" /link /IMPLIB:mailbox.lib

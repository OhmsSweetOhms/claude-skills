@echo off
rem jtag_erase_reflash.bat — THIN Windows convenience wrapper for jtag_erase_reflash.tcl.
rem The TCL is the product (identical invocation on Windows/Linux once xsdb is on PATH);
rem this wrapper only puts xsdb on PATH and forwards every argument untouched.
rem
rem   jtag_erase_reflash.bat erase        --ps7 C:\path\ps7_init.tcl
rem   jtag_erase_reflash.bat erase+flash  C:\path\boot.mcs --ps7 C:\path\ps7_init.tcl
rem
rem Toolchain pick: the ZB_VITIS env var wins (e.g. C:\Xilinx\Vitis\2022.2), else the
rem newest install under C:\Xilinx\Vitis. If xsdb is ALREADY on PATH, nothing is sourced.
setlocal enabledelayedexpansion

where xsdb >nul 2>nul
if %errorlevel%==0 goto run

set "VITIS=%ZB_VITIS%"
if "%VITIS%"=="" (
    rem Directories list in name order; the last one iterated is the newest version.
    for /d %%D in (C:\Xilinx\Vitis\*) do set "VITIS=%%D"
)
if "%VITIS%"=="" (
    echo Vitis not found; set ZB_VITIS or run settings64.bat first >&2
    exit /b 1
)
call "%VITIS%\settings64.bat"

:run
xsdb "%~dp0jtag_erase_reflash.tcl" %*
exit /b %errorlevel%

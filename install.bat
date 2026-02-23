@echo off
REM mememo installer for Windows
REM
REM Usage:
REM   install.bat              # Install for production
REM   install.bat --dev        # Install with dev/test dependencies
REM   install.bat --upgrade    # Upgrade existing installation
REM   install.bat --uninstall  # Uninstall mememo
REM

setlocal enabledelayedexpansion

set VERSION=0.1.0
set VENV_DIR=.venv
set PYTHON_MIN=3.9

REM Parse arguments
set MODE=production
set ACTION=install
set AUTO_CONFIGURE=

:parse_args
if "%~1"=="" goto end_parse
if "%~1"=="--dev" set MODE=dev
if "%~1"=="--upgrade" set ACTION=upgrade
if "%~1"=="--uninstall" set ACTION=uninstall
if "%~1"=="--configure" set AUTO_CONFIGURE=claude
if "%~1"=="--configure" if not "%~2"=="" set AUTO_CONFIGURE=%~2
if "%~1"=="--configure=claude" set AUTO_CONFIGURE=claude
if "%~1"=="--configure=claudecli" set AUTO_CONFIGURE=claudecli
if "%~1"=="--help" goto show_help
shift
goto parse_args
:end_parse

echo ======================================
echo mememo v%VERSION% Installer
echo ======================================
echo.

if "%ACTION%"=="install" goto do_install
if "%ACTION%"=="upgrade" goto do_upgrade
if "%ACTION%"=="uninstall" goto do_uninstall
goto end

:do_install
    echo [INFO] Checking Python version...
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found. Install Python %PYTHON_MIN%+
        exit /b 1
    )

    echo [INFO] Creating virtual environment...
    if exist "%VENV_DIR%" (
        echo [INFO] Virtual environment exists at %VENV_DIR%
    ) else (
        python -m venv %VENV_DIR%
        echo [OK] Virtual environment created
    )

    echo [INFO] Activating virtual environment...
    call %VENV_DIR%\Scripts\activate.bat

    echo [INFO] Upgrading pip...
    python -m pip install --upgrade pip

    if "%MODE%"=="dev" goto install_dev
    goto install_prod

:install_dev
    echo [INFO] Installing mememo v%VERSION% with dev dependencies...
    pip install -e ".[dev]"
    if errorlevel 1 (
        echo [ERROR] Installation failed
        exit /b 1
    )
    echo [OK] mememo installed with dev/test tools
    goto install_done

:install_prod
    echo [INFO] Installing mememo v%VERSION% (production)...
    pip install -e .
    if errorlevel 1 (
        echo [ERROR] Installation failed
        exit /b 1
    )
    echo [OK] mememo installed
    goto install_done

:install_done
    REM Auto-configure MCP if requested
    if "%AUTO_CONFIGURE%"=="" goto configure_tip
    if /i "%AUTO_CONFIGURE%"=="claude" goto do_configure_claude
    if /i "%AUTO_CONFIGURE%"=="claudecli" goto do_configure_claudecli
    echo [ERROR] AI assistant '%AUTO_CONFIGURE%' not yet supported
    echo [INFO] Supported: claude, claudecli
    echo [INFO] Configure manually (see README.md)
    goto show_complete

:configure_tip
    echo [INFO] Tip: Run 'install.bat --configure=claude' to auto-configure Claude Desktop
    echo [INFO]      Run 'install.bat --configure=claudecli' to auto-configure Claude Code CLI
    goto show_complete

:do_configure_claude
    echo.
    call :configure_mcp
    goto show_complete

:do_configure_claudecli
    echo.
    call :configure_claude_cli
    goto show_complete

:show_complete
    echo.
    echo ======================================
    echo Installation Complete!
    echo ======================================
    echo.

    if "%MODE%"=="dev" goto show_dev_msg
    if "%AUTO_CONFIGURE%"=="" goto show_not_configured
    if /i "%AUTO_CONFIGURE%"=="claudecli" goto show_claudecli_done
    goto show_claude_done

:show_dev_msg
    echo Dev environment ready:
    echo.
    echo   1. Activate venv:
    echo      %VENV_DIR%\Scripts\activate.bat
    echo.
    echo   2. Run tests:
    echo      pytest tests/ -v
    echo.
    echo   3. Configure your AI assistant (if not done):
    echo      install.bat --configure=claude      (Claude Desktop)
    echo      install.bat --configure=claudecli   (Claude Code CLI)
    echo.
    goto end

:show_not_configured
    echo   mememo is installed but not yet connected to an AI assistant.
    echo   Run: install.bat --configure=claude      (Claude Desktop)
    echo        install.bat --configure=claudecli   (Claude Code CLI)
    echo.
    echo   See README.md for usage and configuration options.
    echo.
    goto end

:show_claudecli_done
    echo   mememo is ready in Claude Code CLI.
    echo   Verify with: claude mcp list
    echo.
    echo   See README.md for usage and configuration options.
    echo.
    goto end

:show_claude_done
    echo   mememo is ready. Restart Claude Desktop to activate.
    echo.
    echo   See README.md for usage and configuration options.
    echo.
    goto end

:configure_claude_cli
    echo [INFO] Configuring Claude Code CLI MCP server...

    set PROJECT_DIR=%CD%
    set PYTHON_PATH=%PROJECT_DIR%\%VENV_DIR%\Scripts\python.exe

    if not exist "%PYTHON_PATH%" (
        echo [ERROR] Python not found at %PYTHON_PATH%
        goto :eof
    )

    where claude >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Claude CLI not found. Install it first: https://claude.ai/download
        goto :eof
    )

    claude mcp add --scope user mememo -- "%PYTHON_PATH%" -m mememo
    if errorlevel 1 (
        echo [WARN] Auto-configuration failed. Add manually:
        echo        claude mcp add --scope user mememo -- "%PYTHON_PATH%" -m mememo
    ) else (
        echo [OK] Claude CLI MCP server configured (user scope - available in all projects)!
        echo [INFO] Verify with: claude mcp list
    )

    goto :eof

:configure_mcp
    echo [INFO] Configuring Claude Desktop MCP server...

    REM Get absolute paths
    set PROJECT_DIR=%CD%
    set PYTHON_PATH=%PROJECT_DIR%\%VENV_DIR%\Scripts\python.exe
    set CONFIG_DIR=%APPDATA%\Claude
    set CONFIG_FILE=%CONFIG_DIR%\claude_desktop_config.json

    REM Check if Python exists
    if not exist "%PYTHON_PATH%" (
        echo [ERROR] Python not found at %PYTHON_PATH%
        goto :eof
    )

    REM Create config directory
    if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"

    REM Create or update config using Python
    "%PYTHON_PATH%" -c "import json; import os; config_file = r'%CONFIG_FILE%'; config = json.load(open(config_file)) if os.path.exists(config_file) else {}; config.setdefault('mcpServers', {})['mememo'] = {'command': r'%PYTHON_PATH%', 'args': ['-m', 'mememo']}; json.dump(config, open(config_file, 'w'), indent=2)"

    if errorlevel 1 (
        echo [WARN] Auto-configuration failed. Configure Claude Desktop manually (see README.md)
    ) else (
        echo [OK] Claude Desktop MCP server configured at %CONFIG_FILE%
        echo [WARN] Restart Claude Desktop to start using mememo
    )

    goto :eof

:do_upgrade
    if not exist "%VENV_DIR%" (
        echo [ERROR] No installation found. Run 'install.bat' first
        exit /b 1
    )

    echo [INFO] Upgrading mememo...
    call %VENV_DIR%\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install --upgrade -e ".[dev]"
    echo [OK] mememo upgraded
    goto end

:do_uninstall
    echo [WARN] This will remove mememo and %VENV_DIR%
    set /p confirm="Continue? (y/N): "
    if /i not "%confirm%"=="y" (
        echo [INFO] Cancelled
        goto end
    )

    echo [INFO] Uninstalling...

    if exist "%VENV_DIR%" (
        rmdir /s /q "%VENV_DIR%"
        echo [OK] Virtual environment removed
    )

    for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
    del /s /q *.pyc 2>nul
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
    for /d %%d in (*.egg-info) do @if exist "%%d" rmdir /s /q "%%d"

    echo [OK] mememo uninstalled
    echo.
    echo [INFO] User data in %USERPROFILE%\.mememo preserved
    echo [INFO] To complete uninstall, manually remove mememo from:
    echo        %APPDATA%\Claude\claude_desktop_config.json
    echo        (Remove the "mememo" entry from mcpServers section)
    goto end

:show_help
    echo Usage: install.bat [OPTIONS]
    echo.
    echo Options:
    echo   (none)            Install for production
    echo   --dev             Install with dev/test dependencies
    echo   --configure[=AI]  Auto-configure AI assistant MCP server
    echo                     Supported: claude (Claude Desktop), claudecli (Claude Code CLI)
    echo                     Default: claude
    echo                     Example: --configure=claude
    echo                              --configure=claudecli
    echo   --upgrade         Upgrade existing installation
    echo   --uninstall       Remove mememo and virtual environment
    echo   --help            Show this help
    goto end

:end
endlocal

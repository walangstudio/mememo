@echo off
REM mememo installer for Windows
setlocal enabledelayedexpansion

set VENV_DIR=.venv
set PYTHON_MIN=3.10
set "SCRIPT_DIR=%~dp0"
if "!SCRIPT_DIR:~-1!"=="\" set "SCRIPT_DIR=!SCRIPT_DIR:~0,-1!"

rem ── Defaults ─────────────────────────────────────────────
set FORCE=false
set UNINSTALL=false
set UPGRADE=false
set CLIENT=
set SKIP_TEST=false
set GLOBAL_CONFIG=false
set DEV_MODE=false
set CLIENT_EXPLICIT=false
set STATUS=false

:parse_args
if "%~1"=="" goto :end_parse
if /i "%~1"=="-c"                    goto :pa_client
if /i "%~1"=="--client"              goto :pa_client
if /i "%~1"=="-f"                    goto :pa_force
if /i "%~1"=="--force"               goto :pa_force
if /i "%~1"=="-u"                    goto :pa_uninstall
if /i "%~1"=="--uninstall"           goto :pa_uninstall
if /i "%~1"=="--upgrade"             goto :pa_upgrade
if /i "%~1"=="--update"              goto :pa_upgrade
if /i "%~1"=="--status"              goto :pa_status
if /i "%~1"=="--global"              goto :pa_global
if /i "%~1"=="--skip-test"           goto :pa_skip_test
if /i "%~1"=="--dev"                 goto :pa_dev
if /i "%~1"=="--configure=claude"    goto :pa_cfg_claude
if /i "%~1"=="--configure=claudecli" goto :pa_cfg_claudecli
if /i "%~1"=="--configure"           goto :pa_cfg_claude
if /i "%~1"=="--help"                goto :show_help
if /i "%~1"=="-h"                    goto :show_help
echo [ERROR] Unknown option: %~1
echo Run 'install.bat --help' for usage
exit /b 1

:pa_client
if "%~2"=="" (echo [ERROR] --client requires a value & exit /b 1)
set "CLIENT=%~2"
set "CLIENT_EXPLICIT=true"
shift & shift & goto :parse_args

:pa_force
set "FORCE=true" & shift & goto :parse_args

:pa_uninstall
set "UNINSTALL=true" & shift & goto :parse_args

:pa_upgrade
set "UPGRADE=true" & shift & goto :parse_args

:pa_status
set "STATUS=true" & shift & goto :parse_args

:pa_global
set "GLOBAL_CONFIG=true" & shift & goto :parse_args

:pa_skip_test
set "SKIP_TEST=true" & shift & goto :parse_args

:pa_dev
set "DEV_MODE=true" & shift & goto :parse_args

:pa_cfg_claude
set "CLIENT=claudedesktop" & set "CLIENT_EXPLICIT=true" & shift & goto :parse_args

:pa_cfg_claudecli
set "CLIENT=claude" & set "CLIENT_EXPLICIT=true" & shift & goto :parse_args

:end_parse

if "%UNINSTALL%"=="true" (
    if "%CLIENT_EXPLICIT%"=="false" set "CLIENT=all"
)

if "%GLOBAL_CONFIG%"=="true" (
    if not "%CLIENT%"=="claude" (
        if not "%CLIENT%"=="cursor" (
            if not "%CLIENT%"=="gemini" (
                if not "%CLIENT%"=="codex" (
                    if not "%CLIENT%"=="both" (
                        if not "%CLIENT%"=="opencode" (
                            if not "%CLIENT%"=="all" (
                                if not "%CLIENT%"=="" (
                                    echo [ERROR] --global is only valid with -c claude, cursor, gemini, codex, opencode, both, or all
                                    exit /b 1
                                )
                            )
                        )
                    )
                )
            )
        )
    )
)

rem ── Config paths ─────────────────────────────────────────
set "DESKTOP_CONFIG=%APPDATA%\Claude\claude_desktop_config.json"

set "_PARENT=%CD%"

if "%GLOBAL_CONFIG%"=="true" (
    set "CODE_CONFIG=%USERPROFILE%\.claude.json"
) else (
    set "CODE_CONFIG=!_PARENT!\.mcp.json"
)

if "%GLOBAL_CONFIG%"=="true" (
    set "CURSOR_CONFIG=%USERPROFILE%\.cursor\mcp.json"
) else (
    set "CURSOR_CONFIG=!_PARENT!\.cursor\mcp.json"
)

set "WINDSURF_CONFIG=%USERPROFILE%\.codeium\windsurf\mcp_config.json"
set "VSCODE_CONFIG=!_PARENT!\.vscode\mcp.json"

if "%GLOBAL_CONFIG%"=="true" (
    set "GEMINI_CONFIG=%USERPROFILE%\.gemini\settings.json"
) else (
    set "GEMINI_CONFIG=!_PARENT!\.gemini\settings.json"
)

if "%GLOBAL_CONFIG%"=="true" (
    set "CODEX_CONFIG=%USERPROFILE%\.codex\config.toml"
) else (
    set "CODEX_CONFIG=!_PARENT!\.codex\config.toml"
)

set "ZED_CONFIG=%USERPROFILE%\.config\zed\settings.json"
set "KILO_CONFIG=!_PARENT!\.kilocode\mcp.json"

if "%GLOBAL_CONFIG%"=="true" (
    set "OPENCODE_CONFIG=%USERPROFILE%\.config\opencode\opencode.json"
) else (
    set "OPENCODE_CONFIG=!_PARENT!\opencode.json"
)

set "GOOSE_CONFIG=%USERPROFILE%\.config\goose\config.yaml"

echo ======================================
echo mememo Installer
echo ======================================
echo.

if "%STATUS%"=="true" (
    set "_py_path=%SCRIPT_DIR%\%VENV_DIR%\Scripts\python.exe"
    if not exist "!_py_path!" (
        for %%P in (python python3) do (
            where %%P >nul 2>&1 && set "_py_path=%%P"
        )
    )
    call :show_status "!_py_path!"
    goto end
)

if "%UNINSTALL%"=="true" goto do_uninstall
if "%UPGRADE%"=="true"   goto do_upgrade
goto do_install

rem ════════════════════════════════════════════════════════
:do_install
    echo [INFO] Checking Python version...
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found. Install Python %PYTHON_MIN%+
        exit /b 1
    )

    if exist "%VENV_DIR%\.mememo_installed" (
        if exist "%VENV_DIR%" (
            if not "%FORCE%"=="true" (
                set "_inst_ver=unknown"
                for /f "usebackq delims=" %%v in ("%VENV_DIR%\.mememo_installed") do set "_inst_ver=%%v"
                echo [INFO] mememo !_inst_ver! already installed. Use --upgrade to update.
                if "%CLIENT_EXPLICIT%"=="true" goto :do_configure_only
                echo [INFO] Run 'install.bat -c claudedesktop' to configure Claude Desktop
                echo [INFO]      'install.bat -c claude'    to configure Claude Code
                echo.
                goto end
            )
        )
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

    if "%DEV_MODE%"=="true" goto install_dev
    goto install_prod

:install_dev
    echo [INFO] Installing mememo with dev dependencies...
    pip install -e ".[dev]"
    if errorlevel 1 (echo [ERROR] Installation failed & exit /b 1)
    for /f %%v in ('python -c "from importlib.metadata import version; print(version(\"mememo\"))"') do set "_inst_ver=%%v"
    echo [OK] mememo !_inst_ver! installed with dev/test tools
    echo !_inst_ver!>"%VENV_DIR%\.mememo_installed"
    goto install_done

:install_prod
    echo [INFO] Installing mememo (production)...
    pip install -e .
    if errorlevel 1 (echo [ERROR] Installation failed & exit /b 1)
    for /f %%v in ('python -c "from importlib.metadata import version; print(version(\"mememo\"))"') do set "_inst_ver=%%v"
    echo [OK] mememo !_inst_ver! installed
    echo !_inst_ver!>"%VENV_DIR%\.mememo_installed"
    goto install_done

:install_done
    echo.
    if not "%SKIP_TEST%"=="true" call :run_warmup

    if "%CLIENT_EXPLICIT%"=="true" (
        echo.
        call :do_configure
    ) else (
        echo [INFO] Tip: Run 'install.bat -c claudedesktop' to auto-configure Claude Desktop
        echo [INFO]      Run 'install.bat -c claude'    to auto-configure Claude Code CLI
        echo [INFO]      Run 'install.bat -c all'       to configure all detected clients
        echo [INFO]      Run 'install.bat --status'     to show all install locations
    )

    echo.
    echo ======================================
    echo Installation Complete!
    echo ======================================
    echo.

    if "%DEV_MODE%"=="true" goto show_dev_msg
    if "%CLIENT_EXPLICIT%"=="true" goto show_configured
    goto show_not_configured

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
    echo      install.bat -c claudedesktop   (Claude Desktop)
    echo      install.bat -c claude          (Claude Code CLI)
    echo.
    goto end

:show_configured
    if /i "%CLIENT%"=="claude" (
        echo   mememo is ready in Claude Code CLI.
        echo   Verify with: claude mcp list
    ) else (
        echo   mememo is ready. Restart the client to activate.
    )
    echo.
    echo   See README.md for usage and configuration options.
    echo.
    goto end

:show_not_configured
    echo   mememo is installed but not yet connected to an AI assistant.
    echo   Run: install.bat -c claudedesktop    (Claude Desktop)
    echo        install.bat -c claude           (Claude Code CLI)
    echo        install.bat -c cursor           (Cursor)
    echo        install.bat -c windsurf         (Windsurf)
    echo        install.bat -c vscode           (VS Code)
    echo        install.bat -c gemini           (Gemini CLI)
    echo        install.bat -c codex            (OpenAI Codex CLI)
    echo        install.bat -c zed              (Zed)
    echo        install.bat -c kilo             (Kilo Code)
    echo        install.bat -c opencode         (OpenCode)
    echo        install.bat -c goose            (Goose)
    echo        install.bat -c all              (all detected clients)
    echo.
    echo   See README.md for usage and configuration options.
    echo.
    goto end

rem ════════════════════════════════════════════════════════
:do_configure_only
    call %VENV_DIR%\Scripts\activate.bat
    call :do_configure
    goto end

rem ════════════════════════════════════════════════════════
:do_configure
    set "_py_path=%SCRIPT_DIR%\%VENV_DIR%\Scripts\python.exe"
    if not exist "!_py_path!" (
        echo [ERROR] Python not found at !_py_path!
        goto :eof
    )
    call :configure_client "%CLIENT%" "!_py_path!"
    goto :eof

rem ════════════════════════════════════════════════════════
:do_upgrade
    if not exist "%VENV_DIR%" (
        echo [ERROR] No installation found. Run 'install.bat' first
        exit /b 1
    )

    echo [INFO] Upgrading mememo...


    call %VENV_DIR%\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install --upgrade -e ".[dev]"
    for /f %%v in ('python -c "from importlib.metadata import version; print(version(\"mememo\"))"') do set "_inst_ver=%%v"
    echo [OK] mememo !_inst_ver! upgraded
    echo !_inst_ver!>"%VENV_DIR%\.mememo_installed"
    echo.
    if not "%SKIP_TEST%"=="true" call :run_warmup

    if "%CLIENT_EXPLICIT%"=="true" (
        echo.
        call :do_configure
    )
    goto end

rem ════════════════════════════════════════════════════════
:do_uninstall
    set "_py_path=%SCRIPT_DIR%\%VENV_DIR%\Scripts\python.exe"

    if "%CLIENT_EXPLICIT%"=="true" (
        if exist "!_py_path!" (
            call :configure_client "%CLIENT%" "!_py_path!"
            echo.
        )
    )

    echo [WARN] This will remove mememo and %VENV_DIR%
    if not "%FORCE%"=="true" (
        set /p confirm="Continue? (y/N): "
        if /i not "!confirm!"=="y" (
            echo [INFO] Cancelled
            goto end
        )
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
    goto end

rem ════════════════════════════════════════════════════════
:configure_client
set "_cct=%~1"
set "_cpy=%~2"

if /i "!_cct!"=="claudedesktop"  goto :cc_desktop
if /i "!_cct!"=="claude"         goto :cc_code
if /i "!_cct!"=="cursor"   goto :cc_cursor
if /i "!_cct!"=="windsurf" goto :cc_windsurf
if /i "!_cct!"=="vscode"   goto :cc_vscode
if /i "!_cct!"=="gemini"   goto :cc_gemini
if /i "!_cct!"=="codex"    goto :cc_codex
if /i "!_cct!"=="zed"      goto :cc_zed
if /i "!_cct!"=="kilo"     goto :cc_kilo
if /i "!_cct!"=="opencode" goto :cc_opencode
if /i "!_cct!"=="goose"    goto :cc_goose
if /i "!_cct!"=="pidev"    goto :cc_pidev
if /i "!_cct!"=="both"     goto :cc_both
if /i "!_cct!"=="all"      goto :cc_all
echo [ERROR] Unknown client type: !_cct!
goto :eof

:cc_desktop
set "_cfg_d=!DESKTOP_CONFIG!"
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Claude Desktop & if not exist "%APPDATA%\Claude" mkdir "%APPDATA%\Claude" )
call :_configure_mcp_json "!_cfg_d!" "!_cpy!" mememo "Claude Desktop"
goto :eof

:cc_code
if not "%UNINSTALL%"=="true" echo [INFO] Client: Claude Code
where claude >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Claude CLI not found. Install it first: https://claude.ai/download
    goto :eof
)
if "%UNINSTALL%"=="true" (
    claude mcp remove mememo --scope user >nul 2>&1
    echo [OK] Removed mememo from Claude Code ^(user scope^)
    goto :eof
)
if "%GLOBAL_CONFIG%"=="true" (
    claude mcp remove mememo --scope user >nul 2>&1
    claude mcp add --scope user mememo -- "!_cpy!" -m mememo
) else (
    claude mcp remove mememo --scope project >nul 2>&1
    claude mcp add --scope project mememo -- "!_cpy!" -m mememo
)
if errorlevel 1 (
    echo [WARN] Auto-configuration failed. Add manually:
    echo        claude mcp add --scope user mememo -- "!_cpy!" -m mememo
) else (
    echo [OK] Claude Code MCP server configured
    echo [INFO] Verify with: claude mcp list
)
goto :eof

:cc_cursor
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Cursor & echo [INFO] Config: !CURSOR_CONFIG! )
call :_configure_mcp_json "!CURSOR_CONFIG!" "!_cpy!" mememo "Cursor"
goto :eof

:cc_windsurf
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Windsurf ^(global^) & echo [INFO] Config: !WINDSURF_CONFIG! )
call :_configure_mcp_json "!WINDSURF_CONFIG!" "!_cpy!" mememo "Windsurf"
goto :eof

:cc_vscode
if not "%UNINSTALL%"=="true" (
    echo [INFO] Client: VS Code ^(workspace^)
    echo [INFO] Config: !VSCODE_CONFIG!
    echo [INFO] Note: for global VS Code config, use the VS Code command palette
)
call :_configure_vscode_json "!VSCODE_CONFIG!" "!_cpy!"
goto :eof

:cc_gemini
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Gemini CLI & echo [INFO] Config: !GEMINI_CONFIG! )
call :_configure_mcp_json "!GEMINI_CONFIG!" "!_cpy!" mememo "Gemini CLI"
goto :eof

:cc_codex
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: OpenAI Codex CLI & echo [INFO] Config: !CODEX_CONFIG! )
call :_configure_codex_toml "!CODEX_CONFIG!" "!_cpy!"
goto :eof

:cc_zed
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Zed ^(global^) & echo [INFO] Config: !ZED_CONFIG! )
call :_configure_zed_json "!ZED_CONFIG!" "!_cpy!"
goto :eof

:cc_kilo
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Kilo Code & echo [INFO] Config: !KILO_CONFIG! )
call :_configure_mcp_json "!KILO_CONFIG!" "!_cpy!" mememo "Kilo Code"
goto :eof

:cc_opencode
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: OpenCode & echo [INFO] Config: !OPENCODE_CONFIG! )
call :_configure_opencode_json "!OPENCODE_CONFIG!" "!_cpy!"
goto :eof

:cc_goose
set "_goose_cfg=!GOOSE_CONFIG!"
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Goose & echo [INFO] Config: !_goose_cfg! )
call :_configure_goose_yaml "!_goose_cfg!" "!_cpy!"
goto :eof

:cc_pidev
echo [INFO] Client: pi.dev
echo.
echo   pi.dev does not support MCP servers natively.
echo   pi.dev uses TypeScript extensions and CLI tools instead.
echo   To use mememo concepts in pi.dev, see: https://pi.dev/docs/extensions
echo.
goto :eof

:cc_both
call :configure_client "claudedesktop" "!_cpy!"
echo.
call :configure_client "claude" "!_cpy!"
goto :eof

:cc_all
call :configure_client "claudedesktop" "!_cpy!"
echo.
call :configure_client "claude" "!_cpy!"
if "%UNINSTALL%"=="true" (
    echo. & call :configure_client "cursor" "!_cpy!"
    echo. & call :configure_client "windsurf" "!_cpy!"
    echo. & call :configure_client "vscode" "!_cpy!"
    echo. & call :configure_client "gemini" "!_cpy!"
    echo. & call :configure_client "codex" "!_cpy!"
    echo. & call :configure_client "zed" "!_cpy!"
    echo. & call :configure_client "kilo" "!_cpy!"
    echo. & call :configure_client "opencode" "!_cpy!"
    echo. & call :configure_client "goose" "!_cpy!"
) else (
    if exist "!_PARENT!\.cursor\mcp.json" ( echo. & call :configure_client "cursor" "!_cpy!" )
    if exist "%USERPROFILE%\.cursor\mcp.json" ( echo. & call :configure_client "cursor" "!_cpy!" )
    if exist "!WINDSURF_CONFIG!" ( echo. & call :configure_client "windsurf" "!_cpy!" )
    if exist "!VSCODE_CONFIG!" ( echo. & call :configure_client "vscode" "!_cpy!" )
    if exist "!_PARENT!\.gemini\settings.json" ( echo. & call :configure_client "gemini" "!_cpy!" )
    if exist "%USERPROFILE%\.gemini\settings.json" ( echo. & call :configure_client "gemini" "!_cpy!" )
    if exist "!_PARENT!\.codex\config.toml" ( echo. & call :configure_client "codex" "!_cpy!" )
    if exist "%USERPROFILE%\.codex\config.toml" ( echo. & call :configure_client "codex" "!_cpy!" )
    if exist "!ZED_CONFIG!" ( echo. & call :configure_client "zed" "!_cpy!" )
    if exist "!KILO_CONFIG!" ( echo. & call :configure_client "kilo" "!_cpy!" )
    if exist "!OPENCODE_CONFIG!" ( echo. & call :configure_client "opencode" "!_cpy!" )
    if exist "%USERPROFILE%\.config\opencode\opencode.json" ( echo. & call :configure_client "opencode" "!_cpy!" )
    if exist "!GOOSE_CONFIG!" ( echo. & call :configure_client "goose" "!_cpy!" )
)
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_mcp_json <config_path> <python_path> <server_name> [label]
:_configure_mcp_json
set "_mcfg=%~1"
set "_mpy=%~2"
set "_mname=%~3"
set "_mlbl=%~4"

if "%UNINSTALL%"=="true" (
    if not exist "!_mcfg!" (goto :eof)
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_mcfg!" "!_mcfg!.backup.!_ts!" >nul
    set "_tmp_rm=%TEMP%\mememo_rm_%RANDOM%.py"
    echo import json, sys, os > "!_tmp_rm!"
    echo config_path = sys.argv[1]; name = sys.argv[2] >> "!_tmp_rm!"
    echo lbl = sys.argv[3] if len(sys.argv) > 3 else name >> "!_tmp_rm!"
    echo if not os.path.exists(config_path): sys.exit(0) >> "!_tmp_rm!"
    echo try: >> "!_tmp_rm!"
    echo     with open(config_path) as f: cfg = json.load(f) >> "!_tmp_rm!"
    echo except: sys.exit(0) >> "!_tmp_rm!"
    echo s = cfg.get('mcpServers', {}) >> "!_tmp_rm!"
    echo if name in s: >> "!_tmp_rm!"
    echo     del s[name] >> "!_tmp_rm!"
    echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "!_tmp_rm!"
    echo     print('[OK] Removed from ' + lbl) >> "!_tmp_rm!"
    "!_mpy!" "!_tmp_rm!" "!_mcfg!" "!_mname!" "!_mlbl!"
    del /f /q "!_tmp_rm!" 2>nul
    goto :eof
)

if exist "!_mcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_mcfg!" "!_mcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)

set "_tmp_merge=%TEMP%\mememo_merge_%RANDOM%.py"
echo import json, sys, os > "!_tmp_merge!"
echo config_path = os.path.abspath(sys.argv[1]) >> "!_tmp_merge!"
echo python_path = sys.executable >> "!_tmp_merge!"
echo name = sys.argv[2] >> "!_tmp_merge!"
echo try: >> "!_tmp_merge!"
echo     with open(config_path) as f: config = json.load(f) >> "!_tmp_merge!"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "!_tmp_merge!"
echo config.setdefault('mcpServers', {}) >> "!_tmp_merge!"
echo config['mcpServers'][name] = {'command': python_path, 'args': ['-m', name]} >> "!_tmp_merge!"
echo d = os.path.dirname(config_path) >> "!_tmp_merge!"
echo if d: os.makedirs(d, exist_ok=True) >> "!_tmp_merge!"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "!_tmp_merge!"
"!_mpy!" "!_tmp_merge!" "!_mcfg!" "!_mname!"
del /f /q "!_tmp_merge!" 2>nul
echo [OK] MCP config updated at !_mcfg!
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_vscode_json <config_path> <python_path>
:_configure_vscode_json
set "_vcfg=%~1"
set "_vpy=%~2"

if "%UNINSTALL%"=="true" (
    if not exist "!_vcfg!" (goto :eof)
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_vcfg!" "!_vcfg!.backup.!_ts!" >nul
    set "_tmp_rv=%TEMP%\mememo_rm_vscode_%RANDOM%.py"
    echo import json, sys, os > "!_tmp_rv!"
    echo config_path = sys.argv[1] >> "!_tmp_rv!"
    echo if not os.path.exists(config_path): sys.exit(0) >> "!_tmp_rv!"
    echo try: >> "!_tmp_rv!"
    echo     with open(config_path) as f: cfg = json.load(f) >> "!_tmp_rv!"
    echo except: sys.exit(0) >> "!_tmp_rv!"
    echo s = cfg.get('servers', {}) >> "!_tmp_rv!"
    echo if 'mememo' in s: >> "!_tmp_rv!"
    echo     del s['mememo'] >> "!_tmp_rv!"
    echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "!_tmp_rv!"
    echo     print('[OK] Removed mememo from VS Code config') >> "!_tmp_rv!"
    "!_vpy!" "!_tmp_rv!" "!_vcfg!"
    del /f /q "!_tmp_rv!" 2>nul
    goto :eof
)

if exist "!_vcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_vcfg!" "!_vcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)

set "_tmp_mv=%TEMP%\mememo_merge_vscode_%RANDOM%.py"
echo import json, sys, os > "!_tmp_mv!"
echo config_path = os.path.abspath(sys.argv[1]) >> "!_tmp_mv!"
echo python_path = sys.executable >> "!_tmp_mv!"
echo try: >> "!_tmp_mv!"
echo     with open(config_path) as f: config = json.load(f) >> "!_tmp_mv!"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "!_tmp_mv!"
echo config.setdefault('servers', {}) >> "!_tmp_mv!"
echo config['servers']['mememo'] = {'type': 'stdio', 'command': python_path, 'args': ['-m', 'mememo']} >> "!_tmp_mv!"
echo d = os.path.dirname(config_path) >> "!_tmp_mv!"
echo if d: os.makedirs(d, exist_ok=True) >> "!_tmp_mv!"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "!_tmp_mv!"
"!_vpy!" "!_tmp_mv!" "!_vcfg!"
del /f /q "!_tmp_mv!" 2>nul
echo [OK] VS Code MCP config updated at !_vcfg!
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_codex_toml <config_path> <python_path>
:_configure_codex_toml
set "_ccfg=%~1"
set "_cpy2=%~2"

if "%UNINSTALL%"=="true" (
    if not exist "!_ccfg!" (goto :eof)
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ccfg!" "!_ccfg!.backup.!_ts!" >nul
    set "_tmp_rc=%TEMP%\mememo_rm_codex_%RANDOM%.py"
    echo import sys, os, re > "!_tmp_rc!"
    echo config_path = sys.argv[1] >> "!_tmp_rc!"
    echo sn = 'mememo' >> "!_tmp_rc!"
    echo section_header = '[mcp_servers.' + sn + ']' >> "!_tmp_rc!"
    echo if not os.path.exists(config_path): sys.exit(0) >> "!_tmp_rc!"
    echo with open(config_path) as f: existing = f.read() >> "!_tmp_rc!"
    echo if section_header not in existing: >> "!_tmp_rc!"
    echo     sys.exit(0) >> "!_tmp_rc!"
    echo lines = existing.split('\n') >> "!_tmp_rc!"
    echo start = next((i for i, l in enumerate(lines) if l.strip() == section_header), -1) >> "!_tmp_rc!"
    echo if start != -1: >> "!_tmp_rc!"
    echo     end = len(lines) >> "!_tmp_rc!"
    echo     for i in range(start + 1, len(lines)): >> "!_tmp_rc!"
    echo         if re.match(r'^\[', lines[i]): end = i; break >> "!_tmp_rc!"
    echo     del lines[start:end] >> "!_tmp_rc!"
    echo     with open(config_path, 'w') as f: f.write('\n'.join(lines)) >> "!_tmp_rc!"
    echo     print('[OK] Removed mememo from codex config') >> "!_tmp_rc!"
    "!_cpy2!" "!_tmp_rc!" "!_ccfg!"
    del /f /q "!_tmp_rc!" 2>nul
    goto :eof
)

if exist "!_ccfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ccfg!" "!_ccfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)

set "_tmp_mc=%TEMP%\mememo_merge_codex_%RANDOM%.py"
echo import sys, os, re > "!_tmp_mc!"
echo config_path = os.path.abspath(sys.argv[1]) >> "!_tmp_mc!"
echo python_path = sys.executable >> "!_tmp_mc!"
echo sn = 'mememo' >> "!_tmp_mc!"
echo section_header = '[mcp_servers.' + sn + ']' >> "!_tmp_mc!"
echo cmd = python_path + ' -m ' + sn >> "!_tmp_mc!"
echo new_section = '\n' + section_header + '\ncommand = "' + cmd + '"\nstartup_timeout_sec = 30\ntool_timeout_sec = 300\nenabled = true\n' >> "!_tmp_mc!"
echo os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True) >> "!_tmp_mc!"
echo existing = '' >> "!_tmp_mc!"
echo try: >> "!_tmp_mc!"
echo     with open(config_path) as f: existing = f.read() >> "!_tmp_mc!"
echo except FileNotFoundError: pass >> "!_tmp_mc!"
echo if section_header in existing: >> "!_tmp_mc!"
echo     lines = existing.split('\n') >> "!_tmp_mc!"
echo     start = next((i for i, l in enumerate(lines) if l.strip() == section_header), -1) >> "!_tmp_mc!"
echo     if start != -1: >> "!_tmp_mc!"
echo         end = len(lines) >> "!_tmp_mc!"
echo         for i in range(start + 1, len(lines)): >> "!_tmp_mc!"
echo             if re.match(r'^\[', lines[i]): end = i; break >> "!_tmp_mc!"
echo         del lines[start:end] >> "!_tmp_mc!"
echo         existing = '\n'.join(lines) >> "!_tmp_mc!"
echo existing = existing.rstrip() >> "!_tmp_mc!"
echo if existing: existing += '\n' >> "!_tmp_mc!"
echo with open(config_path, 'w') as f: f.write(existing + new_section) >> "!_tmp_mc!"
"!_cpy2!" "!_tmp_mc!" "!_ccfg!"
del /f /q "!_tmp_mc!" 2>nul
echo [OK] Codex TOML config updated at !_ccfg!
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_zed_json <config_path> <python_path>
:_configure_zed_json
set "_zcfg=%~1"
set "_zpy=%~2"

if "%UNINSTALL%"=="true" (
    if not exist "!_zcfg!" (goto :eof)
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_zcfg!" "!_zcfg!.backup.!_ts!" >nul
    set "_tmp_rz=%TEMP%\mememo_rm_zed_%RANDOM%.py"
    echo import json, sys, os > "!_tmp_rz!"
    echo config_path = sys.argv[1] >> "!_tmp_rz!"
    echo if not os.path.exists(config_path): sys.exit(0) >> "!_tmp_rz!"
    echo try: >> "!_tmp_rz!"
    echo     with open(config_path) as f: cfg = json.load(f) >> "!_tmp_rz!"
    echo except: sys.exit(0) >> "!_tmp_rz!"
    echo cs = cfg.get('context_servers', {}) >> "!_tmp_rz!"
    echo if 'mememo' in cs: >> "!_tmp_rz!"
    echo     del cs['mememo'] >> "!_tmp_rz!"
    echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "!_tmp_rz!"
    echo     print('[OK] Removed mememo from Zed config') >> "!_tmp_rz!"
    "!_zpy!" "!_tmp_rz!" "!_zcfg!"
    del /f /q "!_tmp_rz!" 2>nul
    goto :eof
)

if exist "!_zcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_zcfg!" "!_zcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)

set "_tmp_mz=%TEMP%\mememo_merge_zed_%RANDOM%.py"
echo import json, sys, os > "!_tmp_mz!"
echo config_path = os.path.abspath(sys.argv[1]) >> "!_tmp_mz!"
echo python_path = sys.executable >> "!_tmp_mz!"
echo try: >> "!_tmp_mz!"
echo     with open(config_path) as f: config = json.load(f) >> "!_tmp_mz!"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "!_tmp_mz!"
echo config.setdefault('context_servers', {}) >> "!_tmp_mz!"
echo config['context_servers']['mememo'] = {'command': {'path': python_path, 'args': ['-m', 'mememo'], 'env': {}}} >> "!_tmp_mz!"
echo os.makedirs(os.path.dirname(config_path), exist_ok=True) >> "!_tmp_mz!"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "!_tmp_mz!"
"!_zpy!" "!_tmp_mz!" "!_zcfg!"
del /f /q "!_tmp_mz!" 2>nul
echo [OK] Zed config updated at !_zcfg!
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_opencode_json <config_path> <python_path>
:_configure_opencode_json
set "_ocfg=%~1"
set "_opy=%~2"

if "%UNINSTALL%"=="true" (
    if not exist "!_ocfg!" (goto :eof)
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ocfg!" "!_ocfg!.backup.!_ts!" >nul
    set "_tmp_ro=%TEMP%\mememo_rm_oc_%RANDOM%.py"
    echo import json, sys, os > "!_tmp_ro!"
    echo config_path = sys.argv[1] >> "!_tmp_ro!"
    echo if not os.path.exists(config_path): sys.exit(0) >> "!_tmp_ro!"
    echo try: >> "!_tmp_ro!"
    echo     with open(config_path) as f: cfg = json.load(f) >> "!_tmp_ro!"
    echo except: sys.exit(0) >> "!_tmp_ro!"
    echo mcp = cfg.get('mcp', {}) >> "!_tmp_ro!"
    echo if 'mememo' in mcp: >> "!_tmp_ro!"
    echo     del mcp['mememo'] >> "!_tmp_ro!"
    echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "!_tmp_ro!"
    echo     print('[OK] Removed from OpenCode') >> "!_tmp_ro!"
    "!_opy!" "!_tmp_ro!" "!_ocfg!"
    del /f /q "!_tmp_ro!" 2>nul
    goto :eof
)

if exist "!_ocfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ocfg!" "!_ocfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)

set "_tmp_oc=%TEMP%\mememo_merge_oc_%RANDOM%.py"
echo import json, sys, os > "!_tmp_oc!"
echo config_path = os.path.abspath(sys.argv[1]) >> "!_tmp_oc!"
echo python_path = sys.executable >> "!_tmp_oc!"
echo try: >> "!_tmp_oc!"
echo     with open(config_path) as f: config = json.load(f) >> "!_tmp_oc!"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "!_tmp_oc!"
echo config.setdefault('mcp', {}) >> "!_tmp_oc!"
echo config['mcp']['mememo'] = {'type': 'local', 'command': [python_path, '-m', 'mememo']} >> "!_tmp_oc!"
echo d = os.path.dirname(config_path) >> "!_tmp_oc!"
echo if d: os.makedirs(d, exist_ok=True) >> "!_tmp_oc!"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "!_tmp_oc!"
"!_opy!" "!_tmp_oc!" "!_ocfg!"
del /f /q "!_tmp_oc!" 2>nul
echo [OK] OpenCode MCP config updated at !_ocfg!
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_goose_yaml <config_path> <python_path>
:_configure_goose_yaml
set "_gcfg=%~1"
set "_gpy=%~2"

if "%UNINSTALL%"=="true" (
    if not exist "!_gcfg!" (goto :eof)
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_gcfg!" "!_gcfg!.backup.!_ts!" >nul
    set "_tmp_rg=%TEMP%\mememo_rm_goose_%RANDOM%.py"
    echo import sys, os > "!_tmp_rg!"
    echo try: import yaml >> "!_tmp_rg!"
    echo except ImportError: print('[WARN] PyYAML not available'); sys.exit(0) >> "!_tmp_rg!"
    echo cfg_path = sys.argv[1] >> "!_tmp_rg!"
    echo if not os.path.exists(cfg_path): sys.exit(0) >> "!_tmp_rg!"
    echo with open(cfg_path) as f: config = yaml.safe_load(f) or {} >> "!_tmp_rg!"
    echo ext = config.get('extensions', {}) >> "!_tmp_rg!"
    echo if 'mememo' in ext: >> "!_tmp_rg!"
    echo     del ext['mememo'] >> "!_tmp_rg!"
    echo     with open(cfg_path, 'w') as f: yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False) >> "!_tmp_rg!"
    echo     print('[OK] Removed mememo from Goose config') >> "!_tmp_rg!"
    "!_gpy!" "!_tmp_rg!" "!_gcfg!"
    del /f /q "!_tmp_rg!" 2>nul
    goto :eof
)

if exist "!_gcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_gcfg!" "!_gcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)

set "_tmp_gg=%TEMP%\mememo_merge_goose_%RANDOM%.py"
echo import sys, os > "!_tmp_gg!"
echo try: import yaml >> "!_tmp_gg!"
echo except ImportError: >> "!_tmp_gg!"
echo     py = sys.executable >> "!_tmp_gg!"
echo     print('[WARN] PyYAML not available. Add manually to config.yaml:') >> "!_tmp_gg!"
echo     print('extensions:') >> "!_tmp_gg!"
echo     print('  mememo:') >> "!_tmp_gg!"
echo     print('    name: mememo') >> "!_tmp_gg!"
echo     print('    type: stdio') >> "!_tmp_gg!"
echo     print('    cmd: ' + py) >> "!_tmp_gg!"
echo     print('    args: [\"-m\", \"mememo\"]') >> "!_tmp_gg!"
echo     print('    enabled: true') >> "!_tmp_gg!"
echo     sys.exit(0) >> "!_tmp_gg!"
echo config_path = os.path.abspath(sys.argv[1]) >> "!_tmp_gg!"
echo python_path = sys.executable >> "!_tmp_gg!"
echo try: >> "!_tmp_gg!"
echo     with open(config_path) as f: config = yaml.safe_load(f) or {} >> "!_tmp_gg!"
echo except FileNotFoundError: config = {} >> "!_tmp_gg!"
echo config.setdefault('extensions', {}) >> "!_tmp_gg!"
echo config['extensions']['mememo'] = {'name': 'mememo', 'type': 'stdio', 'cmd': python_path, 'args': ['-m', 'mememo'], 'enabled': True} >> "!_tmp_gg!"
echo d = os.path.dirname(config_path) >> "!_tmp_gg!"
echo if d: os.makedirs(d, exist_ok=True) >> "!_tmp_gg!"
echo with open(config_path, 'w') as f: yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False) >> "!_tmp_gg!"
"!_gpy!" "!_tmp_gg!" "!_gcfg!"
del /f /q "!_tmp_gg!" 2>nul
echo [OK] Goose MCP config updated at !_gcfg!
goto :eof

rem ════════════════════════════════════════════════════════
:show_status
set "_spy=%~1"
set "_inst_ver=not installed"
if exist "%VENV_DIR%\.mememo_installed" (
    for /f "usebackq delims=" %%v in ("%VENV_DIR%\.mememo_installed") do set "_inst_ver=%%v"
)

set "_tmp_st=%TEMP%\mememo_status_%RANDOM%.py"
echo import json, sys, os > "!_tmp_st!"
echo def chk(p, fmt): >> "!_tmp_st!"
echo     if not os.path.exists(p): return False >> "!_tmp_st!"
echo     try: >> "!_tmp_st!"
echo         with open(p) as f: raw = f.read() >> "!_tmp_st!"
echo         if fmt == 'toml': return '[mcp_servers.mememo]' in raw >> "!_tmp_st!"
echo         if fmt == 'yaml': return '  mememo:' in raw >> "!_tmp_st!"
echo         return '"mememo"' in json.dumps(json.load(open(p))) >> "!_tmp_st!"
echo     except: return False >> "!_tmp_st!"
echo rows = [ >> "!_tmp_st!"
echo     ('claudedesktop        ', r'!DESKTOP_CONFIG!', 'json'), >> "!_tmp_st!"
echo     ('claude (workspace)   ', r'!CODE_CONFIG!', 'json'), >> "!_tmp_st!"
echo     ('claude (global)      ', r'%USERPROFILE%\.claude.json', 'json'), >> "!_tmp_st!"
echo     ('cursor (workspace)   ', r'!_PARENT!\.cursor\mcp.json', 'json'), >> "!_tmp_st!"
echo     ('cursor (global)      ', r'%USERPROFILE%\.cursor\mcp.json', 'json'), >> "!_tmp_st!"
echo     ('windsurf             ', r'!WINDSURF_CONFIG!', 'json'), >> "!_tmp_st!"
echo     ('vscode (workspace)   ', r'!VSCODE_CONFIG!', 'json'), >> "!_tmp_st!"
echo     ('gemini (workspace)   ', r'!_PARENT!\.gemini\settings.json', 'json'), >> "!_tmp_st!"
echo     ('gemini (global)      ', r'%USERPROFILE%\.gemini\settings.json', 'json'), >> "!_tmp_st!"
echo     ('codex (workspace)    ', r'!_PARENT!\.codex\config.toml', 'toml'), >> "!_tmp_st!"
echo     ('codex (global)       ', r'%USERPROFILE%\.codex\config.toml', 'toml'), >> "!_tmp_st!"
echo     ('zed                  ', r'!ZED_CONFIG!', 'json'), >> "!_tmp_st!"
echo     ('kilo                 ', r'!KILO_CONFIG!', 'json'), >> "!_tmp_st!"
echo     ('opencode (workspace) ', r'!_PARENT!\opencode.json', 'json'), >> "!_tmp_st!"
echo     ('opencode (global)    ', r'%USERPROFILE%\.config\opencode\opencode.json', 'json'), >> "!_tmp_st!"
echo     ('goose                ', r'!GOOSE_CONFIG!', 'yaml'), >> "!_tmp_st!"
echo ] >> "!_tmp_st!"
echo for lbl, p, fmt in rows: >> "!_tmp_st!"
echo     if chk(p, fmt): print(f'   {lbl}  YES  {p}') >> "!_tmp_st!"
echo     else: print(f'   {lbl}  NO') >> "!_tmp_st!"

echo.
echo   mememo -- Status
echo   ------------------------------------------------------------------------
echo   Client               Installed  Config path
echo   ------------------------------------------------------------------------
"!_spy!" "!_tmp_st!"
echo   ------------------------------------------------------------------------
echo   Package: !_inst_ver!
echo.

del /f /q "!_tmp_st!" 2>nul
goto :eof

rem ════════════════════════════════════════════════════════
:run_warmup
    echo [INFO] Pre-warming bytecode cache and embedding model (this runs once)...
    python warmup.py
    if errorlevel 1 (
        echo [WARN] Warmup had a non-fatal error -- first MCP startup may be slow
    ) else (
        echo [OK] Warmup complete
    )
    goto :eof

:show_help
    echo Usage: install.bat [OPTIONS]
    echo.
    echo Options:
    echo   -c, --client TYPE   MCP client: claudedesktop, claude, cursor, windsurf,
    echo                       vscode, gemini, codex, zed, kilo, opencode, goose,
    echo                       pidev, all
    echo   -f, --force         Skip prompts, overwrite existing config
    echo   -u, --uninstall     Remove mememo from MCP client config and virtual environment
    echo       --upgrade       Upgrade existing installation
    echo       --status        Show where this server is currently installed
    echo       --global        Use global config path (claude, cursor, gemini, codex,
    echo                       opencode, all)
    echo       --skip-test     Skip warmup validation step
    echo       --dev           Install dev/test dependencies
    echo   -h, --help          Show this help
    echo.
    echo Backward-compatible aliases:
    echo   --configure=claude      same as -c claudedesktop
    echo   --configure=claudecli   same as -c claude
    echo.
    echo Examples:
    echo   install.bat -c claudedesktop         Configure Claude Desktop
    echo   install.bat -c claude                Configure Claude Code
    echo   install.bat -c cursor                Configure Cursor (workspace)
    echo   install.bat -c windsurf              Configure Windsurf
    echo   install.bat -c vscode                Configure VS Code (workspace)
    echo   install.bat -c gemini                Configure Gemini CLI
    echo   install.bat -c codex                 Configure OpenAI Codex CLI
    echo   install.bat -c zed                   Configure Zed (global)
    echo   install.bat -c kilo                  Configure Kilo Code
    echo   install.bat -c opencode              Configure OpenCode (workspace)
    echo   install.bat -c opencode --global     Configure OpenCode (global)
    echo   install.bat -c goose                 Configure Goose
    echo   install.bat -c all                   Configure all detected clients
    echo   install.bat --status                 Show installation status
    echo   install.bat --upgrade                Upgrade existing installation
    echo   install.bat --upgrade -c all         Upgrade + reconfigure all clients
    echo   install.bat -u -c all                Uninstall from all client configs
    goto end

:end
endlocal

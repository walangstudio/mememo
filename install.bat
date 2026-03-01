@echo off
REM mememo installer for Windows
setlocal enabledelayedexpansion

set VENV_DIR=.venv
set PYTHON_MIN=3.10

REM ── Defaults ─────────────────────────────────────────────
set FORCE=false
set UNINSTALL=false
set UPGRADE=false
set CLIENT=
set SKIP_TEST=false
set GLOBAL_CONFIG=false
set DEV_MODE=false
set CLIENT_EXPLICIT=false

:parse_args
if "%~1"=="" goto :end_parse
if /i "%~1"=="-c"                    goto :pa_client
if /i "%~1"=="--client"              goto :pa_client
if /i "%~1"=="-f"                    goto :pa_force
if /i "%~1"=="--force"               goto :pa_force
if /i "%~1"=="-u"                    goto :pa_uninstall
if /i "%~1"=="--uninstall"           goto :pa_uninstall
if /i "%~1"=="--upgrade"             goto :pa_upgrade
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
if "%~2"=="" (
    echo [ERROR] --client requires a value
    exit /b 1
)
set "CLIENT=%~2"
set "CLIENT_EXPLICIT=true"
shift
shift
goto :parse_args

:pa_force
set "FORCE=true"
shift
goto :parse_args

:pa_uninstall
set "UNINSTALL=true"
shift
goto :parse_args

:pa_upgrade
set "UPGRADE=true"
shift
goto :parse_args

:pa_global
set "GLOBAL_CONFIG=true"
shift
goto :parse_args

:pa_skip_test
set "SKIP_TEST=true"
shift
goto :parse_args

:pa_dev
set "DEV_MODE=true"
shift
goto :parse_args

:pa_cfg_claude
set "CLIENT=desktop"
set "CLIENT_EXPLICIT=true"
shift
goto :parse_args

:pa_cfg_claudecli
set "CLIENT=code"
set "CLIENT_EXPLICIT=true"
shift
goto :parse_args

:end_parse

if "%GLOBAL_CONFIG%"=="true" (
    if not "%CLIENT%"=="code" (
        if not "%CLIENT%"=="both" (
            if not "%CLIENT%"=="opencode" (
                if not "%CLIENT%"=="all" (
                    if not "%CLIENT%"=="" (
                        echo [ERROR] --global is only valid with -c code, opencode, both, or all
                        exit /b 1
                    )
                )
            )
        )
    )
)

echo ======================================
echo mememo Installer
echo ======================================
echo.

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
                echo [INFO] Run 'install.bat -c desktop' to configure Claude Desktop
                echo [INFO]      'install.bat -c code'    to configure Claude Code
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
    if errorlevel 1 (
        echo [ERROR] Installation failed
        exit /b 1
    )
    for /f %%v in ('python -c "from importlib.metadata import version; print(version(\"mememo\"))"') do set "_inst_ver=%%v"
    echo [OK] mememo !_inst_ver! installed with dev/test tools
    echo !_inst_ver!>"%VENV_DIR%\.mememo_installed"
    goto install_done

:install_prod
    echo [INFO] Installing mememo (production)...
    pip install -e .
    if errorlevel 1 (
        echo [ERROR] Installation failed
        exit /b 1
    )
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
        echo [INFO] Tip: Run 'install.bat -c desktop' to auto-configure Claude Desktop
        echo [INFO]      Run 'install.bat -c code'    to auto-configure Claude Code CLI
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
    echo      install.bat -c desktop   (Claude Desktop)
    echo      install.bat -c code      (Claude Code CLI)
    echo.
    goto end

:show_configured
    if /i "%CLIENT%"=="code" (
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
    echo   Run: install.bat -c desktop   (Claude Desktop)
    echo        install.bat -c code      (Claude Code CLI)
    echo        install.bat -c kilo      (Kilo Code)
    echo        install.bat -c opencode  (OpenCode)
    echo        install.bat -c goose     (Goose)
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
    set "_py_path=%CD%\%VENV_DIR%\Scripts\python.exe"
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
    set "_py_path=%CD%\%VENV_DIR%\Scripts\python.exe"

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

if /i "!_cct!"=="desktop"  goto :cc_desktop
if /i "!_cct!"=="code"     goto :cc_code
if /i "!_cct!"=="kilo"     goto :cc_kilo
if /i "!_cct!"=="opencode" goto :cc_opencode
if /i "!_cct!"=="goose"    goto :cc_goose
if /i "!_cct!"=="both"     goto :cc_both
if /i "!_cct!"=="all"      goto :cc_all
echo [ERROR] Unknown client type: !_cct!
goto :eof

:cc_desktop
echo [INFO] Client: Claude Desktop
set "_cfg_d=%APPDATA%\Claude\claude_desktop_config.json"
if not exist "%APPDATA%\Claude" mkdir "%APPDATA%\Claude"
call :_configure_mcp_json "!_cfg_d!" "!_cpy!" mememo
goto :eof

:cc_code
echo [INFO] Client: Claude Code
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
claude mcp remove mememo --scope user >nul 2>&1
claude mcp add --scope user mememo -- "!_cpy!" -m mememo
if errorlevel 1 (
    echo [WARN] Auto-configuration failed. Add manually:
    echo        claude mcp add --scope user mememo -- "!_cpy!" -m mememo
) else (
    echo [OK] Claude Code MCP server configured ^(user scope^)
    echo [INFO] Verify with: claude mcp list
)
goto :eof

:cc_kilo
echo [INFO] Client: Kilo Code
for %%I in ("%CD%") do set "_kilo_parent=%%~dpI"
if "!_kilo_parent:~-1!"=="\" set "_kilo_parent=!_kilo_parent:~0,-1!"
set "_kilo_cfg=!_kilo_parent!\.kilocode\mcp.json"
echo [INFO] Config: !_kilo_cfg!
call :_configure_mcp_json "!_kilo_cfg!" "!_cpy!" mememo
goto :eof

:cc_opencode
echo [INFO] Client: OpenCode
if "%GLOBAL_CONFIG%"=="true" (
    set "_oc_cfg=%USERPROFILE%\.config\opencode\opencode.json"
) else (
    for %%I in ("%CD%") do set "_oc_parent=%%~dpI"
    if "!_oc_parent:~-1!"=="\" set "_oc_parent=!_oc_parent:~0,-1!"
    set "_oc_cfg=!_oc_parent!\opencode.json"
)
echo [INFO] Config: !_oc_cfg!
call :_configure_opencode_json "!_oc_cfg!" "!_cpy!"
goto :eof

:cc_goose
echo [INFO] Client: Goose
set "_goose_cfg=%USERPROFILE%\.config\goose\config.yaml"
echo [INFO] Config: !_goose_cfg!
call :_configure_goose_yaml "!_goose_cfg!" "!_cpy!"
goto :eof

:cc_both
call :configure_client "desktop" "!_cpy!"
echo.
call :configure_client "code" "!_cpy!"
goto :eof

:cc_all
call :configure_client "desktop" "!_cpy!"
echo.
call :configure_client "code" "!_cpy!"
for %%I in ("%CD%") do set "_all_parent=%%~dpI"
if "!_all_parent:~-1!"=="\" set "_all_parent=!_all_parent:~0,-1!"
set "_all_kilo=!_all_parent!\.kilocode\mcp.json"
set "_all_oc=!_all_parent!\opencode.json"
set "_all_oc_g=%USERPROFILE%\.config\opencode\opencode.json"
set "_all_goose=%USERPROFILE%\.config\goose\config.yaml"
if "%UNINSTALL%"=="true" (
    echo.
    call :configure_client "kilo" "!_cpy!"
    echo.
    call :configure_client "opencode" "!_cpy!"
    echo.
    call :configure_client "goose" "!_cpy!"
) else (
    if exist "!_all_kilo!" (
        echo.
        call :configure_client "kilo" "!_cpy!"
    )
    if exist "!_all_oc!" (
        echo.
        call :configure_client "opencode" "!_cpy!"
    ) else if exist "!_all_oc_g!" (
        echo.
        call :configure_client "opencode" "!_cpy!"
    )
    if exist "!_all_goose!" (
        echo.
        call :configure_client "goose" "!_cpy!"
    )
)
goto :eof

rem ════════════════════════════════════════════════════════
rem Subroutine: _configure_mcp_json <config_path> <python_path> <server_name>
rem Writes standard mcpServers format (used by desktop, code-file, kilo)
:_configure_mcp_json
set "_mcfg=%~1"
set "_mpy=%~2"
set "_mname=%~3"

if "%UNINSTALL%"=="true" (
    if not exist "!_mcfg!" ( echo [INFO] Config not found, nothing to remove & goto :eof )
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_mcfg!" "!_mcfg!.backup.!_ts!" >nul
    "!_mpy!" -c "import json,sys,os; cfg=json.load(open(sys.argv[1])) if os.path.exists(sys.argv[1]) else {}; s=cfg.get('mcpServers',{}); (s.pop(sys.argv[2],None) or True) and s.__class__; open(sys.argv[1],'w').write(json.dumps(cfg,indent=2)+'\n'); print('[OK] Removed ' + sys.argv[2] + ' from config') if sys.argv[2] not in s else print('[INFO] Entry not found')" "!_mcfg!" "!_mname!" 2>nul
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
rem Subroutine: _configure_opencode_json <config_path> <python_path>
:_configure_opencode_json
set "_ocfg=%~1"
set "_opy=%~2"

if "%UNINSTALL%"=="true" (
    if not exist "!_ocfg!" ( echo [INFO] Config not found, nothing to remove & goto :eof )
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ocfg!" "!_ocfg!.backup.!_ts!" >nul
    set "_tmp_rm=%TEMP%\mememo_rm_oc_%RANDOM%.py"
    echo import json, sys, os > "!_tmp_rm!"
    echo cfg=json.load(open(sys.argv[1])) if os.path.exists(sys.argv[1]) else {} >> "!_tmp_rm!"
    echo mcp=cfg.get('mcp',{}) >> "!_tmp_rm!"
    echo if 'mememo' in mcp: >> "!_tmp_rm!"
    echo     del mcp['mememo'] >> "!_tmp_rm!"
    echo     open(sys.argv[1],'w').write(json.dumps(cfg,indent=2)+'\n') >> "!_tmp_rm!"
    echo     print('[OK] Removed mememo from config') >> "!_tmp_rm!"
    echo else: print('[INFO] mememo not found in config') >> "!_tmp_rm!"
    "!_opy!" "!_tmp_rm!" "!_ocfg!"
    del /f /q "!_tmp_rm!" 2>nul
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
    if not exist "!_gcfg!" ( echo [INFO] Config not found, nothing to remove & goto :eof )
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
    echo else: print('[INFO] mememo not found in config') >> "!_tmp_rg!"
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
    echo   -c, --client TYPE   MCP client: desktop, code, kilo, opencode, goose, all
    echo   -f, --force         Skip prompts, overwrite existing config
    echo   -u, --uninstall     Remove mememo from MCP client config and virtual environment
    echo       --upgrade       Upgrade existing installation
    echo       --global        Use global config path (applies to: code, opencode, all)
    echo       --skip-test     Skip warmup validation step
    echo       --dev           Install dev/test dependencies
    echo   -h, --help          Show this help
    echo.
    echo Backward-compatible aliases:
    echo   --configure=claude      same as -c desktop
    echo   --configure=claudecli   same as -c code
    echo.
    echo Examples:
    echo   install.bat -c desktop         Configure Claude Desktop
    echo   install.bat -c code            Configure Claude Code
    echo   install.bat -c kilo            Configure Kilo Code
    echo   install.bat -c opencode        Configure OpenCode (workspace)
    echo   install.bat -c opencode --global Configure OpenCode (global)
    echo   install.bat -c goose           Configure Goose
    echo   install.bat -c all             Configure all detected clients
    echo   install.bat --upgrade          Upgrade existing installation
    echo   install.bat -u -c all          Uninstall from all client configs
    goto end

:end
endlocal

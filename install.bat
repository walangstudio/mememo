@echo off
REM mememo installer for Windows
setlocal enabledelayedexpansion

set VENV_DIR=.venv
set PYTHON_MIN=3.10
set "SCRIPT_DIR=%~dp0"
if "!SCRIPT_DIR:~-1!"=="\" set "SCRIPT_DIR=!SCRIPT_DIR:~0,-1!"

rem -- Defaults ---------------------------------------------
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

rem -- Config paths -----------------------------------------
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

rem -- Write Python helper scripts to temp ---------------
set "PY_MERGE=%TEMP%\mememo_merge.py"
set "PY_REMOVE=%TEMP%\mememo_remove.py"
set "PY_MERGE_VSCODE=%TEMP%\mememo_merge_vscode.py"
set "PY_REMOVE_VSCODE=%TEMP%\mememo_remove_vscode.py"
set "PY_MERGE_CODEX=%TEMP%\mememo_merge_codex.py"
set "PY_REMOVE_CODEX=%TEMP%\mememo_remove_codex.py"
set "PY_MERGE_ZED=%TEMP%\mememo_merge_zed.py"
set "PY_REMOVE_ZED=%TEMP%\mememo_remove_zed.py"
set "PY_MERGE_OPENCODE=%TEMP%\mememo_merge_opencode.py"
set "PY_REMOVE_OPENCODE=%TEMP%\mememo_remove_opencode.py"
set "PY_MERGE_GOOSE=%TEMP%\mememo_merge_goose.py"
set "PY_REMOVE_GOOSE=%TEMP%\mememo_remove_goose.py"
set "PY_STATUS=%TEMP%\mememo_status.py"

echo import json, sys, os > "%PY_MERGE%"
echo config_path = os.path.abspath(sys.argv[1]) >> "%PY_MERGE%"
echo python_path = sys.executable >> "%PY_MERGE%"
echo name = sys.argv[2] >> "%PY_MERGE%"
echo try: >> "%PY_MERGE%"
echo     with open(config_path) as f: config = json.load(f) >> "%PY_MERGE%"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "%PY_MERGE%"
echo config.setdefault('mcpServers', {}) >> "%PY_MERGE%"
echo config['mcpServers'][name] = {'command': python_path, 'args': ['-m', name]} >> "%PY_MERGE%"
echo d = os.path.dirname(config_path) >> "%PY_MERGE%"
echo if d: os.makedirs(d, exist_ok=True) >> "%PY_MERGE%"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "%PY_MERGE%"

echo import json, sys, os > "%PY_REMOVE%"
echo config_path = sys.argv[1]; name = sys.argv[2] >> "%PY_REMOVE%"
echo lbl = sys.argv[3] if sys.argv[3:] else name >> "%PY_REMOVE%"
echo if not os.path.exists(config_path): sys.exit(0) >> "%PY_REMOVE%"
echo try: >> "%PY_REMOVE%"
echo     with open(config_path) as f: cfg = json.load(f) >> "%PY_REMOVE%"
echo except: sys.exit(0) >> "%PY_REMOVE%"
echo changed = False >> "%PY_REMOVE%"
echo s = cfg.get('mcpServers', {}) >> "%PY_REMOVE%"
echo if name in s: del s[name]; changed = True >> "%PY_REMOVE%"
echo for pv in cfg.get('projects', {}).values(): >> "%PY_REMOVE%"
echo     ps = pv.get('mcpServers', {}) if isinstance(pv, dict) else {} >> "%PY_REMOVE%"
echo     if name in ps: del ps[name]; changed = True >> "%PY_REMOVE%"
echo if changed: >> "%PY_REMOVE%"
echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "%PY_REMOVE%"
echo     print('[OK] Removed from ' + lbl) >> "%PY_REMOVE%"

echo import json, sys, os > "%PY_MERGE_VSCODE%"
echo config_path = os.path.abspath(sys.argv[1]) >> "%PY_MERGE_VSCODE%"
echo python_path = sys.executable >> "%PY_MERGE_VSCODE%"
echo try: >> "%PY_MERGE_VSCODE%"
echo     with open(config_path) as f: config = json.load(f) >> "%PY_MERGE_VSCODE%"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "%PY_MERGE_VSCODE%"
echo config.setdefault('servers', {}) >> "%PY_MERGE_VSCODE%"
echo config['servers']['mememo'] = {'type': 'stdio', 'command': python_path, 'args': ['-m', 'mememo']} >> "%PY_MERGE_VSCODE%"
echo d = os.path.dirname(config_path) >> "%PY_MERGE_VSCODE%"
echo if d: os.makedirs(d, exist_ok=True) >> "%PY_MERGE_VSCODE%"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "%PY_MERGE_VSCODE%"

echo import json, sys, os > "%PY_REMOVE_VSCODE%"
echo config_path = sys.argv[1] >> "%PY_REMOVE_VSCODE%"
echo if not os.path.exists(config_path): sys.exit(0) >> "%PY_REMOVE_VSCODE%"
echo try: >> "%PY_REMOVE_VSCODE%"
echo     with open(config_path) as f: cfg = json.load(f) >> "%PY_REMOVE_VSCODE%"
echo except: sys.exit(0) >> "%PY_REMOVE_VSCODE%"
echo s = cfg.get('servers', {}) >> "%PY_REMOVE_VSCODE%"
echo if 'mememo' in s: >> "%PY_REMOVE_VSCODE%"
echo     del s['mememo'] >> "%PY_REMOVE_VSCODE%"
echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "%PY_REMOVE_VSCODE%"
echo     print('[OK] Removed mememo from VS Code config') >> "%PY_REMOVE_VSCODE%"

echo import sys, os > "%PY_MERGE_CODEX%"
echo config_path = os.path.abspath(sys.argv[1]) >> "%PY_MERGE_CODEX%"
echo python_path = sys.executable >> "%PY_MERGE_CODEX%"
echo sn = 'mememo' >> "%PY_MERGE_CODEX%"
echo section_header = '[mcp_servers.' + sn + ']' >> "%PY_MERGE_CODEX%"
echo cmd = python_path + ' -m ' + sn >> "%PY_MERGE_CODEX%"
echo new_section = '\n' + section_header + '\ncommand = "' + cmd + '"\nstartup_timeout_sec = 30\ntool_timeout_sec = 300\nenabled = true\n' >> "%PY_MERGE_CODEX%"
echo os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True) >> "%PY_MERGE_CODEX%"
echo existing = '' >> "%PY_MERGE_CODEX%"
echo try: >> "%PY_MERGE_CODEX%"
echo     with open(config_path) as f: existing = f.read() >> "%PY_MERGE_CODEX%"
echo except FileNotFoundError: pass >> "%PY_MERGE_CODEX%"
echo if section_header in existing: >> "%PY_MERGE_CODEX%"
echo     lines = existing.split('\n') >> "%PY_MERGE_CODEX%"
echo     start = next((i for i, l in enumerate(lines) if l.strip() == section_header), -1) >> "%PY_MERGE_CODEX%"
echo     if start != -1: >> "%PY_MERGE_CODEX%"
echo         end = len(lines) >> "%PY_MERGE_CODEX%"
echo         for i in range(start + 1, len(lines)): >> "%PY_MERGE_CODEX%"
echo             if lines[i].startswith('['): end = i; break >> "%PY_MERGE_CODEX%"
echo         del lines[start:end] >> "%PY_MERGE_CODEX%"
echo         existing = '\n'.join(lines) >> "%PY_MERGE_CODEX%"
echo existing = existing.rstrip() >> "%PY_MERGE_CODEX%"
echo if existing: existing += '\n' >> "%PY_MERGE_CODEX%"
echo with open(config_path, 'w') as f: f.write(existing + new_section) >> "%PY_MERGE_CODEX%"

echo import sys, os > "%PY_REMOVE_CODEX%"
echo config_path = sys.argv[1] >> "%PY_REMOVE_CODEX%"
echo sn = 'mememo' >> "%PY_REMOVE_CODEX%"
echo section_header = '[mcp_servers.' + sn + ']' >> "%PY_REMOVE_CODEX%"
echo if not os.path.exists(config_path): sys.exit(0) >> "%PY_REMOVE_CODEX%"
echo with open(config_path) as f: existing = f.read() >> "%PY_REMOVE_CODEX%"
echo if section_header not in existing: sys.exit(0) >> "%PY_REMOVE_CODEX%"
echo lines = existing.split('\n') >> "%PY_REMOVE_CODEX%"
echo start = next((i for i, l in enumerate(lines) if l.strip() == section_header), -1) >> "%PY_REMOVE_CODEX%"
echo if start != -1: >> "%PY_REMOVE_CODEX%"
echo     end = len(lines) >> "%PY_REMOVE_CODEX%"
echo     for i in range(start + 1, len(lines)): >> "%PY_REMOVE_CODEX%"
echo         if lines[i].startswith('['): end = i; break >> "%PY_REMOVE_CODEX%"
echo     del lines[start:end] >> "%PY_REMOVE_CODEX%"
echo     with open(config_path, 'w') as f: f.write('\n'.join(lines)) >> "%PY_REMOVE_CODEX%"
echo     print('[OK] Removed mememo from codex config') >> "%PY_REMOVE_CODEX%"

echo import json, sys, os > "%PY_MERGE_ZED%"
echo config_path = os.path.abspath(sys.argv[1]) >> "%PY_MERGE_ZED%"
echo python_path = sys.executable >> "%PY_MERGE_ZED%"
echo try: >> "%PY_MERGE_ZED%"
echo     with open(config_path) as f: config = json.load(f) >> "%PY_MERGE_ZED%"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "%PY_MERGE_ZED%"
echo config.setdefault('context_servers', {}) >> "%PY_MERGE_ZED%"
echo config['context_servers']['mememo'] = {'command': {'path': python_path, 'args': ['-m', 'mememo'], 'env': {}}} >> "%PY_MERGE_ZED%"
echo os.makedirs(os.path.dirname(config_path), exist_ok=True) >> "%PY_MERGE_ZED%"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "%PY_MERGE_ZED%"

echo import json, sys, os > "%PY_REMOVE_ZED%"
echo config_path = sys.argv[1] >> "%PY_REMOVE_ZED%"
echo if not os.path.exists(config_path): sys.exit(0) >> "%PY_REMOVE_ZED%"
echo try: >> "%PY_REMOVE_ZED%"
echo     with open(config_path) as f: cfg = json.load(f) >> "%PY_REMOVE_ZED%"
echo except: sys.exit(0) >> "%PY_REMOVE_ZED%"
echo cs = cfg.get('context_servers', {}) >> "%PY_REMOVE_ZED%"
echo if 'mememo' in cs: >> "%PY_REMOVE_ZED%"
echo     del cs['mememo'] >> "%PY_REMOVE_ZED%"
echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "%PY_REMOVE_ZED%"
echo     print('[OK] Removed mememo from Zed config') >> "%PY_REMOVE_ZED%"

echo import json, sys, os > "%PY_MERGE_OPENCODE%"
echo config_path = os.path.abspath(sys.argv[1]) >> "%PY_MERGE_OPENCODE%"
echo python_path = sys.executable >> "%PY_MERGE_OPENCODE%"
echo try: >> "%PY_MERGE_OPENCODE%"
echo     with open(config_path) as f: config = json.load(f) >> "%PY_MERGE_OPENCODE%"
echo except (FileNotFoundError, json.JSONDecodeError): config = {} >> "%PY_MERGE_OPENCODE%"
echo config.setdefault('mcp', {}) >> "%PY_MERGE_OPENCODE%"
echo config['mcp']['mememo'] = {'type': 'local', 'command': [python_path, '-m', 'mememo']} >> "%PY_MERGE_OPENCODE%"
echo d = os.path.dirname(config_path) >> "%PY_MERGE_OPENCODE%"
echo if d: os.makedirs(d, exist_ok=True) >> "%PY_MERGE_OPENCODE%"
echo with open(config_path, 'w') as f: json.dump(config, f, indent=2); f.write('\n') >> "%PY_MERGE_OPENCODE%"

echo import json, sys, os > "%PY_REMOVE_OPENCODE%"
echo config_path = sys.argv[1] >> "%PY_REMOVE_OPENCODE%"
echo if not os.path.exists(config_path): sys.exit(0) >> "%PY_REMOVE_OPENCODE%"
echo try: >> "%PY_REMOVE_OPENCODE%"
echo     with open(config_path) as f: cfg = json.load(f) >> "%PY_REMOVE_OPENCODE%"
echo except: sys.exit(0) >> "%PY_REMOVE_OPENCODE%"
echo mcp = cfg.get('mcp', {}) >> "%PY_REMOVE_OPENCODE%"
echo if 'mememo' in mcp: >> "%PY_REMOVE_OPENCODE%"
echo     del mcp['mememo'] >> "%PY_REMOVE_OPENCODE%"
echo     with open(config_path, 'w') as f: json.dump(cfg, f, indent=2); f.write('\n') >> "%PY_REMOVE_OPENCODE%"
echo     print('[OK] Removed from OpenCode') >> "%PY_REMOVE_OPENCODE%"

echo import sys, os > "%PY_MERGE_GOOSE%"
echo try: import yaml >> "%PY_MERGE_GOOSE%"
echo except ImportError: >> "%PY_MERGE_GOOSE%"
echo     py = sys.executable >> "%PY_MERGE_GOOSE%"
echo     print('[WARN] PyYAML not available. Add manually to config.yaml:') >> "%PY_MERGE_GOOSE%"
echo     print('extensions:') >> "%PY_MERGE_GOOSE%"
echo     print('  mememo:') >> "%PY_MERGE_GOOSE%"
echo     print('    name: mememo') >> "%PY_MERGE_GOOSE%"
echo     print('    type: stdio') >> "%PY_MERGE_GOOSE%"
echo     print('    cmd: ' + py) >> "%PY_MERGE_GOOSE%"
echo     print('    args: [\"-m\", \"mememo\"]') >> "%PY_MERGE_GOOSE%"
echo     print('    enabled: true') >> "%PY_MERGE_GOOSE%"
echo     sys.exit(0) >> "%PY_MERGE_GOOSE%"
echo config_path = os.path.abspath(sys.argv[1]) >> "%PY_MERGE_GOOSE%"
echo python_path = sys.executable >> "%PY_MERGE_GOOSE%"
echo try: >> "%PY_MERGE_GOOSE%"
echo     with open(config_path) as f: config = yaml.safe_load(f) or {} >> "%PY_MERGE_GOOSE%"
echo except FileNotFoundError: config = {} >> "%PY_MERGE_GOOSE%"
echo config.setdefault('extensions', {}) >> "%PY_MERGE_GOOSE%"
echo config['extensions']['mememo'] = {'name': 'mememo', 'type': 'stdio', 'cmd': python_path, 'args': ['-m', 'mememo'], 'enabled': True} >> "%PY_MERGE_GOOSE%"
echo d = os.path.dirname(config_path) >> "%PY_MERGE_GOOSE%"
echo if d: os.makedirs(d, exist_ok=True) >> "%PY_MERGE_GOOSE%"
echo with open(config_path, 'w') as f: yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False) >> "%PY_MERGE_GOOSE%"

echo import sys, os > "%PY_REMOVE_GOOSE%"
echo try: import yaml >> "%PY_REMOVE_GOOSE%"
echo except ImportError: print('[WARN] PyYAML not available'); sys.exit(0) >> "%PY_REMOVE_GOOSE%"
echo cfg_path = sys.argv[1] >> "%PY_REMOVE_GOOSE%"
echo if not os.path.exists(cfg_path): sys.exit(0) >> "%PY_REMOVE_GOOSE%"
echo with open(cfg_path) as f: config = yaml.safe_load(f) or {} >> "%PY_REMOVE_GOOSE%"
echo ext = config.get('extensions', {}) >> "%PY_REMOVE_GOOSE%"
echo if 'mememo' in ext: >> "%PY_REMOVE_GOOSE%"
echo     del ext['mememo'] >> "%PY_REMOVE_GOOSE%"
echo     with open(cfg_path, 'w') as f: yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False) >> "%PY_REMOVE_GOOSE%"
echo     print('[OK] Removed mememo from Goose config') >> "%PY_REMOVE_GOOSE%"

echo import json, os > "%PY_STATUS%"
echo def chk(p, fmt): >> "%PY_STATUS%"
echo     if not os.path.exists(p): return False >> "%PY_STATUS%"
echo     try: >> "%PY_STATUS%"
echo         with open(p) as f: raw = f.read() >> "%PY_STATUS%"
echo         if fmt == 'toml': return '[mcp_servers.mememo]' in raw >> "%PY_STATUS%"
echo         if fmt == 'yaml': return '  mememo:' in raw >> "%PY_STATUS%"
echo         return 'mememo' in json.load(open(p)).get('mcpServers', {}) >> "%PY_STATUS%"
echo     except: return False >> "%PY_STATUS%"
echo rows = [ >> "%PY_STATUS%"
echo     ('claudedesktop        ', r'!DESKTOP_CONFIG!', 'json'), >> "%PY_STATUS%"
echo     ('claude (workspace)   ', r'!CODE_CONFIG!', 'json'), >> "%PY_STATUS%"
echo     ('claude (global)      ', r'%USERPROFILE%\.claude.json', 'json'), >> "%PY_STATUS%"
echo     ('cursor (workspace)   ', r'!_PARENT!\.cursor\mcp.json', 'json'), >> "%PY_STATUS%"
echo     ('cursor (global)      ', r'%USERPROFILE%\.cursor\mcp.json', 'json'), >> "%PY_STATUS%"
echo     ('windsurf             ', r'!WINDSURF_CONFIG!', 'json'), >> "%PY_STATUS%"
echo     ('vscode (workspace)   ', r'!VSCODE_CONFIG!', 'json'), >> "%PY_STATUS%"
echo     ('gemini (workspace)   ', r'!_PARENT!\.gemini\settings.json', 'json'), >> "%PY_STATUS%"
echo     ('gemini (global)      ', r'%USERPROFILE%\.gemini\settings.json', 'json'), >> "%PY_STATUS%"
echo     ('codex (workspace)    ', r'!_PARENT!\.codex\config.toml', 'toml'), >> "%PY_STATUS%"
echo     ('codex (global)       ', r'%USERPROFILE%\.codex\config.toml', 'toml'), >> "%PY_STATUS%"
echo     ('zed                  ', r'!ZED_CONFIG!', 'json'), >> "%PY_STATUS%"
echo     ('kilo                 ', r'!KILO_CONFIG!', 'json'), >> "%PY_STATUS%"
echo     ('opencode (workspace) ', r'!_PARENT!\opencode.json', 'json'), >> "%PY_STATUS%"
echo     ('opencode (global)    ', r'%USERPROFILE%\.config\opencode\opencode.json', 'json'), >> "%PY_STATUS%"
echo     ('goose                ', r'!GOOSE_CONFIG!', 'yaml'), >> "%PY_STATUS%"
echo ] >> "%PY_STATUS%"
echo for lbl, p, fmt in rows: >> "%PY_STATUS%"
echo     if chk(p, fmt): print(f'   {lbl}  YES  {p}') >> "%PY_STATUS%"
echo     else: print(f'   {lbl}  NO') >> "%PY_STATUS%"

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

rem ========================================================
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

    if not "%CLIENT_EXPLICIT%"=="true" goto :inst_show_tip
    echo.
    call :do_configure
    goto :inst_after_cfg
    :inst_show_tip
    echo [INFO] Tip: Run 'install.bat -c claudedesktop' to auto-configure Claude Desktop
    echo [INFO]      Run 'install.bat -c claude'    to auto-configure Claude Code CLI
    echo [INFO]      Run 'install.bat -c all'       to configure all detected clients
    echo [INFO]      Run 'install.bat --status'     to show all install locations
    :inst_after_cfg

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

rem ========================================================
:do_configure_only
    call %VENV_DIR%\Scripts\activate.bat
    call :do_configure
    goto end

rem ========================================================
:do_configure
    set "_py_path=%SCRIPT_DIR%\%VENV_DIR%\Scripts\python.exe"
    if not exist "!_py_path!" (
        echo [ERROR] Python not found at !_py_path!
        exit /b
    )
    call :configure_client "%CLIENT%" "!_py_path!"
    exit /b

rem ========================================================
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

    if not "%CLIENT_EXPLICIT%"=="true" goto end
    echo.
    call :do_configure
    goto end

rem ========================================================
:do_uninstall
    set "_py_path=%SCRIPT_DIR%\%VENV_DIR%\Scripts\python.exe"
    if not exist "!_py_path!" (
        for %%P in (python python3) do (
            where %%P >nul 2>&1 && set "_py_path=%%P"
        )
    )

    if not "%CLIENT_EXPLICIT%"=="true" goto :du_skip_cfg
    call :configure_client "%CLIENT%" "!_py_path!"
    echo.
    :du_skip_cfg

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

rem ========================================================
:configure_client
set "_cct=%~1"
set "_cpy=%~2"
if /i "!_cct!"=="claudedesktop" goto :cc_desktop
if /i "!_cct!"=="claude"        goto :cc_code
if /i "!_cct!"=="cursor"        goto :cc_cursor
if /i "!_cct!"=="windsurf"      goto :cc_windsurf
if /i "!_cct!"=="vscode"        goto :cc_vscode
if /i "!_cct!"=="gemini"        goto :cc_gemini
if /i "!_cct!"=="codex"         goto :cc_codex
if /i "!_cct!"=="zed"           goto :cc_zed
if /i "!_cct!"=="kilo"          goto :cc_kilo
if /i "!_cct!"=="opencode"      goto :cc_opencode
if /i "!_cct!"=="goose"         goto :cc_goose
if /i "!_cct!"=="pidev"         goto :cc_pidev
if /i "!_cct!"=="both"          goto :cc_both
if /i "!_cct!"=="all"           goto :cc_all
echo [ERROR] Unknown client type: !_cct!
exit /b

:cc_desktop
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Claude Desktop & if not exist "%APPDATA%\Claude" mkdir "%APPDATA%\Claude" )
call :cfg_mcp_json "!DESKTOP_CONFIG!" "!_cpy!" mememo "Claude Desktop"
exit /b

:cc_code
if "%UNINSTALL%"=="true" goto :cc_code_rm
echo [INFO] Client: Claude Code
where claude >nul 2>&1
if errorlevel 1 ( echo [ERROR] Claude CLI not found. Install it first: https^://claude.ai/download & exit /b )
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
exit /b
:cc_code_rm
where claude >nul 2>&1
if not errorlevel 1 (
    claude mcp remove mememo --scope user >nul 2>&1
    echo [OK] Removed mememo from Claude Code ^(user scope^)
    pushd "!SCRIPT_DIR!" >nul 2>&1
    claude mcp remove mememo --scope project >nul 2>&1
    popd >nul 2>&1
)
call :cfg_mcp_json "%USERPROFILE%\.claude.json" "!_cpy!" mememo "Claude Code (global)"
call :cfg_mcp_json "!CODE_CONFIG!" "!_cpy!" mememo "Claude Code (project)"
exit /b

:cc_cursor
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Cursor & echo [INFO] Config: !CURSOR_CONFIG! )
call :cfg_mcp_json "!CURSOR_CONFIG!" "!_cpy!" mememo "Cursor"
exit /b

:cc_windsurf
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Windsurf ^(global^) & echo [INFO] Config: !WINDSURF_CONFIG! )
call :cfg_mcp_json "!WINDSURF_CONFIG!" "!_cpy!" mememo "Windsurf"
exit /b

:cc_vscode
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: VS Code ^(workspace^) & echo [INFO] Config: !VSCODE_CONFIG! )
call :cfg_vscode_json "!VSCODE_CONFIG!" "!_cpy!"
exit /b

:cc_gemini
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Gemini CLI & echo [INFO] Config: !GEMINI_CONFIG! )
call :cfg_mcp_json "!GEMINI_CONFIG!" "!_cpy!" mememo "Gemini CLI"
exit /b

:cc_codex
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: OpenAI Codex CLI & echo [INFO] Config: !CODEX_CONFIG! )
call :cfg_codex_toml "!CODEX_CONFIG!" "!_cpy!"
exit /b

:cc_zed
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Zed ^(global^) & echo [INFO] Config: !ZED_CONFIG! )
call :cfg_zed_json "!ZED_CONFIG!" "!_cpy!"
exit /b

:cc_kilo
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Kilo Code & echo [INFO] Config: !KILO_CONFIG! )
call :cfg_mcp_json "!KILO_CONFIG!" "!_cpy!" mememo "Kilo Code"
exit /b

:cc_opencode
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: OpenCode & echo [INFO] Config: !OPENCODE_CONFIG! )
call :cfg_opencode_json "!OPENCODE_CONFIG!" "!_cpy!"
exit /b

:cc_goose
if not "%UNINSTALL%"=="true" ( echo [INFO] Client: Goose & echo [INFO] Config: !GOOSE_CONFIG! )
call :cfg_goose_yaml "!GOOSE_CONFIG!" "!_cpy!"
exit /b

:cc_pidev
echo [INFO] Client: pi.dev
echo.
echo   pi.dev does not support MCP servers natively.
echo   pi.dev uses TypeScript extensions and CLI tools instead.
echo   To use mememo concepts in pi.dev, see: https^://pi.dev/docs/extensions
echo.
exit /b

:cc_both
call :configure_client claudedesktop "!_cpy!"
echo.
call :configure_client claude "!_cpy!"
exit /b

:cc_all
call :configure_client claudedesktop "!_cpy!"
echo.
call :configure_client claude "!_cpy!"
if "%UNINSTALL%"=="true" goto :cc_all_rm
if exist "!_PARENT!\.cursor\mcp.json"                   echo. & call :configure_client cursor   "!_cpy!"
if exist "%USERPROFILE%\.cursor\mcp.json"               echo. & call :configure_client cursor   "!_cpy!"
if exist "!WINDSURF_CONFIG!"                            echo. & call :configure_client windsurf "!_cpy!"
if exist "!VSCODE_CONFIG!"                              echo. & call :configure_client vscode   "!_cpy!"
if exist "!_PARENT!\.gemini\settings.json"              echo. & call :configure_client gemini   "!_cpy!"
if exist "%USERPROFILE%\.gemini\settings.json"          echo. & call :configure_client gemini   "!_cpy!"
if exist "!_PARENT!\.codex\config.toml"                 echo. & call :configure_client codex    "!_cpy!"
if exist "%USERPROFILE%\.codex\config.toml"             echo. & call :configure_client codex    "!_cpy!"
if exist "!ZED_CONFIG!"                                 echo. & call :configure_client zed      "!_cpy!"
if exist "!KILO_CONFIG!"                                echo. & call :configure_client kilo     "!_cpy!"
if exist "!OPENCODE_CONFIG!"                            echo. & call :configure_client opencode "!_cpy!"
if exist "%USERPROFILE%\.config\opencode\opencode.json" echo. & call :configure_client opencode "!_cpy!"
if exist "!GOOSE_CONFIG!"                               echo. & call :configure_client goose    "!_cpy!"
exit /b
:cc_all_rm
echo. & call :configure_client cursor   "!_cpy!"
echo. & call :configure_client windsurf "!_cpy!"
echo. & call :configure_client vscode   "!_cpy!"
echo. & call :configure_client gemini   "!_cpy!"
echo. & call :configure_client codex    "!_cpy!"
echo. & call :configure_client zed      "!_cpy!"
echo. & call :configure_client kilo     "!_cpy!"
echo. & call :configure_client opencode "!_cpy!"
echo. & call :configure_client goose    "!_cpy!"
exit /b

rem ========================================================
rem Subroutine: cfg_mcp_json <config_path> <python_path> <server_name> [label]
:cfg_mcp_json
set "_mcfg=%~1"
set "_mpy=%~2"
set "_mname=%~3"
set "_mlbl=%~4"
if "%UNINSTALL%"=="true" goto cfg_mcp_rm
if exist "!_mcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_mcfg!" "!_mcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)
"!_mpy!" "%PY_MERGE%" "!_mcfg!" "!_mname!"
echo [OK] MCP config updated at !_mcfg!
exit /b
:cfg_mcp_rm
if not exist "!_mcfg!" exit /b
for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
copy /y "!_mcfg!" "!_mcfg!.backup.!_ts!" >nul
"!_mpy!" "%PY_REMOVE%" "!_mcfg!" "!_mname!" "!_mlbl!"
exit /b

rem ========================================================
rem Subroutine: cfg_vscode_json <config_path> <python_path>
:cfg_vscode_json
set "_vcfg=%~1"
set "_vpy=%~2"
if "%UNINSTALL%"=="true" goto cfg_vsc_rm
if exist "!_vcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_vcfg!" "!_vcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)
"!_vpy!" "%PY_MERGE_VSCODE%" "!_vcfg!"
echo [OK] VS Code MCP config updated at !_vcfg!
exit /b
:cfg_vsc_rm
if not exist "!_vcfg!" exit /b
for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
copy /y "!_vcfg!" "!_vcfg!.backup.!_ts!" >nul
"!_vpy!" "%PY_REMOVE_VSCODE%" "!_vcfg!"
exit /b

rem ========================================================
rem Subroutine: cfg_codex_toml <config_path> <python_path>
:cfg_codex_toml
set "_ccfg=%~1"
set "_cpy2=%~2"
if "%UNINSTALL%"=="true" goto cfg_cdx_rm
if exist "!_ccfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ccfg!" "!_ccfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)
"!_cpy2!" "%PY_MERGE_CODEX%" "!_ccfg!"
echo [OK] Codex TOML config updated at !_ccfg!
exit /b
:cfg_cdx_rm
if not exist "!_ccfg!" exit /b
for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
copy /y "!_ccfg!" "!_ccfg!.backup.!_ts!" >nul
"!_cpy2!" "%PY_REMOVE_CODEX%" "!_ccfg!"
exit /b

rem ========================================================
rem Subroutine: cfg_zed_json <config_path> <python_path>
:cfg_zed_json
set "_zcfg=%~1"
set "_zpy=%~2"
if "%UNINSTALL%"=="true" goto cfg_zed_rm
if exist "!_zcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_zcfg!" "!_zcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)
"!_zpy!" "%PY_MERGE_ZED%" "!_zcfg!"
echo [OK] Zed config updated at !_zcfg!
exit /b
:cfg_zed_rm
if not exist "!_zcfg!" exit /b
for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
copy /y "!_zcfg!" "!_zcfg!.backup.!_ts!" >nul
"!_zpy!" "%PY_REMOVE_ZED%" "!_zcfg!"
exit /b

rem ========================================================
rem Subroutine: cfg_opencode_json <config_path> <python_path>
:cfg_opencode_json
set "_ocfg=%~1"
set "_opy=%~2"
if "%UNINSTALL%"=="true" goto cfg_oc_rm
if exist "!_ocfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_ocfg!" "!_ocfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)
"!_opy!" "%PY_MERGE_OPENCODE%" "!_ocfg!"
echo [OK] OpenCode MCP config updated at !_ocfg!
exit /b
:cfg_oc_rm
if not exist "!_ocfg!" exit /b
for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
copy /y "!_ocfg!" "!_ocfg!.backup.!_ts!" >nul
"!_opy!" "%PY_REMOVE_OPENCODE%" "!_ocfg!"
exit /b

rem ========================================================
rem Subroutine: cfg_goose_yaml <config_path> <python_path>
:cfg_goose_yaml
set "_gcfg=%~1"
set "_gpy=%~2"
if "%UNINSTALL%"=="true" goto cfg_goo_rm
if exist "!_gcfg!" (
    for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
    copy /y "!_gcfg!" "!_gcfg!.backup.!_ts!" >nul
    echo [INFO] Backed up existing config
)
"!_gpy!" "%PY_MERGE_GOOSE%" "!_gcfg!"
echo [OK] Goose MCP config updated at !_gcfg!
exit /b
:cfg_goo_rm
if not exist "!_gcfg!" exit /b
for /f "tokens=*" %%T in ('powershell -command "Get-Date -Format yyyyMMddHHmmss"') do set "_ts=%%T"
copy /y "!_gcfg!" "!_gcfg!.backup.!_ts!" >nul
"!_gpy!" "%PY_REMOVE_GOOSE%" "!_gcfg!"
exit /b

rem ========================================================
:show_status
set "_spy=%~1"
set "_inst_ver=not installed"
if exist "%VENV_DIR%\.mememo_installed" (
    for /f "usebackq delims=" %%v in ("%VENV_DIR%\.mememo_installed") do set "_inst_ver=%%v"
)
echo.
echo   mememo -- Status
echo   ------------------------------------------------------------------------
echo   Client               Installed  Config path
echo   ------------------------------------------------------------------------
"!_spy!" "%PY_STATUS%"
echo   ------------------------------------------------------------------------
echo   Package: !_inst_ver!
echo.
exit /b

rem ========================================================
:run_warmup
    echo [INFO] Pre-warming bytecode cache and embedding model (this runs once)...
    python warmup.py
    if errorlevel 1 (
        echo [WARN] Warmup had a non-fatal error -- first MCP startup may be slow
    ) else (
        echo [OK] Warmup complete
    )
    exit /b

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

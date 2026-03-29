#!/usr/bin/env bash
set -e

# -- Config ------------------------------------------------
VENV_DIR=".venv"
MARKER="$VENV_DIR/.mememo_installed"
PYTHON_MIN_VERSION="3.10"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_NAME="mememo"
WORKSPACE_DIR="$PWD"

# -- Defaults ----------------------------------------------
FORCE=false
UNINSTALL=false
UPGRADE=false
CLIENT=""
SKIP_TEST=false
GLOBAL_CONFIG=false
DEV_MODE=false
CLIENT_EXPLICIT=false
STATUS=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

show_help() {
    cat <<EOF
Usage: bash install.sh [OPTIONS]

Options:
  -c, --client TYPE   MCP client: claudedesktop, claude, cursor, windsurf,
                      vscode, gemini, codex, zed, kilo, opencode, goose,
                      pidev, all  (default: none)
  -f, --force         Skip prompts, overwrite existing config
  -u, --uninstall     Remove mememo from MCP client config and virtual environment
      --upgrade       Upgrade existing installation
      --update        Alias for --upgrade
      --status        Show where this server is currently installed
      --global        Use global config path (applies to: claude, cursor, gemini,
                      codex, opencode, all)
      --skip-test     Skip warmup validation step
      --dev           Install dev/test dependencies
  -h, --help          Show this help

Backward-compatible aliases (still supported):
  --configure=claude      same as -c claudedesktop
  --configure=claudecli   same as -c claude
  --configure             same as -c claudedesktop

Examples:
  bash install.sh                          Install (no MCP config written)
  bash install.sh -c claudedesktop         Install + configure Claude Desktop
  bash install.sh -c claude                Install + configure Claude Code (workspace)
  bash install.sh -c claude --global       Install + configure Claude Code (global)
  bash install.sh -c cursor                Install + configure Cursor (workspace)
  bash install.sh -c cursor --global       Install + configure Cursor (global)
  bash install.sh -c windsurf              Install + configure Windsurf
  bash install.sh -c vscode                Install + configure VS Code (workspace)
  bash install.sh -c gemini                Install + configure Gemini CLI (workspace)
  bash install.sh -c codex                 Install + configure OpenAI Codex CLI
  bash install.sh -c zed                   Install + configure Zed (global)
  bash install.sh -c kilo                  Install + configure Kilo Code
  bash install.sh -c opencode              Install + configure OpenCode (workspace)
  bash install.sh -c opencode --global     Install + configure OpenCode (global)
  bash install.sh -c goose                 Install + configure Goose
  bash install.sh -c all                   Install + configure all detected clients
  bash install.sh --status                 Show installation status
  bash install.sh --upgrade                Upgrade existing installation
  bash install.sh --upgrade -c all         Upgrade + reconfigure all clients
  bash install.sh -u -c all               Uninstall from all client configs
  bash install.sh --dev                    Install with dev/test dependencies
EOF
    exit 0
}

# -- Parse args --------------------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--client)
            CLIENT="$2"; CLIENT_EXPLICIT=true; shift 2 ;;
        -f|--force)
            FORCE=true; shift ;;
        -u|--uninstall)
            UNINSTALL=true; shift ;;
        --update|--upgrade)
            UPGRADE=true; shift ;;
        --status)
            STATUS=true; shift ;;
        --global)
            GLOBAL_CONFIG=true; shift ;;
        --skip-test)
            SKIP_TEST=true; shift ;;
        --dev)
            DEV_MODE=true; shift ;;
        --configure=claude)
            CLIENT="claudedesktop"; CLIENT_EXPLICIT=true; shift ;;
        --configure=claudecli)
            CLIENT="claude"; CLIENT_EXPLICIT=true; shift ;;
        --configure=*)
            log_error "Unknown --configure value: ${1#*=}. Supported: claude, claudecli"
            exit 1 ;;
        --configure)
            CLIENT="claudedesktop"; CLIENT_EXPLICIT=true; shift ;;
        -h|--help)
            show_help ;;
        *)
            log_error "Unknown option: $1"
            echo "Run 'bash install.sh --help' for usage"
            exit 1 ;;
    esac
done

if [[ "$UNINSTALL" == true && "$CLIENT_EXPLICIT" == false ]]; then
    CLIENT="all"
fi

if [[ "$GLOBAL_CONFIG" == true && -n "$CLIENT" ]]; then
    case "$CLIENT" in
        claude|cursor|gemini|codex|opencode|both|all) ;;
        *) log_error "--global is only valid with -c claude, cursor, gemini, codex, opencode, or all"; exit 1 ;;
    esac
fi

# -- Python & venv -----------------------------------------
check_python_version() {
    if ! command -v python3 &>/dev/null; then
        log_error "python3 not found. Install Python $PYTHON_MIN_VERSION+"
        exit 1
    fi
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    if [ "$(printf '%s\n' "$PYTHON_MIN_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$PYTHON_MIN_VERSION" ]; then
        log_error "Python $PYTHON_MIN_VERSION+ required, found $PYTHON_VERSION"
        exit 1
    fi
    log_success "Python $PYTHON_VERSION detected"
}

create_venv() {
    if [ -d "$VENV_DIR" ]; then
        log_info "Virtual environment exists at $VENV_DIR"
        return 0
    fi
    log_info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    log_success "Virtual environment created"
}

activate_venv() {
    if [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
    else
        log_error "Virtual environment not found"
        exit 1
    fi
}

get_venv_python() {
    if [[ -f "$SCRIPT_DIR/$VENV_DIR/bin/python" ]]; then
        echo "$SCRIPT_DIR/$VENV_DIR/bin/python"
    elif [[ -f "$SCRIPT_DIR/$VENV_DIR/Scripts/python.exe" ]]; then
        echo "$SCRIPT_DIR/$VENV_DIR/Scripts/python.exe"
    elif [[ -f "$SCRIPT_DIR/$VENV_DIR/bin/python3.exe" ]]; then
        echo "$SCRIPT_DIR/$VENV_DIR/bin/python3.exe"
    elif [[ -f "$SCRIPT_DIR/$VENV_DIR/bin/python3" ]]; then
        echo "$SCRIPT_DIR/$VENV_DIR/bin/python3"
    else
        return 1
    fi
}

install_production() {
    python -m pip install --upgrade pip
    python -m pip install -e .
    log_success "mememo $(python -c 'import mememo; print(mememo.__version__)') installed"
    python -c 'import mememo; print(mememo.__version__)' > "$MARKER"
}

install_dev() {
    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"
    log_success "mememo $(python -c 'import mememo; print(mememo.__version__)') installed with dev/test tools"
    python -c 'import mememo; print(mememo.__version__)' > "$MARKER"
}

run_warmup() {
    if [[ "$SKIP_TEST" == true ]]; then
        return 0
    fi
    log_info "Pre-warming bytecode cache and embedding model (this runs once)..."
    python warmup.py
    if [ $? -eq 0 ]; then
        log_success "Warmup complete"
    else
        log_warn "Warmup had a non-fatal error -- first MCP startup may be slow"
    fi
}

# -- MCP config paths --------------------------------------
get_desktop_config_path() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
    elif [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "cygwin"* ]] || [[ "$OSTYPE" == "win"* ]]; then
        echo "$APPDATA/Claude/claude_desktop_config.json"
    else
        if grep -qi microsoft /proc/version 2>/dev/null; then
            local appdata
            appdata=$(cmd.exe /c "echo %APPDATA%" 2>/dev/null | tr -d '\r')
            echo "$appdata/Claude/claude_desktop_config.json"
        else
            echo "$HOME/.config/Claude/claude_desktop_config.json"
        fi
    fi
}

get_code_config_path() {
    echo "$WORKSPACE_DIR/.mcp.json"
}

get_global_code_config_paths() {
    local found=()
    [[ -f "$HOME/.claude.json"     ]] && found+=("$HOME/.claude.json")
    [[ -f "$HOME/.claude/mcp.json" ]] && found+=("$HOME/.claude/mcp.json")
    if [[ ${#found[@]} -eq 0 ]]; then
        found+=("$HOME/.claude.json")
    fi
    printf '%s\n' "${found[@]}"
}

get_cursor_config_path() {
    if [[ "$GLOBAL_CONFIG" == true ]]; then
        echo "$HOME/.cursor/mcp.json"
    else
        echo "$WORKSPACE_DIR/.cursor/mcp.json"
    fi
}

get_windsurf_config_path() {
    echo "$HOME/.codeium/windsurf/mcp_config.json"
}

get_vscode_config_path() {
    echo "$WORKSPACE_DIR/.vscode/mcp.json"
}

get_gemini_config_path() {
    if [[ "$GLOBAL_CONFIG" == true ]]; then
        echo "$HOME/.gemini/settings.json"
    else
        echo "$WORKSPACE_DIR/.gemini/settings.json"
    fi
}

get_codex_config_path() {
    if [[ "$GLOBAL_CONFIG" == true ]]; then
        echo "$HOME/.codex/config.toml"
    else
        echo "$WORKSPACE_DIR/.codex/config.toml"
    fi
}

get_zed_config_path() {
    echo "$HOME/.config/zed/settings.json"
}

get_kilo_config_path() {
    echo "$WORKSPACE_DIR/.kilocode/mcp.json"
}

get_opencode_config_path() {
    if [[ "$GLOBAL_CONFIG" == true ]]; then
        echo "$HOME/.config/opencode/opencode.json"
    else
        echo "$WORKSPACE_DIR/opencode.json"
    fi
}

get_goose_config_path() {
    echo "$HOME/.config/goose/config.yaml"
}

# -- Merge/remove helpers ----------------------------------
# All use $venv_python set by configure_client (dynamic scoping).
# Command format: python_path -m mememo

merge_mcp_config() {
    local config_path="$1"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    "$venv_python" -c "
import json, sys, os
config_path = os.path.abspath(sys.argv[1])
python_path = sys.executable
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}
config.setdefault('mcpServers', {})
config['mcpServers']['$SERVER_NAME'] = {'command': python_path, 'args': ['-m', '$SERVER_NAME']}
os.makedirs(os.path.dirname(config_path), exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path"
}

remove_mcp_config() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }

    cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"

    "$venv_python" -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(0)
servers = config.get('mcpServers', {})
if '$SERVER_NAME' in servers:
    del servers['$SERVER_NAME']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed $SERVER_NAME from config')
else:
    print('[INFO] $SERVER_NAME not found in config')
" "$config_path"
}

merge_vscode_config() {
    local config_path="$1"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    "$venv_python" -c "
import json, sys, os
config_path = os.path.abspath(sys.argv[1])
python_path = sys.executable
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}
config.setdefault('servers', {})
config['servers']['$SERVER_NAME'] = {'type': 'stdio', 'command': python_path, 'args': ['-m', '$SERVER_NAME']}
os.makedirs(os.path.dirname(config_path), exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path"
}

remove_vscode_config() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }

    cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"

    "$venv_python" -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(0)
servers = config.get('servers', {})
if '$SERVER_NAME' in servers:
    del servers['$SERVER_NAME']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed $SERVER_NAME from VS Code config')
else:
    print('[INFO] $SERVER_NAME not found in VS Code config')
" "$config_path"
}

merge_codex_config() {
    local config_path="$1"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    "$venv_python" -c "
import sys, os, re
config_path = os.path.abspath(sys.argv[1])
python_path = sys.executable
sn = '$SERVER_NAME'
section_header = '[mcp_servers.' + sn + ']'
cmd = python_path + ' -m ' + sn
new_section = '\n' + section_header + '\ncommand = \"' + cmd + '\"\nstartup_timeout_sec = 30\ntool_timeout_sec = 300\nenabled = true\n'
os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True)
existing = ''
try:
    with open(config_path) as f:
        existing = f.read()
except FileNotFoundError:
    pass
if section_header in existing:
    lines = existing.split('\n')
    start = next((i for i, l in enumerate(lines) if l.strip() == section_header), -1)
    if start != -1:
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if re.match(r'^\[', lines[i]):
                end = i
                break
        del lines[start:end]
        existing = '\n'.join(lines)
existing = existing.rstrip()
if existing:
    existing += '\n'
with open(config_path, 'w') as f:
    f.write(existing + new_section)
" "$config_path"
}

remove_codex_config() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }

    cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"

    "$venv_python" -c "
import sys, os, re
config_path = sys.argv[1]
sn = '$SERVER_NAME'
section_header = '[mcp_servers.' + sn + ']'
try:
    with open(config_path) as f:
        existing = f.read()
except FileNotFoundError:
    sys.exit(0)
if section_header not in existing:
    print('[INFO] $SERVER_NAME not found in codex config')
    sys.exit(0)
lines = existing.split('\n')
start = next((i for i, l in enumerate(lines) if l.strip() == section_header), -1)
if start != -1:
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r'^\[', lines[i]):
            end = i
            break
    del lines[start:end]
    with open(config_path, 'w') as f:
        f.write('\n'.join(lines))
    print('[OK] Removed $SERVER_NAME from codex config')
" "$config_path"
}

merge_zed_config() {
    local config_path="$1"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    "$venv_python" -c "
import json, sys, os
config_path = os.path.abspath(sys.argv[1])
python_path = sys.executable
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}
config.setdefault('context_servers', {})
config['context_servers']['$SERVER_NAME'] = {
    'command': {'path': python_path, 'args': ['-m', '$SERVER_NAME'], 'env': {}}
}
os.makedirs(os.path.dirname(config_path), exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path"
}

remove_zed_config() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }

    cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"

    "$venv_python" -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(0)
cs = config.get('context_servers', {})
if '$SERVER_NAME' in cs:
    del cs['$SERVER_NAME']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed $SERVER_NAME from Zed config')
else:
    print('[INFO] $SERVER_NAME not found in Zed config')
" "$config_path"
}

merge_opencode_config() {
    local config_path="$1"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    "$venv_python" -c "
import json, sys, os
config_path = os.path.abspath(sys.argv[1])
python_path = sys.executable
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}
config.setdefault('mcp', {})
config['mcp']['$SERVER_NAME'] = {'type': 'local', 'command': [python_path, '-m', '$SERVER_NAME']}
d = os.path.dirname(config_path)
if d:
    os.makedirs(d, exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path"
}

remove_opencode_config() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }

    cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"

    "$venv_python" -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(0)
mcp = config.get('mcp', {})
if '$SERVER_NAME' in mcp:
    del mcp['$SERVER_NAME']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed $SERVER_NAME from OpenCode config')
else:
    print('[INFO] $SERVER_NAME not found in config')
" "$config_path"
}

merge_goose_config() {
    local config_path="$1"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    "$venv_python" -c "
import sys, os
try:
    import yaml
except ImportError:
    python_path = sys.executable
    print('[WARN] PyYAML not available. Add manually to ~/.config/goose/config.yaml:')
    print('extensions:')
    print('  $SERVER_NAME:')
    print('    name: $SERVER_NAME')
    print('    type: stdio')
    print('    cmd: ' + python_path)
    print('    args: [\"-m\", \"$SERVER_NAME\"]')
    print('    enabled: true')
    sys.exit(0)
config_path = os.path.abspath(sys.argv[1])
python_path = sys.executable
try:
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
except FileNotFoundError:
    config = {}
config.setdefault('extensions', {})
config['extensions']['$SERVER_NAME'] = {
    'name': '$SERVER_NAME', 'type': 'stdio',
    'cmd': python_path, 'args': ['-m', '$SERVER_NAME'], 'enabled': True,
}
d = os.path.dirname(config_path)
if d:
    os.makedirs(d, exist_ok=True)
with open(config_path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
" "$config_path"
}

remove_goose_config() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }

    cp "$config_path" "${config_path}.backup.$(date +%Y%m%d%H%M%S)"

    "$venv_python" -c "
import sys, os
try:
    import yaml
except ImportError:
    print('[WARN] PyYAML not available, cannot auto-remove from Goose config')
    sys.exit(0)
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
except FileNotFoundError:
    sys.exit(0)
ext = config.get('extensions', {})
if '$SERVER_NAME' in ext:
    del ext['$SERVER_NAME']
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print('[OK] Removed $SERVER_NAME from Goose config')
else:
    print('[INFO] $SERVER_NAME not found in config')
" "$config_path"
}

# -- Status helpers -----------------------------------------
_check_in_json() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { echo "NO"; return; }
    grep -q "\"$SERVER_NAME\"" "$config_path" 2>/dev/null && echo "YES" || echo "NO"
}

_check_in_toml() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { echo "NO"; return; }
    grep -q "^\[mcp_servers\.$SERVER_NAME\]" "$config_path" 2>/dev/null && echo "YES" || echo "NO"
}

_check_in_yaml() {
    local config_path="$1"
    [[ -f "$config_path" ]] || { echo "NO"; return; }
    grep -q "  $SERVER_NAME:" "$config_path" 2>/dev/null && echo "YES" || echo "NO"
}

show_status() {
    local installed_version=""
    [[ -f "$SCRIPT_DIR/$MARKER" ]] && installed_version=$(cat "$SCRIPT_DIR/$MARKER")
    local _ws="$SCRIPT_DIR/.."
    local _gh="$HOME"

    echo ""
    echo "  mememo -- Status"
    echo "  ----------------------------------------------------------------------------"
    printf "  %-30s %-9s %s\n" "Client" "Installed" "Config path"
    echo "  ----------------------------------------------------------------------------"

    _row() {
        local label="$1" status="$2" path="$3"
        if [[ "$status" == "YES" ]]; then
            printf "  %-30s %-9s %s\n" "$label" "YES" "$path"
        else
            printf "  %-30s %s\n" "$label" "NO"
        fi
    }

    local p s
    p="$(get_desktop_config_path)";              s=$(_check_in_json "$p"); _row "claudedesktop" "$s" "$p"
    p="$(get_code_config_path)";                 s=$(_check_in_json "$p"); _row "claude (workspace)" "$s" "$p"
    while IFS= read -r gp; do
        s=$(_check_in_json "$gp"); _row "claude (global)" "$s" "$gp"
    done < <(get_global_code_config_paths)
    p="$_ws/.cursor/mcp.json";                   s=$(_check_in_json "$p"); _row "cursor (workspace)" "$s" "$p"
    p="$_gh/.cursor/mcp.json";                   s=$(_check_in_json "$p"); _row "cursor (global)" "$s" "$p"
    p="$(get_windsurf_config_path)";             s=$(_check_in_json "$p"); _row "windsurf" "$s" "$p"
    p="$(get_vscode_config_path)";               s=$(_check_in_json "$p"); _row "vscode (workspace)" "$s" "$p"
    p="$_ws/.gemini/settings.json";              s=$(_check_in_json "$p"); _row "gemini (workspace)" "$s" "$p"
    p="$_gh/.gemini/settings.json";              s=$(_check_in_json "$p"); _row "gemini (global)" "$s" "$p"
    p="$_ws/.codex/config.toml";                 s=$(_check_in_toml "$p"); _row "codex (workspace)" "$s" "$p"
    p="$_gh/.codex/config.toml";                 s=$(_check_in_toml "$p"); _row "codex (global)" "$s" "$p"
    p="$(get_zed_config_path)";                  s=$(_check_in_json "$p"); _row "zed" "$s" "$p"
    p="$(get_kilo_config_path)";                 s=$(_check_in_json "$p"); _row "kilo" "$s" "$p"
    p="$_ws/opencode.json";                      s=$(_check_in_json "$p"); _row "opencode (workspace)" "$s" "$p"
    p="$_gh/.config/opencode/opencode.json";     s=$(_check_in_json "$p"); _row "opencode (global)" "$s" "$p"
    p="$(get_goose_config_path)";                s=$(_check_in_yaml "$p"); _row "goose" "$s" "$p"

    echo "  ----------------------------------------------------------------------------"
    if [[ -n "$installed_version" ]]; then
        echo "  Package: v${installed_version} installed"
    else
        echo "  Package: not installed"
    fi
    echo ""
}

# -- Configure client --------------------------------------
configure_client() {
    local client_type="$1"
    local python_path="$2"
    venv_python="$python_path"  # dynamic scoping for merge/remove helpers

    case "$client_type" in
        claudedesktop)
            local p; p="$(get_desktop_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_mcp_config "$p" > /dev/null 2>&1; log_success "Removed from Claude Desktop"; fi
            else
                log_info "Client: Claude Desktop"; log_info "Config: $p"
                merge_mcp_config "$p"; log_success "Claude Desktop configured at $p"
                log_warn "Restart Claude Desktop to activate mememo"
            fi
            ;;
        claude)
            if [[ "$GLOBAL_CONFIG" == true ]]; then
                [[ "$UNINSTALL" != true ]] && log_info "Client: Claude Code (global)"
                while IFS= read -r gp; do
                    if [[ "$UNINSTALL" == true ]]; then
                        if [[ "$(_check_in_json "$gp")" == "YES" ]]; then
                            remove_mcp_config "$gp" > /dev/null 2>&1; log_success "Removed from Claude Code (global)"; fi
                    else
                        log_info "Config: $gp"; merge_mcp_config "$gp"; log_success "Claude Code configured at $gp"
                    fi
                done < <(get_global_code_config_paths)
                [[ "$UNINSTALL" != true ]] && log_info "Verify with: claude mcp list"
            else
                local p; p="$(get_code_config_path)"
                if [[ "$UNINSTALL" == true ]]; then
                    if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                        remove_mcp_config "$p" > /dev/null 2>&1; log_success "Removed from Claude Code"; fi
                else
                    log_info "Client: Claude Code (workspace)"; log_info "Config: $p"
                    merge_mcp_config "$p"; log_success "Claude Code configured at $p"
                    log_info "Verify with: claude mcp list"
                fi
            fi
            ;;
        cursor)
            local p; p="$(get_cursor_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_mcp_config "$p" > /dev/null 2>&1; log_success "Removed from Cursor"; fi
            else
                log_info "Client: Cursor"; log_info "Config: $p"
                merge_mcp_config "$p"; log_success "Cursor configured at $p"
            fi
            ;;
        windsurf)
            local p; p="$(get_windsurf_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_mcp_config "$p" > /dev/null 2>&1; log_success "Removed from Windsurf"; fi
            else
                log_info "Client: Windsurf (global)"; log_info "Config: $p"
                merge_mcp_config "$p"; log_success "Windsurf configured at $p"
            fi
            ;;
        vscode)
            local p; p="$(get_vscode_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_vscode_config "$p" > /dev/null 2>&1; log_success "Removed from VS Code"; fi
            else
                log_info "Client: VS Code (workspace)"; log_info "Config: $p"
                log_info "Note: for global VS Code config, use the VS Code command palette"
                merge_vscode_config "$p"; log_success "VS Code configured at $p"
            fi
            ;;
        gemini)
            local p; p="$(get_gemini_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_mcp_config "$p" > /dev/null 2>&1; log_success "Removed from Gemini CLI"; fi
            else
                log_info "Client: Gemini CLI"; log_info "Config: $p"
                merge_mcp_config "$p"; log_success "Gemini CLI configured at $p"
            fi
            ;;
        codex)
            local p; p="$(get_codex_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_toml "$p")" == "YES" ]]; then
                    remove_codex_config "$p" > /dev/null 2>&1; log_success "Removed from Codex CLI"; fi
            else
                log_info "Client: OpenAI Codex CLI"; log_info "Config: $p"
                merge_codex_config "$p"; log_success "Codex CLI configured at $p"
            fi
            ;;
        zed)
            local p; p="$(get_zed_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_zed_config "$p" > /dev/null 2>&1; log_success "Removed from Zed"; fi
            else
                log_info "Client: Zed (global)"; log_info "Config: $p"
                merge_zed_config "$p"; log_success "Zed configured at $p"
            fi
            ;;
        kilo)
            local p; p="$(get_kilo_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_mcp_config "$p" > /dev/null 2>&1; log_success "Removed from Kilo Code"; fi
            else
                log_info "Client: Kilo Code"; log_info "Config: $p"
                merge_mcp_config "$p"; log_success "Kilo Code configured at $p"
            fi
            ;;
        opencode)
            local p; p="$(get_opencode_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_json "$p")" == "YES" ]]; then
                    remove_opencode_config "$p" > /dev/null 2>&1; log_success "Removed from OpenCode"; fi
            else
                log_info "Client: OpenCode"; log_info "Config: $p"
                merge_opencode_config "$p"; log_success "OpenCode configured at $p"
            fi
            ;;
        goose)
            local p; p="$(get_goose_config_path)"
            if [[ "$UNINSTALL" == true ]]; then
                if [[ "$(_check_in_yaml "$p")" == "YES" ]]; then
                    remove_goose_config "$p" > /dev/null 2>&1; log_success "Removed from Goose"; fi
            else
                log_info "Client: Goose"; log_info "Config: $p"
                merge_goose_config "$p"; log_success "Goose configured at $p"
            fi
            ;;
        pidev)
            log_info "Client: pi.dev"
            echo ""
            echo "  pi.dev does not support MCP servers natively."
            echo "  pi.dev uses TypeScript extensions and CLI tools instead."
            echo "  To use mememo concepts in pi.dev, see: https://pi.dev/docs/extensions"
            echo ""
            ;;
        both)
            configure_client "claudedesktop" "$python_path"
            echo ""
            configure_client "claude" "$python_path"
            ;;
        all)
            configure_client "claudedesktop" "$python_path"
            echo ""
            configure_client "claude" "$python_path"
            local _ws="$SCRIPT_DIR/.." _gh="$HOME"
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_ws/.cursor/mcp.json" ]] || [[ -f "$_gh/.cursor/mcp.json" ]]; then
                echo ""; configure_client "cursor" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_gh/.codeium/windsurf/mcp_config.json" ]]; then
                echo ""; configure_client "windsurf" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_ws/.vscode/mcp.json" ]]; then
                echo ""; configure_client "vscode" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_ws/.gemini/settings.json" ]] || [[ -f "$_gh/.gemini/settings.json" ]]; then
                echo ""; configure_client "gemini" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_ws/.codex/config.toml" ]] || [[ -f "$_gh/.codex/config.toml" ]]; then
                echo ""; configure_client "codex" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_gh/.config/zed/settings.json" ]]; then
                echo ""; configure_client "zed" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_ws/.kilocode/mcp.json" ]]; then
                echo ""; configure_client "kilo" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_ws/opencode.json" ]] || [[ -f "$_gh/.config/opencode/opencode.json" ]]; then
                echo ""; configure_client "opencode" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$(get_goose_config_path)" ]]; then
                echo ""; configure_client "goose" "$python_path"
            fi
            ;;
        *)
            log_error "Unknown client type: $client_type"
            log_error "Valid: claudedesktop, claude, cursor, windsurf, vscode, gemini, codex, zed, kilo, opencode, goose, pidev, both, all"
            exit 1
            ;;
    esac
}

# -- Banner -------------------------------------------------
echo ""
echo "======================================"
echo "mememo Installer"
echo "======================================"
echo ""

# -- Status -------------------------------------------------
if [[ "$STATUS" == true ]]; then
    show_status
    exit 0
fi

# -- Uninstall path -----------------------------------------
if [[ "$UNINSTALL" == true ]]; then
    VENV_PYTHON=$(get_venv_python 2>/dev/null) || true

    if [[ -n "$CLIENT" ]]; then
        PYTHON_PATH="${VENV_PYTHON:-$(command -v python3)}"
        configure_client "$CLIENT" "$PYTHON_PATH"
        echo ""
    fi

    log_warn "This will remove mememo and $VENV_DIR"
    if [[ "$FORCE" != true ]]; then
        read -p "Continue? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Cancelled"
            exit 0
        fi
    fi

    log_info "Uninstalling..."

    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        log_success "Virtual environment removed"
    fi

    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    rm -rf build/ dist/ *.egg-info 2>/dev/null || true

    log_success "mememo uninstalled"
    echo ""
    log_info "User data in ~/.mememo preserved"
    exit 0
fi

# -- Upgrade path -------------------------------------------
if [[ "$UPGRADE" == true ]]; then
    if [ ! -d "$VENV_DIR" ]; then
        log_error "No installation found. Run 'bash install.sh' first"
        exit 1
    fi

    log_info "Upgrading mememo..."


    activate_venv
    python -m pip install --upgrade pip
    python -m pip install --upgrade -e ".[dev]"
    log_success "mememo upgraded"
    echo ""
    run_warmup

    if [[ "$CLIENT_EXPLICIT" == true ]]; then
        echo ""
        VENV_PYTHON=$(get_venv_python)
        configure_client "$CLIENT" "$VENV_PYTHON"
    fi
    exit 0
fi

# -- Install path -------------------------------------------
if [ -f "$MARKER" ] && [ -d "$VENV_DIR" ] && [[ "$FORCE" != true ]]; then
    log_info "mememo already installed ($(cat "$MARKER")). Use --upgrade to update."
    if [[ "$CLIENT_EXPLICIT" == true ]]; then
        VENV_PYTHON=$(get_venv_python)
        echo ""
        configure_client "$CLIENT" "$VENV_PYTHON"
    else
        echo ""
        log_info "Run 'bash install.sh -c claudedesktop' to configure Claude Desktop"
        log_info "     'bash install.sh -c claude'    to configure Claude Code"
    fi
    echo ""
    exit 0
fi

check_python_version
create_venv
activate_venv

if [[ "$DEV_MODE" == true ]]; then
    install_dev
else
    install_production
fi

echo ""
run_warmup

if [[ "$CLIENT_EXPLICIT" == true ]]; then
    echo ""
    VENV_PYTHON=$(get_venv_python)
    configure_client "$CLIENT" "$VENV_PYTHON"
else
    echo ""
    log_info "Tip: Run 'bash install.sh -c claudedesktop' to auto-configure Claude Desktop"
    log_info "     Run 'bash install.sh -c claude'       to auto-configure Claude Code"
    log_info "     Run 'bash install.sh -c all'          to configure all detected clients"
    log_info "     Run 'bash install.sh --status'        to show all install locations"
fi

echo ""
echo "======================================"
echo "Installation Complete!"
echo "======================================"
echo ""

if [[ "$DEV_MODE" == true ]]; then
    cat <<EOF
Dev environment ready:

  1. Activate venv:
     source $VENV_DIR/bin/activate

  2. Run tests:
     pytest tests/ -v

  3. Configure your AI assistant (if not done):
     bash install.sh -c claudedesktop   (Claude Desktop)
     bash install.sh -c claude          (Claude Code CLI)
EOF
elif [[ "$CLIENT_EXPLICIT" == true ]]; then
    case "$CLIENT" in
        claude)
            echo "  mememo is ready in Claude Code CLI."
            echo "  Verify with: claude mcp list"
            ;;
        claudedesktop)
            echo "  mememo is ready. Restart Claude Desktop to activate."
            ;;
        *)
            echo "  mememo is ready. Restart the client to activate."
            ;;
    esac
    echo ""
    echo "  See README.md for usage and configuration options."
else
    cat <<EOF
  mememo is installed but not yet connected to an AI assistant.
  Run: bash install.sh -c claudedesktop    (Claude Desktop)
       bash install.sh -c claude           (Claude Code CLI)
       bash install.sh -c cursor           (Cursor)
       bash install.sh -c windsurf         (Windsurf)
       bash install.sh -c vscode           (VS Code)
       bash install.sh -c gemini           (Gemini CLI)
       bash install.sh -c codex            (OpenAI Codex CLI)
       bash install.sh -c zed              (Zed)
       bash install.sh -c kilo             (Kilo Code)
       bash install.sh -c opencode         (OpenCode)
       bash install.sh -c goose            (Goose)
       bash install.sh -c all              (all detected clients)

  See README.md for usage and configuration options.
EOF
fi
echo ""

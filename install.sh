#!/usr/bin/env bash
set -e

# ── Config ───────────────────────────────────────────────
VENV_DIR=".venv"
MARKER="$VENV_DIR/.mememo_installed"
PYTHON_MIN_VERSION="3.10"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Defaults ─────────────────────────────────────────────
FORCE=false
UNINSTALL=false
UPGRADE=false
CLIENT=""
SKIP_TEST=false
GLOBAL_CONFIG=false
DEV_MODE=false
CLIENT_EXPLICIT=false

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
  -c, --client TYPE   MCP client: desktop, code, kilo, opencode, goose, all (default: none)
  -f, --force         Skip prompts, overwrite existing config
  -u, --uninstall     Remove mememo from MCP client config and virtual environment
      --upgrade       Upgrade existing installation
      --global        Use global config path (applies to: code, opencode, all)
      --skip-test     Skip warmup validation step
      --dev           Install dev/test dependencies
  -h, --help          Show this help

Backward-compatible aliases (still supported):
  --configure=claude      same as -c desktop
  --configure=claudecli   same as -c code
  --configure             same as -c desktop

Examples:
  bash install.sh                    Install (no MCP config written)
  bash install.sh -c desktop         Install + configure Claude Desktop
  bash install.sh -c code            Install + configure Claude Code
  bash install.sh -c kilo            Install + configure Kilo Code
  bash install.sh -c opencode        Install + configure OpenCode (workspace)
  bash install.sh -c opencode --global Install + configure OpenCode (global)
  bash install.sh -c goose           Install + configure Goose
  bash install.sh -c all             Install + configure all detected clients
  bash install.sh --upgrade          Upgrade existing installation
  bash install.sh -u -c all          Uninstall from all client configs
  bash install.sh --dev              Install with dev/test dependencies
EOF
    exit 0
}

# ── Parse args ───────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--client)
            CLIENT="$2"; CLIENT_EXPLICIT=true; shift 2 ;;
        -f|--force)
            FORCE=true; shift ;;
        -u|--uninstall)
            UNINSTALL=true; shift ;;
        --upgrade)
            UPGRADE=true; shift ;;
        --global)
            GLOBAL_CONFIG=true; shift ;;
        --skip-test)
            SKIP_TEST=true; shift ;;
        --dev)
            DEV_MODE=true; shift ;;
        --configure=claude)
            CLIENT="desktop"; CLIENT_EXPLICIT=true; shift ;;
        --configure=claudecli)
            CLIENT="code"; CLIENT_EXPLICIT=true; shift ;;
        --configure=*)
            log_error "Unknown --configure value: ${1#*=}. Supported: claude, claudecli"
            exit 1 ;;
        --configure)
            CLIENT="desktop"; CLIENT_EXPLICIT=true; shift ;;
        -h|--help)
            show_help ;;
        *)
            log_error "Unknown option: $1"
            echo "Run 'bash install.sh --help' for usage"
            exit 1 ;;
    esac
done

if [[ "$GLOBAL_CONFIG" == true && -n "$CLIENT" ]]; then
    case "$CLIENT" in
        code|both|opencode|all) ;;
        *) log_error "--global is only valid with -c code, opencode, both, or all"; exit 1 ;;
    esac
fi

# ── Python & venv ────────────────────────────────────────
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
        log_warn "Warmup had a non-fatal error — first MCP startup may be slow"
    fi
}

# ── MCP config paths ─────────────────────────────────────
get_desktop_config_path() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
    elif [[ "$OSTYPE" == "linux-gnu"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
        echo "$HOME/.config/Claude/claude_desktop_config.json"
    else
        echo "$HOME/.config/Claude/claude_desktop_config.json"
    fi
}

get_kilo_config_path() {
    echo "$SCRIPT_DIR/../.kilocode/mcp.json"
}

get_opencode_config_path() {
    if [[ "$GLOBAL_CONFIG" == true ]]; then
        echo "$HOME/.config/opencode/opencode.json"
    else
        echo "$SCRIPT_DIR/../opencode.json"
    fi
}

get_goose_config_path() {
    echo "$HOME/.config/goose/config.yaml"
}

# ── Configure functions ──────────────────────────────────
_configure_desktop() {
    local python_path="$1"
    local config_path
    config_path="$(get_desktop_config_path)"

    log_info "Config: $config_path"

    if [[ "$UNINSTALL" == true ]]; then
        [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        python3 -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except: config = {}
servers = config.get('mcpServers', {})
if 'mememo' in servers:
    del servers['mememo']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed mememo from Claude Desktop config')
else:
    print('[INFO] mememo not found in config')
" "$config_path"
        return 0
    fi

    mkdir -p "$(dirname "$config_path")"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    python3 -c "
import json, sys, os

config_path = sys.argv[1]
python_path = sys.argv[2]

try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['mememo'] = {
    'command': python_path,
    'args': ['-m', 'mememo']
}

os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path" "$python_path"

    log_success "Claude Desktop configured at $config_path"
    log_warn "Restart Claude Desktop to activate mememo"
}

_configure_code() {
    local python_path="$1"

    # ~/.claude.json takes precedence (Linux default); fall back to ~/.claude/mcp.json
    local config_path
    if [[ -f "$HOME/.claude.json" ]]; then
        config_path="$HOME/.claude.json"
    else
        config_path="$HOME/.claude/mcp.json"
    fi

    log_info "Config: $config_path"

    if [[ "$UNINSTALL" == true ]]; then
        [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        python3 -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except: config = {}
servers = config.get('mcpServers', {})
if 'mememo' in servers:
    del servers['mememo']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed mememo from Claude Code config')
else:
    print('[INFO] mememo not found in config')
" "$config_path"
        return 0
    fi

    mkdir -p "$(dirname "$config_path")"

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    python3 -c "
import json, sys, os

config_path = sys.argv[1]
python_path = sys.argv[2]

try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['mememo'] = {
    'command': python_path,
    'args': ['-m', 'mememo']
}

os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path" "$python_path"

    log_success "Claude Code configured at $config_path"
    log_info "Verify with: claude mcp list"
}

_configure_kilo() {
    local python_path="$1"
    local config_path
    config_path="$(get_kilo_config_path)"

    log_info "Config: $config_path"

    if [[ "$UNINSTALL" == true ]]; then
        [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        python3 -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except: config = {}
servers = config.get('mcpServers', {})
if 'mememo' in servers:
    del servers['mememo']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed mememo from Kilo Code config')
else:
    print('[INFO] mememo not found in config')
" "$config_path"
        return 0
    fi

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    python3 -c "
import json, sys, os

config_path = os.path.abspath(sys.argv[1])
python_path = sys.argv[2]

try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['mememo'] = {
    'command': python_path,
    'args': ['-m', 'mememo']
}

os.makedirs(os.path.dirname(config_path), exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path" "$python_path"

    log_success "Kilo Code configured at $config_path"
}

_configure_opencode() {
    local python_path="$1"
    local config_path
    config_path="$(get_opencode_config_path)"

    log_info "Config: $config_path"

    if [[ "$UNINSTALL" == true ]]; then
        [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        python3 -c "
import json, sys
config_path = sys.argv[1]
try:
    with open(config_path) as f:
        config = json.load(f)
except: config = {}
mcp = config.get('mcp', {})
if 'mememo' in mcp:
    del mcp['mememo']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print('[OK] Removed mememo from OpenCode config')
else:
    print('[INFO] mememo not found in config')
" "$config_path"
        return 0
    fi

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    python3 -c "
import json, sys, os

config_path = os.path.abspath(sys.argv[1])
python_path = sys.argv[2]

try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcp', {})
config['mcp']['mememo'] = {'type': 'local', 'command': [python_path, '-m', 'mememo']}

d = os.path.dirname(config_path)
if d:
    os.makedirs(d, exist_ok=True)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$config_path" "$python_path"

    log_success "OpenCode configured at $config_path"
}

_configure_goose() {
    local python_path="$1"
    local config_path
    config_path="$(get_goose_config_path)"

    log_info "Config: $config_path"

    if [[ "$UNINSTALL" == true ]]; then
        [[ -f "$config_path" ]] || { log_info "Config not found, nothing to remove"; return 0; }
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        python3 -c "
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
if 'mememo' in ext:
    del ext['mememo']
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print('[OK] Removed mememo from Goose config')
else:
    print('[INFO] mememo not found in config')
" "$config_path"
        return 0
    fi

    if [[ -f "$config_path" ]]; then
        cp "$config_path" "$config_path.backup.$(date +%Y%m%d%H%M%S)"
        log_info "Backed up existing config"
    fi

    python3 -c "
import sys, os

try:
    import yaml
except ImportError:
    python_path = sys.argv[2]
    print('[WARN] PyYAML not available. Add manually to ~/.config/goose/config.yaml:')
    print('extensions:')
    print('  mememo:')
    print('    name: mememo')
    print('    type: stdio')
    print('    cmd: ' + python_path)
    print('    args: [\"-m\", \"mememo\"]')
    print('    enabled: true')
    sys.exit(0)

config_path = os.path.abspath(sys.argv[1])
python_path = sys.argv[2]

try:
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
except FileNotFoundError:
    config = {}

config.setdefault('extensions', {})
config['extensions']['mememo'] = {
    'name': 'mememo',
    'type': 'stdio',
    'cmd': python_path,
    'args': ['-m', 'mememo'],
    'enabled': True,
}

d = os.path.dirname(config_path)
if d:
    os.makedirs(d, exist_ok=True)
with open(config_path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
" "$config_path" "$python_path"

    log_success "Goose configured at $config_path"
}

configure_client() {
    local client_type="$1"
    local python_path="$2"

    case "$client_type" in
        desktop)
            log_info "Client: Claude Desktop"
            _configure_desktop "$python_path"
            ;;
        code)
            log_info "Client: Claude Code"
            _configure_code "$python_path"
            ;;
        kilo)
            log_info "Client: Kilo Code"
            _configure_kilo "$python_path"
            ;;
        opencode)
            log_info "Client: OpenCode"
            _configure_opencode "$python_path"
            ;;
        goose)
            log_info "Client: Goose"
            _configure_goose "$python_path"
            ;;
        both)
            configure_client "desktop" "$python_path"
            echo ""
            configure_client "code" "$python_path"
            ;;
        all)
            configure_client "desktop" "$python_path"
            echo ""
            configure_client "code" "$python_path"
            local _kilo_path _opencode_ws _opencode_global _goose_path
            _kilo_path="$(get_kilo_config_path)"
            _opencode_ws="$SCRIPT_DIR/../opencode.json"
            _opencode_global="$HOME/.config/opencode/opencode.json"
            _goose_path="$(get_goose_config_path)"
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_kilo_path" ]]; then
                echo ""
                configure_client "kilo" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_opencode_ws" ]] || [[ -f "$_opencode_global" ]]; then
                echo ""
                configure_client "opencode" "$python_path"
            fi
            if [[ "$UNINSTALL" == true ]] || [[ -f "$_goose_path" ]]; then
                echo ""
                configure_client "goose" "$python_path"
            fi
            ;;
        *)
            log_error "Unknown client type: $client_type. Valid: desktop, code, kilo, opencode, goose, both, all"
            exit 1
            ;;
    esac
}

# ── Banner ───────────────────────────────────────────────
echo ""
echo "======================================"
echo "mememo Installer"
echo "======================================"
echo ""

# ── Uninstall path ───────────────────────────────────────
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

# ── Upgrade path ─────────────────────────────────────────
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

# ── Install path ─────────────────────────────────────────
if [ -f "$MARKER" ] && [ -d "$VENV_DIR" ] && [[ "$FORCE" != true ]]; then
    log_info "mememo already installed ($(cat "$MARKER")). Use --upgrade to update."
    if [[ "$CLIENT_EXPLICIT" == true ]]; then
        VENV_PYTHON=$(get_venv_python)
        echo ""
        configure_client "$CLIENT" "$VENV_PYTHON"
    else
        echo ""
        log_info "Run 'bash install.sh -c desktop' to configure Claude Desktop"
        log_info "     'bash install.sh -c code'    to configure Claude Code"
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
    log_info "Tip: Run 'bash install.sh -c desktop' to auto-configure Claude Desktop"
    log_info "     Run 'bash install.sh -c code'    to auto-configure Claude Code"
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
     bash install.sh -c desktop   (Claude Desktop)
     bash install.sh -c code      (Claude Code CLI)
EOF
elif [[ "$CLIENT_EXPLICIT" == true ]]; then
    case "$CLIENT" in
        code)
            echo "  mememo is ready in Claude Code CLI."
            echo "  Verify with: claude mcp list"
            ;;
        desktop)
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
  Run: bash install.sh -c desktop   (Claude Desktop)
       bash install.sh -c code      (Claude Code CLI)
       bash install.sh -c kilo      (Kilo Code)
       bash install.sh -c opencode  (OpenCode)
       bash install.sh -c goose     (Goose)

  See README.md for usage and configuration options.
EOF
fi
echo ""

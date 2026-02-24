#!/usr/bin/env bash
#
# mememo installer - Handles installation, upgrade, testing setup, and uninstall
#
# Usage:
#   bash install.sh              # Install for production
#   bash install.sh --dev        # Install with dev/test dependencies
#   bash install.sh --upgrade    # Upgrade existing installation
#   bash install.sh --uninstall  # Uninstall mememo
#

set -e

# Configuration
VENV_DIR=".venv"
PYTHON_MIN_VERSION="3.10"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Helper functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_python_version() {
    if ! command -v python3 &> /dev/null; then
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

install_production() {
    pip install --upgrade pip
    pip install -e .
    log_success "mememo $(python -c 'import mememo; print(mememo.__version__)') installed"
}

install_dev() {
    pip install --upgrade pip
    pip install -e ".[dev]"
    log_success "mememo $(python -c 'import mememo; print(mememo.__version__)') installed with dev/test tools"
}

run_warmup() {
    log_info "Pre-warming bytecode cache and embedding model (this runs once)..."
    python warmup.py
    if [ $? -eq 0 ]; then
        log_success "Warmup complete"
    else
        log_warn "Warmup had a non-fatal error — first MCP startup may be slow"
    fi
}

upgrade_installation() {
    if [ ! -d "$VENV_DIR" ]; then
        log_error "No installation found. Run 'bash install.sh' first"
        exit 1
    fi

    log_info "Upgrading mememo..."
    activate_venv
    pip install --upgrade pip
    pip install --upgrade -e ".[dev]"
    log_success "mememo upgraded"
    echo ""
    run_warmup
}

configure_claude_cli() {
    log_info "Configuring Claude Code CLI MCP server..."

    PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PYTHON_PATH="$PROJECT_DIR/$VENV_DIR/bin/python"

    if [ ! -f "$PYTHON_PATH" ]; then
        log_error "Python not found at $PYTHON_PATH"
        return 1
    fi

    if ! command -v claude &> /dev/null; then
        log_error "Claude CLI not found. Install it first: https://claude.ai/download"
        return 1
    fi

    # Remove existing entry first so re-installs and path changes always apply
    claude mcp remove mememo --scope user &> /dev/null || true

    claude mcp add --scope user mememo -- "$PYTHON_PATH" -m mememo
    if [ $? -eq 0 ]; then
        log_success "Claude CLI MCP server configured (user scope)"
        log_info "Verify with: claude mcp list"
    else
        log_warn "Auto-configuration failed. Add manually:"
        echo "  claude mcp add --scope user mememo -- $PYTHON_PATH -m mememo"
        return 1
    fi
}

configure_mcp() {
    log_info "Configuring Claude Desktop MCP server..."

    # Detect Claude Desktop config location
    if [[ "$OSTYPE" == "darwin"* ]]; then
        CONFIG_DIR="$HOME/Library/Application Support/Claude"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        CONFIG_DIR="$HOME/.config/Claude"
    else
        log_warn "Unsupported OS for auto-configuration of Claude Desktop"
        return 1
    fi

    CONFIG_FILE="$CONFIG_DIR/claude_desktop_config.json"

    # Get absolute paths
    PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PYTHON_PATH="$PROJECT_DIR/$VENV_DIR/bin/python"

    # Check if Python exists
    if [ ! -f "$PYTHON_PATH" ]; then
        log_error "Python not found at $PYTHON_PATH"
        return 1
    fi

    # Create config directory if needed
    mkdir -p "$CONFIG_DIR"

    # Create or update config
    if [ -f "$CONFIG_FILE" ]; then
        log_info "Updating existing Claude Desktop config..."

        # Backup existing config
        cp "$CONFIG_FILE" "$CONFIG_FILE.backup"

        # Use Python to merge JSON
        python3 << EOF
import json
import sys

config_file = "$CONFIG_FILE"

try:
    with open(config_file, 'r') as f:
        config = json.load(f)
except:
    config = {}

if 'mcpServers' not in config:
    config['mcpServers'] = {}

config['mcpServers']['mememo'] = {
    'command': '$PYTHON_PATH',
    'args': ['-m', 'mememo']
}

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print('Updated')
EOF

        log_success "Updated $CONFIG_FILE"
        log_info "Backup saved at $CONFIG_FILE.backup"
    else
        log_info "Creating new Claude Desktop config..."

        cat > "$CONFIG_FILE" << EOF
{
  "mcpServers": {
    "mememo": {
      "command": "$PYTHON_PATH",
      "args": ["-m", "mememo"]
    }
  }
}
EOF

        log_success "Created $CONFIG_FILE"
    fi

    return 0
}

uninstall() {
    log_warn "This will remove mememo and $VENV_DIR"
    read -p "Continue? (y/N): " -n 1 -r
    echo

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Cancelled"
        exit 0
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
    log_info "To complete uninstall, manually remove mememo from:"

    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "       ~/Library/Application Support/Claude/claude_desktop_config.json"
    else
        echo "       ~/.config/Claude/claude_desktop_config.json"
    fi

    echo "       (Remove the \"mememo\" entry from mcpServers section)"
}

show_next_steps() {
    local mode=$1

    echo ""
    echo "======================================"
    echo "Installation Complete!"
    echo "======================================"
    echo ""

    if [ "$mode" = "dev" ]; then
        cat <<EOF
Dev environment ready:

  1. Activate venv:
     source $VENV_DIR/bin/activate

  2. Run tests:
     pytest tests/ -v

  3. Configure your AI assistant (if not done):
     bash install.sh --configure=claude      (Claude Desktop)
     bash install.sh --configure=claudecli   (Claude Code CLI)
EOF
    else
        echo ""
        if [ -z "$AUTO_CONFIGURE" ]; then
            cat <<EOF
  mememo is installed but not yet connected to an AI assistant.
  Run: bash install.sh --configure=claude      (Claude Desktop)
       bash install.sh --configure=claudecli   (Claude Code CLI)

  See README.md for usage and configuration options.
EOF
        elif [ "$AUTO_CONFIGURE" = "claudecli" ]; then
            cat <<EOF
  mememo is ready in Claude Code CLI.
  Verify with: claude mcp list

  See README.md for usage and configuration options.
EOF
        else
            cat <<EOF
  mememo is ready. Restart Claude Desktop to activate.

  See README.md for usage and configuration options.
EOF
        fi
    fi
    echo ""
}

main() {
    echo "======================================"
    echo "mememo Installer"
    echo "======================================"
    echo ""

    MODE="production"
    ACTION="install"
    AUTO_CONFIGURE=""

    for arg in "$@"; do
        case $arg in
            --dev) MODE="dev" ;;
            --upgrade) ACTION="upgrade" ;;
            --uninstall) ACTION="uninstall" ;;
            --configure=*)
                AUTO_CONFIGURE="${arg#*=}"
                ;;
            --configure)
                AUTO_CONFIGURE="claude"
                ;;
            --help)
                cat <<EOF
Usage: bash install.sh [OPTIONS]

Options:
  (none)            Install for production
  --dev             Install with dev/test dependencies
  --configure[=AI]  Auto-configure AI assistant MCP server
                    Supported: claude (Claude Desktop), claudecli (Claude Code CLI)
                    Default: claude
                    Example: --configure=claude
                             --configure=claudecli
  --upgrade         Upgrade existing installation
  --uninstall       Remove mememo and virtual environment
  --help            Show this help
EOF
                exit 0
                ;;
            *)
                log_error "Unknown option: $arg"
                echo "Run 'bash install.sh --help' for usage"
                exit 1
                ;;
        esac
    done

    case $ACTION in
        install)
            check_python_version
            create_venv
            activate_venv

            if [ "$MODE" = "dev" ]; then
                install_dev
            else
                install_production
            fi

            echo ""
            run_warmup

            # Auto-configure MCP if requested
            if [ -n "$AUTO_CONFIGURE" ]; then
                echo ""
                case "$AUTO_CONFIGURE" in
                    claude)
                        if configure_mcp; then
                            log_success "Claude Desktop MCP server configured!"
                            log_warn "Restart Claude Desktop to start using mememo"
                        else
                            log_warn "Auto-configuration skipped. Configure Claude Desktop manually (see README.md)"
                        fi
                        ;;
                    claudecli)
                        configure_claude_cli
                        ;;
                    *)
                        log_error "AI assistant '$AUTO_CONFIGURE' not yet supported"
                        log_info "Supported: claude, claudecli"
                        log_info "Configure manually (see README.md)"
                        ;;
                esac
            else
                log_info "Tip: Run 'bash install.sh --configure=claude' to auto-configure Claude Desktop"
                log_info "     Run 'bash install.sh --configure=claudecli' to auto-configure Claude Code CLI"
            fi

            show_next_steps "$MODE"
            ;;
        upgrade)
            upgrade_installation
            ;;
        uninstall)
            uninstall
            ;;
    esac
}

main "$@"

#!/usr/bin/env bash
# ==============================================================================
# Derindex - One-Step Setup & Installer Script
# ==============================================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}       Derindex Setup & Environment Installer       ${NC}"
echo -e "${CYAN}====================================================${NC}"

# 1. Check Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[!] Error: Python 3 is not installed. Please install Python >= 3.10.${NC}"
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}[✓] Detected Python $PY_VER${NC}"

# 2. Setup Virtual Environment
if [ ! -d ".venv" ]; then
    echo -e "${CYAN}[*] Creating virtual environment (.venv)...${NC}"
    python3 -m venv .venv
else
    echo -e "${GREEN}[✓] Existing virtual environment found (.venv).${NC}"
fi

# 3. Upgrade pip and install requirements
echo -e "${CYAN}[*] Installing dependencies from requirements.txt...${NC}"
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet
echo -e "${GREEN}[✓] Dependencies installed successfully.${NC}"

# 4. Make scripts executable
chmod +x derindex mysearch main.py scripts/*.sh 2>/dev/null || true

# 5. Create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
ln -sf "$PROJECT_DIR/derindex" "$HOME/.local/bin/derindex"
ln -sf "$PROJECT_DIR/mysearch" "$HOME/.local/bin/mysearch"
echo -e "${GREEN}[✓] Global symlinks created in ~/.local/bin/derindex and ~/.local/bin/mysearch${NC}"

# 6. Run verification tests
echo -e "${CYAN}[*] Running test suite...${NC}"
.venv/bin/python -m unittest discover tests -v
echo -e "${GREEN}[✓] All verification tests passed!${NC}"

echo -e "\n${CYAN}====================================================${NC}"
echo -e "${GREEN}Derindex is ready to use!${NC}"
echo -e "${CYAN}Try the following commands:${NC}"
echo -e "  derindex index data/demo_docs    # Index the demo files"
echo -e "  derindex search \"memory\"         # Run hybrid search"
echo -e "  derindex serve --port 8000       # Launch web UI on http://localhost:8000"
echo -e "${CYAN}====================================================${NC}"

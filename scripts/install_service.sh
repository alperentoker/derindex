#!/usr/bin/env bash
set -e

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo -e "${CYAN}=== Derindex Systemd Servis Kurulumu ===${NC}"

# 1. Ensure user systemd directory exists
mkdir -p "$SYSTEMD_USER_DIR"

# 2. Render service unit with current paths
SERVICE_FILE="$SYSTEMD_USER_DIR/derindex.service"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Derindex Personal Search Engine & Semantic Search Service
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/derindex daemon --watch $HOME/Belgeler --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

# Laptop belleğini korumak için tavan sınırları
MemoryMax=1.5G
MemoryHigh=1G

[Install]
WantedBy=default.target
EOF

echo -e "${GREEN}✓ Servis dosyası oluşturuldu:${NC} $SERVICE_FILE"

# 3. Reload and enable systemd user daemon
systemctl --user daemon-reload
systemctl --user enable derindex.service

# 4. Enable lingering so it starts on system boot even without graphical login
if command -v loginctl &> /dev/null; then
    loginctl enable-linger "$USER" 2>/dev/null || true
    echo -e "${GREEN}✓ Boot sırasında kullanıcı girişinden bağımsız otomatik başlama (linger) aktif edildi.${NC}"
fi

echo -e "\n${CYAN}Servisi başlatmak için:${NC}"
echo -e "  systemctl --user start derindex.service"
echo -e "\n${CYAN}Durumunu kontrol etmek için:${NC}"
echo -e "  systemctl --user status derindex.service"
echo -e "\n${CYAN}Canlı logları takip etmek için:${NC}"
echo -e "  journalctl --user -u derindex.service -f"
echo -e "\n${GREEN}Kurulum başarıyla tamamlandı!${NC}"

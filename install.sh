#!/usr/bin/env bash
# ==============================================================================
# SNNU 校园网自动登录 - Linux / WSL 一键安装脚本
#
# 用法：
#   bash install.sh                          # 交互式安装（逐项询问）
#   bash install.sh --account 学号 --password 密码 --provider mobile
#   bash install.sh --account 学号 --password 密码 --autostart   # 同时设置开机自启
#
# 脚本会：
#   1. 检查 python3 并创建独立虚拟环境（不污染系统）
#   2. 安装依赖（仅 requests）
#   3. 生成 config.ini（已存在则不覆盖）
#   4. 生成启动器 snnu_login.sh
#   5. --autostart 时注册 systemd 用户服务（登录后自动联网）
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ACCOUNT="" PASSWORD="" PROVIDER="" AUTOSTART=0

print_help() {
  sed -n '2,20p' "$0"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)  ACCOUNT="$2";  shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --autostart) AUTOSTART=1;  shift ;;
    -h|--help)  print_help ;;
    *) echo "[错误] 未知参数: $1（-h 查看帮助）"; exit 1 ;;
  esac
done

say()  { echo -e "\033[1;32m[安装]\033[0m $*"; }
warn() { echo -e "\033[1;33m[提示]\033[0m $*"; }
die()  { echo -e "\033[1;31m[失败]\033[0m $*"; exit 1; }

# ---------------------------------------------------------------- 1. python3
if ! command -v python3 >/dev/null 2>&1; then
  die "未找到 python3。请先安装：sudo apt install python3 python3-venv python3-pip"
fi
say "python3: $(python3 --version 2>&1)"

# ---------------------------------------------------------------- 2. 虚拟环境
if [[ ! -x venv/bin/python ]]; then
  say "创建独立虚拟环境 ./venv ..."
  if ! python3 -m venv venv 2>/dev/null; then
    die "创建 venv 失败。请安装 venv 组件：sudo apt install python3-venv，然后重新运行本脚本"
  fi
fi
say "安装依赖（requests）..."
venv/bin/pip install --quiet --disable-pip-version-check requests || \
  die "pip 安装失败，请检查网络后重试"

# ---------------------------------------------------------------- 3. 配置文件
if [[ -f config.ini ]]; then
  warn "config.ini 已存在，跳过生成（如需重配请删除后重跑）"
else
  if [[ -z "$ACCOUNT" || -z "$PASSWORD" ]]; then
    read -rp "请输入校园网账号（学号）: " ACCOUNT
    read -rsp "请输入校园网密码: " PASSWORD; echo
    read -rp "代拨运营商 [mobile/unicom/telecom，留空=校园网] (默认 mobile): " PROVIDER
  fi
  PROVIDER="${PROVIDER:-mobile}"
  cat > config.ini <<EOF
[User]
account = ${ACCOUNT}
password = ${PASSWORD}

[Network]
provider = ${PROVIDER}
base_url = http://202.117.144.205:8602/snnuportal

[WiFi]
enabled = false
ssid = SNNU
interface =
auto_switch = false
wait_after_connect = 20

[Settings]
timeout = 8
retries = 3
fallback_selenium = false
headless = true
browser = edge
log_cleanup_days = 30
EOF
  chmod 600 config.ini
  say "已生成 config.ini（权限 600，仅本人可读）"
fi

# ---------------------------------------------------------------- 4. 启动器
cat > snnu_login.sh <<EOF
#!/usr/bin/env bash
cd "\$(dirname "\$(readlink -f "\$0")")"
exec ./venv/bin/python auto_login_api.py "\$@"
EOF
chmod +x snnu_login.sh
say "已生成启动器 ./snnu_login.sh（用法: ./snnu_login.sh [--status|--daemon 参数同 python 版]）"

# ---------------------------------------------------------------- 5. 自启
if [[ "$AUTOSTART" == "1" ]]; then
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/snnu-login.service" <<EOF
[Unit]
Description=SNNU campus network auto login
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/venv/bin/python ${SCRIPT_DIR}/auto_login_api.py

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable snnu-login.service >/dev/null
    say "已注册开机自启（systemd 用户服务），验证: systemctl --user status snnu-login"
  else
    warn "未检测到 systemd，跳过自启配置（可手动把 snnu_login.sh 加入桌面自启动）"
  fi
fi

# ---------------------------------------------------------------- 6. 自检
if venv/bin/python -c "import requests" 2>/dev/null; then
  say "自检通过！现在可以运行:  ./snnu_login.sh --status"
else
  die "自检失败：requests 未装上，请检查网络后重新运行本脚本"
fi

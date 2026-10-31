#!/usr/bin/env bash
# ==============================================================================
# SNNU 校园网自动登录 - Linux / WSL 一键安装脚本（空环境自举版）
#
# 用法：
#   bash install.sh                                            # 交互式安装
#   bash install.sh --account 学号 --password 密码 --provider mobile
#   bash install.sh --account 学号 --password 密码 --autostart
#
# 安装流程：
#   1. 准备 Python：有 python3 直接用；没有就自动用 apt/dnf/yum/pacman 装；
#      都装不上就下载独立的 python-build-standalone 到 ~/.local/share/snnu-python
#   2. 建虚拟环境 venv 并装 requests（不支持 venv 时退回 ./lib 目录方式）
#   3. 引导填写账号 / 密码 / 运营商
#   4. 生成 config.ini（已存在则不覆盖，权限 600）
#   5. 生成启动器 ./snnu_login.sh
#   6. 可选：注册开机自启常驻保活（systemd 用户服务，无 systemd 用 cron @reboot）
#   7. 自检：requests 可导入 + 自启注册状态
#
# 任何一步失败都会告诉你「怎么修、下一步做什么」。
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------- 参数
ACCOUNT="" PASSWORD="" PROVIDER="" AUTOSTART=0
FORCE_BOOTSTRAP=0 AUTOSTART_SET=0 PROVIDER_SET=0

show_help() {
  cat <<'SNNU_HELP'
SNNU 校园网自动登录 - 安装脚本

用法：
  bash install.sh                     交互式安装（依次问账号、密码、运营商、是否自启）
  bash install.sh --account 学号 --password 密码 --provider mobile --autostart

参数：
  --account <学号>      校园网账号（学号）；给了就不再询问
  --password <密码>     校园网密码；给了就不再询问
  --provider <运营商>   mobile(移动) / unicom(联通) / telecom(电信) / 留空=校园网
  --autostart           安装完注册开机自启（常驻保活，掉线自动重连）
  -h, --help            显示本帮助

装完之后：
  ./snnu_login.sh --status    看看现在是否在线（只读，不登录）
  ./snnu_login.sh             登录一次
  ./snnu_login.sh --watch     前台保活，掉线自动重连
SNNU_HELP
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)  if [[ $# -lt 2 ]]; then echo "[错误] --account 后面要跟学号"; exit 1; fi
                ACCOUNT="$2"; shift 2 ;;
    --password) if [[ $# -lt 2 ]]; then echo "[错误] --password 后面要跟密码"; exit 1; fi
                PASSWORD="$2"; shift 2 ;;
    --provider) if [[ $# -lt 2 ]]; then echo "[错误] --provider 后面要跟运营商"; exit 1; fi
                PROVIDER="$2"; PROVIDER_SET=1; shift 2 ;;
    --autostart) AUTOSTART=1; AUTOSTART_SET=1; shift ;;
    --force-bootstrap) FORCE_BOOTSTRAP=1; shift ;;
    -h|--help) show_help ;;
    *) echo "[错误] 未知参数：$1（-h 查看帮助）"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------- 输出工具
if [[ -t 1 ]]; then
  C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'; C_END=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_END=""
fi
say()  { printf '%s[安装]%s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '%s[提示]%s %s\n' "$C_WARN" "$C_END" "$*"; }
die()  { printf '%s[失败]%s %s\n' "$C_ERR" "$C_END" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

TMP_SNNU="$(mktemp -d "${TMPDIR:-/tmp}/snnu-install.XXXXXX" 2>/dev/null || true)"
if [[ -z "$TMP_SNNU" || ! -d "$TMP_SNNU" ]]; then
  TMP_SNNU="$(mktemp -d)"
fi
cleanup_tmp() {
  if [[ -n "${TMP_SNNU:-}" && -d "${TMP_SNNU:-}" ]]; then rm -rf "$TMP_SNNU"; fi
  return 0
}
trap cleanup_tmp EXIT

# ---------------------------------------------------------------- 1. Python
SNNU_PY_HOME="$HOME/.local/share/snnu-python"
CACHE_DIR="$HOME/.cache/snnu-install"
PYTHON_BIN=""
PY_SOURCE=""

python_ok() {
  # $1 = python 可执行文件；3.8 以上才算合格
  if [[ -z "$1" ]]; then return 1; fi
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

find_system_python() {
  local cand=""
  if have python3; then cand="$(command -v python3)"; fi
  if [[ -n "$cand" ]] && python_ok "$cand"; then
    PYTHON_BIN="$cand"; PY_SOURCE="系统自带"
    say "找到系统 Python：$PYTHON_BIN（$("$PYTHON_BIN" --version 2>&1)）"
    return 0
  fi
  if [[ -n "$cand" ]]; then
    warn "系统里的 python3 版本太低（需要 3.8 以上）：$("$cand" --version 2>&1)"
  else
    say "这台机器上没有 python3，准备自动安装..."
  fi
  return 1
}

install_python_by_pkg() {
  local pm="" pkgs="" SUDO="" rc=0
  if have apt-get; then pm="apt-get"; pkgs="python3 python3-venv python3-pip"
  elif have dnf;   then pm="dnf";     pkgs="python3 python3-pip"
  elif have yum;   then pm="yum";     pkgs="python3 python3-pip"
  elif have pacman; then pm="pacman"; pkgs="python python-pip"
  else
    warn "没找到 apt-get / dnf / yum / pacman，没法用系统包管理器装 Python"
    return 1
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
  elif have sudo; then
    SUDO="sudo"
  else
    warn "当前不是 root，又没有 sudo，没法用 $pm 装 Python"
    return 1
  fi
  say "用 $pm 安装 Python（要联网，可能要几分钟）..."
  set +e
  case "$pm" in
    apt-get) $SUDO apt-get update -qq; $SUDO apt-get install -y -qq $pkgs ;;
    pacman)  $SUDO pacman -Sy --noconfirm $pkgs ;;
    *)       $SUDO "$pm" install -y $pkgs ;;
  esac
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    warn "$pm 安装失败（退出码 $rc）"
    return 1
  fi
  hash -r 2>/dev/null || true
  if have python3 && python_ok "$(command -v python3)"; then
    PYTHON_BIN="$(command -v python3)"; PY_SOURCE="$pm 安装"
    say "已用 $pm 装好 Python：$PYTHON_BIN（$("$PYTHON_BIN" --version 2>&1)）"
    return 0
  fi
  warn "$pm 跑完了，但 python3 还是不可用"
  return 1
}

# python-build-standalone 候选资产（tag:文件名），下面每一条都用 curl 验证过真实存在。
# 优先 install_only_stripped：同一个 CPython 3.12.11，体积只有 1/3，校园网下快很多。
PBS_ORIGIN="https://github.com/astral-sh/python-build-standalone/releases/download"
PBS_CANDIDATES="
20250818:cpython-3.12.11+20250818-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
20250818:cpython-3.12.11+20250818-x86_64-unknown-linux-gnu-install_only.tar.gz
20250612:cpython-3.12.11+20250612-x86_64-unknown-linux-gnu-install_only.tar.gz
20241016:cpython-3.12.7+20241016-x86_64-unknown-linux-gnu-install_only.tar.gz
20240415:cpython-3.12.3+20240415-x86_64-unknown-linux-gnu-install_only.tar.gz
"
# 下载源依次：github 官方 -> gh-proxy 前缀 -> ghfast 前缀
PBS_TIERS="
https://gh-proxy.com/
https://ghfast.top/
"

http_exists() {
  # $1 = url：先确认这个地址真的存在，再决定要不要下载
  if have curl; then
    curl -fsIL --connect-timeout 15 --max-time 25 -o /dev/null "$1" >/dev/null 2>&1
  elif have wget; then
    wget -q --spider --timeout=20 --tries=1 "$1" >/dev/null 2>&1
  else
    return 1
  fi
}

fetch_file() {
  # $1 = url, $2 = 保存路径, $3 = 源标签
  # 校园网下 github 常常几十秒就断：断了就带着已有进度续传，最多试 40 次。
  # 下载是否完整的判据是这个压缩包能不能正常解开（断在半截的包一定解不开）。
  local url="$1" dest="$2" key="$3"
  local part="" attempt=0 before=0 after=0 rc=0 stall=0 limit=6
  local prog="-sS"
  if [[ -t 1 ]]; then prog="--progress-bar"; fi
  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  part="$CACHE_DIR/$(basename "$dest").$key.part"
  if [[ -s "$part" ]] && tar -tzf "$part" >/dev/null 2>&1; then
    say "  缓存里已经是完整的压缩包，直接用"
    mv -f "$part" "$dest"
    return $?
  fi
  before="$(stat -c %s "$part" 2>/dev/null || echo 0)"
  if [[ $before -gt 0 ]]; then
    say "  发现上次没下完的缓存（$((before/1024)) KB），接着下"
  fi
  while [[ $attempt -lt 40 ]]; do
    attempt=$((attempt+1))
    before="$(stat -c %s "$part" 2>/dev/null || echo 0)"
    rc=0
    if have curl; then
      curl -fL $prog -C - --connect-timeout 20 --max-time 300 \
           --speed-limit 1024 --speed-time 60 -o "$part" "$url" || rc=$?
    else
      wget -q -c -T 30 -O "$part" "$url" || rc=$?
    fi
    if tar -tzf "$part" >/dev/null 2>&1; then
      break
    fi
    after="$(stat -c %s "$part" 2>/dev/null || echo 0)"
    if [[ $after -lt $before ]]; then
      warn "  这个源不支持断点续传（下载被重置），换下一个源"
      return 1
    fi
    if [[ $after -eq $before ]]; then
      stall=$((stall+1))
      limit=6
      if [[ $after -ge 1048576 ]]; then limit=20; fi
      if [[ $stall -ge $limit ]]; then
        warn "  这个源连续 $stall 次没有进度，换下一个源"
        return 1
      fi
      sleep 3
      continue
    fi
    stall=0
    say "  连接断了，已下到 $((after/1024)) KB，自动续传（第 $attempt 次）"
  done
  if ! tar -tzf "$part" >/dev/null 2>&1; then
    warn "  续传 $attempt 次还是没下完整"
    return 1
  fi
  mv -f "$part" "$dest"
  return 0
}

bootstrap_python() {
  # 已经装过独立 Python 就直接复用，不重复下载 30MB
  if [[ -x "$SNNU_PY_HOME/bin/python3" ]] && python_ok "$SNNU_PY_HOME/bin/python3"; then
    PYTHON_BIN="$SNNU_PY_HOME/bin/python3"
    PY_SOURCE="独立 Python（复用已安装的）"
    say "复用已装好的独立 Python：$PYTHON_BIN（$("$PYTHON_BIN" --version 2>&1)）"
    return 0
  fi
  if ! have curl && ! have wget; then
    die "这台机器上既没有 curl 也没有 wget，没法自动下载 Python。请任选一种方式：
  A. 先装下载工具：sudo apt install curl（Fedora/CentOS 用 sudo dnf install curl），然后重新运行本脚本
  B. 先装 Python：sudo apt install python3 python3-venv python3-pip，然后重新运行本脚本
  C. 手动下载独立 Python：在能上网的机器打开
     https://github.com/astral-sh/python-build-standalone/releases
     下载 cpython-3.12.*-x86_64-unknown-linux-gnu-install_only.tar.gz，
     解压后把里面的 python 目录整个放到 $SNNU_PY_HOME，再重新运行本脚本"
  fi
  say "开始下载独立 Python（30-100MB；校园网慢，这一步可能要十几分钟，请耐心等）"
  local dest="$TMP_SNNU/pbs.tar.gz" entry tag asset tier url base found pyroot key
  local got=0
  for entry in $PBS_CANDIDATES; do
    tag="${entry%%:*}"
    asset="${entry#*:}"
    base="$PBS_ORIGIN/$tag/$asset"
    for tier in "" $PBS_TIERS; do
      url="${tier}${base}"
      if [[ -z "$tier" ]]; then
        say "  下载源 github 官方（tag $tag）"
      else
        say "  下载源 ${tier}（tag $tag）"
      fi
      key="${tier:-official}"
      key="${key//[^a-zA-Z0-9._]/_}"
      if http_exists "$url"; then
        say "  地址可用，开始下载"
      else
        warn "  地址探测超时，仍然试着下（有的线路只放行 GET）"
      fi
      if fetch_file "$url" "$dest" "$key"; then
        got=1
        break
      fi
      warn "  这个源下载中断，换下一个源"
    done
    if [[ $got -eq 1 ]]; then
      say "下载完成：$url"
      rm -f "$CACHE_DIR"/pbs.tar.gz.*.part 2>/dev/null || true
      break
    fi
  done
  if [[ $got -ne 1 ]]; then
    die "所有下载源都失败了（校园网对 github 经常不稳）。请任选一种方式：
  A. 换个网络好的时间点重跑本脚本
  B. 先用包管理器装 Python：sudo apt install python3 python3-venv python3-pip
  C. 手动下载：在能上网的机器打开
     https://github.com/astral-sh/python-build-standalone/releases
     下载 cpython-3.12.*-x86_64-unknown-linux-gnu-install_only.tar.gz，
     解压后把里面的 python 目录整个放到 $SNNU_PY_HOME，再重新运行本脚本"
  fi
  say "解压独立 Python..."
  rm -rf "$TMP_SNNU/extract"
  mkdir -p "$TMP_SNNU/extract"
  if ! tar -xzf "$dest" -C "$TMP_SNNU/extract"; then
    die "解压失败（多半是没下完）。删掉重跑本脚本即可；也可以手动解压后放到 $SNNU_PY_HOME"
  fi
  pyroot="$TMP_SNNU/extract/python"
  if [[ ! -x "$pyroot/bin/python3" ]]; then
    found="$(find "$TMP_SNNU/extract" -maxdepth 4 -type f -name python3 -path '*/bin/*' 2>/dev/null | head -n 1)"
    if [[ -n "$found" ]]; then
      pyroot="$(dirname "$(dirname "$found")")"
    fi
  fi
  if [[ ! -x "$pyroot/bin/python3" ]]; then
    die "解压出来的包里没找到 python3，可能是下载不完整。删掉重跑本脚本，或用 B/C 方式装 Python。"
  fi
  mkdir -p "$(dirname "$SNNU_PY_HOME")"
  rm -rf "$SNNU_PY_HOME"
  if ! mv "$pyroot" "$SNNU_PY_HOME" 2>/dev/null; then
    if ! cp -a "$pyroot" "$SNNU_PY_HOME"; then
      die "安装独立 Python 失败（$SNNU_PY_HOME 写不进去？）。请检查目录权限后重跑。"
    fi
  fi
  PYTHON_BIN="$SNNU_PY_HOME/bin/python3"
  PY_SOURCE="独立 Python（python-build-standalone）"
  if ! python_ok "$PYTHON_BIN"; then
    die "独立 Python 装好了但跑不起来：$PYTHON_BIN。请把这条信息反馈给开发者。"
  fi
  say "独立 Python 就绪：$PYTHON_BIN（$("$PYTHON_BIN" --version 2>&1)）"
}

if [[ "$FORCE_BOOTSTRAP" == "1" ]]; then
  warn "已启用 --force-bootstrap（内部测试开关）：跳过系统 Python，强制走自举下载"
  bootstrap_python
elif find_system_python; then
  :
elif install_python_by_pkg; then
  :
else
  bootstrap_python
fi

if [[ -z "$PYTHON_BIN" ]]; then
  die "没能准备好可用的 Python（需要 3.8 以上）。请手动安装：sudo apt install python3 python3-venv python3-pip，然后重跑本脚本。"
fi

# ---------------------------------------------------------------- 2. 依赖环境
VENV_DIR="$SCRIPT_DIR/venv"
LIB_DIR="$SCRIPT_DIR/lib"
RUNTIME_PY=""
USE_TARGET=0

if [[ -x "$VENV_DIR/bin/python" ]]; then
  RUNTIME_PY="$VENV_DIR/bin/python"
  say "复用已有虚拟环境：$VENV_DIR"
else
  say "创建虚拟环境 venv ..."
  if "$PYTHON_BIN" -m venv "$VENV_DIR" >/dev/null 2>&1 && [[ -x "$VENV_DIR/bin/python" ]]; then
    RUNTIME_PY="$VENV_DIR/bin/python"
  else
    rm -rf "$VENV_DIR"
    warn "这台机器不支持 venv（Debian/Ubuntu 常见原因：没装 python3-venv）"
    say "改用「把依赖装到 ./lib 目录」的方式（效果一样，只是启动器里多一句 PYTHONPATH）"
    USE_TARGET=1
    RUNTIME_PY="$PYTHON_BIN"
  fi
fi

if [[ $USE_TARGET -eq 0 ]] && ! "$RUNTIME_PY" -m pip --version >/dev/null 2>&1; then
  "$RUNTIME_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if [[ $USE_TARGET -eq 0 ]] && ! "$RUNTIME_PY" -m pip --version >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"
  warn "虚拟环境里没有 pip，退回「装到 ./lib 目录」的方式"
  USE_TARGET=1
  RUNTIME_PY="$PYTHON_BIN"
fi

pip_install_requests() {
  # $1 = 索引地址（空字符串 = 默认源）；pip 的啰嗦输出收进日志，失败时只摘关键行
  local idx="$1"
  local -a args=("$RUNTIME_PY" -m pip install -q --disable-pip-version-check --retries 2 --timeout 30)
  if [[ $USE_TARGET -eq 1 ]]; then
    args+=(--target "$LIB_DIR")
  fi
  if [[ -n "$idx" ]]; then
    args+=(-i "$idx")
  fi
  args+=(requests)
  "${args[@]}" >"$TMP_SNNU/pip.log" 2>&1
}

pip_last_error() {
  if [[ -f "$TMP_SNNU/pip.log" ]]; then
    grep -E '^ERROR:' "$TMP_SNNU/pip.log" 2>/dev/null | tail -n 2 | sed 's/^/    /' || true
  fi
  return 0
}

install_deps() {
  say "安装依赖 requests ..."
  if pip_install_requests ""; then
    say "依赖装好了（默认源）"
    return 0
  fi
  pip_last_error
  warn "默认源装不上，换清华镜像重试..."
  if pip_install_requests "https://pypi.tuna.tsinghua.edu.cn/simple"; then
    say "依赖装好了（清华镜像）"
    return 0
  fi
  pip_last_error
  warn "清华镜像也不通，换阿里云镜像重试..."
  if pip_install_requests "https://mirrors.aliyun.com/pypi/simple/"; then
    say "依赖装好了（阿里云镜像）"
    return 0
  fi
  pip_last_error
  warn "阿里云镜像也不通，换腾讯云镜像重试..."
  if pip_install_requests "https://mirrors.cloud.tencent.com/pypi/simple/"; then
    say "依赖装好了（腾讯云镜像）"
    return 0
  fi
  pip_last_error
  return 1
}

MANUAL_PIP="$RUNTIME_PY -m pip install requests"
if [[ $USE_TARGET -eq 1 ]]; then
  MANUAL_PIP="$RUNTIME_PY -m pip install --target $LIB_DIR requests"
fi

if ! install_deps; then
  die "requests 装不上（网络不通）。下一步可以这样排查：
  1) 先确认这台机器能上外网：curl -I https://pypi.org/simple/
  2) 手动装一次看报错：$MANUAL_PIP
  3) 装好后重新运行本脚本即可（已装好的部分不会重复下载）"
fi

# ---------------------------------------------------------------- 3. 交互配置
normalize_provider() {
  case "$1" in
    1|mobile|移动|M)    echo "mobile" ;;
    2|unicom|联通|U)    echo "unicom" ;;
    3|telecom|电信|T)   echo "telecom" ;;
    4|campus|校园网|"") echo "" ;;
    *) echo "$1" ;;
  esac
}

CONFIG_EXISTS=0
if [[ -f "$SCRIPT_DIR/config.ini" ]]; then
  CONFIG_EXISTS=1
  warn "config.ini 已存在，保留原文件，不再询问账号/密码/运营商"
fi

if [[ $CONFIG_EXISTS -eq 0 ]]; then
  while [[ -z "$ACCOUNT" ]]; do
    if ! read -r -p "请输入校园网账号（学号）：" ACCOUNT; then
      die "读不到输入（像是非交互环境）。请改用参数运行：
  bash install.sh --account 学号 --password 密码 --provider mobile"
    fi
    if [[ -z "$ACCOUNT" ]]; then
      warn "账号不能为空，请重新输入（想退出按 Ctrl+C）"
    fi
  done
  if [[ -z "$PASSWORD" ]]; then
    if ! read -r -s -p "请输入校园网密码（输入时不显示）：" PASSWORD; then
      die "读不到输入（像是非交互环境）。请改用参数运行：
  bash install.sh --account 学号 --password 密码 --provider mobile"
    fi
    printf '\n'
    if [[ -z "$PASSWORD" ]]; then
      die "密码不能为空。请重跑本脚本再输一次：bash install.sh"
    fi
  fi
  if [[ $PROVIDER_SET -eq 0 ]]; then
    ans_provider=""
    if ! read -r -p "请选择运营商 [1] 移动 [2] 联通 [3] 电信 [4] 校园网（默认 4）：" ans_provider; then
      ans_provider=""
    fi
    PROVIDER="$(normalize_provider "$ans_provider")"
  fi
fi

PROVIDER="$(normalize_provider "$PROVIDER")"
case "$PROVIDER" in
  ""|mobile|unicom|telecom) : ;;
  *) warn "运营商取值有点陌生（$PROVIDER），会原样写进 config.ini" ;;
esac

if [[ $AUTOSTART_SET -eq 0 ]]; then
  ans_autostart=""
  if ! read -r -p "是否开机自启（常驻保活，掉线自动重连）? [y/N] " ans_autostart; then
    ans_autostart=""
  fi
  case "$ans_autostart" in
    y|Y|yes|YES|Yes|是) AUTOSTART=1 ;;
    *) AUTOSTART=0 ;;
  esac
fi

# ---------------------------------------------------------------- 4. config.ini
if [[ $CONFIG_EXISTS -eq 0 ]]; then
  cat > "$SCRIPT_DIR/config.ini" <<EOF
[User]
account = $ACCOUNT
password = $PASSWORD

[Network]
# 代拨运营商：mobile / unicom / telecom，留空表示校园网（不代拨）
provider = $PROVIDER
base_url = http://202.117.144.205:8602/snnuportal

[WiFi]
# 门户不可达时是否自动连校园网 Wi-Fi（目前仅 Windows 生效）
enabled = false
ssid = SNNU
interface =
auto_switch = false
wait_after_connect = 20

[Settings]
timeout = 8
retries = 3
EOF
  chmod 600 "$SCRIPT_DIR/config.ini"
  say "已生成 config.ini（权限 600，只有你能读）"
fi

# ---------------------------------------------------------------- 5. 启动器
cat > "$SCRIPT_DIR/snnu_login.sh" <<'SNNU_LAUNCHER'
#!/usr/bin/env bash
# 由 install.sh 生成：SNNU 校园网自动登录启动器
set -uo pipefail
SELF="$0"
if [[ -L "$SELF" ]]; then
  SELF="$(readlink -f "$SELF" 2>/dev/null || echo "$SELF")"
fi
DIR="$(cd "$(dirname "$SELF")" && pwd)"
cd "$DIR" || exit 1

# 布局一：venv 虚拟环境
if [[ -x "$DIR/venv/bin/python" ]]; then
  exec "$DIR/venv/bin/python" "$DIR/auto_login_api.py" "$@"
fi

# 布局二：独立 Python / 系统 Python + ./lib 目录
PY=""
for c in "$HOME/.local/share/snnu-python/bin/python3" python3 python; do
  if [[ -x "$c" ]]; then PY="$c"; break; fi
  if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
done
if [[ -z "$PY" ]]; then
  echo "[失败] 找不到 Python。请重新运行 install.sh，或先安装 python3 再试。" >&2
  exit 1
fi
if [[ -d "$DIR/lib" ]]; then
  export PYTHONPATH="$DIR/lib${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$PY" "$DIR/auto_login_api.py" "$@"
SNNU_LAUNCHER
chmod +x "$SCRIPT_DIR/snnu_login.sh"
say "已生成启动器：$SCRIPT_DIR/snnu_login.sh"

# ---------------------------------------------------------------- 6. 开机自启
AUTOSTART_MODE=""
RUN_PY="$RUNTIME_PY"

register_systemd() {
  local UNIT_DIR="$HOME/.config/systemd/user"
  local UNIT="snnu-login.service"
  local ENV_LINE="" state=""
  mkdir -p "$UNIT_DIR"
  # 幂等：先把旧的关掉（没有 user bus 的环境 disable --now 会失败，退回纯 disable）
  systemctl --user disable --now "$UNIT" >/dev/null 2>&1 || systemctl --user disable "$UNIT" >/dev/null 2>&1 || true
  if [[ $USE_TARGET -eq 1 ]]; then
    ENV_LINE="Environment=PYTHONPATH=$LIB_DIR"
  fi
  cat > "$UNIT_DIR/$UNIT" <<EOF
[Unit]
Description=SNNU campus network auto login (keep-alive)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$RUN_PY $SCRIPT_DIR/auto_login_watch.py --daemon
$ENV_LINE
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  if ! systemctl --user enable "$UNIT" >/dev/null 2>&1; then
    # 兜底：没有 user bus 的环境（例如没开 systemd 的 WSL）手工建软链接
    mkdir -p "$UNIT_DIR/default.target.wants"
    if ! ln -sf "$UNIT_DIR/$UNIT" "$UNIT_DIR/default.target.wants/$UNIT" 2>/dev/null; then
      return 1
    fi
  fi
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
  state="$(systemctl --user is-enabled "$UNIT" 2>/dev/null || true)"
  if [[ "$state" != "enabled" && ! -L "$UNIT_DIR/default.target.wants/$UNIT" ]]; then
    return 1
  fi
  AUTOSTART_MODE="systemd 用户服务（snnu-login.service）"
  return 0
}

register_cron() {
  have crontab || return 1
  local LINE="@reboot cd $SCRIPT_DIR && $RUN_PY $SCRIPT_DIR/auto_login_watch.py --daemon >> $SCRIPT_DIR/auto_login.log 2>&1"
  local cur=""
  cur="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$cur" | grep -qF "auto_login_watch.py --daemon"; then
    say "cron 自启已存在，跳过（幂等）"
  else
    if ! printf '%s\n%s\n' "$cur" "$LINE" | grep -v '^[[:space:]]*$' | crontab -; then
      return 1
    fi
    say "已写入 cron @reboot 自启"
  fi
  AUTOSTART_MODE="cron @reboot"
  return 0
}

register_autostart() {
  if have systemctl; then
    if register_systemd; then
      return 0
    fi
    warn "systemd 用户服务没注册成功，改用 cron 兜底..."
  fi
  if register_cron; then
    return 0
  fi
  return 1
}

if [[ $AUTOSTART -eq 1 ]]; then
  if register_autostart; then
    say "已注册开机自启：$AUTOSTART_MODE"
  else
    warn "自启没注册上（systemd 和 cron 都用不了）。可以手动把下面这条命令加进你的开机启动项：
  $RUN_PY $SCRIPT_DIR/auto_login_watch.py --daemon"
  fi
fi

# ---------------------------------------------------------------- 7. 自检
say "自检中..."
if [[ $USE_TARGET -eq 1 ]]; then
  if ! PYTHONPATH="$LIB_DIR" "$RUNTIME_PY" -c 'import requests' >/dev/null 2>&1; then
    die "自检失败：requests 在 $LIB_DIR 里导不进来。重新跑本脚本；一直失败就把 $LIB_DIR 删掉再重跑。"
  fi
else
  if ! "$RUNTIME_PY" -c 'import requests' >/dev/null 2>&1; then
    die "自检失败：虚拟环境里 requests 导不进来。把 $VENV_DIR 删掉后重新运行本脚本。"
  fi
fi
say "自检通过：requests 可用"

if [[ "$AUTOSTART_MODE" == systemd* ]]; then
  state_now="$(systemctl --user is-enabled snnu-login.service 2>/dev/null || true)"
  if [[ "$state_now" == "enabled" ]]; then
    say "自启自检通过：systemctl --user is-enabled snnu-login = $state_now"
  else
    warn "自启状态是「$state_now」，不是 enabled。可以用 systemctl --user status snnu-login 看看。"
  fi
elif [[ "$AUTOSTART_MODE" == cron* ]]; then
  say "自启自检：crontab 里已经有 auto_login_watch.py --daemon 这一行"
fi

if [[ $USE_TARGET -eq 1 ]]; then
  DEP_MODE="./lib 目录（启动器已自动加 PYTHONPATH）"
else
  DEP_MODE="venv 虚拟环境"
fi

printf '\n'
say "================ 安装完成 ================"
say "项目目录 ：$SCRIPT_DIR"
say "Python   ：$RUNTIME_PY（$PY_SOURCE）"
say "依赖方式 ：$DEP_MODE"
say "配置文件 ：$SCRIPT_DIR/config.ini"
if [[ -n "$AUTOSTART_MODE" ]]; then
  say "开机自启 ：$AUTOSTART_MODE（掉线自动重连）"
else
  say "开机自启 ：未注册（想要就重跑本脚本，最后一步选 y）"
fi
printf '\n接下来可以这样用：\n'
printf '  ./snnu_login.sh --status   看看现在是否在线\n'
printf '  ./snnu_login.sh            登录一次\n'
printf '  ./snnu_login.sh --watch    前台保活，掉线自动重连\n'
printf '  想改账号密码：删掉 config.ini 再跑一遍 install.sh\n\n'

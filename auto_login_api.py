#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
陕Star 校园网自动登录 - 接口版（纯 HTTP，不启动浏览器）

原理：直接向门户 POST 登录表单，等价于人工在登录页点"登录"。
      - 表单字段：sourceurl / account / password / yys / issave
      - 密码为明文提交，无前端加密
      - 运营商（代拨）之只是前端弹窗确认，无额外接口，因此可用 yys 直接指定

用法：
    python auto_login_api.py              # 检测并登录（已在线则跳过）
    python auto_login_api.py --status      # 只看状态，不登录
    python auto_login_api.py --logoff      # 主动断开
    python auto_login_api.py --test-cycle  # 端到端自检：断开 -> 重连（真实断网数秒）
    python auto_login_api.py --setup       # 重新填写账号密码

首次运行（没有 config.ini）时会自动进入配置向导，按提示输入学号和密码即可。
"""

import argparse
import configparser
import os
import re
import subprocess
import sys
import time
from datetime import datetime

import requests

from wifi_ctl import WifiController

# 打包成 exe 后 __file__ 指向临时解包目录，改用 exe 所在目录定位配置
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.ini')
LOG_FILE = os.path.join(SCRIPT_DIR, 'auto_login.log')

DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

RE_ACCOUNT = re.compile(r'当前登录账号：<span class="zhengwen1">([^<]+)</span>')
RE_LOGIN_TIME = re.compile(r'登录时间：([\d\-: ]+)')
RE_ALERT = re.compile(r'alert\("([^"]*)"\)')
RE_FORM_CHECKCODE = re.compile(r'name="checkcode"', re.I)

# 门户常见错误文案，用于把登录失败原因写进日志
ERROR_HINTS = ['密码错误', '账号或密码', '账号不存在', '已在线', '已达上限',
               '余额不足', '已停机', '验证码', '账号被锁定', '无法登录']


# ---------------------------------------------------------------- 日志
def write_log(level: str, message: str):
    """追加写日志，UTF-8 BOM，兼容 Windows 记事本"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{ts} - {level.upper():>5} - {message}\n"
    try:
        with open(LOG_FILE, 'a', encoding='utf-8-sig') as f:
            f.write(line)
    except Exception:
        pass
    # 同时输出到控制台，方便手动运行时直接看
    try:
        if sys.stdout and not sys.stdout.closed:
            print(line.rstrip())
    except Exception:
        pass


def log_info(msg):
    write_log('INFO', msg)


def log_warn(msg):
    write_log('WARN', msg)


def log_error(msg):
    write_log('ERROR', msg)


def build_wifi_controller(cfg: configparser.ConfigParser):
    """按配置构造 Wi-Fi 控制器；未启用则返回 None"""
    enabled = cfg.getboolean('WiFi', 'enabled', fallback=False)
    if not enabled:
        return None
    ssids = [s.strip() for s in cfg.get('WiFi', 'ssid', fallback='SNNU').split(',')]
    return WifiController(
        campus_ssids=ssids,
        interface=cfg.get('WiFi', 'interface', fallback='').strip() or None,
        auto_switch=cfg.getboolean('WiFi', 'auto_switch', fallback=False),
        wait_after_connect=cfg.getint('WiFi', 'wait_after_connect', fallback=20),
    )


def trim_log_file(max_lines: int = 5000):
    try:
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, 'r', encoding='utf-8-sig', errors='replace') as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(LOG_FILE, 'w', encoding='utf-8-sig') as f:
                f.writelines(lines[-max_lines:])
    except Exception:
        pass


# ---------------------------------------------------------------- 配置
PROVIDER_CHOICES = [
    ('1', 'mobile', '中国移动'),
    ('2', 'unicom', '中国联通'),
    ('3', 'telecom', '中国电信'),
    ('4', '', '校园网（不代拨）'),
]

TEMPLATE = """[User]
account = {account}
password = {password}

[Network]
# 代拨运营商：mobile / unicom / telecom，留空表示校园网（不代拨）
provider = {provider}
base_url = http://202.117.144.205:8602/snnuportal

[WiFi]
# 是否在门户不可达时自动连接校园网 Wi-Fi（目前仅 Windows 生效）
enabled = {wifi_enabled}
ssid = SNNU
interface =
# 已连着其他 Wi-Fi（如手机热点）时，是否强制切回校园网
auto_switch = false
wait_after_connect = 20

[Settings]
timeout = 8
retries = 3
"""


def _ask(prompt: str, default: str = '') -> str:
    """读取一行输入；EOF / 非交互时抛出 EOFError 由上层处理"""
    tip = f'{prompt}' + (f'（默认 {default}）' if default else '')
    try:
        print(tip, end='', flush=True)
    except Exception:
        pass
    line = input().strip()
    return line or default


def _ask_password() -> str:
    """优先用 getpass 隐藏输入，失败（无 tty / 异常）时退回明文输入"""
    try:
        import getpass
        return getpass.getpass('请输入校园网密码（输入时不显示）: ').strip()
    except Exception:
        print('请输入校园网密码: ', end='', flush=True)
        return input().strip()


def _resolve_shortcut_target() -> str:
    """解析自启快捷方式的目标路径（exe 或脚本）"""
    if getattr(sys, 'frozen', False):
        # 打包态：目标为 exe 自身，参数 --watch --no-pause
        return f'"{sys.executable}"'
    else:
        # 源码态：目标为 python.exe + 本脚本路径
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def _resolve_shortcut_args() -> str:
    """解析自启快捷方式的命令行参数"""
    if getattr(sys, 'frozen', False):
        return '--watch --no-pause'
    else:
        return '--watch'


def _get_startup_dir() -> str:
    """获取 Windows 用户启动文件夹路径；支持环境变量 SNNU_STARTUP_DIR 覆盖（自动化测试钩子）"""
    if 'SNNU_STARTUP_DIR' in os.environ:
        return os.environ['SNNU_STARTUP_DIR']
    # shell:startup 通过 PowerShell 解析
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command', '[Environment]::GetFolderPath("Startup")'],
            capture_output=True, text=True, timeout=10)
        path = r.stdout.strip()
        if path:
            return path
    except Exception:
        pass
    # 兜底：手动拼 APPDATA 路径
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        return os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
    return ''


def _register_startup() -> bool:
    """
    在 Windows 用户启动文件夹写入本工具的自启 .lnk 快捷方式。
    幂等：重复注册覆盖同名 .lnk。返回是否成功。
    """
    startup_dir = _get_startup_dir()
    if not startup_dir:
        log_warn('无法获取 Windows 启动文件夹路径，跳过自启注册')
        return False
    lnk_path = os.path.join(startup_dir, '陕Star校园网自动登录.lnk')
    target = _resolve_shortcut_target()
    args = _resolve_shortcut_args()

    # 使用 PowerShell WScript.Shell 创建 .lnk，WindowStyle=7 最小化
    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("{lnk_path}")
$sc.TargetPath = "{sys.executable if getattr(sys, 'frozen', False) else sys.executable}"
'''
    if not getattr(sys, 'frozen', False):
        ps_script += f'$sc.Arguments = "{os.path.abspath(__file__)} --watch"\n'
    else:
        ps_script += '$sc.Arguments = "--watch --no-pause"\n'
    ps_script += '''
$sc.WindowStyle = 7
$sc.Save()
'''
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_script],
            capture_output=True, text=True, timeout=30)
        # 回读验证
        if os.path.exists(lnk_path):
            log_info(f'已注册开机自启: {lnk_path}')
            return True
        else:
            log_warn(f'自启注册后 .lnk 未出现: {lnk_path}，stderr={r.stderr[:200]}')
            return False
    except Exception as e:
        log_warn(f'自启注册失败: {e.__class__.__name__} {e}')
        return False


def _unregister_startup() -> bool:
    """删除本工具的自启 .lnk；已不存在时视为成功。"""
    startup_dir = _get_startup_dir()
    if not startup_dir:
        return True
    lnk_path = os.path.join(startup_dir, '陕Star校园网自动登录.lnk')
    if not os.path.exists(lnk_path):
        return True
    try:
        os.remove(lnk_path)
        log_info(f'已删除开机自启: {lnk_path}')
        return True
    except Exception as e:
        log_warn(f'删除自启快捷方式失败: {e.__class__.__name__} {e}')
        return False


def interactive_setup() -> bool:
    """
    首次运行向导：config.ini 不存在时引导用户填写账号密码并生成配置文件。
    返回是否成功生成。
    """
    print()
    print('=' * 56)
    print('  首次运行：需要先配置你的校园网账号')
    print('=' * 56)
    print('（这些信息只会保存在本机的 config.ini，不会上传到任何地方）')
    print()

    account = _ask('请输入校园网账号（学号）: ')
    if not account:
        print('[失败] 账号不能为空。')
        return False

    password = _ask_password()
    if not password:
        print('[失败] 密码不能为空。')
        return False

    print()
    print('请选择上网方式（代拨运营商）：')
    for key, _val, label in PROVIDER_CHOICES:
        print(f'  {key}. {label}')
    picked = _ask('输入序号', '1')
    provider = next((v for k, v, _ in PROVIDER_CHOICES if k == picked),
                    'mobile' if picked not in ('2', '3', '4') else '')
    print(f'已选择: {provider or "校园网（不代拨）"}')

    wifi_enabled = 'true' if os.name == 'nt' else 'false'

    # 开机自启询问（仅 Windows）
    if sys.platform == 'win32':
        print()
        ans = _ask('是否开机自启（常驻保活，掉线自动重连）? [y/N]', 'n').lower()
        if ans == 'y':
            _register_startup()
            print('已注册开机自启。')
        else:
            _unregister_startup()
            print('未注册开机自启。')
    else:
        print()
        print('提示: 非 Windows 系统，可在 install.sh 中配置自启。')

    content = TEMPLATE.format(account=account, password=password,
                              provider=provider, wifi_enabled=wifi_enabled)
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        if os.name != 'nt':
            try:
                os.chmod(CONFIG_FILE, 0o600)
            except Exception:
                pass
    except Exception as e:
        print(f'[失败] 无法写入配置文件 {CONFIG_FILE}: {e}')
        print('       若程序装在 C:\\Program Files 等受保护目录，请换到普通文件夹再运行。')
        return False

    print()
    print(f'[成功] 已生成配置文件: {CONFIG_FILE}')
    print('       以后想改账号密码，直接编辑这个文件即可。')
    print('=' * 56)
    print()
    log_info(f'首次运行向导已生成配置文件（账号={account}, 运营商={provider or "校园网"}）')
    return True


def load_config(allow_setup: bool = True) -> configparser.ConfigParser:
    # interpolation=None：密码里含 % 时不会被当成插值语法而报错
    cfg = configparser.ConfigParser(interpolation=None)
    if not os.path.exists(CONFIG_FILE):
        # 只在"有人在看着屏幕"时才引导填写；计划任务/管道里无法交互，直接报错更清楚
        interactive = allow_setup and sys.stdin is not None and sys.stdin.isatty()
        if interactive and interactive_setup():
            cfg.read(CONFIG_FILE, encoding='utf-8')
            return cfg
        log_error(f'未找到配置文件 {CONFIG_FILE}')
        if not interactive:
            log_error('当前为非交互环境（后台/计划任务），无法引导填写。')
        log_error('请手动创建：把 config.example.ini 复制为 config.ini 并填入账号密码，'
                  '或在命令行下直接运行本程序按提示填写。')
        raise SystemExit(2)
    cfg.read(CONFIG_FILE, encoding='utf-8')
    return cfg


def decode(resp) -> str:
    """门户页面是 GB2312，兜底 gb18030"""
    raw = resp.content
    for enc in ('gb18030', 'utf-8'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('gb18030', errors='replace')


def wait_for_network(client, wifi, max_wait_minutes: int, check_interval: int) -> bool:
    """
    等待校园网可用：门户不可达时先尝试把 Wi-Fi 连上校园网，再检测门户。
    连 Wi-Fi 只在没网的时候发生，正常上网时不干预。
    """
    start = time.time()
    deadline = max_wait_minutes * 60
    log_info(f'开始等待校园网连接，最长 {max_wait_minutes} 分钟，每 {check_interval} 秒检测一次')

    while True:
        if wifi:
            wifi.ensure_campus_wifi(client.portal_reachable)
        if client.portal_reachable():
            return True
        if time.time() - start > deadline:
            log_error(f'等待超时（{max_wait_minutes} 分钟），未检测到校园网')
            return False
        log_info('校园网门户不可达，继续等待...')
        time.sleep(check_interval)


def run_watch(args) -> int:
    """常驻保活模式：等待校园网 → 登录 → 周期性检查 → 掉线自动重连。
    由 --watch (auto_login_api.py) 或 --daemon (auto_login_watch.py) 触发。"""
    cfg = load_config()
    client = PortalClient(cfg)
    wifi = build_wifi_controller(cfg) if not args.no_wifi else None

    # 等待校园网出现并完成首次登录
    if not wait_for_network(client, wifi, args.wait, args.interval):
        return 1

    ok = client.ensure_online()

    # 非 watch 模式（仅 --daemon 时由旧 watch 调用进来），仅完成单次登录
    daemon = getattr(args, 'watch', False) or getattr(args, 'daemon', False)
    if not daemon:
        log_info('单次登录模式结束')
        return 0 if ok else 1

    # 常驻：周期性检查，掉线自动重连
    log_info(f'进入常驻模式，每 {args.interval} 秒检查一次在线状态')
    while True:
        time.sleep(args.interval)
        try:
            online, acc, ts, _ = client.get_status()
            if online:
                continue
            log_warn('检测到已掉线，先确认 Wi-Fi，再尝试重新登录')
            if wifi:
                wifi.ensure_campus_wifi(client.portal_reachable)
            client.ensure_online()
        except KeyboardInterrupt:
            log_info('收到退出信号，监听结束')
            return 0
        except Exception as e:
            log_warn(f'检查异常: {e.__class__.__name__} {e}')


class PortalClient:
    def __init__(self, cfg: configparser.ConfigParser):
        self.account = cfg.get('User', 'account').strip()
        self.password = cfg.get('User', 'password')
        self.provider = cfg.get('Network', 'provider', fallback='').strip().lower()
        self.base_url = cfg.get('Network', 'base_url',
                                fallback='http://202.117.144.205:8602/snnuportal').rstrip('/')
        self.timeout = cfg.getint('Settings', 'timeout', fallback=8)
        self.retries = cfg.getint('Settings', 'retries', fallback=3)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': DEFAULT_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
        })

    # ---- 基础
    def _url(self, path: str) -> str:
        return f'{self.base_url}/{path.lstrip("/")}'

    def _fallback_base(self):
        """8601 / 8602 是同一套门户的双端口，主端口不通时切换"""
        idx = self.base_url.rfind(':')
        if idx < 0:
            return None
        host_and_scheme, port = self.base_url[:idx], self.base_url[idx + 1:]
        alt = '8601' if port != '8601' else '8602'
        return f'{host_and_scheme}:{alt}'

    # ---- 探测
    def portal_reachable(self) -> bool:
        for base in (self.base_url, self._fallback_base()):
            if not base:
                continue
            try:
                r = self.session.get(f'{base}/login', timeout=3, allow_redirects=False)
                if r.status_code in (200, 302, 301):
                    self.base_url = base
                    log_info(f'门户可达: {base} (HTTP {r.status_code})')
                    return True
            except Exception as e:
                log_info(f'门户探测失败 {base}: {e.__class__.__name__}')
        return False

    def get_status(self):
        """
        返回 (是否在线, 在线账号, 登录时间, 页面原文)
        未登录时门户会把 userstatus.jsp 重定向到 login.jsp
        """
        try:
            r = self.session.get(self._url('userstatus.jsp'), timeout=self.timeout,
                                 allow_redirects=True)
            text = decode(r)
            if 'login.jsp' in r.url or 'name="password"' in text:
                return False, None, None, text
            m = RE_ACCOUNT.search(text)
            if m:
                t = RE_LOGIN_TIME.search(text)
                return True, m.group(1).strip(), (t.group(1).strip() if t else None), text
            return False, None, None, text
        except Exception as e:
            log_warn(f'读取在线状态失败: {e.__class__.__name__} {e}')
            return False, None, None, ''

    def login_page_has_checkcode(self) -> bool:
        """检查登录页是否启用了验证码（启用则接口式无法全自动）"""
        try:
            r = self.session.get(self._url('login.jsp'), timeout=self.timeout,
                                 allow_redirects=True)
            return bool(RE_FORM_CHECKCODE.search(decode(r)))
        except Exception:
            return False

    # ---- 动作
    def logoff(self) -> bool:
        try:
            r = self.session.get(self._url('logoff'), timeout=self.timeout, allow_redirects=True)
            log_info(f'已请求断开连接 (HTTP {r.status_code})')
            return r.status_code == 200
        except Exception as e:
            log_warn(f'断开连接失败: {e.__class__.__name__} {e}')
            return False

    def do_login(self) -> bool:
        data = {
            'sourceurl': 'null',
            'account': self.account,
            'password': self.password,
            'yys': self.provider,
            'issave': '',
        }
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': self._url('login.jsp'),
            'Origin': self.base_url.rsplit('/snnuportal', 1)[0],
            'Upgrade-Insecure-Requests': '1',
        }
        try:
            r = self.session.post(self._url('login'), data=data, headers=headers,
                                  timeout=self.timeout, allow_redirects=True)
            text = decode(r)
            log_info(f'登录请求已提交: HTTP {r.status_code} -> {r.url}')
            # 抓服务器返回的提示文案（若有）
            alerts = RE_ALERT.findall(text)
            if alerts:
                log_warn(f'服务器提示: {alerts[0][:120]}')
            for hint in ERROR_HINTS:
                if hint in text and '当前登录账号' not in text:
                    log_warn(f'页面出现可疑提示: {hint}')
                    break
            return True
        except Exception as e:
            log_error(f'提交登录表单失败: {e.__class__.__name__} {e}')
            return False

    # ---- 主流程
    def ensure_online(self) -> bool:
        online, acc, ts, _ = self.get_status()
        if online:
            log_info(f'已在线: 账号={acc} 登录时间={ts}')
            if acc and self.account not in acc:
                log_warn(f'在线账号 {acc} 与配置账号 {self.account} 不一致，仍不重复登录')
            return True

        log_info('当前未认证，准备提交登录表单')
        if self.login_page_has_checkcode():
            log_warn('登录页启用了验证码，接口式登录可能失败（需改用浏览器版）')

        for i in range(1, self.retries + 1):
            log_info(f'第 {i}/{self.retries} 次尝试登录 (账号={self.account}@'
                     f'{self.provider or "校园网"})')
            if self.do_login():
                # 给服务器 1~2 秒结算
                time.sleep(1.5)
                online, acc, ts, _ = self.get_status()
                if online:
                    log_info(f'登录成功: 账号={acc} 登录时间={ts}')
                    return True
                log_warn(f'第 {i} 次尝试后仍未检测到在线')
            if i < self.retries:
                time.sleep(2)

        log_error('登录失败：重试已用尽')
        return False


def _pause_if_needed(args) -> None:
    """
    exe 双击运行时，控制台会在结束瞬间关闭，用户来不及看结果。
    这里在冻结（PyInstaller 打包）且非后台模式下等待一次回车。
    """
    if not getattr(sys, 'frozen', False) or args.no_pause:
        return
    try:
        print()
        print('按回车键关闭窗口...', end='', flush=True)
        input()
    except Exception:
        pass


# ---------------------------------------------------------------- 入口
def main() -> int:
    ap = argparse.ArgumentParser(description='陕Star校园网登录（接口版）')
    _add_arguments(ap)
    args = ap.parse_args()
    code = run(args)
    _pause_if_needed(args)
    return code


def _add_arguments(ap: argparse.ArgumentParser):
    ap.add_argument('--status', action='store_true', help='只查询在线状态，不登录')
    ap.add_argument('--logoff', action='store_true', help='主动断开连接')
    ap.add_argument('--test-cycle', action='store_true',
                    help='端到端自检：断开后重新登录（会真实断网数秒）')
    ap.add_argument('--no-fallback', action='store_true', help='（已废弃，保留兼容）')
    ap.add_argument('--no-wifi', action='store_true', help='本次不干预 Wi-Fi')
    ap.add_argument('--wifi-info', action='store_true', help='只读：显示 Wi-Fi 状态后退出')
    ap.add_argument('--setup', action='store_true', help='重新运行配置向导（重填账号密码）')
    ap.add_argument('--no-pause', action='store_true', help='exe 双击运行时结束不暂停')
    ap.add_argument('--watch', action='store_true', help='常驻保活模式：掉线自动重连')
    ap.add_argument('--interval', type=int, default=15, help='保活检测间隔秒数，默认 15')
    ap.add_argument('--wait', type=int, default=10, help='等待校园网出现的分钟数，默认 10')
    return ap


def run(args) -> int:
    trim_log_file()
    log_info('=' * 50)

    if args.setup:
        return 0 if interactive_setup() else 1

    # --watch 常驻保活模式
    if args.watch:
        return run_watch(args)

    cfg = load_config()
    client = PortalClient(cfg)
    wifi = build_wifi_controller(cfg) if not args.no_wifi else None

    if args.wifi_info:
        if not wifi:
            wifi = WifiController(['SNNU'])
        cur = wifi.current_ssid()
        log_info(f'当前 Wi-Fi: {cur or "(未连接)"} | 校园网 Wi-Fi: {wifi.is_campus_wifi(cur)}'
                 f' | 有线连接: {wifi.wired_connected()}')
        log_info(f'可见网络: {wifi.visible_networks()}')
        log_info(f'已保存配置: {wifi.saved_profiles()}')
        return 0

    # Wi-Fi 兜底：只有门户不可达（本来就没网/没连校园网）时才会去连 Wi-Fi
    if wifi:
        wifi.ensure_campus_wifi(client.portal_reachable)

    if not client.portal_reachable():
        log_warn('门户不可达，可能未连接校园网，跳过')
        return 1

    if args.status:
        online, acc, ts, _ = client.get_status()
        if online:
            log_info(f'状态: 在线 | 账号={acc} | 登录时间={ts}')
            return 0
        log_info('状态: 未认证（离线）')
        return 3

    if args.logoff:
        client.logoff()
        time.sleep(1)
        online, _, _, _ = client.get_status()
        log_info(f'断开后状态: {"仍在线" if online else "已离线"}')
        return 0 if not online else 1

    if args.test_cycle:
        log_info('--- 自检模式：断开 -> 重连 ---')
        client.logoff()
        time.sleep(2)
        online, _, _, _ = client.get_status()
        if online:
            log_warn('断开失败，仍显示在线，自检中止（避免影响当前网络）')
            return 1
        log_info('已确认离线，开始重连')
        ok = client.ensure_online()
        log_info(f'自检结果: {"通过" if ok else "失败"}')
        return 0 if ok else 1

    ok = client.ensure_online()
    log_info('任务完成' if ok else '任务失败')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

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
    python auto_login_api.py --no-fallback # 接口失败时不回退浏览器版

接口失败时默认自动回退到 auto_login_selenium.py（可在 config.ini 关掉）。
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
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        log_error(f'未找到配置文件 {CONFIG_FILE}')
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


class PortalClient:
    def __init__(self, cfg: configparser.ConfigParser):
        self.account = cfg.get('User', 'account').strip()
        self.password = cfg.get('User', 'password')
        self.provider = cfg.get('Network', 'provider', fallback='').strip().lower()
        self.base_url = cfg.get('Network', 'base_url',
                                fallback='http://202.117.144.205:8602/snnuportal').rstrip('/')
        self.timeout = cfg.getint('Settings', 'timeout', fallback=8)
        self.retries = cfg.getint('Settings', 'retries', fallback=3)
        self.fallback_selenium = cfg.getboolean('Settings', 'fallback_selenium', fallback=True)

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

    def try_selenium_fallback(self) -> bool:
        """接口登录失败时的兜底：改用浏览器版登录"""
        script = os.path.join(SCRIPT_DIR, 'auto_login_selenium.py')
        if not os.path.exists(script):
            log_warn('未找到 auto_login_selenium.py，无法回退')
            return False
        log_info('接口登录失败，回退到浏览器版 (Selenium) ...')
        try:
            r = subprocess.run([sys.executable, script], cwd=SCRIPT_DIR,
                               timeout=180, capture_output=True, text=True)
            for line in (r.stdout or '').strip().splitlines()[-3:]:
                log_info('[selenium] ' + line.strip())
            ok = (r.returncode == 0)
            log_info(f'浏览器版回退结果: {"成功" if ok else "失败"}')
            return ok
        except Exception as e:
            log_error(f'回退浏览器版异常: {e.__class__.__name__} {e}')
            return False


# ---------------------------------------------------------------- 入口
def main() -> int:
    ap = argparse.ArgumentParser(description='陕Star校园网登录（接口版）')
    ap.add_argument('--status', action='store_true', help='只查询在线状态，不登录')
    ap.add_argument('--logoff', action='store_true', help='主动断开连接')
    ap.add_argument('--test-cycle', action='store_true',
                    help='端到端自检：断开后重新登录（会真实断网数秒）')
    ap.add_argument('--no-fallback', action='store_true', help='登录失败时不回退浏览器版')
    ap.add_argument('--no-wifi', action='store_true', help='本次不干预 Wi-Fi')
    ap.add_argument('--wifi-info', action='store_true', help='只读：显示 Wi-Fi 状态后退出')
    args = ap.parse_args()

    trim_log_file()
    log_info('=' * 50)

    cfg = load_config()
    client = PortalClient(cfg)
    wifi = build_wifi_controller(cfg) if not args.no_wifi else None

    if args.wifi_info:
        if not wifi:
            wifi = WifiController(['SNNU'])
        cur = wifi.current_ssid()
        log_info(f'当前 Wi-Fi: {cur or "(未连接)"} | 校园网 Wi-Fi: {wifi.is_campus_wifi(cur)}')
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
        if not ok and client.fallback_selenium and not args.no_fallback:
            log_warn('接口重连失败，立即回退浏览器版恢复网络')
            ok = client.try_selenium_fallback()
        log_info(f'自检结果: {"通过" if ok else "失败"}')
        return 0 if ok else 1

    ok = client.ensure_online()
    if not ok and client.fallback_selenium and not args.no_fallback:
        ok = client.try_selenium_fallback()
    log_info('任务完成' if ok else '任务失败')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

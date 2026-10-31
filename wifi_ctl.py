#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows Wi-Fi 控制封装（基于 netsh wlan，无需额外依赖）

只封装了"查询"和"连接"两类能力，脚本本身不会主动断开任何网络。
可单独运行查看 Wi-Fi 状态（只读，不改变网络）：
    python wifi_ctl.py --info
"""

import os
import re
import subprocess
import sys
import time
from datetime import datetime

# 打包成 exe 后 __file__ 指向临时解包目录，改用 exe 所在目录定位日志
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, 'auto_login.log')

# netsh 在中文 Windows 上输出 UTF-8；为兼容其他语言环境做多编码尝试
ENCODINGS = ('utf-8', 'gb18030', 'mbcs')

# 状态关键字（中英文都认）
CONNECTED_WORDS = ('已连接', 'connected')
DISCONNECTED_WORDS = ('已断开', '未连接', 'disconnected')
WIRED_WORDS = ('以太网', 'ethernet')
# 虚拟/伪以太网类接口名关键字：这些接口「已连接」不代表真有线在线，误判会让 Wi-Fi 兜底静默失效
VIRTUAL_WORDS = ('vethernet', 'virtual', 'vmware', 'hyper-v', 'wsl', 'loopback', 'bluetooth', 'tap')
SUCCESS_WORDS = ('已成功完成', '成功完成', 'successfully', 'completed')

RE_KV = re.compile(r'^\s*([^\s:][^:]*?)\s*:\s*(.*?)\s*$')
RE_COLS = re.compile(r'\s{2,}')
RE_NET_SSID = re.compile(r'^\s*SSID\s*\d+\s*:\s*(.+?)\s*$')


def log(level: str, msg: str):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {level.upper():>5} - {msg}\n"
    try:
        with open(LOG_FILE, 'a', encoding='utf-8-sig') as f:
            f.write(line)
    except Exception:
        pass
    try:
        print(line.rstrip())
    except Exception:
        pass


def log_info(msg):
    log('INFO', msg)


def log_warn(msg):
    log('WARN', msg)


def log_error(msg):
    log('ERROR', msg)


def decode(raw: bytes) -> str:
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('utf-8', errors='replace')


class WifiController:
    def __init__(self, campus_ssids, interface=None, auto_switch=False,
                 wait_after_connect=20, log=log_info):
        # campus_ssids: 校园网 Wi-Fi 名称列表（按优先级）
        self.campus_ssids = [s.strip() for s in campus_ssids if s and s.strip()]
        self.interface = interface
        self.auto_switch = auto_switch
        self.wait_after_connect = wait_after_connect
        self._log = log

    # ------------------------------------------------------------ 底层
    def _run(self, cmd: str, timeout: int = 20):
        try:
            r = subprocess.run(cmd, capture_output=True, shell=True, timeout=timeout)
            return r.returncode, decode(r.stdout)
        except Exception as e:
            self._log(f'[WiFi] 执行 "{cmd}" 失败: {e.__class__.__name__} {e}')
            return -1, ''

    def _netsh(self, args: str, timeout: int = 20):
        return self._run(f'netsh wlan {args}', timeout)

    def _netsh_sys(self, args: str, timeout: int = 20):
        """执行只读的 `netsh <args>`（非 wlan 子命令）"""
        return self._run(f'netsh {args}', timeout)

    # ------------------------------------------------------------ 查询
    def interfaces(self):
        """解析 `netsh wlan show interfaces`，返回接口信息列表"""
        _, text = self._netsh('show interfaces')
        result, cur = [], None
        for line in text.splitlines():
            if not line.strip():
                continue
            kv = RE_KV.match(line)
            if not kv:
                continue
            key, val = kv.group(1).strip(), kv.group(2).strip()
            low = key.lower()
            # 每个接口块以"名称 / Name"开始
            if low in ('名称', 'name') and cur is not None:
                result.append(cur)
                cur = None
            if cur is None:
                cur = {}
            if low in ('名称', 'name'):
                cur['name'] = val
            elif low == 'ssid':
                cur['ssid'] = val
            elif low in ('状态', 'state'):
                cur['state'] = val
            elif low in ('信号', 'signal'):
                cur['signal'] = val
            elif low in ('配置文件', 'profile'):
                cur['profile'] = val
        if cur:
            result.append(cur)
        # 过滤掉没有实质内容的解析块（例如标题行被误当成接口块）
        return [i for i in result if i.get('name') or i.get('ssid')]

    def current_ssid(self):
        """当前已连接的 SSID；未连接任何 Wi-Fi 时返回 None"""
        for itf in self.interfaces():
            state = (itf.get('state') or '').lower()
            ssid = itf.get('ssid')
            if ssid and any(w in state for w in CONNECTED_WORDS):
                return ssid
        return None

    def is_connected(self):
        return self.current_ssid() is not None

    def wired_connected(self):
        """有线（以太网类接口）是否已连接；只读探测，不做任何改动

        netsh interface show interface 的行形如：
            已启用            已连接            专用               以太网
        列序为 管理状态 / 状态 / 类型 / 接口名，中英文措辞与空白宽度都可能不同。
        """
        _, text = self._netsh_sys('interface show interface')
        for line in text.splitlines():
            line = line.strip()
            if not line or set(line) <= {'-'}:
                continue
            low = line.lower()
            if not any(w in low for w in WIRED_WORDS):
                continue
            # 虚拟/伪以太网（vEthernet、VMware Virtual Ethernet、TAP、环回、蓝牙 PAN 等）一律不算有线
            if any(w in low for w in VIRTUAL_WORDS):
                continue
            cols = [c.strip() for c in RE_COLS.split(line) if c.strip()]
            # 取不到列时退回整行判断
            state = cols[1].lower() if len(cols) >= 3 else low
            # 先判断开：英文 "disconnected" 里含有 "connected"
            if any(w in state for w in DISCONNECTED_WORDS):
                continue
            if any(w in state for w in CONNECTED_WORDS):
                return True
        return False

    def visible_networks(self):
        """当前能搜到的 Wi-Fi 名称列表"""
        _, text = self._netsh('show networks mode=bssid')
        names = []
        for line in text.splitlines():
            m = RE_NET_SSID.match(line)
            if m and m.group(1).strip():
                names.append(m.group(1).strip())
        return names

    def saved_profiles(self):
        """本机已保存的 Wi-Fi 配置名称"""
        _, text = self._netsh('show profiles')
        names = []
        for line in text.splitlines():
            if '配置文件' in line or 'User Profile' in line:
                kv = RE_KV.match(line)
                if kv:
                    name = kv.group(2).strip()
                    # 过滤空值和 "<无>" / "<None>" 这类占位
                    if name and not re.match(r'^<.*>$', name):
                        names.append(name)
        return names

    def is_campus_wifi(self, ssid):
        if not ssid:
            return False
        return ssid.strip().lower() in [s.lower() for s in self.campus_ssids]

    def pick_target(self):
        """在校园网 SSID 列表中挑一个：优先可见且已保存的"""
        visible = self.visible_networks()
        saved = self.saved_profiles()
        vlow = [v.lower() for v in visible]
        slow = [s.lower() for s in saved]
        for ssid in self.campus_ssids:
            if ssid.lower() in vlow:
                return ssid, (ssid.lower() in slow)
        # 都搜不到时，退而求其次：只要本机保存过该配置也尝试连接
        for ssid in self.campus_ssids:
            if ssid.lower() in slow:
                return ssid, True
        return None, False

    # ------------------------------------------------------------ 动作
    def connect(self, ssid):
        """连接指定 SSID（使用本机已保存的配置）。不会断开别的网络之外的操作"""
        args = f'connect name="{ssid}" ssid="{ssid}"'
        if self.interface:
            args += f' interface="{self.interface}"'
        code, text = self._netsh(args)
        ok = any(w in text for w in SUCCESS_WORDS) or code == 0
        brief = ' '.join(text.split())[:120]
        self._log(f'[WiFi] 已发起连接 {ssid} -> {"命令被接受" if ok else "命令返回异常"}: {brief}')
        return ok

    def wait_for_ssid(self, ssid, timeout=20):
        """等待 Wi-Fi 关联到指定 SSID"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.current_ssid() == ssid:
                return True
            time.sleep(2)
        return self.current_ssid() == ssid

    # ------------------------------------------------------------ 编排
    def ensure_campus_wifi(self, portal_ok_fn):
        """
        确保连上校园网 Wi-Fi。
        只在"门户不可达"时才会动作——也就是说只有本来就没网/没认证时才碰 Wi-Fi，
        正在正常上网时绝不干扰。

        portal_ok_fn: 无参函数，返回 True 表示门户可达（已经通网）
        返回 True 表示"Wi-Fi 层面已就绪，可以继续尝试登录"
        """
        if portal_ok_fn():
            self._log('[WiFi] 门户已可达，无需干预 Wi-Fi')
            return True

        # 有线保护：以太网在线时门户不可达多半只是认证/网关抖动，绝不能因此去连 Wi-Fi
        if self.wired_connected():
            self._log('[WiFi] 检测到有线连接，不干预 Wi-Fi，等待门户恢复')
            return True

        cur = self.current_ssid()
        if self.is_campus_wifi(cur):
            self._log(f'[WiFi] 已连校园网 Wi-Fi "{cur}"，但门户不可达，等待网络就绪')
            time.sleep(3)
            return portal_ok_fn()

        if cur and not self.auto_switch:
            self._log(f'[WiFi] 当前连的是 "{cur}"，未开启自动切换（auto_switch=false），不干预')
            return False

        target, saved = self.pick_target()
        if not target:
            self._log('[WiFi] 未找到可用的校园网 Wi-Fi（既没搜到也没保存过配置）')
            return False
        if not saved:
            self._log(f'[WiFi] 搜到 "{target}" 但本机没有保存过它的配置，无法自动连接')

        self._log(f'[WiFi] 尝试连接校园网 Wi-Fi: {target}')
        self.connect(target)
        if not self.wait_for_ssid(target, timeout=15):
            self._log(f'[WiFi] 15 秒内未关联到 {target}，继续按超时策略处理')

        # 等 DHCP 拿到 IP 并能访问门户
        deadline = time.time() + self.wait_after_connect
        while time.time() < deadline:
            if portal_ok_fn():
                self._log(f'[WiFi] 校园网 Wi-Fi 已就绪: {target}')
                return True
            time.sleep(2)
        self._log(f'[WiFi] 连接后 {self.wait_after_connect} 秒门户仍不可达')
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Wi-Fi 状态查看与控制')
    ap.add_argument('--info', action='store_true', help='只读：显示当前 Wi-Fi / 可见网络 / 已保存配置')
    ap.add_argument('--ssid', default='SNNU', help='校园网 Wi-Fi 名称，多个用逗号分隔')
    args = ap.parse_args()

    ctl = WifiController([s.strip() for s in args.ssid.split(',')])
    cur = ctl.current_ssid()
    log_info(f'当前 Wi-Fi: {cur or "(未连接)"}')
    log_info(f'是否校园网 Wi-Fi: {ctl.is_campus_wifi(cur)}')
    log_info(f'有线连接: {ctl.wired_connected()}')
    log_info(f'可见网络: {ctl.visible_networks()}')
    log_info(f'已保存配置: {ctl.saved_profiles()}')
    log_info(f'接口详情: {ctl.interfaces()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

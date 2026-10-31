#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
陕Star 校园网登录 - 持续监听版（接口式，无浏览器）

用法：
    python auto_login_watch.py                 # 等待校园网出现 -> 登录一次 -> 结束
    python auto_login_watch.py --daemon        # 常驻后台：掉线自动重连
    python auto_login_watch.py --wait 10 --interval 15
"""

import argparse
import sys
import time

from auto_login_api import (PortalClient, build_wifi_controller, load_config,
                            log_info, log_warn, log_error, trim_log_file)


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


def main() -> int:
    ap = argparse.ArgumentParser(description='陕Star校园网登录 - 持续监听版（接口式）')
    ap.add_argument('--daemon', action='store_true', help='常驻模式：掉线自动重连')
    ap.add_argument('--wait', type=int, default=10, help='等待校园网出现的分钟数，默认 10')
    ap.add_argument('--interval', type=int, default=15, help='检测间隔秒数，默认 15')
    ap.add_argument('--no-wifi', action='store_true', help='不干预 Wi-Fi')
    args = ap.parse_args()

    trim_log_file()
    log_info('=' * 50)
    log_info('校园网自动登录 - 持续监听模式（接口版）')

    cfg = load_config()
    client = PortalClient(cfg)
    wifi = build_wifi_controller(cfg) if not args.no_wifi else None

    if not wait_for_network(client, wifi, args.wait, args.interval):
        return 1

    ok = client.ensure_online()
    if not ok and client.fallback_selenium:
        ok = client.try_selenium_fallback()

    if not args.daemon:
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


if __name__ == '__main__':
    sys.exit(main())

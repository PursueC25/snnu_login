#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
陕Star 校园网登录 - 持续监听版（接口式，无浏览器）

用法：
    python auto_login_watch.py                 # 等待校园网出现 -> 登录一次 -> 结束
    python auto_login_watch.py --daemon        # 常驻后台：掉线自动重连
    python auto_login_watch.py --wait 10 --interval 15

此文件为薄委托层，所有核心逻辑已下沉到 auto_login_api.py。
"""

import argparse
import sys

from auto_login_api import run_watch


def main() -> int:
    ap = argparse.ArgumentParser(description='陕Star校园网登录 - 持续监听版（接口式）')
    ap.add_argument('--daemon', action='store_true', help='常驻模式：掉线自动重连')
    ap.add_argument('--wait', type=int, default=10, help='等待校园网出现的分钟数，默认 10')
    ap.add_argument('--interval', type=int, default=15, help='检测间隔秒数，默认 15')
    ap.add_argument('--no-wifi', action='store_true', help='不干预 Wi-Fi')
    args = ap.parse_args()

    return run_watch(args)


if __name__ == '__main__':
    sys.exit(main())

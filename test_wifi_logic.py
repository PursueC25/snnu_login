#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wi-Fi 逻辑分支自检：全部打桩，绝不执行真实 netsh connect / disconnect"""
import wifi_ctl

CASES = [
    # (名称, 当前SSID, 门户可达, auto_switch, 期望是否发起连接)
    ('门户可达（正常上网）', 'SNNU', True, False, False),
    ('未连任何WiFi + 门户不通', None, False, False, True),
    ('连着校园网但门户不通', 'SNNU', False, False, False),
    ('连着手机热点 且 不允许切换', 'Redmi K60', False, False, False),
    ('连着手机热点 且 允许切换', 'Redmi K60', False, True, True),
]

fails = 0
for name, cur_ssid, portal_ok, auto_switch, expect_connect in CASES:
    ctl = wifi_ctl.WifiController(['SNNU'], auto_switch=auto_switch, wait_after_connect=4)
    ctl.current_ssid = lambda v=cur_ssid: v
    ctl.visible_networks = lambda: ['SNNU', 'WIFI_5G']
    ctl.saved_profiles = lambda: ['SNNU', 'Redmi K60']
    ctl.wait_for_ssid = lambda ssid, timeout=15: True
    calls = []

    def fake_connect(ssid, _c=calls):
        _c.append(ssid)
        return True
    ctl.connect = fake_connect
    ctl._log = lambda msg: None  # 静音，避免污染日志

    result = ctl.ensure_campus_wifi(lambda v=portal_ok: v)
    did = len(calls) > 0
    ok = (did == expect_connect)
    if not ok:
        fails += 1
    print(f'[{"PASS" if ok else "FAIL"}] {name:<28} 发起连接={did}(期望{expect_connect}) '
          f'目标={calls[0] if calls else "-"} 返回={result}')

print()
print('全部通过' if fails == 0 else f'有 {fails} 个用例未通过')
raise SystemExit(1 if fails else 0)

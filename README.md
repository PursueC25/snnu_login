# SNNU 校园网自动登录工具

陕西师范大学（SNNU）校园网全自动登录工具。**接口式登录 + Wi-Fi 自动连接 + 掉线自愈**，不依赖浏览器，开机后全自动，日常使用无感。

> 适用范围：陕西师范大学校园网（长安校区 / 雁塔校区），门户地址 `202.117.144.205:8601 / 8602`（同一套门户双端口）。

## 特性

| 能力 | 说明 |
|---|---|
| 接口式登录 | 直接 POST 门户登录表单，不启动浏览器，1 秒内完成 |
| 在线状态检测 | 已在线自动跳过，**幂等安全**，重复运行不会踢线 |
| Wi-Fi 自动连接 | 断网后 Wi-Fi 显示"可用但未连接"时，自动通过 `netsh wlan` 连上校园网再认证 |
| 双端口容错 | 8601 / 8602 自动切换，主端口不通不影响使用 |
| 失败重试 + 兜底 | 接口登录失败自动重试，仍失败可选回退浏览器版（Selenium） |
| 常驻保活 | `--daemon` 模式掉线自动重连，配合计划任务实现开机自愈 |
| 日志可查 | 全程写日志，成败原因一目了然，自动精简防止无限膨胀 |
| 配置与代码分离 | 账号密码写在本地 `config.ini`，不污染源码，便于公开分享 |
| 自检脚本 | 内置打桩自检（不碰真实网络），随时验证逻辑分支 |

## 工作原理

"断网"其实是两层问题，本工具按顺序自检修复：

```
第 1 层 · Wi-Fi 未连接（Windows 显示「SNNU 可用，未连接」）
    → netsh wlan connect 连接校园网 Wi-Fi（走系统原生 WLAN 接口）
    → 等待关联 + DHCP 获取 IP（最多 20 秒）

第 2 层 · Wi-Fi 已连但门户未认证
    → GET  userstatus.jsp     检查是否已在线（未认证会被重定向到登录页）
    → POST /snnuportal/login  提交表单（sourceurl / account / password / yys / issave）
    → 复检 userstatus.jsp     出现「当前登录账号」即认证成功
```

几个关键事实：

- 登录表单**明文提交密码**（门户前端无加密），运营商"确认弹窗"只是前端效果，`yys` 字段可直接指定 `mobile` / `unicom` / `telecom`，留空为校园网
- 门户页面编码 GB2312，`netsh wlan` 输出编码 UTF-8，脚本均已处理
- **只在门户不可达（本来就没网）时才碰 Wi-Fi**，正在正常上网时绝不干扰，也绝不会主动断开任何网络

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

接口版只需要 `requests`；`selenium` 与 `webdriver-manager` 仅作浏览器版兜底，可不装。

### 2. 配置账号

复制 `config.example.ini` 并重命名为 `config.ini`，填写：

```ini
[User]
account = 你的学号
password = 你的密码

[Network]
provider = mobile   ; mobile / unicom / telecom / 留空（校园网）
```

### 3. 运行

```bash
python auto_login_api.py        # 检测并登录（已在线就跳过，最常用）
```

或者直接双击 `auto_login.bat`。

## 命令一览

| 命令 | 作用 |
|---|---|
| `python auto_login_api.py` | 检测并登录（日常使用就这一条） |
| `python auto_login_api.py --status` | 只看当前在线状态，不登录 |
| `python auto_login_api.py --wifi-info` | 只读查看 Wi-Fi 状态，不改变任何网络 |
| `python auto_login_api.py --test-cycle` | 端到端自检：断开 → 接口重连（**会真实断网几秒**） |
| `python auto_login_api.py --logoff` | 主动断开连接 |
| `python auto_login_api.py --no-wifi` | 本次运行不干预 Wi-Fi |
| `python auto_login_watch.py` | 等待校园网出现 → 登录一次 → 结束 |
| `python auto_login_watch.py --daemon` | 常驻保活：掉线自动重连（推荐配合计划任务） |
| `python wifi_ctl.py --info` | Wi-Fi 状态详情（只读） |
| `python test_wifi_logic.py` | Wi-Fi 分支逻辑自检（打桩，不碰真实网络） |

## 开机 / 唤醒后自动运行（推荐）

用 Windows 计划任务把 `--daemon` 常驻保活挂到开机：

1. `Win + R` → `taskschd.msc` → 创建任务
2. 触发器：**启动时** 与 **工作站解锁时**（覆盖开机和休眠唤醒）
3. 操作：
   - 程序：`pythonw`（不带窗口的 Python）
   - 参数：`auto_login_watch.py --daemon`
   - 起始于：本工具所在目录

之后开机、休眠唤醒、Wi-Fi 掉线都会自动恢复，全程无感。

## 配置说明（[WiFi] 段）

| 配置项 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否开启 Wi-Fi 自动连接 |
| `ssid` | `SNNU` | 校园网 Wi-Fi 名，多个用英文逗号分隔 |
| `interface` | 空 | 指定网卡名，留空自动识别 |
| `auto_switch` | `false` | 连着别的 Wi-Fi（如手机热点）时是否强制切回校园网 |
| `wait_after_connect` | `20` | 连上后最多等多少秒让它拿到 IP |

## 项目结构

```
├── auto_login_api.py       主程序（接口版，推荐）
├── auto_login_watch.py     持续监听 / 常驻保活（含 Wi-Fi 自愈）
├── wifi_ctl.py             Wi-Fi 查询与控制（netsh wlan 封装）
├── test_wifi_logic.py      Wi-Fi 分支逻辑自检（打桩，不碰真实网络）
├── auto_login_selenium.py  浏览器版登录（仅作接口失败时的兜底）
├── auto_login.bat          双击运行
├── config.example.ini      配置模板（复制为 config.ini 使用）
└── requirements.txt        依赖
```

## 常见问题

**Q: 提示"门户不可达"？**
当前没连上校园网（Wi-Fi 没连，或不在校园网覆盖内）。若已开启 Wi-Fi 自动连接，脚本会尝试替你连上；连不上时检查 Windows 的 Wi-Fi 开关是否被关闭。

**Q: 提示"登录页启用了验证码"？**
门户开启验证码后接口式无法全自动，脚本会自动回退浏览器版。目前默认未开启。

**Q: 登录失败"重试已用尽"？**
检查 `config.ini` 账号密码是否正确、是否欠费或被锁定；日志里会附上门户返回的提示文案。

**Q: 密码安全吗？**
账号密码只保存在本地 `config.ini`（该文件被 `.gitignore` 排除，不会意外上传）；日志只记录执行状态，不记录密码。

**Q: 为什么不动我的手机热点？**
`auto_switch` 默认 `false`：连着别的 Wi-Fi 时脚本不干预，只有设为 `true` 才会在门户不通时切回校园网。

## 兼容性

- Windows 10 / 11，Python 3.8+
- 校园网门户若升级（如开启验证码、更换接口），请提 Issue

## 隐私与安全

- 所有数据（账号、密码、日志）仅存本地，不上传任何第三方服务器
- 本仓库不含任何真实账号信息
- 欢迎用任意 AI 工具审查源码确认安全性

## 免责声明

本工具仅用于自动化个人已获授权的校园网登录流程，不破解、不绕过任何计费或权限机制。请遵守学校网络使用规定，因使用本工具产生的任何后果由使用者自行承担。

## 致谢

- 门户接口细节参考了社区先例 [Lancycool/snnu-portal](https://github.com/Lancycool/snnu-portal)
- 浏览器版兜底方案基于原作者 PURSUEC 的早期 Selenium 实现

---

MIT License © 2026 PursueC25

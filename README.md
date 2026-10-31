# SNNU 校园网自动登录工具

陕西师范大学（SNNU）校园网全自动登录工具。**纯接口登录 + Wi-Fi 自动连接 + 掉线自愈**，不依赖浏览器，首次运行跟着向导填一次，之后开机全自动，日常使用无感。

> 适用范围：陕西师范大学校园网（长安校区 / 雁塔校区），门户地址 `202.117.144.205:8601 / 8602`（同一套门户双端口）。

## 特性

| 能力 | 说明 |
|---|---|
| 纯接口登录 | 直接 POST 门户登录表单，不启动浏览器、不需要任何驱动，1 秒内完成 |
| 首次运行向导 | 第一次运行就有中文向导：学号 → 密码 → 上网方式 →（Windows）是否开机自启，自动生成 `config.ini` |
| 在线状态检测 | 已在线自动跳过，**幂等安全**，重复运行不会踢线 |
| Wi-Fi 自动连接 | 断网后 Wi-Fi 显示"可用但未连接"时，自动通过 `netsh wlan` 连上校园网再认证 |
| 有线保护 | 检测到网线（以太网）在线时**绝不**自动连 / 切换 SNNU Wi-Fi；vEthernet / VMware / Hyper-V / WSL / TAP 等虚拟网卡不算有线 |
| 双端口容错 | 8601 / 8602 自动切换，主端口不通不影响使用 |
| 失败重试 | 登录失败自动重试，日志里同步附上门户返回的提示文案 |
| 常驻保活 | `--watch` 掉线自动重连，配合开机自启实现开机、唤醒后自动恢复 |
| 开机自启 | Windows 在向导里选 `y` 一键注册；Linux 用 `install.sh` 注册 systemd 常驻服务 |
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

纯接口登录，只需要 `requests`；不需要浏览器，也不需要任何浏览器驱动。

### 2. 首次运行（向导）

```bash
python auto_login_api.py        # Windows 也可以直接双击 auto_login.bat 或 snnu_login.exe
```

第一次运行、本目录还没有 `config.ini` 时，会自动进入中文向导，按提示依次输入：

1. 校园网账号（学号）
2. 校园网密码（用 `getpass` 隐藏输入，屏幕上不显示）
3. 上网方式：`1` 移动 / `2` 联通 / `3` 电信 / `4` 校园网（不代拨）
4. Windows 下还会问「是否设置开机自启」（默认 `n`），选 `y` 会在系统「启动」文件夹写入一个快捷方式，开机自动以 `--watch` 常驻保活，掉线自动重连

向导会把信息写进本目录的 `config.ini`（Linux 下自动 `chmod 600`），并直接开始登录。

> 后台 / 计划任务等没有交互终端的环境不会启动向导，而是打印提示让你手动创建 `config.ini`，避免任务卡住。
> 不想走向导也可以手动配置：把 `config.example.ini` 复制为 `config.ini`，填入账号密码。

### 3. 运行

```bash
python auto_login_api.py        # 检测并登录（已在线就跳过，最常用）
```

或者直接双击 `auto_login.bat`（打包版双击 `snnu_login.exe`）。

## 命令一览

| 命令 | 作用 |
|---|---|
| `python auto_login_api.py` | 检测并登录（日常使用就这一条） |
| `python auto_login_api.py --setup` | 重跑首次运行向导，重新填账号密码 / 上网方式 |
| `python auto_login_api.py --watch` | 常驻保活：掉线自动重连（**开机自启用它**） |
| `python auto_login_api.py --status` | 只看当前在线状态，不登录 |
| `python auto_login_api.py --wifi-info` | 只读查看 Wi-Fi 状态，不改变任何网络 |
| `python auto_login_api.py --test-cycle` | 端到端自检：断开 → 接口重连（**会真实断网几秒**） |
| `python auto_login_api.py --logoff` | 主动断开连接 |
| `python auto_login_api.py --no-wifi` | 本次运行不干预 Wi-Fi |
| `python auto_login_api.py --no-pause` | 结束时不等待回车（给脚本 / 计划任务用） |
| `python auto_login_watch.py` | 等待校园网出现 → 登录一次 → 结束 |
| `python auto_login_watch.py --daemon` | 常驻保活（独立脚本版，效果同 `--watch`） |
| `python wifi_ctl.py --info` | Wi-Fi 状态详情（只读） |
| `python test_wifi_logic.py` | Wi-Fi 分支逻辑自检（打桩，不碰真实网络） |

> `--no-fallback` 是历史遗留参数，已废弃但保留兼容（避免老脚本报错），新用法不用管它。

## 常驻保活（--watch）与重配（--setup）

### 常驻保活：掉线自动重连

```bash
python auto_login_api.py --watch          # 推荐写法
python auto_login_watch.py --daemon       # 等价写法（独立脚本）
```

先等校园网出现，然后进入常驻循环：周期性检查在线状态，一旦发现掉线就先确认 Wi-Fi、再自动重新登录，直到你按 `Ctrl + C` 退出。**开机自启注册的就是它**，因此开机、休眠唤醒、Wi-Fi 掉线都能自动恢复，全程无感。

有线保护：如果检测到网线（以太网）在线，工具**不会**去连校园网 Wi-Fi、也不会切换当前 Wi-Fi——有线在线时门户不可达多半只是认证 / 网关抖动，这时候去连 Wi-Fi 属于误动作，它会原地等待门户自己恢复（虚拟网卡如 vEthernet / VMware / TAP 一律不算有线）。

可调参数：`--interval 15`（在线检测间隔秒数，默认 15）、`--wait 10`（启动时最多等多少分钟让校园网出现，默认 10）。

### 修改账号密码

两种办法，任选一种：

```bash
python auto_login_api.py --setup       # 重跑向导，按提示重新输入
```

或者把 `config.ini` 删掉 / 重命名，下次运行时会自动重新进入向导。只想手改某一项（比如换运营商）也可以直接用记事本编辑 `config.ini`。

顺便一提，`--setup` 里也能关掉开机自启：在「是否开机自启」处选 `n`，会自动删除自启项。

## 开机 / 唤醒后自动运行（推荐）

### Windows：向导里选 y

首次运行向导（或 `--setup`）会询问「是否设置开机自启」，选 `y` 即自动在当前用户的「启动」文件夹写入一个快捷方式（运行参数 `--watch --no-pause`），之后开机自动联网、掉线自动重连；选 `n` 则自动删除该快捷方式。想自己动手也可以用计划任务：

1. `Win + R` → `taskschd.msc` → 创建任务
2. 触发器：**启动时** 与 **工作站解锁时**（覆盖开机和休眠唤醒）
3. 操作：
   - 程序：`snnu_login.exe` 的完整路径（源码运行则填 `pythonw`）
   - 参数：`--watch --no-pause`（`--no-pause` 不能省，否则窗口会停在「按回车键关闭窗口」不动；源码运行则填 `auto_login_watch.py --daemon`）
   - 起始于：本工具所在目录

### Linux：install.sh

```bash
bash install.sh --account 学号 --password 密码 --autostart
```

脚本会自动准备 Python 环境（创建独立 venv，不污染系统）、生成 `config.ini`、生成启动器，并在 `--autostart` 时注册 systemd 用户服务常驻保活。没有 `--autostart` 时也能事后手动加：

```bash
systemctl --user enable --now snnu-login.service
```

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
├── auto_login_api.py       主程序（纯接口登录 + 首次运行向导 + --watch 常驻保活）
├── auto_login_watch.py     持续监听 / 常驻保活（含 Wi-Fi 自愈）
├── wifi_ctl.py             Wi-Fi 查询与控制（netsh wlan 封装）
├── test_wifi_logic.py      Wi-Fi 分支逻辑自检（打桩，不碰真实网络）
├── auto_login.bat          Windows 双击运行
├── install.sh              Linux 一键安装（venv + systemd 常驻自启）
├── config.example.ini      配置模板（复制为 config.ini；向导会自动生成）
├── requirements.txt        依赖（只有 requests）
└── LICENSE
```

## 常见问题

**Q: 提示"门户不可达"？**
当前没连上校园网（Wi-Fi 没连，或不在校园网覆盖内）。若已开启 Wi-Fi 自动连接，脚本会尝试替你连上；连不上时检查 Windows 的 Wi-Fi 开关是否被关闭。

**Q: 首次运行向导里输错了怎么办？**
重新跑一次 `python auto_login_api.py --setup`，或直接删掉 `config.ini` 再运行。

**Q: 怎么取消开机自启？**
再跑一次 `python auto_login_api.py --setup`，在「是否开机自启」处选 `n`，会自动删除启动项（也可以手动删掉「启动」文件夹里的快捷方式）。

**Q: 提示"登录页启用了验证码"？**
门户一旦开启验证码，接口式就无法全自动，需要自己打开登录页手动登一次。目前默认未开启。

**Q: 登录失败"重试已用尽"？**
检查 `config.ini` 账号密码是否正确（可用 `--setup` 重填）、是否欠费或被锁定；日志里会附上门户返回的提示文案。

**Q: 密码安全吗？**
账号密码只保存在本地 `config.ini`（该文件被 `.gitignore` 排除，不会意外上传）；日志只记录执行状态，不记录密码。

**Q: 为什么不动我的手机热点？**
`auto_switch` 默认 `false`：连着别的 Wi-Fi 时脚本不干预，只有设为 `true` 才会在门户不通时切回校园网。

## 兼容性

- Windows 10 / 11，Python 3.8+
- Linux（systemd 发行版）可用 `install.sh` 一键安装 + 常驻自启
- 校园网门户若升级（如开启验证码、更换接口），请提 Issue

## 隐私与安全

- 所有数据（账号、密码、日志）仅存本地，不上传任何第三方服务器
- 本仓库不含任何真实账号信息
- 欢迎用任意 AI 工具审查源码确认安全性

## 免责声明

本工具仅用于自动化个人已获授权的校园网登录流程，不破解、不绕过任何计费或权限机制。请遵守学校网络使用规定，因使用本工具产生的任何后果由使用者自行承担。

## 致谢

- 门户接口细节参考了社区先例 [Lancycool/snnu-portal](https://github.com/Lancycool/snnu-portal)

---

MIT License © 2026 PursueC25

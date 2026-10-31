#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校园网自动登录脚本 - Selenium 浏览器版
功能：在开机和休眠结束时自动连接校园网（模拟人工操作，抗检测）
"""

import time
import os
import configparser
import subprocess
import sys
from datetime import datetime, timedelta

# Selenium 相关
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from webdriver_manager.microsoft import EdgeChromiumDriverManager
from webdriver_manager.chrome import ChromeDriverManager

# 配置日志
LOG_DIR = os.path.dirname(__file__)
LOG_FILE = os.path.join(LOG_DIR, 'auto_login.log')

# 配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.ini')


def write_log(level: str, message: str):
    """
    直接写入日志文件，使用 UTF-8 BOM 编码（兼容 Windows 记事本）
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{timestamp} - {level.upper():>5} - {message}\n"
    try:
        with open(LOG_FILE, 'a', encoding='utf-8-sig') as f:
            f.write(line)
    except Exception:
        pass


def log_info(msg):
    write_log('INFO', msg)


def log_warn(msg):
    write_log('WARN', msg)


def log_error(msg):
    write_log('ERROR', msg)


def cleanup_old_logs(log_file: str, days: int = 30):
    """清理旧日志文件，只保留指定天数内的日志"""
    try:
        if not os.path.exists(log_file):
            return
        file_mtime = datetime.fromtimestamp(os.path.getmtime(log_file))
        cutoff_date = datetime.now() - timedelta(days=days)
        if file_mtime < cutoff_date:
            backup_file = log_file + '.bak'
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()
            with open(backup_file, 'w', encoding='utf-8') as f:
                f.write(content)
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('')
            log_info(f'日志已清理，备份到 {backup_file}')
    except Exception:
        pass


def trim_log_file(log_file: str, max_lines: int = 5000):
    """精简日志文件，只保留最后 max_lines 行"""
    try:
        if not os.path.exists(log_file):
            return
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.writelines(lines[-max_lines:])
            log_info(f'日志已精简，保留最后 {max_lines} 行')
    except Exception:
        pass


def get_browser_options(headless: bool = True, browser: str = 'edge'):
    """获取浏览器配置"""
    if browser == 'edge':
        options = EdgeOptions()
        prefs_key = "profile.default_content_setting_values"
    else:
        options = ChromeOptions()
        prefs_key = "profile.managed_default_content_settings"

    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,720')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    )
    prefs = {prefs_key: {"images": 2}}
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    return options


def get_driver(headless: bool = True, browser: str = 'edge'):
    """获取浏览器 WebDriver"""
    script_dir = os.path.dirname(os.path.abspath(__file__))

    def try_local_driver(driver_exe_name, browser_type):
        local_path = os.path.join(script_dir, driver_exe_name)
        if os.path.exists(local_path):
            log_info(f"使用本地 Driver: {local_path}")
            try:
                service = (EdgeService(local_path, args=['--silent-launcher']) if browser_type == 'edge'
                           else ChromeService(local_path))
                options = get_browser_options(headless, browser_type)
                return webdriver.Edge(service=service, options=options) if browser_type == 'edge' \
                       else webdriver.Chrome(service=service, options=options)
            except Exception as e:
                log_warn(f"本地 Driver 版本不匹配或启动失败: {e}")
                log_warn("将尝试自动下载匹配的 Driver...")
                return None
        return None

    try:
        if browser == 'edge':
            driver = try_local_driver('msedgedriver.exe', 'edge')
            if driver:
                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                })
                log_info("Edge WebDriver (本地) 初始化成功")
                return driver
            service = EdgeService(EdgeChromiumDriverManager().install(), args=['--silent-launcher'])
            options = get_browser_options(headless, 'edge')
            driver = webdriver.Edge(service=service, options=options)
        else:
            driver = try_local_driver('chromedriver.exe', 'chrome')
            if driver:
                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                })
                log_info("Chrome WebDriver (本地) 初始化成功")
                return driver
            service = ChromeService(ChromeDriverManager().install())
            options = get_browser_options(headless, 'chrome')
            driver = webdriver.Chrome(service=service, options=options)

        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
        log_info(f"{browser.capitalize()} WebDriver 初始化成功")
        return driver
    except Exception as e:
        log_error(f"WebDriver 初始化失败: {e}")
        log_error("解决方案：请访问 https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/")
        log_error("下载与你 Edge 浏览器版本 (149) 完全一致的 msedgedriver.exe，替换项目目录下的旧文件")
        return None


class CampusNetworkLogin:
    def __init__(self):
        self.config = self.load_config()
        self.base_url = self.config.get('Network', 'base_url', fallback='http://202.117.144.205:8602/snnuportal')
        self.account = self.config.get('User', 'account')
        self.password = self.config.get('User', 'password')
        self.provider = self.config.get('Network', 'provider', fallback='')
        self.headless = self.config.getboolean('Settings', 'headless', fallback=True)
        self.browser = self.config.get('Settings', 'browser', fallback='edge')
        self.log_cleanup_days = self.config.getint('Settings', 'log_cleanup_days', fallback=30)
        trim_log_file(LOG_FILE, max_lines=5000)

    def load_config(self):
        """加载配置文件"""
        config = configparser.ConfigParser()

        if not os.path.exists(CONFIG_FILE):
            config['User'] = {
                'account': 'your_account',
                'password': 'your_password'
            }
            config['Network'] = {
                'base_url': 'http://202.117.144.205:8602/snnuportal',
                'provider': ''
            }
            config['Settings'] = {
                'headless': 'true',
                'browser': 'edge',
                'log_cleanup_days': '30'
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config.write(f)
            log_warn('配置文件不存在，已创建默认配置文件，请修改config.ini填写正确的账号密码')
        else:
            config.read(CONFIG_FILE, encoding='utf-8')

        return config

    def get_local_ip(self):
        """获取本机IP地址，并记录完整调试信息"""
        try:
            result = subprocess.run(
                ['ipconfig'], capture_output=True, text=True, timeout=5,
                encoding='gbk', errors='replace'
            )
            stdout = result.stdout
            log_info(f'---- ipconfig 原始输出 ({len(stdout)} 字节) ----')
            # 只记录关键行，避免日志太长
            for line in stdout.split('\n'):
                if 'IPv4' in line or 'v4' in line.lower() or '地址' in line:
                    log_info(f'  >> {line.strip()}')
            log_info('---- ipconfig 结束 ----')

            for line in stdout.split('\n'):
                if 'IPv4' in line:
                    ip = line.split(':')[-1].strip()
                    log_info(f'解析到IP: {ip}')
                    return ip
            return None
        except Exception as e:
            log_error(f'执行 ipconfig 失败: {e}')
            return None

    def is_campus_network(self):
        """检测是否连接了校园网（双重检测：ipconfig + HTTP 探测）"""
        # 方法一：通过 ipconfig 检测校园网 IP
        local_ip = self.get_local_ip()
        if local_ip:
            campus_prefixes = ['10.139.', '10.191.', '10.168.',
                               '172.16.', '172.17.', '172.18.', '172.19.', '172.20.']
            for prefix in campus_prefixes:
                if local_ip.startswith(prefix):
                    log_info(f'检测到校园网，IP地址: {local_ip}')
                    return True
            log_info(f'本机IP: {local_ip}，不在校园网段，尝试HTTP探测...')
        else:
            log_info('未能获取本机IP，尝试HTTP探测校园网...')

        # 方法二：直接 HTTP 访问校园网登录页面，能连上就是校园网
        import requests
        test_urls = [
            self.base_url,
            'http://202.117.144.205:8602/',
            'http://10.139.8.1/',
        ]
        for url in test_urls:
            try:
                resp = requests.get(url, timeout=3,
                                    headers={'User-Agent': 'Mozilla/5.0'},
                                    allow_redirects=True)
                if resp.status_code == 200:
                    log_info(f'HTTP探测成功，可访问校园网: {url}')
                    return True
                else:
                    log_info(f'HTTP探测 {url} 返回状态码: {resp.status_code}')
            except requests.ConnectionError:
                log_info(f'HTTP探测 {url} 无法连接')
            except Exception as e:
                log_info(f'HTTP探测 {url} 异常: {e}')

        log_warn('双重检测均未确认校园网环境，跳过登录')
        return False

    def check_network(self):
        """检查网络是否已连接（能访问外网）"""
        import requests
        try:
            response = requests.get(
                'http://www.baidu.com', timeout=5,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                allow_redirects=False
            )
            return response.status_code == 200
        except:
            return False

    def login(self):
        """使用 Selenium 执行登录操作"""
        log_info('=' * 50)
        log_info('开始执行校园网自动登录 (Selenium 浏览器版)')

        # 检测是否在校园网环境
        if not self.is_campus_network():
            log_info('不在校园网环境，跳过登录')
            return True

        # 检测网络是否已连接
        if self.check_network():
            try:
                import requests as req
                resp = req.get(
                    f'{self.base_url}/userstatus.jsp',
                    timeout=5,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                if '当前登录账号' in resp.text or 'userstatus' in resp.url:
                    log_info('校园网已登录在线')
                    return True
            except:
                pass
            log_info('网络已连接，但可能未进行校园网认证，尝试登录...')

        # 启动浏览器
        driver = get_driver(headless=self.headless, browser=self.browser)
        if not driver:
            log_error('无法启动浏览器，登录失败')
            return False

        try:
            login_url = f'{self.base_url}/login'
            log_info(f'打开登录页面: {login_url}')
            driver.get(login_url)
            time.sleep(4)

            # 截图1
            driver.save_screenshot(os.path.join(LOG_DIR, '01_before_login.png'))
            log_info('截图已保存: 01_before_login.png')

            # 检查账号密码
            account_val = driver.execute_script('return document.querySelector(\'input[name="account"]\').value || "";')
            password_val = driver.execute_script('return document.querySelector(\'input[name="password"]\').value || "";')
            log_info(f'当前账号: {account_val}, 密码: {"有" if password_val else "无"}')

            if not account_val:
                driver.execute_script(f'document.querySelector(\'input[name="account"]\').value = "{self.account}";')
            if not password_val:
                driver.execute_script(f'document.querySelector(\'input[name="password"]\').value = "{self.password}";')

            # 截图2
            driver.save_screenshot(os.path.join(LOG_DIR, '02_after_fill.png'))
            log_info('截图已保存: 02_after_fill.png')

            time.sleep(0.5)

            use_daibo = self.provider and self.provider.strip() != ''

            if use_daibo:
                log_info(f'需要代拨，选择运营商: {self.provider}')
                provider_map = {
                    'unicom': 'n1',
                    'mobile': 'n2'
                }
                target_radio = provider_map.get(self.provider.lower(), 'n2')

                driver.execute_script('''
                    var radios = document.querySelectorAll('input[name="yys"]');
                    radios.forEach(function(radio) { radio.checked = false; });
                    var target = document.getElementById(arguments[0]);
                    if (target) {
                        target.checked = true;
                        target.click();
                        target.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                ''', target_radio)
                time.sleep(0.5)

                # 截图3
                driver.save_screenshot(os.path.join(LOG_DIR, '03_after_provider.png'))
                log_info('截图已保存: 03_after_provider.png')

                checked_id = driver.execute_script("var checked = document.querySelector('input[name=\"yys\"]:checked'); return checked ? checked.id : 'none';")
                log_info(f'当前选中: {checked_id}')
            else:
                log_info('使用校园网（无需代拨）')

            # 点击登录按钮
            clicked = False

            try:
                btns = driver.find_elements(By.CSS_SELECTOR, '.btn-login, a.btn-login, button.btn-login')
                for btn in btns:
                    if btn.is_displayed():
                        driver.execute_script('arguments[0].click();', btn)
                        clicked = True
                        log_info('点击登录 (btn-login)')
                        break
            except:
                pass

            if not clicked:
                try:
                    btn = driver.find_element(By.XPATH, '//*[contains(@onclick, "login")]')
                    driver.execute_script('arguments[0].click();', btn)
                    clicked = True
                    log_info('点击登录 (onclick)')
                except:
                    pass

            if not clicked:
                try:
                    btns = driver.find_elements(By.TAG_NAME, 'button')
                    for btn in btns:
                        if btn.is_displayed() and btn.textContent.strip():
                            driver.execute_script('arguments[0].click();', btn)
                            clicked = True
                            log_info('点击登录 (button)')
                            break
                except:
                    pass

            if not clicked:
                log_error('找不到登录按钮')
                return False

            # 截图4
            driver.save_screenshot(os.path.join(LOG_DIR, '04_after_click.png'))
            log_info('截图已保存: 04_after_click.png')

            if use_daibo:
                log_info('等待确认弹窗...')
                time.sleep(2)
                driver.save_screenshot(os.path.join(LOG_DIR, '05_popup.png'))
                log_info('截图已保存: 05_popup.png')

                for attempt in range(3):
                    popup_exists = driver.execute_script('''
                        var popup = document.querySelector('.sweet-alert');
                        return popup ? (popup.style.display !== 'none') : false;
                    ''')
                    if not popup_exists:
                        log_info('弹窗已消失')
                        break
                    log_info(f'点击确认按钮 (第{attempt+1}次)')
                    driver.execute_script('''
                        var btn = document.querySelector('.sweet-alert button.confirm');
                        if (btn) {
                            btn.click();
                            btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        }
                    ''')
                    time.sleep(1)
                time.sleep(3)
            else:
                log_info('使用校园网，等待登录...')
                time.sleep(3)

            # 截图6
            driver.save_screenshot(os.path.join(LOG_DIR, '06_result.png'))
            log_info('截图已保存: 06_result.png')

            # 检查结果
            try:
                current_url = driver.current_url
                page_source = driver.page_source

                if 'userstatus' in current_url:
                    log_info('登录成功！')
                    return True
                elif '当前登录' in page_source:
                    log_info('登录成功！')
                    return True
                elif '密码' in page_source and '错误' in page_source:
                    log_error('密码错误')
                    return False
                else:
                    log_warn(f'登录结果未知，URL: {current_url}')
                    return False
            except Exception as e:
                log_error(f'检查结果失败: {e}')
                return False

        except Exception as e:
            log_error(f'登录过程出错: {str(e)}')
            try:
                driver.save_screenshot(os.path.join(LOG_DIR, '07_error.png'))
                log_info('错误截图已保存: 07_error.png')
            except:
                pass
            return False

        finally:
            try:
                driver.quit()
                log_info('浏览器已关闭')
            except:
                pass


def main():
    """主函数"""
    login_client = CampusNetworkLogin()
    success = login_client.login()

    if success:
        log_info('校园网自动登录任务完成')
    else:
        log_error('校园网自动登录任务失败')

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())

import os
import time
import logging
import random
import requests
import zipfile
import subprocess
import shutil
import sys
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from dotenv import load_dotenv
from pathlib import Path
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor, as_completed

import colorama
from colorama import Fore, Style

# Инициализация colorama для цветного логирования
colorama.init(autoreset=True)

# Кастомный форматтер для логов с цветами
class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT
    }
    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        message = super().format(record)
        return color + message + Style.RESET_ALL

# Флаг для headless-режима. Для отладки можно поставить False.
HEADLESS = True

banner = r"""
 _   _           _  _____      
| \ | |         | ||____ |     
|  \| | ___   __| |    / /_ __ 
| . ` |/ _ \ / _` |    \ \ '__|
| |\  | (_) | (_| |.___/ / |   
\_| \_/\___/ \__,_|\____/|_|   
                               
Менеджер Gradient Bot
    @nod3r - Мультиаккаунт версия
"""
print(banner)
time.sleep(1)

# Настройка логгера с цветным форматтером
logger = logging.getLogger()
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = ColoredFormatter("%(asctime)s - %(message)s", "%H:%M:%S")
console_handler.setFormatter(formatter)
logger.handlers = [console_handler]

# Идентификатор расширения (если нужно открыть его напрямую)
EXTENSION_ID = "caacbgbklghmpodbdafajbgdnegacfmo"
# Ссылка для скачивания CRX (при необходимости)
CRX_URL = ("https://clients2.google.com/service/update2/crx?"
           "response=redirect&prodversion=98.0.4758.102&acceptformat=crx2,crx3&"
           "x=id%3D{0}%26uc&nacl_arch=x86-64".format(EXTENSION_ID))
EXTENSION_FILENAME = "app.crx"

def load_accounts():
    """
    Загружает аккаунты.
    Если существует файл accounts.txt, читает его (email:пароль).
    Иначе пытается брать из переменных окружения APP_USER и APP_PASS.
    """
    accounts = []
    if os.path.exists("accounts.txt"):
        with open("accounts.txt", "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        email = parts[0].strip()
                        password = ":".join(parts[1:]).strip()
                        accounts.append((email, password))
        if accounts:
            logger.info(f"Загружено {len(accounts)} аккаунтов из accounts.txt.")
    else:
        user = os.getenv("APP_USER")
        password = os.getenv("APP_PASS")
        if user and password:
            accounts.append((user, password))
            logger.info("Используется один аккаунт из переменных окружения.")
        else:
            logger.error("Не заданы аккаунты. Укажите APP_USER и APP_PASS или создайте файл accounts.txt.")
            exit(1)
    return accounts

def download_extension():
    """
    Скачивает нужное CRX-расширение (app.crx), если его нет или оно устарело.
    Если вам не нужно это конкретное расширение, можно убрать.
    """
    logger.info(f"Скачивание расширения с: {CRX_URL}")
    ext_path = Path(EXTENSION_FILENAME)
    # Проверяем, не скачивали ли мы его недавно (за последние сутки)
    if ext_path.exists() and time.time() - ext_path.stat().st_mtime < 86400:
        logger.info("Расширение уже скачано, пропускаем скачивание...")
        return
    response = requests.get(CRX_URL)
    if response.status_code == 200:
        ext_path.write_bytes(response.content)
        logger.info("Расширение успешно скачано")
    else:
        logger.error(f"Не удалось скачать расширение: {response.status_code}")
        exit(1)

def create_proxy_auth_extension(host, port, username, password, scheme='http', plugin_path='proxy_auth_plugin.zip'):
    """
    Создает динамическое расширение для Chrome, задающее прокси с логином/паролем.
    """
    manifest_json = """
    {
      "version": "1.0.0",
      "manifest_version": 2,
      "name": "Chrome Proxy Auth Extension",
      "permissions": [
        "proxy",
        "tabs",
        "unlimitedStorage",
        "storage",
        "<all_urls>",
        "webRequest",
        "webRequestBlocking"
      ],
      "background": {
        "scripts": ["background.js"]
      },
      "minimum_chrome_version": "22.0.0"
    }
    """
    background_js = f"""
    var config = {{
        mode: "fixed_servers",
        rules: {{
            singleProxy: {{
                scheme: "{scheme}",
                host: "{host}",
                port: parseInt({port})
            }},
            bypassList: ["localhost"]
        }}
    }};
    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});

    function callbackFn(details) {{
        return {{
            authCredentials: {{
                username: "{username}",
                password: "{password}"
            }}
        }};
    }}
    chrome.webRequest.onAuthRequired.addListener(
        callbackFn,
        {{urls: ["<all_urls>"]}},
        ['blocking']
    );
    """
    with zipfile.ZipFile(plugin_path, 'w') as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    return plugin_path

def setup_chrome_options(proxy=None):
    """
    Настраивает ChromeOptions.
    Если есть прокси с логином/паролем - создаём расширение.
    Если нужно headless - используем флаги.
    """
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless=new")  # Или "--headless", смотря что работает лучше.

    ua = UserAgent()
    chrome_options.add_argument(f"user-agent={ua.random}")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--webrtc-ip-handling-policy=disable_non_proxied_udp")

    if proxy:
        if "@" in proxy:
            parsed = urlparse(proxy)
            scheme = parsed.scheme
            username = parsed.username
            password = parsed.password
            host = parsed.hostname
            port = parsed.port
            plugin_path = create_proxy_auth_extension(host, port, username, password, scheme)
            chrome_options.add_extension(plugin_path)
            logger.info(f"Динамическое расширение для прокси создано для: {proxy}")
        else:
            chrome_options.add_argument("--proxy-server=" + proxy)
            logger.info(f"Используется прокси: {proxy}")
    else:
        logger.info("Режим прямого соединения (без прокси).")

    # Подключаем app.crx, если оно скачано
    ext_path = Path(EXTENSION_FILENAME).resolve()
    if ext_path.exists():
        chrome_options.add_extension(str(ext_path))
    else:
        logger.warning("Расширение для приложения не найдено (app.crx).")

    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    return chrome_options

def login_to_app(driver, account):
    """
    Авторизация в веб-приложении (пример: https://app.gradient.network/).
    account: (email, пароль)
    """
    email, password = account
    driver.get("https://app.gradient.network/")
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '[placeholder="Enter Email"]'))
    )
    driver.find_element(By.CSS_SELECTOR, '[placeholder="Enter Email"]').send_keys(email)
    driver.find_element(By.CSS_SELECTOR, '[type="password"]').send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button").click()
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/dashboard/setting"]'))
    )
    logger.info(f"Успешная авторизация для аккаунта: {email}")

def open_extension(driver):
    """
    Пример открытия уже установленного расширения (по ID).
    Если это не нужно, можете убрать.
    """
    driver.get(f"chrome-extension://{EXTENSION_ID}/popup.html")
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "Status")]'))
    )
    logger.info("Расширение загружено успешно")

def attempt_connection(proxy, account):
    """
    Пробуем установить соединение (запустить браузер с нужными опциями, авторизоваться).
    Если прокси не указан, идём без прокси.
    """
    if proxy is None:
        try:
            chrome_options = setup_chrome_options(None)
            # Используем локально установленный chromedriver (114), путь /usr/local/bin/chromedriver
            driver = webdriver.Chrome(
                service=Service("/usr/local/bin/chromedriver"),
                options=chrome_options
            )
            download_extension()
            login_to_app(driver, account)
            open_extension(driver)
            logger.info(f"Подключение успешно без прокси для аккаунта {account[0]}")
            return driver
        except Exception as e:
            logger.warning(f"Подключение без прокси не удалось для аккаунта {account[0]} - Ошибка: {e}")
            return None
    else:
        # Если хотим перебрать список прокси (у вас в коде proxy может быть выбран).
        from_main_list = proxies.copy()
        if proxy in from_main_list:
            from_main_list.remove(proxy)
            from_main_list.insert(0, proxy)

        for pr in from_main_list:
            if pr is None:
                continue
            try:
                chrome_options = setup_chrome_options(pr)
                driver = webdriver.Chrome(
                    service=Service("/usr/local/bin/chromedriver"),
                    options=chrome_options
                )
                download_extension()
                login_to_app(driver, account)
                open_extension(driver)
                logger.info(f"Подключение успешно с прокси: {pr} для аккаунта {account[0]}")
                return driver
            except Exception as e:
                logger.warning(f"Прокси не сработал: {pr} для аккаунта {account[0]} - Ошибка: {e}")
                try:
                    driver.quit()
                except:
                    pass
                logger.info("Пробуем следующий вариант прокси...")

        logger.error(f"Не удалось установить подключение для аккаунта {account[0]} ни через один из вариантов прокси.")
        return None

def worker(account, proxy, node_index):
    """
    Функция-воркер для задачи по аккаунту.
    """
    driver = attempt_connection(proxy, account)
    if driver:
        logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Работаем.")
        try:
            while True:
                time.sleep(random.uniform(20, 40))
                logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Выполнение задач...")
        except KeyboardInterrupt:
            logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Остановка по запросу пользователя.")
        finally:
            driver.quit()
    else:
        logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Не удалось подключиться. Переход к следующему.")

def management_interface(accounts):
    """
    Меню управления: позволяет выбрать режим работы бота (с прокси / без).
    """
    while True:
        print("\nМеню управления:")
        print("1. Запустить бота для одного аккаунта (с прокси)")
        print("2. Запустить бота для одного аккаунта (без прокси)")
        print("3. Запустить бота для всех аккаунтов (с прокси)")
        print("4. Запустить бота для всех аккаунтов (без прокси)")
        print("5. Выход")

        choice = input("Выберите опцию (1-5): ").strip()
        if choice == "1":
            print("\nСписок аккаунтов:")
            for idx, account in enumerate(accounts, start=1):
                print(f"{idx}. {account[0]}")
            try:
                sel = int(input("Выберите номер аккаунта: ").strip())
                if 1 <= sel <= len(accounts):
                    selected_account = accounts[sel - 1]
                    print("\nСписок доступных прокси:")
                    for idx, pr in enumerate(proxies, start=1):
                        print(f"{idx}. {pr if pr else 'Direct mode'}")
                    sel_proxy_input = input("Выберите номер прокси (или пусто для случайного): ").strip()
                    if sel_proxy_input:
                        sel_proxy = int(sel_proxy_input)
                        if 1 <= sel_proxy <= len(proxies):
                            chosen_proxy = proxies[sel_proxy - 1]
                        else:
                            print("Неверный выбор прокси. Будет использовано случайное прокси.")
                            chosen_proxy = random.choice(proxies)
                    else:
                        chosen_proxy = random.choice(proxies)

                    use_same_proxy = input("Использовать один прокси для всех нод? (да/нет): ").strip().lower()
                    same_proxy = use_same_proxy in ["да", "yes", "y"]

                    sessions_input = input("Введите количество сессий (нод): ").strip()
                    try:
                        sessions = int(sessions_input)
                    except ValueError:
                        sessions = 1

                    delay_input = input("Введите задержку между запуском нод (сек): ").strip()
                    try:
                        delay = float(delay_input)
                    except ValueError:
                        delay = 0

                    logger.info(f"Аккаунт {selected_account[0]}: запуск {sessions} сессий с прокси {chosen_proxy} задержка {delay} сек.")
                    with ThreadPoolExecutor(max_workers=sessions) as executor:
                        for node in range(1, sessions+1):
                            proxy_for_node = chosen_proxy if same_proxy else random.choice(proxies)
                            executor.submit(worker, selected_account, proxy_for_node, node)
                            time.sleep(delay)
                else:
                    print("Неверный номер аккаунта.")
            except ValueError:
                print("Введите корректное число.")

        elif choice == "2":
            print("\nЗапуск бота без прокси для одного аккаунта.")
            print("\nСписок аккаунтов:")
            for idx, account in enumerate(accounts, start=1):
                print(f"{idx}. {account[0]}")
            try:
                sel = int(input("Выберите номер аккаунта: ").strip())
                if 1 <= sel <= len(accounts):
                    selected_account = accounts[sel - 1]
                    sessions_input = input("Сколько сессий (нод)? ").strip()
                    try:
                        sessions = int(sessions_input)
                    except ValueError:
                        sessions = 1

                    delay_input = input("Задержка между нодами (сек)? ").strip()
                    try:
                        delay = float(delay_input)
                    except ValueError:
                        delay = 0

                    logger.info(f"Аккаунт {selected_account[0]}: {sessions} сессий без прокси, задержка {delay} сек.")
                    with ThreadPoolExecutor(max_workers=sessions) as executor:
                        for node in range(1, sessions+1):
                            executor.submit(worker, selected_account, None, node)
                            time.sleep(delay)
                else:
                    print("Неверный номер аккаунта.")
            except ValueError:
                print("Введите корректное число.")

        elif choice == "3":
            try:
                sessions_input = input("Сколько сессий (нод) на аккаунт?: ").strip()
                try:
                    sessions = int(sessions_input)
                except ValueError:
                    sessions = 1

                delay_input = input("Задержка между нодами (сек)?: ").strip()
                try:
                    delay = float(delay_input)
                except ValueError:
                    delay = 0

                logger.info(f"Запуск бота для всех аккаунтов (с прокси). {sessions} сессий на аккаунт, задержка {delay} сек.")
                with ThreadPoolExecutor(max_workers=min(len(accounts)*sessions, 5)) as executor:
                    futures = []
                    for account in accounts:
                        for node in range(1, sessions+1):
                            chosen_proxy = random.choice(proxies)
                            futures.append(executor.submit(worker, account, chosen_proxy, node))
                            time.sleep(delay)
                    for future in as_completed(futures):
                        future.result()
            except KeyboardInterrupt:
                logger.info("Остановка всех воркеров.")
                break

        elif choice == "4":
            try:
                sessions_input = input("Сколько сессий (нод) на аккаунт?: ").strip()
                try:
                    sessions = int(sessions_input)
                except ValueError:
                    sessions = 1

                delay_input = input("Задержка между нодами (сек)?: ").strip()
                try:
                    delay = float(delay_input)
                except ValueError:
                    delay = 0

                logger.info(f"Запуск бота для всех аккаунтов (без прокси). {sessions} сессий на аккаунт, задержка {delay} сек.")
                with ThreadPoolExecutor(max_workers=len(accounts)*sessions) as executor:
                    futures = []
                    for account in accounts:
                        for node in range(1, sessions+1):
                            futures.append(executor.submit(worker, account, None, node))
                            time.sleep(delay)
                    for future in as_completed(futures):
                        future.result()
            except KeyboardInterrupt:
                logger.info("Остановка всех воркеров.")
                break

        elif choice == "5":
            print("Выход из программы.")
            exit(0)

        else:
            print("Неверный выбор. Попробуйте снова.")

def main():
    # Загружаем переменные окружения
    load_dotenv()

    # Глобальная загрузка прокси
    global proxies
    proxies = []
    if os.path.exists("active_proxies.txt"):
        with open("active_proxies.txt", "r") as f:
            proxies = [line.strip() for line in f if line.strip()]
        if not proxies:
            logger.warning("Прокси не найдены в active_proxies.txt. Работаем без прокси.")
            proxies = [None]
    else:
        logger.warning("Файл active_proxies.txt не найден. Работаем без прокси.")
        proxies = [None]

    accounts = load_accounts()
    management_interface(accounts)

if __name__ == "__main__":
    main()

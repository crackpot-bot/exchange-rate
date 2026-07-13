"""
云端汇率采集脚本 — GitHub Actions 用
从 cnyrate.com 抓取工行/农行美元汇率，写入 JSON 文件
无需 Flask、SQLite、APScheduler，纯独立运行
"""

import re
import json
import os
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TZ_BEIJING = timezone(timedelta(hours=8))
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
LATEST_FILE = os.path.join(DATA_DIR, 'latest.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
MAX_HISTORY_HOURS = 24

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

BANK_CONFIG = {
    'ICBC': {'name': '工商银行', 'url': 'https://www.cnyrate.com/icbc.html'},
    'ABC': {'name': '农业银行', 'url': 'https://www.cnyrate.com/abc.html'},
}


def fetch_page(url):
    """抓取页面，返回 BeautifulSoup，失败返回 None"""
    session = requests.Session()
    for attempt in range(3):
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                resp.encoding = 'utf-8'
                return BeautifulSoup(resp.text, 'lxml')
            logger.warning(f"HTTP {resp.status_code}, 尝试 {attempt + 1}/3")
        except Exception as e:
            logger.warning(f"请求异常: {e}, 尝试 {attempt + 1}/3")
    logger.error(f"无法获取: {url}")
    return None


def parse_rate(soup, bank_code):
    """解析汇率页面，返回 dict 或 None"""
    if soup is None:
        return None

    now = datetime.now(TZ_BEIJING)
    fetched_at = now.strftime('%Y-%m-%d %H:%M:%S')

    # 提取银行公布时间
    published_at = fetched_at
    update_text = soup.find(string=re.compile(r'更新时间'))
    if update_text:
        m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', update_text)
        if m:
            published_at = m.group(1)

    # 主方法：data-row-key="USD"
    usd_row = soup.find('tr', attrs={'data-row-key': 'USD'})
    if usd_row:
        cells = usd_row.find_all('td')
        if len(cells) >= 5:
            try:
                return {
                    'bank_name': bank_code,
                    'currency': 'USD',
                    'currency_name': '美元',
                    'buying_rate': float(cells[1].get_text(strip=True)),
                    'selling_rate': float(cells[3].get_text(strip=True)),
                    'cash_buying_rate': float(cells[2].get_text(strip=True)),
                    'cash_selling_rate': float(cells[4].get_text(strip=True)),
                    'published_at': published_at,
                    'fetched_at': fetched_at,
                }
            except (ValueError, AttributeError) as e:
                logger.warning(f"{bank_code} 解析数值失败: {e}")

    # 备用正则
    text = soup.get_text()
    usd_idx = text.find('美元')
    if usd_idx > 0:
        search = text[usd_idx:usd_idx + 200]
        m = re.search(r'美元\D*?(\d+\.?\d*)\s*(\d+\.?\d*)\s*(\d+\.?\d*)\s*(\d+\.?\d*)', search)
        if m:
            buying, cash_buy, selling, cash_sell = map(float, m.groups())
            if 400 < buying < 900 and 400 < selling < 900:
                return {
                    'bank_name': bank_code,
                    'currency': 'USD',
                    'currency_name': '美元',
                    'buying_rate': buying,
                    'selling_rate': selling,
                    'cash_buying_rate': cash_buy,
                    'cash_selling_rate': cash_sell,
                    'published_at': published_at,
                    'fetched_at': fetched_at,
                }

    logger.error(f"{bank_code} 解析失败")
    return None


def save_latest(rates):
    """保存最新汇率"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LATEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(rates, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存最新汇率: {len(rates)} 条 → {LATEST_FILE}")


def save_history(rates):
    """追加到历史记录，保留最近24小时"""
    os.makedirs(DATA_DIR, exist_ok=True)

    existing = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            existing = []

    # 追加新数据
    existing.extend(rates)

    # 保留最近24小时
    cutoff = datetime.now(TZ_BEIJING) - timedelta(hours=MAX_HISTORY_HOURS)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    filtered = [r for r in existing if r.get('fetched_at', '') >= cutoff_str]

    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, ensure_ascii=False)
    logger.info(f"历史记录: {len(filtered)} 条 (新增 {len(rates)} 条) → {HISTORY_FILE}")


def collect_all():
    """采集所有银行汇率并保存"""
    rates = []
    for code, cfg in BANK_CONFIG.items():
        logger.info(f"采集 {cfg['name']}...")
        soup = fetch_page(cfg['url'])
        rate = parse_rate(soup, code)
        if rate:
            logger.info(f"  {cfg['name']}: 卖出 {rate['selling_rate']}, 买入 {rate['buying_rate']}")
            rates.append(rate)
        else:
            logger.error(f"  {cfg['name']} 采集失败!")

    if rates:
        save_latest(rates)
        save_history(rates)
        logger.info("✅ 采集完成")
    else:
        logger.error("❌ 所有银行采集失败，不更新数据文件")
        # 不更新文件，避免覆盖已有数据

    return rates


if __name__ == '__main__':
    collect_all()

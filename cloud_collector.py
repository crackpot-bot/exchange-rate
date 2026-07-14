"""
云端汇率采集脚本 — GitHub Actions 用
从 cnyrate.com 抓取工行/农行美元汇率，写入 JSON 文件
无需 Flask、SQLite、APScheduler，纯独立运行

时间规则（北京时间）：
- 08:00 ~ 24:00：正常采集
- 00:00 ~ 08:00：暂停采集（不自链，等待 08:00 cron 触发恢复）
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
MAX_HISTORY_DAYS = 365          # 保留1年历史
COLLECTION_START_HOUR = 8       # 采集开始时间
COLLECTION_END_HOUR = 24        # 采集结束时间（24=午夜）

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

BANK_CONFIG = {
    'ICBC': {'name': '工商银行', 'url': 'https://www.cnyrate.com/icbc.html'},
    'ABC': {'name': '农业银行', 'url': 'https://www.cnyrate.com/abc.html'},
}


def is_collection_time():
    """判断当前北京时间是否在采集时段（08:00~24:00）"""
    now = datetime.now(TZ_BEIJING)
    return COLLECTION_START_HOUR <= now.hour < COLLECTION_END_HOUR


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
    """保存最新汇率（完整字段）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LATEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(rates, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存最新汇率: {len(rates)} 条 -> {LATEST_FILE}")


def save_history(rates):
    """追加到历史记录，保留最近1年，精简字段以减小文件体积"""
    os.makedirs(DATA_DIR, exist_ok=True)

    existing = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            existing = []

    # 精简新记录：只保留图表和历史表需要的字段
    minimal_new = [{
        'fetched_at': r['fetched_at'],
        'bank_name': r['bank_name'],
        'selling_rate': r['selling_rate'],
        'buying_rate': r['buying_rate'],
    } for r in rates]

    # 同时精简已有记录（逐步迁移旧格式）
    existing = [{
        'fetched_at': r.get('fetched_at', ''),
        'bank_name': r.get('bank_name', ''),
        'selling_rate': r.get('selling_rate', 0),
        'buying_rate': r.get('buying_rate', 0),
    } for r in existing]

    existing.extend(minimal_new)

    # 去重：同一时间戳同一银行只保留最后一条
    seen = {}
    for r in existing:
        key = (r['fetched_at'], r['bank_name'])
        seen[key] = r
    existing = sorted(seen.values(), key=lambda x: (x['fetched_at'], x['bank_name']))

    # 保留最近 MAX_HISTORY_DAYS 天
    cutoff = datetime.now(TZ_BEIJING) - timedelta(days=MAX_HISTORY_DAYS)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    filtered = [r for r in existing if r.get('fetched_at', '') >= cutoff_str]

    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, ensure_ascii=False, separators=(',', ':'))
    logger.info(f"历史记录: {len(filtered)} 条 (新增 {len(minimal_new)} 条, 保留 {MAX_HISTORY_DAYS} 天) -> {HISTORY_FILE}")


def collect_all():
    """采集所有银行汇率并保存"""
    # ── 暂停时段检查 ──
    if not is_collection_time():
        now_str = datetime.now(TZ_BEIJING).strftime('%H:%M')
        logger.info(f"⏸️  当前北京时间 {now_str}，处于暂停时段 (00:00-08:00)，跳过采集")
        return []

    logger.info("✅ 处于采集时段 (08:00-24:00)，开始采集...")
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

    return rates


if __name__ == '__main__':
    collect_all()

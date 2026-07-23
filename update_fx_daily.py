"""
每日更新 USD/CNY 日线数据（bp 格式）
- 读取 data/fx_daily.json（按日期降序存储，数值单位为 bp）
- 从外部 API 获取今日收盘价
- 自动 ×100 转换为 bp 后追加
- 如果今天的数据已存在则跳过，不覆盖历史数据
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

DATA_FILE = 'data/fx_daily.json'
TZ_BEIJING = timezone(timedelta(hours=8))

def fetch_today_rate():
    """尝试从多个免费 API 获取今日 USD/CNY 汇率"""
    try:
        import urllib.request
        # 方法1: exchangerate-api.com (免费, 无需 key)
        url = 'https://api.exchangerate-api.com/v4/latest/USD'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            rate = data['rates'].get('CNY')
            if rate:
                return round(rate, 4)
    except Exception as e:
        print(f"  [exchangerate-api] 失败: {e}")

    try:
        # 方法2: 直接请求
        import urllib.request
        url = 'https://open.er-api.com/v6/latest/USD'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            rate = data['rates'].get('CNY')
            if rate:
                return round(rate, 4)
    except Exception as e:
        print(f"  [er-api] 失败: {e}")

    return None


def main():
    # 检查北京时间
    now = datetime.now(TZ_BEIJING)
    today_str = now.strftime('%Y-%m-%d')
    print(f"北京时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    # 周末/非交易时段不更新
    if now.weekday() >= 5:
        print("周末，跳过 fx 日线更新")
        return

    # 加载现有数据
    if not os.path.exists(DATA_FILE):
        print(f"文件不存在: {DATA_FILE}")
        return

    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"现有数据: {len(data)} 条, 最新: {data[0]['date']}")

    # 检查今天是否已有数据 —— 增量更新核心：存在则跳过，不覆盖
    if data and data[0]['date'] == today_str:
        print(f"今天 ({today_str}) 已有数据，跳过更新（保持历史数据不变）")
        return

    # 获取今日汇率
    rate = fetch_today_rate()
    if rate is None:
        print("无法获取今日汇率，跳过更新")
        return

    # 转换为 bp（×100），保留两位小数
    bp_rate = round(rate * 100, 2)
    print(f"获取到今日汇率: {rate} (原始) -> {bp_rate} bp")

    # 追加新数据（今天的 OHLC 都用收盘价近似，因为免费 API 没有 OHLC）
    today_entry = {
        'date': today_str,
        'open': bp_rate,
        'high': bp_rate,
        'low': bp_rate,
        'last': bp_rate
    }

    data.insert(0, today_entry)  # 降序排列，最新在前
    # 保留最近 500 条
    if len(data) > 500:
        data = data[:500]

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"✅ 已增量更新: {today_str} = {bp_rate} bp, 总 {len(data)} 条")


if __name__ == '__main__':
    main()

import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://www.asiagoal.com.tw/api/store/products"

PARAMS = {
    "type": "category",
    "id": "52139",
    "orderType": "default",
    "hotSalePeriod": "default",
    "activeId": "52139",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.asiagoal.com.tw/",
}

# 重試設定：遇到 429 / 5xx 時，等待 30 → 60 → 120 → 240 秒
# 若對方的 Retry-After 比退避時間更長才採用對方的，絕不比退避時間短
# （站方的 Retry-After 可能只有 5 秒，照它等只會變成連續狂打）
MAX_RETRIES = 5
BACKOFF_BASE = 30
MAX_WAIT = 240

# 每頁之間的隨機間隔（秒），避免被判定為高頻請求
PAGE_DELAY = (1.5, 3.0)

# 安全上限，避免 API 異常時無限翻頁
MAX_PAGES = 200

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
BLOCKED_STATUS = {403, 429}


class BlockedError(RuntimeError):
    """被對方限流或封鎖（429 / 403），換 limit 重試也沒有意義。"""


session = requests.Session()
session.headers.update(HEADERS)

# 若 IP 被擋，可在 GitHub Secrets 設定 PROXY_URL（例如 http://user:pass@host:port）
proxy_url = os.environ.get("PROXY_URL")
if proxy_url:
    session.proxies.update({"http": proxy_url, "https": proxy_url})
    print("已使用 PROXY_URL 代理。")


def get_wait_seconds(response, attempt):
    """取「指數退避」與「Retry-After」中較長者，再加上隨機抖動。"""
    wait = min(BACKOFF_BASE * (2 ** (attempt - 1)), MAX_WAIT)

    retry_after = response.headers.get("Retry-After") if response is not None else None

    if retry_after and retry_after.strip().isdigit():
        wait = max(wait, min(int(retry_after.strip()), MAX_WAIT))

    return wait + random.uniform(0, 5)


def print_diagnostics(r):
    """印出判斷是否被 Cloudflare / WAF 擋下的線索。"""
    print(f"  Server: {r.headers.get('Server', '')}")
    print(f"  CF-RAY: {r.headers.get('CF-RAY', '')}")
    print(f"  Retry-After: {r.headers.get('Retry-After', '')}")
    print(f"  回應內容前 200 字：{r.text[:200]!r}")


def request_page(page, limit):
    params = PARAMS.copy()
    params["page"] = page
    params["limit"] = limit

    last_error = None
    last_status = None

    for attempt in range(1, MAX_RETRIES + 1):
        response = None

        try:
            print(
                f"請求第 {page} 頁（limit={limit}），"
                f"第 {attempt}/{MAX_RETRIES} 次..."
            )

            response = session.get(BASE_URL, params=params, timeout=30)
            last_status = response.status_code

            print(f"HTTP Status: {response.status_code}")

            if response.status_code in BLOCKED_STATUS | RETRYABLE_STATUS:
                print_diagnostics(response)

            if response.status_code in RETRYABLE_STATUS:
                raise requests.HTTPError(
                    f"{response.status_code} for url: {response.url}",
                    response=response,
                )

            response.raise_for_status()

            try:
                data = response.json()
            except ValueError:
                print("⚠️ API 回應不是 JSON，內容前 300 字：")
                print(response.text[:300])
                raise

            if data.get("result") != "success":
                raise RuntimeError("API 回傳失敗")

            payload = data.get("response")

            if not payload:
                raise RuntimeError("API response 為空")

            return payload[0]

        except Exception as e:
            last_error = e

            print(f"⚠️ 第 {attempt} 次請求失敗：{type(e).__name__}: {e}")

            # 非暫時性錯誤（例如 404、401）不必重試
            if (
                response is not None
                and response.status_code not in RETRYABLE_STATUS
                and response.status_code >= 400
            ):
                break

            if attempt < MAX_RETRIES:
                wait = get_wait_seconds(response, attempt)
                print(f"{wait:.0f} 秒後重試...")
                time.sleep(wait)

    message = f"第 {page} 頁在 {MAX_RETRIES} 次嘗試後仍然失敗"

    if last_status in BLOCKED_STATUS:
        raise BlockedError(message) from last_error

    raise RuntimeError(message) from last_error


def fetch_first_page():
    """抓第一頁並回傳 (limit, data)，避免重複請求同一頁。"""
    try:
        data = request_page(1, 100)
        print("limit=100 成功。")
        return 100, data

    except BlockedError:
        # 被限流 / 封鎖時換 limit 沒用，直接放棄，避免再多發請求
        print("❌ 被 Asiagoal 限流或封鎖（429/403），不再嘗試其他 limit。")
        raise

    except Exception as e:
        print(f"limit=100 失敗：{e}")
        print("改用 limit=16。")

    data = request_page(1, 16)
    print("limit=16 成功。")
    return 16, data


def parse_date(text):
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", text or "")
    if not m:
        return datetime.max

    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def save_json(path, obj):
    """先寫暫存檔再替換，避免寫到一半造成檔案損毀。"""
    tmp_path = f"{path}.tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, path)


def to_product(item):
    route = item.get("route", "")

    return {
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "route": route,
        "price": item.get("shownPrice"),
        "origin_price": item.get("shownOriginPrice"),
        "pre_order_hint": item.get("pre_order_hint", ""),
        "photo": item.get("photo", ""),
        "url": f"https://www.asiagoal.com.tw/item/{route}",
    }


# ---------------------------------------------------------------------------
# 抓取資料
# ---------------------------------------------------------------------------

print("開始抓取最新預購商品...")

try:
    LIMIT, first_page = fetch_first_page()
except Exception as e:
    print(f"❌ Asiagoal API 目前無法正常取得資料：{e}")
    print("為避免覆蓋現有資料，本次不會寫入 products.json 或 deadline.json。")
    raise

print(f"使用 limit = {LIMIT}")

products = []
seen_routes = set()

page = 1
data = first_page

while True:
    print(f"處理第 {page} 頁...")

    items = data.get("products", [])

    if not items:
        break

    new_count = 0

    for item in items:
        product = to_product(item)
        key = product["route"] or product["title"]

        if key in seen_routes:
            continue

        seen_routes.add(key)
        products.append(product)
        new_count += 1

    # 這一頁完全沒有新商品，代表 API 在重複回傳，停止避免無限迴圈
    if new_count == 0:
        print("這一頁沒有新商品，停止翻頁。")
        break

    page += 1

    if page > MAX_PAGES:
        print(f"已達頁數上限 {MAX_PAGES}，停止翻頁。")
        break

    time.sleep(random.uniform(*PAGE_DELAY))

    try:
        data = request_page(page, LIMIT)
    except Exception as e:
        print(f"❌ 第 {page} 頁抓取失敗：{e}")
        print("資料可能不完整，為避免覆蓋現有資料，本次不會寫入檔案。")
        raise

print(f"共抓到 {len(products)} 件商品")

if not products:
    print("❌ 抓到 0 件商品，視為異常，不覆蓋現有資料。")
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# 整理結單日期
# ---------------------------------------------------------------------------

groups = defaultdict(list)

for p in products:
    summary = (p.get("summary") or "").strip()

    if not summary:
        continue

    if "結單" not in summary:
        continue

    summary = summary.replace("結單", "").strip()

    groups[summary].append(
        {
            "title": p["title"],
            "url": p["url"],
            "price": p["price"],
            "photo": p["photo"],
        }
    )

result = []

for date in sorted(groups.keys(), key=parse_date):
    result.append(
        {
            "date": date,
            "count": len(groups[date]),
            "items": sorted(groups[date], key=lambda x: x["title"]),
        }
    )

# 使用台灣時間
taipei_now = datetime.now(ZoneInfo("Asia/Taipei"))
today = taipei_now.date()

for g in result:
    parsed = parse_date(g["date"])

    if parsed == datetime.max:
        g["days_left"] = None
    else:
        g["days_left"] = (parsed.date() - today).days

deadline = {
    "updated": taipei_now.strftime("%Y-%m-%d %H:%M:%S"),
    "total": len(products),
    "groups": result,
}

save_json("products.json", products)
save_json("deadline.json", deadline)

print("完成！")
print("products.json 已建立")
print("deadline.json 已建立")

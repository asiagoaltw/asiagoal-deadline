import json
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
    "limit": 100,
    "orderType": "default",
    "hotSalePeriod": "default",
    "activeId": "52139"
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    ),
    "Accept": "application/json"
}

MAX_RETRIES = 3
RETRY_DELAY = 5


def request_page(page, limit):
    params = PARAMS.copy()
    params["page"] = page
    params["limit"] = limit

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(
                f"請求第 {page} 頁（limit={limit}），"
                f"第 {attempt}/{MAX_RETRIES} 次..."
            )

            r = requests.get(
                BASE_URL,
                params=params,
                headers=HEADERS,
                timeout=30
            )

            print(f"HTTP Status: {r.status_code}")
            print(f"Content-Type: {r.headers.get('Content-Type', '')}")

            r.raise_for_status()

            try:
                data = r.json()
            except requests.exceptions.JSONDecodeError:
                print("⚠️ API 回應不是 JSON。")
                print("回應內容前 300 字：")
                print(r.text[:300])
                raise

            if data.get("result") != "success":
                raise RuntimeError("API 回傳失敗")

            response = data.get("response")

            if not response:
                raise RuntimeError("API response 為空")

            return response[0]

        except Exception as e:
            last_error = e

            print(
                f"⚠️ 第 {attempt} 次請求失敗："
                f"{type(e).__name__}: {e}"
            )

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * attempt
                print(f"{delay} 秒後重試...")
                time.sleep(delay)

    raise RuntimeError(
        f"第 {page} 頁在 {MAX_RETRIES} 次嘗試後仍然失敗"
    ) from last_error


def parse_date(text):
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", text or "")
    if not m:
        return datetime.max

    return datetime(
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3))
    )


print("開始抓取最新預購商品...")

try:
    request_page(1, 100)
    LIMIT = 100
    print("limit=100 測試成功。")

except Exception as e:
    print(f"limit=100 測試失敗：{e}")
    print("改用 limit=16。")

    try:
        request_page(1, 16)
        LIMIT = 16
        print("limit=16 測試成功。")

    except Exception as e:
        print(f"limit=16 也失敗：{e}")
        print("❌ Asiagoal API 目前無法正常取得資料。")
        print("為避免覆蓋現有資料，本次不會寫入 products.json 或 deadline.json。")
        raise

print(f"使用 limit = {LIMIT}")

products = []

page = 1

while True:
    print(f"第 {page} 頁...")

    data = request_page(page, LIMIT)
    items = data.get("products", [])

    if not items:
        break

    for item in items:
        products.append({
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "route": item.get("route", ""),
            "price": item.get("shownPrice"),
            "origin_price": item.get("shownOriginPrice"),
            "pre_order_hint": item.get("pre_order_hint", ""),
            "photo": item.get("photo", ""),
            "url": f"https://www.asiagoal.com.tw/item/{item.get('route','')}"
        })

    page += 1

print(f"共抓到 {len(products)} 件商品")

with open("products.json", "w", encoding="utf-8") as f:
    json.dump(products, f, ensure_ascii=False, indent=2)

groups = defaultdict(list)

for p in products:
    summary = (p.get("summary") or "").strip()

    if not summary:
        continue

    if "結單" not in summary:
        continue

    summary = summary.replace("結單", "").strip()

    groups[summary].append({
        "title": p["title"],
        "url": p["url"],
        "price": p["price"],
        "photo": p["photo"]
    })

result = []

for date in sorted(groups.keys(), key=parse_date):
    result.append({
        "date": date,
        "count": len(groups[date]),
        "items": sorted(
            groups[date],
            key=lambda x: x["title"]
        )
    })

# 使用台灣時間
taipei_now = datetime.now(ZoneInfo("Asia/Taipei"))
today = taipei_now.date()

for g in result:
    target = datetime.strptime(
        g["date"],
        "%Y/%m/%d"
    ).date()

    g["days_left"] = (target - today).days

deadline = {
    "updated": taipei_now.strftime("%Y-%m-%d %H:%M:%S"),
    "total": len(products),
    "groups": result
}

with open("deadline.json", "w", encoding="utf-8") as f:
    json.dump(
        deadline,
        f,
        ensure_ascii=False,
        indent=2
    )

print("完成！")
print("products.json 已建立")
print("deadline.json 已建立")

import json
import re
from collections import defaultdict
from datetime import datetime

import requests

BASE_URL = "https://www.asiagoal.com.tw/store/products"

PARAMS = {
    "type": "category",
    "id": "52139",          # 最新預購
    "limit": 100,           # 先試100，若API限制會自動改16
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


def request_page(page, limit):
    params = PARAMS.copy()
    params["page"] = page
    params["limit"] = limit

    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()

    data = r.json()

    if data.get("result") != "success":
        raise Exception("API 回傳失敗")

    return data["response"][0]


def parse_date(summary):
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", summary or "")
    if not m:
        return datetime.max

    return datetime(
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3))
    )


print("開始抓取最新預購商品...")

# ---------- 自動偵測 limit ----------
try:
    test = request_page(1, 100)
    limit = 100
except Exception:
    limit = 16

print(f"使用 limit = {limit}")

products = []

page = 1

while True:

    print(f"第 {page} 頁...")

    data = request_page(page, limit)

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

# ------------------------------
# products.json
# ------------------------------

with open(
    "products.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        products,
        f,
        ensure_ascii=False,
        indent=2
    )

# ------------------------------
# deadline.json
# ------------------------------

groups = defaultdict(list)

for p in products:

    summary = (p.get("summary") or "").strip()

# 沒有結單日期就跳過
if not summary:
    continue

# 只保留真正的結單日期
if "結單" not in summary:
    continue

# 去掉「結單」兩個字
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

today = datetime.now().date()

for g in result:

    target = datetime.strptime(g["date"], "%Y/%m/%d").date()

    g["days_left"] = (target - today).days

deadline = {
    "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "total": len(products),
    "groups": result
}

with open(
    "deadline.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        deadline,
        f,
        ensure_ascii=False,
        indent=2
    )

print("完成！")
print("products.json 已建立")
print("deadline.json 已建立")

import json
import time
import requests

LIST_URL = "https://www.asiagoal.com.tw/store/products?page=1&type=category&id=52139,52138,52141,52184,52193,52167&limit=16&orderType=default&hotSalePeriod=default&activeId=52139"

headers = {
    "User-Agent": "Mozilla/5.0"
}

print("取得第一頁商品...")

r = requests.get(LIST_URL, headers=headers, timeout=30)
r.raise_for_status()

data = r.json()

products = data["response"][0]["products"]

print(f"第一頁共有 {len(products)} 件商品")

result = []

for i, product in enumerate(products, start=1):

    route = product["route"]

    detail_url = f"https://www.asiagoal.com.tw/item/query/{route}"

    print(f"[{i}/{len(products)}] {route}")

    try:

        detail = requests.get(
            detail_url,
            headers=headers,
            timeout=30
        )

        detail.raise_for_status()

        detail_json = detail.json()

        result.append({
            "title": product["title"],
            "route": route,
            "summary": detail_json.get("summary", "")
        })

    except Exception as e:

        print("失敗：", e)

    # 故意慢一點，避免429
    time.sleep(1.5)


with open("products.json", "w", encoding="utf-8") as f:

    json.dump(
        result,
        f,
        ensure_ascii=False,
        indent=2
    )

print("完成！")
print(f"共輸出 {len(result)} 筆")

import requests

url = "https://www.asiagoal.com.tw/store/products?page=1&type=category&id=52139,52138,52141,52184,52193,52167&limit=16&orderType=default&hotSalePeriod=default&activeId=52139"

r = requests.get(url)

print(r.status_code)

print(r.text[:300])

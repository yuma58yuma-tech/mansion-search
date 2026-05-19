from ddgs import DDGS
import re

NAME = "フォルトゥナ秋葉原"

with DDGS() as ddgs:
    query = f'"{NAME}" site:homes.co.jp/archive'
    results = ddgs.text(query, max_results=5)
    found = ""
    for r in results:
        url = r.get("href", "")
        m = re.search(r'homes\.co\.jp/archive/(b-\d+)', url)
        if m:
            found = f"https://www.homes.co.jp/archive/{m.group(1)}/"
            break
        print(f"  候補: {url}")

if found:
    print(f"成功: {found}")
else:
    print("失敗: URLが見つかりませんでした")

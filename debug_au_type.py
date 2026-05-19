"""
cells[3]のHTML構造を確認 + ラジオボタンを押して/apartページのタイプを取得するテスト
"""
from playwright.sync_api import sync_playwright
import re

ZIP = "1010024"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        page.goto("https://bb-application.au.kddi.com/auhikari/zipcode", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        page.fill('#sendzip1', ZIP[:3])
        page.fill('#sendzip2', ZIP[3:])
        page.wait_for_timeout(300)
        page.check('#mantion')
        page.wait_for_timeout(1000)
        page.evaluate("() => { document.querySelectorAll('input[type=\"submit\"]').forEach(s => { s.classList.remove('selecthide'); s.style.removeProperty('display'); }); }")
        page.locator('input[type="submit"]').first.click(timeout=5000)
        page.wait_for_url(lambda u: "aparts" in u or "address" in u, timeout=25000)
        page.wait_for_timeout(1000)
        aparts_url = page.url
        print(f"URL: {aparts_url}\n")

        rows = page.query_selector_all("table tr")
        mansion_rows = []
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) >= 3:
                name = cells[0].inner_text().strip()
                if name and name not in {"マンション名", "物件名", "建物名"}:
                    # cells[3]の内部HTML確認
                    if len(cells) > 3:
                        inner = cells[3].inner_html()
                        print(f"【{name}】 cells[3] HTML: {inner[:150]}")
                    mansion_rows.append((name, row, cells))

        # 最初のマンションのラジオボタンをクリックしてテスト
        if mansion_rows:
            name, row, cells = mansion_rows[0]
            print(f"\n== {name} のラジオボタンをクリックテスト ==")
            radio = cells[3].query_selector("input[type='radio']") if len(cells) > 3 else None
            if radio:
                radio.click()
                page.wait_for_timeout(500)
                # 確認ボタンを探す
                for btn_text in ["確認", "次へ", "選択", "決定"]:
                    try:
                        page.click(f'text="{btn_text}"', timeout=3000)
                        print(f"  ボタン '{btn_text}' クリック成功")
                        page.wait_for_timeout(1500)
                        break
                    except:
                        pass
                print(f"  遷移後URL: {page.url}")
                if "apart" in page.url:
                    content = page.content()
                    match = re.search(r'タイプ\s*([A-Z])', content)
                    print(f"  タイプ: タイプ{match.group(1)}" if match else "  タイプ: 見つからず")
                    # タイプを含む箇所を抜粋
                    idx = content.find('タイプ')
                    if idx >= 0:
                        print(f"  タイプ周辺テキスト: {content[idx:idx+50]}")
            else:
                print("  ラジオボタンが見つかりません")
                # submitボタンなど他の方法を探す
                submits = page.query_selector_all("input[type='submit'], button")
                for s in submits:
                    print(f"  ボタン候補: {s.inner_text()[:30]} / value={s.get_attribute('value')}")

        input("\nEnterを押すと終了...")
        browser.close()

if __name__ == "__main__":
    run()

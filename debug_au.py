"""
auサイトのAPIエンドポイントを調べるデバッグスクリプト
ブラウザを実際に表示して動作を確認します
"""
from playwright.sync_api import sync_playwright
import json
import re

TEST_ZIP = "3620033"  # 上尾市栄町（スクショに写っていた郵便番号）

captured_requests = []

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # ブラウザを表示
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # ネットワークリクエストを全て記録
        page = context.new_page()

        def on_request(request):
            if "bb-application" in request.url or "kddi" in request.url.lower():
                captured_requests.append({
                    "method": request.method,
                    "url": request.url,
                    "post_data": request.post_data,
                })

        def on_response(response):
            if "bb-application" in response.url and response.status == 200:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = response.json()
                        print(f"\n=== JSON RESPONSE from {response.url} ===")
                        print(json.dumps(body, ensure_ascii=False, indent=2)[:2000])
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        print("== auサイトに移動します ==")
        page.goto("https://bb-application.au.kddi.com/auhikari/zipcode")
        page.wait_for_load_state("networkidle")

        print("\n== ページのinput要素 ==")
        inputs = page.query_selector_all("input")
        for i, inp in enumerate(inputs):
            print(f"  [{i}] type={inp.get_attribute('type')} name={inp.get_attribute('name')} id={inp.get_attribute('id')} placeholder={inp.get_attribute('placeholder')}")

        print("\n== ボタン/クリック可能要素 ==")
        for el in page.query_selector_all("button, a, [role='button'], [onclick]"):
            text = el.inner_text().strip()
            if text:
                print(f"  [{el.get_attribute('class') or ''}] {text[:50]}")

        print(f"\n== 郵便番号 {TEST_ZIP} を入力します ==")
        inputs = page.query_selector_all("input")
        text_inputs = [i for i in inputs if i.get_attribute("type") in ("text", "tel", "number", None, "")]
        print(f"テキスト系input数: {len(text_inputs)}")

        if len(text_inputs) >= 2:
            text_inputs[0].fill(TEST_ZIP[:3])
            text_inputs[1].fill(TEST_ZIP[3:])
            print(f"  前半: {TEST_ZIP[:3]}, 後半: {TEST_ZIP[3:]}")
        elif len(text_inputs) == 1:
            text_inputs[0].fill(TEST_ZIP)
            print(f"  全体: {TEST_ZIP}")

        # マンション選択
        print("\n== マンション/アパートをクリック ==")
        try:
            page.click('text=マンション', timeout=3000)
            print("  クリック成功")
        except Exception as e:
            print(f"  失敗: {e}")

        page.wait_for_timeout(500)

        # エリア確認ボタン
        print("\n== エリアを確認するをクリック ==")
        try:
            page.click('text=エリアを確認する', timeout=5000)
            print("  クリック成功")
        except Exception as e:
            print(f"  失敗: {e}")
            # 他のボタンを試す
            for btn in page.query_selector_all("button"):
                t = btn.inner_text().strip()
                print(f"  ボタン候補: {t}")

        page.wait_for_timeout(3000)
        print(f"\n== 遷移後URL: {page.url} ==")

        print("\n== 現在のページのinput/button ==")
        for el in page.query_selector_all("button, a, td, li"):
            text = el.inner_text().strip()
            if text and len(text) < 30:
                print(f"  {text}")

        print("\n== キャプチャしたリクエスト ==")
        for r in captured_requests:
            print(f"  {r['method']} {r['url']}")
            if r['post_data']:
                print(f"    body: {r['post_data'][:200]}")

        input("\nEnterを押すと終了します...")
        browser.close()

if __name__ == "__main__":
    run()

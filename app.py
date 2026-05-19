import streamlit as st
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import pandas as pd
import urllib.parse
import re
import subprocess
import sys

@st.cache_resource
def _install_playwright():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)



def get_homes_archive_urls(mansions: list) -> dict:
    """duckduckgo-searchライブラリでホームズarchiveページを検索して取得する。"""
    from ddgs import DDGS
    results = {}
    with DDGS() as ddgs:
        for m in mansions:
            name = m["マンション名"]
            found = ""
            try:
                query = f'"{name}" site:homes.co.jp/archive'
                for r in ddgs.text(query, max_results=5):
                    url = r.get("href", "")
                    m2 = re.search(r'homes\.co\.jp/archive/(b-\d+)', url)
                    if m2:
                        found = f"https://www.homes.co.jp/archive/{m2.group(1)}/"
                        break
            except Exception:
                pass
            results[name] = found
    return results


def scrape_au_mansions(postal_code: str) -> tuple:
    zip_clean = re.sub(r'[^\d]', '', postal_code)
    if len(zip_clean) != 7:
        return [], "郵便番号は7桁で入力してください"

    mansions = []
    error = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        def submit_form(pg):
            pg.goto("https://bb-application.au.kddi.com/auhikari/zipcode", timeout=30000)
            pg.wait_for_load_state("domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1500)

            pg.fill('#sendzip1', zip_clean[:3])
            pg.wait_for_timeout(200)
            pg.fill('#sendzip2', zip_clean[3:])
            pg.wait_for_timeout(300)

            pg.check('#mantion')
            pg.wait_for_timeout(1000)

            pg.evaluate("""
                () => {
                    document.querySelectorAll('input[type="submit"]').forEach(s => {
                        s.classList.remove('selecthide');
                        s.style.removeProperty('display');
                    });
                }
            """)
            pg.wait_for_timeout(300)
            pg.locator('input[type="submit"]').first.click(timeout=5000)

            try:
                pg.wait_for_url(
                    lambda url: "aparts" in url or "address" in url,
                    timeout=25000
                )
                pg.wait_for_timeout(800)
                return True
            except Exception:
                return False

        try:
            success = False
            for attempt in range(2):
                try:
                    success = submit_form(page)
                    if success:
                        break
                    page.wait_for_timeout(3000)
                except Exception:
                    if attempt == 0:
                        page.wait_for_timeout(3000)
                    continue

            if not success:
                error = "auサイトへの接続に失敗しました（時間をおいて再試行してください）"
                browser.close()
                return [], error

            def extract_mansions(pg):
                result = []
                rows = pg.query_selector_all("table tr")
                for row in rows:
                    cells = row.query_selector_all("td")
                    if len(cells) >= 3:
                        name = cells[0].inner_text().strip()
                        bldg = cells[1].inner_text().strip()
                        addr = cells[2].inner_text().strip()
                        skip = {"マンション名", "物件名", "建物名", ""}
                        if name not in skip:
                            apart_id = ""
                            if len(cells) > 3:
                                radio = cells[3].query_selector("input[type='radio']")
                                if radio:
                                    apart_id = radio.get_attribute("value") or ""
                            result.append({
                                "マンション名": name,
                                "棟名": bldg,
                                "住所": addr,
                                "郵便番号": f"{zip_clean[:3]}-{zip_clean[3:]}",
                                "_apart_id": apart_id,
                            })
                return result

            def fetch_types(pg, mansion_list):
                """ラジオボタン選択→次へ→/apart でタイプを取得する"""
                aparts_url = pg.url
                for m in mansion_list:
                    apart_id = m.pop("_apart_id", "")
                    m["タイプ"] = ""
                    if not apart_id:
                        continue
                    try:
                        radio = pg.query_selector(f"input[name='apart_id'][value='{apart_id}']")
                        if not radio:
                            continue
                        radio.click()
                        pg.wait_for_timeout(300)
                        pg.click('text="次へ"', timeout=5000)
                        pg.wait_for_url(lambda u: "apart" in u and "aparts" not in u, timeout=10000)
                        try:
                            pg.wait_for_function(
                                "() => document.body.innerText.includes('対応サービス')",
                                timeout=8000
                            )
                        except Exception:
                            pg.wait_for_timeout(3000)
                        content = pg.content()
                        match = re.search(r'タイプ([GVEMU])', content)
                        if match:
                            m["タイプ"] = f"タイプ{match.group(1)}"
                        pg.go_back()
                        pg.wait_for_load_state("domcontentloaded", timeout=10000)
                        pg.wait_for_timeout(500)
                    except Exception:
                        try:
                            pg.goto(aparts_url, timeout=15000)
                            pg.wait_for_load_state("domcontentloaded", timeout=10000)
                            pg.wait_for_timeout(500)
                        except Exception:
                            pass
                return mansion_list

            url = page.url

            if "aparts" in url:
                mansions = extract_mansions(page)
                mansions = fetch_types(page, mansions)

            elif "address" in url:
                chome_texts = []
                for el in page.query_selector_all("td, a"):
                    t = el.inner_text().strip()
                    if re.search(r'\d+丁目', t) or re.match(r'^\d+$', t):
                        if t not in chome_texts:
                            chome_texts.append(t)

                address_url = page.url
                for ct in chome_texts:
                    try:
                        page.click(f'text="{ct}"', timeout=5000)
                        try:
                            page.wait_for_url(lambda u: "aparts" in u, timeout=10000)
                        except Exception:
                            page.wait_for_load_state("load", timeout=10000)
                        page.wait_for_timeout(500)
                        if "aparts" in page.url:
                            chunk = extract_mansions(page)
                            chunk = fetch_types(page, chunk)
                            mansions.extend(chunk)
                        page.goto(address_url)
                        try:
                            page.wait_for_url(lambda u: "address" in u, timeout=10000)
                        except Exception:
                            page.wait_for_load_state("load", timeout=10000)
                        page.wait_for_timeout(500)
                    except Exception:
                        continue
            else:
                error = f"予期しないページ: {url}"

        except PWTimeout:
            error = "タイムアウト: auサイトへの接続に失敗しました"
        except Exception as e:
            error = f"エラー: {e}"
        finally:
            browser.close()

    return mansions, error


def maps_url(name: str, addr: str) -> str:
    query = urllib.parse.quote(f"{name} {addr}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def main():
    _install_playwright()
    st.set_page_config(page_title="マンション調べツール", layout="wide")
    st.title("マンション調べ効率化ツール")
    st.caption("auひかり提供エリアのマンションを一括取得。ホームズの建物ページとGoogleマップを直接開けます。")

    postal_input = st.text_area(
        "郵便番号を入力（1行に1つ、最大5件）",
        placeholder="362-0031\n362-0033\n362-0035",
        height=150,
    )

    if st.button("検索開始", type="primary"):
        postal_codes = [p.strip() for p in postal_input.strip().split("\n") if p.strip()][:5]

        if not postal_codes:
            st.error("郵便番号を入力してください")
            return

        all_mansions = []
        progress = st.progress(0)
        status = st.empty()
        errors = []

        for i, pc in enumerate(postal_codes):
            status.text(f"auサイト検索中: {pc}　（{i + 1} / {len(postal_codes)} 件目）")
            mansions, err = scrape_au_mansions(pc)
            if err:
                errors.append(f"{pc}: {err}")
            all_mansions.extend(mansions)
            progress.progress((i + 1) / len(postal_codes))

        if all_mansions:
            status.text(f"ホームズのURLを取得中... （{len(all_mansions)}件）")
            homes_urls = get_homes_archive_urls(all_mansions)
            for m in all_mansions:
                m["ホームズURL"] = homes_urls.get(m["マンション名"], "")

        progress.empty()
        for e in errors:
            st.warning(e)

        if all_mansions:
            status.success(f"完了！{len(all_mansions)} 件見つかりました")
            st.session_state["df"] = pd.DataFrame(all_mansions)
        else:
            status.warning("マンションが見つかりませんでした")

    if "df" in st.session_state:
        df = st.session_state["df"].copy()

        st.divider()
        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.subheader(f"結果：{len(df)} 件")
        with col_b:
            cols = [c for c in ["郵便番号", "マンション名", "棟名", "タイプ", "住所", "ホームズURL"] if c in df.columns]
            csv = df[cols].to_csv(index=False, encoding="utf-8-sig")
            st.download_button("CSVダウンロード", csv, "mansions.csv", "text/csv")

        for _, row in df.iterrows():
            c1, c2, c3 = st.columns([4, 2, 2])
            with c1:
                label = row["マンション名"]
                if row["棟名"]:
                    label += f"　{row['棟名']}"
                type_badge = f"　`{row['タイプ']}`" if row.get("タイプ") else ""
                st.write(f"**{label}**{type_badge}")
                st.caption(f"〒{row['郵便番号']}　{row['住所']}")
            with c2:
                if row["ホームズURL"]:
                    st.link_button("ホームズで確認", row["ホームズURL"], use_container_width=True)
                else:
                    st.button("ホームズ（未取得）", disabled=True, use_container_width=True, key=f"homes_{_}")
            with c3:
                st.link_button("Googleマップ", maps_url(row["マンション名"], row["住所"]), use_container_width=True)
            st.divider()


if __name__ == "__main__":
    main()

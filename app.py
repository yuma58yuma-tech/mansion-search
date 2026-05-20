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
    """複数の戦略でホームズのarchiveページURLを取得する。"""
    import requests
    from bs4 import BeautifulSoup
    from ddgs import DDGS
    import time

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    def extract_b_url(text: str) -> str:
        m = re.search(r'homes\.co\.jp/archive/(b-\d+)', text)
        return f"https://www.homes.co.jp/archive/{m.group(1)}/" if m else ""

    def ddg_search(ddgs_obj, query: str) -> str:
        try:
            for r in ddgs_obj.text(query, max_results=5):
                found = extract_b_url(r.get("href", ""))
                if found:
                    return found
        except Exception:
            pass
        return ""

    def bing_search(name: str) -> str:
        """Bing検索でホームズのarchiveページを探す"""
        try:
            q = urllib.parse.quote(f'"{name}" site:homes.co.jp/archive')
            res = requests.get(f"https://www.bing.com/search?q={q}", headers=HEADERS, timeout=10)
            found = extract_b_url(res.text)
            if found:
                return found
            # 引用符なしでも試す
            q2 = urllib.parse.quote(f'{name} site:homes.co.jp/archive')
            res2 = requests.get(f"https://www.bing.com/search?q={q2}", headers=HEADERS, timeout=10)
            return extract_b_url(res2.text)
        except Exception:
            pass
        return ""

    def homes_direct_search(name: str) -> str:
        try:
            q = urllib.parse.quote(name)
            url = f"https://www.homes.co.jp/archive/search/?q={q}"
            res = requests.get(url, headers=HEADERS, timeout=10)
            found = extract_b_url(res.url)
            if found:
                return found
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                found = extract_b_url(a['href'])
                if found:
                    return found
        except Exception:
            pass
        return ""

    def normalize(name: str) -> str:
        return re.sub(r'[　\s]', ' ', name).strip()

    def get_name_variants(name: str) -> list:
        """数字表記のバリエーションを生成（1,１,Ⅰ,I など）"""
        variants = [name]
        # 半角数字→全角
        zen = name.translate(str.maketrans('0123456789', '０１２３４５６７８９'))
        if zen != name:
            variants.append(zen)
        # 全角数字→半角
        han = name.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
        if han != name:
            variants.append(han)
        # 半角数字→全角ローマ数字
        roman_zen = {'10':'Ⅹ','9':'Ⅸ','8':'Ⅷ','7':'Ⅶ','6':'Ⅵ','5':'Ⅴ','4':'Ⅳ','3':'Ⅲ','2':'Ⅱ','1':'Ⅰ'}
        roman_han = {'10':'X','9':'IX','8':'VIII','7':'VII','6':'VI','5':'V','4':'IV','3':'III','2':'II','1':'I'}
        for src, dst_map in [(han, roman_zen), (han, roman_han)]:
            v = src
            for num, roman in sorted(dst_map.items(), key=lambda x: -int(x[0])):
                v = re.sub(r'(?<![0-9])' + num + r'(?![0-9])', roman, v)
            if v != name:
                variants.append(v)
        # 全角ローマ数字→半角数字
        rev = {'Ⅹ':'10','Ⅸ':'9','Ⅷ':'8','Ⅶ':'7','Ⅵ':'6','Ⅴ':'5','Ⅳ':'4','Ⅲ':'3','Ⅱ':'2','Ⅰ':'1'}
        v = name
        for roman, num in rev.items():
            v = v.replace(roman, num)
        if v != name:
            variants.append(v)
        # 半角ローマ数字→半角数字（末尾のみ）
        rev2 = [('VIII','8'),('VII','7'),('VI','6'),('IV','4'),('IX','9'),('III','3'),('II','2'),('XI','11'),('X','10'),('V','5'),('I','1')]
        v = name
        for roman, num in rev2:
            v = re.sub(r'(?<![A-Z])' + roman + r'(?![A-Z])', num, v)
        if v != name:
            variants.append(v)
        return list(dict.fromkeys(variants))  # 重複除去

    results = {}
    with DDGS() as ddgs:
        for m in mansions:
            name = m["マンション名"]
            addr = m.get("住所", "")
            found = ""
            variants = get_name_variants(name)

            # 戦略1-2: 全バリエーションでDDG検索（/archive限定）
            for v in variants:
                if found: break
                found = ddg_search(ddgs, f'"{v}" site:homes.co.jp/archive')
                if not found and addr:
                    found = ddg_search(ddgs, f'"{v}" {addr} site:homes.co.jp/archive')

            # 戦略3: 引用符なし + /archive
            if not found:
                for v in variants:
                    if found: break
                    found = ddg_search(ddgs, f'{normalize(v)} site:homes.co.jp/archive')

            # 戦略4: homes.co.jp全体
            if not found:
                for v in variants:
                    if found: break
                    found = ddg_search(ddgs, f'"{v}" site:homes.co.jp')

            # 戦略5: Bing検索（全バリエーション）
            if not found:
                for v in variants:
                    if found: break
                    found = bing_search(v)

            # 戦略6: ホームズ検索ページを直接スクレイプ
            if not found:
                for v in variants:
                    if found: break
                    found = homes_direct_search(v)

            results[name] = found
            time.sleep(0.5)

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
    st.caption("auひかり提供エリアのマンションを一括取得。ホームズの建物ページを直接開けます。")

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
        st.subheader(f"結果：{len(df)} 件")

        for _, row in df.iterrows():
            c1, c2 = st.columns([4, 2])
            with c1:
                label = row["マンション名"]
                if row["棟名"]:
                    label += f"　{row['棟名']}"
                type_badge = f"　`{row['タイプ']}`" if row.get("タイプ") else ""
                st.write(f"**{label}**{type_badge}")
                st.caption(f"〒{row['郵便番号']}　{row['住所']}")
                st.code(f"{row['マンション名']} {row['住所']}", language=None)
            with c2:
                if row["ホームズURL"]:
                    st.link_button("ホームズで確認", row["ホームズURL"], use_container_width=True)
                else:
                    search_url = f"https://www.homes.co.jp/archive/search/?q={urllib.parse.quote(row['マンション名'])}"
                    st.link_button("ホームズで検索", search_url, use_container_width=True)
            st.divider()


if __name__ == "__main__":
    main()

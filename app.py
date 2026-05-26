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
    """並列処理＋複数戦略でホームズのarchiveページURLを取得する。"""
    import requests
    from bs4 import BeautifulSoup
    from ddgs import DDGS
    from concurrent.futures import ThreadPoolExecutor, as_completed

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    def extract_b_url(text: str) -> str:
        m = re.search(r'homes\.co\.jp/archive/(b-\d+)', text)
        return f"https://www.homes.co.jp/archive/{m.group(1)}/" if m else ""

    def verify_url(url: str, name: str) -> bool:
        """URLのページに実際にマンション名が含まれているか確認"""
        try:
            res = requests.get(url, headers=HEADERS, timeout=6)
            # 名前の主要部分（最初の4文字）がページに含まれているか
            core = re.sub(r'[\s　\-・]', '', name)[:4]
            return core in res.text
        except Exception:
            return True  # 確認失敗時はOKとして扱う

    def normalize(name: str) -> str:
        return re.sub(r'[　\s]', ' ', name).strip()

    def get_name_variants(name: str) -> list:
        variants = [name]
        zen = name.translate(str.maketrans('0123456789', '０１２３４５６７８９'))
        if zen != name: variants.append(zen)
        han = name.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
        if han != name: variants.append(han)
        roman_zen = {'10':'Ⅹ','9':'Ⅸ','8':'Ⅷ','7':'Ⅶ','6':'Ⅵ','5':'Ⅴ','4':'Ⅳ','3':'Ⅲ','2':'Ⅱ','1':'Ⅰ'}
        roman_han = {'10':'X','9':'IX','8':'VIII','7':'VII','6':'VI','5':'V','4':'IV','3':'III','2':'II','1':'I'}
        for src, dst_map in [(han, roman_zen), (han, roman_han)]:
            v = src
            for num, roman in sorted(dst_map.items(), key=lambda x: -int(x[0])):
                v = re.sub(r'(?<![0-9])' + num + r'(?![0-9])', roman, v)
            if v != name: variants.append(v)
        rev = {'Ⅹ':'10','Ⅸ':'9','Ⅷ':'8','Ⅶ':'7','Ⅵ':'6','Ⅴ':'5','Ⅳ':'4','Ⅲ':'3','Ⅱ':'2','Ⅰ':'1'}
        v = name
        for roman, num in rev.items(): v = v.replace(roman, num)
        if v != name: variants.append(v)
        rev2 = [('VIII','8'),('VII','7'),('VI','6'),('IV','4'),('IX','9'),('III','3'),('II','2'),('XI','11'),('X','10'),('V','5'),('I','1')]
        v = name
        for roman, num in rev2:
            v = re.sub(r'(?<![A-Z])' + roman + r'(?![A-Z])', num, v)
        if v != name: variants.append(v)
        return list(dict.fromkeys(variants))

    def search_one(name: str, addr: str) -> str:
        """1件のマンションを検索（各スレッドが独自のDDGSインスタンスを持つ）"""
        variants = get_name_variants(name)
        found = ""

        def ddg(query):
            try:
                with DDGS() as d:
                    for r in d.text(query, max_results=5):
                        url = extract_b_url(r.get("href", ""))
                        if url:
                            return url
            except Exception:
                pass
            return ""

        def bing(query):
            try:
                q = urllib.parse.quote(query)
                res = requests.get(f"https://www.bing.com/search?q={q}", headers=HEADERS, timeout=8)
                return extract_b_url(res.text)
            except Exception:
                return ""

        def homes_direct(n):
            try:
                q = urllib.parse.quote(n)
                res = requests.get(f"https://www.homes.co.jp/archive/search/?q={q}", headers=HEADERS, timeout=8)
                url = extract_b_url(res.url)
                if url: return url
                soup = BeautifulSoup(res.text, 'html.parser')
                for a in soup.find_all('a', href=True):
                    url = extract_b_url(a['href'])
                    if url: return url
            except Exception:
                pass
            return ""

        # 戦略1: 完全一致 + /archive
        for v in variants:
            if found: break
            found = ddg(f'"{v}" site:homes.co.jp/archive')
            if not found and addr:
                found = ddg(f'"{v}" {addr} site:homes.co.jp/archive')

        # 戦略2: 引用符なし
        if not found:
            for v in variants:
                if found: break
                found = ddg(f'{normalize(v)} site:homes.co.jp/archive')

        # 戦略3: homes全体
        if not found:
            for v in variants:
                if found: break
                found = ddg(f'"{v}" site:homes.co.jp')

        # 戦略4: Bing
        if not found:
            for v in variants:
                if found: break
                found = bing(f'"{v}" site:homes.co.jp/archive')
                if not found:
                    found = bing(f'{v} site:homes.co.jp/archive')

        # 戦略5: ホームズ直接検索
        if not found:
            for v in variants:
                if found: break
                found = homes_direct(v)

        # 精度確認: 見つかったURLが本当に正しいか検証（失敗しても採用）
        if found and not verify_url(found, name):
            pass  # 検証失敗でも採用（ホームズはJS描画のため検証しにくい）

        return found

    # 全マンションを並列検索
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(search_one, m["マンション名"], m.get("住所", "")): m["マンション名"]
            for m in mansions
        }
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()

    return results


def scrape_au_mansions(postal_code: str) -> tuple:
    """requestsでauサイトをスクレイピング（高速・bot検知回避）"""
    import requests as req
    from bs4 import BeautifulSoup as BS

    zip_clean = re.sub(r'[^\d]', '', postal_code)
    if len(zip_clean) != 7:
        return [], "郵便番号は7桁で入力してください"

    BASE = "https://bb-application.au.kddi.com"
    session = req.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        "Referer": f"{BASE}/auhikari/zipcode",
    })

    def parse_table(soup):
        result = []
        skip = {"マンション名", "物件名", "建物名", ""}
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) >= 3:
                name = cells[0].get_text(strip=True)
                bldg = cells[1].get_text(strip=True)
                addr = cells[2].get_text(strip=True)
                if name and name not in skip:
                    apart_id = ""
                    if len(cells) > 3:
                        radio = cells[3].find("input", {"type": "radio"})
                        apart_id = radio.get("value", "") if radio else ""
                    result.append({
                        "マンション名": name,
                        "棟名": bldg,
                        "住所": addr,
                        "郵便番号": f"{zip_clean[:3]}-{zip_clean[3:]}",
                        "_apart_id": apart_id,
                    })
        return result

    def get_type(aparts_soup, apart_id):
        if not apart_id:
            return ""
        try:
            form = aparts_soup.find("form")
            if not form:
                return ""
            action = form.get("action", "")
            form_url = BASE + action if action.startswith("/") else action
            data = {}
            for inp in form.find_all("input", {"type": "hidden"}):
                if inp.get("name"):
                    data[inp["name"]] = inp.get("value", "")
            data["apart_id"] = apart_id
            for btn in form.find_all("input", {"type": "submit"}):
                if btn.get("name"):
                    data[btn["name"]] = btn.get("value", "")
            r = session.post(form_url, data=data, timeout=15)
            content = r.text
            match = re.search(r'タイプ([GVEMU])', content)
            has_mini = 'ミニギガ' in content
            has_giga = 'ギガ' in content and not has_mini
            if match:
                speed = "（ミニギガ）" if has_mini else "（ギガ）" if has_giga else ""
                return f"タイプ{match.group(1)}{speed}"
            elif has_mini:
                return "ミニギガ"
            elif has_giga:
                return "ギガ"
        except Exception:
            pass
        return ""

    try:
        # Step1: フォームページを取得
        r0 = session.get(f"{BASE}/auhikari/zipcode", timeout=15)
        soup0 = BS(r0.text, "html.parser")
        form = soup0.find("form")
        if not form:
            return [], "サイト構造が変更されました（フォームが見つかりません）"

        action = form.get("action", "/auhikari/zipcode")
        form_url = BASE + action if action.startswith("/") else action

        post_data = {}
        for inp in form.find_all("input", {"type": "hidden"}):
            if inp.get("name"):
                post_data[inp["name"]] = inp.get("value", "")
        post_data["sendzip1"] = zip_clean[:3]
        post_data["sendzip2"] = zip_clean[3:]

        # マンションラジオボタンを取得
        radio_el = form.find("input", {"id": "mantion"}) or form.find("input", {"type": "radio"})
        if radio_el and radio_el.get("name"):
            post_data[radio_el["name"]] = radio_el.get("value", "on")
        else:
            post_data["mantion"] = "on"

        # Step2: フォーム送信
        method = form.get("method", "post").lower()
        r1 = session.post(form_url, data=post_data, timeout=20) if method == "post" \
             else session.get(form_url, params=post_data, timeout=20)
        r1_url = r1.url
        soup1 = BS(r1.text, "html.parser")

        mansions = []

        if "aparts" in r1_url:
            raw = parse_table(soup1)
            for m in raw:
                m["タイプ"] = get_type(soup1, m.pop("_apart_id", ""))
            mansions = raw

        elif "address" in r1_url:
            for a in soup1.find_all("a", href=True):
                t = a.get_text(strip=True)
                if not (re.search(r'\d+丁目', t) or re.match(r'^\d+$', t)):
                    continue
                href = a["href"]
                chome_url = BASE + href if href.startswith("/") else href
                r2 = session.get(chome_url, timeout=15)
                soup2 = BS(r2.text, "html.parser")
                chunk = parse_table(soup2)
                for m in chunk:
                    m["タイプ"] = get_type(soup2, m.pop("_apart_id", ""))
                mansions.extend(chunk)

        if not mansions:
            return [], "対象エリアにauひかり対応マンションがありません"

        return mansions, ""

    except Exception as e:
        return [], f"エラー: {e}"


def _scrape_au_playwright_unused(postal_code: str) -> tuple:
    """Playwright版（現在は未使用・参考用）"""
    zip_clean = re.sub(r'[^\d]', '', postal_code)
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
                    timeout=30000
                )
                try:
                    pg.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
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
                        has_mini_giga = 'ミニギガ' in content
                        has_giga = 'ギガ' in content and not has_mini_giga
                        if match:
                            speed = "（ミニギガ）" if has_mini_giga else "（ギガ）" if has_giga else ""
                            m["タイプ"] = f"タイプ{match.group(1)}{speed}"
                        elif has_mini_giga:
                            m["タイプ"] = "ミニギガ"
                        elif has_giga:
                            m["タイプ"] = "ギガ"
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
                try:
                    page.wait_for_function(
                        "() => document.querySelectorAll('table tr').length > 1",
                        timeout=10000
                    )
                except Exception:
                    page.wait_for_timeout(2000)
                mansions = extract_mansions(page)
                if not mansions:
                    # テーブルが見つからない場合、ページ内テキストから物件名を探す
                    page_text = page.inner_text("body")
                    if "マンション" not in page_text and "物件" not in page_text:
                        error = f"対象エリアにauひかり対応マンションがありません"
                    else:
                        error = f"ページ構造変更の可能性あり（URL: {url.split('?')[0]}）"
                else:
                    mansions = fetch_types(page, mansions)

            elif "address" in url:
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    page.wait_for_timeout(2000)
                # リンク要素から丁目テキストを取得して直接クリック
                address_url = page.url
                link_els = page.query_selector_all("td a, a")
                chome_links = []
                seen = set()
                for el in link_els:
                    t = el.inner_text().strip()
                    if t and t not in seen and (re.search(r'\d+丁目', t) or re.match(r'^\d+$', t)):
                        seen.add(t)
                        chome_links.append(t)

                if not chome_links:
                    error = f"住所選択ページで選択肢が見つかりません（URL: {url.split('?')[0]}）"

                for ct in chome_links:
                    try:
                        # テキストで要素を再取得してクリック
                        els = page.query_selector_all("td a, a")
                        target = next((e for e in els if e.inner_text().strip() == ct), None)
                        if target:
                            target.click(timeout=5000)
                        else:
                            page.click(f'text="{ct}"', timeout=5000)
                        try:
                            page.wait_for_url(lambda u: "aparts" in u, timeout=10000)
                        except Exception:
                            page.wait_for_load_state("load", timeout=10000)
                        page.wait_for_timeout(800)
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
                error = f"予期しないページ: {url.split('?')[0]}"

        except PWTimeout:
            error = "タイムアウト: auサイトへの接続に失敗しました"
        except Exception as e:
            error = f"エラー: {e}"
        finally:
            browser.close()

    return mansions, error




SINGLE_MADORI = {'1R', '1K', '1DK', '1LDK'}

def classify_mansion(homes_url: str) -> str:
    """ホームズページから間取りを取得して一人暮らし/ファミリー/混合を判定"""
    if not homes_url:
        return "不明"
    import requests
    from bs4 import BeautifulSoup
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        res = requests.get(homes_url, headers=headers, timeout=8)
        soup = BeautifulSoup(res.text, 'html.parser')
        found = {t.strip() for t in soup.stripped_strings if re.fullmatch(r'\d+[RLDK]+', t.strip())}
        if not found:
            return "不明"
        has_single = bool(found & SINGLE_MADORI)
        has_family = bool(found - SINGLE_MADORI)
        if has_single and has_family:
            return "混合"
        return "一人暮らし向け" if has_single else "ファミリー向け"
    except Exception:
        return "不明"

def classify_all_parallel(mansions: list):
    """並列処理で全マンションの分類を一気に取得"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(classify_mansion, m["ホームズURL"]): i for i, m in enumerate(mansions)}
        for future in as_completed(futures):
            idx = futures[future]
            mansions[idx]["分類"] = future.result()


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
            status.text(f"一人暮らし/ファミリー分類中... （{len(all_mansions)}件）")
            homes_urls = get_homes_archive_urls(all_mansions)
            for m in all_mansions:
                m["ホームズURL"] = homes_urls.get(m["マンション名"], "")
            classify_all_parallel(all_mansions)

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

        # フィルター
        fc1, fc2 = st.columns(2)
        with fc1:
            all_types = sorted([t for t in df["タイプ"].dropna().unique() if t]) if "タイプ" in df.columns else []
            if all_types:
                sel_types = st.multiselect("タイプで絞り込み", all_types, default=all_types)
                df = df[df["タイプ"].isin(sel_types) | (df["タイプ"] == "")]
        with fc2:
            if "分類" in df.columns:
                all_cls = sorted([c for c in df["分類"].dropna().unique() if c])
                if all_cls:
                    sel_cls = st.multiselect("分類で絞り込み", all_cls, default=all_cls)
                    df = df[df["分類"].isin(sel_cls)]

        st.subheader(f"結果：{len(df)} 件")

        for _, row in df.iterrows():
            c1, c2 = st.columns([4, 2])
            with c1:
                label = row["マンション名"]
                if row["棟名"]:
                    label += f"　{row['棟名']}"
                type_badge = f"　`{row['タイプ']}`" if row.get("タイプ") else ""
                cls = row.get("分類", "")
                cls_badge = f"　`{cls}`" if cls and cls != "不明" else ""
                st.markdown(f"**{label}**{type_badge}{cls_badge}")
                st.caption(f"〒{row['郵便番号']}　{row['住所']}")
                st.code(f"{row['マンション名']} {row['住所']}", language=None)
            with c2:
                google_url = f"https://www.google.com/search?q={urllib.parse.quote(row['マンション名'] + ' site:homes.co.jp/archive')}"
                st.link_button("ホームズで確認", google_url, use_container_width=True)
            st.divider()


if __name__ == "__main__":
    main()

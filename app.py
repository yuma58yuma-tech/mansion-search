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

    results = {}
    with DDGS() as ddgs:
        for m in mansions:
            name = m["マンション名"]
            addr = m.get("住所", "")
            found = ""

            # 戦略1: 名前完全一致 + /archive
            found = ddg_search(ddgs, f'"{name}" site:homes.co.jp/archive')

            # 戦略2: 名前 + 住所 + /archive
            if not found and addr:
                found = ddg_search(ddgs, f'"{name}" {addr} site:homes.co.jp/archive')

            # 戦略3: 引用符なし + /archive
            if not found:
                found = ddg_search(ddgs, f'{normalize(name)} site:homes.co.jp/archive')

            # 戦略4: 名前 + homes.co.jp 全体
            if not found:
                found = ddg_search(ddgs, f'"{name}" site:homes.co.jp')

            # 戦略5: ホームズ検索ページを直接スクレイプ
            if not found:
                found = homes_direct_search(name)

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


SINGLE_MADORI = {'1R', '1K', '1DK', '1LDK'}

def scrape_homes_details(homes_url: str) -> dict:
    """ホームズのアーカイブページから建物情報をまとめて取得する"""
    result = {"分類": "不明", "間取り": "", "専有面積": "", "築年数": "", "階数": ""}
    if not homes_url:
        return result
    import requests
    from bs4 import BeautifulSoup
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        res = requests.get(homes_url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        all_texts = list(soup.stripped_strings)
        full_text = " ".join(all_texts)

        # 間取り
        madori_found = set()
        for t in all_texts:
            if re.fullmatch(r'\d+[RLDK]+', t.strip()):
                madori_found.add(t.strip())
        if madori_found:
            result["間取り"] = "・".join(sorted(madori_found))
            has_single = bool(madori_found & SINGLE_MADORI)
            has_family = bool(madori_found - SINGLE_MADORI)
            if has_single and has_family:
                result["分類"] = "混合"
            elif has_single:
                result["分類"] = "一人暮らし向け"
            else:
                result["分類"] = "ファミリー向け"

        # 専有面積（例: 25.5㎡ や 30㎡）
        areas = re.findall(r'\d+(?:\.\d+)?㎡', full_text)
        if areas:
            nums = sorted(set(areas), key=lambda x: float(re.search(r'[\d.]+', x).group()))
            result["専有面積"] = f"{nums[0]}〜{nums[-1]}" if len(nums) > 1 else nums[0]

        # 築年数（例: 築14年 / 2010年築）
        m = re.search(r'築(\d+)年', full_text)
        if m:
            result["築年数"] = f"築{m.group(1)}年"
        else:
            m2 = re.search(r'(\d{4})年築', full_text)
            if m2:
                import datetime
                age = datetime.date.today().year - int(m2.group(1))
                result["築年数"] = f"築{age}年"

        # 階数（例: 6階建）
        m = re.search(r'(\d+)階建', full_text)
        if m:
            result["階数"] = f"{m.group(1)}階建"

    except Exception:
        pass
    return result


def maps_url(name: str, addr: str) -> str:
    query = urllib.parse.quote(f"{name} {addr}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def main():
    _install_playwright()
    st.set_page_config(page_title="マンション調べツール", layout="wide")
    st.title("マンション調べ効率化ツール")
    st.caption("auひかり提供エリアのマンションを一括取得。ホームズの建物ページとGoogleマップを直接開けます。")

    if "history" not in st.session_state:
        st.session_state["history"] = []
    if "postal_input" not in st.session_state:
        st.session_state["postal_input"] = ""

    # 検索履歴
    if st.session_state["history"]:
        st.markdown("**検索履歴**")
        cols = st.columns(min(len(st.session_state["history"]), 5))
        for i, entry in enumerate(reversed(st.session_state["history"][-5:])):
            label = " / ".join(entry)
            if cols[i].button(label, key=f"hist_{i}"):
                st.session_state["postal_input"] = "\n".join(entry)
                st.rerun()

    postal_input = st.text_area(
        "郵便番号を入力（1行に1つ、最大5件）",
        value=st.session_state["postal_input"],
        placeholder="362-0031\n362-0033\n362-0035",
        height=150,
        key="postal_textarea",
    )

    if st.button("検索開始", type="primary"):
        postal_codes = [p.strip() for p in postal_input.strip().split("\n") if p.strip()][:5]

        if not postal_codes:
            st.error("郵便番号を入力してください")
            return

        # 履歴に追加（同じ組み合わせは重複しない）
        if postal_codes not in st.session_state["history"]:
            st.session_state["history"].append(postal_codes)

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

            status.text(f"ホームズから建物情報を取得中... （{len(all_mansions)}件）")
            for m in all_mansions:
                details = scrape_homes_details(m["ホームズURL"])
                m.update(details)

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

        # フィルター行
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            all_types = sorted(df["タイプ"].dropna().unique().tolist()) if "タイプ" in df.columns else []
            all_types = [t for t in all_types if t]
            if all_types:
                selected_types = st.multiselect(
                    "タイプで絞り込み",
                    options=all_types,
                    default=all_types,
                    placeholder="タイプを選択...",
                )
                df = df[df["タイプ"].isin(selected_types) | (df["タイプ"] == "")]
        with filter_col2:
            if "分類" in df.columns:
                all_cls = sorted(df["分類"].dropna().unique().tolist())
                selected_cls = st.multiselect(
                    "分類で絞り込み",
                    options=all_cls,
                    default=all_cls,
                    placeholder="分類を選択...",
                )
                df = df[df["分類"].isin(selected_cls)]

        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.subheader(f"結果：{len(df)} 件")
        with col_b:
            cols = [c for c in ["郵便番号", "マンション名", "棟名", "タイプ", "住所", "ホームズURL"] if c in df.columns]
            csv = df[cols].to_csv(index=False, encoding="utf-8-sig")
            st.download_button("CSVダウンロード", csv, "mansions.csv", "text/csv")

        for _, row in df.iterrows():
            c1, c2 = st.columns([4, 2])
            with c1:
                label = row["マンション名"]
                if row["棟名"]:
                    label += f"　{row['棟名']}"
                type_badge = f"　`{row['タイプ']}`" if row.get("タイプ") else ""
                cls = row.get("分類", "")
                cls_badge = f"　`{cls}`" if cls and cls != "不明" else ""
                st.write(f"**{label}**{type_badge}{cls_badge}")
                # 建物詳細
                details_parts = []
                if row.get("築年数"): details_parts.append(row["築年数"])
                if row.get("階数"):   details_parts.append(row["階数"])
                if row.get("間取り"): details_parts.append(row["間取り"])
                if row.get("専有面積"): details_parts.append(row["専有面積"])
                if details_parts:
                    st.caption("　".join(details_parts))
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

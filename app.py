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


def scrape_au_mansions(postal_code: str) -> tuple:
    zip_clean = re.sub(r'[^\d]', '', postal_code)
    if len(zip_clean) != 7:
        return [], "郵便番号は7桁で入力してください"

    mansions = []
    error = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except Exception:
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        def submit_form(pg):
            pg.goto("https://bb-application.au.kddi.com/auhikari/zipcode", timeout=30000)
            pg.wait_for_load_state("domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1500)
            pg.fill('#sendzip1', zip_clean[:3])
            pg.wait_for_timeout(300)
            pg.fill('#sendzip2', zip_clean[3:])
            pg.wait_for_timeout(500)
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
            pg.wait_for_timeout(500)
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
                browser.close()
                return [], "auサイトへの接続に失敗しました（時間をおいて再試行してください）"

            def extract_mansions(pg):
                result = []
                rows = pg.query_selector_all("table tr")
                for row in rows:
                    cells = row.query_selector_all("td")
                    if len(cells) >= 3:
                        name = cells[0].inner_text().strip()
                        bldg = cells[1].inner_text().strip()
                        addr = cells[2].inner_text().strip()
                        if name not in {"マンション名", "物件名", "建物名", ""}:
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
                        has_mini = 'ミニギガ' in content
                        has_giga = 'ギガ' in content and not has_mini
                        if match:
                            speed = "（ミニギガ）" if has_mini else "（ギガ）" if has_giga else ""
                            m["タイプ"] = f"タイプ{match.group(1)}{speed}"
                        elif has_mini:
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
                    page.wait_for_selector("table tr td", timeout=10000)
                except Exception:
                    page.wait_for_timeout(3000)
                mansions = extract_mansions(page)
                if mansions:
                    mansions = fetch_types(page, mansions)

            elif "address" in url:
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    page.wait_for_timeout(2000)
                address_url = page.url
                link_els = page.query_selector_all("td a, a")
                seen = set()
                chome_links = []
                for el in link_els:
                    t = el.inner_text().strip()
                    if t and t not in seen and (re.search(r'\d+丁目', t) or re.match(r'^\d+$', t)):
                        seen.add(t)
                        chome_links.append(t)
                for ct in chome_links:
                    try:
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
                error = "auサイトへの接続に失敗しました（時間をおいて再試行してください）"

        except PWTimeout:
            error = "タイムアウト: auサイトへの接続に失敗しました"
        except Exception as e:
            error = f"エラー: {e}"
        finally:
            browser.close()

    return mansions, error


def main():
    _install_playwright()
    st.set_page_config(page_title="マンション調べツール", layout="wide")
    st.title("マンション調べ効率化ツール")
    st.caption("auひかり提供エリアのマンションを一括取得")

    postal_input = st.text_area(
        "郵便番号を入力（1行に1つ、最大5件）",
        placeholder="362-0031\n362-0033",
        height=130,
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
            status.text(f"検索中: {pc}　（{i + 1} / {len(postal_codes)} 件目）")
            mansions, err = scrape_au_mansions(pc)
            if err:
                errors.append(f"{pc}: {err}")
            all_mansions.extend(mansions)
            progress.progress((i + 1) / len(postal_codes))

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

        # タイプで絞り込み
        all_types = sorted([t for t in df["タイプ"].dropna().unique() if t]) if "タイプ" in df.columns else []
        if all_types:
            sel_types = st.multiselect("タイプで絞り込み", all_types, default=all_types)
            df = df[df["タイプ"].isin(sel_types) | (df["タイプ"] == "")]

        st.subheader(f"結果：{len(df)} 件")

        for _, row in df.iterrows():
            c1, c2 = st.columns([4, 2])
            with c1:
                label = row["マンション名"]
                if row.get("棟名"):
                    label += f"　{row['棟名']}"
                type_badge = f"　`{row['タイプ']}`" if row.get("タイプ") else ""
                st.markdown(f"**{label}**{type_badge}")
                st.caption(f"〒{row['郵便番号']}　{row['住所']}")
                st.code(f"{row['マンション名']} {row['住所']}", language=None)
            with c2:
                google_url = f"https://www.google.com/search?q={urllib.parse.quote(row['マンション名'] + ' site:homes.co.jp/archive')}"
                st.link_button("ホームズで検索", google_url, use_container_width=True)
            st.divider()


if __name__ == "__main__":
    main()

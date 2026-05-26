import streamlit as st
from playwright.sync_api import sync_playwright
import re
import subprocess
import sys
import urllib.parse


@st.cache_resource
def _install_playwright():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)


def scrape_au(zip_code: str):
    z = re.sub(r'\D', '', zip_code)
    if len(z) != 7:
        return [], "郵便番号は7桁で入力してください"

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except Exception:
            pass

        try:
            page.goto("https://bb-application.au.kddi.com/auhikari/zipcode", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(2000)

            page.click('#sendzip1')
            page.wait_for_timeout(300)
            page.locator('#sendzip1').type(z[:3], delay=100)
            page.wait_for_timeout(400)
            page.click('#sendzip2')
            page.wait_for_timeout(300)
            page.locator('#sendzip2').type(z[3:], delay=100)
            page.wait_for_timeout(500)
            page.check('#mantion')
            page.wait_for_timeout(1000)

            page.evaluate("""() => {
                document.querySelectorAll('input[type="submit"]').forEach(s => {
                    s.classList.remove('selecthide');
                    s.style.removeProperty('display');
                });
            }""")
            page.wait_for_timeout(500)
            page.locator('input[type="submit"]').first.click()

            page.wait_for_url(lambda u: "aparts" in u or "address" in u, timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(2000)

            def get_mansions():
                items = []
                for row in page.query_selector_all("table tr"):
                    cells = row.query_selector_all("td")
                    if len(cells) < 3:
                        continue
                    name = cells[0].inner_text().strip()
                    addr = cells[2].inner_text().strip()
                    if not name or name in {"マンション名", "物件名", "建物名"}:
                        continue
                    apart_id = ""
                    if len(cells) > 3:
                        r = cells[3].query_selector("input[type='radio']")
                        if r:
                            apart_id = r.get_attribute("value") or ""
                    items.append({"name": name, "addr": addr, "apart_id": apart_id, "type": ""})
                return items

            def get_type(apart_id, aparts_url):
                if not apart_id:
                    return ""
                try:
                    radio = page.query_selector(f"input[name='apart_id'][value='{apart_id}']")
                    if not radio:
                        return ""
                    radio.click()
                    page.wait_for_timeout(300)
                    page.click('text="次へ"', timeout=5000)
                    page.wait_for_url(lambda u: "apart" in u and "aparts" not in u, timeout=10000)
                    page.wait_for_timeout(3000)
                    html = page.content()
                    m = re.search(r'タイプ([GVEMU])', html)
                    has_mini = 'ミニギガ' in html
                    has_giga = 'ギガ' in html and not has_mini
                    t = ""
                    if m:
                        spd = "（ミニギガ）" if has_mini else "（ギガ）" if has_giga else ""
                        t = f"タイプ{m.group(1)}{spd}"
                    elif has_mini:
                        t = "ミニギガ"
                    elif has_giga:
                        t = "ギガ"
                    page.go_back()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.wait_for_timeout(500)
                    return t
                except Exception:
                    try:
                        page.goto(aparts_url, timeout=15000)
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    return ""

            url = page.url
            if "aparts" in url:
                try:
                    page.wait_for_selector("table tr td", timeout=8000)
                except Exception:
                    pass
                mansions = get_mansions()
                aparts_url = url
                for m in mansions:
                    m["type"] = get_type(m["apart_id"], aparts_url)
                results = mansions

            elif "address" in url:
                adr_url = url
                links = []
                seen = set()
                for el in page.query_selector_all("td a, a"):
                    t = el.inner_text().strip()
                    if t and t not in seen and (re.search(r'\d+丁目', t) or re.match(r'^\d+$', t)):
                        seen.add(t)
                        links.append(t)
                for ct in links:
                    try:
                        els = page.query_selector_all("td a, a")
                        tgt = next((e for e in els if e.inner_text().strip() == ct), None)
                        if tgt:
                            tgt.click()
                        else:
                            page.click(f'text="{ct}"', timeout=5000)
                        page.wait_for_url(lambda u: "aparts" in u, timeout=10000)
                        try:
                            page.wait_for_selector("table tr td", timeout=8000)
                        except Exception:
                            page.wait_for_timeout(2000)
                        chunk = get_mansions()
                        aparts_url = page.url
                        for m in chunk:
                            m["type"] = get_type(m["apart_id"], aparts_url)
                        results.extend(chunk)
                        page.goto(adr_url)
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        page.wait_for_timeout(500)
                    except Exception:
                        continue

        except Exception as e:
            browser.close()
            return [], f"エラー: {e}"

        browser.close()

    return results, ""


def main():
    _install_playwright()
    st.set_page_config(page_title="マンション調べツール", layout="wide")
    st.title("マンション調べ効率化ツール")

    zip_input = st.text_input("郵便番号を入力", placeholder="101-0024")

    if st.button("検索", type="primary"):
        if not zip_input.strip():
            st.error("郵便番号を入力してください")
            return

        with st.spinner("auサイトを検索中..."):
            mansions, err = scrape_au(zip_input.strip())

        if err:
            st.error(err)
        elif not mansions:
            st.warning("マンションが見つかりませんでした")
        else:
            st.success(f"{len(mansions)} 件見つかりました")
            st.session_state["results"] = mansions

    if "results" in st.session_state:
        st.divider()
        for m in st.session_state["results"]:
            c1, c2 = st.columns([4, 2])
            with c1:
                badge = f"　`{m['type']}`" if m["type"] else ""
                st.markdown(f"**{m['name']}**{badge}")
                st.code(f"{m['name']} {m['addr']}", language=None)
            with c2:
                url = "https://www.google.com/search?q=" + urllib.parse.quote(m["name"] + " site:homes.co.jp/archive")
                st.link_button("ホームズで検索", url, use_container_width=True)
            st.divider()


if __name__ == "__main__":
    main()

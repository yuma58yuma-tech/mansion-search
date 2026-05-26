"""
mansion-search セットアップスクリプト
  python setup.py
で全ファイル生成 + パッケージインストールが完了します。
"""
import os, subprocess, sys

BASE = r"C:\Users\yuma5\mansion-search"
os.makedirs(BASE, exist_ok=True)

# ── app.py ──────────────────────────────────────────────────────────────────
APP = r'''import streamlit as st
from playwright.sync_api import sync_playwright
import re
import urllib.parse


def scrape_au(zip_code: str, get_types: bool = True):
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
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_timeout(1000)

            page.click('#sendzip1')
            page.wait_for_timeout(150)
            page.locator('#sendzip1').type(z[:3], delay=80)
            page.wait_for_timeout(200)
            page.click('#sendzip2')
            page.wait_for_timeout(150)
            page.locator('#sendzip2').type(z[3:], delay=80)
            page.wait_for_timeout(300)
            page.check('#mantion')
            page.wait_for_timeout(600)

            page.evaluate("""() => {
                document.querySelectorAll('input[type="submit"]').forEach(s => {
                    s.classList.remove('selecthide');
                    s.style.removeProperty('display');
                });
            }""")
            page.wait_for_timeout(300)
            page.locator('input[type="submit"]').first.click()

            page.wait_for_url(lambda u: "aparts" in u or "address" in u, timeout=30000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(1000)

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
                    page.wait_for_timeout(200)
                    page.click('text="次へ"', timeout=5000)
                    page.wait_for_url(lambda u: "apart" in u and "aparts" not in u, timeout=10000)
                    page.wait_for_timeout(1500)
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
                    page.wait_for_timeout(300)
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
                if get_types:
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
                            page.wait_for_timeout(1500)
                        chunk = get_mansions()
                        aparts_url = page.url
                        if get_types:
                            for m in chunk:
                                m["type"] = get_type(m["apart_id"], aparts_url)
                        results.extend(chunk)
                        page.goto(adr_url)
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        page.wait_for_timeout(300)
                    except Exception:
                        continue

        except Exception as e:
            browser.close()
            return [], f"エラー: {e}"

        browser.close()

    return results, ""


TYPE_OPTIONS = ["G", "E", "V", "ミニギガ", "ギガ"]

def type_match(m_type: str, selected: list) -> bool:
    if not selected:
        return True
    t = m_type or ""
    for s in selected:
        if s == "ギガ" and "ギガ" in t and "ミニギガ" not in t:
            return True
        elif s != "ギガ" and s in t:
            return True
    return False


def main():
    st.set_page_config(page_title="マンション調べツール", layout="wide")
    st.title("マンション調べ効率化ツール")

    col_zip, col_btn = st.columns([3, 1])
    with col_zip:
        zip_input = st.text_input("郵便番号", placeholder="101-0024", label_visibility="collapsed")
    with col_btn:
        search_btn = st.button("検索", type="primary", use_container_width=True)

    fetch_types = st.checkbox("タイプも取得する（+1〜2分）", value=True)

    st.write("**タイプ絞り込み**（複数選択可・何も選ばなければ全表示）")
    type_filter = st.pills("タイプ", TYPE_OPTIONS, selection_mode="multi", label_visibility="collapsed")

    if search_btn:
        if not zip_input.strip():
            st.error("郵便番号を入力してください")
        else:
            spinner_msg = "auサイトを検索中...（1〜2分かかります）" if fetch_types else "auサイトを検索中...（15〜30秒）"
            with st.spinner(spinner_msg):
                mansions, err = scrape_au(zip_input.strip(), get_types=fetch_types)

            if err:
                st.error(err)
            elif not mansions:
                st.warning("対応マンションが見つかりませんでした")
            else:
                st.session_state["results"] = mansions
                st.session_state["zip"] = zip_input.strip()

    if "results" in st.session_state:
        mansions = st.session_state["results"]
        filtered = [m for m in mansions if type_match(m.get("type", ""), type_filter)]

        st.success(f"{len(filtered)} 件 / 全{len(mansions)} 件")
        st.divider()

        for m in filtered:
            c1, c2 = st.columns([6, 2])
            with c1:
                badge = f"　`{m['type']}`" if m.get("type") else ""
                st.markdown(f"**{m['name']}**{badge}")
                st.code(m["addr"], language=None)
            with c2:
                google_url = "https://www.google.com/search?q=" + urllib.parse.quote(f"{m['name']} homes.co.jp")
                st.link_button("ホームズ検索", google_url, use_container_width=True)
            st.divider()


if __name__ == "__main__":
    main()
'''

# ── Dockerfile ───────────────────────────────────────────────────────────────
DOCKERFILE = '''FROM python:3.11-slim

RUN apt-get update && apt-get install -y \\
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \\
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \\
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \\
    libx11-6 libxext6 libxcb1 libx11-xcb1 \\
    wget curl ca-certificates fonts-noto-cjk \\
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m playwright install chromium

COPY app.py .
COPY packages.txt .

EXPOSE 8501

CMD python -m streamlit run app.py \\
    --server.port=${PORT:-8501} \\
    --server.address=0.0.0.0 \\
    --server.headless=true \\
    --server.enableCORS=false \\
    --server.enableXsrfProtection=false
'''

# ── requirements.txt ────────────────────────────────────────────────────────
REQS = "streamlit\nplaywright\nplaywright-stealth\n"

# ── packages.txt（Streamlit Cloud用） ────────────────────────────────────────
PACKAGES = """libnss3
libnspr4
libatk1.0-0
libatk-bridge2.0-0
libcups2
libdrm2
libxkbcommon0
libxcomposite1
libxdamage1
libxfixes3
libxrandr2
libgbm1
libasound2
"""

# ── 起動.bat（ローカル用） ────────────────────────────────────────────────────
BAT = (
    "@echo off\n"
    "cd /d \"%~dp0\"\n"
    "python -m streamlit run app.py --server.port 8504\n"
    "start microsoft-edge:http://127.0.0.1:8504\n"
    "pause\n"
)

# ── ファイル書き出し ──────────────────────────────────────────────────────────
files = {
    "app.py":          APP,
    "Dockerfile":      DOCKERFILE,
    "requirements.txt": REQS,
    "packages.txt":    PACKAGES,
    "起動.bat":         BAT,
}
for name, content in files.items():
    with open(os.path.join(BASE, name), "w", encoding="utf-8") as f:
        f.write(content)
print("ファイル作成完了")

# ── パッケージインストール ────────────────────────────────────────────────────
print("パッケージをインストール中...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                os.path.join(BASE, "requirements.txt"), "-q"], check=True)

print("Playwright chromium をインストール中...")
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)

print("\nセットアップ完了！")
print("ローカル起動: 起動.bat をダブルクリック")
print("Railway URL: https://mansion-search-production.up.railway.app")

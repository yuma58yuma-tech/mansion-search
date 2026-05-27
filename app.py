import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse

BASE_URL = "https://bb-application.au.kddi.com"

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def parse_mansions(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        name = cells[0].get_text(strip=True)
        addr = cells[2].get_text(strip=True)
        if not name or name in {"マンション名", "物件名", "建物名"}:
            continue
        apart_id = ""
        if len(cells) > 3:
            radio = cells[3].find("input")
            if radio:
                apart_id = radio.get("value", "")
        items.append({"name": name, "addr": addr, "apart_id": apart_id, "type": ""})
    return items


def get_type_http(session, aparts_url, apart_id):
    if not apart_id:
        return ""
    try:
        # aparts ページのフォームを送信してタイプページへ
        data = {"apart_id": apart_id}
        # フォームのaction URLを推定（aparts→apart）
        action_url = re.sub(r'/aparts(/.*)?$', '/apart', aparts_url)
        resp = session.post(action_url, data=data, timeout=20,
                            headers={"Referer": aparts_url})
        html = resp.text
        m = re.search(r'タイプ([GVEMU])', html)
        has_mini = 'ミニギガ' in html
        has_giga = 'ギガ' in html and not has_mini
        if m:
            spd = "（ミニギガ）" if has_mini else "（ギガ）" if has_giga else ""
            return f"タイプ{m.group(1)}{spd}"
        elif has_mini:
            return "ミニギガ"
        elif has_giga:
            return "ギガ"
    except Exception:
        pass
    return ""


def scrape_au(zip_code: str, get_types: bool = True):
    z = re.sub(r'\D', '', zip_code)
    if len(z) != 7:
        return [], "郵便番号は7桁で入力してください"

    session = make_session()

    try:
        # フォームページ取得
        r = session.get(f"{BASE_URL}/auhikari/zipcode", timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")

        # 隠しフィールド収集
        form_data = {
            "hometype": "apart",   # マンション/アパート
            "zip1": z[:3],
            "zip2": z[3:],
            "tel1": "", "tel2": "", "tel3": "",
        }
        for inp in soup.find_all("input", {"type": "hidden"}):
            if inp.get("name"):
                form_data[inp["name"]] = inp.get("value", "")

        # フォーム送信
        session.headers["Referer"] = f"{BASE_URL}/auhikari/zipcode"
        r = session.post(f"{BASE_URL}/auhikari/zipcode", data=form_data, timeout=30)

        results = []

        if "aparts" in r.url:
            mansions = parse_mansions(r.text)
            if get_types:
                aparts_url = r.url
                for m in mansions:
                    m["type"] = get_type_http(session, aparts_url, m["apart_id"])
            results = mansions

        elif "address" in r.url:
            addr_soup = BeautifulSoup(r.text, "html.parser")
            for a_tag in addr_soup.find_all("a", href=True):
                href = a_tag["href"]
                if not href or href == "#":
                    continue
                full_url = href if href.startswith("http") else BASE_URL + href
                try:
                    resp = session.get(full_url, timeout=30)
                    if "aparts" not in resp.url:
                        continue
                    chunk = parse_mansions(resp.text)
                    if get_types:
                        for m in chunk:
                            m["type"] = get_type_http(session, resp.url, m["apart_id"])
                    results.extend(chunk)
                except Exception:
                    continue

        else:
            snippet = re.sub(r'\s+', ' ', r.text[:300]) if r.text else "(空)"
            return [], (
                f"アクセス失敗\n"
                f"URL: {r.url}\n"
                f"Status: {r.status_code}\n"
                f"内容: {snippet}"
            )

        return results, ""

    except Exception as e:
        return [], f"エラー: {e}"


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
            spinner_msg = "auサイトを検索中...（しばらくお待ちください）"
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

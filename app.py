import streamlit as st
import gspread
import pandas as pd
import threading
import time
import json
import base64
import unicodedata
import requests  # 用於發送推播通知

# 設定網頁為手機優化寬度，標題換上新名稱
st.set_page_config(page_title="SANBAN備品快速查扣系統 (網頁版)", layout="centered")

# 搜尋只涵蓋試算表 A～G 欄，排除 H～J 欄的數量、使用與殘數。
SEARCH_COLUMNS = ["位置", "編號", "產線", "設備名", "部品名稱", "部品型號", "廠牌"]
def normalize_search_text(value):
    """統一全半形、大小寫與空白，降低試算表資料格式差異造成的漏搜。"""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return normalized.replace(" ", "").replace("　", "")


def expand_search_terms(keyword):
    """只回傳使用者實際輸入的關鍵字，不自動擴充同義詞，避免誤抓。"""
    normalized_keyword = normalize_search_text(keyword)
    return [normalized_keyword] if normalized_keyword else []


# --- 📱 手機推播通知 ---
def send_push_notification(title, description):
    """以背景執行方式發送 PushDeer 通知，避免阻塞 Streamlit 畫面。"""
    try:
        pushkey = st.secrets.get("pushdeer_pushkey", "PDU43335TPkNbbnLLxdEs91V1sGUqI8JphjeUo46O")
        body_payload = {
            "pushkey": pushkey,
            "text": title,
            "desp": description,
        }
        url_trigger = "https://api2.pushdeer.com/message/push"
        threading.Thread(
            target=requests.post,
            args=(url_trigger,),
            kwargs={"data": body_payload, "timeout": 3.0},
            daemon=True,
        ).start()
    except Exception as err:
        print(f"發送推播失敗: {err}")


# --- 🔐 密碼保護機制 ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    st.markdown("<h3 style='text-align: center; color: #28a745; font-weight: bold;'>🔐 SANBAN 系統安全登入</h3>", unsafe_allow_html=True)
    user_password = st.text_input("🔑 請輸入工廠專屬連線密碼", type="password")
    
    if st.button("確認登入", type="primary", use_container_width=True):
        if user_password == st.secrets["app_password"]:
            st.session_state["password_correct"] = True
            send_push_notification(
                "🔐 SANBAN登入成功",
                "有人使用正確密碼登入 SANBAN 備品快速查扣系統。",
            )
            st.rerun()
        else:
            send_push_notification(
                "⚠️ SANBAN密碼錯誤",
                "有人嘗試登入 SANBAN 系統，但輸入的密碼不正確。",
            )
            st.error("❌ 密碼錯誤，請重新輸入！")
    return False

# 🌟 終極 Base64 機器人直連機制 🌟
def init_gspread():
    try:
        base64_creds = st.secrets["gcp_service_account_base64"]
        decoded_bytes = base64.b64decode(base64_creds)
        creds_json = decoded_bytes.decode("utf-8")
        creds_dict = json.loads(creds_json)
        
        gc = gspread.service_account_from_dict(creds_dict)
        spreadsheet_url = st.secrets["spreadsheet_url"]
        return gc.open_by_url(spreadsheet_url)
    except Exception as e:
        st.error(f"雲端保險箱授權解析失敗，請確認 Secrets 設定：{e}")
        return None

# ✨ 高速記憶體快取讀取（內建 503 自動重試喚醒機制）
@st.cache_data(ttl=300) 
def load_data():
    max_retries = 3  # 最多重試 3 次
    retry_delay = 2  # 每次失敗後等待的基礎秒數
    
    for attempt in range(max_retries):
        try:
            gs_client = init_gspread()
            if gs_client is None:
                return pd.DataFrame()
            sheet = gs_client.get_worksheet(0)
            raw_data = sheet.get_all_values()
            if len(raw_data) <= 1:
                return pd.DataFrame()
            
            cols = ['位置', '編號', '產線', '設備名', '部品名稱', '部品型號', '廠牌', '數量', '使用', '殘數']
            processed_rows = []
            for row in raw_data[1:]:
                if len(row) < 10:
                    row += [''] * (10 - len(row))
                processed_rows.append(row[:10])
                
            df = pd.DataFrame(processed_rows, columns=cols)
            df['行數'] = range(2, len(df) + 2)
            return df
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "unavailable" in error_msg.lower():
                if attempt == max_retries - 1:
                    return pd.DataFrame()
                sleep_time = retry_delay * (attempt + 1)
                time.sleep(sleep_time)
            else:
                st.error(f"讀取雲端資料失敗：{e}")
                return pd.DataFrame()
    return pd.DataFrame()

# Google 試算表同步更新
def update_google(row_num, used_col, new_used):
    try:
        gs_client = init_gspread()
        if not gs_client:
            return False
        sheet = gs_client.get_worksheet(0)
        sheet.update_cell(row_num, used_col, int(new_used))
        return True
    except Exception as e:
        print(f"同步 Google 試算表失敗: {e}")
        return False
@st.dialog("⚠️ 領用扣除確認")
def deduct_confirmation_dialog():
    """顯示中央彈窗，只有按下確認扣除才會更新庫存。"""
    p_name = st.session_state["selected_part_name"]
    amt_val = st.session_state["selected_take_amt"]
    target_row = st.session_state["selected_row_idx"]

    st.warning(f"確定要扣除「{p_name}」數量 {amt_val} 件嗎？")
    st.caption("按下「確認扣除」後才會正式更新 Google 庫存並發送手機通知。")

    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        confirm_clicked = st.button("✅ 確認扣除", type="primary", use_container_width=True)
    with cancel_col:
        cancel_clicked = st.button("取消", use_container_width=True)

    if cancel_clicked:
        st.session_state["selected_row_idx"] = None
        st.session_state["deduct_confirm_version"] += 1
        st.rerun()

    if not confirm_clicked:
        return

    latest_rows = st.session_state["df_data"][st.session_state["df_data"]["行數"] == target_row]
    if latest_rows.empty:
        st.error("找不到原本選取的部品，請關閉視窗後重新選取。")
        return

    latest_row = latest_rows.iloc[0]
    latest_remain = int(latest_row["殘數"]) if str(latest_row["殘數"]).isdigit() else 0
    latest_used = int(latest_row["使用"]) if str(latest_row["使用"]).isdigit() else 0
    if amt_val > latest_remain:
        st.error(f"目前庫存只剩 {latest_remain} 件，已不足以扣除 {amt_val} 件，請取消後重新選取。")
        return

    new_remain = latest_remain - amt_val
    new_used = latest_used + amt_val

    with st.spinner("💾 正在同步寫入 Google 雲端庫存..."):
        if not update_google(target_row, 9, new_used):
            st.error("❌ Google 雲端庫存同步失敗，本次未完成扣除，請稍後重試。")
            return

        st.session_state["df_data"].loc[st.session_state["df_data"]["行數"] == target_row, "使用"] = str(new_used)
        st.session_state["df_data"].loc[st.session_state["df_data"]["行數"] == target_row, "殘數"] = str(new_remain)

        send_push_notification(
            f"🏭 SANBAN領取通知：{p_name}",
            f"領取數量：{amt_val} 件\\n庫存剩餘：{new_remain} 件",
        )

    st.session_state["selected_row_idx"] = None
    st.session_state["deduct_confirm_version"] += 1
    st.toast(f"✅ 成功扣除備品數量 {amt_val} 件！")
    st.rerun()


# --- 核心主程式執行區 ---
if check_password():
    st.markdown("<h2 style='text-align: center; color: #28a745; font-weight: bold;'>🏭 SANBAN備品快速查扣系統 (網頁版)</h2>", unsafe_allow_html=True)

    # 💡 全域狀態暫存器
    if "selected_row_idx" not in st.session_state:
        st.session_state["selected_row_idx"] = None
    if "selected_part_name" not in st.session_state:
        st.session_state["selected_part_name"] = ""
    if "selected_take_amt" not in st.session_state:
        st.session_state["selected_take_amt"] = 0
    if "selected_remain_val" not in st.session_state:
        st.session_state["selected_remain_val"] = 0
    if "selected_current_used" not in st.session_state:
        st.session_state["selected_current_used"] = 0
    if "deduct_confirm_version" not in st.session_state:
        st.session_state["deduct_confirm_version"] = 0

    with st.spinner("🔄 正在連線雲端資料庫，請稍候..."):
        raw_df = load_data()

    # ✨ 解決 Google 503 導致畫面永久卡死的問題，提供救磚按鈕
    if raw_df.empty:
        st.error("❌ 無法連線至 Google 雲端資料庫 (伺服器暫時忙碌中)")
        st.warning("💡 提示：這通常是 Google 伺服器休眠。請點擊下方按鈕重新嘗試連線。")
        if st.button("🔌 嘗試重新喚醒並同步雲端數據", type="primary", use_container_width=True):
            st.cache_data.clear()
            if "df_data" in st.session_state:
                del st.session_state["df_data"]
            st.rerun()
    else:
        if "df_data" not in st.session_state:
            st.session_state["df_data"] = raw_df.copy()

        current_df = st.session_state["df_data"]

        col_filter1, col_filter2 = st.columns(2)
        with col_filter1:
            all_locs = ["所有位置"] + [str(x).strip() for x in current_df["位置"].unique() if str(x).strip()]
            selected_loc = st.selectbox("📍 選擇位置 (快速篩選)", all_locs)
        with col_filter2:
            all_lines = ["所有產線"] + [str(x).strip() for x in current_df["產線"].unique() if str(x).strip()]
            selected_line = st.selectbox("⚙️ 選擇產線 (快速篩選)", all_lines)

        search_keyword = st.text_input("🔍 輸入關鍵字（搜尋 A～G 欄：位置、編號、產線、設備、部品名稱、型號、廠牌）", "")

        filtered_df = current_df.copy()
        if selected_loc != "所有位置":
            filtered_df = filtered_df[filtered_df["位置"].fillna("").astype(str).str.strip() == selected_loc]
        if selected_line != "所有產線":
            filtered_df = filtered_df[filtered_df["產線"].fillna("").astype(str).str.strip() == selected_line]
        if search_keyword.strip():
            # 所有關鍵字只搜尋 A～G 欄；B 欄編號中的 O(培林)-0001 也會被抓到。
            # 使用者輸入「培林」時，不會自動擴充成「軸承」或「bearing」。
            search_blob = (
                filtered_df[SEARCH_COLUMNS]
                .fillna("")
                .astype(str)
                .agg(" ".join, axis=1)
                .map(normalize_search_text)
            )
            matched_rows = pd.Series(False, index=filtered_df.index)
            for term in expand_search_terms(search_keyword):
                matched_rows |= search_blob.str.contains(term, regex=False, na=False)
            filtered_df = filtered_df[matched_rows]

        st.caption(f"🔎 找到 {len(filtered_df)} 筆符合的備品")

        # 已選取項目時只顯示摘要，正式確認改由中央模態視窗處理。
        if st.session_state["selected_row_idx"] is not None:
            st.markdown("---")
            st.info(
                f"已選取：{st.session_state['selected_part_name']}，數量 {st.session_state['selected_take_amt']} 件。"
            )
            if st.button("↩️ 取消此次領用", use_container_width=True):
                    st.session_state["selected_row_idx"] = None
                    st.session_state["deduct_confirm_version"] += 1
                    st.rerun()

        if filtered_df.empty:
            st.info("沒有找到符合的備品 ❌")
        else:
            for idx, row in filtered_df.iterrows():
                row_idx = int(row['行數'])
                remain_val = int(row["殘數"]) if str(row["殘數"]).isdigit() else 0
                is_zero = remain_val <= 0
                
                card_color = "#f8d7da" if is_zero else "#ffffff"
                st.markdown(
                    f"""
                    <div style="background-color:{card_color}; padding:15px; border-radius:10px; 
                         box-shadow: 0 2px 5px rgba(0,0,0,0.1); border-left: 5px solid {'#dc3545' if is_zero else '#28a745'}; margin-bottom:10px; margin-top:15px;">
                        <h4 style="margin:0; color:#333;">{row['部品名稱']} <span style="font-size:0.8rem; background:#ffc107; color:black; padding:2px 6px; border-radius:3px;">{row['位置']}</span></h4>
                        <p style="margin:5px 0; font-size:0.9rem; color:#666;">
                            <b>型號：</b>{row['部品型號']}<br>
                            <b>設備：</b>{row['設備名']} ({row['產線']})<br>
                            <b>廠牌/編號：</b>{row['廠牌']} / {row['編號']}<br>
                            <b>目前殘數：</b><span style="font-size:1.3rem; font-weight:bold; color:{'#dc3545' if is_zero else '#28a745'}">{remain_val}</span> (總數: {row['數量']} | 已用: {row['使用']})
                        </p>
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
                
                if not is_zero:
                    col_input, col_btn = st.columns(2)
                    is_selected = st.session_state["selected_row_idx"] == row_idx
                    with col_input:
                        if is_selected:
                            # 已進入確認流程後改為純顯示，完全禁止修改數量。
                            st.info(f"🔒 領取數量已鎖定：{st.session_state['selected_take_amt']} 件")
                        else:
                            take_amt = st.number_input(
                                "領取數量",
                                min_value=1,
                                max_value=remain_val,
                                value=1,
                                key=f"amt_{row_idx}",
                                label_visibility="collapsed",
                            )
                    with col_btn:
                        if is_selected:
                            st.button("已選取，請至上方確認", key=f"selected_btn_{row_idx}", disabled=True, use_container_width=True)
                        elif st.button("確認領取", key=f"btn_{row_idx}", type="primary", use_container_width=True):
                            st.session_state["selected_row_idx"] = row_idx
                            st.session_state["selected_part_name"] = row['部品名稱']
                            st.session_state["selected_take_amt"] = take_amt
                            st.session_state["selected_remain_val"] = remain_val
                            st.session_state["selected_current_used"] = int(row["使用"]) if str(row["使用"]).isdigit() else 0
                            # 設定領用資料後立即開啟中央模態確認視窗，不再需要額外按鈕。
                            st.session_state["deduct_confirm_version"] += 1
                            deduct_confirmation_dialog()


        st.markdown("---")
        if st.button("🔄 手動同步雲端最新數據", key="manual_sync_btn", use_container_width=True):
            with st.spinner("📥 正在重新抓取最新試算表庫存..."):
                st.cache_data.clear()
                if "df_data" in st.session_state:
                    del st.session_state["df_data"]
                st.session_state["selected_row_idx"] = None
                st.rerun()

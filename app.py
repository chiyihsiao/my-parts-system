import streamlit as st
import gspread
import pandas as pd
import threading
import time
import json
import base64

# 設定網頁為手機優化寬度，標題換上新名稱
st.set_page_config(page_title="SANBAN備品快速查扣系統 (網頁版)", layout="centered")

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
            st.rerun()
        else:
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

# 高速記憶體快取讀取
@st.cache_data(ttl=300) 
def load_data():
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
        st.error(f"讀取雲端資料失敗：{e}")
        return pd.DataFrame()

# 背景非同步更新
def bg_update_google(row_num, used_col, new_used):
    try:
        gs_client = init_gspread()
        if gs_client:
            sheet = gs_client.get_worksheet(0)
            sheet.update_cell(row_num, used_col, int(new_used))
    except Exception as e:
        print(f"背景同步失敗: {e}")

# --- 核心主程式執行區 ---
if check_password():
    st.markdown("<h2 style='text-align: center; color: #28a745; font-weight: bold;'>🏭 SANBAN備品快速查扣系統 (網頁版)</h2>", unsafe_allow_html=True)

    # 初始化全局狀態字典，徹底防止過濾篩選時撞號
    if "confirm_states" not in st.session_state:
        st.session_state["confirm_states"] = {}
    if "temp_amounts" not in st.session_state:
        st.session_state["temp_amounts"] = {}

    with st.spinner("🔄 正在連線雲端資料庫，請稍候..."):
        df = load_data()

    if df.empty:
        st.warning("資料庫載入中，正在從您的 Google 試算表即時同步...")
    else:
        col_filter1, col_filter2 = st.columns(2)
        with col_filter1:
            all_locs = ["所有位置"] + [str(x).strip() for x in df["位置"].unique() if str(x).strip()]
            selected_loc = st.selectbox("📍 選擇位置 (快速篩選)", all_locs)
        with col_filter2:
            all_lines = ["所有產線"] + [str(x).strip() for x in df["產線"].unique() if str(x).strip()]
            selected_line = st.selectbox("⚙️ 選擇產線 (快速篩選)", all_lines)

        search_keyword = st.text_input("🔍 輸入關鍵字 (可搜部品名稱、型號、廠牌或設備...)", "").strip().lower()

        filtered_df = df.copy()
        if selected_loc != "所有位置":
            filtered_df = filtered_df[filtered_df["位置"].str.strip() == selected_loc]
        if selected_line != "所有產線":
            filtered_df = filtered_df[filtered_df["產線"].str.strip() == selected_line]
        if search_keyword:
            filtered_df = filtered_df[
                filtered_df["部品名稱"].str.lower().str.contains(search_keyword) |
                filtered_df["部品型號"].str.lower().str.contains(search_keyword) |
                filtered_df["設備名"].str.lower().str.contains(search_keyword) |
                filtered_df["廠牌"].str.lower().str.contains(search_keyword)
            ]

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
                    # 🌟 安全機制：確保此行數在字典中有初始值
                    if row_idx not in st.session_state["confirm_states"]:
                        st.session_state["confirm_states"][row_idx] = False

                    if not st.session_state["confirm_states"][row_idx]:
                        # 第一階段：常規顯示輸入框與領取按鈕
                        col_input, col_btn = st.columns(2)
                        with col_input:
                            take_amt = st.number_input(f"領取數量", min_value=1, max_value=remain_val, value=1, key=f"amt_{row_idx}", label_visibility="collapsed")
                        with col_btn:
                            if st.button("確認領取", key=f"btn_{row_idx}", type="primary", use_container_width=True):
                                st.session_state["confirm_states"][row_idx] = True
                                st.session_state["temp_amounts"][row_idx] = take_amt
                                st.rerun()
                    else:
                        # 第二階段：精確對位二次彈窗防呆，徹底根治過濾時的 StreamlitAPIException Bug
                        saved_amt = st.session_state["temp_amounts"].get(row_idx, 1)
                        st.warning(f"⚠️ 確定要扣除 【{row['部品名稱']}】 數量 {saved_amt} 件嗎？")
                        
                        col_yes, col_no = st.columns(2)
                        with col_yes:
                            if st.button("⭕ 是，確定扣除", key=f"yes_final_{row_idx}", type="danger", use_container_width=True):
                                with st.spinner("💾 正在同步寫入 Google 雲端庫存..."):
                                    current_used = int(row["使用"]) if str(row["使用"]).isdigit() else 0
                                    new_used = current_used + saved_amt
                                    
                                    new_remain = remain_val - saved_amt
                                    df.loc[df['行數'] == row_idx, '使用'] = str(new_used)
                                    df.loc[df['行數'] == row_idx, '殘數'] = str(new_remain)
                                    
                                    t = threading.Thread(target=bg_update_google, args=(row_idx, 9, new_used))
                                    t.start()
                                    
                                    st.toast(f"✅ 成功扣除備品數量 {saved_amt} 件！")
                                    st.session_state["confirm_states"][row_idx] = False
                                    time.sleep(0.8)
                                    st.rerun()
                        with col_no:
                            if st.button("❌ 取消", key=f"no_final_{row_idx}", type="secondary", use_container_width=True):
                                st.session_state["confirm_states"][row_idx] = False
                                st.rerun()

        st.markdown("---")
        if st.button("🔄 手動同步雲端最新數據", use_container_width=True):
            with st.spinner("📥 正在重新抓取最新試算表庫存..."):
                st.cache_data.clear()
                st.session_state["confirm_states"] = {}
                st.session_state["temp_amounts"] = {}
                st.rerun()

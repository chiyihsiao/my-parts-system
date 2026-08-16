import streamlit as st
import gspread
import pandas as pd
import threading
import time
import json
import base64
import requests  # 用於發送推播通知

# 設定網頁為手機優化寬度，標題換上新名稱
st.set_page_config(page_title="SANBAN備品快速查扣系統 (網頁版)", layout="centered")

# --- 📢 PushDeer 遠端通知通用函式 ---
def send_pushdeer_notification(text_title, desp_content):
    """
    發送 PushDeer 推播通知（採用背景執行緒，完全不卡網頁速度）
    完美對接 Python 的 data= 格式 (等同於 PowerShell 的 -Body)
    """
    try:
        body_payload = {
            "pushkey": "PDU43335TPkNbbnLLxdEs91V1sGUqI8JphjeUo46O",
            "text": text_title,
            "desp": desp_content
        }
        url_trigger = "https://pushdeer.com"
        # 丟到背景非同步執行
        threading.Thread(target=requests.post, args=(url_trigger,), kwargs={"data": body_payload, "timeout": 3.0}).start()
    except Exception as err:
        print(f"背景發送 PushDeer 通知失敗: {err}")

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
            
            # 🚀 【追加】 登入成功遠端通知
            send_pushdeer_notification(
                text_title="🔐 SANBAN系統：人員登入成功",
                desp_content="已有工廠人員通過密碼驗證，成功進入備品快速查扣系統。"
            )
            
            st.rerun()
        else:
            # 🚀 【追加】 密碼錯誤遠端通知（安全警報）
            send_pushdeer_notification(
                text_title="⚠️ SANBAN安全警報：登入密碼錯誤",
                desp_content="偵測到有人嘗試登入系統，但輸入的密碼錯誤，已被系統攔截！"
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
# --- 核心主程式執行區 ---
if check_password():
    st.markdown("<h2 style='text-align: center; color: #28a745; font-weight: bold;'>🏭 SANBAN備品快速查扣系統 (網頁版)</h2>", unsafe_allow_html=True)

    with st.spinner("🔄 正在連線雲端資料庫，請稍候..."):
        raw_df = load_data()

    # ✨ 解決 Google 503 救磚按鈕
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

        # 🔍 篩選介面
        col_filter1, col_filter2 = st.columns(2)
        with col_filter1:
            all_locs = ["所有位置"] + [str(x).strip() for x in current_df["位置"].unique() if str(x).strip()]
            selected_loc = st.selectbox("📍 選擇位置 (快速篩選)", all_locs)
        with col_filter2:
            all_lines = ["所有產線"] + [str(x).strip() for x in current_df["產線"].unique() if str(x).strip()]
            selected_line = st.selectbox("⚙️ 選擇產線 (快速篩選)", all_lines)

        search_keyword = st.text_input("🔍 輸入關鍵字 (可搜部品名稱、型號、廠牌或設備...)", "").strip().lower()

        # 執行過濾
        filtered_df = current_df.copy()
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
                
                total_qty = int(row["數量"]) if str(row["數量"]).isdigit() else 0
                used_val = int(row["使用"]) if str(row["ប្រើ"]).isdigit() or str(row["使用"]).isdigit() else 0
                remain_val = int(row["殘數"]) if str(row["殘數"]).isdigit() else (total_qty - used_val)
                
                is_zero = remain_val <= 0
                card_color = "#f8d7da" if is_zero else "#ffffff"
                
                # 備品資訊卡片
                st.markdown(
                    f"""
                    <div style="background-color:{card_color}; padding:15px; border-radius:10px; 
                         box-shadow: 0 2px 5px rgba(0,0,0,0.1); border-left: 5px solid {'#dc3545' if is_zero else '#28a745'}; margin-bottom:10px; margin-top:15px;">
                        <h4 style="margin:0; color:#333;">{row['部品名稱']} <span style="font-size:0.8rem; background:#ffc107; color:black; padding:2px 6px; border-radius:3px;">{row['位置']}</span></h4>
                        <p style="margin:5px 0; font-size:0.9rem; color:#666;">
                            <b>型號：</b>{row['部品型號']}<br>
                            <b>設備：</b>{row['設備名']} ({row['產線']})<br>
                            <b>廠牌/編號：</b>{row['廠牌']} / {row['編號']}<br>
                            <b>目前殘數：</b><span style="font-size:1.3rem; font-weight:bold; color:{'#dc3545' if is_zero else '#28a745'}">{remain_val}</span> (總數: {total_qty} | 已用: {used_val})
                        </p>
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
                
                # 領取按鈕操作區
                if not is_zero:
                    col_input, col_btn = st.columns(2)
                    with col_input:
                        take_amt = st.number_input(f"領取數量", min_value=1, max_value=remain_val, value=1, key=f"amt_{row_idx}", label_visibility="collapsed")
                    with col_btn:
                        if st.button("確認領取", key=f"btn_{row_idx}", type="primary", use_container_width=True):
                            st.session_state["selected_row_idx"] = row_idx
                            st.session_state["selected_part_name"] = row['部品名稱']
                            st.session_state["selected_take_amt"] = take_amt
                            st.session_state["selected_remain_val"] = remain_val
                            st.session_state["selected_current_used"] = used_val
                            st.rerun()

        # 🌟🌟 終極安全防當解鎖：彈出式領取扣除確認與非同步機制 🌟🌟
        if st.session_state["selected_row_idx"] is not None:
            st.markdown("---")
            st.markdown(
                f"""
                <div style="background-color:#fff3cd; padding:15px; border-radius:10px; border-left: 5px solid #ffc107;">
                    <h5 style="margin:0; color:#856404; font-weight:bold;">⚠️ 【領取扣除確認】</h5>
                    <p style="margin:5px 0; color:#856404;">
                        確定要從庫存扣除 <b>{st.session_state['selected_part_name']}</b> 數量 <b>{st.session_state['selected_take_amt']}</b> 件嗎？
                    </p>
                </div>
                """, 
                unsafe_allow_html=True
            )
            st.write("")
            
            col_cancel, col_confirm = st.columns(2)
            
            with col_cancel:
                if st.button("❌ 取消領取", use_container_width=True):
                    st.session_state["selected_row_idx"] = None
                    st.rerun()
                    
            with col_confirm:
                if st.button("🔥 確定扣除庫存", type="primary", use_container_width=True):
                    p_name = st.session_state['selected_part_name']
                    amt_val = st.session_state['selected_take_amt']
                    r_val = st.session_state["selected_remain_val"]
                    new_remain = r_val - amt_val
                    
                    # 🚀 雙重防禦第一步：發送庫存扣除通知（呼叫通用函式）
                    send_pushdeer_notification(
                        text_title=f"🏭 SANBAN領取通知：{p_name}",
                        desp_content=f"領取數量：{amt_val} 件\n庫存剩餘：{new_remain} 件"
                    )

                    # 🚀 雙重防禦第二步：處理 Google 試算表寫入
                    with st.spinner("💾 正在同步寫入 Google 雲端庫存..."):
                        target_row = st.session_state["selected_row_idx"]
                        amt = st.session_state["selected_take_amt"]
                        c_used = st.session_state["selected_current_used"]
                        new_used = c_used + amt
                        
                        # A. 立即更新本地端 Streamlit 記憶體數據
                        st.session_state["df_data"].loc[st.session_state["df_data"]['行數'] == target_row, '使用'] = str(new_used)
                        st.session_state["df_data"].loc[st.session_state["df_data"]['行數'] == target_row, '殘數'] = str(new_remain)
                        
                        # B. 寫入雲端（由主執行緒進行，安全不崩潰）
                        try:
                            gs_client = init_gspread()
                            if gs_client:
                                sheet = gs_client.get_worksheet(0)
                                sheet.update_cell(target_row, 9, int(new_used))  # 第 9 欄是「使用」
                        except Exception as e:
                            st.error(f"雲端同步失敗（但本地已更新），請稍後手動同步：{e}")
                        
                        st.toast(f"✅ 成功扣除備品數量 {amt} 件！")
                        st.session_state["selected_row_idx"] = None  # 重設選取狀態
                        time.sleep(0.5)
                        st.rerun()

        st.markdown("---")
        if st.button("🔄 手動同步雲端最新數據", key="manual_sync_btn", use_container_width=True):
            with st.spinner("📥 正在重新抓取最新試算表庫存..."):
                st.cache_data.clear()
                if "df_data" in st.session_state:
                    del st.session_state["df_data"]
                st.session_state["selected_row_idx"] = None
                st.rerun()

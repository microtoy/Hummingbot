from datetime import datetime, time, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import requests

from frontend.st_utils import get_backend_api_client, initialize_st_page

# Initialize Streamlit page
initialize_st_page(title="Download Candles", icon="💾")
backend_api_client = get_backend_api_client()

# Sidebar: Quick Info
st.sidebar.markdown("""
### 💡 使用说明
1. **多选间隔**：左侧可勾选多个频率（如 1m, 1h）。
2. **批量同步**：点击 `Sync Top 10` 一次性备齐主流币数据。
3. **资产管理**：下方列表展示已下载数据，点击 **[Refresh]** 自动补全至最新时间。
""")

# Top 10 coins by market cap
TOP_10_PAIRS = ["BTC-USDT", "ETH-USDT", "BNB-USDT", "XRP-USDT", "SOL-USDT", "TRX-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT"]

# --- API Config ---
api_url = "http://hummingbot-api:8000"
api_auth = ("admin", "admin")
try:
    raw_url = backend_api_client.base_url
    api_url = raw_url.split("/api/")[0] if "/api/" in raw_url else raw_url.rstrip("/")
    if hasattr(backend_api_client, "auth") and backend_api_client.auth:
        api_auth = (backend_api_client.auth.login, backend_api_client.auth.password)
except: pass

def sync_worker(pair, interval, start_dt, end_dt, connector, base_url, auth, status_dict):
    """Worker function to sync a single pair/interval."""
    key = f"{pair}_{interval}"
    try:
        status_dict[key]["Status"] = "📥 Syncing..."
        payload = {
            "start_time": int(start_dt.timestamp()),
            "end_time": int(end_dt.timestamp()),
            "backtesting_resolution": interval,
            "trade_cost": 0.0006,
            "config": {"connector_name": connector, "trading_pair": pair, "candles_config": []}
        }
        url = f"{base_url}/backtesting/candles/sync"
        response = requests.post(url, json=payload, auth=auth, timeout=3600)
        if response.status_code == 200:
            result = response.json()
            if "error" in result:
                err_msg = str(result["error"])
                return {"key": key, "status": f"❌ {err_msg[:20]}", "rows": "-"}
            rows = result.get("rows", 0)
            try:
                formatted_rows = f"{int(rows):,}" if isinstance(rows, (int, float, str)) and str(rows).isdigit() else str(rows)
            except: formatted_rows = str(rows)
            return {"key": key, "status": "✅ Done", "rows": formatted_rows}
        return {"key": key, "status": f"❌ Error {response.status_code}", "rows": "-"}
    except Exception as e:
        err_str = str(e)
        if "ConnectionError" in err_str: err_str = "ConnError"
        return {"key": key, "status": f"❌ {err_str[:15]}", "rows": "-"}

def perform_batch_sync(tasks_to_run, start_dt, end_dt, connector_name, api_url, api_auth, placeholder_bar, placeholder_table):
    """Executes a batch of sync tasks using a thread pool."""
    st.session_state.sync_running = True
    total_tasks = len(tasks_to_run)
    task_status = st.session_state.task_status
    progress_tracker = 0
    failures = []
    
    def redraw():
        df = pd.DataFrame(task_status.values())
        placeholder_table.dataframe(df, use_container_width=True, hide_index=True)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(sync_worker, p, i, start_dt, end_dt, connector_name, api_url, api_auth, task_status): f"{p}_{i}" for p, i in tasks_to_run}
        for future in as_completed(futures):
            if not st.session_state.get("sync_running", True):
                break
            res = future.result()
            key = res["key"]
            task_status[key]["Status"] = res["status"]
            task_status[key]["Rows"] = res["rows"]
            if "❌" in res["status"]:
                p, i = key.rsplit("_", 1)
                failures.append((p, i))
            progress_tracker += 1
            placeholder_bar.progress(progress_tracker / total_tasks, text=f"Progress: {progress_tracker}/{total_tasks}")
            redraw()
    
    st.session_state.sync_running = False
    return failures

# --- 1. UI Setup ---
c1, c2, c3, c4 = st.columns([2, 2, 2, 1.5])
with c1:
    connector = st.selectbox("Exchange", ["binance", "binance_perpetual", "gate_io", "gate_io_perpetual", "kucoin", "ascend_ex"], index=0)
    trading_pair = st.text_input("Trading Pair", value="BTC-USDT")
with c2:
    intervals = st.multiselect("Intervals", options=["1m", "3m", "5m", "15m", "1h", "4h", "1d", "1s"], default=["1h", "1d"])
with c3:
    start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=7))
    end_date = st.date_input("End Date", value=datetime.now() - timedelta(days=1))
with c4:
    get_data_button = st.button("Get Candles! (Browser)", use_container_width=True)
    sync_to_server = st.button("Sync to Server Cache 🚀", use_container_width=True)
    sync_top10 = st.button("🔥 Sync Top 10 Coins", use_container_width=True)
    if st.button("🛑 FORCE STOP ALL SYNCING", use_container_width=True, type="primary"):
        try: requests.post(f"{api_url}/backtesting/candles/stop-all", auth=api_auth, timeout=5)
        except: pass
        st.session_state.sync_running = False
        st.warning("🛑 Stop signal sent.")
        st.rerun()

# --- 2. Centralized Status Center ---
st.divider()
st.subheader("📊 Task Status & Progress")
progress_bar_placeholder = st.empty()
status_msg_placeholder = st.empty()
status_table_placeholder = st.empty()

if "sync_running" not in st.session_state:
    st.session_state.sync_running = False

def redraw_table():
    if "task_status" in st.session_state:
        df = pd.DataFrame(st.session_state.task_status.values())
        status_table_placeholder.dataframe(df, use_container_width=True, hide_index=True)

def init_task_status(pairs, selected_intervals, exch, start, end):
    time_range = f"{start} ~ {end}"
    status_dict = {}
    for p in pairs:
        for i in selected_intervals:
            status_dict[f"{p}_{i}"] = {
                "Exch": exch,
                "Pair": p,
                "Intv": i,
                "Range": time_range,
                "Status": "⏳ Waiting...",
                "Rows": "-"
            }
    st.session_state.task_status = status_dict
    st.session_state.failed_sync_tasks = []

# --- 3. Sync Logic ---
if sync_to_server or get_data_button:
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    if not intervals: st.error("Please select at least one interval.")
    else:
        init_task_status([trading_pair], intervals, connector, start_date, end_date)
        st.session_state.browser_ready = False
        failures = perform_batch_sync([(trading_pair, i) for i in intervals], start_dt, end_dt, connector, api_url, api_auth, progress_bar_placeholder, status_table_placeholder)
        if get_data_button and not failures:
            st.session_state.browser_ready = True
            st.session_state.browser_intervals = intervals
            st.session_state.browser_params = {"connector": connector, "pair": trading_pair}

if sync_top10:
    if not intervals: st.error("Please select at least one interval.")
    else:
        init_task_status(TOP_10_PAIRS, intervals, connector, start_date, end_date)
        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(end_date, time.max)
        perform_batch_sync([(p, i) for p in TOP_10_PAIRS for i in intervals], start_dt, end_dt, connector, api_url, api_auth, progress_bar_placeholder, status_table_placeholder)

# Persistent Render
redraw_table()

# Browser Download Area (Integrated)
if st.session_state.get("browser_ready"):
    ready_intervals = st.session_state.get("browser_intervals", [])
    params = st.session_state.get("browser_params", {})
    st.info("✅ Browser data is ready for fetch:")
    cols = st.columns(len(ready_intervals))
    for idx, interval in enumerate(ready_intervals):
        csv_url = f"{api_url}/backtesting/candles/csv?connector={params['connector']}&trading_pair={params['pair']}&interval={interval}"
        try:
            resp = requests.get(csv_url, auth=api_auth)
            if resp.status_code == 200:
                cols[idx].download_button(label=f"💾 Download {interval}", data=resp.content, file_name=f"{params['pair']}_{interval}.csv", mime="text/csv", key=f"dl_btn_{interval}", use_container_width=True)
            else: cols[idx].error(f"Error {resp.status_code}")
        except: cols[idx].error("Conn Failed")

if "failed_sync_tasks" in st.session_state and st.session_state.failed_sync_tasks:
    if st.button("🔄 Retry Failed Tasks"):
        current_failures = st.session_state.failed_sync_tasks
        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(end_date, time.max)
        perform_batch_sync(current_failures, start_dt, end_dt, connector, api_url, api_auth, progress_bar_placeholder, status_table_placeholder)

# --- 4. Server Cache Management ---
st.divider()
st.subheader("📦 Server Cache Management")
try:
    cache_status = backend_api_client.backtesting.get_candles_status()
    cached_files = cache_status.get("cached_files", [])
    if not cached_files: st.info("No data cached yet.")
    else:
        cached_files = sorted(cached_files, key=lambda x: x['trading_pair'])
        headers = st.columns([1.5, 1, 1, 2.5, 1, 1])
        headers[0].write("**Exch**"); headers[1].write("**Pair**"); headers[2].write("**Intv**"); headers[3].write("**Coverage**"); headers[4].write("**Rows**"); headers[5].write("**Action**")
        for f in cached_files:
            c = st.columns([1.5, 1, 1, 2.5, 1, 1])
            c[0].write(f["connector"]); c[1].write(f["trading_pair"]); c[2].write(f["interval"])
            c[3].write(f"📅 {datetime.fromtimestamp(f['start']).strftime('%Y-%m-%d')} ➡️ {datetime.fromtimestamp(f['end']).strftime('%m-%d %H:%M')}")
            c[4].write(f"{f['count']:,}")
            if c[5].button("🔄 Refresh", key=f"ref_{f['file']}", use_container_width=True):
                backend_api_client.backtesting.sync_candles(start_time=f["end"]+1, end_time=int(datetime.now().timestamp()), backtesting_resolution=f["interval"], config={"connector_name": f["connector"], "trading_pair": f["trading_pair"], "candles_config": []})
                st.rerun()
except Exception as e: st.error(f"Cache Error: {e}")

import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import sys
import os

# 确保可以导入 data_lake
sys.path.append(os.getcwd())

from data.data_lake.manager import get_lake_manager

st.set_page_config(layout="wide", page_title="Data Lake V2")

LAKE = get_lake_manager()

st.title("🛡️ 行情数据管理 V2 (Data Lake)")
st.info("基于分片存储的数据中心：零锁冲突，极速检测，100% 安全。")

# --- 实时进度中心 (公共层) ---
st.subheader("📊 实时任务中心")

@st.fragment(run_every="2s")
def render_progress_center():
    # 获取最新状态
    current_status = LAKE.get_status()
    dl_status = current_status.get("download", {})
    slots = current_status.get("slots", [])
    active_workers = len([s for s in slots if s is not None])
    max_workers = len(slots)
    
    if dl_status.get("total", 0) > 0:
        # 总进度栏 (模仿 V1 风格)
        percent = dl_status.get("percent", 0)
        completed = dl_status.get("completed", 0)
        total = dl_status.get("total", 0)
        failed = dl_status.get("failed", 0)
        
        # 状态与控制项
        is_paused = LAKE.is_paused()
        status_emoji = "⏸️ 暂停中" if is_paused else "🚀 运行中"
        
        # 使用带有状态描述的进度条
        status_text = f"总进度: {completed}/{total} ( {percent:.1f}% )"
        if failed > 0:
            status_text += f" | ⚠️ {failed} 失败/取消"
        
        # 增加并发信息与控制按钮
        col_prog, col_pause, col_stop = st.columns([3, 1, 1])
        with col_prog:
            st.progress(percent / 100, text=status_text)
            st.caption(f"{status_emoji} | 并发: {active_workers}/{max_workers} | ⚡ 基于 asyncio 高并发引擎")
        
        with col_pause:
            if is_paused:
                if st.button("▶️ 恢复下载", use_container_width=True, type="primary"):
                    LAKE.resume_download()
                    st.rerun()
            else:
                if st.button("⏸️ 暂停下载", use_container_width=True):
                    LAKE.pause_download()
                    st.rerun()
        
        with col_stop:
            if st.button("⏹️ 终止全部", use_container_width=True, type="secondary", help="清空所有任务列表"):
                LAKE.stop_download()
                st.rerun()
        
        # 分项下载卡片
        details = dl_status.get("details", {})
        if details:
            # 只展示正在下载或有失败的任务，保持界面简洁
            active_keys = [k for k, v in details.items() if v["downloading"] > 0 or v["percent"] < 100]
            if active_keys:
                # 使用 3 列布局以适配 15 线程展示
                cols = st.columns(3)
                for i, key in enumerate(active_keys[:15]): # 展示前 15 个活跃任务
                    info = details[key]
                    with cols[i % 3]:
                        # 简化版分项进度
                        status_label = f"**{key}** ({info['completed']}/{info['total']} 天)"
                        if info.get("failed", 0) > 0:
                            status_label += f" | ⚠️ {info['failed']} 失败"
                        st.caption(status_label)
                        st.progress(info["percent"] / 100)
                        
                        # 如果有错误信息，展示第一条错误
                        if info.get("error"):
                            st.caption(f":red[{info['error']}]")
                if len(active_keys) > 15:
                    st.write(f"...等其余 {len(active_keys)-15} 个任务正在排队")
            else:
                st.success("✅ 当前批次所有任务已完成")
    else:
        st.info("当前无活动任务。在下方配置参数并启动下载。")

render_progress_center()
st.markdown("---")

# --- SIDEBAR ---
st.sidebar.header("📊 系统状态")
status = LAKE.get_status()

st.sidebar.metric("存储文件数", status["storage"]["total_files"])
st.sidebar.metric("存储总大小", f"{status['storage']['total_size_mb']} MB")

st.sidebar.markdown("---")
st.sidebar.header("📥 下载进度")
prog = status["download"]
if prog["total"] > 0:
    st.sidebar.progress(prog["percent"] / 100)
    st.sidebar.write(f"任务: {prog['completed']} / {prog['total']}")
    st.sidebar.write(f"进行中: {prog['downloading']} | 失败: {prog['failed']}")
else:
    st.sidebar.write("暂无活跃下载任务")

if st.sidebar.button("🔄 强制刷新页面"):
    st.rerun()

# --- MAIN PAGE TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 灵活下载 & 修复", "📋 数据资产概览", "🛡️ 兼容性桥接 (Export)"])

# TAB 1: 灵活下载
with tab1:
    st.subheader("🛠️ 参数配置")
    
    # 获取默认币种列表 (直接读取 yaml 以免 import 错误)
    import yaml
    symbols_path = "config/symbols.yaml"
    config_symbols_list = []
    try:
        if os.path.exists(symbols_path):
            with open(symbols_path, 'r') as f:
                config_symbols = yaml.safe_load(f)
                config_symbols_list = [s['trading_pair'] for s in config_symbols.get('symbols', [])]
    except Exception as e:
        st.warning(f"加载 symbols.yaml 失败: {e}")

    @st.cache_data(ttl=3600)  # 1小时缓存一次，提高性能
    def get_market_rankings(limit, rank_type="market_cap"):
        import asyncio
        try:
            return asyncio.run(LAKE.get_top_pairs(limit, rank_type=rank_type))
        except:
            if rank_type == "market_cap":
                return ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", "ADA-USDT", "DOGE-USDT", "TRX-USDT", "LINK-USDT", "DOT-USDT"][:limit]
            else:
                return ["BTC-USDT", "ETH-USDT", "SOL-USDT", "PEPE-USDT", "DOGE-USDT", "SHIB-USDT"][:limit]

    col_rank1, col_rank2 = st.columns(2)
    with col_rank1:
        rank_mode = st.selectbox("📊 排名选择模式", ["🌍 全球市值 (稳健优先)", "🔥 24h 热度 (活跃次选)", "手动选择 (本地配置)"], index=0)
    
    with col_rank2:
        if "手动" not in rank_mode:
            rank_size = st.selectbox("🎯 TOP N 规模", ["TOP 10", "TOP 20", "TOP 50", "TOP 100", "TOP 200"], index=0)
            target_limit = int(rank_size.split(" ")[1])
        else:
            st.write(" ") # 占位
            target_limit = 0

    if "市值" in rank_mode:
        top_pairs = get_market_rankings(target_limit, rank_type="market_cap")
        all_options = sorted(list(set(config_symbols_list + top_pairs)))
        selected_pairs = st.multiselect("选择交易对", all_options, default=top_pairs)
    elif "热度" in rank_mode:
        top_pairs = get_market_rankings(target_limit, rank_type="volume")
        all_options = sorted(list(set(config_symbols_list + top_pairs)))
        selected_pairs = st.multiselect("选择交易对", all_options, default=top_pairs)
    else:
        all_options = sorted(list(set(config_symbols_list + ["BTC-USDT", "ETH-USDT"])))
        selected_pairs = st.multiselect("选择交易对", all_options, default=config_symbols_list)

    st.subheader("2️⃣ 选择周期")
    intervals = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    selected_intervals = st.multiselect("时间粒度", intervals, default=["1m", "1h"])
    
    st.subheader("3️⃣ 选择日期范围")
    col_date1, col_date2 = st.columns(2)
    with col_date1:
        start_date = st.date_input("起始日期", date.today() - timedelta(days=7))
    with col_date2:
        end_date = st.date_input("结束日期", date.today())
        
    st.subheader("4️⃣ 下载设置")
    col_set1, col_set2 = st.columns([1, 2])
    with col_set1:
        use_proxy = st.checkbox("使用代理下载", value=False)
    with col_set2:
        proxy_url = st.text_input("代理地址", value="http://host.docker.internal:7890", disabled=not use_proxy)

    st.markdown("---")
    
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🚀 开始下载/补齐测试 (仅前3天)", type="secondary", use_container_width=True):
            test_end = start_date + timedelta(days=min(2, (end_date - start_date).days))
            LAKE.start_download(selected_pairs, selected_intervals, start_date, test_end, use_proxy=use_proxy, proxy_url=proxy_url)
            st.success(f"已触发测试下载: {selected_pairs}")
            
    with col_btn2:
        if st.button("🔥 执行全量下载任务", type="primary", use_container_width=True):
            LAKE.start_download(selected_pairs, selected_intervals, start_date, end_date, use_proxy=use_proxy, proxy_url=proxy_url)
            st.success(f"已按指定起始范围触发下载")

    st.markdown("---")
    st.subheader("💡 智能维护")
    col_smart1, col_smart2 = st.columns(2)
    with col_smart1:
        years = st.number_input("补齐历史年限", min_value=1, max_value=10, value=3)
        if st.button("🩹 一键补齐所有缺失历史", use_container_width=True):
            LAKE.auto_fill_history(selected_pairs, selected_intervals, years=years, use_proxy=use_proxy, proxy_url=proxy_url)
            st.info("已启动后台历史扫描与补齐功能...")

    with col_smart2:
        st.write(" ") # 占位
        st.write(" ") # 占位
        if st.button("🔄 同步更新至最新时刻", use_container_width=True):
            # 将结束日期设为今天
            LAKE.start_download(selected_pairs, selected_intervals, date.today() - timedelta(days=2), date.today(), use_proxy=use_proxy, proxy_url=proxy_url)
            st.info("已启动增量同步任务...")


# TAB 2: 数据资产
with tab2:
    c1, c2 = st.columns([5, 1])
    with c1:
        st.subheader("数据湖存储详情")
    with c2:
        if st.button("🔍 深度质检", help="物理扫描每个文件，核对行数 (1m=1440, 1h=24)"):
            LAKE.get_status(audit=True)
            st.rerun()

    def render_coverage_ribbon(bits):
        if not bits: return ""
        # 使用 CSS Gradient 生成像条形码一样的状态线
        colors = []
        step = 100 / len(bits)
        for i, b in enumerate(bits):
            color = "#2E7D32" if b == 1 else "#E0E0E0" # 绿色 vs 灰色
            colors.append(f"{color} {i*step}%")
            colors.append(f"{color} {(i+1)*step}%")
        
        gradient = ", ".join(colors)
        html = f"""
        <div style="
            width: 100%; 
            height: 12px; 
            background: linear-gradient(90deg, {gradient}); 
            border-radius: 6px;
            margin: 5px 0;
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.1);
        "></div>
        """
        return html

    pairs_data = status["storage"]["pairs"]
    if pairs_data:
        for k, v in pairs_data.items():
            with st.container():
                col1, col2, col3 = st.columns([2, 2, 1])
                with col1:
                    st.markdown(f"**{k}**")
                with col2:
                    start_str = v.get('start').isoformat() if v.get('start') else "-"
                    end_str = v.get('end').isoformat() if v.get('end') else "-"
                    st.write(f"📅 {start_str} 至 {end_str}")
                with col3:
                    rows = v.get('total_rows', 0)
                    st.write(f"📈 {rows:,} 条记录")
                
                # 第二行详情
                m_col1, m_col2, m_col3, m_col4 = st.columns([3, 1, 1, 1])
                with m_col1:
                    # 渲染状态线
                    parts = k.split(":")
                    if len(parts) == 3:
                        bits = LAKE.storage.get_coverage(parts[0], parts[1], parts[2])
                        st.markdown(render_coverage_ribbon(bits), unsafe_allow_html=True)
                
                with m_col2:
                    missing_count = v.get('missing_days', 0)
                    if missing_count > 0:
                        # 使用 expander 展示具体日期
                        with st.expander(f"🩹 {missing_count} 天缺口"):
                            parts = k.split(":")
                            missing_list = LAKE.storage.get_missing_days(parts[0], parts[1], parts[2])
                            st.write([d.isoformat() for d in missing_list[:50]])
                    else:
                        st.write("✅ 范围完整")
                
                with m_col3:
                    incomplete_count = v.get('incomplete_days', 0)
                    if incomplete_count > 0:
                        with st.expander(f"⚠️ {incomplete_count} 天异常", help="行数不足的天数"):
                            st.write(v.get('incomplete_list', [])[:50])
                    else:
                        st.write("💎 内容完整")
                
                with m_col4:
                    if st.button("🛠️ 深度修补", key=f"repair_{k}", help="补齐缺失并重刷异常天"):
                        parts = k.split(":")
                        # 清理异常文件以便重新下载
                        for d_str in v.get('incomplete_list', []):
                            try:
                                path = LAKE.storage.get_partition_path(parts[0], parts[1], parts[2], d_str)
                                if path.exists(): path.unlink()
                            except: pass
                        
                        LAKE.start_download([parts[1]], [parts[2]], v['start'], v['end'])
                        st.toast(f"已启动 {parts[1]} 深度修补任务")
                st.markdown("---")
    else:
        st.info("数据湖中暂无分片文件")

# --- 兼容性桥接 (Export) ---
# 检测 Legacy 数据存储路径（兼容 Docker 挂载）
LEGACY_CANDLES_DIR = "data/candles"
if os.path.exists("/tmp/hbot_data/candles"):
    LEGACY_CANDLES_DIR = "/tmp/hbot_data/candles"

with tab3:
    st.subheader("导出至 Hummingbot (Legacy CSV)")
    st.write("将数据湖中的分片合并为 Hummingbot 识别的单一 CSV 文件。")
    
    if selected_pairs:
        target_pair = st.selectbox("选择要导出的币种", selected_pairs)
        target_interval = st.selectbox("选择粒度", selected_intervals)
        
        output_filename = f"binance_{target_pair}_{target_interval}.csv"
        # 转换显示路径，如果是 Docker 内部路径，显示为用户友好的相对路径
        display_path = f"data/candles/{output_filename}"
        st.code(f"目标文件: {display_path}")
        
        if st.button("🖇️ 执行合并并覆盖旧系统数据"):
            from data.data_lake.merger import DataMerger
            merger = DataMerger(LAKE.storage)
            target_path = os.path.join(LEGACY_CANDLES_DIR, output_filename)
            success = merger.auto_merge_full_history("binance", target_pair, target_interval, target_path)
            if success:
                st.success(f"✅ 已成功合并并覆盖 {output_filename}")
            else:
                st.error("❌ 导出失败：请确认数据湖中已下载相关数据")
    else:
        st.warning("请在 Tab 1 中先选择一个币种。")

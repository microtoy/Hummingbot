import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px
from frontend.st_utils import get_backend_api_client, initialize_st_page
import ast
from pathlib import Path
from typing import Dict, List, Any, Optional

# Initialize page
initialize_st_page(title="Batch Backtesting", icon="🚀", layout="wide")
backend_api_client = get_backend_api_client()

def get_custom_strategies_dir() -> Optional[Path]:
    possible_dirs = [
        Path("/home/dashboard/custom_strategies"),
        Path("/app/custom_strategies"),
        Path.cwd() / "custom_strategies",
    ]
    for dir_path in possible_dirs:
        if dir_path.exists() and dir_path.is_dir():
            return dir_path
    return None

def extract_value(node) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, (ast.Num, ast.Str)):
        return getattr(node, 'n', getattr(node, 's', None))
    elif isinstance(node, ast.NameConstant):
        return node.value
    elif isinstance(node, ast.Dict):
        keys = [extract_value(k) for k in node.keys]
        values = [extract_value(v) for v in node.values]
        return dict(zip(keys, values))
    elif isinstance(node, ast.List):
        return [extract_value(e) for e in node.elts]
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "Decimal" and node.args:
                arg = extract_value(node.args[0])
                try: return float(arg)
                except: return 0.0
            elif node.func.id == "Field":
                for kw in node.keywords:
                    if kw.arg == "default":
                        return extract_value(kw.value)
    return None

def extract_controller_info(content: str) -> Dict[str, Any]:
    info = {"config_class": "Unknown", "docstring": "", "parameters": {}}
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if "Config" in node.name:
                    info["config_class"] = node.name
                    info["docstring"] = ast.get_docstring(node) or ""
                    for item in node.body:
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            attr_name = item.target.id
                            if attr_name in ["controller_name", "candles_config", "markets"]:
                                continue
                            value = extract_value(item.value) if item.value is not None else None
                            if value is not None:
                                info["parameters"][attr_name] = value
                    break
    except: pass
    return info

def scan_strategies():
    strategies = []
    strat_dir = get_custom_strategies_dir()
    if not strat_dir: return strategies
    for file_path in strat_dir.glob("*.py"):
        if file_path.name.startswith("__"): continue
        try:
            content = file_path.read_text()
            info = extract_controller_info(content)
            if info["config_class"] != "Unknown":
                strategies.append({
                    "id": file_path.stem,
                    "name": file_path.stem.replace("_", " ").title(),
                    "filename": file_path.name,
                    "info": info
                })
        except: continue
    return strategies

def get_cached_symbols():
    try:
        status = backend_api_client._get("/backtesting/candles/status")
        if isinstance(status, dict) and "cached_files" in status:
            return sorted(list(set([f["file"].replace("binance_", "").replace("_1m.csv", "").replace("_3m.csv", "").replace("_5m.csv", "").replace("_15m.csv", "").replace("_1h.csv", "") for f in status["cached_files"]])))
    except: pass
    return ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT", "ADA-USDT", "DOT-USDT", "DOGE-USDT", "AVAX-USDT", "MATIC-USDT"]

# --- APP UI ---
st.markdown("""
<div style="text-align: center; padding: 1rem 0;">
    <h1 style="font-size: 2.5rem; color: #667eea;">🚀 Parallel Batch Backtesting</h1>
    <p style="color: #888;">Multi-coin strategy verification with M1 Max hardware acceleration</p>
</div>
""", unsafe_allow_html=True)

strategies = scan_strategies()
if not strategies:
    st.error("No V2 Controllers detected.")
    st.stop()

# Sidebar: Strategy & Global Params
with st.sidebar:
    st.header("🎯 Strategy Settings")
    selected_name = st.selectbox("Controller", options=[s["name"] for s in strategies])
    selected_strat = next(s for s in strategies if s["name"] == selected_name)
    
    st.divider()
    st.header("📅 Time Range")
    default_end = datetime.now().date() - timedelta(days=1)
    default_start = default_end - timedelta(days=7)
    start_date = st.date_input("Start", default_start)
    end_date = st.date_input("End", default_end)
    resolution = st.selectbox("Resolution", ["1m", "3m", "5m", "15m", "1h"], index=0)
    cost = st.number_input("Cost (%)", value=0.06, step=0.01)

# Main Area: Coins & Parameters
c1, c2 = st.columns([0.6, 0.4])

with c1:
    st.subheader("🌐 Asset Selection")
    symbols = get_cached_symbols()
    selected_symbols = st.multiselect("Select Trading Pairs", options=symbols, default=symbols[:5] if len(symbols) > 5 else symbols)
    
with c2:
    st.subheader("⚙️ Controller Params")
    params = selected_strat["info"]["parameters"]
    config_params = {}
    if params:
        for p_name, p_val in params.items():
            label = p_name.replace("_", " ").title()
            if isinstance(p_val, bool):
                config_params[p_name] = st.checkbox(label, value=bool(p_val))
            elif isinstance(p_val, (int, float)):
                config_params[p_name] = st.number_input(label, value=float(p_val))
            else:
                config_params[p_name] = st.text_input(label, value=str(p_val))

if st.button("🔥 START PARALLEL BACKTEST", use_container_width=True, type="primary"):
    if not selected_symbols:
        st.warning("Please select at least one symbol.")
    else:
        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
        end_ts = int(datetime.combine(end_date, datetime.max.time()).timestamp())
        
        # Build batch request
        batch_configs = []
        for symbol in selected_symbols:
            cfg = {
                "controller_name": selected_strat["id"],
                "controller_type": "custom",
                "connector_name": "binance", # Defaulting to binance for now
                "trading_pair": symbol,
                **config_params
            }
            batch_configs.append({
                "config": cfg,
                "start_time": start_ts,
                "end_time": end_ts,
                "backtesting_resolution": resolution,
                "trade_cost": cost / 100
            })
            
        with st.status("Executing parallel backtests on M1 Max...", expanded=True) as status:
            st.write(f"Spinning up {len(selected_symbols)} processes...")
            try:
                # Use the internal _post method since batch-run might not be in client libs yet
                response = backend_api_client._post("/backtesting/batch-run", json=batch_configs)
                if response and "results" in response:
                    results = response["results"]
                    status.update(label="✅ Batch Backtesting Complete!", state="complete")
                    
                    df_results = pd.DataFrame(results)
                    
                    # --- ELEGANT PRESENTATION ---
                    st.divider()
                    st.header("🏆 Market Leaderboard")
                    
                    # Sorting options
                    sort_col = st.selectbox("Sort by", ["net_pnl", "sharpe_ratio", "accuracy", "profit_factor"], index=0)
                    df_sorted = df_results.sort_values(sort_col, ascending=False)
                    
                    # Styled Table
                    def color_pnl(val):
                        color = '#2ecc71' if val > 0 else '#e74c3c'
                        return f'color: {color}; font-weight: bold'
                    
                    st.dataframe(
                        df_sorted.style.format({
                            'net_pnl': '{:.2%}',
                            'net_pnl_quote': '${:.2f}',
                            'accuracy': '{:.1%}',
                            'sharpe_ratio': '{:.2f}',
                            'profit_factor': '{:.2f}',
                            'max_drawdown_pct': '{:.2%}'
                        }).applymap(color_pnl, subset=['net_pnl', 'net_pnl_quote']),
                        use_container_width=True,
                        hide_index=True
                    )
                    
                    # Charts
                    st.subheader("📊 Performance Visualization")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # PnL Bar Chart
                        fig_pnl = px.bar(
                            df_sorted, x='trading_pair', y='net_pnl',
                            color='net_pnl', color_continuous_scale='RdYlGn',
                            title="Net PnL % by Token",
                            labels={'net_pnl': 'PnL %', 'trading_pair': 'Asset'}
                        )
                        fig_pnl.update_layout(template="plotly_dark")
                        st.plotly_chart(fig_pnl, use_container_width=True)
                        
                    with col2:
                        # Sharpe vs Accuracy Scatter
                        fig_scatter = px.scatter(
                            df_sorted, x='accuracy', y='sharpe_ratio',
                            size='total_positions', color='net_pnl',
                            hover_name='trading_pair',
                            title="Risk/Reward Map (Size = Strategy Intensity)",
                            labels={'accuracy': 'Win Rate', 'sharpe_ratio': 'Sharpe Ratio'}
                        )
                        fig_scatter.update_layout(template="plotly_dark")
                        st.plotly_chart(fig_scatter, use_container_width=True)
                        
                    # Summary Stats
                    st.divider()
                    s1, s2, s3, s4 = st.columns(4)
                    avg_pnl = df_results['net_pnl'].mean()
                    total_pnl = df_results['net_pnl_quote'].sum()
                    win_rate_market = (df_results['net_pnl'] > 0).mean()
                    
                    s1.metric("Avg PnL %", f"{avg_pnl:.2%}")
                    s2.metric("Total PnL $", f"${total_pnl:,.2f}")
                    s3.metric("Market Win Rate", f"{win_rate_market:.1%}")
                    s4.metric("Best Performer", df_sorted.iloc[0]['trading_pair'])
                    
                else:
                    st.error("Backend returned empty results or error.")
            except Exception as e:
                st.error(f"Execution Error: {e}")

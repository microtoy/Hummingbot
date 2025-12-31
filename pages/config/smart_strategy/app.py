"""
Smart Strategy Manager (V2 Controller Edition)

Features:
1. Scan custom V2 Controllers in custom_strategies/
2. Dynamic parameter UI generation from Pydantic config classes
3. Full Backtesting support via backend custom controller bridge
4. Standardized config versioning and deployment
"""
import ast
from pathlib import Path
from typing import Dict, List, Any, Optional

import streamlit as st

# Internal Components
from frontend.components.backtesting import backtesting_section
from frontend.components.config_loader import get_default_config_loader
from frontend.components.save_config import render_save_config
from frontend.st_utils import get_backend_api_client, initialize_st_page
from frontend.visualization.backtesting import create_backtesting_figure
from frontend.visualization.backtesting_metrics import render_accuracy_metrics, render_backtesting_metrics, render_close_types

# Initialize page
initialize_st_page(title="Smart Strategy", icon="🎯")
backend_api_client = get_backend_api_client()

def get_custom_strategies_dir() -> Optional[Path]:
    """Get the directory of custom strategies inside the container."""
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
    """Extract literal value from AST node."""
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, (ast.Num, ast.Str)): # Compatibility for older python
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
    """Extract V2 Controller info: class name, docstring, and pydantic fields."""
    info = {"config_class": "Unknown", "docstring": "", "parameters": {}}
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Look for Config classes ending in 'Config' or inheriting from something relevant
                if "Config" in node.name:
                    info["config_class"] = node.name
                    info["docstring"] = ast.get_docstring(node) or ""
                    
                    for item in node.body:
                        # Handle type-annotated fields: name: type = Field(...)
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            attr_name = item.target.id
                            # Ignore standard fields
                            if attr_name in ["controller_name", "candles_config", "markets"]:
                                continue
                            
                            value = extract_value(item.value) if item.value else None
                            if value is not None or attr_name != "Unknown":
                                info["parameters"][attr_name] = value or ""
                    break
    except:
        pass
    return info

def scan_strategies() -> List[Dict[str, Any]]:
    """Scan custom strategies directory."""
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

def safe_backtesting_section(inputs, backend_api_client):
    """Custom safe backtesting section."""
    from datetime import datetime, timedelta
    st.write("### Backtesting")
    c1, c2, c3, c4, c5 = st.columns(5)
    default_end_time = datetime.now().date() - timedelta(days=1)
    default_start_time = default_end_time - timedelta(days=2)
    with c1: start_date = st.date_input("Start Date", default_start_time, key="bt_sd")
    with c2: end_date = st.date_input("End Date", default_end_time, key="bt_ed")
    with c3: resolution = st.selectbox("Resolution", ["1m", "3m", "5m", "15m", "1h"], index=0, key="bt_res")
    with c4: cost = st.number_input("Cost (%)", min_value=0.0, value=0.06, step=0.01, key="bt_cost")
    with c5: run_bt = st.button("Run Backtesting", key="bt_run")

    if run_bt:
        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
        end_ts = int(datetime.combine(end_date, datetime.max.time()).timestamp())
        with st.spinner("Executing simulation on backend..."):
            try:
                results = backend_api_client.backtesting.run_backtesting(
                    start_time=start_ts, end_time=end_ts, backtesting_resolution=resolution,
                    trade_cost=cost / 100, config=inputs
                )
                if not results: return None
                if "error" in results:
                    st.error(f"Backend Error: {results['error']}")
                    return None
                return results
            except Exception as e:
                st.error(f"Connection failed: {e}")
                return None
    return None

# --- UI START ---
st.text("Create, test and deploy your custom V2 Controllers.")

strategies = scan_strategies()
if not strategies:
    st.error("No valid V2 Controllers found. Please check your file structure.")
    st.stop()

selected_name = st.selectbox("Select Custom Controller", options=[s["name"] for s in strategies])
selected_strat = next(s for s in strategies if s["name"] == selected_name)

# Loader
get_default_config_loader(selected_strat["id"])

st.write(f"### {selected_strat['info']['config_class']}")
if selected_strat["info"]["docstring"]:
    st.info(selected_strat["info"]["docstring"])

# Config Setup
if "default_config" not in st.session_state or not st.session_state["default_config"]:
    st.session_state["default_config"] = {"id": f"{selected_strat['id']}_0.1"}

config = st.session_state["default_config"]
config["controller_name"] = selected_strat["id"]
config["controller_type"] = "custom" # Key change: point to bots.controllers.custom

# Market Config
with st.expander("Market Configuration", expanded=True):
    c1, c2 = st.columns(2)
    config["connector_name"] = c1.text_input("Connector", value=config.get("connector_name", "binance_paper_trade"))
    config["trading_pair"] = c2.text_input("Trading Pair", value=config.get("trading_pair", "BTC-USDT"))

# Dynamic Params
params = selected_strat["info"]["parameters"]
if params:
    with st.expander("Controller Parameters", expanded=True):
        items = list(params.items())
        for i in range(0, len(items), 3):
            cols = st.columns(min(3, len(items) - i))
            for col, (p_name, p_val) in zip(cols, items[i:i+3]):
                cur_val = config.get(p_name, p_val)
                label = p_name.replace("_", " ").title()
                if isinstance(p_val, bool):
                    config[p_name] = col.checkbox(label, value=bool(cur_val), key=f"ui_{p_name}")
                elif isinstance(p_val, (int, float)):
                    config[p_name] = col.number_input(label, value=float(cur_val), key=f"ui_{p_name}")
                else:
                    config[p_name] = col.text_input(label, value=str(cur_val), key=f"ui_{p_name}")

# Backtesting
bt_results = safe_backtesting_section(config, backend_api_client)
if bt_results:
    fig = create_backtesting_figure(df=bt_results["processed_data"], executors=bt_results["executors"], config=config)
    c1, c2 = st.columns([0.9, 0.1])
    with c1:
        render_backtesting_metrics(bt_results["results"])
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        render_accuracy_metrics(bt_results["results"])
        st.write("---")
        render_close_types(bt_results["results"])

# Save
st.write("---")
render_save_config(config["id"], config)

with st.expander("📜 Controller Source", expanded=False):
    st.code((get_custom_strategies_dir() / selected_strat["filename"]).read_text(), language="python")

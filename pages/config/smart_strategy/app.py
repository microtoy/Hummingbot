"""
Smart Strategy Manager

Features:
1. Scan custom strategy files in custom_strategies/
2. Dynamic parameter UI generation via AST parsing
3. Integrated Backtesting with visualization
4. Config versioning and saving to Backend API
5. Consistent UI/UX with built-in Hummingbot strategies
"""
import ast
import os
import re
import time
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
    elif isinstance(node, ast.Num):
        return node.n
    elif isinstance(node, ast.Str):
        return node.s
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
                try:
                    return float(arg)
                except:
                    return 0.0
    return None

def extract_strategy_info(content: str) -> Dict[str, Any]:
    """Extract class name, docstring, and parameters from strategy code."""
    info = {"class_name": "Unknown", "docstring": "", "parameters": {}, "markets": {}}
    try:
        tree = ast.parse(content)
        info["docstring"] = ast.get_docstring(tree) or ""
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Check inheritance
                is_strategy = any("Strategy" in (b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "") for b in node.bases)
                if is_strategy:
                    info["class_name"] = node.name
                    if not info["docstring"]:
                        info["docstring"] = ast.get_docstring(node) or ""
                    
                    for item in node.body:
                        if isinstance(item, (ast.Assign, ast.AnnAssign)):
                            target = item.targets[0] if isinstance(item, ast.Assign) else item.target
                            if isinstance(target, ast.Name):
                                attr_name = target.id
                                value = extract_value(item.value) if item.value else None
                                if attr_name == "markets":
                                    info["markets"] = value
                                elif not attr_name.startswith("_") and value is not None:
                                    info["parameters"][attr_name] = value
                    break
    except:
        pass
    return info

def scan_strategies() -> List[Dict[str, Any]]:
    """Scan the custom strategies directory and return list of strategy infos."""
    strategies = []
    strat_dir = get_custom_strategies_dir()
    if not strat_dir:
        return strategies
    
    for file_path in strat_dir.glob("*.py"):
        if file_path.name.startswith("__"):
            continue
        try:
            content = file_path.read_text()
            info = extract_strategy_info(content)
            strategies.append({
                "id": file_path.stem,
                "name": file_path.stem.replace("_", " ").title(),
                "filename": file_path.name,
                "info": info
            })
        except:
            continue
    return strategies

# --- UI START ---

st.text("Automate the configuration and backtesting of your custom scripts.")

# 1. Strategy Selection
strategies = scan_strategies()
if not strategies:
    st.error("No custom strategies found in `custom_strategies/` directory.")
    st.info("Please sync your strategies via GitHub or upload them manually.")
    st.stop()

strategy_map = {s["name"]: s for s in strategies}
selected_name = st.selectbox("Select Strategy", options=list(strategy_map.keys()))
selected_strat = strategy_map[selected_name]

# 2. Config Loader (Built-in style)
get_default_config_loader(selected_strat["id"])

st.write(f"### {selected_strat['info']['class_name']}")
if selected_strat["info"]["docstring"]:
    st.info(selected_strat["info"]["docstring"])

# 3. Parameter Inputs
st.write("### Strategy Configuration")
params = selected_strat["info"]["parameters"]
markets = selected_strat["info"]["markets"]

# Initialize session state with default config if empty
if "default_config" not in st.session_state or not st.session_state["default_config"]:
    st.session_state["default_config"] = {
        "id": f"{selected_strat['id']}_0.1",
        "controller_name": selected_strat["id"],
        "controller_type": "script",
    }

# Sync with currently editing config
current_config = st.session_state["default_config"]

# Markets display/edit
with st.expander("Market Configuration", expanded=True):
    col1, col2 = st.columns(2)
    # Extract first connector/trading pair if available
    default_connector = "binance_paper_trade"
    default_pair = "BTC-USDT"
    if markets and isinstance(markets, dict):
        default_connector = list(markets.keys())[0]
        if isinstance(markets[default_connector], (list, set)):
            default_pair = list(markets[default_connector])[0]
        elif isinstance(markets[default_connector], str):
            default_pair = markets[default_connector]
    
    connector_name = col1.text_input("Connector", value=current_config.get("connector_name", default_connector))
    trading_pair = col2.text_input("Trading Pair", value=current_config.get("trading_pair", default_pair))
    
    current_config["connector_name"] = connector_name
    current_config["trading_pair"] = trading_pair

# Dynamic parameters
if params:
    with st.expander("Custom Parameters", expanded=True):
        param_items = list(params.items())
        cols_per_row = 3
        for i in range(0, len(param_items), cols_per_row):
            row_items = param_items[i:i+cols_per_row]
            cols = st.columns(len(row_items))
            for col, (p_name, p_val) in zip(cols, row_items):
                display_name = p_name.replace("_", " ").title()
                val = current_config.get(p_name, p_val)
                
                if isinstance(p_val, bool):
                    new_val = col.checkbox(display_name, value=bool(val), key=f"p_{p_name}")
                elif isinstance(p_val, int):
                    new_val = col.number_input(display_name, value=int(val), step=1, key=f"p_{p_name}")
                elif isinstance(p_val, float):
                    new_val = col.number_input(display_name, value=float(val), format="%.6f", key=f"p_{p_name}")
                else:
                    new_val = col.text_input(display_name, value=str(val), key=f"p_{p_name}")
                
                current_config[p_name] = new_val

# Update common fields
current_config["controller_name"] = selected_strat["id"]
current_config["controller_type"] = "script"
current_config["script_file"] = selected_strat["filename"]

def safe_backtesting_section(inputs, backend_api_client):
    """
    A safe version of the backtesting section that handles API errors gracefully.
    Built-in component crashes when the API returns an error dictionary.
    """
    from datetime import datetime, timedelta
    st.write("### Backtesting")
    c1, c2, c3, c4, c5 = st.columns(5)
    default_end_time = datetime.now().date() - timedelta(days=1)
    default_start_time = default_end_time - timedelta(days=2)
    with c1:
        start_date = st.date_input("Start Date", default_start_time, key="bt_start_date")
    with c2:
        end_date = st.date_input("End Date", default_end_time,
                                 help="End date is inclusive, make sure that you are not including the current date.", key="bt_end_date")
    with c3:
        backtesting_resolution = st.selectbox("Backtesting Resolution",
                                              options=["1m", "3m", "5m", "15m", "30m", "1h", "1s"], index=0, key="bt_resolution")
    with c4:
        trade_cost = st.number_input("Trade Cost (%)", min_value=0.0, value=0.06, step=0.01, format="%.2f", key="bt_cost_input")
    with c5:
        run_backtesting = st.button("Run Backtesting", key="bt_run_button")

    if run_backtesting:
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        with st.spinner("Running backtesting..."):
            try:
                results = backend_api_client.backtesting.run_backtesting(
                    start_time=int(start_datetime.timestamp()),
                    end_time=int(end_datetime.timestamp()),
                    backtesting_resolution=backtesting_resolution,
                    trade_cost=trade_cost / 100,
                    config=inputs,
                )
                if not results:
                    st.error("No response from the backtesting API.")
                    return None
                if "error" in results:
                    st.error(f"Backend Error: {results['error']}")
                    with st.expander("Show detailed error response"):
                        st.json(results)
                    return None
                if "processed_data" not in results:
                    st.error("API Response missing 'processed_data' key.")
                    st.json(results)
                    return None
                if len(results["processed_data"]) == 0:
                    st.warning("No trades were executed during the selected period.")
                    return None
                return results
            except Exception as e:
                st.error(f"Failed to connect to backtesting service: {e}")
                return None
    return None

# 4. Backtesting Section
bt_results = safe_backtesting_section(current_config, backend_api_client)

if bt_results:
    try:
        fig = create_backtesting_figure(
            df=bt_results["processed_data"],
            executors=bt_results["executors"],
            config=current_config
        )
        c1, c2 = st.columns([0.9, 0.1])
        with c1:
            render_backtesting_metrics(bt_results["results"])
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            render_accuracy_metrics(bt_results["results"])
            st.write("---")
            render_close_types(bt_results["results"])
    except Exception as e:
        st.error(f"Error rendering backtesting results: {e}")
        st.json(bt_results)

# 5. Save/Upload Section
st.write("---")
render_save_config(current_config["id"], current_config)

# 6. Source Preview (Optional)
with st.expander("📜 Script Source Code", expanded=False):
    strat_dir = get_custom_strategies_dir()
    if strat_dir:
        code = (strat_dir / selected_strat["filename"]).read_text()
        st.code(code, language="python")

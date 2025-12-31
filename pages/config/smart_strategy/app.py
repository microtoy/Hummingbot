"""
智能策略管理页面 - Smart Strategy Manager

功能:
1. 扫描并列出所有自定义策略文件
2. 解析策略类的参数(使用 AST)
3. 动态生成参数配置界面
4. 支持保存配置版本
5. 部署机器人实例

参考内置策略页面(如 pmm_dynamic)的结构设计
"""
import ast
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

import streamlit as st

from frontend.st_utils import get_backend_api_client, initialize_st_page
from frontend.components.save_config import render_save_config

initialize_st_page(title="Smart Strategy", icon="🎯", show_readme=False)

# Initialize backend client
backend_api_client = get_backend_api_client()


def get_custom_strategies_dir() -> Optional[Path]:
    """获取自定义策略目录路径"""
    possible_dirs = [
        Path("/home/dashboard/custom_strategies"),
        Path("/app/custom_strategies"),
        Path("custom_strategies"),
        Path.cwd() / "custom_strategies",
        Path.cwd().parent / "custom_strategies",
    ]
    
    for dir_path in possible_dirs:
        if dir_path.exists() and dir_path.is_dir():
            return dir_path
    return None


def scan_strategy_files() -> List[Dict[str, Any]]:
    """扫描自定义策略目录,返回所有策略文件信息"""
    strategies = []
    
    strategies_dir = get_custom_strategies_dir()
    
    if not strategies_dir:
        return strategies
    
    for file_path in strategies_dir.glob("*.py"):
        if file_path.name.startswith("__"):
            continue
        
        try:
            content = file_path.read_text()
            docstring = extract_docstring(content)
            class_info = extract_class_info(content)
            
            strategies.append({
                "filename": file_path.name,
                "filepath": str(file_path),
                "name": file_path.stem,
                "display_name": file_path.stem.replace("_", " ").title(),
                "docstring": docstring or "无描述",
                "class_name": class_info.get("class_name", "Unknown"),
                "parameters": class_info.get("parameters", {}),
                "markets": class_info.get("markets", {}),
                "content": content,
            })
        except Exception as e:
            strategies.append({
                "filename": file_path.name,
                "name": file_path.stem,
                "display_name": file_path.stem.replace("_", " ").title(),
                "docstring": f"解析错误: {str(e)}",
                "class_name": "Unknown",
                "parameters": {},
                "markets": {},
                "error": str(e),
            })
    
    return strategies


def extract_docstring(content: str) -> Optional[str]:
    """提取模块或类的文档字符串"""
    try:
        tree = ast.parse(content)
        docstring = ast.get_docstring(tree)
        if docstring:
            return docstring
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_doc = ast.get_docstring(node)
                if class_doc:
                    return class_doc
    except:
        pass
    
    match = re.search(r'"""(.*?)"""', content, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    return None


def extract_class_info(content: str) -> Dict[str, Any]:
    """从策略文件中提取类信息和参数"""
    result = {
        "class_name": "Unknown",
        "parameters": {},
        "markets": {},
    }
    
    try:
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = ""
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    
                    if "Strategy" in base_name or "Script" in base_name:
                        result["class_name"] = node.name
                        
                        for item in node.body:
                            if isinstance(item, ast.Assign):
                                for target in item.targets:
                                    if isinstance(target, ast.Name):
                                        attr_name = target.id
                                        value = extract_value(item.value)
                                        
                                        if attr_name == "markets":
                                            result["markets"] = value
                                        elif not attr_name.startswith("_"):
                                            result["parameters"][attr_name] = {
                                                "name": attr_name,
                                                "display_name": attr_name.replace("_", " ").title(),
                                                "default": value,
                                                "type": infer_type(value),
                                            }
                            
                            elif isinstance(item, ast.AnnAssign):
                                if isinstance(item.target, ast.Name):
                                    attr_name = item.target.id
                                    value = extract_value(item.value) if item.value else None
                                    
                                    if not attr_name.startswith("_"):
                                        result["parameters"][attr_name] = {
                                            "name": attr_name,
                                            "display_name": attr_name.replace("_", " ").title(),
                                            "default": value,
                                            "type": get_annotation_type(item.annotation),
                                        }
                        break
    except Exception as e:
        result["error"] = str(e)
    
    return result


def extract_value(node) -> Any:
    """从 AST 节点提取值"""
    if node is None:
        return None
    
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
    elif isinstance(node, ast.Set):
        return set(extract_value(e) for e in node.elts)
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "Decimal" and node.args:
                arg = extract_value(node.args[0])
                return float(arg) if arg else 0.0
        return None
    
    return None


def infer_type(value) -> str:
    """推断值的类型"""
    if value is None:
        return "str"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, (list, set)):
        return "list"
    return "str"


def get_annotation_type(annotation) -> str:
    """获取类型注解的字符串表示"""
    if isinstance(annotation, ast.Name):
        return annotation.id.lower()
    elif isinstance(annotation, ast.Subscript):
        return get_annotation_type(annotation.value)
    elif isinstance(annotation, ast.Attribute):
        return annotation.attr.lower()
    return "str"


def create_parameter_input(param_name: str, param_info: Dict, key_prefix: str) -> Any:
    """根据参数信息创建单个输入控件"""
    default = param_info.get("default")
    param_type = param_info.get("type", "str")
    display_name = param_info.get("display_name", param_name)
    
    # 从 session state 获取已保存的值
    config = st.session_state.get("default_config", {})
    current_value = config.get(param_name, default)
    
    key = f"{key_prefix}_{param_name}"
    
    if param_type == "bool" or isinstance(default, bool):
        return st.checkbox(display_name, value=bool(current_value) if current_value is not None else False, key=key)
    elif param_type == "int" or isinstance(default, int):
        return st.number_input(display_name, value=int(current_value) if current_value is not None else 0, step=1, key=key)
    elif param_type in ["float", "decimal"] or isinstance(default, float):
        return st.number_input(display_name, value=float(current_value) if current_value is not None else 0.0, format="%.6f", key=key)
    elif isinstance(default, dict):
        return st.text_area(display_name, value=str(current_value) if current_value else "{}", key=key)
    else:
        return st.text_input(display_name, value=str(current_value) if current_value is not None else "", key=key)


def load_existing_configs(strategy_name: str) -> List[Dict]:
    """加载指定策略的已保存配置"""
    try:
        all_configs = backend_api_client.controllers.list_controller_configs()
        return [c for c in all_configs if c.get("config", {}).get("controller_name") == strategy_name]
    except Exception:
        return []


def custom_config_loader(strategy_name: str, parameters: Dict):
    """自定义配置加载器,类似 get_default_config_loader"""
    
    # 初始化 session state
    if "default_config" not in st.session_state:
        st.session_state["default_config"] = {
            "id": f"{strategy_name}_v1",
            "controller_name": strategy_name,
            "controller_type": "script",
        }
    
    # 加载已保存的配置
    existing_configs = load_existing_configs(strategy_name)
    
    with st.expander("📂 配置管理", expanded=False):
        if existing_configs:
            config_names = ["新建配置"] + [c.get("id", "Unknown") for c in existing_configs]
            selected_config = st.selectbox("加载已保存的配置", config_names)
            
            if selected_config != "新建配置":
                for c in existing_configs:
                    if c.get("id") == selected_config:
                        st.session_state["default_config"] = c.get("config", c)
                        st.session_state["default_config"]["id"] = selected_config
                        st.success(f"已加载配置: {selected_config}")
                        break
        else:
            st.info("没有已保存的配置,将创建新配置")
        
        # 配置 ID
        config_id = st.text_input(
            "配置 ID (版本名)",
            value=st.session_state["default_config"].get("id", f"{strategy_name}_v1"),
            help="用于标识此配置版本,格式: 策略名_版本号"
        )
        st.session_state["default_config"]["id"] = config_id


# ==================== 页面主体 ====================

st.title("🎯 智能策略管理")
st.text("自动扫描和配置自定义交易策略,支持保存版本和一键部署")

# 扫描策略文件
strategies = scan_strategy_files()

if not strategies:
    st.warning("""
    ⚠️ **未找到自定义策略文件**
    
    请将策略文件放入 `custom_strategies/` 目录,然后刷新页面。
    
    **策略文件要求:**
    - 文件扩展名为 `.py`
    - 包含继承自 `ScriptStrategyBase` 的类
    - 文件名不要以 `__` 开头
    """)
    
    with st.expander("🔍 调试信息"):
        st.write("**检查的目录:**")
        possible_dirs = [
            Path("/home/dashboard/custom_strategies"),
            Path("/app/custom_strategies"),
            Path("custom_strategies"),
            Path.cwd() / "custom_strategies",
        ]
        for dir_path in possible_dirs:
            exists = "✅ 存在" if dir_path.exists() else "❌ 不存在"
            st.write(f"- `{dir_path}`: {exists}")
        
        st.write(f"\n**当前工作目录:** `{Path.cwd()}`")
else:
    # 显示策略选择器
    st.success(f"📂 找到 {len(strategies)} 个自定义策略")
    
    # 策略选择下拉框
    strategy_options = {s["display_name"]: s for s in strategies}
    selected_strategy_name = st.selectbox(
        "选择策略",
        options=list(strategy_options.keys()),
        help="选择要配置的自定义策略"
    )
    
    selected_strategy = strategy_options[selected_strategy_name]
    
    st.divider()
    
    # 策略信息
    st.markdown(f"### 📜 {selected_strategy['class_name']}")
    st.markdown(f"**文件:** `{selected_strategy['filename']}`")
    st.markdown(f"**描述:** {selected_strategy['docstring']}")
    
    # 显示交易市场配置
    if selected_strategy.get("markets"):
        with st.expander("🏪 交易市场配置", expanded=True):
            st.json(selected_strategy["markets"])
    
    st.divider()
    
    # 配置加载器
    custom_config_loader(selected_strategy["name"], selected_strategy.get("parameters", {}))
    
    # 参数配置区域
    st.markdown("### ⚙️ 策略参数配置")
    
    parameters = selected_strategy.get("parameters", {})
    
    if parameters:
        # 过滤掉 markets 参数
        config_params = {k: v for k, v in parameters.items() if k != "markets"}
        
        if config_params:
            with st.expander(f"{selected_strategy['display_name']} 参数设置", expanded=True):
                # 按行显示参数,每行最多 4 个
                param_list = list(config_params.items())
                cols_per_row = 4
                
                for i in range(0, len(param_list), cols_per_row):
                    row_params = param_list[i:i + cols_per_row]
                    cols = st.columns(len(row_params))
                    
                    for col, (param_name, param_info) in zip(cols, row_params):
                        with col:
                            value = create_parameter_input(
                                param_name, 
                                param_info, 
                                key_prefix=selected_strategy["name"]
                            )
                            # 更新到配置中
                            st.session_state["default_config"][param_name] = value
        else:
            st.info("此策略没有可配置的参数")
    else:
        st.info("此策略没有可配置的参数")
    
    # 更新配置中的策略信息
    st.session_state["default_config"]["controller_name"] = selected_strategy["name"]
    st.session_state["default_config"]["controller_type"] = "script"
    st.session_state["default_config"]["script_file"] = selected_strategy["filename"]
    
    st.divider()
    
    # 保存配置区域 - 使用内置组件
    st.markdown("### 💾 保存配置")
    
    try:
        render_save_config(
            st.session_state["default_config"]["id"], 
            st.session_state["default_config"]
        )
    except Exception as e:
        # 如果内置组件失败,使用自定义保存逻辑
        st.warning(f"使用简化保存模式 (内置组件不可用: {e})")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 保存配置", type="primary", use_container_width=True):
                try:
                    config_id = st.session_state["default_config"]["id"]
                    config_data = st.session_state["default_config"].copy()
                    
                    # 尝试保存配置
                    backend_api_client.controllers.add_controller_config(
                        config_id=config_id,
                        config=config_data
                    )
                    st.success(f"✅ 配置已保存: {config_id}")
                except Exception as save_error:
                    st.error(f"保存失败: {save_error}")
        
        with col2:
            if st.button("🔄 重置配置", use_container_width=True):
                for key in list(st.session_state.keys()):
                    if key.startswith(selected_strategy["name"]):
                        del st.session_state[key]
                st.session_state["default_config"] = {
                    "id": f"{selected_strategy['name']}_v1",
                    "controller_name": selected_strategy["name"],
                    "controller_type": "script",
                }
                st.rerun()
    
    st.divider()
    
    # 部署区域
    st.markdown("### 🚀 部署机器人")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        bot_name = st.text_input(
            "机器人名称",
            value=f"{selected_strategy['name']}-bot",
            key="deploy_bot_name"
        )
    
    with col2:
        try:
            available_credentials = backend_api_client.accounts.list_accounts()
            credentials = st.selectbox(
                "凭证配置",
                options=available_credentials,
                index=0,
                key="deploy_credentials"
            )
        except:
            credentials = st.text_input("凭证配置", value="master_account", key="deploy_credentials")
    
    with col3:
        image_name = st.text_input(
            "Docker 镜像",
            value="hummingbot/hummingbot:latest",
            key="deploy_image"
        )
    
    if st.button("🚀 部署机器人", type="primary", use_container_width=True):
        with st.spinner("正在部署..."):
            try:
                start_time_str = time.strftime("%Y%m%d-%H%M")
                full_bot_name = f"{bot_name}-{start_time_str}"
                
                # 获取配置 ID
                config_id = st.session_state["default_config"]["id"]
                
                # 使用 V2 Controllers 部署
                backend_api_client.bot_orchestration.deploy_v2_controllers(
                    instance_name=full_bot_name,
                    credentials_profile=credentials,
                    controllers_config=[config_id],
                    image=image_name,
                )
                st.success(f"✅ 成功部署机器人: {full_bot_name}")
                time.sleep(2)
                
            except Exception as e:
                st.error(f"❌ 部署失败: {e}")
                st.info("请确保已先保存配置,然后再部署")
    
    st.divider()
    
    # 预览配置
    with st.expander("📋 当前配置预览", expanded=False):
        st.json(st.session_state.get("default_config", {}))
    
    # 查看源代码
    with st.expander("📜 策略源代码", expanded=False):
        st.code(selected_strategy.get("content", "无法加载源代码"), language="python")

# 页脚
st.markdown("---")
st.caption("""
**智能策略管理** | 自动扫描 `custom_strategies/` 目录 | Git push 后自动同步
""")

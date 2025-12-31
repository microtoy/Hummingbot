"""
智能策略管理页面

功能:
1. 扫描并列出所有自定义策略文件
2. 解析策略类的参数
3. 动态生成参数配置界面
4. 支持创建策略配置
5. 部署机器人实例
"""
import ast
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

import streamlit as st

from frontend.st_utils import get_backend_api_client, initialize_st_page

initialize_st_page(icon="🎯", show_readme=False)

# Initialize backend client
backend_api_client = get_backend_api_client()

# 自定义策略目录路径
CUSTOM_STRATEGIES_DIR = Path("/app/custom_strategies") if os.path.exists("/app/custom_strategies") else Path("custom_strategies")


def scan_strategy_files() -> List[Dict[str, str]]:
    """扫描自定义策略目录,返回所有策略文件信息"""
    strategies = []
    
    # 检查多个可能的目录
    possible_dirs = [
        Path("/app/custom_strategies"),
        Path("custom_strategies"),
        Path("/home/dashboard/custom_strategies"),
        Path.cwd() / "custom_strategies",
    ]
    
    strategies_dir = None
    for dir_path in possible_dirs:
        if dir_path.exists():
            strategies_dir = dir_path
            break
    
    if not strategies_dir or not strategies_dir.exists():
        return strategies
    
    for file_path in strategies_dir.glob("*.py"):
        if file_path.name.startswith("__"):
            continue
        
        # 读取文件内容
        try:
            content = file_path.read_text()
            
            # 提取文档字符串
            docstring = extract_docstring(content)
            
            # 提取类信息
            class_info = extract_class_info(content)
            
            strategies.append({
                "filename": file_path.name,
                "filepath": str(file_path),
                "name": file_path.stem,
                "docstring": docstring or "无描述",
                "class_name": class_info.get("class_name", "Unknown"),
                "parameters": class_info.get("parameters", {}),
                "markets": class_info.get("markets", {}),
            })
        except Exception as e:
            strategies.append({
                "filename": file_path.name,
                "filepath": str(file_path),
                "name": file_path.stem,
                "docstring": f"解析错误: {str(e)}",
                "class_name": "Unknown",
                "parameters": {},
                "markets": {},
            })
    
    return strategies


def extract_docstring(content: str) -> Optional[str]:
    """提取模块或类的文档字符串"""
    try:
        tree = ast.parse(content)
        docstring = ast.get_docstring(tree)
        if docstring:
            return docstring
        
        # 查找类的文档字符串
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_doc = ast.get_docstring(node)
                if class_doc:
                    return class_doc
    except:
        pass
    
    # 尝试正则匹配
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
                # 检查是否继承自策略基类
                for base in node.bases:
                    base_name = ""
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    
                    if "Strategy" in base_name or "Script" in base_name:
                        result["class_name"] = node.name
                        
                        # 提取类属性
                        for item in node.body:
                            if isinstance(item, ast.Assign):
                                for target in item.targets:
                                    if isinstance(target, ast.Name):
                                        attr_name = target.id
                                        
                                        # 提取参数值
                                        value = extract_value(item.value)
                                        
                                        if attr_name == "markets":
                                            result["markets"] = value
                                        elif not attr_name.startswith("_"):
                                            result["parameters"][attr_name] = {
                                                "default": value,
                                                "type": type(value).__name__ if value is not None else "str"
                                            }
                            
                            # 处理带类型注解的赋值
                            elif isinstance(item, ast.AnnAssign):
                                if isinstance(item.target, ast.Name):
                                    attr_name = item.target.id
                                    value = extract_value(item.value) if item.value else None
                                    
                                    if not attr_name.startswith("_"):
                                        result["parameters"][attr_name] = {
                                            "default": value,
                                            "type": get_annotation_type(item.annotation)
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
        return {extract_value(e) for e in node.elts}
    elif isinstance(node, ast.Call):
        # 处理 Decimal() 等调用
        if isinstance(node.func, ast.Name):
            if node.func.id == "Decimal" and node.args:
                return float(extract_value(node.args[0]))
        return f"<{node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id}>"
    
    return str(node)


def get_annotation_type(annotation) -> str:
    """获取类型注解的字符串表示"""
    if isinstance(annotation, ast.Name):
        return annotation.id
    elif isinstance(annotation, ast.Subscript):
        return f"{get_annotation_type(annotation.value)}[...]"
    elif isinstance(annotation, ast.Attribute):
        return annotation.attr
    return "str"


def create_parameter_inputs(parameters: Dict[str, Dict]) -> Dict[str, Any]:
    """根据参数定义创建 Streamlit 输入控件"""
    values = {}
    
    for param_name, param_info in parameters.items():
        default = param_info.get("default")
        param_type = param_info.get("type", "str")
        
        label = param_name.replace("_", " ").title()
        
        if param_type in ["int", "integer"]:
            values[param_name] = st.number_input(
                label,
                value=int(default) if default is not None else 0,
                step=1,
                key=f"param_{param_name}"
            )
        elif param_type in ["float", "Decimal", "decimal"]:
            values[param_name] = st.number_input(
                label,
                value=float(default) if default is not None else 0.0,
                format="%.6f",
                key=f"param_{param_name}"
            )
        elif param_type in ["bool", "boolean"]:
            values[param_name] = st.checkbox(
                label,
                value=bool(default) if default is not None else False,
                key=f"param_{param_name}"
            )
        elif isinstance(default, dict):
            values[param_name] = st.text_area(
                label,
                value=str(default),
                key=f"param_{param_name}"
            )
        else:
            values[param_name] = st.text_input(
                label,
                value=str(default) if default is not None else "",
                key=f"param_{param_name}"
            )
    
    return values


def deploy_script_bot(bot_name: str, script_file: str, credentials: str, 
                     image: str, parameters: Dict[str, Any]) -> bool:
    """部署脚本策略机器人"""
    try:
        start_time_str = time.strftime("%Y%m%d-%H%M")
        full_bot_name = f"{bot_name}-{start_time_str}"
        
        # 构建部署配置
        deploy_config = {
            "instance_name": full_bot_name,
            "script": script_file,
            "credentials_profile": credentials,
            "image": image,
        }
        
        # 添加参数
        if parameters:
            deploy_config["script_config"] = parameters
        
        # 尝试使用脚本部署 API
        try:
            backend_api_client.bot_orchestration.deploy_script(
                instance_name=full_bot_name,
                script=script_file,
                credentials_profile=credentials,
                image=image,
            )
            st.success(f"✅ 成功部署机器人: {full_bot_name}")
            return True
        except AttributeError:
            # 如果没有 deploy_script 方法,使用通用部署
            st.warning("使用通用部署方式...")
            
            # 使用 V2 controllers 部署作为备选
            backend_api_client.bot_orchestration.deploy_v2_controllers(
                instance_name=full_bot_name,
                credentials_profile=credentials,
                controllers_config=[],  # 空配置
                image=image,
            )
            st.success(f"✅ 成功创建机器人实例: {full_bot_name}")
            st.info(f"请手动配置脚本: {script_file}")
            return True
            
    except Exception as e:
        st.error(f"❌ 部署失败: {e}")
        return False


# ==================== 页面主体 ====================

st.title("🎯 智能策略管理")
st.subheader("管理和部署自定义交易策略")

# 刷新按钮
col1, col2 = st.columns([3, 1])
with col2:
    if st.button("🔄 刷新策略列表", use_container_width=True):
        st.rerun()

st.divider()

# 扫描策略文件
strategies = scan_strategy_files()

if not strategies:
    st.warning("""
    ⚠️ **未找到自定义策略文件**
    
    请将策略文件放入 `custom_strategies/` 目录,然后刷新页面。
    
    **策略文件要求:**
    - 文件扩展名为 `.py`
    - 包含继承自 `ScriptStrategyBase` 的类
    - 不要以 `__` 开头
    
    **示例路径:** `custom_strategies/my_strategy.py`
    """)
    
    # 显示目录信息
    with st.expander("🔍 调试信息"):
        st.write("检查的目录:")
        possible_dirs = [
            Path("/app/custom_strategies"),
            Path("custom_strategies"),
            Path("/home/dashboard/custom_strategies"),
            Path.cwd() / "custom_strategies",
        ]
        for dir_path in possible_dirs:
            exists = "✅" if dir_path.exists() else "❌"
            st.write(f" {exists} {dir_path}")
else:
    # 策略列表
    st.success(f"📂 找到 {len(strategies)} 个自定义策略")
    
    # 创建标签页
    tabs = st.tabs([s["name"] for s in strategies])
    
    for idx, (tab, strategy) in enumerate(zip(tabs, strategies)):
        with tab:
            st.markdown(f"### 📜 {strategy['class_name']}")
            st.markdown(f"**文件:** `{strategy['filename']}`")
            st.markdown(f"**描述:** {strategy['docstring']}")
            
            # 显示市场配置
            if strategy.get("markets"):
                with st.expander("🏪 交易市场配置", expanded=True):
                    st.json(strategy["markets"])
            
            # 参数配置
            if strategy.get("parameters"):
                with st.expander("⚙️ 策略参数", expanded=True):
                    st.markdown("**当前参数设置:**")
                    
                    # 创建参数输入
                    param_values = create_parameter_inputs(strategy["parameters"])
            else:
                param_values = {}
                st.info("此策略没有可配置的参数")
            
            st.divider()
            
            # 部署配置
            st.markdown("### 🚀 部署配置")
            
            col1, col2 = st.columns(2)
            
            with col1:
                bot_name = st.text_input(
                    "机器人名称",
                    value=f"{strategy['name']}-bot",
                    key=f"bot_name_{idx}"
                )
            
            with col2:
                try:
                    available_credentials = backend_api_client.accounts.list_accounts()
                    credentials = st.selectbox(
                        "凭证配置",
                        options=available_credentials,
                        index=0,
                        key=f"credentials_{idx}"
                    )
                except:
                    credentials = st.text_input(
                        "凭证配置",
                        value="master_account",
                        key=f"credentials_{idx}"
                    )
            
            image_name = st.text_input(
                "Docker 镜像",
                value="hummingbot/hummingbot:latest",
                key=f"image_{idx}"
            )
            
            st.divider()
            
            # 部署按钮
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("💾 保存配置", key=f"save_{idx}", use_container_width=True):
                    st.success("配置已保存到会话中")
                    st.session_state[f"strategy_config_{strategy['name']}"] = {
                        "parameters": param_values,
                        "bot_name": bot_name,
                        "credentials": credentials,
                        "image": image_name,
                    }
            
            with col2:
                if st.button("🚀 部署机器人", key=f"deploy_{idx}", type="primary", use_container_width=True):
                    with st.spinner("正在部署..."):
                        if deploy_script_bot(
                            bot_name=bot_name,
                            script_file=strategy["filename"],
                            credentials=credentials,
                            image=image_name,
                            parameters=param_values
                        ):
                            time.sleep(2)
                            st.rerun()

# 页脚
st.divider()
st.markdown("""
---
**提示:** 
- 将策略文件放入 `custom_strategies/` 目录
- Git push 后,云端会在 5 分钟内自动同步
- 刷新此页面查看新策略
""")

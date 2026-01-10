# -*- coding: utf-8 -*-
"""
配置服务 - 处理config.py文件的解析和数据处理

回测网页版 | 邢不行 | 2025分享会
author: 邢不行
微信: xbx6660
"""


import importlib.util
import inspect
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from types import ModuleType

from utils.constant import is_debug
from utils.log_kit import get_logger
from utils.path_kit import get_file_path, get_folder_path, get_backtest_file_path
from model.config_model import BacktestConfig, create_config_from_dict, create_strategy_from_dict


# 初始化日志记录器
logger = get_logger()


class MockModule:
    """模拟模块类，用于处理缺失的依赖模块"""

    def __getattr__(self, name):
        if name == 'get_folder_path':
            # 返回一个模拟的路径函数
            return lambda *args: os.path.join(os.getcwd(), 'mock_data')
        return lambda *args, **kwargs: None


class ConfigService:
    """配置服务类"""

    def __init__(self):
        self.mock_modules = ['core.utils.path_kit', 'core', 'core.utils']

    def serialize_complex_value(self, value):
        """
        递归序列化复杂数据结构，确保可以JSON序列化
        """
        try:
            if isinstance(value, (list, tuple)):
                return [self.serialize_complex_value(item) for item in value]
            elif isinstance(value, dict):
                return {k: self.serialize_complex_value(v) for k, v in value.items()}
            elif isinstance(value, range):
                return list(value)
            elif isinstance(value, Path):
                return str(value)
            elif hasattr(value, '__dict__'):
                return str(value)
            else:
                return value
        except Exception:
            return str(value)

    def parse_config_variables(self, config_file_path):
        """
        解析config.py文件中的变量
        使用动态导入的方式，参考用户提供的简洁方法
        """
        logger.info(f"开始解析配置文件: {config_file_path}")

        try:
            # 检查文件是否存在
            if not os.path.exists(config_file_path):
                logger.error(f"配置文件不存在: {config_file_path}")
                return {"error": "配置文件不存在"}

            # 动态导入config模块
            spec = importlib.util.spec_from_file_location("config", config_file_path)
            config_module = importlib.util.module_from_spec(spec)

            # 在sys.modules中注册模拟模块
            original_modules = {}

            for module_name in self.mock_modules:
                if module_name not in sys.modules:
                    original_modules[module_name] = None
                    sys.modules[module_name] = MockModule()

            try:
                # 执行模块
                spec.loader.exec_module(config_module)
            except SystemExit:
                # 捕获exit()调用，但继续解析已有变量
                logger.warning("配置模块包含exit()调用，已忽略")
                pass

            # 使用用户提供的方法提取自定义变量，但过滤掉导入的类、函数等
            config_dict = {}
            for key, value in vars(config_module).items():
                # 跳过私有变量和模块
                if key.startswith("__") or isinstance(value, ModuleType):
                    continue

                # 跳过导入的类和函数
                if (callable(value) or
                        inspect.isclass(value) or
                        inspect.isfunction(value) or
                        inspect.isbuiltin(value) or
                        inspect.ismethod(value)):
                    continue

                # 保留配置变量
                if key == 'strategy_list':
                    strategy_list = self.serialize_complex_value(value)
                    for stg in strategy_list:
                        if 'is_use_spot' in stg:
                            stg['market'] = 'spot_swap' if stg['is_use_spot'] else 'swap_swap'
                            del stg['is_use_spot']
                    config_dict[key] = strategy_list
                else:
                    config_dict[key] = self.serialize_complex_value(value)

                # 根据 cpu 核心数，筛选性能模式
                if key == 'job_num':
                    economy = min(int(os.cpu_count() / 3), 63)
                    equal = min(int(os.cpu_count() / 2), 63)
                    if value <= economy:
                        config_dict['performance_mode'] = 'ECONOMY'
                    elif value <= equal:
                        config_dict['performance_mode'] = 'EQUAL'
                    else:
                        config_dict['performance_mode'] = 'PERFORMANCE'

            # 清理模拟模块
            for module_name in self.mock_modules:
                if module_name in original_modules:
                    if original_modules[module_name] is None:
                        sys.modules.pop(module_name, None)
                    else:
                        sys.modules[module_name] = original_modules[module_name]

            logger.ok(f"配置解析完成，获取到 {len(config_dict)} 个配置变量")
            return config_dict

        except Exception as e:
            error_msg = f"配置文件解析失败: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return {"error": error_msg}

    def get_config_data(self, config_name: str = 'config'):
        """获取完整的配置数据，只返回业务数据，错误抛出异常。支持指定配置名"""
        logger.info(f"开始获取配置数据: {config_name}")

        config_path = self.get_config_file_path(config_name)
        config_data = self.parse_config_variables(config_path)

        if "error" in config_data:
            logger.error("配置数据获取失败")
            raise RuntimeError(config_data["error"])

        logger.ok("配置数据获取成功")
        return config_data

    @staticmethod
    def get_config_file_path(config_name: str = 'config'):
        """获取配置文件路径，支持指定配置名"""
        if is_debug or config_name != 'config':
            return get_file_path('data', f'{config_name}.py', as_path_type=False)
        return get_backtest_file_path(f'{config_name}.py', as_path_type=False)

    @staticmethod
    def generate_config_file_content(config: BacktestConfig) -> str:
        """生成配置文件的Python代码内容"""
        logger.info(f"生成配置文件内容: {config.name}")

        # 获取配置数据字典
        config_dict = config.to_dict()

        # 生成Python代码
        content_parts = []

        # 文件头注释
        content_parts.append('"""')
        content_parts.append('邢不行｜策略分享会')
        content_parts.append('选币策略框架𝓟𝓻𝓸')
        content_parts.append('')
        content_parts.append('版权所有 ©️ 邢不行')
        content_parts.append('微信: xbx1717')
        content_parts.append('')
        content_parts.append('本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。')
        content_parts.append('')
        content_parts.append('Author: 邢不行')
        content_parts.append('"""')
        content_parts.append('')

        # 导入语句
        content_parts.append('import os')
        content_parts.append('from pathlib import Path')
        content_parts.append('')
        content_parts.append('from core.utils.path_kit import get_folder_path')
        content_parts.append('')

        # 数据配置部分
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 数据配置 **')
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# 数据存储路径，填写绝对路径')
        content_parts.append('# 使用官方准备的预处理数据，专门用于本框架回测使用，大幅提高速度')
        content_parts.append(f"pre_data_path = r'{config_dict['pre_data_path']}'")
        content_parts.append('')

        # 额外数据配置
        content_parts.append('# ** 额外数据 **')
        content_parts.append('# 当且仅当用到额外数据的因子时候，该配置才需要配置，且自动生效')
        content_parts.append('data_source_dict = {')
        for key, value in config_dict['data_source_dict'].items():
            content_parts.append(f'    "{key}": {repr(value)},')
        content_parts.append('}')
        content_parts.append('')

        # 回测策略细节配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 回测策略细节配置 **')
        content_parts.append('# 需要配置需要的策略以及遍历的参数范围')
        content_parts.append('# ' + '=' * 100)
        content_parts.append(f"start_date = '{config_dict['start_date']}'  # 回测开始时间")
        content_parts.append(f"end_date = '{config_dict['end_date']}'  # 回测结束时间")
        content_parts.append('')

        # 策略配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 策略配置 **')
        content_parts.append('# 需要配置需要的策略以及遍历的参数范围')
        content_parts.append('# ' + '=' * 100)
        content_parts.append(f"backtest_name = '{config_dict['backtest_name']}'  # 回测的策略组合的名称")
        content_parts.append('"""策略配置"""')

        # 策略列表 - 使用StrategyConfig对象生成格式化的配置
        content_parts.append('strategy_list = [')
        for i, strategy_dict in enumerate(config_dict['strategy_list']):
            content_parts.append('    {')
            for key, value in strategy_dict.items():
                if key == 'offset_list' and isinstance(value, list):
                    # 处理range对象
                    if len(value) > 0 and value == list(range(value[0], value[-1] + 1, 1)):
                        content_parts.append(f'        "{key}": range({value[0]}, {value[-1] + 1}, 1),')
                    else:
                        content_parts.append(f'        "{key}": {value},')
                elif isinstance(value, str):
                    content_parts.append(f'        "{key}": \'{value}\',')
                elif key in ['is_use_spot'] and value is None:
                    # 对于可选字段，如果是None则注释掉
                    content_parts.append(f'        # "{key}": {repr(value)},')
                elif isinstance(value, list):
                    if len(value) > 0:
                        content_parts.append(f'        "{key}": {repr(value)},')
                else:
                        content_parts.append(f'        "{key}": {repr(value)},')
            # 在最后一个策略后面不加逗号
            if i < len(config_dict['strategy_list']) - 1:
                content_parts.append('    },')
            else:
                content_parts.append('    }')
        content_parts.append(']')
        content_parts.append('')

        # 其他策略参数
        if config_dict.get('re_timing'):
            content_parts.append(f"re_timing = {config_dict['re_timing']}")
        if config_dict.get('rebalance_mode'):
            content_parts.append(f"rebalance_mode = {config_dict['rebalance_mode']}")
        content_parts.append(f"min_kline_num = {config_dict['min_kline_num']}  # 最少上市多久")
        content_parts.append(f"black_list = {config_dict['black_list']}  # 拉黑名单")
        content_parts.append(f"white_list = {config_dict['white_list']}  # 白名单")
        content_parts.append('')

        # 回测模拟下单配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 回测模拟下单配置 **')
        content_parts.append('# ' + '=' * 100)
        content_parts.append(f"account_type = '{config_dict['account_type']}'  # '统一账户'或者'普通账户'")
        content_parts.append(f"initial_usdt = {config_dict['initial_usdt']:.0f}  # 初始资金")
        content_parts.append(f"leverage = {config_dict['leverage']}  # 杠杆数")
        content_parts.append(f"margin_rate = {config_dict['margin_rate']}  # 维持保证金率")
        content_parts.append('')
        content_parts.append(f"swap_c_rate = {config_dict['swap_c_rate']}  # 合约手续费(包含滑点)")
        content_parts.append(f"spot_c_rate = {config_dict['spot_c_rate']}  # 现货手续费(包含滑点)")
        content_parts.append('')
        content_parts.append(f"swap_min_order_limit = {config_dict['swap_min_order_limit']}  # 合约最小下单量")
        content_parts.append(f"spot_min_order_limit = {config_dict['spot_min_order_limit']}  # 现货最小下单量")
        content_parts.append('')
        content_parts.append(f"avg_price_col = '{config_dict['avg_price_col']}'  # 用于模拟计算的平均价")
        content_parts.append('')

        # 回测全局设置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 回测全局设置 **')
        content_parts.append('# 这些设置是客观事实，基本不会影响到回测的细节')
        content_parts.append('# ' + '=' * 100)
        if config_dict['job_num'] is None:
            content_parts.append("job_num = max(os.cpu_count() - 1, 1)  # 回测并行数量")
        else:
            content_parts.append(f"job_num = {config_dict['job_num']}  # 回测并行数量")
        content_parts.append('')
        content_parts.append(f"factor_col_limit = {config_dict['factor_col_limit']}  # 内存优化选项")
        content_parts.append('')

        # 全局变量及自动化
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 全局变量及自动化 **')
        content_parts.append('# 没事别动这边的东西 :)')
        content_parts.append('# ' + '=' * 100)
        content_parts.append('raw_data_path = Path(pre_data_path)')
        content_parts.append('# 现货数据路径')
        content_parts.append("spot_path = raw_data_path / 'spot_dict.pkl'")
        content_parts.append('# 合约数据路径')
        content_parts.append("swap_path = raw_data_path / 'swap_dict.pkl'")
        content_parts.append('')
        content_parts.append('# 回测结果数据路径。用于发帖脚本使用')
        content_parts.append("backtest_path = Path(get_folder_path('data', '回测结果'))")
        content_parts.append("backtest_iter_path = Path(get_folder_path('data', '遍历结果'))")
        content_parts.append('')

        # 稳定币信息
        content_parts.append('# 稳定币信息，不参与交易的币种')
        stable_coins = ['BKRW', 'USDC', 'USDP', 'TUSD', 'BUSD', 'FDUSD', 'DAI', 'EUR', 'GBP', 'USBP', 'SUSD', 'PAXG',
                        'AEUR', 'EURI']
        content_parts.append('stable_symbol = [')
        for i, coin in enumerate(stable_coins):
            if i == len(stable_coins) - 1:
                content_parts.append(f"    '{coin}'")
            else:
                content_parts.append(f"    '{coin}',")
        content_parts.append(']')
        content_parts.append('')

        # 检查和验证
        content_parts.append("if len(pre_data_path) == 0:")
        content_parts.append(
            "    print('⚠️ 请先准确配置预处理数据的位置（pre_data_path）。建议直接复制绝对路径，并且粘贴给 pre_data_path')")
        content_parts.append("    exit()")
        content_parts.append("")
        content_parts.append("if (not spot_path.exists()) or (not swap_path.exists()):")
        content_parts.append("    print(f'⚠️ 预处理数据不存在，请检查配置 `pre_data_path`: {pre_data_path}')")
        content_parts.append("    exit()")
        content_parts.append("")

        return '\n'.join(content_parts)

    def save_config_file(self, config: BacktestConfig) -> dict:
        """保存配置到Python文件，只返回业务数据，错误抛出异常"""
        logger.info(f"开始保存配置文件: {config.name}")

        # 验证配置数据
        errors = config.validate()
        if errors:
            logger.error(f"配置验证失败: {errors}")
            raise ValueError(f"配置数据验证失败: {errors}")

        # 生成文件内容
        content = self.generate_config_file_content(config)

        # 确定保存路径
        filename = f"{config.name}.py"
        if config.name == 'config' and not is_debug:
            file_path = get_backtest_file_path('config.py', as_path_type=False)
        else:
            file_path = get_file_path('data', filename, as_path_type=False)

        # 保存文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.ok(f"配置文件保存成功: {file_path}")

        return {
            "config_name": config.name,
            "file_path": file_path,
            "filename": filename
        }

    @staticmethod
    def get_config_list() -> list:
        """获取data目录下所有配置文件列表，只返回业务数据，错误抛出异常"""
        logger.info("获取配置文件列表")

        try:
            data_dir = get_folder_path('data')

            configs = []

            if data_dir.exists():
                for file in data_dir.iterdir():
                    if file.is_file() and file.suffix == ".py" and not file.name.startswith('_'):
                        configs.append(file.stem)
            logger.ok(f"找到 {len(configs)} 个配置文件")

            return configs

        except Exception as e:
            logger.error(f"获取配置列表失败: {e}")
            raise RuntimeError(f'获取配置列表失败: {str(e)}')

    @staticmethod
    def create_config_from_request(data: dict) -> BacktestConfig:
        """从请求数据创建配置对象"""
        logger.info("从请求数据创建配置对象")

        try:
            config = create_config_from_dict(data)
            logger.ok("配置对象创建成功")
            return config
        except Exception as e:
            logger.error(f"创建配置对象失败: {str(e)}")
            raise

    @staticmethod
    def process_symbol(symbol_list):
        results = []
        for symbol in symbol_list:
            if symbol.endswith('USDT') and '-' not in symbol:
                symbol = symbol.replace('USDT', '-USDT')
            results.append(symbol)
        return results

    def convert_real_trading_to_backtest_config(self, config_file_path: Path) -> str:
        """将实盘配置文件转换为回测配置文件"""
        logger.info(f"开始转换实盘配置文件: {config_file_path}")
        
        try:
            # 检查文件是否存在
            if not os.path.exists(config_file_path):
                logger.error(f"配置文件不存在: {config_file_path}")
                return None
            
            # 动态导入config模块
            spec = importlib.util.spec_from_file_location("config", config_file_path)
            config_module = importlib.util.module_from_spec(spec)
            
            # 在sys.modules中注册模拟模块
            original_modules = {}
            
            for module_name in self.mock_modules:
                if module_name not in sys.modules:
                    original_modules[module_name] = None
                    sys.modules[module_name] = MockModule()

            try:
                # 执行模块
                spec.loader.exec_module(config_module)
            except SystemExit:
                # 捕获exit()调用，但继续解析已有变量
                logger.warning("配置模块包含exit()调用，已忽略")
                pass
            except Exception as e:
                logger.warning(f"执行配置文件失败: {e}")
                # 即使有错误，也尝试继续解析
                pass
            
            # 提取account_config中的strategy_list
            account_config = getattr(config_module, 'account_config', None)
            if not account_config:
                logger.warning("未找到account_config")
                return None
            
            # 获取第一个账户的配置（实盘配置通常只有一个账户）
            account_name = list(account_config.keys())[0]
            account_data = account_config[account_name]
            
            if 'strategy_list' not in account_data:
                logger.warning("未找到strategy_list")
                return None
            
            # 转换为BacktestConfig格式
            strategy_configs = []
            for strategy_dict in account_data['strategy_list']:
                # 创建StrategyConfig对象，使用create_strategy_from_dict确保字段过滤
                strategy_config = create_strategy_from_dict(strategy_dict)
                strategy_configs.append(strategy_config)
            
            # 从原配置中提取其他有用信息
            data_source_dict = getattr(config_module, 'data_source_dict', {})
            
            # 准备配置数据字典
            config_data = {
                'name': f"{config_file_path.stem}",
                'data_source_dict': data_source_dict,
                'start_date': getattr(config_module, 'start_date', '2021-01-01'),
                'end_date': getattr(config_module, 'end_date', '2025-04-01 23:00:00'),
                'backtest_name': f'{config_file_path.stem}',
                'strategy_list': strategy_configs,
                'min_kline_num': account_data.get('min_kline_num', 168),
                'black_list': self.process_symbol(account_data.get('black_list', [])),
                'white_list': self.process_symbol(account_data.get('white_list', [])),
                'account_type': '普通账户',
                'initial_usdt': 10000,
                'leverage': account_data.get('leverage', 1),
                'margin_rate': getattr(config_module, 'margin_rate', 0.05),
                'swap_c_rate': getattr(config_module, 'swap_c_rate', 6 / 10000),
                'spot_c_rate': getattr(config_module, 'spot_c_rate', 1 / 1000),
                'swap_min_order_limit': getattr(config_module, 'swap_min_order_limit', 5),
                'spot_min_order_limit': getattr(config_module, 'spot_min_order_limit', 10),
                'avg_price_col': getattr(config_module, 'avg_price_col', 'avg_price_1m'),
                'job_num': getattr(config_module, 'job_num', max(os.cpu_count() - 1, 1)),
                'factor_col_limit': getattr(config_module, 'factor_col_limit', 64),
            }
            
            # 使用create_config_from_dict来创建BacktestConfig对象
            backtest_config = create_config_from_dict(config_data)
            
            # 保存配置文件
            result = self.save_config_file(backtest_config)
            
            # 清理模拟模块
            for module_name in self.mock_modules:
                if module_name in original_modules:
                    if original_modules[module_name] is None:
                        sys.modules.pop(module_name, None)
                    else:
                        sys.modules[module_name] = original_modules[module_name]

            logger.info(f"配置文件转换成功: {result['filename']}")
            return result['filename']
            
        except Exception as e:
            logger.error(f"转换配置文件失败: {e}")
            logger.error(traceback.format_exc())
            return None

    def execute_backtest_script(self, python_exec: str, py_file: Path):
        try:
            logger.info(f"开始执行脚本: {py_file}")

            # 构建命令
            cmd = [python_exec, str(py_file)]

            # 创建进程 - 在Windows上不使用universal_newlines=True，手动处理编码
            is_windows = platform.system().lower() == 'windows'
            
            if is_windows:
                # Windows系统：使用二进制模式，手动处理编码
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1
                )

                # 启动输出读取线程
                output_queue = queue.Queue()
                output_thread = threading.Thread(target=self.read_output_windows, args=(process.stdout, output_queue))
                output_thread.daemon = True
                output_thread.start()
            else:
                # 非Windows系统：使用文本模式
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1
                )
                
                # 启动输出读取线程
                output_queue = queue.Queue()
                output_thread = threading.Thread(target=self.read_output, args=(process.stdout, output_queue))
                output_thread.daemon = True
                output_thread.start()

            # 监控输出并记录日志
            while True:
                try:
                    # 检查进程是否还在运行
                    if process.poll() is not None:
                        break

                    # 读取输出
                    try:
                        line = output_queue.get_nowait()
                        print(line)
                    except queue.Empty:
                        pass

                    # 短暂休眠避免CPU占用过高
                    time.sleep(0.03)

                except Exception as e:
                    logger.error(f"监控回测输出失败: {e}")
                    break

            # 等待进程结束
            return_code = process.wait()

            if return_code == 0:
                logger.ok("回测执行完成")
            else:
                logger.error(f"回测执行失败，返回码: {return_code}")
                raise Exception('回测执行失败')

        except Exception as e:
            logger.error(f"执行脚本失败: {e}")
            logger.error(traceback.format_exc())
            raise e

    @staticmethod
    def read_output(pipe, _queue):
        # 实时读取输出
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    _queue.put(line.strip())
            pipe.close()
        except Exception as e:
            logger.error(f"读取输出失败: {e}")

    @staticmethod
    def read_output_windows(pipe, _queue):
        """Windows系统专用的输出读取方法，处理编码问题"""
        try:
            # 尝试多种编码方式
            encodings = ['utf-8', 'gbk', 'gb2312', 'cp936', 'latin-1']
            
            for line in iter(pipe.readline, b''):
                if line:
                    # 尝试不同的编码
                    decoded_line = None
                    for encoding in encodings:
                        try:
                            decoded_line = line.decode(encoding, errors='replace')
                            break
                        except UnicodeDecodeError:
                            continue
                    
                    if decoded_line:
                        _queue.put(decoded_line.strip())
                    else:
                        # 如果所有编码都失败，使用replace模式
                        decoded_line = line.decode('utf-8', errors='replace')
                        _queue.put(decoded_line.strip())
            
            pipe.close()
        except Exception as e:
            logger.error(f"Windows读取输出失败: {e}")
            logger.error(traceback.format_exc())

# 创建全局配置服务实例
config_service = ConfigService()

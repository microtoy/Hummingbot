"""
2024分享会
author: 邢不行
微信: xbx6660
选币策略框架
"""
import importlib

import pandas as pd


class DummyFactor:
    """
    ！！！！抽象因子对象，仅用于代码提示！！！！
    """
    # 额外数据
    extra_data_dict: dict = {}

    # 因子模式：时序因子/截面因子（factors/sections）
    # False： 时序因子
    # True：  截面因子
    is_cross: bool = False

    FA_INTRO: dict = {}

    def signal(self, *args) -> pd.DataFrame:
        raise NotImplementedError

    def signal_multi_params(self, df, param_list: list | set | tuple) -> dict:
        raise NotImplementedError

    def get_factor_list(self, n):
        raise NotImplementedError


class FactorHub:
    _factor_cache = {}

    # noinspection PyTypeChecker
    @staticmethod
    def get_by_name(factor_name, is_debug: bool = False) -> DummyFactor:
        if factor_name in FactorHub._factor_cache:
            return FactorHub._factor_cache[factor_name]

        try:
            prefix = 'data.' if is_debug else ''
            try:
                module = 'factors'
                factor_module = importlib.import_module(f"{prefix}{module}.{factor_name}")
            except ModuleNotFoundError:
                module = 'sections'
                factor_module = importlib.import_module(f"{prefix}{module}.{factor_name}")

            # 创建一个包含模块变量和函数的字典
            factor_content = {
                name: getattr(factor_module, name) for name in dir(factor_module)
                if not name.startswith("__")
            }
            factor_content['is_cross'] = module == 'sections'

            if "FA_INTRO" not in factor_content:
                factor_content["FA_INTRO"] = {}

            # 创建一个包含这些变量和函数的对象
            factor_instance = type(factor_name, (), factor_content)

            # 缓存策略对象
            FactorHub._factor_cache[factor_name] = factor_instance

            return factor_instance
        except ModuleNotFoundError:
            raise ValueError(f"Factor {factor_name} not found.")
        except AttributeError:
            raise ValueError(f"Error accessing factor content in module {factor_name}.")


# 使用示例
if __name__ == "__main__":
    factor = FactorHub.get_by_name("PctChange")

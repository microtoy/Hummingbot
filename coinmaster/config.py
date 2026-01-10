# -*- coding: utf-8 -*-
"""
回测网页版 | 邢不行 | 2025分享会
author: 邢不行
微信: xbx6660
"""
import os


"""
数据相关配置
"""

# 葫芦 id
uuid = 'd945ec495f7034efbe805ef46e95a00b'

# apikey
api_key = 'GRMRSXKO0674VN4M2EMYKHC179C2HRQ5'

# 存放 B 圈回测数据的目录位置（不要与 `股票客户端` 路径相同，会污染数据）
fuel_data_path = './data'

# 代理信息(境外更新数据网络不好时，可以尝试配置一下代理)
proxies = {}

# 多进程数量(只影响回测数据更新的速度)
njobs = max(int(os.cpu_count() - 1), 2)


"""
配置检查，未配置不运行执行
"""
if not (uuid and api_key and fuel_data_path):
    print('❌ 检查 config 中的 uuid，api_key，fuel_data_path，配置完成后再次尝试启动')
    exit(1)

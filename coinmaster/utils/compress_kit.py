# -*- coding: utf-8 -*-
"""
回测网页版 | 邢不行 | 2025分享会
author: 邢不行
微信: xbx6660
"""
import os
import shutil
import tarfile
import zipfile

import rarfile
from py7zr import py7zr
from retrying import retry


def _fix_zip_filename(zip_info):
    """
    修复 ZIP 文件中的中文文件名编码问题
    :param zip_info: ZipInfo 对象
    :return: 修正后的文件名
    """
    # 检查 flag_bits 第 11 位，判断是否为 UTF-8 编码
    if zip_info.flag_bits & 0x800:
        # 文件名是 UTF-8 编码，直接返回
        return zip_info.filename
    else:
        # 文件名不是 UTF-8，尝试用 GBK 解码
        try:
            # 将错误解码的字符串还原为原始字节，再用 GBK 解码
            return zip_info.filename.encode('cp437').decode('gbk')
        except (UnicodeDecodeError, UnicodeEncodeError):
            # 解码失败，返回原始文件名
            return zip_info.filename


@retry(stop_max_attempt_number=5)
def zip_uncompress(path, save_path):
    """
    解压zip，支持修复中文文件名编码问题
    :param path: ZIP 文件路径
    :param save_path: 解压目标目录
    :return:
    """
    with zipfile.ZipFile(path, 'r') as zip_ref:
        for zip_info in zip_ref.infolist():
            # 获取修正后的文件名
            fixed_filename = _fix_zip_filename(zip_info)
            # 构建目标路径
            target_path = os.path.join(save_path, fixed_filename)

            if zip_info.is_dir():
                # 处理目录条目
                os.makedirs(target_path, exist_ok=True)
            else:
                # 处理文件条目
                # 确保父目录存在
                parent_dir = os.path.dirname(target_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                # 读取文件内容并写入目标路径
                with zip_ref.open(zip_info) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)


@retry(stop_max_attempt_number=5)
def tar_uncompress(path, save_path):
    """
    解压tar格式
    :param path:
    :param save_path:
    :return:
    """
    f = tarfile.open(path)
    f.extractall(save_path)
    f.close()


@retry(stop_max_attempt_number=5)
def rar_uncompress(path, save_path):
    """
    :param path:
    :param save_path:
    :return:
    """
    # rar
    f = rarfile.RarFile(path)  # 待解压文件
    f.extractall(save_path)  # 解压指定文件路径
    f.close()
    pass


@retry(stop_max_attempt_number=5)
def uncompress(path, save_path):
    """
    解压7z
    :param path:
    :param save_path:
    :return:
    """
    # 7z
    f = py7zr.SevenZipFile(path, 'r')
    f.extractall(path=save_path)
    f.close()
    pass

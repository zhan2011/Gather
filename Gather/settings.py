#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局路径与 config.json 读写。

config.json 只放"站点级"设置（端口、标题、首次生成的管理员密码）。
报名/问卷/投票的具体内容都存在数据库里，由管理端页面维护。
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "signup.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")

DEFAULT_CONFIG = {
    "port": 8000,
    "title": "报名 / 问卷 / 投票",
    "subtitle": "点开下面的活动，填写并提交",
    # 仅在第一次启动、还没有任何管理员账号时，用这个密码创建 admin 账号
    "admin_password": "admin123",
}

CFG = dict(DEFAULT_CONFIG)


def load():
    """读取 config.json；文件不存在时生成一份默认配置。"""
    CFG.clear()
    CFG.update(json.loads(json.dumps(DEFAULT_CONFIG)))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    CFG[k] = v
        except Exception as exc:  # 配置坏了也要能启动
            print(f"[警告] 读取 config.json 失败，已使用默认配置：{exc}")
    else:
        try:
            save()
            print(f"[提示] 已生成配置文件：{CONFIG_PATH}")
        except OSError as exc:
            print(f"[警告] 无法写入 config.json：{exc}")
    return CFG


def save():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)

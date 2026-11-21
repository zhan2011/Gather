#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据层：活动（报名 / 问卷 / 投票）、提交记录、管理员账号。

设计要点
--------
* 三种活动共用一张 activities 表，差异放在 config(JSON) 里：
  - signup: {"sports": [{key,name,icon,capacity}], "allow_multiple": bool}
  - survey / poll: {"questions": [{id,label,type,options,required}], "show_results": bool}
* 提交记录集中在 responses 表，data 为 JSON；signup 额外写 uniq 列
  （"项目|学号"）用于数据库层面的去重（部分唯一索引）。
* 截止日期 deadline 为空表示不限；比较用 'YYYY-MM-DD HH:MM' 文本。
* 每个 IP 的填报次数限制在"同一事务 + 全局写锁"内校验，管理员提交不计次。
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, timedelta

import settings

SPORTS = [
    {"key": "morning", "name": "上午场", "icon": ""},
    {"key": "afternoon", "name": "下午场", "icon": ""},
    {"key": "evening", "name": "晚上场", "icon": ""},
]
KIND_NAMES = {"signup": "报名", "survey": "问卷", "poll": "投票"}
KINDS = ("signup", "survey", "poll")

DB_LOCK = threading.RLock()


# ------------------------------------------------------------------ 基础工具
def db():
    conn = sqlite3.connect(settings.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return datetime.now().strftime("%Y-%m-%d")


def clean_text(raw, limit=200):
    """去控制字符、收拢空白。

    用 NFC 而不是 NFKC：中文的「？」「！」「，」这类全角标点要原样保留，
    NFKC 会把它们压成半角，题干和选项看着就不像中文了。
    """
    if raw is None:
        return ""
    s = unicodedata.normalize("NFC", str(raw))
    s = "".join(ch for ch in s if ch >= " " or ch in "\n")
    s = " ".join(s.split()) if "\n" not in s else "\n".join(
        " ".join(line.split()) for line in s.split("\n")
    )
    return s.strip()[:limit]


def clean_key(raw, limit=32):
    if raw is None:
        return ""
    s = "".join(ch for ch in str(raw) if ch.isalnum() or ch in "-_")
    return s[:limit]


def normalize_sid(raw):
    """学号：全角转半角、去空格、字母统一大写。"""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    return "".join(s.split()).upper()


def normalize_deadline(raw):
    """把 datetime-local / 手输日期统一成 'YYYY-MM-DD HH:MM'，空串表示不限。"""
    s = clean_text(raw, 40)
    if not s:
        return ""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.replace("/", "-"), fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return ""


def _to_int(value, default=0, low=0, high=1000000):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def hash_password(password, salt=None):
    salt = salt or uuid.uuid4().hex
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return dk.hex(), salt


# ------------------------------------------------------------------ 初始化
def init_db():
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    with closing(db()) as conn:
        with conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    is_super      INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activities (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind        TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    config      TEXT NOT NULL DEFAULT '{}',
                    deadline    TEXT,
                    max_per_ip  INTEGER NOT NULL DEFAULT 0,
                    closed      INTEGER NOT NULL DEFAULT 0,
                    created_by  TEXT,
                    created_at  TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS responses (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_id INTEGER NOT NULL,
                    data        TEXT NOT NULL,
                    uniq        TEXT,
                    ip          TEXT,
                    is_admin    INTEGER NOT NULL DEFAULT 0,
                    admin_name  TEXT,
                    created_at  TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_resp_activity ON responses(activity_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_resp_uniq "
                "ON responses(activity_id, uniq) WHERE uniq IS NOT NULL"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    nickname      TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    last_login_at TEXT,
                    register_ip   TEXT,
                    created_by_admin INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notices (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT NOT NULL,
                    content    TEXT NOT NULL DEFAULT '',
                    pinned     INTEGER NOT NULL DEFAULT 0,
                    hidden     INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bans (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    target     TEXT NOT NULL UNIQUE,
                    kind       TEXT NOT NULL DEFAULT '',
                    reason     TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            # 老库补列（SQLite 不支持 IF NOT EXISTS 的 ADD COLUMN）
            _ensure_column(conn, "activities", "allow_guest", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(conn, "responses", "user_id", "INTEGER")
            _ensure_column(conn, "responses", "user_name", "TEXT")
            _ensure_column(conn, "users", "register_ip", "TEXT")
            _ensure_column(conn, "users", "created_by_admin", "INTEGER NOT NULL DEFAULT 0")
        _migrate_legacy()
        _ensure_default_admin()


def _ensure_column(conn, table, column, ddl):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _has_legacy_table(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='signups'"
    ).fetchone()
    return bool(row)


def _migrate_legacy():
    """把旧版 signups 表里的报名记录迁移成一条"报名"活动。"""
    with closing(db()) as conn:
        done = conn.execute("SELECT v FROM meta WHERE k = 'legacy_migrated'").fetchone()
        if done:
            return
        migrated = 0
        if _has_legacy_table(conn):
            total = conn.execute("SELECT COUNT(*) AS c FROM signups").fetchone()["c"]
            if total:
                cfg = {
                    "sports": [
                        {"key": s["key"], "name": s["name"], "icon": s["icon"], "capacity": 0}
                        for s in SPORTS
                    ],
                    "allow_multiple": False,
                }
                created = now_text()
                with conn:
                    cur = conn.execute(
                        "INSERT INTO activities (kind, title, description, config, deadline,"
                        " max_per_ip, closed, created_by, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            "signup",
                            clean_text(settings.CFG.get("title"), 80) or "班级活动报名",
                            clean_text(settings.CFG.get("subtitle"), 200) or "旧版报名数据（自动迁移）",
                            json.dumps(cfg, ensure_ascii=False),
                            None,
                            0,
                            0,
                            "系统迁移",
                            created,
                        ),
                    )
                    aid = cur.lastrowid
                    rows = conn.execute(
                        "SELECT student_id, name, sport, created_at, ip FROM signups ORDER BY id"
                    ).fetchall()
                    for r in rows:
                        data = {
                            "sport": r["sport"],
                            "student_id": r["student_id"],
                            "name": r["name"],
                        }
                        conn.execute(
                            "INSERT OR IGNORE INTO responses"
                            " (activity_id, data, uniq, ip, is_admin, admin_name, created_at)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (
                                aid,
                                json.dumps(data, ensure_ascii=False),
                                f"{r['sport']}|{r['student_id']}",
                                r["ip"] or "",
                                0,
                                "",
                                r["created_at"] or created,
                            ),
                        )
                        migrated += 1
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES ('legacy_migrated', ?)",
                (now_text(),),
            )
        if migrated:
            print(f"[提示] 已把旧版 {migrated} 条报名记录迁移到新的活动数据中")


def _ensure_default_admin():
    with closing(db()) as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"]
        if total:
            return
        pwd = str(settings.CFG.get("admin_password") or "admin123")
        h, salt = hash_password(pwd)
        with conn:
            conn.execute(
                "INSERT INTO admins (username, password_hash, salt, is_super, created_at)"
                " VALUES (?,?,?,?,?)",
                ("admin", h, salt, 1, now_text()),
            )
        print(f"[提示] 已创建默认管理员：admin / {pwd}（登录后请尽快修改密码）")


# ------------------------------------------------------------------ 管理员
def admin_public(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "isSuper": bool(row["is_super"]),
        "createdAt": row["created_at"],
    }


def list_admins():
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM admins ORDER BY id").fetchall()
    return [admin_public(r) for r in rows]


def find_admin(aid):
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM admins WHERE id = ?", (aid,)).fetchone()
    return admin_public(row) if row else None


def verify_admin(username, password):
    """用户名 + 密码校验，成功返回管理员信息。"""
    username = clean_text(username, 40)
    password = str(password or "")
    if not username or not password:
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
    if not row:
        # 同理：不存在的管理员账号也空跑一次哈希
        hash_password(password, _DUMMY_SALT)
        return None
    candidate, _ = hash_password(password, row["salt"])
    if not hmac_compare(candidate, row["password_hash"]):
        return None
    return admin_public(row)


def hmac_compare(a, b):
    import hmac

    try:
        return hmac.compare_digest(a, b)
    except TypeError:
        return False


def create_admin(username, password, is_super=False):
    username = clean_text(username, 40)
    password = str(password or "")
    if not (2 <= len(username) <= 20):
        raise ValueError("用户名需 2-20 个字符")
    if not all(ch.isalnum() or ch in "_-" or "\u4e00" <= ch <= "\u9fff" for ch in username):
        raise ValueError("用户名只能用字母、数字、下划线、连字符或中文")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    if find_user_row(username):
        raise ValueError("这个用户名已被普通成员占用，请换一个")
    h, salt = hash_password(password)
    with DB_LOCK:
        with closing(db()) as conn:
            try:
                with conn:
                    cur = conn.execute(
                        "INSERT INTO admins (username, password_hash, salt, is_super, created_at)"
                        " VALUES (?,?,?,?,?)",
                        (username, h, salt, 1 if is_super else 0, now_text()),
                    )
            except sqlite3.IntegrityError:
                raise ValueError("该用户名已存在")
    return find_admin(cur.lastrowid)


def set_admin_password(aid, password):
    password = str(password or "")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    h, salt = hash_password(password)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE admins SET password_hash = ?, salt = ? WHERE id = ?", (h, salt, aid)
                )
    return cur.rowcount > 0


def delete_admin(aid):
    with DB_LOCK:
        with closing(db()) as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"]
            if total <= 1:
                raise ValueError("至少要保留一个管理员账号")
            row = conn.execute("SELECT is_super FROM admins WHERE id = ?", (aid,)).fetchone()
            if not row:
                return False
            if row["is_super"]:
                supers = conn.execute(
                    "SELECT COUNT(*) AS c FROM admins WHERE is_super = 1"
                ).fetchone()["c"]
                if supers <= 1:
                    raise ValueError("不能删除唯一的高级管理员")
            with conn:
                conn.execute("DELETE FROM admins WHERE id = ?", (aid,))
    return True


# ------------------------------------------------------------------ 普通用户
def user_public(row):
    if not row:
        return None
    keys = row.keys()
    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"] or row["username"],
        "createdAt": row["created_at"],
        "lastLoginAt": row["last_login_at"] or "",
        "registerIp": (row["register_ip"] or "") if "register_ip" in keys else "",
        "createdByAdmin": bool(row["created_by_admin"]) if "created_by_admin" in keys else False,
    }


def find_user(uid):
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return user_public(row)


def find_user_row(username):
    username = clean_text(username, 40)
    if not username:
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return row


def find_admin_row(username):
    """查管理员表里有没有这个名字（用于避免普通账号和管理员账号重名混淆）。"""
    username = clean_text(username, 40)
    if not username:
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
    return row


def verify_user(username, password):
    row = find_user_row(username)
    if not row:
        # 空跑一次 pbkdf2：让「账号不存在」和「密码错误」耗时一致
        hash_password(password, _DUMMY_SALT)
        return None
    calc = hash_password(password, row["salt"])
    if not hmac_compare(calc[0], row["password_hash"]):
        return None
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                conn.execute(
                    "UPDATE users SET last_login_at = ? WHERE id = ?",
                    (now_text(), row["id"]),
                )
    return user_public(row)


USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{2,20}$")

# 账号被占用（已注册 / 是管理员账号）统一这句话，避免注册接口泄漏哪些名字存在
USERNAME_TAKEN = "这个用户名不能用，换一个试试"

# 用户不存在时也跑一遍同样轮数的哈希，让登录耗时不泄漏账号是否存在
_DUMMY_SALT = "00000000000000000000000000000000"

# 同一个网络（IP）每 7 天最多自助注册几个账号
REGISTER_PER_IP_WEEK = 1


def count_ip_registrations(ip, days=7):
    """这个 IP 最近 N 天自助注册了几个账号（管理员代建的、没记 IP 的都不算）。"""
    if not ip:
        return 0
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM users"
            " WHERE register_ip = ? AND created_at >= ? AND created_by_admin = 0",
            (ip, since),
        ).fetchone()
    return row["c"] if row else 0


def _clean_nickname(value, username=""):
    name = clean_text(value, 40)
    if not name:
        return username
    if not (2 <= len(name) <= 20):
        raise ValueError("名字请填 2-20 个字")
    return name


def create_user(username, password, nickname="", ip="", by_admin=False):
    username = clean_text(username, 40)
    if not USERNAME_RE.match(username):
        raise ValueError("用户名请用 2-20 位字母、数字、下划线、连字符或中文")
    if not by_admin and ip and is_banned(ip):
        raise ValueError("这台设备已被管理员限制，不能注册新账号")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if len(password) > 64:
        raise ValueError("密码最多 64 位")
    if find_admin_row(username) or find_user_row(username):
        raise ValueError(USERNAME_TAKEN)
    nickname = _clean_nickname(nickname, username)
    ip = (ip or "")[:64]
    if not by_admin and ip and count_ip_registrations(ip) >= REGISTER_PER_IP_WEEK:
        where = "设备" if ip.startswith("dev-") else "网络"
        raise ValueError(
            f"同一个{where}每周只能注册 1 个账号，这个{where}本周已经注册过了。"
            "需要更多账号，请联系管理员在后台代建"
        )
    h, salt = hash_password(password)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                try:
                    cur = conn.execute(
                        "INSERT INTO users (username, password_hash, salt, nickname,"
                        " created_at, register_ip, created_by_admin)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (
                            username,
                            h,
                            salt,
                            nickname,
                            now_text(),
                            "" if by_admin else ip,
                            1 if by_admin else 0,
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise ValueError(USERNAME_TAKEN)
    return find_user(cur.lastrowid)


def set_user_nickname(uid, nickname):
    user = find_user(uid)
    if not user:
        raise ValueError("账号不存在")
    name = _clean_nickname(nickname, "")
    if not name:
        raise ValueError("名字请填 2-20 个字")
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (name, uid))
    return find_user(uid)


def change_user_password(uid, old_password, new_password):
    user = find_user(uid)
    if not user:
        raise ValueError("账号不存在")
    row = find_user_row(user["username"])
    calc = hash_password(old_password, row["salt"]) if row else None
    if not row or not hmac_compare(calc[0], row["password_hash"]):
        raise ValueError("原来的密码不对")
    if len(new_password or "") < 6:
        raise ValueError("新密码至少 6 位")
    if len(new_password) > 64:
        raise ValueError("新密码最多 64 位")
    set_user_password(uid, new_password)
    return True


def set_user_password(uid, password):
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    h, salt = hash_password(password)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                    (h, salt, uid),
                )
    return cur.rowcount > 0


def delete_user(uid):
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    return cur.rowcount > 0


def count_users():
    with closing(db()) as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def list_users(keyword=""):
    """列出普通账号（按注册时间倒序），带各自的提交次数。"""
    sql = "SELECT * FROM users"
    args = []
    kw = (keyword or "").strip()
    if kw:
        sql += " WHERE username LIKE ? OR nickname LIKE ?"
        like = "%" + kw + "%"
        args = [like, like]
    sql += " ORDER BY id ASC"
    with closing(db()) as conn:
        rows = conn.execute(sql, args).fetchall()
        counts = dict(
            conn.execute(
                "SELECT user_id, COUNT(*) AS c FROM responses"
                " WHERE user_id IS NOT NULL GROUP BY user_id"
            ).fetchall()
        )
    out = []
    for r in rows:
        item = user_public(r)
        item["count"] = counts.get(r["id"], 0)
        out.append(item)
    return out


def user_usage(activity_id, user_id):
    if not user_id:
        return 0
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM responses"
            " WHERE activity_id = ? AND user_id = ? AND is_admin = 0",
            (activity_id, user_id),
        ).fetchone()
    return row["c"]


def user_usage_map(user_id):
    """一次查出该账号在各活动下的提交次数。"""
    if not user_id:
        return {}
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT activity_id, COUNT(*) AS c FROM responses"
            " WHERE user_id = ? AND is_admin = 0 GROUP BY activity_id",
            (user_id,),
        ).fetchall()
    return {r["activity_id"]: r["c"] for r in rows}


# ------------------------------------------------------------------ 活动配置
def _clean_questions(raw):
    out = []
    seen = set()
    for i, q in enumerate(raw or []):
        if not isinstance(q, dict):
            continue
        label = clean_text(q.get("label"), 200)
        if not label:
            continue
        qtype = str(q.get("type") or "single").lower()
        if qtype not in ("single", "multi", "text"):
            qtype = "single"
        options = []
        if qtype != "text":
            for opt in q.get("options") or []:
                text = clean_text(opt, 100)
                if text and text not in options:
                    options.append(text)
            if len(options) < 2:
                continue  # 选项不足的题直接丢掉
            options = options[:30]
        qid = clean_key(q.get("id")) or f"q{i + 1}"
        while qid in seen:
            qid = f"{qid}_x"
        seen.add(qid)
        # 多选题可以限制最多选几项；0 表示不限，不能超过选项数
        max_pick = 0
        if qtype == "multi":
            max_pick = _to_int(q.get("max"), 0, 0, 30)
            if max_pick > len(options):
                max_pick = len(options)
        out.append(
            {
                "id": qid,
                "label": label,
                "type": qtype,
                "options": options,
                "required": bool(q.get("required", True)),
                # "允许填写其他"：选项之外还能自己写一段话（问卷星里的「其他____」）
                "other": bool(q.get("other")) and qtype in ("single", "multi"),
                "max": max_pick,
            }
        )
        if len(out) >= 50:
            break
    return out


def clean_config(kind, cfg):
    """校验并归一化活动配置，非法时抛 ValueError。"""
    if not isinstance(cfg, dict):
        cfg = {}
    if kind == "signup":
        sports = []
        used_keys = set()
        raw = cfg.get("sports")
        if isinstance(raw, list):
            for i, s in enumerate(raw):
                if not isinstance(s, dict):
                    continue
                name = clean_text(s.get("name"), 20)
                if not name:
                    continue
                key = clean_key(s.get("key")) or f"sport{i + 1}"
                while key in used_keys:
                    key = f"{key}x"
                used_keys.add(key)
                sports.append(
                    {
                        "key": key,
                        "name": name,
                        "icon": clean_text(s.get("icon"), 8),
                        "capacity": _to_int(s.get("capacity"), 0, 0, 100000),
                    }
                )
                if len(sports) >= 20:
                    break
        if not sports:
            sports = [
                {"key": s["key"], "name": s["name"], "icon": s["icon"], "capacity": 0}
                for s in SPORTS
            ]
        return {"sports": sports, "allow_multiple": bool(cfg.get("allow_multiple"))}
    if kind in ("survey", "poll"):
        questions = _clean_questions(cfg.get("questions"))
        if not questions:
            raise ValueError("请至少添加一道题目，且选择题至少要有 2 个选项")
        return {
            "questions": questions,
            "show_results": bool(cfg.get("show_results", kind == "poll")),
        }
    raise ValueError("未知的活动类型")


def _activity_from_row(row, count=0):
    try:
        cfg = json.loads(row["config"] or "{}")
    except ValueError:
        cfg = {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "description": row["description"] or "",
        "config": cfg,
        "deadline": row["deadline"] or "",
        "maxPerIp": row["max_per_ip"] or 0,
        "allowGuest": bool(row["allow_guest"]) if "allow_guest" in row.keys() else True,
        "closed": bool(row["closed"]),
        "createdBy": row["created_by"] or "",
        "createdAt": row["created_at"],
        "count": count,
    }


def status_of(activity):
    if activity["closed"]:
        return "closed"
    deadline = activity["deadline"]
    if deadline and now_text() >= deadline:
        return "ended"
    return "open"


STATUS_TEXT = {"open": "进行中", "closed": "已结束", "ended": "已截止"}


def list_activities(kind=None):
    sql = (
        "SELECT a.*, (SELECT COUNT(*) FROM responses r WHERE r.activity_id = a.id) AS c "
        "FROM activities a"
    )
    args = []
    if kind in KINDS:
        sql += " WHERE a.kind = ?"
        args.append(kind)
    sql += " ORDER BY a.id DESC"
    with closing(db()) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_activity_from_row(r, r["c"]) for r in rows]


def get_activity(aid):
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM responses r WHERE r.activity_id = a.id) AS c "
            "FROM activities a WHERE a.id = ?",
            (aid,),
        ).fetchone()
    return _activity_from_row(row, row["c"]) if row else None


def create_activity(kind, title, description="", config=None, deadline="", max_per_ip=0,
                    created_by="", allow_guest=True):
    if kind not in KINDS:
        raise ValueError("未知的活动类型")
    title = clean_text(title, 80)
    if not title:
        raise ValueError("请填写活动标题")
    cfg = clean_config(kind, config)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT INTO activities (kind, title, description, config, deadline,"
                    " max_per_ip, allow_guest, closed, created_by, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        kind,
                        title,
                        clean_text(description, 1000),
                        json.dumps(cfg, ensure_ascii=False),
                        normalize_deadline(deadline) or None,
                        _to_int(max_per_ip, 0, 0, 10000),
                        1 if allow_guest else 0,
                        0,
                        clean_text(created_by, 40),
                        now_text(),
                    ),
                )
    return get_activity(cur.lastrowid)


def update_activity(aid, title=None, description=None, deadline=None, max_per_ip=None,
                    closed=None, sports=None, allow_guest=None):
    """只改元信息和名额；题目/选项/项目本身不支持修改（避免与已有提交对不上）。"""
    activity = get_activity(aid)
    if not activity:
        raise ValueError("活动不存在")
    fields, args = [], []
    if title is not None:
        title = clean_text(title, 80)
        if not title:
            raise ValueError("请填写活动标题")
        fields.append("title = ?")
        args.append(title)
    if description is not None:
        fields.append("description = ?")
        args.append(clean_text(description, 1000))
    if deadline is not None:
        fields.append("deadline = ?")
        args.append(normalize_deadline(deadline) or None)
    if max_per_ip is not None:
        fields.append("max_per_ip = ?")
        args.append(_to_int(max_per_ip, 0, 0, 10000))
    if closed is not None:
        fields.append("closed = ?")
        args.append(1 if closed else 0)
    if allow_guest is not None:
        fields.append("allow_guest = ?")
        args.append(1 if allow_guest else 0)
    if sports is not None:
        if activity["kind"] != "signup":
            raise ValueError("只有报名活动才有名额设置")
        cfg = dict(activity["config"])
        used = {}
        for row in list_responses(aid):
            key = (row["data"] or {}).get("sport")
            if key:
                used[key] = used.get(key, 0) + 1
        wanted = {}
        for item in sports or []:
            if not isinstance(item, dict):
                continue
            key = clean_key(item.get("key"))
            if key:
                wanted[key] = _to_int(item.get("capacity"), 0, 0, 100000)
        merged = []
        for sport in cfg.get("sports") or []:
            cap = wanted.get(sport["key"], sport.get("capacity") or 0)
            joined = used.get(sport["key"], 0)
            if cap and cap < joined:
                raise ValueError(
                    f"{sport['name']}名额不能小于已报人数（已报 {joined} 人）"
                )
            merged.append({**sport, "capacity": cap})
        cfg["sports"] = merged
        fields.append("config = ?")
        args.append(json.dumps(cfg, ensure_ascii=False))
    if not fields:
        return activity
    args.append(aid)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                conn.execute(f"UPDATE activities SET {', '.join(fields)} WHERE id = ?", args)
    return get_activity(aid)


def delete_activity(aid):
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                conn.execute("DELETE FROM responses WHERE activity_id = ?", (aid,))
                cur = conn.execute("DELETE FROM activities WHERE id = ?", (aid,))
    return cur.rowcount > 0


# ------------------------------------------------------------------ 公告
def notice_public(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"] or "",
        "pinned": bool(row["pinned"]),
        "hidden": bool(row["hidden"]),
        "createdBy": row["created_by"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"] or "",
    }


def list_notices(include_hidden=True, limit=0):
    """公告列表：置顶的在前，其余按最新在前。"""
    sql = "SELECT * FROM notices"
    if not include_hidden:
        sql += " WHERE hidden = 0"
    sql += " ORDER BY pinned DESC, id DESC"
    if limit:
        sql += " LIMIT %d" % int(limit)
    with closing(db()) as conn:
        rows = conn.execute(sql).fetchall()
    return [notice_public(r) for r in rows]


def get_notice(nid):
    try:
        nid = int(nid)
    except (TypeError, ValueError):
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM notices WHERE id = ?", (nid,)).fetchone()
    return notice_public(row)


def create_notice(title, content="", pinned=False, created_by=""):
    title = clean_text(title, 100)
    if not title:
        raise ValueError("请填写公告标题")
    content = clean_text(content, 5000)
    if not content:
        raise ValueError("请填写公告内容")
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT INTO notices (title, content, pinned, hidden, created_by, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (title, content, 1 if pinned else 0, 0, created_by or "", now_text()),
                )
    return get_notice(cur.lastrowid)


def update_notice(nid, title=None, content=None, pinned=None, hidden=None):
    fields = []
    args = []
    if title is not None:
        title = clean_text(title, 100)
        if not title:
            raise ValueError("请填写公告标题")
        fields.append("title = ?")
        args.append(title)
    if content is not None:
        content = clean_text(content, 5000)
        if not content:
            raise ValueError("请填写公告内容")
        fields.append("content = ?")
        args.append(content)
    if pinned is not None:
        fields.append("pinned = ?")
        args.append(1 if pinned else 0)
    if hidden is not None:
        fields.append("hidden = ?")
        args.append(1 if hidden else 0)
    if not fields:
        raise ValueError("没有要修改的内容")
    fields.append("updated_at = ?")
    args.append(now_text())
    args.append(nid)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE notices SET " + ", ".join(fields) + " WHERE id = ?", args
                )
    if not cur.rowcount:
        raise ValueError("这条公告不存在")
    return get_notice(nid)


def delete_notice(nid):
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute("DELETE FROM notices WHERE id = ?", (nid,))
    return cur.rowcount > 0


# ------------------------------------------------------------------ 来源封禁
BAN_TEXT = "这台设备已被管理员限制，无法提交。如有疑问请联系管理员"


def _ban_from_row(row):
    return {
        "id": row["id"],
        "target": row["target"],
        "kind": row["kind"] or ("device" if row["target"].startswith("dev-") else "ip"),
        "reason": row["reason"] or "",
        "createdBy": row["created_by"] or "",
        "createdAt": row["created_at"] or "",
    }


def list_bans():
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM bans ORDER BY id DESC").fetchall()
    return [_ban_from_row(r) for r in rows]


def find_ban(target):
    target = (target or "").strip()
    if not target:
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM bans WHERE target = ?", (target,)).fetchone()
    return _ban_from_row(row) if row else None


def is_banned(target):
    return find_ban(target) is not None


def ban_targets():
    """所有被封禁的来源标识，给明细页标出哪些行已经拉黑。"""
    with closing(db()) as conn:
        return {r["target"] for r in conn.execute("SELECT target FROM bans").fetchall()}


def add_ban(target, reason="", created_by=""):
    target = (target or "").strip()[:64]
    if not target:
        raise ValueError("要封禁的标识不能为空")
    if target.startswith("用户:"):
        raise ValueError("现在只支持封禁 IP 或设备标识")
    kind = "device" if target.startswith("dev-") else "ip"
    reason = clean_text(reason, 200)
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO bans (target, kind, reason, created_by, created_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(target) DO UPDATE SET"
                    " kind = excluded.kind, reason = excluded.reason,"
                    " created_by = excluded.created_by",
                    (target, kind, reason, created_by or "", now_text()),
                )
                row = conn.execute(
                    "SELECT * FROM bans WHERE target = ?", (target,)
                ).fetchone()
    return _ban_from_row(row)


def delete_ban(bid):
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute("DELETE FROM bans WHERE id = ?", (bid,))
    return cur.rowcount > 0


# ------------------------------------------------------------------ 提交记录
def ip_usage(activity_id, ip):
    if not ip:
        return 0
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM responses"
            " WHERE activity_id = ? AND ip = ? AND is_admin = 0",
            (activity_id, ip),
        ).fetchone()
    return row["c"]


def usage_map(ip):
    """一次查出该 IP 在各活动下（非管理员）的提交次数。"""
    if not ip:
        return {}
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT activity_id, COUNT(*) AS c FROM responses"
            " WHERE ip = ? AND is_admin = 0 GROUP BY activity_id",
            (ip,),
        ).fetchall()
    return {r["activity_id"]: r["c"] for r in rows}


def _parse_response(row):
    try:
        data = json.loads(row["data"] or "{}")
    except ValueError:
        data = {}
    return {
        "id": row["id"],
        "activityId": row["activity_id"],
        "data": data,
        "ip": row["ip"] or "",
        "isAdmin": bool(row["is_admin"]),
        "adminName": row["admin_name"] or "",
        "userId": row["user_id"] or 0,
        "userName": row["user_name"] or "",
        "createdAt": row["created_at"],
    }


def list_responses(aid):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT * FROM responses WHERE activity_id = ? ORDER BY id ASC", (aid,)
        ).fetchall()
    return [_parse_response(r) for r in rows]


def submit_response(activity, payload, ip="", admin=None, user=None):
    """校验并写入一条提交。返回 (ok, message, record)。"""
    kind = activity["kind"]
    status = status_of(activity)
    if status != "open":
        return False, f"该活动{STATUS_TEXT[status]}，不能再提交", None

    is_admin = bool(admin)
    admin_name = admin["username"] if admin else ""
    uid = user["id"] if user else 0
    uname = (user["nickname"] or user["username"]) if user else ""
    cfg = activity["config"]

    if not is_admin and ip and is_banned(ip):
        return False, BAN_TEXT, None

    if not is_admin and not uid and not activity.get("allowGuest", True):
        return False, "本活动仅限已登录的账号填写，请先登录", None

    if kind == "signup":
        ok, msg, data, uniq = _validate_signup(cfg, payload)
        if not ok:
            return False, msg, None
    else:
        ok, msg, data = _validate_answers(cfg, payload)
        if not ok:
            return False, msg, None
        uniq = None

    created = now_text()
    aid = activity["id"]
    max_ip = activity["maxPerIp"] or 0

    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                if not is_admin and max_ip:
                    if uid:
                        used = conn.execute(
                            "SELECT COUNT(*) AS c FROM responses"
                            " WHERE activity_id = ? AND user_id = ? AND is_admin = 0",
                            (aid, uid),
                        ).fetchone()["c"]
                        if used >= max_ip:
                            return False, (
                                f"该账号最多可提交 {max_ip} 次，已用完。"
                                "如需再次提交请联系管理员"
                            ), None
                    else:
                        used = conn.execute(
                            "SELECT COUNT(*) AS c FROM responses"
                            " WHERE activity_id = ? AND ip = ? AND is_admin = 0",
                            (aid, ip),
                        ).fetchone()["c"]
                        if used >= max_ip:
                            where = (
                                "这台设备"
                                if ip.startswith("dev-")
                                else "该网络（IP）"
                            )
                            return False, (
                                f"{where}最多可提交 {max_ip} 次，已用完。"
                                "如需再次提交请联系管理员"
                            ), None
                if kind == "signup":
                    if not cfg.get("allow_multiple"):
                        exist = conn.execute(
                            "SELECT data FROM responses WHERE activity_id = ?", (aid,)
                        ).fetchall()
                        for r in exist:
                            try:
                                d = json.loads(r["data"] or "{}")
                            except ValueError:
                                continue
                            if normalize_sid(d.get("student_id")) == data["student_id"]:
                                sport = _sport_name(cfg, d.get("sport"))
                                return False, (
                                    f"该学号已报名 {sport}，每人限报一项。"
                                    "如需更改请联系管理员"
                                ), None
                    sport = _find_sport(cfg, data["sport"])
                    cap = sport.get("capacity") or 0
                    if cap:
                        cur = conn.execute(
                            "SELECT COUNT(*) AS c FROM responses WHERE activity_id = ?", (aid,)
                        ).fetchone()["c"]
                        if cur >= cap:
                            return False, f"{sport['name']}名额已满（{cap} 人）", None
                try:
                    cur = conn.execute(
                        "INSERT INTO responses"
                        " (activity_id, data, uniq, ip, is_admin, admin_name,"
                        " user_id, user_name, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            aid,
                            json.dumps(data, ensure_ascii=False),
                            uniq,
                            ip,
                            1 if is_admin else 0,
                            admin_name,
                            uid or None,
                            uname or None,
                            created,
                        ),
                    )
                except sqlite3.IntegrityError:
                    return False, "该信息已提交过，不能重复提交", None

    record = {
        "id": cur.lastrowid,
        "kind": kind,
        "data": data,
        "createdAt": created,
        "isAdmin": is_admin,
        "userName": uname,
    }
    return True, "提交成功", record


def _find_sport(cfg, key):
    for s in cfg.get("sports") or []:
        if s["key"] == key:
            return s
    return {"key": key, "name": key, "capacity": 0}


def _sport_name(cfg, key):
    return _find_sport(cfg, key)["name"]


def _validate_signup(cfg, payload):
    raw = str(payload.get("sport") or "").strip()
    # 允许传项目 key 或项目名，统一转成 key 存库
    key = ""
    for sport in cfg.get("sports") or []:
        if raw and (sport["key"] == raw or sport["name"] == raw):
            key = sport["key"]
            break
    if not key:
        return False, "请选择报名项目", None, None
    sid = normalize_sid(payload.get("student_id") or payload.get("sid"))
    name = clean_text(payload.get("name"), 20)
    if not (2 <= len(sid) <= 20):
        return False, "学号格式不正确（2-20 位字母或数字）", None, None
    if not all(ch.isalnum() or ch in "-_" for ch in sid):
        return False, "学号只能包含字母、数字、- 或 _", None, None
    if not (2 <= len(name) <= 4):
        return False, "姓名请填 2-4 个字", None, None
    return True, "", {"sport": key, "student_id": sid, "name": name}, f"{key}|{sid}"


def _validate_answers(cfg, payload):
    raw = payload.get("answers")
    if not isinstance(raw, dict):
        raw = {}
    answers = {}
    for q in cfg.get("questions") or []:
        value = raw.get(q["id"])
        if q["type"] == "text":
            text = clean_text(value, 500)
            if q["required"] and not text:
                return False, f"请填写「{q['label']}」", None
            if text:
                answers[q["id"]] = text
        elif q["type"] == "single":
            picked = clean_text(value, 100)
            if picked and picked not in q["options"] and not q.get("other"):
                return False, f"「{q['label']}」的选项无效", None
            if q["required"] and not picked:
                return False, f"请选择「{q['label']}」", None
            if picked:
                answers[q["id"]] = picked
        else:  # multi
            values = value if isinstance(value, list) else ([value] if value else [])
            picked = []
            custom = ""
            for v in values:
                v = clean_text(v, 100)
                if not v or v in picked:
                    continue
                if v in q["options"]:
                    picked.append(v)
                elif q.get("other") and not custom:
                    custom = v  # 选项外最多允许一段自填内容
            if custom:
                picked.append(custom)
            limit = q.get("max") or 0
            if limit and len(picked) > limit:
                return False, f"「{q['label']}」最多选 {limit} 项", None
            if q["required"] and not picked:
                return False, f"请选择「{q['label']}」", None
            if picked:
                answers[q["id"]] = picked
    if not answers:
        return False, "请先作答再提交", None
    return True, "", {"answers": answers}


def delete_response(rid):
    with DB_LOCK:
        with closing(db()) as conn:
            with conn:
                cur = conn.execute("DELETE FROM responses WHERE id = ?", (rid,))
    return cur.rowcount > 0


# ------------------------------------------------------------------ 结果统计
def compute_results(activity, rows=None):
    if rows is None:
        rows = list_responses(activity["id"])
    cfg = activity["config"]
    total = len(rows)
    result = {"total": total, "kind": activity["kind"]}

    if activity["kind"] == "signup":
        counts = {s["key"]: 0 for s in cfg.get("sports") or []}
        for r in rows:
            key = r["data"].get("sport")
            if key in counts:
                counts[key] += 1
        result["sports"] = [
            {
                "key": s["key"],
                "name": s["name"],
                "icon": s.get("icon") or "",
                "count": counts.get(s["key"], 0),
                "capacity": s.get("capacity") or 0,
            }
            for s in cfg.get("sports") or []
        ]
        return result

    answered = {}
    questions = []
    for q in cfg.get("questions") or []:
        if q["type"] == "text":
            texts = []
            for r in rows:
                v = (r["data"].get("answers") or {}).get(q["id"])
                if isinstance(v, str) and v:
                    texts.append({"text": v, "createdAt": r["createdAt"]})
            questions.append(
                {
                    "id": q["id"],
                    "label": q["label"],
                    "type": "text",
                    "total": len(texts),
                    "texts": texts[:300],
                }
            )
            continue
        counts = {opt: 0 for opt in q["options"]}
        extras = {}
        answered_count = 0
        for r in rows:
            v = (r["data"].get("answers") or {}).get(q["id"])
            picked = v if isinstance(v, list) else ([v] if v else [])
            picked = [p for p in picked if p]
            if picked:
                answered_count += 1
            for p in picked:
                if p in counts:
                    counts[p] += 1
                else:
                    extras[p] = extras.get(p, 0) + 1  # 选项外自填的内容
        options = []
        for opt in q["options"]:
            c = counts[opt]
            percent = round(c * 100.0 / answered_count) if answered_count else 0
            options.append({"text": opt, "count": c, "percent": percent})
        if extras:
            other_total = sum(extras.values())
            subs = [
                {"text": t, "count": c}
                for t, c in sorted(extras.items(), key=lambda kv: (-kv[1], kv[0]))[:100]
            ]
            percent = round(other_total * 100.0 / answered_count) if answered_count else 0
            options.append(
                {"text": "其他", "count": other_total, "percent": percent, "subs": subs}
            )
        questions.append(
            {
                "id": q["id"],
                "label": q["label"],
                "type": q["type"],
                "total": answered_count,
                "options": options,
            }
        )
        answered[q["id"]] = answered_count
    result["questions"] = questions
    return result


def _who_text(r):
    """导出的「提交账号」列：管理员 / 注册账号 / 空（访客）。"""
    if r["isAdmin"]:
        return "管理员" + (f" {r['adminName']}" if r["adminName"] else "")
    if r["userName"]:
        return r["userName"]
    return ""


def source_text(ip):
    """把来源标识翻成人话，原始值保留，方便管理员核对是谁。

    真实 IP 原样显示；dev-* 是拿不到 IP 时的设备 cookie，加个「设备」前缀区分。
    """
    ip = (ip or "").strip()
    if not ip:
        return ""
    if ip.startswith("dev-"):
        return "设备 " + ip
    return ip


def export_table(activity, rows=None):
    """返回 (表头, 数据行)。"""
    if rows is None:
        rows = list_responses(activity["id"])
    cfg = activity["config"]
    if activity["kind"] == "signup":
        header = ["序号", "项目", "学号", "姓名", "提交时间", "IP", "提交账号"]
        table = []
        for i, r in enumerate(rows, 1):
            d = r["data"]
            table.append(
                [
                    i,
                    _sport_name(cfg, d.get("sport")),
                    d.get("student_id") or "",
                    d.get("name") or "",
                    r["createdAt"],
                    source_text(r["ip"]),
                    _who_text(r),
                ]
            )
        return header, table

    questions = cfg.get("questions") or []
    header = ["序号", "提交时间", "IP", "提交账号"] + [q["label"] for q in questions]
    table = []
    for i, r in enumerate(rows, 1):
        answers = r["data"].get("answers") or {}
        line = [i, r["createdAt"], source_text(r["ip"]), _who_text(r)]
        for q in questions:
            v = answers.get(q["id"])
            if isinstance(v, list):
                line.append("、".join(v))
            else:
                line.append(v or "")
        table.append(line)
    return header, table

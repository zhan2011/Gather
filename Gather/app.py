#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报名 / 问卷 / 投票 一体化小站（零第三方依赖，Python 3.8+）

客户端
  1. 首页列出进行中的报名、问卷、投票，手机端自适应；
  2. 点开活动填写并提交，提交后立刻看到统计结果（投票）。

管理端（/admin）
  1. 多管理员账号登录，登录后既能发起活动也能亲自参与（参与不占 IP 次数）；
  2. 报名 / 问卷 / 投票都可设截止日期、每 IP 填报次数、手动结束；
  3. 查看明细、统计结果、导出 Excel / CSV。

启动：python app.py [--port 8000] [--no-browser]
"""

import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import exporters
import settings
import store

settings.load()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}

SESSION_TTL = 12 * 3600
SESSIONS = {}
SESS_LOCK = threading.Lock()
USER_SESSIONS = {}
USER_COOKIE = "user_token"

# 真实 IP 拿不到时（TCP 内网穿透）用来区分访客的浏览器标识
DEVICE_COOKIE = "device_id"
DEVICE_RE = re.compile(r"^dev-[0-9a-f]{16}$")

# ---------------- 登录限流
# 三层闸：
#   1) 同一个来源（IP / 设备标识）在窗口期内失败太多次 -> 锁这个来源
#   2) 同一个账号名失败太多次 -> 锁这个账号（换 cookie 换 IP 也绕不过去）
#   3) 全站失败速率过高 -> 先整体降速，再整体冷却（挡住「每次都换 cookie 重来」的脚本）
LOGIN_WINDOW = 15 * 60          # 计数窗口：15 分钟
LOGIN_MAX_FAILS = 10            # 窗口内允许的失败次数
LOGIN_BLOCK_SECONDS = 15 * 60   # 触顶后锁多久
LOGIN_FAILS = {}                # key -> {"first": ts, "count": n, "until": ts}
LOGIN_LOCK = threading.Lock()

GLOBAL_WINDOW = 60              # 全站失败速率窗口：1 分钟
GLOBAL_SOFT = 30                # 一分钟内失败超过这么多 -> 登录失败统一多等 2 秒
GLOBAL_HARD = 120               # 一分钟内失败超过这么多 -> 全站登录冷却
GLOBAL_HARD_SECONDS = 120
GLOBAL_FAILS = deque()          # 最近窗口内的失败时间戳
GLOBAL_UNTIL = [0.0]            # 全局冷却截止时间
GLOBAL_LOCK = threading.Lock()


LOGIN_BAD_TEXT = "账号或密码错误"


def login_retry_after(key):
    """这个来源还要等多少秒才能再试登录；0 表示没被限制。"""
    now = time.time()
    with LOGIN_LOCK:
        rec = LOGIN_FAILS.get(key)
        if not rec:
            return 0
        if rec["until"] and rec["until"] > now:
            return int(rec["until"] - now) + 1
        if rec["until"] or now - rec["first"] > LOGIN_WINDOW:
            LOGIN_FAILS.pop(key, None)
        return 0


def login_failed(key):
    now = time.time()
    with LOGIN_LOCK:
        rec = LOGIN_FAILS.get(key)
        if not rec or now - rec["first"] > LOGIN_WINDOW:
            rec = {"first": now, "count": 0, "until": 0}
            LOGIN_FAILS[key] = rec
        rec["count"] += 1
        if rec["count"] >= LOGIN_MAX_FAILS:
            rec["until"] = now + LOGIN_BLOCK_SECONDS
        return rec["until"] > now


def login_succeeded(key):
    with LOGIN_LOCK:
        LOGIN_FAILS.pop(key, None)


def global_gate():
    """返回 (还要等多少秒才能整体登录, 是否该降速)。"""
    now = time.time()
    with GLOBAL_LOCK:
        while GLOBAL_FAILS and now - GLOBAL_FAILS[0] > GLOBAL_WINDOW:
            GLOBAL_FAILS.popleft()
        soft = len(GLOBAL_FAILS) >= GLOBAL_SOFT
        hard = int(GLOBAL_UNTIL[0] - now) + 1 if GLOBAL_UNTIL[0] > now else 0
    return hard, soft


def global_failed():
    now = time.time()
    with GLOBAL_LOCK:
        GLOBAL_FAILS.append(now)
        while GLOBAL_FAILS and now - GLOBAL_FAILS[0] > GLOBAL_WINDOW:
            GLOBAL_FAILS.popleft()
        if len(GLOBAL_FAILS) >= GLOBAL_HARD:
            GLOBAL_UNTIL[0] = now + GLOBAL_HARD_SECONDS


def new_session(admin):
    token = uuid.uuid4().hex
    with SESS_LOCK:
        SESSIONS[token] = {
            "id": admin["id"],
            "username": admin["username"],
            "exp": time.time() + SESSION_TTL,
        }
    return token


def drop_session(token):
    with SESS_LOCK:
        SESSIONS.pop(token or "", None)


def session_admin(token):
    if not token:
        return None
    now = time.time()
    with SESS_LOCK:
        item = SESSIONS.get(token)
        if not item:
            return None
        if item["exp"] < now:
            SESSIONS.pop(token, None)
            return None
        aid = item["id"]
    admin = store.find_admin(aid)  # 每次回库校验，账号被删立刻失效
    if not admin:
        drop_session(token)
        return None
    return admin


def new_user_session(user):
    token = uuid.uuid4().hex
    with SESS_LOCK:
        USER_SESSIONS[token] = {
            "id": user["id"],
            "username": user["username"],
            "exp": time.time() + SESSION_TTL,
        }
    return token


def drop_user_session(token):
    with SESS_LOCK:
        USER_SESSIONS.pop(token or "", None)


def session_user(token):
    if not token:
        return None
    now = time.time()
    with SESS_LOCK:
        item = USER_SESSIONS.get(token)
        if not item:
            return None
        if item["exp"] < now:
            USER_SESSIONS.pop(token, None)
            return None
        uid = item["id"]
    user = store.find_user(uid)  # 每次回库校验，账号被删立刻失效
    if not user:
        drop_user_session(token)
        return None
    return user


def _is_local_peer(peer):
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _is_loopback(peer):
    """回环地址 = 请求一定是从本机转发进来的，分辨不出真实访客。"""
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


# PROXY protocol v2 的 12 字节固定签名
PROXY_V2_SIG = b"\r\n\r\n\x00\r\nQUIT\n"


def activity_card(activity, used, is_admin, need_login=False, by_account=False, by_device=False):
    """首页/列表用的活动摘要。used 由调用方按身份（账号 / IP / 设备）算好。"""
    status = store.status_of(activity)
    max_ip = activity["maxPerIp"] or 0
    allow_guest = activity.get("allowGuest", True)
    need_login = bool(need_login) and status == "open"
    if is_admin:
        remaining = None
        can_submit = status == "open"
    elif need_login:
        remaining = None
        can_submit = False
    elif max_ip:
        remaining = max(0, max_ip - used)
        can_submit = status == "open" and remaining > 0
    else:
        remaining = None
        can_submit = status == "open"
    if max_ip:
        who = "账号" if by_account else ("设备" if by_device else "网络")
        limit_text = f"每{who}最多 {max_ip} 次"
    else:
        limit_text = ""
    cfg = activity["config"]
    if activity["kind"] == "signup":
        total = activity["count"]
        hint = f"已报 {total} 人"
    else:
        total = activity["count"]
        hint = f"{len(cfg.get('questions') or [])} 题 · 已提交 {total} 份"
    return {
        "id": activity["id"],
        "kind": activity["kind"],
        "kindName": store.KIND_NAMES.get(activity["kind"], activity["kind"]),
        "title": activity["title"],
        "description": activity["description"],
        "deadline": activity["deadline"],
        "maxPerIp": max_ip,
        "allowGuest": allow_guest,
        "needLogin": need_login,
        "limitText": limit_text,
        "status": status,
        "statusText": store.STATUS_TEXT[status],
        "count": total,
        "hint": hint,
        "used": used,
        "remaining": remaining,
        "canSubmit": can_submit,
        "createdAt": activity["createdAt"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "SportsSignup/2.0"
    protocol_version = "HTTP/1.1"

    _tag_cache = None
    _pending_device = None
    _proxy_ip = None
    _proxy_parsed = False
    _proxy_note = ""

    def handle_one_request(self):
        # 每条连接最前面可能有一行 PROXY 协议头（内网穿透开启后才会有），
        # 必须在 HTTP 解析之前吃掉，否则会被当成非法请求行。
        if not self._proxy_parsed:
            self._proxy_parsed = True
            try:
                self._parse_proxy_header()
            except Exception:
                self._proxy_ip = None
        return super().handle_one_request()

    # ---------------- 工具
    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    def _json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._emit_device_cookie()
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, status=200, headers=None):
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"ok": False, "error": "文件不存在"}, 404)
        self.send_response(status)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self._emit_device_cookie()
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, relpath, headers=None):
        rel = relpath.lstrip("/")
        full = os.path.normpath(os.path.join(settings.STATIC_DIR, rel))
        if not (full == settings.STATIC_DIR or full.startswith(settings.STATIC_DIR + os.sep)):
            return self._json({"ok": False, "error": "非法路径"}, 403)
        if not os.path.isfile(full):
            return self._json({"ok": False, "error": "页面不存在"}, 404)
        return self._file(full, headers=headers or [("Cache-Control", "no-cache")])

    def _download(self, body, ctype, filename):
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        safe = re.sub(r"[\\/:*?\"<>|]", "_", filename)[:60] or "导出"
        disp = (
            f'attachment; filename="export_{stamp}"; '
            f"filename*=UTF-8''{quote(safe + '_' + stamp)}"
        )
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", disp)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 2000000:
            return {}
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").lower()
        text = raw.decode("utf-8", "replace")
        if "application/json" in ctype:
            try:
                data = json.loads(text or "{}")
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}
        form = parse_qs(text, keep_blank_values=True)
        return {k: v[0] for k, v in form.items()}

    def _cookie(self, name):
        from http.cookies import SimpleCookie

        raw = self.headers.get("Cookie") or ""
        try:
            jar = SimpleCookie()
            jar.load(raw)
            item = jar.get(name)
            return item.value if item else ""
        except Exception:
            return ""

    # ---------------- PROXY protocol（真实 IP）
    def _parse_proxy_header(self):
        """内网穿透若开了 PROXY protocol，连接开头会先说真实来源。

        v1 是一行 ASCII：``PROXY TCP4 1.2.3.4 5.6.7.8 12345 443``
        v2 是 12 字节签名 + 二进制地址块。
        只在连接来自本机/内网时才解析——否则公网直连的客户端可以自己伪造
        这一行，冒充任何 IP 去绕过限次和封禁。
        """
        peer = self.client_address[0] if self.client_address else ""
        if not _is_local_peer(peer):
            self._proxy_note = "peer 不是内网，按安全策略不看 PROXY 头（peer=%s）" % peer
            return None
        try:
            head = self.connection.recv(12, socket.MSG_PEEK)
        except OSError:
            return None
        if len(head) < 6:
            return None
        if head.startswith(b"PROXY "):
            ip = self._read_proxy_v1()
            self._proxy_note = "v1 解析结果：%s" % ip
        elif head[:12] == PROXY_V2_SIG:
            ip = self._read_proxy_v2()
            self._proxy_note = "v2 解析结果：%s" % ip
        else:
            self._proxy_note = "连接开头没有 PROXY 头，前 12 字节：%r" % head
            return None
        if ip:
            self._proxy_ip = ip
            try:  # 让日志/调试里的来源也变成真实地址
                self.client_address = (ip, self.client_address[1])
            except Exception:
                pass
        return ip

    def _read_proxy_v1(self):
        buf = b""
        for _ in range(20):
            try:
                chunk = self.connection.recv(108, socket.MSG_PEEK)
            except OSError:
                return None
            if not chunk:
                return None
            idx = chunk.find(b"\r\n")
            if idx >= 0:
                buf = chunk[:idx]
                self.connection.recv(idx + 2)  # 只吃掉这一行，后面是正常 HTTP
                break
            if len(chunk) >= 108:
                return None
            buf = chunk
            time.sleep(0.01)
        parts = buf.split()
        if len(parts) < 6 or parts[1] not in (b"TCP4", b"TCP6"):
            return None  # PROXY UNKNOWN 之类，没有可用地址
        try:
            return parts[2].decode("ascii")[:64]
        except UnicodeDecodeError:
            return None

    def _read_proxy_v2(self):
        hdr = b""
        while len(hdr) < 16:
            chunk = self.connection.recv(16 - len(hdr))
            if not chunk:
                return None
            hdr += chunk
        if hdr[:12] != PROXY_V2_SIG:
            return None
        cmd = hdr[12] & 0x0F
        if cmd != 0x01:  # 0x00 = LOCAL（探活连接），没有地址
            return None
        fam = hdr[13] >> 4  # 1 = IPv4, 2 = IPv6
        alen = int.from_bytes(hdr[14:16], "big")
        body = b""
        while len(body) < alen:
            chunk = self.connection.recv(alen - len(body))
            if not chunk:
                return None
            body += chunk
        if fam == 1 and len(body) >= 12:
            return socket.inet_ntoa(body[0:4])
        if fam == 2 and len(body) >= 36:
            return socket.inet_ntop(socket.AF_INET6, body[0:16])
        return None

    def _client_ip(self):
        """返回用于计次的来源标识。

        正常情况下是真实 IP：只在请求来自本机/内网（含内网穿透隧道）时才采信
        X-Forwarded-For，防止公网直接伪造。但如果隧道是纯 TCP 转发，穿透服务器
        不会带任何来源头，所有访客在服务端看来都是 127.0.0.1——这时按 IP 限次会
        变成"全站只能提交一次"，所以退回用浏览器 cookie 里的设备标识区分。
        """
        if self._tag_cache is not None:
            return self._tag_cache
        peer = self.client_address[0] if self.client_address else ""
        ip = self._proxy_ip or peer
        if not self._proxy_ip and _is_local_peer(peer):
            fwd = self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP") or ""
            fwd = fwd.split(",")[0].strip()
            if fwd:
                ip = fwd
        if _is_loopback(ip):
            ip = self._device_id()
        self._tag_cache = ip[:64]
        return self._tag_cache

    def _device_id(self):
        """浏览器级别的设备标识，仅在真实 IP 不可用时兜底。"""
        val = self._cookie(DEVICE_COOKIE)
        if val and DEVICE_RE.match(val):
            return val
        val = "dev-" + uuid.uuid4().hex[:16]
        self._pending_device = val
        return val

    def _emit_device_cookie(self):
        if getattr(self, "_pending_device", None):
            self.send_header(
                "Set-Cookie",
                "%s=%s; Path=/; Max-Age=31536000; SameSite=Lax"
                % (DEVICE_COOKIE, self._pending_device),
            )
            self._pending_device = None

    def _admin(self):
        return session_admin(self._cookie("admin_token"))

    def _user(self):
        return session_user(self._cookie(USER_COOKIE))

    def _device_identity(self, by_account):
        """不是按账号计次、且来源标识是设备 cookie 时，文案要说「设备」而不是「网络」。"""
        return (not by_account) and self._client_ip().startswith("dev-")

    def _identity(self, activity):
        """按身份算出该活动下的已提交次数。

        管理员不计次；已登录账号按账号计次；访客按 IP 计次。
        返回 (admin, user, used, by_account)。
        """
        admin = self._admin()
        user = self._user()
        if admin:
            return admin, user, 0, False
        if user:
            return None, user, store.user_usage(activity["id"], user["id"]), True
        ip = self._client_ip()
        return None, None, (store.ip_usage(activity["id"], ip) if ip else 0), False

    def _require_admin(self):
        admin = self._admin()
        if not admin:
            self._json({"ok": False, "error": "未登录"}, 401)
            return None
        return admin

    def _int(self, value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ---------------- GET
    def do_GET(self):
        self._tag_cache = None
        self._pending_device = None
        parsed = urlparse(self.path)
        path = parsed.path
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path in ("/admin", "/admin/"):
            return self._static("admin.html")
        if re.match(r"^/a/\d+/?$", path) or path == "/activity.html":
            return self._static("activity.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/favicon.ico":
            return self._file(
                os.path.join(settings.STATIC_DIR, "favicon.svg"),
                headers=[("Content-Type", "image/svg+xml"), ("Cache-Control", "max-age=86400")],
            )
        if path == "/healthz":
            return self._json({"ok": True})

        if path == "/api/home":
            return self._api_home()
        if path == "/api/activity":
            return self._api_activity(qs)
        if path == "/api/user/me":
            return self._json({"ok": True, "user": self._user()})

        if path.startswith("/api/admin/"):
            admin = self._require_admin()
            if not admin:
                return
            if path == "/api/admin/me":
                return self._json(
                    {
                        "ok": True,
                        "admin": admin,
                        "clientTag": self._client_ip(),
                        "net": {
                            "peer": (self.client_address or ("", 0))[0],
                            "proxyIp": self._proxy_ip or "",
                            "proxyNote": self._proxy_note,
                            "xForwardedFor": self.headers.get("X-Forwarded-For") or "",
                            "xRealIp": self.headers.get("X-Real-IP") or "",
                        },
                    }
                )
            if path == "/api/admin/activities":
                return self._api_admin_activities()
            if path == "/api/admin/activity":
                return self._api_admin_activity(qs)
            if path == "/api/admin/export":
                return self._api_admin_export(qs)
            if path == "/api/admin/admins":
                return self._json(
                    {
                        "ok": True,
                        "admins": store.list_admins(),
                        "me": admin,
                    }
                )
            if path == "/api/admin/users":
                return self._json(
                    {"ok": True, "users": store.list_users(qs.get("q") or "")}
                )
            if path == "/api/admin/notices":
                return self._json({"ok": True, "notices": store.list_notices()})
            if path == "/api/admin/bans":
                return self._json({"ok": True, "bans": store.list_bans()})

        return self._json({"ok": False, "error": "页面不存在"}, 404)

    # ---------------- 公开接口
    def _api_home(self):
        ip = self._client_ip()
        admin = self._admin()
        user = self._user()
        if admin:
            usage = {}
        elif user:
            usage = store.user_usage_map(user["id"])
        else:
            usage = store.usage_map(ip) if ip else {}
        by_account = bool(user and not admin)
        activities = store.list_activities()
        cards = []
        for a in activities:
            need_login = not admin and not user and not a.get("allowGuest", True)
            cards.append(
                activity_card(
                    a,
                    usage.get(a["id"], 0),
                    bool(admin),
                    need_login=need_login,
                    by_account=by_account,
                    by_device=self._device_identity(by_account),
                )
            )
        counts = {"all": len(cards)}
        for kind in store.KINDS:
            counts[kind] = sum(1 for c in cards if c["kind"] == kind)
        return self._json(
            {
                "ok": True,
                "site": {
                    "title": settings.CFG.get("title") or "报名 / 问卷 / 投票",
                    "subtitle": settings.CFG.get("subtitle") or "",
                },
                "activities": cards,
                "counts": counts,
                "notices": store.list_notices(include_hidden=False, limit=30),
                "admin": {
                    "loggedIn": bool(admin),
                    "username": admin["username"] if admin else "",
                    "isSuper": bool(admin and admin["isSuper"]),
                },
                "user": user,
            }
        )

    def _api_activity(self, qs):
        aid = self._int(qs.get("id"))
        activity = store.get_activity(aid)
        if not activity:
            return self._json({"ok": False, "error": "活动不存在"}, 404)
        admin, user, used, by_account = self._identity(activity)
        need_login = not admin and not user and not activity.get("allowGuest", True)
        card = activity_card(
            activity,
            used,
            bool(admin),
            need_login=need_login,
            by_account=by_account,
            by_device=self._device_identity(by_account),
        )
        results = None
        visible = activity["kind"] == "signup" or bool(
            activity["config"].get("show_results")
        )
        if store.status_of(activity) != "open":
            visible = True
        if visible:
            results = store.compute_results(activity)
        return self._json(
            {
                "ok": True,
                "activity": {
                    "id": activity["id"],
                    "kind": activity["kind"],
                    "kindName": store.KIND_NAMES.get(activity["kind"], ""),
                    "title": activity["title"],
                    "description": activity["description"],
                    "deadline": activity["deadline"],
                    "config": activity["config"],
                    "allowGuest": activity.get("allowGuest", True),
                },
                "card": card,
                "used": used,
                "isAdmin": bool(admin),
                "adminName": admin["username"] if admin else "",
                "user": user,
                "results": results,
                "resultsVisible": visible,
            }
        )

    # ---------------- 管理接口
    def _api_admin_activities(self):
        rows = []
        for a in store.list_activities():
            card = activity_card(a, 0, True)
            card["createdBy"] = a["createdBy"]
            card["maxPerIp"] = a["maxPerIp"]
            card["allowGuest"] = a.get("allowGuest", True)
            card["closed"] = a["closed"]
            card["questionCount"] = len(a["config"].get("questions") or [])
            rows.append(card)
        return self._json({"ok": True, "activities": rows})

    def _api_admin_activity(self, qs):
        aid = self._int(qs.get("id"))
        activity = store.get_activity(aid)
        if not activity:
            return self._json({"ok": False, "error": "活动不存在"}, 404)
        responses = store.list_responses(aid)
        results = store.compute_results(activity, responses)
        return self._json(
            {
                "ok": True,
                "activity": {
                    "id": activity["id"],
                    "kind": activity["kind"],
                    "kindName": store.KIND_NAMES.get(activity["kind"], ""),
                    "title": activity["title"],
                    "description": activity["description"],
                    "deadline": activity["deadline"],
                    "maxPerIp": activity["maxPerIp"],
                    "closed": activity["closed"],
                    "config": activity["config"],
                    "createdBy": activity["createdBy"],
                    "createdAt": activity["createdAt"],
                    "status": store.status_of(activity),
                    "statusText": store.STATUS_TEXT[store.status_of(activity)],
                    "count": activity["count"],
                },
                "results": results,
                "responses": [
                    {
                        "id": r["id"],
                        "data": r["data"],
                        "ip": r["ip"],
                        "isAdmin": r["isAdmin"],
                        "adminName": r["adminName"],
                        "userId": r["userId"],
                        "userName": r["userName"],
                        "createdAt": r["createdAt"],
                    }
                    for r in responses[:1000]
                ],
                "truncated": len(responses) > 1000,
                "bannedTargets": sorted(store.ban_targets()),
            }
        )

    def _api_admin_export(self, qs):
        aid = self._int(qs.get("id"))
        activity = store.get_activity(aid)
        if not activity:
            return self._json({"ok": False, "error": "活动不存在"}, 404)
        header, table = store.export_table(activity)
        fmt = (qs.get("fmt") or "xlsx").lower()
        if fmt == "csv":
            body = exporters.build_csv(header, table)
            ctype = "text/csv; charset=utf-8"
            ext = "csv"
        else:
            body = exporters.build_xlsx(header, table, sheet_name=activity["title"] or "数据")
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        return self._download(body, ctype, f"{activity['title']}.{ext}")

    # ---------------- POST
    def do_POST(self):
        self._tag_cache = None
        self._pending_device = None
        path = urlparse(self.path).path
        data = self._read_body()

        if path == "/api/activity/submit":
            return self._do_submit(data)

        if path == "/api/user/register":
            return self._do_user_register(data)
        if path == "/api/user/login":
            return self._do_user_login(data)
        if path == "/api/user/logout":
            drop_user_session(self._cookie(USER_COOKIE))
            cookie = f"{USER_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            return self._json({"ok": True}, 200, headers=[("Set-Cookie", cookie)])
        if path == "/api/user/profile":
            return self._do_my_profile(data)
        if path == "/api/user/password":
            return self._do_my_password(data)

        if path == "/api/admin/login":
            return self._do_login(data)
        if path == "/api/admin/logout":
            drop_session(self._cookie("admin_token"))
            cookie = "admin_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            return self._json({"ok": True}, 200, headers=[("Set-Cookie", cookie)])

        if not path.startswith("/api/admin/"):
            return self._json({"ok": False, "error": "页面不存在"}, 404)

        admin = self._require_admin()
        if not admin:
            return

        if path == "/api/admin/activity/save":
            return self._do_activity_save(data, admin)
        if path == "/api/admin/activity/close":
            return self._do_activity_close(data)
        if path == "/api/admin/activity/delete":
            return self._do_activity_delete(data)
        if path == "/api/admin/response/delete":
            return self._do_response_delete(data)
        if path == "/api/admin/admins/save":
            return self._do_admin_save(data, admin)
        if path == "/api/admin/admins/delete":
            return self._do_admin_delete(data, admin)
        if path == "/api/admin/users/password":
            return self._do_user_password(data)
        if path == "/api/admin/users/delete":
            return self._do_user_delete(data)
        if path == "/api/admin/users/create":
            return self._do_admin_user_create(data)
        if path == "/api/admin/notice/save":
            return self._do_notice_save(data)
        if path == "/api/admin/notice/delete":
            return self._do_notice_delete(data)
        if path == "/api/admin/bans/add":
            return self._do_ban_add(data, admin)
        if path == "/api/admin/bans/delete":
            return self._do_ban_delete(data)

        return self._json({"ok": False, "error": "页面不存在"}, 404)

    def _do_submit(self, data):
        aid = self._int(data.get("id"))
        activity = store.get_activity(aid)
        if not activity:
            return self._json({"ok": False, "error": "活动不存在"}, 404)
        admin = self._admin()
        user = self._user()
        ok, msg, record = store.submit_response(
            activity, data, self._client_ip(), admin, user
        )
        activity = store.get_activity(aid)
        _, user2, used, by_account = self._identity(activity)
        need_login = not admin and not user2 and not activity.get("allowGuest", True)
        card = activity_card(
            activity,
            used,
            bool(admin),
            need_login=need_login,
            by_account=by_account,
            by_device=self._device_identity(by_account),
        )
        results = None
        if activity["kind"] == "signup" or activity["config"].get("show_results"):
            results = store.compute_results(activity)
        if ok and record:
            if admin:
                who = f"管理员 {admin['username']}"
            elif user:
                who = f"账号 {user['nickname'] or user['username']}"
            else:
                who = "访客"
            print(f"[提交] 活动#{aid} {activity['title']} · {who} · {now_stamp()}")
        return self._json(
            {
                "ok": ok,
                "message": msg,
                "error": "" if ok else msg,
                "record": record,
                "card": card,
                "results": results,
            }
        )

    def _login_gate(self, scope, username):
        """登录前的三道闸。返回要直接回给客户端的 429 响应，或 None。

        scope: "user" / "admin"，两种入口各算各的。
        同时按「来源」和「账号名」计数，并检查全站失败速率。
        """
        src = "src:" + scope + ":" + (self._client_ip() or "-")
        who = "name:" + scope + ":" + (username or "").lower()
        self._login_keys = (src, who)
        self._login_soft = False
        hard, soft = global_gate()
        if hard:
            return self._too_many_attempts(hard)
        wait = max(login_retry_after(src), login_retry_after(who))
        if wait:
            return self._too_many_attempts(wait)
        self._login_soft = soft
        return None

    def _login_fail(self):
        """记一次失败，返回是否已经触顶被锁。"""
        blocked = False
        for key in getattr(self, "_login_keys", ()):
            if login_failed(key):
                blocked = True
        global_failed()
        return blocked

    def _login_ok(self):
        for key in getattr(self, "_login_keys", ()):
            login_succeeded(key)

    def _too_many_attempts(self, wait):
        minutes = max(1, int((wait + 59) // 60))
        return self._json(
            {
                "ok": False,
                "error": "尝试次数过多，请 %d 分钟后再试" % minutes,
                "retryAfter": wait,
            },
            429,
            headers=[("Retry-After", str(wait))],
        )

    def _do_login(self, data):
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        blocked = self._login_gate("admin", username)
        if blocked:
            return blocked
        admin = store.verify_admin(username, password)
        if not admin:
            time.sleep(0.4 + (2.0 if getattr(self, "_login_soft", False) else 0.0))
            if self._login_fail():
                return self._too_many_attempts(LOGIN_BLOCK_SECONDS)
            return self._json({"ok": False, "error": LOGIN_BAD_TEXT}, 401)
        self._login_ok()
        token = new_session(admin)
        cookie = (
            f"admin_token={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax"
        )
        return self._json({"ok": True, "admin": admin}, 200, headers=[("Set-Cookie", cookie)])

    def _do_user_register(self, data):
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        nickname = str(data.get("nickname") or "").strip()
        try:
            user = store.create_user(username, password, nickname, ip=self._client_ip())
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        token = new_user_session(user)
        cookie = (
            f"{USER_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax"
        )
        return self._json({"ok": True, "user": user}, 200, headers=[("Set-Cookie", cookie)])

    def _do_user_login(self, data):
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        blocked = self._login_gate("user", username)
        if blocked:
            return blocked
        user = store.verify_user(username, password)
        if not user:
            # 密码不对时一律同一句话，不区分「账号不存在 / 密码错」。
            # 只有密码校验通过之后，才告诉对方「这是管理员账号，请走后台」——
            # 能走到这一步说明他本来就知道这组凭证，不算泄漏。
            if store.verify_admin(username, password):
                self._login_ok()
                return self._json(
                    {
                        "ok": False,
                        "error": "这是管理员账号，请到「管理员入口」登录（/admin）",
                    },
                    401,
                )
            time.sleep(0.4 + (2.0 if getattr(self, "_login_soft", False) else 0.0))
            if self._login_fail():
                return self._too_many_attempts(LOGIN_BLOCK_SECONDS)
            return self._json({"ok": False, "error": LOGIN_BAD_TEXT}, 401)
        self._login_ok()
        token = new_user_session(user)
        cookie = (
            f"{USER_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax"
        )
        return self._json({"ok": True, "user": user}, 200, headers=[("Set-Cookie", cookie)])

    def _do_my_profile(self, data):
        """已登录的普通成员改自己的名字。"""
        user = self._user()
        if not user:
            return self._json({"ok": False, "error": "请先登录"}, 401)
        nickname = str(data.get("nickname") or "").strip()
        try:
            updated = store.set_user_nickname(user["id"], nickname)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        return self._json({"ok": True, "user": updated})

    def _do_my_password(self, data):
        """已登录的普通成员改自己的密码，要验旧密码。"""
        user = self._user()
        if not user:
            return self._json({"ok": False, "error": "请先登录"}, 401)
        try:
            store.change_user_password(
                user["id"],
                str(data.get("old_password") or ""),
                str(data.get("password") or ""),
            )
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        return self._json({"ok": True})

    def _do_activity_save(self, data, admin):
        aid = self._int(data.get("id"))
        try:
            if aid:
                activity = store.update_activity(
                    aid,
                    title=data.get("title"),
                    description=data.get("description"),
                    deadline=data.get("deadline"),
                    max_per_ip=data.get("max_per_ip"),
                    closed=data.get("closed"),
                    sports=data.get("sports"),
                    allow_guest=data.get("allow_guest"),
                )
            else:
                activity = store.create_activity(
                    str(data.get("kind") or ""),
                    data.get("title") or "",
                    data.get("description") or "",
                    data.get("config") or {},
                    data.get("deadline") or "",
                    data.get("max_per_ip") or 0,
                    admin["username"],
                    allow_guest=bool(data.get("allow_guest", True)),
                )
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)})
        return self._json({"ok": True, "activity": activity, "message": "已保存"})

    def _do_activity_close(self, data):
        aid = self._int(data.get("id"))
        closed = bool(data.get("closed"))
        try:
            activity = store.update_activity(aid, closed=closed)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)})
        return self._json(
            {
                "ok": True,
                "activity": activity,
                "message": "已结束" if closed else "已重新开放",
            }
        )

    def _do_activity_delete(self, data):
        aid = self._int(data.get("id"))
        if not aid or not store.delete_activity(aid):
            return self._json({"ok": False, "error": "活动不存在"})
        return self._json({"ok": True, "message": "已删除活动及其提交记录"})

    def _do_response_delete(self, data):
        rid = self._int(data.get("id"))
        if not rid or not store.delete_response(rid):
            return self._json({"ok": False, "error": "记录不存在"})
        return self._json({"ok": True, "message": "已删除"})

    def _do_admin_save(self, data, me):
        aid = self._int(data.get("id"))
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        try:
            if aid:
                if not me["isSuper"] and aid != me["id"]:
                    return self._json({"ok": False, "error": "只有高级管理员能修改他人密码"})
                if not password:
                    return self._json({"ok": False, "error": "请填写新密码"})
                if not store.set_admin_password(aid, password):
                    return self._json({"ok": False, "error": "管理员不存在"})
                return self._json({"ok": True, "message": "密码已更新"})
            if not me["isSuper"]:
                return self._json({"ok": False, "error": "只有高级管理员能新增账号"})
            admin = store.create_admin(
                username, password, bool(data.get("is_super"))
            )
            return self._json({"ok": True, "admin": admin, "message": "已添加管理员"})
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)})

    def _do_admin_delete(self, data, me):
        aid = self._int(data.get("id"))
        if not aid:
            return self._json({"ok": False, "error": "参数错误"})
        if not me["isSuper"]:
            return self._json({"ok": False, "error": "只有高级管理员能删除账号"})
        if aid == me["id"]:
            return self._json({"ok": False, "error": "不能删除当前登录的账号"})
        try:
            store.delete_admin(aid)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)})
        return self._json({"ok": True, "message": "已删除"})

    def _do_user_password(self, data):
        uid = self._int(data.get("id"))
        pwd = str(data.get("password") or "")
        if not uid:
            return self._json({"ok": False, "error": "参数错误"})
        if not store.find_user(uid):
            return self._json({"ok": False, "error": "账号不存在"}, 404)
        try:
            store.set_user_password(uid, pwd)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        return self._json({"ok": True, "message": "密码已重置"})

    def _do_user_delete(self, data):
        uid = self._int(data.get("id"))
        if not uid:
            return self._json({"ok": False, "error": "参数错误"})
        if not store.delete_user(uid):
            return self._json({"ok": False, "error": "账号不存在"}, 404)
        return self._json({"ok": True, "message": "已删除"})

    def _do_admin_user_create(self, data):
        """管理员代建普通账号：不受「每个网络每周 1 个」的限制。"""
        try:
            user = store.create_user(
                str(data.get("username") or "").strip(),
                str(data.get("password") or ""),
                str(data.get("nickname") or "").strip(),
                ip="",
                by_admin=True,
            )
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        return self._json({"ok": True, "user": user})

    def _do_notice_save(self, data):
        nid = self._int(data.get("id"))
        try:
            if nid:
                notice = store.update_notice(
                    nid,
                    title=data.get("title"),
                    content=data.get("content"),
                    pinned=bool(data.get("pinned")),
                    hidden=bool(data.get("hidden")),
                )
                msg = "公告已更新"
            else:
                admin = self._admin()
                notice = store.create_notice(
                    str(data.get("title") or ""),
                    str(data.get("content") or ""),
                    pinned=bool(data.get("pinned")),
                    created_by=admin["username"] if admin else "",
                )
                msg = "公告已发布"
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        return self._json({"ok": True, "message": msg, "notice": notice})

    def _do_notice_delete(self, data):
        nid = self._int(data.get("id"))
        if not nid:
            return self._json({"ok": False, "error": "参数错误"})
        if not store.delete_notice(nid):
            return self._json({"ok": False, "error": "这条公告不存在"}, 404)
        return self._json({"ok": True, "message": "公告已删除"})

    def _do_ban_add(self, data, admin):
        target = str(data.get("target") or "").strip()
        if not target:
            return self._json({"ok": False, "error": "要封禁的标识不能为空"})
        if target in ("127.0.0.1", "::1"):
            return self._json(
                {
                    "ok": False,
                    "error": "127.0.0.1 是穿透转发时的占位地址，封了会连自己也进不来",
                }
            )
        try:
            ban = store.add_ban(
                target,
                reason=str(data.get("reason") or ""),
                created_by=admin["username"],
            )
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)})
        return self._json({"ok": True, "ban": ban, "message": "已封禁"})

    def _do_ban_delete(self, data):
        bid = self._int(data.get("id"))
        if not bid:
            return self._json({"ok": False, "error": "参数错误"})
        if not store.delete_ban(bid):
            return self._json({"ok": False, "error": "这条封禁记录不存在"}, 404)
        return self._json({"ok": True, "message": "已解除封禁"})


def now_stamp():
    return datetime.now().strftime("%H:%M:%S")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def bind_server(prefer_port):
    last_err = None
    for port in range(prefer_port, prefer_port + 12):
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            httpd.daemon_threads = True
            httpd.allow_reuse_address = True
            return httpd, port
        except OSError as exc:
            last_err = exc
    raise SystemExit(f"端口 {prefer_port}~{prefer_port + 11} 都被占用，无法启动：{last_err}")


def main():
    args = sys.argv[1:]
    port = int(settings.CFG.get("port") or 8000)
    open_browser = True
    if "--no-browser" in args:
        open_browser = False
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass

    store.init_db()
    httpd, real_port = bind_server(port)
    local = f"http://127.0.0.1:{real_port}"
    lan = f"http://{lan_ip()}:{real_port}"

    print("=" * 58)
    print(f"  {settings.CFG.get('title')} - 已启动")
    print(f"  本机访问 : {local}")
    print(f"  局域网   : {lan}")
    print(f"  管理后台 : {local}/admin")
    print(f"  数据文件 : {settings.DB_PATH}")
    print("  Ctrl+C 停止服务")
    print("=" * 58)

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(local)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

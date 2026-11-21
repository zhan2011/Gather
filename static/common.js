/* 公共工具：DOM、请求、提示、时间格式 */
(function () {
  "use strict";

  window.$ = function (sel, root) {
    return (root || document).querySelector(sel);
  };
  window.$$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  window.esc = function (value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  var toastTimer = null;
  window.toast = function (message, isError) {
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = isError ? "show err" : "show";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.className = "";
    }, isError ? 3600 : 2400);
  };

  window.api = async function (path, options) {
    options = options || {};
    var init = {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    };
    if (options.body !== undefined && options.body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    var resp;
    try {
      resp = await fetch(path, init);
    } catch (err) {
      return { ok: false, error: "网络连接失败，请检查网络后重试" };
    }
    var data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = { ok: false, error: "服务器返回异常（" + resp.status + "）" };
    }
    if (resp.status === 401 && path.indexOf("/api/admin/") === 0 && path !== "/api/admin/login") {
      data.needLogin = true;
    }
    if (data.error === undefined) data.error = "";
    return data;
  };

  /* "2026-05-01 12:00" -> "2026年5月1日 12:00" */
  window.fmtTime = function (text, withYear) {
    if (!text) return "";
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(text);
    if (!m) return text;
    var date = withYear
      ? m[1] + "年" + Number(m[2]) + "月" + Number(m[3]) + "日"
      : Number(m[2]) + "月" + Number(m[3]) + "日";
    if (m[4]) date += " " + m[4] + ":" + m[5];
    return date;
  };

  window.deadlineText = function (activity) {
    if (activity.status === "closed") return "已手动结束";
    if (!activity.deadline) return "不设截止";
    if (activity.status === "ended") return "已于 " + fmtTime(activity.deadline) + " 截止";
    return "截止 " + fmtTime(activity.deadline);
  };

  /* 距离截止还有多久 */
  window.remainText = function (deadline) {
    if (!deadline) return "";
    var target = new Date(deadline.replace(/-/g, "/"));
    var diff = target.getTime() - Date.now();
    if (isNaN(diff) || diff <= 0) return "";
    var day = Math.floor(diff / 86400000);
    var hour = Math.floor((diff % 86400000) / 3600000);
    if (day > 0) return "剩 " + day + " 天" + (hour ? " " + hour + " 小时" : "");
    var minute = Math.floor((diff % 3600000) / 60000);
    if (hour > 0) return "剩 " + hour + " 小时 " + minute + " 分";
    return "剩 " + Math.max(1, minute) + " 分钟";
  };

  window.KIND_NAME = { signup: "报名", survey: "问卷", poll: "投票" };

  /* ---------------- 普通账号：登录 / 注册 / 退出 ---------------- */
  var Auth = {
    user: null,
    onChange: null
  };
  window.Auth = Auth;

  Auth.set = function (user) {
    Auth.user = user || null;
    if (typeof Auth.onChange === "function") Auth.onChange(Auth.user);
  };

  Auth.refresh = async function () {
    var data = await api("/api/user/me");
    Auth.set(data && data.ok ? data.user : null);
    return Auth.user;
  };

  /* 顶栏右侧的登录状态区，返回 HTML */
  Auth.barHtml = function (admin) {
    if (Auth.user) {
      return (
        '<span class="who">已登录 <b>' + esc(Auth.user.nickname || Auth.user.username) +
        "</b></span>" +
        '<button type="button" class="btn small ghost" data-auth="account">我的账号</button>' +
        '<button type="button" class="btn small ghost" data-auth="logout">退出</button>'
      );
    }
    if (admin && admin.loggedIn) return "";
    return '<button type="button" class="btn small primary" data-auth="open">登录 / 注册</button>';
  };

  Auth.bindBar = function (root, admin, after) {
    $$("[data-auth]", root || document).forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (btn.dataset.auth === "logout") {
          await api("/api/user/logout", { method: "POST", body: {} });
          Auth.set(null);
          toast("已退出登录");
          if (after) after();
        } else if (btn.dataset.auth === "account") {
          Auth.openAccount(function () {
            if (after) after();
          });
        } else {
          Auth.open("login", function () {
            if (after) after();
          });
        }
      });
    });
  };

  var authEl = null;
  function buildAuth() {
    if (authEl) return authEl;
    authEl = document.createElement("div");
    authEl.className = "mask";
    authEl.id = "authMask";
    authEl.innerHTML =
      '<div class="auth-card">' +
      '<button type="button" class="auth-close" aria-label="关闭">✕</button>' +
      '<h2 id="authTitle">登录</h2>' +
      '<p class="auth-tip" id="authTip">登录后参与活动，同一账号的填报次数单独计算。</p>' +
      '<label class="field auth-field"><span class="field-label">用户名</span>' +
      '<input id="authUser" maxlength="20" autocomplete="username" placeholder="2-20 位字母、数字或中文"></label>' +
      '<label class="field auth-field" id="authNickWrap" style="display:none"><span class="field-label">昵称（选填）</span>' +
      '<input id="authNick" maxlength="20" autocomplete="nickname" placeholder="显示在提交记录里"></label>' +
      '<label class="field auth-field"><span class="field-label">密码</span>' +
      '<input id="authPass" type="password" maxlength="64" autocomplete="current-password" placeholder="至少 6 位"></label>' +
      '<div class="notice err" id="authErr" style="display:none"></div>' +
      '<button type="button" class="btn primary block" id="authSubmit">登录</button>' +
      '<div class="auth-switch">' +
      '<span id="authSwitchText">还没有账号？</span> ' +
      '<a href="#" id="authSwitch">立即注册</a>' +
      "</div></div>";
    document.body.appendChild(authEl);
    return authEl;
  }

  Auth.open = function (mode, done) {
    var el = buildAuth();
    var modeNow = mode === "register" ? "register" : "login";
    el.style.display = "flex";
    function apply() {
      var isReg = modeNow === "register";
      $("#authTitle", el).textContent = isReg ? "注册新账号" : "登录";
      $("#authSubmit", el).textContent = isReg ? "注册并登录" : "登录";
      $("#authNickWrap", el).style.display = isReg ? "" : "none";
      $("#authSwitchText", el).textContent = isReg ? "已经有账号？" : "还没有账号？";
      $("#authSwitch", el).textContent = isReg ? "去登录" : "立即注册";
      $("#authTip", el).textContent = isReg
        ? "建一个参与者账号，登录后提交次数按这个账号单独计算。"
        : "参与者账号登录。管理员请从首页的「管理员入口」登录后台。";
      $("#authPass").setAttribute("autocomplete", isReg ? "new-password" : "current-password");
      $("#authErr", el).style.display = "none";
    }
    apply();
    setTimeout(function () { $("#authUser", el).focus(); }, 30);

    function close() { el.style.display = "none"; }
    $(".auth-close", el).onclick = close;
    el.onclick = function (e) { if (e.target === el) close(); };
    $("#authSwitch", el).onclick = function (e) {
      e.preventDefault();
      modeNow = modeNow === "login" ? "register" : "login";
      apply();
      setTimeout(function () { $("#authUser", el).focus(); }, 20);
    };
    async function submit() {
      var username = $("#authUser", el).value.trim();
      var password = $("#authPass", el).value;
      var nickname = $("#authNick", el).value.trim();
      var errBox = $("#authErr", el);
      errBox.style.display = "none";
      if (!username) { errBox.textContent = "请填写用户名"; errBox.style.display = ""; return; }
      if (!password) { errBox.textContent = "请填写密码"; errBox.style.display = ""; return; }
      var btn = $("#authSubmit", el);
      btn.disabled = true;
      var path = modeNow === "register" ? "/api/user/register" : "/api/user/login";
      var data = await api(path, { method: "POST", body: { username: username, password: password, nickname: nickname } });
      btn.disabled = false;
      if (!data.ok) {
        errBox.textContent = data.error || "操作失败";
        errBox.style.display = "";
        return;
      }
      Auth.set(data.user);
      close();
      toast(modeNow === "register" ? "注册成功，已登录" : "登录成功");
      $("#authPass", el).value = "";
      if (typeof done === "function") done(data.user);
    }
    $("#authSubmit", el).onclick = submit;
    $$("#authMask input").forEach(function (input) {
      input.onkeydown = function (e) { if (e.key === "Enter") submit(); };
    });
  };
  /* ------------------------------------------------------------------ 我的账号 */
  var acctEl = null;
  function buildAccount() {
    if (acctEl) return acctEl;
    acctEl = document.createElement("div");
    acctEl.className = "mask";
    acctEl.id = "acctMask";
    acctEl.innerHTML =
      '<div class="auth-card">' +
      '<button type="button" class="auth-close" aria-label="关闭">✕</button>' +
      "<h2>我的账号</h2>" +
      '<p class="auth-tip" id="acctWho"></p>' +
      '<label class="field auth-field"><span class="field-label">名字（2-20 字，显示在提交记录里）</span>' +
      '<input id="acctNick" maxlength="20" autocomplete="nickname"></label>' +
      '<button type="button" class="btn primary block" id="acctSaveNick">保存名字</button>' +
      '<p class="auth-tip" style="margin:20px 0 8px">修改密码</p>' +
      '<label class="field auth-field"><span class="field-label">原来的密码</span>' +
      '<input id="acctOld" type="password" maxlength="64" autocomplete="current-password"></label>' +
      '<label class="field auth-field"><span class="field-label">新密码（至少 6 位）</span>' +
      '<input id="acctNew" type="password" maxlength="64" autocomplete="new-password"></label>' +
      '<label class="field auth-field"><span class="field-label">再输一次新密码</span>' +
      '<input id="acctNew2" type="password" maxlength="64" autocomplete="new-password"></label>' +
      '<div class="notice err" id="acctErr" style="display:none"></div>' +
      '<button type="button" class="btn primary block" id="acctSavePwd">保存密码</button>' +
      "</div>";
    document.body.appendChild(acctEl);
    return acctEl;
  }

  Auth.openAccount = function (done) {
    if (!Auth.user) return;
    var el = buildAccount();
    el.style.display = "flex";
    $("#acctWho", el).textContent =
      "用户名：" + Auth.user.username + (Auth.user.createdByAdmin ? "（管理员代建）" : "");
    $("#acctNick", el).value = Auth.user.nickname || Auth.user.username;
    $("#acctOld", el).value = "";
    $("#acctNew", el).value = "";
    $("#acctNew2", el).value = "";
    var errBox = $("#acctErr", el);
    errBox.style.display = "none";
    function fail(msg) { errBox.textContent = msg; errBox.style.display = ""; }
    function close() { el.style.display = "none"; }
    $(".auth-close", el).onclick = close;
    el.onclick = function (e) { if (e.target === el) close(); };

    $("#acctSaveNick", el).onclick = async function () {
      errBox.style.display = "none";
      var name = $("#acctNick", el).value.trim();
      var btn = this;
      btn.disabled = true;
      var data = await api("/api/user/profile", { method: "POST", body: { nickname: name } });
      btn.disabled = false;
      if (!data.ok) return fail(data.error || "保存失败");
      Auth.set(data.user);
      toast("名字已保存");
      if (typeof done === "function") done();
      close();
    };

    $("#acctSavePwd", el).onclick = async function () {
      errBox.style.display = "none";
      var oldPwd = $("#acctOld", el).value;
      var newPwd = $("#acctNew", el).value;
      var newPwd2 = $("#acctNew2", el).value;
      if (!oldPwd) return fail("请填写原来的密码");
      if (!newPwd) return fail("请填写新密码");
      if (newPwd.length < 6) return fail("新密码至少 6 位");
      if (newPwd !== newPwd2) return fail("两次输入的新密码不一样");
      var btn = this;
      btn.disabled = true;
      var data = await api("/api/user/password", {
        method: "POST",
        body: { old_password: oldPwd, password: newPwd }
      });
      btn.disabled = false;
      if (!data.ok) return fail(data.error || "保存失败");
      toast("密码已修改");
      close();
    };
  };
})();

/* 管理后台：活动管理 + 管理员账号 */
(function () {
  "use strict";

  var state = {
    me: null,
    activities: [],
    admins: [],
    users: [],
    notices: [],
    bans: [],
    userKeyword: "",
    view: "login",
    newKind: null,
    editing: null,
    detail: null
  };
  var qSeq = 0;

  var DEFAULT_SPORTS = [
    { key: "morning", name: "上午场", icon: "", capacity: 0 },
    { key: "afternoon", name: "下午场", icon: "", capacity: 0 },
    { key: "evening", name: "晚上场", icon: "", capacity: 0 }
  ];

  /* ---------------- 视图切换 ---------------- */
  function setView(name) {
    state.view = name;
    $("#loginView").style.display = name === "login" ? "" : "none";
    $("#adminView").style.display = name === "login" ? "none" : "";
    $("#seg").style.display =
      name === "list" || name === "admins" || name === "users" || name === "notices" || name === "bans"
        ? ""
        : "none";
    $("#tabActivities").style.display = name === "list" ? "" : "none";
    $("#tabNotices").style.display = name === "notices" ? "" : "none";
    $("#tabUsers").style.display = name === "users" ? "" : "none";
    $("#tabAdmins").style.display = name === "admins" ? "" : "none";
    $("#tabBans").style.display = name === "bans" ? "" : "none";
    $("#editorView").style.display = name === "editor" ? "" : "none";
    $("#detailView").style.display = name === "detail" ? "" : "none";
    window.scrollTo(0, 0);
  }

  /* 来源标识：真实 IP 原样显示；拿不到 IP 时后端记的是 dev-xxxx，加个「设备」前缀 */
  function srcText(ip) {
    if (!ip) return "";
    return String(ip).indexOf("dev-") === 0 ? "设备 " + ip : ip;
  }

  function toLocalInput(text) {
    if (!text) return "";
    var m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(text);
    return m ? m[1] + "T" + m[2] : "";
  }

  /* ---------------- 登录 ---------------- */
  async function boot() {
    var data = await api("/api/admin/me");
    if (data.ok) {
      state.me = data.admin;
      enterAdmin();
    } else {
      setView("login");
      setTimeout(function () {
        var input = $("#loginUser");
        if (input) input.focus();
      }, 60);
    }
  }

  async function doLogin() {
    var btn = $("#btnLogin");
    var username = $("#loginUser").value.trim();
    var password = $("#loginPass").value;
    if (!username || !password) return toast("请输入用户名和密码", true);
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>登录中…';
    var res = await api("/api/admin/login", {
      method: "POST",
      body: { username: username, password: password }
    });
    btn.disabled = false;
    btn.textContent = "登 录";
    if (!res.ok) return toast(res.error || "登录失败", true);
    state.me = res.admin;
    $("#loginPass").value = "";
    toast("登录成功");
    enterAdmin();
  }

  function enterAdmin() {
    renderMe();
    setView("list");
    loadActivities();
    refreshMe();
  }

  function renderMe() {
    $("#meInfo").innerHTML =
      "当前账号：<b>" + esc(state.me.username) + "</b>" +
      (state.me.isSuper ? ' <span class="badge kind">高级管理员</span>' : "");
  }

  /* 顺手确认服务端能否看到访客真实 IP：拿不到就会退回按设备 cookie 计次 */
  async function refreshMe() {
    var res = await api("/api/admin/me");
    if (!res || !res.ok) return;
    state.me = res.admin;
    var tag = String(res.clientTag || "");
    var net = res.net || {};
    renderMe();
    if (tag.indexOf("dev-") === 0) {
      var why = net.proxyNote || "";
      $("#meInfo").innerHTML +=
        ' <span class="badge kind" title="服务端看不到访客真实 IP（多半是 TCP 内网穿透），次数和注册限制已改为按浏览器区分。' +
        esc(why) + '">按设备区分</span>';
    } else if (tag) {
      $("#meInfo").innerHTML +=
        ' <span class="src" title="这是服务端认到的你的来源地址，访客的计次和封禁都按这个算">来源 ' +
        esc(tag) + "</span>";
    }
  }

  /* ---------------- 活动列表 ---------------- */
  async function loadActivities() {
    var data = await api("/api/admin/activities");
    if (data.needLogin) {
      setView("login");
      return;
    }
    if (!data.ok) return toast(data.error || "加载失败", true);
    state.activities = data.activities || [];
    renderList();
  }

  function renderList() {
    var list = state.activities;
    if (!list.length) {
      $("#actList").innerHTML =
        '<div class="empty">还没有活动<br>' +
        "点上面的按钮发起第一场报名、问卷或投票</div>";
      return;
    }
    $("#actList").innerHTML = list
      .map(function (a) {
        var meta = [
          "<span>" + esc(deadlineText(a)) + "</span>",
          "<span>" + esc(a.hint) + "</span>",
          "<span>" + (a.maxPerIp > 0 ? "每 IP 限 " + a.maxPerIp + " 次" : "不限 IP 次数") + "</span>",
          "<span>发起人 " + esc(a.createdBy || "-") + "</span>"
        ];
        return (
          '<div class="card">' +
          '<div class="act-head">' +
          '<div class="act-title">' + esc(a.title) + "</div>" +
          '<span class="badge kind">' + esc(a.kindName) + "</span>" +
          '<span class="badge ' + a.status + '">' + esc(a.statusText) + "</span>" +
          "</div>" +
          '<div class="act-meta">' + meta.join("") + "</div>" +
          '<div class="row-actions" style="margin-top:14px">' +
          '<button class="btn small primary" data-act="detail" data-id="' + a.id + '">查看明细</button>' +
          '<a class="btn small" href="/a/' + a.id + '" target="_blank">去参与</a>' +
          '<button class="btn small" data-act="edit" data-id="' + a.id + '">设置</button>' +
          '<button class="btn small" data-act="export" data-id="' + a.id + '">导出 Excel</button>' +
          '<button class="btn small" data-act="toggle" data-id="' + a.id + '">' +
          (a.closed ? "重新开放" : "结束") + "</button>" +
          '<button class="btn small danger" data-act="delete" data-id="' + a.id + '">删除</button>' +
          "</div></div>"
        );
      })
      .join("");
  }

  /* ---------------- 编辑器 ---------------- */
  async function openEditor(activity, kind) {
    // 列表里的活动不带 config，编辑前先取一次详情（顺带拿到各项目已报人数）
    if (activity && (!activity.config || !activity._results)) {
      var data = await api("/api/admin/activity?id=" + activity.id);
      if (data.needLogin) return setView("login");
      if (!data.ok) return toast(data.error || "加载失败", true);
      activity = data.activity;
      activity._results = data.results;
    }
    state.editing = activity || null;
    state.newKind = kind || (activity && activity.kind);
    setView("editor");
    renderEditor();
  }

  function buildQuestionEl(q) {
    qSeq += 1;
    var wrap = document.createElement("div");
    wrap.className = "q-item";
    wrap.innerHTML =
      '<div class="q-head">' +
      '<span class="idx"></span>' +
      '<input class="q-label" type="text" maxlength="200" placeholder="题干，例如：你最希望班级组织哪类活动？">' +
      '<select class="q-type">' +
      '<option value="single">单选</option>' +
      '<option value="multi">多选</option>' +
      '<option value="text">填空</option>' +
      "</select>" +
      '<button type="button" class="icon-btn" data-x="del-q" title="删除本题">✕</button>' +
      "</div>" +
      '<div class="q-options"></div>' +
      '<div class="q-flags">' +
      '<label><input type="checkbox" class="q-req" checked> 必答</label>' +
      '<label><input type="checkbox" class="q-other"> 允许填「其他」（选项外自己写一段）</label>' +
      '<label class="q-max-wrap"><span>最多选</span>' +
      '<input type="number" class="q-max" min="0" max="30" value="0" inputmode="numeric">' +
      '<span>项（0 = 不限）</span></label>' +
      "</div>";

    wrap.querySelector(".q-label").value = q ? q.label || "" : "";
    wrap.querySelector(".q-type").value = q ? q.type || "single" : "single";
    wrap.querySelector(".q-req").checked = q ? q.required !== false : true;
    wrap.querySelector(".q-other").checked = q ? !!q.other : false;
    wrap.querySelector(".q-max").value = q && q.max ? q.max : 0;

    var optWrap = wrap.querySelector(".q-options");
    var options = (q && q.options) || ["选项 1", "选项 2"];
    options.forEach(function (text) {
      optWrap.appendChild(buildOptionEl(text));
    });
    if (!q) optWrap.appendChild(buildOptionEl(""));

    var addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn small";
    addBtn.textContent = "+ 添加选项";
    addBtn.dataset.x = "add-opt";
    optWrap.appendChild(addBtn);

    syncQuestionType(wrap);
    return wrap;
  }

  function buildOptionEl(text) {
    var row = document.createElement("div");
    row.className = "opt-row";
    row.innerHTML =
      '<input type="text" maxlength="100" placeholder="选项内容">' +
      '<button type="button" class="icon-btn" data-x="del-opt" title="删除选项">✕</button>';
    row.querySelector("input").value = text || "";
    return row;
  }

  function syncQuestionType(wrap) {
    var type = wrap.querySelector(".q-type").value;
    var optWrap = wrap.querySelector(".q-options");
    optWrap.style.display = type === "text" ? "none" : "";
    var otherLabel = wrap.querySelector(".q-other").parentNode;
    if (otherLabel) otherLabel.style.display = type === "text" ? "none" : "";
    var maxLabel = wrap.querySelector(".q-max-wrap");
    if (maxLabel) maxLabel.style.display = type === "multi" ? "" : "none";
  }

  function renumber() {
    $$("#qList .q-item").forEach(function (el, i) {
      var idx = el.querySelector(".idx");
      if (idx) idx.textContent = i + 1;
    });
  }

  function buildSportEl(s) {
    var wrap = document.createElement("div");
    wrap.className = "q-item";
    wrap.innerHTML =
      '<div class="q-head">' +
      '<input class="s-icon" type="text" maxlength="4" placeholder="图标" style="width:58px;flex:0 0 auto;text-align:center">' +
      '<input class="s-name" type="text" maxlength="20" placeholder="项目名称">' +
      '<input class="s-cap" type="number" min="0" placeholder="名额" style="width:86px;flex:0 0 auto">' +
      '<button type="button" class="icon-btn" data-x="del-sport" title="删除项目">✕</button>' +
      "</div>";
    wrap.querySelector(".s-icon").value = s ? s.icon || "" : "";
    wrap.querySelector(".s-name").value = s ? s.name || "" : "";
    wrap.querySelector(".s-cap").value = s && s.capacity ? s.capacity : "";
    return wrap;
  }

  function renderEditor() {
    var editing = state.editing;
    var kind = editing ? editing.kind : state.newKind;
    var isNew = !editing;
    var cfg = editing ? editing.config || {} : null;

    var html = "";
    html += '<div class="row-actions" style="margin-bottom:12px"><button class="btn small" data-back="list">← 返回</button></div>';
    html += '<h1 style="font-size:1.25rem;margin-bottom:16px">' +
      (isNew ? "发起" + KIND_NAME[kind] : "设置" + KIND_NAME[kind]) + "</h1>";

    if (!isNew) {
      html += '<div class="notice info">已发布活动的题目 / 项目不能修改（避免与已有提交对不上），' +
        "但<strong>名额</strong>、截止时间、每 IP 次数、标题和说明都可以改。如需换题，请新建一个活动。</div>";
    }

    html += '<div class="card">';
    html += '<div class="field"><label for="edTitle">标题<span class="req">*</span></label>' +
      '<input id="edTitle" type="text" maxlength="80" placeholder="例如：秋季班服尺码登记"></div>';
    html += '<div class="field"><label for="edDesc">说明（可选）</label>' +
      '<textarea id="edDesc" maxlength="1000" placeholder="写点参与须知、注意事项…"></textarea></div>';
    html += '<div class="field"><label for="edDeadline">截止时间</label>' +
      '<input id="edDeadline" type="datetime-local"><div class="hint-line">留空表示不设截止时间；到期后自动停止接收提交</div></div>';
    html += '<div class="field"><label for="edMaxIp">每个' +
      '<span id="edMaxIpWho">账号</span>最多提交次数</label>' +
      '<input id="edMaxIp" type="number" min="0" max="10000" value="1" inputmode="numeric">' +
      '<div class="hint-line">0 = 不限；登录的账号按账号计次，未登录的访客按 IP 计次；管理员提交不计入这里</div></div>';
    html += '<div class="field"><label class="hint-line" style="display:flex;align-items:center;gap:8px">' +
      '<input type="checkbox" id="edGuest" style="width:auto" checked> ' +
      "允许未登录的访客填写（关掉后必须登录账号才能提交）</label></div>";
    html += "</div>";

    if (kind === "signup") {
      html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:12px">报名项目</h3>';
      html += '<div id="sportList"></div>';
      if (isNew) {
        html += '<button class="btn small" id="btnAddSport" style="margin-top:10px">+ 添加项目</button>';
        html += '<div class="field" style="margin:14px 0 0">' +
          '<label class="hint-line" style="display:flex;align-items:center;gap:8px">' +
          '<input type="checkbox" id="edMulti" style="width:auto"> 允许同一人报多个项目</label></div>';
      }
      html += "</div>";
    } else {
      html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:12px">题目</h3>';
      html += '<div id="qList"></div>';
      if (isNew) {
        html += '<button class="btn small" id="btnAddQ" style="margin-top:10px">+ 添加题目</button>';
        html += '<div class="field" style="margin:14px 0 0">' +
          '<label class="hint-line" style="display:flex;align-items:center;gap:8px">' +
          '<input type="checkbox" id="edShowResults" style="width:auto"> 实时公开结果（投票建议勾选）</label></div>';
      }
      html += "</div>";
    }

    if (!isNew) {
      html += '<div class="card"><div class="row-actions">' +
        '<button class="btn primary" id="btnSaveActivity">保存设置</button>' +
        "</div></div>";
    } else {
      html += '<div class="card"><button class="btn primary block" id="btnSaveActivity">发布' +
        KIND_NAME[kind] + "</button></div>";
    }

    $("#editorView").innerHTML = html;

    // 填值
    if (editing) {
      $("#edTitle").value = editing.title || "";
      $("#edDesc").value = editing.description || "";
      $("#edDeadline").value = toLocalInput(editing.deadline);
      $("#edMaxIp").value = editing.maxPerIp || 0;
    } else {
      $("#edMaxIp").value = 1;
    }
    var guestBox = $("#edGuest");
    if (guestBox) {
      guestBox.checked = editing ? editing.allowGuest !== false : true;
    }

    if (kind === "signup") {
      var sportList = $("#sportList");
      if (isNew) {
        (cfg && cfg.sports ? cfg.sports : DEFAULT_SPORTS).forEach(function (s) {
          sportList.appendChild(buildSportEl(s));
        });
        if ($("#edMulti")) $("#edMulti").checked = !!(cfg && cfg.allow_multiple);
      } else {
        var resList = (editing && editing._results && editing._results.sports) || [];
        var joinedMap = {};
        resList.forEach(function (x) { joinedMap[x.key] = x.count; });
        sportList.innerHTML = (cfg.sports || []).map(function (s) {
          var joined = joinedMap[s.key] || 0;
          return '<div class="q-item" data-key="' + esc(s.key) + '">' +
            '<div class="q-head">' +
            (s.icon ? '<span class="icon">' + esc(s.icon) + "</span>" : "") +
            '<span style="flex:1;font-weight:600">' + esc(s.name) + "</span>" +
            '<span class="hint-line">已报 ' + joined + "</span>" +
            '<input class="s-cap" type="number" min="0" max="100000" inputmode="numeric" ' +
            'style="width:92px;flex:0 0 auto" value="' + (s.capacity || 0) + '">' +
            '<span class="hint-line">名额</span>' +
            "</div></div>";
        }).join("");
        sportList.insertAdjacentHTML("beforeend",
          '<div class="hint-line" style="margin-top:8px">名额填 0 表示不限；' +
          "调小到低于已报人数会保存失败。</div>");
      }
    } else {
      var qList = $("#qList");
      if (isNew) {
        var questions = (cfg && cfg.questions) || [null];
        questions.forEach(function (q) {
          qList.appendChild(buildQuestionEl(q));
        });
        if ($("#edShowResults")) $("#edShowResults").checked = !!(cfg && cfg.show_results);
        renumber();
      } else {
        var typeName = { single: "单选", multi: "多选", text: "填空" };
        qList.innerHTML = ((cfg && cfg.questions) || []).map(function (q, i) {
          var opts = q.type === "text" ? "自由填写"
            : (q.options || []).join("、");
          return '<div class="q-item"><div style="font-size:.9rem;font-weight:600">' +
            (i + 1) + ". " + esc(q.label) +
            ' <span class="hint-line" style="font-weight:400">' +
            (typeName[q.type] || q.type) + (q.required ? " · 必答" : " · 选填") +
            (q.other ? " · 可填其他" : "") +
            (q.type === "multi" && q.max ? " · 最多选 " + q.max + " 项" : "") +
            "</span></div>" +
            '<div class="hint-line" style="margin-top:6px">' + esc(opts) + "</div></div>";
        }).join("");
      }
    }
  }

  function readSports() {
    var sports = [];
    $$("#sportList .q-item").forEach(function (el, i) {
      var name = el.querySelector(".s-name").value.trim();
      if (!name) return;
      sports.push({
        name: name,
        icon: el.querySelector(".s-icon").value.trim(),
        capacity: Number(el.querySelector(".s-cap").value || 0) || 0
      });
    });
    return { sports: sports, allow_multiple: !!($("#edMulti") && $("#edMulti").checked) };
  }

  // 编辑已发布报名时只回传各项目的名额
  function readCaps() {
    var caps = [];
    $$("#sportList .q-item[data-key]").forEach(function (el) {
      var capEl = el.querySelector(".s-cap");
      if (!capEl) return;
      caps.push({
        key: el.getAttribute("data-key"),
        capacity: Number(capEl.value || 0) || 0
      });
    });
    return caps;
  }

  function readQuestions() {
    var questions = [];
    $$("#qList .q-item").forEach(function (el, i) {
      var label = el.querySelector(".q-label").value.trim();
      if (!label) return;
      var type = el.querySelector(".q-type").value;
      var options = [];
      if (type !== "text") {
        $$(".opt-row input", el).forEach(function (input) {
          var v = input.value.trim();
          if (v && options.indexOf(v) < 0) options.push(v);
        });
      }
      questions.push({
        id: "q" + (i + 1),
        label: label,
        type: type,
        options: options,
        required: el.querySelector(".q-req").checked,
        other: type !== "text" && !!el.querySelector(".q-other").checked,
        max: type === "multi" ? Number(el.querySelector(".q-max").value || 0) || 0 : 0
      });
    });
    return { questions: questions, show_results: !!($("#edShowResults") && $("#edShowResults").checked) };
  }

  async function saveEditor() {
    var editing = state.editing;
    var kind = editing ? editing.kind : state.newKind;
    var payload = {
      id: editing ? editing.id : 0,
      kind: kind,
      title: $("#edTitle").value.trim(),
      description: $("#edDesc").value.trim(),
      deadline: $("#edDeadline").value,
      max_per_ip: Number($("#edMaxIp").value || 0) || 0,
      allow_guest: $("#edGuest") ? !!$("#edGuest").checked : true
    };
    if (!payload.title) return toast("请填写标题", true);
    if (!editing) {
      payload.config = kind === "signup" ? readSports() : readQuestions();
      if (kind === "signup" && !payload.config.sports.length) {
        return toast("请至少添加一个报名项目", true);
      }
      if (kind !== "signup" && !payload.config.questions.length) {
        return toast("请至少添加一道填写完整的题目", true);
      }
    } else if (kind === "signup") {
      payload.sports = readCaps();
    }

    var btn = $("#btnSaveActivity");
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>保存中…';
    var res = await api("/api/admin/activity/save", { method: "POST", body: payload });
    btn.disabled = false;
    btn.textContent = editing ? "保存设置" : "发布" + KIND_NAME[kind];
    if (!res.ok) return toast(res.error || "保存失败", true);
    toast(editing ? "已保存" : "发布成功");
    state.editing = null;
    setView("list");
    loadActivities();
  }

  /* ---------------- 详情 ---------------- */
  async function openDetail(id) {
    var data = await api("/api/admin/activity?id=" + id);
    if (data.needLogin) return setView("login");
    if (!data.ok) return toast(data.error || "加载失败", true);
    state.detail = data;
    setView("detail");
    renderDetail();
  }

  function barRow(text, count, total, multi, subs) {
    var percent = total ? Math.round((count * 100) / total) : 0;
    var subHtml = "";
    if (subs && subs.length) {
      subHtml = '<div class="subs">' + subs.map(function (s) {
        return '<span class="sub">' + esc(s.text) + " ×" + s.count + "</span>";
      }).join("") + "</div>";
    }
    return (
      '<div class="bar-row' + (multi ? " multi" : "") + '">' +
      '<div class="bar-top"><span>' + esc(text) + "</span>" +
      '<span class="v">' + count + " 票 · " + percent + "%</span></div>" +
      '<div class="bar"><i style="width:' + percent + '%"></i></div>' +
      subHtml + "</div>"
    );
  }

  function renderDetail() {
    var data = state.detail;
    var a = data.activity;
    var results = data.results;
    var rows = data.responses || [];
    var html = "";

    html += '<div class="row-actions" style="margin-bottom:12px">' +
      '<button class="btn small" data-back="list">← 返回</button></div>';
    html += '<h1 style="font-size:1.25rem;">' + esc(a.title) +
      ' <span class="badge ' + a.status + '">' + esc(a.statusText) + "</span></h1>";
    html += '<div class="hint-line" style="margin:8px 0 16px">' +
      esc(a.kindName) + " · " + esc(deadlineText(a)) +
      (a.createdBy ? " · 发起人 " + esc(a.createdBy) : "") + " · </div>";

    html += '<div class="stat-row">' +
      '<div class="stat"><div class="n">' + results.total + '</div><div class="l">' +
      (a.kind === "signup" ? "报名人数" : "提交份数") + "</div></div>" +
      '<div class="stat"><div class="n">' + (a.maxPerIp || "∞") +
      '</div><div class="l">每 IP 上限</div></div>' +
      '<div class="stat"><div class="n" style="font-size:1rem;padding-top:6px">' +
      (a.closed ? "已结束" : a.deadline ? fmtTime(a.deadline) : "不限") +
      '</div><div class="l">截止时间</div></div></div>';

    html += '<div class="card"><div class="row-actions">' +
      '<a class="btn small primary" href="/api/admin/export?id=' + a.id + '&fmt=xlsx">导出 Excel</a>' +
      '<a class="btn small" href="/api/admin/export?id=' + a.id + '&fmt=csv">导出 CSV</a>' +
      '<a class="btn small" href="/a/' + a.id + '" target="_blank">以管理员身份参与</a>' +
      '<button class="btn small" data-act="edit" data-id="' + a.id + '">设置</button>' +
      '<button class="btn small" data-act="toggle" data-id="' + a.id + '">' +
      (a.closed ? "重新开放" : "结束活动") + "</button>" +
      '<button class="btn small danger" data-act="delete" data-id="' + a.id + '">删除活动</button>' +
      "</div></div>";

    /* 统计 */
    html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:14px">统计结果</h3>';
    if (results.kind === "signup") {
      results.sports.forEach(function (s) {
        html += barRow((s.icon ? s.icon + " " : "") + s.name + (s.capacity ? "（名额 " + s.capacity + "）" : ""),
          s.count, results.total, false);
      });
    } else {
      results.questions.forEach(function (q, i) {
        html += '<div style="margin-bottom:18px"><div style="font-size:.9rem;font-weight:600;margin-bottom:8px">' +
          (i + 1) + ". " + esc(q.label) + "</div>";
        if (q.type === "text") {
          if (!q.texts.length) {
            html += '<div class="hint-line">暂无填写</div>';
          } else {
            html += '<div class="text-list">' + q.texts.map(function (t) {
              return '<div class="text-item">' + esc(t.text) + "</div>";
            }).join("") + "</div>";
          }
        } else {
          q.options.forEach(function (opt) {
            html += barRow(opt.text, opt.count, q.total, q.type === "multi", opt.subs);
          });
        }
        html += "</div>";
      });
    }
    html += "</div>";

    /* 明细 */
    html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:14px">提交明细（' +
      rows.length + " 条" + (data.truncated ? "，仅显示前 1000 条" : "") + "）</h3>";
    if (!rows.length) {
      html += '<div class="hint-line">还没有人提交</div>';
    } else {
      html += '<div class="table-wrap"><table class="data"><thead><tr>';
      if (a.kind === "signup") {
        html += "<th>#</th><th>项目</th><th>学号</th><th>姓名</th><th>提交时间</th><th>IP</th><th>提交账号</th><th></th>";
      } else {
        html += "<th>#</th><th>提交时间</th><th>IP</th><th>提交账号</th>";
        (a.config.questions || []).forEach(function (q, i) {
          html += "<th>" + (i + 1) + ". " + esc(q.label) + "</th>";
        });
        html += "<th></th>";
      }
      html += "</tr></thead><tbody>";
      rows.forEach(function (r, i) {
        html += "<tr>";
        html += "<td>" + (i + 1) + "</td>";
        var who = r.isAdmin
          ? "管理员" + (r.adminName ? " " + r.adminName : "")
          : (r.userName || "访客");
        if (a.kind === "signup") {
          var d = r.data || {};
          var sport = (a.config.sports || []).filter(function (s) {
            return s.key === d.sport;
          })[0];
          html += "<td>" + esc(sport ? sport.name : d.sport) + "</td>";
          html += "<td>" + esc(d.student_id) + "</td>";
          html += "<td>" + esc(d.name) + "</td>";
          html += "<td>" + esc(fmtTime(r.createdAt || "", true)) + "</td>";
          html += '<td class="src">' + esc(srcText(r.ip)) + "</td>";
          html += "<td>" + esc(who) + (r.isAdmin ? ' <span class="badge kind">管理员</span>' : "") + "</td>";
        } else {          var answers = (r.data || {}).answers || {};
          html += "<td>" + esc(fmtTime(r.createdAt || "", true)) + "</td>";
          html += '<td class="src">' + esc(srcText(r.ip)) + "</td>";
          html += "<td>" + esc(who) + (r.isAdmin ? ' <span class="badge kind">管理员</span>' : "") + "</td>";
          (a.config.questions || []).forEach(function (q) {
            var v = answers[q.id];
            if (Array.isArray(v)) v = v.join("、");
            html += '<td class="wrap">' + esc(v || "") + "</td>";
          });
        }
        var bannedList = (data.bannedTargets || []);
        var isBanned = r.ip && bannedList.indexOf(r.ip) >= 0;
        html += '<td><button class="btn small danger" data-act="del-resp" data-id="' +
          r.id + '" data-aid="' + a.id + '">删</button> ';
        if (isBanned) {
          html += '<span class="badge kind">已拉黑</span>';
        } else if (r.ip) {
          html += '<button class="btn small" data-act="ban" data-target="' + esc(r.ip) +
            '" data-who="' + esc(srcText(r.ip)) + '">拉黑</button>';
        }
        html += "</td>";
        html += "</tr>";
      });
      html += "</tbody></table></div>";
    }
    html += "</div>";

    $("#detailView").innerHTML = html;
  }

  /* ---------------- 普通成员 ---------------- */
  async function openUsers(keyword) {
    var data = await api("/api/admin/users" + (keyword ? "?q=" + encodeURIComponent(keyword) : ""));
    if (data.needLogin) return setView("login");
    if (!data.ok) return toast(data.error || "加载失败", true);
    state.users = data.users || [];
    state.userKeyword = keyword || "";
    setView("users");
    renderUsers();
  }

  function renderUsers() {
    var list = state.users || [];
    var html = "";
    html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:12px">普通成员</h3>' +
      '<div class="row-actions" style="margin-bottom:12px">' +
      '<input id="userSearch" type="text" maxlength="20" placeholder="搜索用户名或昵称" ' +
      'style="max-width:220px" value="' + esc(state.userKeyword || "") + '">' +
      '<button class="btn small" id="btnUserSearch">搜索</button>' +
      '<button class="btn small primary" id="btnAddUser">新增成员</button>' +
      '<span class="hint-line" style="margin-left:auto">共 ' + list.length + " 人</span></div>";

    if (!list.length) {
      html += '<div class="empty">还没有普通成员注册<br>学生可以在首页点「登录 / 注册」自己建账号</div>';
    } else {
      html += '<div class="table-wrap"><table class="data"><thead><tr>' +
        "<th>用户名</th><th>昵称</th><th>提交次数</th><th>注册时间</th><th>最近登录</th><th>操作</th>" +
        "</tr></thead><tbody>";
      list.forEach(function (u) {
        html += "<tr><td>" + esc(u.username) +
          (u.createdByAdmin ? ' <span class="badge kind">代建</span>' : "") + "</td>";
        html += "<td>" + esc(u.nickname || "") + "</td>";
        html += "<td>" + (u.count || 0) + "</td>";
        html += "<td>" + esc(fmtTime(u.createdAt, true)) + "</td>";
        html += "<td>" + esc(u.lastLoginAt ? fmtTime(u.lastLoginAt, true) : "—") + "</td>";
        html += "<td>" +
          '<button class="btn small" data-act="user-pwd" data-id="' + u.id + '">重置密码</button> ' +
          '<button class="btn small danger" data-act="del-user" data-id="' + u.id +
          '" data-name="' + esc(u.username) + '">删除</button>' +
          "</td></tr>";
      });
      html += "</tbody></table></div>";
      html += '<div class="hint-line" style="margin-top:10px">删除账号不会删除他已提交的记录，记录里会保留当时的昵称。</div>';
    }
    html += "</div>";
    $("#tabUsers").innerHTML = html;
    var addBtn = $("#btnAddUser");
    if (addBtn) addBtn.onclick = openAddUser;
  }

  /* ---------------- 封禁名单 ---------------- */
  async function openBans() {
    var data = await api("/api/admin/bans");
    if (data.needLogin) return setView("login");
    if (!data.ok) return toast(data.error || "加载失败", true);
    state.bans = data.bans || [];
    setView("bans");
    renderBans();
  }

  function renderBans() {
    var list = state.bans || [];
    var html = "";
    html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:12px">封禁名单</h3>' +
      '<div class="hint-line" style="margin-bottom:12px">被拉黑的来源不能再提交活动和注册新账号（管理员账号不受影响）。' +
      '「设备」是浏览器 cookie，「IP」是真实网络地址——封 IP 可能连带同一 Wi-Fi 下的其他人。</div>';

    if (!list.length) {
      html += '<div class="empty">还没有封禁任何来源<br>在活动明细里可以对某一条记录点「拉黑」</div>';
    } else {
      html += '<div class="table-wrap"><table class="data"><thead><tr>' +
        "<th>来源</th><th>类型</th><th>原因</th><th>操作人</th><th>时间</th><th>操作</th>" +
        "</tr></thead><tbody>";
      list.forEach(function (b) {
        html += '<tr><td class="src">' + esc(srcText(b.target)) + "</td>";
        html += "<td>" + (b.kind === "device" ? "设备" : "IP") + "</td>";
        html += '<td class="wrap">' + esc(b.reason || "—") + "</td>";
        html += "<td>" + esc(b.createdBy || "—") + "</td>";
        html += "<td>" + esc(fmtTime(b.createdAt, true)) + "</td>";
        html += '<td><button class="btn small" data-act="unban" data-id="' + b.id +
          '" data-target="' + esc(b.target) + '">解除</button></td></tr>';
      });
      html += "</tbody></table></div>";
    }
    html += "</div>";
    $("#tabBans").innerHTML = html;
  }

  /* ---------------- 公告 ---------------- */
  async function openNotices() {
    var data = await api("/api/admin/notices");
    if (data.needLogin) return setView("login");
    if (!data.ok) return toast(data.error || "加载失败", true);
    state.notices = data.notices || [];
    setView("notices");
    renderNotices();
  }

  function renderNotices() {
    var list = state.notices || [];
    var html =
      '<div class="card"><h3 style="font-size:.98rem;margin-bottom:12px">公告</h3>' +
      '<div class="row-actions" style="margin-bottom:12px">' +
      '<button class="btn small primary" data-act="notice-new">＋ 发布公告</button>' +
      '<span class="hint-line" style="margin-left:auto">置顶的排在首页最前，隐藏的不会显示</span></div>';

    if (!list.length) {
      html += '<div class="empty">还没有公告<br>发布一条，首页顶部就会显示出来</div>';
    } else {
      html +=
        '<div class="table-wrap"><table class="data"><thead><tr>' +
        "<th>标题</th><th>状态</th><th>发布人</th><th>发布时间</th><th>操作</th>" +
        "</tr></thead><tbody>";
      list.forEach(function (n) {
        html += "<tr><td>" + esc(n.title) +
          (n.pinned ? ' <span class="badge kind">置顶</span>' : "") + "</td>";
        html += "<td>" +
          (n.hidden ? '<span class="badge closed">已隐藏</span>'
                    : '<span class="badge open">显示中</span>') + "</td>";
        html += "<td>" + esc(n.createdBy || "—") + "</td>";
        html += "<td>" + esc(fmtTime(n.createdAt, true)) + "</td>";
        html += '<td><button class="btn small" data-act="notice-edit" data-id="' + n.id +
          '">编辑</button> <button class="btn small danger" data-act="notice-del" data-id="' +
          n.id + '">删除</button></td></tr>';
      });
      html += "</tbody></table></div>";
    }
    html += "</div>";
    $("#tabNotices").innerHTML = html;
  }

  function closeNoticeEditor() {
    var el = document.getElementById("noticeMask");
    if (el) el.style.display = "none";
  }

  function openNoticeEditor(notice) {
    var el = document.getElementById("noticeMask");
    if (!el) {
      el = document.createElement("div");
      el.className = "mask";
      el.id = "noticeMask";
      document.body.appendChild(el);
    }
    el.innerHTML =
      '<div class="auth-card" style="max-width:560px">' +
      '<button type="button" class="auth-close" aria-label="关闭">✕</button>' +
      "<h2>" + (notice ? "编辑公告" : "发布公告") + "</h2>" +
      '<p class="auth-tip">公告会显示在首页顶部，同学们一进来就能看到。</p>' +
      '<div class="field"><label for="ntTitle">标题</label>' +
      '<input id="ntTitle" type="text" maxlength="100" placeholder="例如：本周值日安排"></div>' +
      '<div class="field"><label for="ntContent">内容</label>' +
      '<textarea id="ntContent" rows="7" maxlength="5000" placeholder="可以分多行写，换行会原样显示"></textarea></div>' +
      '<label style="display:flex;gap:8px;align-items:center;margin-bottom:10px;font-size:.92rem">' +
      '<input type="checkbox" id="ntPinned"> 置顶（排在首页最前面）</label>' +
      (notice
        ? '<label style="display:flex;gap:8px;align-items:center;margin-bottom:16px;font-size:.92rem">' +
          '<input type="checkbox" id="ntHidden"> 暂时隐藏（首页不显示，但保留这条）</label>'
        : "") +
      '<div class="row-actions"><button class="btn primary" id="btnSaveNotice">保存</button>' +
      '<button class="btn ghost" id="btnCancelNotice">取消</button></div></div>';
    el.style.display = "flex";
    el.dataset.id = notice ? String(notice.id) : "";
    $("#ntTitle").value = notice ? notice.title : "";
    $("#ntContent").value = notice ? notice.content : "";
    $("#ntPinned").checked = notice ? !!notice.pinned : false;
    if (notice && $("#ntHidden")) $("#ntHidden").checked = !!notice.hidden;
    $("#btnSaveNotice").onclick = saveNotice;
    $("#btnCancelNotice").onclick = closeNoticeEditor;
    $$(".auth-close", el).forEach(function (b) { b.onclick = closeNoticeEditor; });
    el.onclick = function (e) { if (e.target === el) closeNoticeEditor(); };
    $("#ntTitle").focus();
  }

  async function saveNotice() {
    var el = document.getElementById("noticeMask");
    var id = el && el.dataset.id ? Number(el.dataset.id) : 0;
    var payload = {
      title: $("#ntTitle").value.trim(),
      content: $("#ntContent").value,
      pinned: !!$("#ntPinned").checked
    };
    if (id) {
      payload.id = id;
      if ($("#ntHidden")) payload.hidden = !!$("#ntHidden").checked;
    }
    var data = await api("/api/admin/notice/save", { method: "POST", body: payload });
    if (data.needLogin) return setView("login");
    if (!data.ok) return toast(data.error || "保存失败", true);
    toast(data.message || "已保存");
    closeNoticeEditor();
    openNotices();
  }

  /* 管理员代建普通成员：不受「每个网络每周 1 个」的限制 */
  function openAddUser() {
    var el = document.getElementById("addUserMask");
    if (!el) {
      el = document.createElement("div");
      el.className = "mask";
      el.id = "addUserMask";
      el.innerHTML =
        '<div class="auth-card">' +
        '<button type="button" class="auth-close" aria-label="关闭">✕</button>' +
        "<h2>新增普通成员</h2>" +
        '<p class="auth-tip">管理员代建不受「每个网络每周 1 个」的限制，可以一次建多个。</p>' +
        '<label class="field auth-field"><span class="field-label">用户名（2-20 位字母、数字或中文）</span>' +
        '<input id="nuUser" maxlength="20" placeholder="例如：20230412 或 李四"></label>' +
        '<label class="field auth-field"><span class="field-label">名字（2-20 字，选填）</span>' +
        '<input id="nuNick" maxlength="20" placeholder="显示在提交记录里，不填就用用户名"></label>' +
        '<label class="field auth-field"><span class="field-label">初始密码（至少 6 位）</span>' +
        '<input id="nuPass" type="password" maxlength="64"></label>' +
        '<div class="notice err" id="nuErr" style="display:none"></div>' +
        '<button type="button" class="btn primary block" id="nuSubmit">创建</button>' +
        "</div>";
      document.body.appendChild(el);
      el.querySelector(".auth-close").onclick = function () { el.style.display = "none"; };
      el.onclick = function (e) { if (e.target === el) el.style.display = "none"; };
      el.querySelector("#nuSubmit").onclick = submitAddUser;
    }
    el.style.display = "flex";
    el.querySelector("#nuUser").value = "";
    el.querySelector("#nuNick").value = "";
    el.querySelector("#nuPass").value = "";
    el.querySelector("#nuErr").style.display = "none";
    setTimeout(function () { el.querySelector("#nuUser").focus(); }, 30);
  }

  async function submitAddUser() {
    var el = document.getElementById("addUserMask");
    var errBox = el.querySelector("#nuErr");
    errBox.style.display = "none";
    function fail(msg) { errBox.textContent = msg; errBox.style.display = ""; }
    var username = el.querySelector("#nuUser").value.trim();
    var nickname = el.querySelector("#nuNick").value.trim();
    var password = el.querySelector("#nuPass").value;
    if (!username) return fail("请填写用户名");
    if (!password) return fail("请填写初始密码");
    if (password.length < 6) return fail("初始密码至少 6 位");
    var btn = el.querySelector("#nuSubmit");
    btn.disabled = true;
    var res = await api("/api/admin/users/create", {
      method: "POST",
      body: { username: username, nickname: nickname, password: password }
    });
    btn.disabled = false;
    if (!res.ok) return fail(res.error || "创建失败");
    toast("成员已创建");
    el.style.display = "none";
    openUsers(state.userKeyword || "");
  }

  async function resetUserPassword(id) {
    var pwd = window.prompt("给这个成员设置新密码（至少 6 位）");
    if (pwd === null) return;
    if (!pwd) return toast("密码不能为空", true);
    var res = await api("/api/admin/users/password", { method: "POST", body: { id: id, password: pwd } });
    if (!res.ok) return toast(res.error || "重置失败", true);
    toast("密码已重置");
  }

  /* ---------------- 管理员账号 ---------------- */
  async function openAdmins() {
    var data = await api("/api/admin/admins");
    if (data.needLogin) return setView("login");
    if (!data.ok) return toast(data.error || "加载失败", true);
    state.admins = data.admins || [];
    state.me = data.me || state.me;
    setView("admins");
    renderAdmins();
  }

  function renderAdmins() {
    var me = state.me;
    var html = "";

    if (me.isSuper) {
      html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:14px">新增管理员</h3>' +
        '<div class="field"><label for="newAdminUser">用户名</label>' +
        '<input id="newAdminUser" type="text" maxlength="20" placeholder="2-20 个字符"></div>' +
        '<div class="field"><label for="newAdminPass">初始密码</label>' +
        '<input id="newAdminPass" type="password" maxlength="64" placeholder="至少 4 位"></div>' +
        '<label class="hint-line" style="display:flex;align-items:center;gap:8px;margin-bottom:14px">' +
        '<input type="checkbox" id="newAdminSuper" style="width:auto"> 设为高级管理员（可以管理其他账号）</label>' +
        '<button class="btn primary small" id="btnAddAdmin">添加管理员</button></div>';
    } else {
      html += '<div class="notice info">你是一般管理员，只能修改自己的密码；' +
        "新增 / 删除账号需要高级管理员操作。</div>";
    }

    html += '<div class="card"><h3 style="font-size:.98rem;margin-bottom:12px">账号列表</h3>' +
      '<div class="table-wrap"><table class="data"><thead><tr>' +
      "<th>用户名</th><th>类型</th><th>创建时间</th><th>操作</th></tr></thead><tbody>";
    state.admins.forEach(function (a) {
      var self = me && a.id === me.id;
      html += "<tr><td>" + esc(a.username) + (self ? "（我）" : "") + "</td>";
      html += "<td>" + (a.isSuper ? '<span class="badge kind">高级</span>' : "普通") + "</td>";
      html += "<td>" + esc(fmtTime(a.createdAt, true)) + "</td>";
      html += "<td>";
      if (self || me.isSuper) {
        html += '<button class="btn small" data-act="pwd" data-id="' + a.id + '">改密码</button> ';
      }
      if (me.isSuper && !self) {
        html += '<button class="btn small danger" data-act="del-admin" data-id="' + a.id + '">删除</button>';
      }
      html += "</td></tr>";
    });
    html += "</tbody></table></div></div>";

    $("#tabAdmins").innerHTML = html;
  }

  async function addAdmin() {
    var username = $("#newAdminUser").value.trim();
    var password = $("#newAdminPass").value;
    var isSuper = $("#newAdminSuper").checked;
    if (!username || !password) return toast("请填写用户名和密码", true);
    var res = await api("/api/admin/admins/save", {
      method: "POST",
      body: { username: username, password: password, is_super: isSuper }
    });
    if (!res.ok) return toast(res.error || "添加失败", true);
    toast("已添加管理员");
    openAdmins();
  }

  async function changePassword(id) {
    var pwd = window.prompt("输入新密码（至少 4 位）");
    if (pwd === null) return;
    if (!pwd) return toast("密码不能为空", true);
    var res = await api("/api/admin/admins/save", {
      method: "POST",
      body: { id: id, password: pwd }
    });
    if (!res.ok) return toast(res.error || "修改失败", true);
    toast("密码已更新");
  }

  /* ---------------- 事件 ---------------- */
  document.addEventListener("click", async function (e) {
    var target = e.target instanceof Element ? e.target : null;
    if (!target) return;

    if (target.id === "btnLogin") return doLogin();

    if (target.id === "btnLogout") {
      await api("/api/admin/logout", { method: "POST", body: {} });
      state.me = null;
      setView("login");
      return;
    }

    var segBtn = target.closest("#seg button");
    if (segBtn) {
      $$("#seg button").forEach(function (b) {
        b.classList.toggle("active", b === segBtn);
      });
      if (segBtn.dataset.tab === "admins") openAdmins();
      else if (segBtn.dataset.tab === "users") openUsers();
      else if (segBtn.dataset.tab === "notices") openNotices();
      else if (segBtn.dataset.tab === "bans") openBans();
      else {
        setView("list");
        loadActivities();
      }
      return;
    }

    var newBtn = target.closest("[data-new]");
    if (newBtn) return openEditor(null, newBtn.dataset.new);

    var back = target.closest("[data-back]");
    if (back) {
      setView("list");
      loadActivities();
      return;
    }

    if (target.id === "btnSaveActivity") return saveEditor();
    if (target.id === "btnAddQ") {
      $("#qList").appendChild(buildQuestionEl(null));
      renumber();
      return;
    }
    if (target.id === "btnAddSport") {
      $("#sportList").appendChild(buildSportEl(null));
      return;
    }
    if (target.id === "btnAddAdmin") return addAdmin();
    if (target.id === "btnUserSearch") return openUsers($("#userSearch").value.trim());

    var x = target.closest("[data-x]");
    if (x) {
      var action = x.dataset.x;
      if (action === "del-q") {
        if ($$("#qList .q-item").length <= 1) return toast("至少保留一道题目", true);
        x.closest(".q-item").remove();
        renumber();
      } else if (action === "add-opt") {
        var optWrap = x.closest(".q-options");
        optWrap.insertBefore(buildOptionEl(""), x);
      } else if (action === "del-opt") {
        var box = x.closest(".q-options");
        if ($$(".opt-row", box).length <= 2) return toast("每题至少保留两个选项", true);
        x.closest(".opt-row").remove();
      } else if (action === "del-sport") {
        if ($$("#sportList .q-item").length <= 1) return toast("至少保留一个项目", true);
        x.closest(".q-item").remove();
      }
      return;
    }

    var actBtn = target.closest("[data-act]");
    if (actBtn) {
      var act = actBtn.dataset.act;
      var id = Number(actBtn.dataset.id);
      if (act === "detail") return openDetail(id);
      if (act === "edit") {
        var found = state.activities.filter(function (a) { return a.id === id; })[0];
        if (state.detail && state.detail.activity.id === id) found = state.detail.activity;
        if (found) openEditor(found, null);
        return;
      }
      if (act === "export") {
        window.location.href = "/api/admin/export?id=" + id + "&fmt=xlsx";
        return;
      }
      if (act === "toggle") {
        var current = state.activities.filter(function (a) { return a.id === id; })[0];
        if (state.detail && state.detail.activity.id === id) current = state.detail.activity;
        var closed = current ? !current.closed : true;
        var res = await api("/api/admin/activity/close", {
          method: "POST",
          body: { id: id, closed: closed }
        });
        if (!res.ok) return toast(res.error || "操作失败", true);
        toast(res.message);
        if (state.view === "detail") openDetail(id);
        else loadActivities();
        return;
      }
      if (act === "delete") {
        if (!window.confirm("确定删除这个活动吗？所有提交记录也会一起删除，且无法恢复。")) return;
        var r = await api("/api/admin/activity/delete", { method: "POST", body: { id: id } });
        if (!r.ok) return toast(r.error || "删除失败", true);
        toast("已删除");
        setView("list");
        loadActivities();
        return;
      }
      if (act === "del-resp") {
        if (!window.confirm("删除这条提交记录？")) return;
        var aid = Number(actBtn.dataset.aid);
        var rr = await api("/api/admin/response/delete", { method: "POST", body: { id: id } });
        if (!rr.ok) return toast(rr.error || "删除失败", true);
        toast("已删除");
        openDetail(aid);
        return;
      }
      if (act === "pwd") return changePassword(id);
      if (act === "user-pwd") return resetUserPassword(id);
      if (act === "ban") {
        var tgt = actBtn.dataset.target || "";
        var who = actBtn.dataset.who || tgt;
        var why = window.prompt(
          "封禁来源「" + who + "」？\n封禁后它不能再提交活动和注册新账号。\n可以顺带写个原因（可留空）：",
          ""
        );
        if (why === null) return;
        var rb = await api("/api/admin/bans/add", {
          method: "POST",
          body: { target: tgt, reason: why }
        });
        if (!rb.ok) return toast(rb.error || "封禁失败", true);
        toast("已封禁");
        if (state.view === "detail" && state.detail) openDetail(state.detail.activity.id);
        return;
      }
      if (act === "unban") {
        var tb = actBtn.dataset.target || "";
        if (!window.confirm("解除对「" + srcText(tb) + "」的封禁？解除后它可以重新提交。")) return;
        var rd2 = await api("/api/admin/bans/delete", { method: "POST", body: { id: id } });
        if (!rd2.ok) return toast(rd2.error || "解除失败", true);
        toast("已解除");
        openBans();
        return;
      }
      if (act === "del-user") {
        var uname = actBtn.dataset.name || "";
        if (!window.confirm("删除普通成员「" + uname + "」？删掉后他不能再登录，已提交的记录会保留。")) return;
        var ru = await api("/api/admin/users/delete", { method: "POST", body: { id: id } });
        if (!ru.ok) return toast(ru.error || "删除失败", true);
        toast("已删除");
        openUsers(state.userKeyword || "");
        return;
      }
      if (act === "del-admin") {
        if (!window.confirm("删除这个管理员账号？")) return;
        var rd = await api("/api/admin/admins/delete", { method: "POST", body: { id: id } });
        if (!rd.ok) return toast(rd.error || "删除失败", true);
        toast("已删除");
        openAdmins();
        return;
      }
      if (act === "notice-new") return openNoticeEditor(null);
      if (act === "notice-edit") {
        var nt = (state.notices || []).filter(function (n) { return n.id === id; })[0];
        if (!nt) return toast("这条公告不存在", true);
        return openNoticeEditor(nt);
      }
      if (act === "notice-del") {
        if (!window.confirm("删除这条公告？删掉后首页就不再显示。")) return;
        var rn = await api("/api/admin/notice/delete", { method: "POST", body: { id: id } });
        if (!rn.ok) return toast(rn.error || "删除失败", true);
        toast("公告已删除");
        openNotices();
        return;
      }
    }
  });

  document.addEventListener("change", function (e) {
    var target = e.target instanceof Element ? e.target : null;
    if (!target) return;
    if (target.classList.contains("q-type")) {
      syncQuestionType(target.closest(".q-item"));
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var id = e.target && e.target.id;
    if (id === "loginUser") {
      e.preventDefault();
      $("#loginPass").focus();
    } else if (id === "loginPass") {
      e.preventDefault();
      doLogin();
    }
  });

  boot();
})();

/* 首页：活动列表 + 分类筛选 */
(function () {
  "use strict";

  var state = { filter: "all", activities: [], counts: {}, notices: [], noticeOpen: false };

  function renderTabs() {
    var tabs = [
      { key: "all", label: "全部" },
      { key: "signup", label: "报名" },
      { key: "survey", label: "问卷" },
      { key: "poll", label: "投票" }
    ];
    $("#tabs").innerHTML = tabs
      .map(function (t) {
        var n = state.counts[t.key] || 0;
        return (
          '<button class="tab' + (state.filter === t.key ? " active" : "") +
          '" data-key="' + t.key + '">' + t.label +
          (n ? '<span class="n">' + n + "</span>" : "") + "</button>"
        );
      })
      .join("");
    $$("#tabs .tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.filter = btn.dataset.key;
        renderTabs();
        renderList();
      });
    });
  }

  function cardHtml(act) {
    var badgeClass = act.status;
    var meta = [];
    meta.push(
      '<span>' + esc(act.kindName) + "</span>"
    );
    meta.push('<span aria-label="截止时间">' + esc(deadlineText(act)) + "</span>");
    if (act.status === "open" && act.deadline) {
      var remain = remainText(act.deadline);
      if (remain) meta.push("<span>" + esc(remain) + "</span>");
    }
    meta.push("<span>" + esc(act.hint) + "</span>");
    if (act.maxPerIp > 0) {
      meta.push('<span aria-label="次数限制">' + esc(act.limitText || "") + "</span>");
    }
    if (!act.allowGuest) {
      meta.push("<span>仅限登录</span>");
    }

    var button;
    if (act.needLogin) {
      button = '<a class="btn primary" href="/a/' + act.id + '">登录后填写</a>';
    } else if (act.canSubmit) {
      button = '<a class="btn primary" href="/a/' + act.id + '">去填写</a>';
    } else if (act.status === "open") {
      button = '<span class="btn" style="opacity:.55">提交次数已用完</span>';
    } else if (act.status === "ended") {
      button = '<a class="btn" href="/a/' + act.id + '">已截止 · 查看结果</a>';
    } else {
      button = '<a class="btn" href="/a/' + act.id + '">已结束 · 查看结果</a>';
    }

    var hint = "";
    if (act.status === "open" && act.remaining !== null && act.remaining !== undefined) {
      hint = act.remaining > 0 ? "还可以提交 " + act.remaining + " 次" : "提交次数已用完";
    }

    return (
      '<article class="card">' +
      '<div class="act-head">' +
      '<div class="act-title">' + esc(act.title) + "</div>" +
      '<span class="badge ' + badgeClass + '">' + esc(act.statusText) + "</span>" +
      "</div>" +
      (act.description
        ? '<div class="act-desc">' + esc(act.description) + "</div>"
        : "") +
      '<div class="act-meta">' + meta.join("") + "</div>" +
      '<div class="act-foot">' + button +
      '<span class="hint">' + esc(hint) + "</span>" +
      "</div></article>"
    );
  }

  function renderList() {
    var list = state.activities.filter(function (a) {
      return state.filter === "all" || a.kind === state.filter;
    });
    if (!list.length) {
      $("#list").innerHTML =
        '<div class="empty">' +
        (state.filter === "all"
          ? "还没有可以参与的活动<br>管理员登录后台就能发起报名、问卷和投票"
          : "这个分类下暂时没有活动") +
        "</div>";
      return;
    }
    $("#list").innerHTML = list.map(cardHtml).join("");
  }

  function renderNotices() {
    var box = $("#notices");
    var list = state.notices || [];
    if (!list.length) {
      box.innerHTML = "";
      return;
    }
    var pinned = list.filter(function (n) { return n.pinned; });
    var rest = list.filter(function (n) { return !n.pinned; });
    var shown = state.noticeOpen ? rest : rest.slice(0, 2);

    function one(n) {
      return (
        '<div class="notice-card' + (n.pinned ? " pinned" : "") + '">' +
        '<div class="notice-head">' +
        (n.pinned ? '<span class="badge kind">置顶</span>' : "") +
        "<b>" + esc(n.title) + "</b>" +
        '<span class="notice-time">' + esc(fmtTime(n.createdAt, true)) + "</span>" +
        "</div>" +
        '<div class="notice-body">' + esc(n.content) + "</div>" +
        "</div>"
      );
    }

    var html = pinned.map(one).join("") + shown.map(one).join("");
    if (rest.length > shown.length) {
      html +=
        '<button class="btn small ghost notice-more" id="btnMoreNotice">查看其余 ' +
        (rest.length - shown.length) + " 条公告</button>";
    }
    box.innerHTML = html;
    var more = $("#btnMoreNotice");
    if (more) {
      more.onclick = function () {
        state.noticeOpen = true;
        renderNotices();
      };
    }
  }

  async function load() {
    var data = await api("/api/home");
    if (!data.ok) {
      $("#list").innerHTML =
        '<div class="empty">' +
        esc(data.error || "加载失败") + "</div>";
      return;
    }
    state.activities = data.activities || [];
    state.counts = data.counts || {};
    state.notices = data.notices || [];
    if (data.site) {
      if (data.site.title) {
        $("#siteTitle").textContent = data.site.title;
        document.title = data.site.title;
      }
      if (data.site.subtitle) $("#siteSubtitle").textContent = data.site.subtitle;
    }
    if (data.user) Auth.set(data.user);
    else Auth.set(null);
    if (data.admin && data.admin.loggedIn) {
      $("#authBar").innerHTML =
        '<span class="who">已登录 <b>' + esc(data.admin.username) + "</b> · 参与不计次数</span>";
    } else {
      renderAuthBar(data.admin);
    }
    renderTabs();
    renderNotices();
    renderList();
  }

  function renderAuthBar(admin) {
    var box = $("#authBar");
    box.innerHTML = Auth.barHtml(admin);
    Auth.bindBar(box, admin, load);
  }

  load();
  // 回到页面时刷新，看到最新人数
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) load();
  });
})();

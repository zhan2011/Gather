/* 活动填写页：报名 / 问卷 / 投票共用 */
(function () {
  "use strict";

  var match = /^\/a\/(\d+)/.exec(location.pathname);
  var activityId = match ? match[1] : new URLSearchParams(location.search).get("id");
  var state = { data: null, submitted: false, busy: false };

  function countMap(results) {
    var map = {};
    if (results && results.sports) {
      results.sports.forEach(function (s) {
        map[s.key] = s;
      });
    }
    return map;
  }

  /* ---------------- 表单 ---------------- */
  function formHtml(activity, results) {
    var cfg = activity.config || {};
    var html = "";

    if (activity.kind === "signup") {
      var counts = countMap(results);
      var sports = cfg.sports || [];
      html += '<div class="field"><span class="field-label">选择项目<span class="req">*</span></span>';
      html += '<div class="choices grid">';
      sports.forEach(function (s, i) {
        var stat = counts[s.key] || { count: 0, capacity: s.capacity || 0 };
        var cap = s.capacity || 0;
        var full = cap > 0 && stat.count >= cap;
        html +=
          '<label class="choice' + (full ? " disabled" : "") + '">' +
          '<input type="radio" name="sport" value="' + esc(s.key) + '"' +
          (full ? " disabled" : "") + (i === 0 && !full ? "" : "") + ">" +
          (s.icon ? '<span class="icon">' + esc(s.icon) + "</span>" : "") +
          '<span>' + esc(s.name) + "</span>" +
          '<span class="cap">' + (cap > 0 ? stat.count + "/" + cap + " 人" : stat.count + " 人") +
          (full ? " · 已满" : "") +
          "</span></label>";
      });
      html += "</div></div>";
      html +=
        '<div class="field"><label for="f_sid">学号<span class="req">*</span></label>' +
        '<input id="f_sid" name="student_id" type="text" inputmode="text" ' +
        'placeholder="2-20 位字母或数字" maxlength="20" autocomplete="off"></div>';
      html +=
        '<div class="field"><label for="f_name">姓名<span class="req">*</span></label>' +
        '<input id="f_name" name="name" type="text" placeholder="请输入真实姓名（2-4 个字）" ' +
        'maxlength="4" autocomplete="off"></div>';
      return html;
    }

    (cfg.questions || []).forEach(function (q, index) {
      html += '<div class="field"><span class="field-label">' +
        (index + 1) + ". " + esc(q.label) +
        (q.required ? '<span class="req">*</span>' : "") +
        (q.type === "multi" && q.max > 0
          ? '<span class="q-hint" data-q="' + esc(q.id) + '">最多选 ' + q.max + " 项</span>"
          : "") +
        "</span>";
      if (q.type === "text") {
        html += '<textarea id="q_' + esc(q.id) + '" placeholder="请输入你的回答" maxlength="500"></textarea>';
      } else {
        var inputType = q.type === "multi" ? "checkbox" : "radio";
        html += '<div class="choices">';
        q.options.forEach(function (opt) {
          html +=
            '<label class="choice"><input type="' + inputType + '" name="q_' + esc(q.id) +
            '" value="' + esc(opt) + '"><span>' + esc(opt) + "</span></label>";
        });
        if (q.other) {
          html +=
            '<label class="choice"><input type="' + inputType + '" name="q_' + esc(q.id) +
            '" value="__other__"><span>其他</span></label>';
        }
        html += "</div>";
        if (q.other) {
          html +=
            '<input class="other-input" data-q="' + esc(q.id) + '" type="text" maxlength="100" ' +
            'placeholder="选「其他」时，在这里自己填写" autocomplete="off">';
        }
      }
      html += "</div>";
    });
    return html;
  }

  function collectPayload() {
    var activity = state.data.activity;
    if (activity.kind === "signup") {
      var picked = document.querySelector('input[name="sport"]:checked');
      return {
        id: activity.id,
        sport: picked ? picked.value : "",
        student_id: ($("#f_sid") || {}).value || "",
        name: ($("#f_name") || {}).value || ""
      };
    }
    var answers = {};
    (activity.config.questions || []).forEach(function (q) {
      if (q.type === "text") {
        var el = document.getElementById("q_" + q.id);
        answers[q.id] = el ? el.value : "";
      } else if (q.type === "single") {
        var sel = document.querySelector('input[name="q_' + q.id + '"]:checked');
        var one = sel ? sel.value : "";
        if (one === "__other__") one = otherText(q.id);
        answers[q.id] = one;
      } else {
        var list = $$('input[name="q_' + q.id + '"]:checked').map(function (x) {
          return x.value;
        });
        var custom = list.indexOf("__other__") >= 0 ? otherText(q.id) : "";
        list = list.filter(function (v) {
          return v !== "__other__";
        });
        if (custom) list.push(custom);
        answers[q.id] = list;
      }
    });
    return { id: activity.id, answers: answers };
  }

  /* ---------------- 结果 ---------------- */
  function otherText(qid) {
    var el = document.querySelector('.other-input[data-q="' + qid + '"]');
    return el ? el.value.trim() : "";
  }

  function findQuestion(qid) {
    var act = state.data && state.data.activity;
    var list = (act && act.config && act.config.questions) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === qid) return list[i];
    }
    return null;
  }

  // 多选题勾满上限后，其余选项先禁用，取消一个再放开
  function syncMultiLimit(qid) {
    var q = findQuestion(qid);
    if (!q || q.type !== "multi" || !q.max) return;
    var boxes = $$('input[name="q_' + qid + '"]');
    var checked = boxes.filter(function (x) {
      return x.checked;
    });
    boxes.forEach(function (x) {
      var locked = !x.checked && checked.length >= q.max;
      x.disabled = locked;
      var label = x.closest("label");
      if (label) label.classList.toggle("disabled", locked);
    });
    var hint = document.querySelector('.q-hint[data-q="' + qid + '"]');
    if (hint) {
      hint.textContent = "最多选 " + q.max + " 项（已选 " + checked.length + "）";
    }
  }
  function barRow(item, total, multi) {
    var percent = total ? Math.round((item.count * 100) / total) : 0;
    var subHtml = "";
    if (item.subs && item.subs.length) {
      subHtml = '<div class="subs">' + item.subs.map(function (s) {
        return '<span class="sub">' + esc(s.text) + " ×" + s.count + "</span>";
      }).join("") + "</div>";
    }
    return (
      '<div class="bar-row' + (multi ? " multi" : "") + '">' +
      '<div class="bar-top"><span>' + esc(item.text) + "</span>" +
      '<span class="v">' + item.count + " 票 · " + percent + "%</span></div>" +
      '<div class="bar"><i style="width:' + percent + '%"></i></div>' +
      subHtml + "</div>"
    );
  }

  function resultsHtml(results, activity) {
    if (!results) return "";
    var html = '<section class="card result">';
    if (results.kind === "signup") {
      html += "<h3>报名情况</h3>";
      html +=
        '<div class="stat-row"><div class="stat"><div class="n">' + results.total +
        '</div><div class="l">已报名人数</div></div></div>';
      results.sports.forEach(function (s) {
        var percent = results.total ? Math.round((s.count * 100) / results.total) : 0;
        var cap = s.capacity > 0 ? "（名额 " + s.capacity + "）" : "";
        html +=
          '<div class="bar-row">' +
          '<div class="bar-top"><span>' + (s.icon ? esc(s.icon) + " " : "") + esc(s.name) + esc(cap) +
          "</span><span class=\"v\">" + s.count + " 人</span></div>" +
          '<div class="bar"><i style="width:' + percent + '%"></i></div></div>';
      });
      html += "</section>";
      return html;
    }

    (results.questions || []).forEach(function (q, index) {
      html += '<div style="margin-bottom:18px"><h3>' + (index + 1) + ". " + esc(q.label) + "</h3>";
      html += '<div class="hint-line" style="margin:2px 0 10px">' + q.total + " 人作答</div>";
      if (q.type === "text") {
        if (!q.texts.length) {
          html += '<div class="hint-line">还没有人填写</div>';
        } else {
          html += '<div class="text-list">';
          q.texts.forEach(function (t) {
            html += '<div class="text-item">' + esc(t.text) +
              '<div class="t">' + esc(fmtTime(t.createdAt, true)) + "</div></div>";
          });
          html += "</div>";
        }
      } else {
        q.options.forEach(function (opt) {
          html += barRow(opt, q.total, q.type === "multi");
        });
      }
      html += "</div>";
    });
    html += "</section>";
    return html;
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    var data = state.data;
    var activity = data.activity;
    var card = data.card;
    var notices = [];

    $("#loading").style.display = "none";
    document.title = activity.title;
    $("#actTitle").textContent = activity.title;
    $("#actDesc").textContent = activity.description || "";

    if (card.status === "ended") {
      notices.push('<div class="notice warn">该活动已于 ' +
        esc(fmtTime(activity.deadline)) + " 截止，不再接受提交。</div>");
    } else if (card.status === "closed") {
      notices.push('<div class="notice info">管理员已结束该活动，不再接受提交。</div>');
    } else if (card.needLogin) {
      notices.push('<div class="notice warn">本活动仅限登录后的账号填写，' +
        "请先登录或注册一个账号。</div>");
    } else if (!card.canSubmit) {
      notices.push('<div class="notice warn">该' + (data.user ? "账号" : "网络") +
        "已提交 " + data.used + " 次，达到上限（" +
        card.maxPerIp + " 次）。如需再次提交请联系管理员。</div>");
    }

    if (card.needLogin) {
      notices.push('<div class="card flat"><div class="row-actions">' +
        '<button type="button" class="btn primary" id="btnAuth">登录 / 注册</button>' +
        "</div></div>");
    }

    if (data.isAdmin && card.canSubmit) {
      notices.push('<div class="notice info">你正以管理员 <b>' + esc(data.adminName) +
        "</b> 的身份填写，提交不会占用次数限制。</div>");
    } else if (data.user && card.canSubmit) {
      notices.push('<div class="notice info">正在以 <b>' +
        esc(data.user.nickname || data.user.username) + "</b> 的身份填写，次数按账号计算" +
        (card.remaining !== null && card.remaining !== undefined
          ? "，这个账号还可以提交 <b>" + card.remaining + "</b> 次" : "") + "。</div>");
    } else if (card.canSubmit && card.remaining !== null && card.remaining !== undefined) {
      notices.push('<div class="notice info">这个网络还可以提交 <b>' + card.remaining + "</b> 次。</div>");
    }

    if (state.submitted) {
      notices.unshift('<div class="notice ok">提交成功！' +
        (data.resultsVisible ? "" : "感谢参与。") + "</div>");
    }

    $("#notices").innerHTML = notices.join("");

    var showForm = card.canSubmit && !state.submitted;
    var form = $("#form");
    if (showForm) {
      form.innerHTML = formHtml(activity, data.results);
      form.style.display = "";
      $("#submitBar").style.display = "";
      var hint = activity.deadline
        ? "截止时间 " + fmtTime(activity.deadline, true)
        : "不设截止时间";
      $("#submitHint").textContent = hint;
    } else {
      form.style.display = "none";
      $("#submitBar").style.display = "none";
    }

    $("#results").innerHTML = data.resultsVisible
      ? resultsHtml(data.results, activity)
      : "";

    if (state.submitted) {
      var again = document.createElement("div");
      again.className = "card flat";
      again.innerHTML =
        '<div class="row-actions"><a class="btn block" href="/">返回活动列表</a></div>';
      $("#results").appendChild(again);
    }

    renderAuthBar();
  }

  function renderAuthBar() {
    var box = $("#authBar");
    if (!box) return;
    box.innerHTML = Auth.barHtml({ loggedIn: !!(state.data && state.data.isAdmin) });
    Auth.bindBar(box, { loggedIn: !!(state.data && state.data.isAdmin) }, reload);
  }

  async function reload() {
    await load();
  }

  async function load() {
    if (!activityId) {
      $("#loading").innerHTML = '活动地址不正确';
      return;
    }
    var data = await api("/api/activity?id=" + encodeURIComponent(activityId));
    if (!data.ok) {
      $("#loading").innerHTML = '' + esc(data.error || "加载失败");
      return;
    }
    state.data = data;
    Auth.set(data.user);
    render();
  }

  function missingOther() {
    var act = state.data && state.data.activity;
    if (!act || !act.config) return "";
    var bad = "";
    (act.config.questions || []).forEach(function (q) {
      if (bad || !q.other || q.type === "text") return;
      var picked = $$('input[name="q_' + q.id + '"]:checked').some(function (x) {
        return x.value === "__other__";
      });
      if (picked && !otherText(q.id)) bad = "请在「" + q.label + "」的其他里填写内容";
    });
    return bad;
  }

  async function submit() {
    if (state.busy) return;
    var badOther = missingOther();
    if (badOther) return toast(badOther, true);
    var btn = $("#btnSubmit");
    state.busy = true;
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>提交中…';
    var payload = collectPayload();
    var res = await api("/api/activity/submit", { method: "POST", body: payload });
    state.busy = false;
    btn.disabled = false;
    btn.textContent = "提交";

    if (!res.ok) {
      toast(res.error || "提交失败", true);
      return;
    }
    state.submitted = true;
    if (res.card) state.data.card = res.card;
    if (res.results) state.data.results = res.results;
    state.data.used = (state.data.used || 0) + 1;
    state.data.resultsVisible = state.data.resultsVisible || !!res.results;
    toast("提交成功");
    window.scrollTo({ top: 0, behavior: "smooth" });
    render();
  }

  document.addEventListener("click", function (e) {
    if (e.target && e.target.id === "btnSubmit") submit();
    if (e.target && e.target.id === "btnAuth") Auth.open("login", reload);
  });

  // 在「其他」输入框里打字时，自动把「其他」勾上，省得填了没生效
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (!t || !t.classList || !t.classList.contains("other-input")) return;
    if (!t.value.trim()) return;
    $$('input[name="q_' + t.dataset.q + '"][value="__other__"]').forEach(function (x) {
      x.checked = true;
    });
    syncMultiLimit(t.dataset.q);
  });

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || t.type !== "checkbox" || !t.name || t.name.indexOf("q_") !== 0) return;
    syncMultiLimit(t.name.slice(2));
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target && e.target.tagName === "INPUT" && state.data) {
      var activity = state.data.activity;
      if (activity.kind === "signup" && e.target.type === "text") {
        e.preventDefault();
        submit();
      }
    }
  });

  load();
})();

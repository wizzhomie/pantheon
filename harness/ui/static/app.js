"use strict";

(() => {
  const token = new URLSearchParams(location.search).get("t") || "";
  const TABS = [
    ["overview", "Обзор"],
    ["roadmap", "Roadmap"],
    ["tasks", "Задачи"],
    ["runs", "Прогоны"],
    ["records", "Записи"],
  ];

  let state = null;
  let boxName = new URLSearchParams(location.search).get("box") || "";
  let tab = "overview";
  let role = "orchestrator";
  let thread = [];
  let sending = false;
  let lastError = "";
  let chatError = "";

  // ── helpers ──────────────────────────────────────────────────────

  const $ = (id) => document.getElementById(id);

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function clear(node, ...children) {
    node.replaceChildren(...children.flat().filter(Boolean));
    return node;
  }

  function cents(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toFixed(2).replace(".", ",")}¢`;
  }

  function seconds(value) {
    if (value === null || value === undefined) return "";
    const total = Math.round(Number(value));
    if (total < 60) return `${total} с`;
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    return rest ? `${minutes} м ${rest} с` : `${minutes} м`;
  }

  function when(iso) {
    if (!iso) return "";
    const date = new Date(String(iso).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return String(iso);
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    return sameDay ? time : `${date.toLocaleDateString("ru-RU")} ${time}`;
  }

  function shortDigest(value) {
    const text = String(value || "").replace(/^sha256:/, "");
    return text.slice(0, 12);
  }

  function chip(label, tone) {
    return el("span", { class: `chip ${tone || ""}`, text: label });
  }

  function toneOf(value) {
    const word = String(value || "").toLowerCase();
    if (["complete", "closed", "pass", "approved", "running", "on"].includes(word)) return "ok";
    if (["blocked", "stopped", "hold", "refused", "off", "bad"].includes(word)) return "bad";
    if (["incomplete", "in progress", "proposed", "retry", "warn", "changed"].includes(word)) {
      return "warn";
    }
    if (["queued", "noop", "returned", "read"].includes(word)) return "info";
    return "plain";
  }

  function card(title, ...children) {
    return el("section", { class: "card" }, title ? el("h3", {}, title) : null, ...children);
  }

  function empty(title, note) {
    return el("div", { class: "empty" }, el("b", {}, title), note || "");
  }

  function section(title, aside) {
    return el(
      "div",
      { class: "section-title" },
      el("h2", {}, title),
      aside ? el("span", { class: "aside" }, aside) : null
    );
  }

  function kv(pairs) {
    return el(
      "dl",
      { class: "kv" },
      ...pairs.map(([key, value]) =>
        value === null || value === undefined || value === ""
          ? [el("dt", {}, key), el("dd", {}, "—")]
          : [el("dt", {}, key), el("dd", {}, value)]
      )
    );
  }

  function bar(part, total, tone) {
    const share = total > 0 ? Math.min(100, Math.round((part / total) * 100)) : 0;
    return el(
      "div",
      { class: `bar ${tone || ""}` },
      el("span", { style: `width:${share}%` })
    );
  }

  function rich(text) {
    const fragment = document.createDocumentFragment();
    const lines = String(text || "").split("\n");
    let list = null;
    let paragraph = null;
    const flush = () => {
      if (paragraph) {
        fragment.append(paragraph);
        paragraph = null;
      }
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (line.startsWith("- ") || line.startsWith("* ")) {
        flush();
        if (!list) {
          list = el("ul", { style: "margin:0 0 8px;padding-left:18px" });
          fragment.append(list);
        }
        list.append(el("li", {}, inline(line.slice(2))));
        continue;
      }
      list = null;
      if (!line.trim()) {
        flush();
        continue;
      }
      if (!paragraph) paragraph = el("p");
      else paragraph.append(document.createTextNode(" "));
      paragraph.append(inline(line));
    }
    flush();
    if (!fragment.childNodes.length) fragment.append(el("p", {}, text || ""));
    return fragment;
  }

  function inline(text) {
    const fragment = document.createDocumentFragment();
    const pieces = String(text).split(/(`[^`]+`)/g);
    for (const piece of pieces) {
      if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
        fragment.append(el("code", {}, piece.slice(1, -1)));
      } else {
        fragment.append(document.createTextNode(piece));
      }
    }
    return fragment;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "X-Pantheon-Token": token, ...(options.headers || {}) },
    });
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = { error: `ответ ${response.status}` };
    }
    if (!response.ok) throw new Error((data && data.error) || `HTTP ${response.status}`);
    return data;
  }

  // ── state ────────────────────────────────────────────────────────

  async function loadState() {
    try {
      state = await api(`/api/state${boxName ? `?box=${encodeURIComponent(boxName)}` : ""}`);
      lastError = "";
    } catch (error) {
      lastError = error.message;
    }
    renderRail();
    renderHead();
    renderView();
  }

  function meta() {
    return state || {};
  }

  function milestones() {
    const rails = meta().rails || {};
    return rails.milestones || [];
  }

  function campaigns() {
    return meta().campaigns || [];
  }

  function events() {
    return (meta().stream || {}).feed || [];
  }

  function openProblems() {
    const list = [];
    for (const item of campaigns()) {
      for (const reason of item.problems || []) list.push(reason);
      if (item.error) list.push(`план ${item.id}: ${item.error}`);
    }
    const rails = meta().rails || {};
    if (rails.error) list.push(`план: ${rails.error}`);
    return list;
  }

  // ── rail ─────────────────────────────────────────────────────────

  function renderRail() {
    const select = $("boxes");
    const names = (window.__boxes || []).map((item) => item.name);
    if (!names.length && meta().box) names.push(meta().box.name);
    if (!select.options.length) {
      for (const name of names) select.append(el("option", { value: name }, name));
    }
    if (meta().box) select.value = meta().box.name;

    const nav = $("nav");
    const counts = {
      overview: "",
      roadmap: String(milestones().filter((item) => item.state !== "closed").length || ""),
      tasks: String((meta().tasks || []).length || ""),
      runs: String((meta().tasks || []).filter((item) => item.state === "running").length || ""),
      records: String(((meta().records || {}).digest || {}).count || ""),
    };
    clear(
      nav,
      ...TABS.map(([key, label]) =>
        el(
          "button",
          {
            class: tab === key ? "active" : "",
            onclick: () => {
              tab = key;
              renderRail();
              renderHead();
              renderView();
            },
          },
          el("span", {}, label),
          counts[key] ? el("span", { class: "count" }, counts[key]) : null
        )
      )
    );

    const container = meta().container || {};
    const project = meta().project || {};
    const spend = (meta().spend || {}).live_cents;
    clear(
      $("railfoot"),
      el(
        "div",
        { class: "row" },
        el("span", {}, el("span", { class: `dot ${container.running ? "on" : "off"}` }), " ",
          container.running ? "коробка запущена" : "коробка стоит")
      ),
      el("div", { class: "row" }, el("span", {}, "потрачено"), el("span", {}, cents(spend))),
      el("div", { class: "path" }, project.path || ""),
      el("div", { class: "path" }, project.branch ? `ветка ${project.branch}` : "")
    );
  }

  function renderHead() {
    const box = meta().box || {};
    const project = meta().project || {};
    const frame = (meta().frame || {}).state || "none";
    const rails = (meta().rails || {}).standing || {};
    const chips = [
      project.branch ? chip(`ветка ${project.branch}`, "plain") : null,
      chip(frameLabel(frame), toneOf(frame)),
      rails.state ? chip(`план: ${railsLabel(rails.state)}`, toneOf(rails.state)) : null,
      lastError ? chip("страница не обновилась", "bad") : null,
    ];
    clear(
      $("stagehead"),
      el("h1", {}, box.name || "коробка"),
      el("span", { class: "where" }, project.path || ""),
      el("div", { class: "chips" }, ...chips),
      el(
        "button",
        { class: "chip toggle-talk", onclick: () => $("app").classList.toggle("talking") },
        "Спросить"
      )
    );
  }

  function frameLabel(state) {
    if (state === "approved") return "рамка принята";
    if (state === "changed") return "рамка менялась";
    if (state === "never") return "рамка не принята";
    return "рамки нет";
  }

  function railsLabel(state) {
    if (state === "approved") return "принят";
    if (state === "changed") return "менялся после приёмки";
    if (state === "never") return "предложен, не принят";
    return "нет";
  }

  // ── views ────────────────────────────────────────────────────────

  function renderView() {
    const view = $("view");
    if (!state) {
      clear(view, empty(lastError ? "Страница не загрузилась" : "Читаю коробку…", lastError));
      return;
    }
    const render = { overview: viewOverview, roadmap: viewRoadmap, tasks: viewTasks, runs: viewRuns, records: viewRecords }[tab] || viewOverview;
    clear(view, ...render());
  }

  function viewOverview() {
    const box = meta().box || {};
    const project = meta().project || {};
    const container = meta().container || {};
    const frame = meta().frame || {};
    const rails = meta().rails || {};
    const standing = rails.standing || {};
    const records = meta().records || {};
    const digest = records.digest || {};
    const spend = meta().spend || {};
    const tasks = meta().tasks || [];
    const open = milestones().filter((item) => item.state !== "closed");
    const closed = milestones().length - open.length;

    const frameCard = card(
      "Рамка",
      el("div", { class: "big" }, frameLabel(frame.state)),
      el("div", { class: "sub" },
        frame.state === "approved"
          ? `принял ${frame.approved.by}, ${when(frame.approved.at)}`
          : "обещание и требования проекта")
    );

    const planCard = card(
      "План",
      el("div", { class: "big" }, railsLabel(standing.state)),
      el("div", { class: "sub" },
        milestones().length
          ? `вех ${milestones().length}, закрыто ${closed}`
          : "вехи, метрики и принципы не описаны"),
      milestones().length ? bar(closed, milestones().length, open.length ? "" : "ok") : null
    );

    const runCard = card(
      "Прогоны",
      el("div", { class: "big" }, container.running ? "коробка запущена" : "коробка стоит"),
      el("div", { class: "sub" }, container.detail || ""),
      el("div", { class: "sub" },
        tasks.length ? `последняя задача ${tasks[tasks.length - 1].state}` : "задач ещё не было")
    );

    const moneyCard = card(
      "Деньги",
      el("div", { class: "big" }, cents(spend.live_cents)),
      el("div", { class: "sub" },
        `по ${spend.campaigns || 0} кампаниям; отказ на воротах — 0¢`)
    );

    const recordsCard = card(
      "Записи",
      el("div", { class: "big" }, String(digest.count ?? "—")),
      el("div", { class: "sub" },
        digest.digest ? `отпечаток ${shortDigest(digest.digest)}` : "записей нет")
    );

    const problems = openProblems();
    const nowCard = card(
      "Что сейчас",
      el("div", { class: "list" },
        ...(problems.length
          ? problems.map((problem) => el("div", { class: "rowitem" },
              chip("!", "bad"), el("div", { class: "grow" }, problem)))
          : [el("div", { class: "sub" }, "Стопоров нет: планы читаются, коробка на связи.")]))
    );

    return [
      el("div", { class: "grid" }, frameCard, planCard, runCard, recordsCard, moneyCard),
      nowCard,
      section("Последние события", "из ленты коробки"),
      feedList(events().slice(-8).reverse()),
    ];
  }

  function viewRoadmap() {
    const rails = meta().rails || {};
    const standing = rails.standing || {};
    if (!rails.declared) {
      return [
        empty(
          "Плана ещё нет",
          "План — это бриф, вехи, метрики и принципы в файле рельсов. Его пишет архитектор и принимает владелец."
        ),
      ];
    }
    const brief = rails.brief || {};
    const milestones = rails.milestones || [];
    const closed = milestones.filter((item) => item.state === "closed").length;
    const approval = standing.approved;

    const head = card(
      "Принято?",
      el("div", { class: "big" }, railsLabel(standing.state)),
      el("div", { class: "sub" },
        approval
          ? `принял ${approval.by}, ${when(approval.at)}`
          : "план написан, но не принят: шаг обязан назвать веху, иначе ворота откажут"),
      el("div", { class: "sub" }, `отпечаток ${shortDigest(rails.digest || standing.digest)}`)
    );

    const briefCard = card(
      "Бриф",
      kv([
        ["Зачем", brief.why],
        ["Для кого", brief.for],
        ["Успех", brief.success],
      ])
    );

    const goals = (rails.goals || []).length
      ? card("Цели", el("div", { class: "list" },
          ...rails.goals.map((goal) => el("div", { class: "rowitem" },
            el("span", { class: "dot on", style: "margin-top:7px" }),
            el("div", { class: "grow" }, goal)))))
      : null;

    const milestoneCards = milestones.map((item) =>
      el("section", { class: "card" },
        el("div", { class: "chips" },
          chip(`${item.id}`, toneOf(item.state === "closed" ? "closed" : "in progress")),
          chip(item.state === "closed" ? "закрыта" : "открыта", toneOf(item.state))),
        el("div", { class: "big", style: "margin-top:10px" }, item.title),
        el("div", { class: "sub" }, item.closes_when),
        el("div", { class: "sub", style: "margin-top:6px" }, `метрика: ${item.metric}`))
    );

    const metrics = (rails.metrics || []).length
      ? card("Метрики",
          kv(rails.metrics.map((item) => [
            item.name,
            `${item.direction === "down" ? "↓" : "↑"} ${item.threshold}`,
          ])))
      : null;

    const principles = (rails.principles || []).length
      ? card("Принципы",
          el("div", { class: "list" },
            ...rails.principles.map((item) =>
              el("div", { class: "rowitem" },
                el("div", { class: "grow" },
                  el("div", { class: "what" }, item.text),
                  el("div", { class: "meta" }, item.why))))))
      : null;

    return [
      head,
      briefCard,
      goals,
      section("Вехи", milestones.length ? `закрыто ${closed} из ${milestones.length}` : ""),
      milestones.length ? el("div", { class: "grid" }, ...milestoneCards) : empty("Вех нет"),
      metrics,
      principles,
      rails.missing && rails.missing.length
        ? card("Чего не хватает", el("div", { class: "sub" }, rails.missing.join(", ")))
        : null,
    ];
  }

  function viewTasks() {
    const plans = campaigns();
    const recent = [...(meta().tasks || [])].reverse();

    const planCards = plans.map((plan) => {
      if (plan.error) {
        return card(`План ${plan.id}`,
          el("div", { class: "chips" }, chip("не читается", "bad")),
          el("div", { class: "sub", style: "margin-top:8px" }, plan.error));
      }
      const verdict = plan.verdict || {};
      const steps = plan.steps || [];
      const acceptance = plan.acceptance || [];
      const budget = plan.budget || {};
      const spend = plan.spend;
      const done = steps.filter((step) => step.state && step.state.verdict === "pass").length;
      return el("section", { class: "card" },
        el("div", { class: "chips" },
          chip(verdictLabel(verdict.status), toneOf(verdict.status)),
          chip(`${verdict.closed ?? done} из ${verdict.total ?? steps.length}`, "plain"),
          plan.mode ? chip(`режим ${plan.mode}`, "plain") : null),
        el("div", { class: "big", style: "margin-top:10px" }, plan.goal || plan.campaign),
        el("div", { class: "sub", style: "margin-top:2px" }, plan.campaign),
        el("div", { class: "list", style: "margin-top:12px" },
          ...steps.map((step) => stepRow(step))),
        acceptance.length
          ? el("div", { class: "list", style: "margin-top:12px" },
              ...acceptance.map((item) =>
                el("div", { class: "rowitem" },
                  chip(item.satisfied ? "✓" : "…", item.satisfied ? "ok" : "plain"),
                  el("div", { class: "grow" },
                    el("div", { class: "what" }, item.statement),
                    el("div", { class: "meta" }, `шаг ${item.step}`)))))
          : null,
        budget.max_cents
          ? el("div", { style: "margin-top:14px" },
              bar(spend || 0, budget.max_cents, spend > budget.max_cents ? "bad" : ""),
              el("div", { class: "sub" },
                `${cents(spend)} из ${cents(budget.max_cents)}` +
                (budget.max_seconds ? ` · ${Math.round(budget.max_seconds / 60)} мин` : "")))
          : null,
        verdict.reasons && verdict.reasons.length
          ? el("div", { class: "sub", style: "margin-top:10px" }, verdict.reasons.join(" · "))
          : null,
        plan.problems && plan.problems.length
          ? el("div", { class: "sub", style: "margin-top:10px;color:var(--bad)" },
              plan.problems.join(" · "))
          : null
      );
    });

    return [
      section("Планы и шаги", plans.length ? `${plans.length}` : ""),
      plans.length ? el("div", { class: "grid" }, ...planCards) : empty("Планов нет"),
      section("Задачи коробки", "как их записал диспетчер"),
      recent.length
        ? card(null, el("div", { class: "list" },
            ...recent.map((task) =>
              el("div", { class: "rowitem" },
                chip(task.state || "?", toneOf(task.state)),
                el("div", { class: "grow" },
                  el("div", { class: "id" }, task.id),
                  el("div", { class: "meta" },
                    [task.verify ? `проверка ${task.verify}` : "без проверки",
                     task.seconds !== null ? seconds(task.seconds) : null,
                     task.parent ? `поправка к ${task.parent}` : null]
                      .filter(Boolean).join(" · ")))))))
        : empty("Задач ещё нет"),
    ];
  }

  function stepRow(step) {
    const now = step.state || {};
    const label = now.state ? `${now.state}${now.verdict ? ` · ${now.verdict}` : ""}` : "не начат";
    return el("div", { class: "rowitem" },
      chip(step.kind || "шаг", "plain"),
      el("div", { class: "grow" },
        el("div", { class: "id" }, step.id),
        el("div", { class: "meta" },
          [label,
           now.cents !== undefined && now.cents !== null ? cents(now.cents) : null,
           now.attempts !== undefined && now.attempts !== null ? `попытка ${now.attempts}` : null,
           step.next && step.next.allowed ? `повтор можно: ${step.next.reason}` : null]
            .filter(Boolean).join(" · "))),
      chip(now.verdict ? "закрыт" : "открыт", now.verdict === "pass" ? "ok" : "plain"));
  }

  function verdictLabel(status) {
    const words = {
      complete: "сделано",
      blocked: "стоит на воротах",
      incomplete: "не закрыто",
      "in progress": "идёт",
      "over budget": "за бюджетом",
      ready: "готово к шагу",
    };
    return words[status] || status || "нет вердикта";
  }

  function viewRuns() {
    const container = meta().container || {};
    const tasks = [...(meta().tasks || [])].reverse();
    const stream = meta().stream || {};
    const queue = stream.queue || [];
    const live = tasks.filter((task) => task.state === "running");

    return [
      card("Коробка",
        el("div", { class: "chips" },
          chip(container.running ? "запущена" : "стоит", container.running ? "ok" : "bad"),
          chip(container.name || "контейнер", "plain")),
        el("div", { class: "sub", style: "margin-top:8px" }, container.detail || ""),
        container.image ? el("div", { class: "id", style: "margin-top:6px" }, container.image) : null),
      section("Идёт сейчас", live.length ? `${live.length}` : "тишина"),
      live.length
        ? card(null, el("div", { class: "list" }, ...live.map((task) =>
            el("div", { class: "rowitem" },
              el("span", { class: "dot on", style: "margin-top:7px" }),
              el("div", { class: "grow" },
                el("div", { class: "id" }, task.id),
                el("div", { class: "meta" }, `старт ${when(task.at)}`))))))
        : empty("Ничего не выполняется", "Шаг запускается командой `pantheon step`; здесь он появится в работе."),
      queue.length
        ? card("В очереди", el("div", { class: "list" }, ...queue.map((item) =>
            el("div", { class: "rowitem" }, chip("ждёт", "info"),
              el("div", { class: "grow" }, el("div", { class: "id" }, item.task))))))
        : null,
      section("Последние прогоны", "что было и чем кончилось"),
      tasks.length
        ? card(null, el("div", { class: "list" }, ...tasks.slice(0, 12).map((task) =>
            el("div", { class: "rowitem" },
              chip(task.state || "?", toneOf(task.state)),
              el("div", { class: "grow" },
                el("div", { class: "id" }, task.id),
                el("div", { class: "meta" },
                  [when(task.at),
                   task.verify ? `проверка ${task.verify}` : "без проверки",
                   task.seconds !== null ? seconds(task.seconds) : null]
                    .filter(Boolean).join(" · ")))))))
        : empty("Прогонов не было"),
    ];
  }

  function viewRecords() {
    const stream = meta().stream || {};
    const brief = stream.brief;
    const digest = (meta().records || {}).digest || {};
    const entries = [...(digest.entries || [])].reverse();
    return [
      brief
        ? card("Бриф потока",
            kv([["Веха", brief.milestone], ["Что", brief.what], ["Почему", brief.why],
                ["Кто", `${brief.by} · ${when(brief.at)}`]]))
        : null,
      card("Дайджест",
        el("div", { class: "sub" },
          `${digest.count ?? 0} записей, отпечаток ${shortDigest(digest.digest) || "—"}`)),
      section("Записи о работе", entries.length ? `${entries.length}` : ""),
      entries.length
        ? card(null, el("div", { class: "list" }, ...entries.map((entry) =>
            el("div", { class: "rowitem" },
              chip(entry.state || "?", toneOf(entry.state)),
              el("div", { class: "grow" },
                el("div", { class: "what" }, entry.goal || entry.task),
                el("div", { class: "meta" },
                  [entry.task, entry.kind, entry.verdict ? `проверка ${entry.verdict}` : null,
                   entry.commit ? entry.commit.slice(0, 7) : null, when(entry.when)]
                    .filter(Boolean).join(" · ")))))))
        : empty("Записей о работе пока нет"),
      section("Лента событий", events().length ? `${events().length}` : ""),
      feedList(events().slice().reverse(), true),
    ];
  }

  function feedList(items, wide) {
    if (!items || !items.length) return empty("Событий пока нет", "Лента заполняется, когда по задаче принимают решение.");
    return card(null, el("div", { class: "list" }, ...items.map((item) =>
      el("div", { class: "rowitem" },
        chip(kindLabel(item.kind), toneOf(item.kind)),
        el("div", { class: "grow" },
          el("div", { class: "what" }, item.reason || item.task || "—"),
          el("div", { class: "meta" },
            [when(item.at), item.task, item.state, item.milestone ? `веха ${item.milestone}` : null]
              .filter(Boolean).join(" · ")))))));
  }

  function kindLabel(kind) {
    const words = { brief: "бриф", queued: "в очередь", verdict: "вердикт", discarded: "снято", read: "прочитано" };
    return words[kind] || kind;
  }

  // ── talk ─────────────────────────────────────────────────────────

  function renderRoles() {
    clear($("roles"), ...["orchestrator", "architect"].map((key) =>
      el("button", {
        class: role === key ? "active" : "",
        onclick: () => {
          role = key;
          chatError = "";
          renderRoles();
          loadThread();
        },
      }, key === "orchestrator" ? "Оркестратор" : "Архитектор")));
  }

  async function loadThread() {
    try {
      const data = await api(`/api/chat?role=${role}`);
      thread = data.messages || [];
    } catch (error) {
      thread = [];
      $("hint").textContent = error.message;
    }
    renderMessages();
  }

  function renderMessages() {
    const box = $("messages");
    if (!thread.length) {
      clear(box, el("div", { class: "empty" },
        el("b", {}, role === "orchestrator" ? "Оркестратор" : "Архитектор"),
        role === "orchestrator"
          ? "Текущие задачи: что запущено, что ждёт, что делать следующим шагом."
          : "Рамка, план, аудит и внедрение нового: что меняем и почему."));
      if (chatError) box.append(el("div", { class: "msg error" }, chatError));
      return;
    }
    clear(box, ...thread.map((message) => {
      const mine = message.role === "user";
      const node = el("div", { class: `msg ${mine ? "user" : "bot"}` });
      node.append(...rich(message.text || "").childNodes);
      if (!mine && message.cents !== null && message.cents !== undefined) {
        const tokens = message.tokens || {};
        const bits = [cents(message.cents)];
        if (tokens.prompt !== null && tokens.prompt !== undefined) {
          bits.push(`${(tokens.prompt || 0) + (tokens.completion || 0)} токенов`);
        }
        node.append(el("span", { class: "price" }, bits.join(" · ")));
      }
      return node;
    }));
    if (chatError) box.append(el("div", { class: "msg error" }, `Не получилось: ${chatError}`));
    box.scrollTop = box.scrollHeight;
  }

  function thinking() {
    return el("div", { class: "msg bot", id: "thinking" },
      el("span", { class: "thinking" }, el("i"), el("i"), el("i"), " думает"));
  }

  async function send() {
    const field = $("composertext");
    const text = field.value.trim();
    if (!text || sending) return;
    sending = true;
    $("send").disabled = true;
    $("hint").textContent = "ответ стоит денег; цена появится под ним";
    const box = $("messages");
    box.append(el("div", { class: "msg user" }, rich(text)));
    box.append(thinking());
    box.scrollTop = box.scrollHeight;
    try {
      await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, text, box: meta().box ? meta().box.name : boxName }),
      });
      chatError = "";
      field.value = "";
      field.style.height = "";
    } catch (error) {
      chatError = error.message;
    } finally {
      sending = false;
      $("send").disabled = false;
      await loadThread();
    }
  }

  // ── boot ─────────────────────────────────────────────────────────

  function wire() {
    $("composer").addEventListener("submit", (event) => {
      event.preventDefault();
      send();
    });
    $("composertext").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        send();
      }
    });
    $("composertext").addEventListener("input", (event) => {
      const field = event.target;
      field.style.height = "auto";
      field.style.height = `${Math.min(160, field.scrollHeight)}px`;
    });
    $("closetalk").addEventListener("click", () => $("app").classList.remove("talking"));
    $("boxes").addEventListener("change", (event) => {
      const name = event.target.value;
      window.location.search =
        `?t=${encodeURIComponent(token)}&box=${encodeURIComponent(name)}`;
    });
  }

  async function boot() {
    wire();
    renderRoles();
    try {
      const data = await api("/api/boxes");
      window.__boxes = data.boxes || [];
    } catch (error) {
      window.__boxes = [];
      lastError = error.message;
    }
    await loadState();
    if (!state) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await loadState();
    }
    await loadThread();
    setInterval(() => {
      if (document.visibilityState === "visible") loadState();
    }, 8000);
  }

  boot();
})();

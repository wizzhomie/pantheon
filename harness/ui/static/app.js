"use strict";

(() => {
  const token = new URLSearchParams(location.search).get("t") || "";
  const initial = new URLSearchParams(location.search);
  let boxName = initial.get("box") || "";
  let role = initial.get("role") === "architect" ? "architect" : "orchestrator";
  let state = null;
  let boxes = [];
  let thread = [];
  let busy = false;
  let notice = "";

  // ── helpers ──────────────────────────────────────────────────────

  const $ = (id) => document.getElementById(id);

  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
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

  function shortDigest(value) {
    return String(value || "").replace(/^sha256:/, "").slice(0, 12);
  }

  function titleCase(word) {
    const text = String(word || "");
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

  function inline(text) {
    const fragment = document.createDocumentFragment();
    for (const piece of String(text).split(/(`[^`]+`)/g)) {
      if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
        fragment.append(h("code", {}, piece.slice(1, -1)));
      } else {
        fragment.append(document.createTextNode(piece));
      }
    }
    return fragment;
  }

  function rich(text) {
    const fragment = document.createDocumentFragment();
    let list = null;
    let paragraph = null;
    const flush = () => {
      if (paragraph) {
        fragment.append(paragraph);
        paragraph = null;
      }
    };
    for (const raw of String(text || "").split("\n")) {
      const line = raw.trimEnd();
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet || numbered) {
        flush();
        const kind = bullet ? "ul" : "ol";
        if (!list || list.tagName !== kind) {
          list = h(kind);
          fragment.append(list);
        }
        list.append(h("li", {}, inline((bullet || numbered)[1])));
        continue;
      }
      list = null;
      if (!line.trim()) {
        flush();
        continue;
      }
      if (!paragraph) paragraph = h("p");
      else paragraph.append(document.createTextNode(" "));
      paragraph.append(inline(line.replace(/\*\*/g, "")));
    }
    flush();
    if (!fragment.childNodes.length) fragment.append(h("p", {}, text || ""));
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
      data = { error: `answer ${response.status}` };
    }
    if (!response.ok) throw new Error((data && data.error) || `HTTP ${response.status}`);
    return data;
  }

  // ── data views ───────────────────────────────────────────────────

  function meta() { return state || {}; }

  function campaigns() { return meta().campaigns || []; }

  function currentCampaign() {
    const list = campaigns().filter((item) => !item.error);
    if (!list.length) return null;
    const open = list.filter((item) => !item.verdict || item.verdict.status !== "complete");
    return (open.length ? open : list)[open.length ? open.length - 1 : list.length - 1];
  }

  function stepsOf(campaign) {
    return (campaign && campaign.steps) || [];
  }

  function stepState(step) {
    return (step && step.state) || null;
  }

  function isClosed(step) {
    const now = stepState(step);
    return Boolean(now && now.state === "closed");
  }

  function isRunning(step) {
    const now = stepState(step);
    return Boolean(now && now.state === "running");
  }

  function acceptanceFor(campaign, stepId) {
    const items = (campaign && campaign.acceptance) || [];
    return items.find((item) => item.step === stepId) || null;
  }

  function humanStep(step) {
    return String(step.id || "").replace(/^t-\d{8}-/, "");
  }

  // ── sidebar ──────────────────────────────────────────────────────

  function renderProjects() {
    const node = $("projects");
    const sessions = $("sessions");
    const list = boxes.map((item) => {
      const active = state && meta().box && item.name === meta().box.name;
      const icon = active
        ? h("svg", { width: 18, height: 18, viewBox: "0 0 24 24", fill: "#d4141c", "aria-hidden": "true" },
            h("path", { d: "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z" }))
        : h("svg", { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none",
            stroke: "#8a7d7e", "stroke-width": 1.8, "aria-hidden": "true" },
            h("path", { d: "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z" }));
      const button = h("button", {
        class: `folder${active ? " active" : ""}`,
        type: "button",
        onclick: () => switchBox(item.name),
      }, icon, h("span", {}, item.name), active ? h("span", { class: "count mono" }, "2 sessions") : null);
      return { active, button };
    });
    clear(node, ...list.map((item) => item.button));
    const activeButton = list.find((item) => item.active);
    if (activeButton && activeButton.button.after) activeButton.button.after(sessions);
    else node.append(sessions);
  }

  function renderSessions() {
    const campaign = currentCampaign();
    const steps = stepsOf(campaign);
    const closed = steps.filter(isClosed).length;
    const live = meta().container && meta().container.running;
    const plan = (meta().rails || {}).standing || {};
    const planWord = plan.state === "approved" ? "plan approved"
      : plan.state === "never" ? "plan proposed" : "no plan";
    const milestones = (meta().rails || {}).milestones || [];
    const doneMilestones = milestones.filter((item) => item.state === "closed").length;
    const rows = [
      { key: "orchestrator", name: "Orchestrator", sub: milestones.length
          ? `${steps.filter(isRunning).length ? "running" : "idle"} · ${doneMilestones} of ${milestones.length} milestones`
          : (live ? "running · no plan" : "idle · no plan") },
      { key: "architect", name: "Architect", sub: `idle · ${planWord}` },
    ];
    clear($("sessions"), ...rows.map((row) =>
      h("button", {
        class: `session${role === row.key ? " on" : ""}`,
        type: "button",
        onclick: () => switchRole(row.key),
      },
        h("span", { class: `dot${row.key === "orchestrator" && steps.some(isRunning) ? " live" : ""}` }),
        h("span", { class: "stack" },
          h("span", { class: "name" }, row.name),
          h("span", { class: "sub" }, row.sub)))));
  }

  function renderTrajan() {
    const tasks = meta().tasks || [];
    const last = tasks.length ? tasks[tasks.length - 1] : null;
    const spend = (meta().spend || {}).live_cents;
    let text = "Verification is on. Nothing merges without a verdict.";
    if (last) {
      text = `Verification is on. Last verdict: ${last.verify || "not verified"}, ${cents(spend)} spent.`;
    }
    $("trajan").textContent = text;
  }

  // ── topbar ───────────────────────────────────────────────────────

  function renderTopbar() {
    $("crumbbox").textContent = (meta().box || {}).name || "pantheon";
    $("crumbrole").textContent = role === "orchestrator" ? "Orchestrator" : "Architect";
    const mode = (currentCampaign() || {}).mode || "manual";
    $("mode-manual").className = mode === "manual" ? "on" : "";
    $("mode-auto").className = mode === "auto" ? "on" : "";
    $("mode-manual").disabled = true;
    $("mode-auto").disabled = true;
    $("text").placeholder = role === "orchestrator"
      ? "Message the orchestrator…"
      : "Message the architect…";
  }

  // ── thread ───────────────────────────────────────────────────────

  function avatar() {
    const letter = role === "orchestrator" ? "O" : "A";
    return h("div", { class: "avatar", "aria-hidden": "true" }, letter);
  }

  function introBlock() {
    const campaign = currentCampaign();
    const rails = meta().rails || {};
    const standing = rails.standing || {};
    const milestones = rails.milestones || [];
    const frame = meta().frame || {};
    const lines = [];
    if (milestones.length) {
      const closed = milestones.filter((item) => item.state === "closed").length;
      lines.push(`План: вехи — ${milestones.length}, закрыто ${closed}; сейчас ${standing.state === "approved" ? "принят" : "не принят"}.`);
    } else {
      lines.push("Плана нет: вехи и принципы ещё не описаны.");
    }
    if (campaign) {
      const verdict = campaign.verdict || {};
      lines.push(`Кампания «${campaign.campaign}»: ${verdict.status || "без вердикта"}, ${verdict.closed || 0} из ${verdict.total || 0} шагов.`);
    } else {
      lines.push("Кампаний нет: план ещё не писали.");
    }
    const chips = [
      h("div", { class: "chip" }, h("span", { class: "arrow" }, "▸"),
        `frame · ${frame.state || "empty"}`),
      h("div", { class: "chip" }, h("span", { class: "arrow" }, "▸"),
        `plan · ${standing.state || "none"}`),
      h("div", { class: "chip" }, h("span", { class: "arrow" }, "▸"),
        `records · ${((meta().records || {}).digest || {}).count || 0}`),
    ];
    const card = campaign
      ? h("div", { class: "card" },
          h("div", { class: "label mono" }, "CAMPAIGN"),
          h("div", { class: "title" }, campaign.goal || campaign.campaign),
          h("div", { class: "sub" }, `${campaign.steps ? campaign.steps.length : 0} steps · ${cents(campaign.spend)} of ${cents((campaign.budget || {}).max_cents)}`))
      : null;
    return h("div", { class: `answer ${role === "orchestrator" ? "orch" : "arch"}` },
      avatar(),
      h("div", { class: "body" },
        h("p", {}, ...rich(lines.join("\n")).childNodes),
        ...chips,
        card));
  }

  function renderThread() {
    const threadNode = $("thread");
    if (!state) {
      clear(threadNode, h("div", { class: "emptyline" }, notice || "Reading the box…"));
      return;
    }
    const nodes = [];
    if (!thread.length) nodes.push(introBlock());
    for (const message of thread) {
      if (message.role === "user") {
        nodes.push(h("div", { class: "bubble-user" }, ...rich(message.text || "").childNodes));
        continue;
      }
      const bits = [];
      if (message.tokens && message.tokens.prompt !== null && message.tokens.prompt !== undefined) {
        bits.push(`${(message.tokens.prompt || 0) + (message.tokens.completion || 0)} tokens`);
      }
      if (message.cents !== null && message.cents !== undefined) bits.push(cents(message.cents));
      nodes.push(h("div", { class: `answer ${role === "orchestrator" ? "orch" : "arch"}` },
        avatar(),
        h("div", { class: "body" },
          ...rich(message.text || "").childNodes,
          bits.length
            ? h("div", { class: "chip" }, h("span", { class: "arrow" }, "▸"), bits.join(" · "))
            : null)));
    }
    if (busy) {
      nodes.push(h("div", { class: `answer ${role === "orchestrator" ? "orch" : "arch"}` },
        avatar(),
        h("div", { class: "body" },
          h("div", { class: "thinking" }, h("i"), h("i"), h("i"), " thinking…"))));
    }
    if (notice) nodes.push(h("div", { class: "notice" }, notice));
    clear(threadNode, ...nodes);
    threadNode.scrollTop = threadNode.scrollHeight;
  }

  // ── right panel ──────────────────────────────────────────────────

  function renderAgents() {
    const campaign = currentCampaign();
    const steps = stepsOf(campaign);
    const rows = steps.length
      ? steps.map((step) => ({
          name: titleCase(step.kind || "step"),
          note: [step.id, (step.paths || []).slice(0, 1).join("")].filter(Boolean).join(" · "),
          state: isRunning(step) ? "running" : isClosed(step) ? "done" : "queued",
        }))
      : (meta().tasks || []).slice(-4).map((task) => ({
          name: titleCase(task.state === "closed" ? "task" : "task"),
          note: `${task.id} · ${task.verify || "not verified"}`,
          state: task.state === "closed" ? "done" : task.state === "running" ? "running" : "queued",
        }));
    const live = rows.filter((row) => row.state === "running").length;
    $("agentscount").textContent = `${live} running`;
    clear($("agents"), ...(rows.length ? rows.map((row) =>
      h("div", { class: `agentrow${row.state === "running" ? " live" : ""}` },
        h("div", { class: "head" },
          h("span", { class: `dot ${row.state === "running" ? "live" : row.state === "done" ? "done" : ""}` }),
          h("span", { class: "name" }, row.name),
          h("span", { class: `badge ${row.state === "running" ? "live" : row.state === "done" ? "done" : ""}` },
            row.state)),
        h("div", { class: "note" }, row.note)))
      : [h("div", { class: "agentrow" }, h("div", { class: "emptyline" }, "No agents yet."))]));
  }

  function renderTasks() {
    const campaign = currentCampaign();
    const milestones = (meta().rails || {}).milestones || [];
    if (milestones.length) {
      const done = milestones.filter((item) => item.state === "closed").length;
      $("tasksTitle").textContent = "Tasks · roadmap";
      $("taskscount").textContent = `${done} of ${milestones.length}`;
      $("progress").style.width = `${Math.round((done / milestones.length) * 100)}%`;
      clear($("tasks"), ...milestones.map((item) => {
        const closed = item.state === "closed";
        return h("li", { class: `taskrow${closed ? " done" : ""}` },
          h("span", { class: `box${closed ? " done" : ""}` }, closed ? "✓" : ""),
          h("span", { class: "text" }, `${item.id} · ${item.title}`,
            h("span", { class: "meta" }, item.closes_when)));
      }));
      return;
    }
    const steps = stepsOf(campaign);
    const closed = steps.filter(isClosed).length;
    $("tasksTitle").textContent = campaign ? `Tasks · ${campaign.campaign}` : "Tasks";
    $("taskscount").textContent = campaign ? `${closed} of ${steps.length}` : "—";
    $("progress").style.width = steps.length ? `${Math.round((closed / steps.length) * 100)}%` : "0%";
    if (!steps.length) {
      clear($("tasks"), h("li", { class: "taskrow" },
        h("div", { class: "emptyline" }, campaign && campaign.problems && campaign.problems.length
          ? campaign.problems[0]
          : "No plan yet.")));
      return;
    }
    clear($("tasks"), ...steps.map((step) => {
      const acceptance = acceptanceFor(campaign, step.id);
      const done = isClosed(step);
      const active = isRunning(step);
      const now = stepState(step) || {};
      const text = acceptance ? acceptance.statement : humanStep(step);
      const meta = [step.kind, now.verdict ? `verdict ${now.verdict}` : null,
        now.cents !== null && now.cents !== undefined ? cents(now.cents) : null]
        .filter(Boolean).join(" · ");
      return h("li", { class: `taskrow${done ? " done" : active ? " active" : ""}` },
        h("span", { class: `box${done ? " done" : active ? " active" : ""}` },
          done ? "✓" : active ? "▸" : ""),
        h("span", { class: "text" }, text, h("span", { class: "meta" }, meta)));
    }));
  }

  function render() {
    renderProjects();
    renderSessions();
    renderTrajan();
    renderTopbar();
    renderThread();
    renderAgents();
    renderTasks();
  }

  // ── actions ──────────────────────────────────────────────────────

  async function loadState() {
    try {
      state = await api(`/api/state${boxName ? `?box=${encodeURIComponent(boxName)}` : ""}`);
      notice = "";
    } catch (error) {
      notice = `The box did not answer: ${error.message}`;
    }
    render();
  }

  async function loadThread() {
    try {
      const data = await api(`/api/chat?role=${role}${boxName ? `&box=${encodeURIComponent(boxName)}` : ""}`);
      thread = data.messages || [];
    } catch (error) {
      thread = [];
      notice = error.message;
    }
    renderThread();
  }

  function switchRole(next) {
    if (role === next) return;
    role = next;
    thread = [];
    notice = "";
    render();
    loadThread();
  }

  function switchBox(name) {
    const params = new URLSearchParams({ t: token, box: name });
    window.location.search = params.toString();
  }

  async function send() {
    const field = $("text");
    const text = field.value.trim();
    if (!text || busy) return;
    busy = true;
    $("send").disabled = true;
    notice = "";
    const threadNode = $("thread");
    threadNode.append(h("div", { class: "bubble-user" }, ...rich(text).childNodes));
    threadNode.append(h("div", { class: `answer ${role === "orchestrator" ? "orch" : "arch"}` },
      avatar(),
      h("div", { class: "body" },
        h("div", { class: "thinking" }, h("i"), h("i"), h("i"), " thinking…"))));
    threadNode.scrollTop = threadNode.scrollHeight;
    field.value = "";
    try {
      await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, text, box: (meta().box || {}).name || boxName }),
      });
    } catch (error) {
      notice = `The answer did not arrive: ${error.message}`;
    } finally {
      busy = false;
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
    $("newproject").addEventListener("click", () => {
      notice = "New project — for now `pantheon new` in the CLI; the button comes later.";
      renderThread();
    });
    $("mic").addEventListener("click", () => {
      notice = "Voice input is the next slice.";
      renderThread();
    });
  }

  async function boot() {
    wire();
    try {
      const data = await api("/api/boxes");
      boxes = data.boxes || [];
    } catch (error) {
      boxes = [];
      notice = error.message;
    }
    await loadState();
    await loadThread();
    setInterval(() => {
      if (document.visibilityState === "visible") loadState();
    }, 8000);
  }

  boot();
})();

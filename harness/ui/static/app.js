"use strict";

(() => {
  const token = new URLSearchParams(location.search).get("t") || "";
  const DRAFT_MS = 1800;
  const DRAFT_SECONDS = 0.9;
  const DRAFT_LIMIT = 55 * 32000;
  // Live drafting: while the owner speaks, the whole recording so far is read again every
  // DRAFT_MS and the field follows. The final pass on stop replaces the draft with one clean
  // read, so a word cut by a draft boundary cannot survive into the finished sentence.
  const initial = new URLSearchParams(location.search);
  let boxName = initial.get("box") || "";
  let role = initial.get("role") === "architect" ? "architect" : "orchestrator";
  let state = null;
  let boxes = [];
  let thread = [];
  let busy = false;
  let notice = "";
  let recorder = null;
  let voiceRun = 0;
  let pending = null;
  let actJob = null;
  let lastResult = null;
  let voice = null;

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

  function spentOf(campaign) {
    const spent = campaign.spend;
    const ceiling = (campaign.budget || {}).max_cents;
    if (spent === null || spent === undefined) return "nothing spent yet";
    return ceiling === null || ceiling === undefined
      ? `${cents(spent)} spent`
      : `${cents(spent)} of ${cents(ceiling)}`;
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
    renderVoice();
  }

  // ── acts ─────────────────────────────────────────────────────────

  function ask(entry) {
    pending = { ...entry, confirm: entry.id === "step.run" ? "Run step" : "Confirm" };
    renderActions();
  }

  function cancelAct() {
    pending = null;
    renderActions();
  }

  function actButton(entry) {
    return h("button", {
      class: `act${entry.money ? " money" : ""}`,
      type: "button",
      title: entry.detail,
      disabled: entry.enabled === false,
      onclick: () => ask(entry),
    },
      h("span", { class: "actname" }, entry.label),
      entry.detail ? h("span", { class: "actdetail" }, entry.detail) : null);
  }

  function showActions(shown, count) {
    $("actionssection").hidden = !shown;
    if (count !== undefined) $("actionscount").textContent = count;
  }

  function renderActions() {
    const bar = $("actbar");
    if (!state) { showActions(false); return; }
    if (pending) { renderAsk(); return; }
    if (actJob && !actJob.done) { renderRunning(); return; }
    const offered = meta().actions || [];
    if (!offered.length) { showActions(false); clear(bar); return; }
    showActions(true, `${offered.length}`);
    clear(bar, ...offered.map(actButton));
  }

  function renderAsk() {
    showActions(true, "waiting");
    const field = pending.note
      ? h("input", {
          id: "actnote", class: "actnote", autocomplete: "off",
          placeholder: pending.note, "aria-label": pending.note,
        })
      : null;
    const how = pending.money
      ? "Spends money and writes into the project."
      : "Writes a record into the box.";
    clear(bar,
      h("div", { class: "actask" },
        h("div", { class: "actname" }, pending.label),
        h("div", { class: "acthint" }, `${how} ${pending.detail || ""}`.trim()),
        field),
      h("div", { class: "actrow" },
        h("button", { class: `act go${pending.money ? " money" : ""}`, type: "button", onclick: confirmAct },
          pending.confirm),
        h("button", { class: "act stop", type: "button", onclick: cancelAct }, "Cancel")));
    if (field) {
      field.focus();
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); confirmAct(); }
        if (event.key === "Escape") { event.preventDefault(); cancelAct(); }
      });
    }
  }

  function renderRunning() {
    showActions(true, "running");
    clear($("actbar"),
      h("div", { class: "actask" },
        h("div", { class: "actname" }, actJob.label),
        h("div", { class: "acthint" }, "the gate is working: a step can take minutes, and the page says when it ends"),
        h("div", { class: "thinking" }, h("i"), h("i"), h("i"))));
  }

  async function confirmAct() {
    if (!pending) return;
    const asked = pending;
    const params = { ...asked.params };
    const field = $("actnote");
    if (asked.note && field) params.note = field.value.trim();
    pending = null;
    renderActions();
    try {
      const data = await api("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: asked.id, params, box: boxName }),
      });
      actJob = data.job;
    } catch (error) {
      notice = `Кнопка не сработала: ${error.message}`;
      render();
      return;
    }
    renderActions();
    renderAgents();
    pollJob(actJob.id);
  }

  async function pollJob(identifier) {
    for (;;) {
      await new Promise((rest) => setTimeout(rest, 1200));
      let data = null;
      try {
        data = await api(`/api/action?id=${encodeURIComponent(identifier)}`);
      } catch (error) {
        actJob = null;
        notice = `Действие потерялось: ${error.message}`;
        render();
        return;
      }
      actJob = data.job;
      renderActions();
      renderAgents();
      if (!actJob || actJob.done) break;
    }
    if (actJob && actJob.done) await finishJob();
  }

  async function finishJob() {
    const job = actJob;
    actJob = null;
    lastResult = job;
    await loadState();
    render();
  }

  function resultBlock() {
    if (!lastResult) return null;
    const failed = Boolean(lastResult.error) || lastResult.ok === false;
    const said = lastResult.error
      ? `Отказ: ${lastResult.error}`
      : lastResult.sentence || "готово";
    return h("div", { class: `resultcard${failed ? " bad" : ""}` },
      h("div", { class: "sentence" }, ...rich(said).childNodes),
      ...(lastResult.lines || []).filter(Boolean).map((line) =>
        h("div", { class: "line mono" }, line)));
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
          h("div", { class: "sub" }, `${campaign.steps ? campaign.steps.length : 0} steps · ${spentOf(campaign)}`))
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
    const result = resultBlock();
    if (result) nodes.push(result);
    if (notice) nodes.push(h("div", { class: "notice" }, notice));
    clear(threadNode, ...nodes);
    threadNode.scrollTop = threadNode.scrollHeight;
  }

  // ── right panel ──────────────────────────────────────────────────

  function dotClass(state) {
    if (state === "running") return "live";
    if (state === "done") return "done";
    if (state === "stopped") return "bad";
    return "";
  }

  function renderAgents() {
    const campaign = currentCampaign();
    const steps = stepsOf(campaign);
    const rows = steps.length
      ? steps.map((step) => ({
          name: titleCase(step.kind || "step"),
          note: [step.id, (step.paths || []).slice(0, 1).join("")].filter(Boolean).join(" · "),
          state: isRunning(step) ? "running" : isClosed(step) ? "done"
            : stepState(step) ? "stopped" : "queued",
          step: step.id,
          kind: step.kind,
          run: step.run || {},
        }))
      : (meta().tasks || []).slice(-4).map((task) => ({
          name: titleCase(task.state === "closed" ? "task" : "task"),
          note: `${task.id} · ${task.verify || "not verified"}`,
          state: task.state === "closed" ? "done" : task.state === "running" ? "running" : "queued",
          step: null,
          run: {},
        }));
    if (actJob && !actJob.done) {
      rows.unshift({ name: actJob.label, note: "the gate is working", state: "running", step: null, run: {} });
    }
    const live = rows.filter((row) => row.state === "running").length;
    $("agentscount").textContent = `${live} running`;
    clear($("agents"), ...(rows.length ? rows.map((row) =>
      h("div", { class: `agentrow${row.state === "running" ? " live" : ""}` },
        h("div", { class: "head" },
          h("span", { class: `dot ${dotClass(row.state)}` }),
          h("span", { class: "name" }, row.name),
          row.step && row.state !== "done"
            ? h("button", {
                class: "taskrun", type: "button",
                title: row.run.why || "run this step",
                disabled: row.run.allowed !== true,
                "aria-label": `Run ${row.step}`,
                onclick: () => ask({
                  id: "step.run",
                  label: `Run ${humanStep({ id: row.step })}`,
                  detail: `${campaign.campaign} · ${row.kind}${row.run.retry ? " · another attempt" : ""}`,
                  params: {
                    campaign: campaign.campaign,
                    step: row.step,
                    retry: row.run.retry ? "1" : "0",
                  },
                  money: true,
                  note: "",
                  enabled: true,
                }),
              }, "▸")
            : null,
          h("span", { class: `badge ${dotClass(row.state)}` }, row.state)),
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
    renderActions();
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
    if (voice) cancelRecording();
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

  // ── voice ────────────────────────────────────────────────────────

  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      notice = "Браузер не даёт микрофон: страница открыта по http, а микрофон он разрешает только на https или localhost.";
      renderThread();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext({ sampleRate: 16000 });
      await context.resume();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const sink = context.createGain();
      sink.gain.value = 0;
      const chunks = [];
      processor.onaudioprocess = (event) => {
        chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(sink);
      sink.connect(context.destination);
      const field = $("text");
      const run = ++voiceRun;
      recorder = {
        run, stream, context, source, processor, sink, chunks,
        started: Date.now(),
        base: field.value.trim(),
        written: field.value,
        draft: "",
        sending: false,
        stopped: false,
      };
      recorder.timer = setInterval(draftOnce, DRAFT_MS);
      voice = "recording";
      notice = "";
      renderVoice();
      renderThread();
    } catch (error) {
      notice = `Микрофон не открылся: ${error.message}`;
      renderThread();
    }
  }

  function teardown(done) {
    done.stopped = true;
    clearInterval(done.timer);
    done.processor.disconnect();
    done.source.disconnect();
    done.sink.disconnect();
    done.stream.getTracks().forEach((track) => track.stop());
    done.context.close();
  }

  function cancelRecording() {
    if (!recorder) return;
    const done = recorder;
    recorder = null;
    voiceRun += 1;
    teardown(done);
    voice = null;
    renderVoice();
  }

  function stopRecording() {
    if (!recorder) return;
    const done = recorder;
    recorder = null;
    teardown(done);
    if (Date.now() - done.started < 700) {
      voice = null;
      renderVoice();
      notice = "Запись короче секунды: микрофон не успел ничего услышать.";
      renderThread();
      return;
    }
    const wav = encodeWav(done.chunks);
    if (peakOf(done.chunks) < 0.008) {
      voice = null;
      renderVoice();
      notice = "В записи тишина: микрофон ничего не услышал.";
      renderThread();
      return;
    }
    if (!wav) {
      voice = null;
      renderVoice();
      notice = "Запись пустая: микрофон не отдал ни звука.";
      renderThread();
      return;
    }
    sendAudio(wav, done);
  }

  async function draftOnce() {
    const now = recorder;
    if (!now || now.stopped || now.sending || voice !== "recording") return;
    if (now.run !== voiceRun) return;
    const samples = now.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    if (samples < 16000 * DRAFT_SECONDS || samples * 2 > DRAFT_LIMIT) return;
    if (peakOf(now.chunks) < 0.008) return;
    const wav = encodeWav(now.chunks);
    if (!wav) return;
    now.sending = true;
    try {
      const data = await postAudio(wav);
      const text = String(data.text || "").trim();
      const field = $("text");
      if (!text || now.stopped || recorder !== now || now.run !== voiceRun) return;
      if (field.value !== now.written) return; // the owner is typing: the field is theirs
      now.draft = text;
      now.written = now.base ? `${now.base} ${text}` : text;
      field.value = now.written;
      field.setSelectionRange(field.value.length, field.value.length);
    } catch {
      // a draft that failed is not an error: the final pass will say what went wrong
    } finally {
      now.sending = false;
    }
  }

  function peakOf(chunks) {
    let peak = 0;
    for (const chunk of chunks) {
      for (const sample of chunk) peak = Math.max(peak, Math.abs(sample));
    }
    return peak;
  }

  function encodeWav(chunks) {
    const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    if (!length) return null;
    const samples = new Float32Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }
    const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
    const tag = (at, text) => {
      for (let index = 0; index < text.length; index += 1) view.setUint8(at + index, text.charCodeAt(index));
    };
    tag(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    tag(8, "WAVEfmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true);
    view.setUint32(28, 32000, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    tag(36, "data");
    view.setUint32(40, samples.length * 2, true);
    let at = 44;
    for (const sample of samples) {
      const clamped = Math.max(-1, Math.min(1, sample));
      view.setInt16(at, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
      at += 2;
    }
    return new Uint8Array(view.buffer);
  }

  async function postAudio(wav) {
    const response = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "X-Pantheon-Token": token, "Content-Type": "audio/wav" },
      body: wav,
    });
    const data = await response.json().catch(() => ({ error: `answer ${response.status}` }));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function sendAudio(wav, done) {
    const run = done.run;
    voice = "sending";
    renderVoice();
    renderThread();
    try {
      const data = await postAudio(wav);
      if (run !== voiceRun) return;
      const text = String(data.text || "").trim();
      if (!text) {
        notice = "Распознавание не разобрало ни слова: попробуй ближе к микрофону.";
        voice = null;
        renderVoice();
        renderThread();
        return;
      }
      const field = $("text");
      if (field.value !== done.written) {
        notice = "Пока шла запись, поле правили руками: распознанный текст не вставлен.";
        voice = null;
        renderVoice();
        renderThread();
        return;
      }
      field.value = done.base ? `${done.base} ${text}` : text;
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
      notice = "";
    } catch (error) {
      notice = `Голос не сработал: ${error.message}`;
    } finally {
      if (run === voiceRun) {
        voice = null;
        renderVoice();
        renderThread();
      }
    }
  }

  function renderVoice() {
    const mic = $("mic");
    if (!mic) return;
    mic.disabled = voice === "sending";
    mic.classList.toggle("recording", voice === "recording");
    mic.title = voice === "recording" ? "stop and transcribe" : voice === "sending" ? "recognising…" : "voice input";
    mic.setAttribute("aria-label", mic.title);
    $("text").placeholder = voice === "recording"
      ? "Listening… click the mic to stop"
      : role === "orchestrator" ? "Message the orchestrator…" : "Message the architect…";
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
      if (voice === "recording") stopRecording();
      else if (voice !== "sending") startRecording();
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
    try {
      const data = await api("/api/action");
      if (data.job && !data.job.done) {
        actJob = data.job;
        renderActions();
        renderAgents();
        pollJob(actJob.id);
      }
    } catch {
      // the lane is a nicety: a page that cannot read it still reads the box
    }
    setInterval(() => {
      if (document.visibilityState === "visible") loadState();
    }, 8000);
  }

  boot();
})();

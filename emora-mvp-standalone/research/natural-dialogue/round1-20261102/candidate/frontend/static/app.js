const MAX_LENGTH = 10000;
const ACTIVE_SESSION_KEY = "emora.active-session";
const state = { activeId: null, sessionId: null, awaitingClarification: false, busy: false, messages: [], runtimeReady: false };
const EFFORT_LABELS = {
  none: "无", low: "低", medium: "中", high: "高", xhigh: "超高", max: "最大",
};
const RUN_STAGES = {
  input_safety_check: "检查输入安全",
  model_reasoning_none: "请求模型：推理强度无",
  model_reasoning_low: "请求模型：推理强度低",
  model_reasoning_medium: "请求模型：推理强度中",
  model_reasoning_high: "请求模型：推理强度高",
  model_reasoning_xhigh: "请求模型：推理强度超高",
  model_reasoning_max: "请求模型：推理强度最大",
  response_generation: "生成回复",
  output_safety_check: "检查回复安全",
  result_persistence: "保存回复",
};
const $ = (selector) => document.querySelector(selector);
const input = $("#message-input");
const reasoningEffortSelect = $("#reasoning-effort");
const providerModelLabel = $("#provider-model");
const messages = $("#messages");
const history = $("#history");
const liveStatus = $("#status");
const sessionMenu = $("#session-menu");
let menuSession = null;
let menuInvoker = null;
let diagramCounter = 0;

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "neutral",
  flowchart: { htmlLabels: false },
});

const markdownRenderer = new marked.Renderer();
markdownRenderer.html = () => "";
markdownRenderer.code = ({ text, lang }) => {
  if ((lang || "").trim().split(/\s+/, 1)[0].toLowerCase() === "mermaid") {
    return `<pre class="mermaid">${escapeHtml(text)}</pre>`;
  }
  const language = /^[-\w]+/.exec(lang || "")?.[0] || "text";
  return `<pre><code class="language-${language}">${escapeHtml(text)}</code></pre>`;
};

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function renderDiagrams(element) {
  for (const diagram of element.querySelectorAll("pre.mermaid")) {
    const source = diagram.textContent || "";
    const id = `emora-diagram-${++diagramCounter}`;
    void mermaid.render(id, source).then(({ svg }) => {
      if (!diagram.isConnected) return;
      const frame = document.createElement("div");
      frame.className = "diagram-frame";
      frame.setAttribute("role", "img");
      frame.setAttribute("aria-label", "对话图表");
      const template = document.createElement("template");
      template.innerHTML = DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
        FORBID_TAGS: ["foreignObject", "script"],
        FORBID_ATTR: ["onload", "onclick"],
      });
      frame.append(template.content);
      diagram.replaceWith(frame);
      element.closest(".message")?.scrollIntoView({ block: "nearest" });
    }).catch(() => {
      if (diagram.isConnected) diagram.classList.add("diagram-error");
    });
  }
}

function renderRichText(element, text, { diagrams = true } = {}) {
  const source = String(text ?? "");
  const html = marked.parse(source, {
    renderer: markdownRenderer,
    gfm: true,
    breaks: true,
  });
  const safeHtml = DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "style", "iframe", "svg", "math"],
    FORBID_ATTR: ["style", "id", "name"],
  });
  element.classList.add("rich-text");
  element.innerHTML = safeHtml;
  renderMathInElement(element, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
      { left: "$", right: "$", display: false },
    ],
    throwOnError: false,
    trust: false,
  });
  // Incomplete diagram fences are displayed as code until the final response.
  if (diagrams) renderDiagrams(element);
}

function rememberSession(sessionId) {
  state.sessionId = sessionId;
  try {
    if (sessionId) sessionStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
    else sessionStorage.removeItem(ACTIVE_SESSION_KEY);
  } catch { /* The current in-memory conversation remains usable. */ }
}

function announce(message) { liveStatus.textContent = message; }

function makeMessage(role, text, kind = "") {
  const article = document.createElement("article");
  article.className = `message ${role} ${kind}`;
  if (role === "assistant") {
    const mark = document.createElement("span");
    mark.className = "assistant-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "E";
    article.append(mark);
  }
  const body = document.createElement("div");
  body.className = "message-body";
  renderRichText(body, text);
  article.append(body);
  messages.append(article);
  $("#welcome").hidden = true;
  article.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return article;
}

function makePendingMessage() {
  const article = makeMessage("assistant", "", "pending");
  const body = article.querySelector(".message-body");

  const live = document.createElement("div");
  live.className = "run-live";
  live.setAttribute("role", "status");
  live.setAttribute("aria-live", "polite");
  const indicator = document.createElement("span");
  indicator.className = "run-indicator";
  indicator.setAttribute("aria-hidden", "true");
  const liveText = document.createElement("span");
  liveText.className = "run-live-text";
  liveText.textContent = "正在准备回复…";
  live.append(indicator, liveText);

  const answer = document.createElement("div");
  answer.className = "answer-content";

  const requestMeta = document.createElement("div");
  requestMeta.className = "request-meta";
  requestMeta.hidden = true;

  const details = document.createElement("details");
  details.className = "run-details";
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "运行记录";
  const records = document.createElement("div");
  records.className = "run-records";
  const stagesLabel = document.createElement("div");
  stagesLabel.className = "run-group-label";
  stagesLabel.textContent = "处理阶段";
  const stages = document.createElement("ol");
  stages.className = "run-steps";
  const activitiesLabel = document.createElement("div");
  activitiesLabel.className = "run-group-label";
  activitiesLabel.textContent = "技能与工具";
  const activities = document.createElement("ul");
  activities.className = "run-activities";
  const empty = document.createElement("li");
  empty.className = "run-empty";
  empty.textContent = "尚无调用记录";
  activities.append(empty);
  records.append(stagesLabel, stages, activitiesLabel, activities);
  details.append(summary, records);

  body.append(live, answer, requestMeta, details);
  return article;
}

function setRunStatus(message, text) {
  const liveText = message.querySelector(".run-live-text");
  if (liveText) liveText.textContent = text;
  announce(text);
}

function appendRunStage(message, stage) {
  const label = RUN_STAGES[stage];
  if (!label) return;
  const list = message.querySelector(".run-steps");
  if (!list || [...list.children].some((item) => item.dataset.stage === stage)) return;
  const item = document.createElement("li");
  item.dataset.stage = stage;
  item.textContent = label;
  list.append(item);
  setRunStatus(message, `正在${label}…`);
}

function appendRunActivity(message, kind, payload) {
  const list = message.querySelector(".run-activities");
  if (!list) return;
  const empty = list.querySelector(".run-empty");
  if (empty) empty.remove();

  const item = document.createElement("li");
  item.className = "run-activity";
  const title = document.createElement("span");
  title.className = "run-activity-title";
  title.textContent = `${kind === "skill" ? "技能" : "工具"} · ${payload.name || "未知调用"}`;
  const status = document.createElement("span");
  status.className = "run-activity-status";
  const statuses = { started: "进行中", succeeded: "已完成", failed: "失败", waiting: "等待中" };
  status.textContent = statuses[payload.status] || "已记录";
  item.append(title, status);
  if (payload.summary) {
    const summary = document.createElement("span");
    summary.className = "run-activity-summary";
    summary.textContent = payload.summary;
    item.append(summary);
  }
  list.append(item);
}

function finishRun(message, failed = false) {
  const details = message.querySelector(".run-details");
  if (!details) return;
  const live = message.querySelector(".run-live");
  if (live) live.hidden = true;
  const activities = details.querySelector(".run-activities");
  if (activities && !activities.querySelector(".run-activity")) {
    activities.replaceChildren();
    const empty = document.createElement("li");
    empty.className = "run-empty";
    empty.textContent = "本次未调用额外技能或工具";
    activities.append(empty);
  }
  const stageCount = details.querySelectorAll(".run-steps li").length;
  const activityCount = details.querySelectorAll(".run-activity").length;
  const ending = failed ? " · 未完成" : "";
  details.querySelector("summary").textContent =
    `运行记录 · ${stageCount} 个阶段 · ${activityCount} 条调用记录${ending}`;
  details.open = false;
}

function resultText(result) {
  if (typeof result.reply === "string") return result.reply;
  // Legacy report records remain readable; new turns never require these fields.
  const pieces = [
    ["情境概括", result.summary],
    ["主要情绪", (result.emotions || []).join("、")],
    ["核心矛盾", result.conflict],
    ["你可能在意的", result.user_needs],
    ["可以尝试", (result.suggestions || []).map((item, index) => `${index + 1}. ${item}`).join("\n")],
    ["下一步", result.next_step],
    ["安全说明", result.safety_note],
  ].filter(([, value]) => value);
  return pieces.map(([heading, value]) => `### ${heading}\n\n${value}`).join("\n\n");
}

function statusText(response) {
  if (response.status === "blocked") {
    return response.error_code === "unsafe_input_seek_support"
      ? "我很关心你现在的处境。如果你或他人可能正面临现实危险，请联系当地急救服务、危机热线或可信任的人。"
      : "这段内容目前无法继续进行普通对话。";
  }
  if (response.status === "failed") return "这次暂时无法完成。你的内容仍保留在当前对话中，可以稍后重新开始。";
  if (response.status === "waiting_clarification") return response.clarification_question;
  if (response.status === "completed") return resultText(response.result || {});
  return "";
}

function renderResponse(response, existingMessage = null, restoring = false) {
  state.activeId = response.id;
  rememberSession(response.session_id || response.id);
  setReasoningEffort(response.reasoning_effort || "medium");
  state.awaitingClarification = response.status === "waiting_clarification";
  const message = existingMessage || makeMessage("assistant", "", response.status);
  message.className = `message assistant ${response.status}`;
  const answer = message.querySelector(".answer-content");
  if (answer) {
    renderRichText(answer, statusText(response));
    const requestMeta = message.querySelector(".request-meta");
    if (requestMeta && ["completed", "waiting_clarification"].includes(response.status)) {
      const effort = response.reasoning_effort || "medium";
      requestMeta.textContent = `本次模型请求档位：${EFFORT_LABELS[effort] || effort}`;
      requestMeta.hidden = false;
    }
    finishRun(message);
  } else {
    renderRichText(message.querySelector(".message-body"), statusText(response));
  }
  if (["blocked", "failed"].includes(response.status)) {
    state.awaitingClarification = false;
    makeMessage("assistant", "", "new-chat-action");
    const button = document.createElement("button");
    button.className = "text-button";
    button.textContent = "新对话";
    button.addEventListener("click", resetConversation);
    messages.lastElementChild.querySelector(".message-body").append(button);
  }
  if (!restoring && ["completed", "waiting_clarification"].includes(response.status)) {
    void ensureSessionTitle(state.sessionId);
  }
  announce(response.status === "completed" ? "回复已完成" : response.status === "waiting_clarification" ? "需要补充一项信息" : statusText(response));
  if (!restoring) refreshHistory();
}

async function ensureSessionTitle(sessionId) {
  if (!sessionId) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(sessionId)}/title`, { method: "POST" });
    await refreshHistory();
  } catch { /* Title generation is best-effort and must not affect the conversation. */ }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "request_failed");
  return data;
}

function setReasoningEffort(value) {
  if ([...reasoningEffortSelect.options].some((option) => option.value === value)) {
    reasoningEffortSelect.value = value;
  }
}

async function refreshRuntimeInfo() {
  try {
    const runtime = await api("/api/runtime");
    const selected = reasoningEffortSelect.value;
    const efforts = runtime.reasoning_efforts || [];
    reasoningEffortSelect.replaceChildren(...efforts.map((effort) => {
      const option = document.createElement("option");
      option.value = effort;
      option.textContent = EFFORT_LABELS[effort] || effort;
      return option;
    }));
    reasoningEffortSelect.value = efforts.includes(selected)
      ? selected : runtime.default_reasoning_effort || "medium";
    reasoningEffortSelect.disabled = efforts.length === 0;
    providerModelLabel.textContent = runtime.model_name
      ? `模型：${runtime.model_name}` : "模型信息不可用";
    state.runtimeReady = Boolean(runtime.model_name && efforts.length);
  } catch {
    providerModelLabel.textContent = "模型信息暂不可用";
    reasoningEffortSelect.disabled = true;
    state.runtimeReady = false;
  }
}

async function streamApi(path, body, pending) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || "request_failed");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let streamedText = "";
  let renderTimer = null;

  function appendStreamText(text) {
    streamedText += text;
    if (renderTimer !== null) return;
    // Batch token deltas; always parse the accumulated source, not rendered HTML.
    renderTimer = setTimeout(() => {
      renderTimer = null;
      const target = pending.querySelector(".answer-content");
      renderRichText(target, streamedText, { diagrams: false });
      pending.scrollIntoView({ block: "nearest" });
    }, 100);
  }

  function dispatch(frame) {
    let event = "message";
    const data = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return false;
    const payload = JSON.parse(data.join("\n"));
    if (event === "status") {
      setRunStatus(pending, payload.message);
    } else if (event === "progress") {
      appendRunStage(pending, payload.stage);
    } else if (event === "tool" || event === "skill") {
      appendRunActivity(pending, event, payload);
    } else if (event === "delta") {
      appendStreamText(payload.text);
    } else if (event === "complete") {
      result = payload;
    } else if (event === "session") {
      if (/^[a-f0-9]{32}$/.test(payload.session_id || "")) rememberSession(payload.session_id);
    } else if (event === "error") {
      throw new Error(payload.code || "request_failed");
    }
    return event === "delta";
  }

  function waitForPaint() {
    return new Promise((resolve) => requestAnimationFrame(resolve));
  }

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const shouldPaint = dispatch(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (shouldPaint) await waitForPaint();
      }
      if (done) break;
    }
    if (buffer.trim() && dispatch(buffer)) await waitForPaint();
    if (!result) throw new Error("stream_incomplete");
    return result;
  } finally {
    // A queued preview must never overwrite the final response or error message.
    if (renderTimer !== null) clearTimeout(renderTimer);
    reader.releaseLock();
  }
}

async function sendMessage() {
  const value = input.value;
  if (!value.trim() || state.busy) return;
  if (!state.runtimeReady) {
    announce("模型配置尚未就绪，请稍后重试");
    return;
  }
  if (value.length > MAX_LENGTH) {
    announce("输入超过 10,000 字符");
    return;
  }
  state.busy = true;
  $("#send-button").disabled = true;
  reasoningEffortSelect.disabled = true;
  const reasoningEffort = reasoningEffortSelect.value;
  makeMessage("user", value);
  state.messages.push({ role: "user", text: value });
  input.value = "";
  resizeInput();
  const pending = makePendingMessage();
  announce("正在准备回复");
  try {
    const response = state.awaitingClarification && state.activeId
      ? await streamApi(`/api/analyses/${encodeURIComponent(state.activeId)}/clarify/stream`, {
        answer: value, reasoning_effort: reasoningEffort,
      }, pending)
      : await streamApi("/api/analyses/stream", {
        text: value, reasoning_effort: reasoningEffort,
        ...(state.sessionId ? { session_id: state.sessionId } : {}),
      }, pending);
    renderResponse(response, pending);
  } catch (error) {
    pending.className = "message assistant failed";
    pending.querySelector(".answer-content").textContent =
      "这次暂时无法完成。请稍后再试；当前对话内容仍保留。";
    finishRun(pending, true);
    announce("请求失败，请稍后再试");
  } finally {
    state.busy = false;
    $("#send-button").disabled = false;
    reasoningEffortSelect.disabled = !state.runtimeReady;
    input.focus();
  }
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  $("#counter").textContent = `${input.value.length} / ${MAX_LENGTH}`;
}

function resetConversation() {
  if (state.busy) return;
  state.activeId = null;
  rememberSession(null);
  state.awaitingClarification = false;
  state.messages = [];
  messages.replaceChildren();
  $("#welcome").hidden = false;
  input.value = "";
  resizeInput();
  input.focus();
}

async function refreshHistory() {
  try {
    const { items = [] } = await api("/api/sessions");
    const pinned = items.filter((item) => item.pinned && !item.archived);
    const recent = items.filter((item) => !item.pinned && !item.archived);
    const archived = items.filter((item) => item.archived);
    const pinnedSection = $("#pinned-section");
    const archivedGroup = $("#archived-group");
    const archivedCount = $("#archive-count");

    pinnedSection.hidden = pinned.length === 0;
    archivedGroup.hidden = archived.length === 0;
    archivedCount.textContent = archived.length ? String(archived.length) : "";
    renderHistoryItems($("#pinned-history"), pinned);
    renderHistoryItems(history, recent);
    renderHistoryItems($("#archived-history"), archived);
  } catch { /* History remains available after transient API errors. */ }
}

function renderHistoryItems(target, items) {
  target.replaceChildren();
  for (const session of items) {
    const row = document.createElement("div");
    row.className = "history-row";
    row.dataset.sessionId = session.session_id;

    const openButton = document.createElement("button");
    openButton.className = `history-item ${session.session_id === state.sessionId ? "active" : ""}`;
    openButton.type = "button";
    openButton.addEventListener("click", () => openHistory(session.session_id));
    const title = document.createElement("span");
    title.className = "history-title";
    title.textContent = session.title || new Date(session.updated_at).toLocaleDateString("zh-CN");
    openButton.append(title);
    if (session.pinned) {
      const marker = document.createElement("span");
      marker.className = "history-pin";
      marker.textContent = "置顶";
      marker.setAttribute("aria-label", "已置顶");
      openButton.append(marker);
    }
    openButton.setAttribute("aria-label", `${title.textContent}，${session.status}`);

    const moreButton = document.createElement("button");
    moreButton.className = "history-more";
    moreButton.type = "button";
    moreButton.textContent = "⋯";
    moreButton.setAttribute("aria-label", `${title.textContent}的操作`);
    moreButton.setAttribute("aria-haspopup", "menu");
    moreButton.addEventListener("click", (event) => {
      event.stopPropagation();
      const rect = moreButton.getBoundingClientRect();
      showSessionMenu(session, rect.left, rect.bottom, moreButton);
    });

    row.append(openButton, moreButton);
    row.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      const trigger = row.querySelector(".history-more");
      showSessionMenu(session, event.clientX, event.clientY, trigger);
    });
    target.append(row);
  }
  if (items.length === 0 && target === history) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "还没有记录";
    target.append(empty);
  }
}

function showSessionMenu(session, x, y, invoker) {
  if (state.busy) return;
  menuSession = session;
  menuInvoker = invoker;
  sessionMenu.querySelector('[data-action="pin"]').textContent = session.pinned ? "取消置顶" : "置顶";
  sessionMenu.querySelector('[data-action="archive"]').textContent = session.archived ? "取消归档" : "归档";
  sessionMenu.hidden = false;
  const left = Math.max(8, Math.min(x, window.innerWidth - sessionMenu.offsetWidth - 8));
  const top = Math.max(8, Math.min(y, window.innerHeight - sessionMenu.offsetHeight - 8));
  sessionMenu.style.left = `${left}px`;
  sessionMenu.style.top = `${top}px`;
  sessionMenu.querySelector("button").focus();
}

function hideSessionMenu(returnFocus = false) {
  if (sessionMenu.hidden) return;
  sessionMenu.hidden = true;
  if (returnFocus && menuInvoker?.isConnected) menuInvoker.focus();
  menuSession = null;
  menuInvoker = null;
}

async function applySessionAction(action) {
  const session = menuSession;
  if (!session || state.busy) return;
  hideSessionMenu();
  const path = `/api/sessions/${encodeURIComponent(session.session_id)}`;
  try {
    if (action === "delete") {
      if (!window.confirm("删除此会话及其中的全部记录？此操作无法撤销。")) return;
      await api(path, { method: "DELETE" });
      if (state.sessionId === session.session_id) resetConversation();
      announce("会话已删除");
    } else {
      const changes = action === "pin"
        ? { pinned: !session.pinned }
        : { archived: !session.archived };
      await api(path, { method: "PATCH", body: JSON.stringify(changes) });
      announce(action === "pin"
        ? (changes.pinned ? "会话已置顶" : "已取消置顶")
        : (changes.archived ? "会话已归档" : "已取消归档"));
    }
    await refreshHistory();
  } catch {
    announce("会话操作失败，请重试");
  }
}

async function openHistory(sessionId) {
  if (state.busy) return;
  state.busy = true;
  try {
    let page = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    let records = page.items;
    while (page.next_before !== null && page.next_before !== undefined) {
      page = await api(`/api/sessions/${encodeURIComponent(sessionId)}?before=${page.next_before}`);
      records = [...page.items, ...records];
    }
    state.busy = false;
    resetConversation();
    for (const record of records) {
      makeMessage("user", record.original_input);
      if (record.clarification_question && record.status !== "waiting_clarification") {
        makeMessage("assistant", record.clarification_question, "waiting_clarification");
      }
      if (record.clarification_answer) makeMessage("user", record.clarification_answer);
      renderResponse(record, null, true);
    }
    const latest = records.at(-1);
    if (latest && ["completed", "waiting_clarification"].includes(latest.status)) {
      void ensureSessionTitle(sessionId);
    }
    refreshHistory();
    closeMenu();
    return true;
  } catch { announce("无法打开这条记录"); return false; }
  finally { state.busy = false; }
}

async function restoreActiveSession() {
  let sessionId = null;
  try { sessionId = sessionStorage.getItem(ACTIVE_SESSION_KEY); } catch { return; }
  if (!sessionId) return;
  if (!/^[a-f0-9]{32}$/.test(sessionId) || !(await openHistory(sessionId))) {
    rememberSession(null);
  }
}

function openMenu() {
  hideSessionMenu();
  $("#sidebar").classList.add("open");
  $("#scrim").hidden = false;
  $("#close-menu").focus();
}
function closeMenu() {
  $("#sidebar").classList.remove("open");
  $("#scrim").hidden = true;
  $("#open-menu").focus();
}

$("#composer").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
input.addEventListener("input", resizeInput);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
});
$("#new-chat").addEventListener("click", resetConversation);
$("#open-menu").addEventListener("click", openMenu);
$("#close-menu").addEventListener("click", closeMenu);
$("#scrim").addEventListener("click", closeMenu);
sessionMenu.addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action) applySessionAction(action);
});
sessionMenu.addEventListener("keydown", (event) => {
  const buttons = [...sessionMenu.querySelectorAll("button")];
  const index = buttons.indexOf(document.activeElement);
  if (event.key === "Escape") {
    event.preventDefault();
    hideSessionMenu(true);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const delta = event.key === "ArrowDown" ? 1 : -1;
    buttons[(index + delta + buttons.length) % buttons.length].focus();
  } else if (event.key === "Home" || event.key === "End") {
    event.preventDefault();
    buttons[event.key === "Home" ? 0 : buttons.length - 1].focus();
  }
});
document.addEventListener("pointerdown", (event) => {
  if (!sessionMenu.hidden && !sessionMenu.contains(event.target)) hideSessionMenu();
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideSessionMenu(true);
    closeMenu();
  }
});
refreshHistory();
refreshRuntimeInfo();
restoreActiveSession();
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshRuntimeInfo();
});
resizeInput();

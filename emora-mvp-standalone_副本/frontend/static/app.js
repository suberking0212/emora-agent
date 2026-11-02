const MAX_LENGTH = 10000;
const state = { activeId: null, awaitingClarification: false, busy: false, messages: [], runtimeReady: false };
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
  deep_analysis: "梳理分析要点",
  analysis_summary_safety_check: "检查分析提要",
  response_generation: "生成回复",
  output_safety_check: "检查回复安全",
  result_persistence: "保存分析结果",
};
const $ = (selector) => document.querySelector(selector);
const input = $("#message-input");
const deepThinkingToggle = $("#deep-thinking");
const reasoningEffortSelect = $("#reasoning-effort");
const providerModelLabel = $("#provider-model");
const messages = $("#messages");
const history = $("#history");
const liveStatus = $("#status");

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
  body.textContent = text;
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
  liveText.textContent = "正在开始本次分析…";
  live.append(indicator, liveText);

  const answer = document.createElement("div");
  answer.className = "answer-content";

  const requestMeta = document.createElement("div");
  requestMeta.className = "request-meta";
  requestMeta.hidden = true;

  const analysis = document.createElement("details");
  analysis.className = "analysis-details";
  analysis.open = true;
  analysis.hidden = true;
  const analysisLabel = document.createElement("summary");
  analysisLabel.textContent = "分析提要";
  const analysisText = document.createElement("div");
  analysisText.className = "analysis-text";
  analysis.append(analysisLabel, analysisText);

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

  body.append(live, analysis, answer, requestMeta, details);
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

function appendAnalysisText(message, text) {
  const analysis = message.querySelector(".analysis-details");
  const target = message.querySelector(".analysis-text");
  if (!analysis || !target) return;
  analysis.hidden = false;
  target.textContent += text;
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
  const analysis = message.querySelector(".analysis-details");
  if (analysis) analysis.open = false;
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
  const pieces = [
    ["情境概括", result.summary],
    ["主要情绪", (result.emotions || []).join("、")],
    ["核心矛盾", result.conflict],
    ["你可能在意的", result.user_needs],
    ["可以尝试", (result.suggestions || []).map((item, index) => `${index + 1}. ${item}`).join("\n")],
    ["下一步", result.next_step],
    ["安全说明", result.safety_note],
  ].filter(([, value]) => value);
  return pieces.map(([heading, value]) => `${heading}\n${value}`).join("\n\n");
}

function statusText(response) {
  if (response.status === "blocked") {
    return response.error_code === "unsafe_input_seek_support"
      ? "我很关心你现在的处境。如果你或他人可能正面临现实危险，请联系当地急救服务、危机热线或可信任的人。"
      : "这段内容目前无法继续进行普通分析。";
  }
  if (response.status === "failed") return "这次暂时无法完成。你的内容仍保留在当前对话中，可以稍后重新开始。";
  if (response.status === "waiting_clarification") return response.clarification_question;
  if (response.status === "completed") return resultText(response.result || {});
  return "";
}

function renderResponse(response, existingMessage = null) {
  state.activeId = response.id;
  deepThinkingToggle.checked = Boolean(response.deep_thinking);
  setReasoningEffort(response.reasoning_effort || "medium");
  state.awaitingClarification = response.status === "waiting_clarification";
  const message = existingMessage || makeMessage("assistant", "", response.status);
  message.className = `message assistant ${response.status}`;
  const answer = message.querySelector(".answer-content");
  if (answer) {
    answer.textContent = statusText(response);
    const requestMeta = message.querySelector(".request-meta");
    if (requestMeta && ["completed", "waiting_clarification"].includes(response.status)) {
      const effort = response.reasoning_effort || "medium";
      requestMeta.textContent = `本次模型请求档位：${EFFORT_LABELS[effort] || effort}`;
      requestMeta.hidden = false;
    }
    finishRun(message);
  } else {
    message.querySelector(".message-body").textContent = statusText(response);
  }
  if (["completed", "blocked", "failed"].includes(response.status)) {
    state.awaitingClarification = false;
    makeMessage("assistant", "", "new-chat-action");
    const button = document.createElement("button");
    button.className = "text-button";
    button.textContent = "新对话";
    button.addEventListener("click", resetConversation);
    messages.lastElementChild.querySelector(".message-body").append(button);
  }
  announce(response.status === "completed" ? "分析已完成" : response.status === "waiting_clarification" ? "需要补充一项信息" : statusText(response));
  refreshHistory();
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
    } else if (event === "analysis") {
      appendAnalysisText(pending, payload.text);
      pending.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (event === "delta") {
      const target = pending.querySelector(".answer-content");
      target.textContent += payload.text;
      pending.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (event === "complete") {
      result = payload;
    } else if (event === "error") {
      throw new Error(payload.code || "request_failed");
    }
    return event === "analysis" || event === "delta";
  }

  function waitForPaint() {
    return new Promise((resolve) => requestAnimationFrame(resolve));
  }

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
  deepThinkingToggle.disabled = true;
  reasoningEffortSelect.disabled = true;
  const deepThinking = deepThinkingToggle.checked;
  const reasoningEffort = reasoningEffortSelect.value;
  makeMessage("user", value);
  state.messages.push({ role: "user", text: value });
  input.value = "";
  resizeInput();
  const pending = makePendingMessage();
  pending.querySelector(".analysis-details").hidden = !deepThinking;
  announce("正在开始本次分析");
  try {
    const response = state.awaitingClarification && state.activeId
      ? await streamApi(`/api/analyses/${encodeURIComponent(state.activeId)}/clarify/stream`, {
        answer: value, deep_thinking: deepThinking, reasoning_effort: reasoningEffort,
      }, pending)
      : await streamApi("/api/analyses/stream", {
        text: value, deep_thinking: deepThinking, reasoning_effort: reasoningEffort,
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
    deepThinkingToggle.disabled = false;
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
  state.activeId = null;
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
    const { items } = await api("/api/analyses");
    history.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "history-empty";
      empty.textContent = "还没有记录";
      history.append(empty);
      return;
    }
    for (const item of items) {
      const button = document.createElement("button");
      button.className = `history-item ${item.id === state.activeId ? "active" : ""}`;
      button.type = "button";
      button.textContent = item.result?.summary || new Date(item.created_at).toLocaleDateString("zh-CN");
      button.setAttribute("aria-label", `${button.textContent}，${item.status}`);
      button.addEventListener("click", () => openHistory(item.id));
      history.append(button);
    }
  } catch { /* History remains available after transient API errors. */ }
}

async function openHistory(id) {
  try {
    const response = await api(`/api/analyses/${encodeURIComponent(id)}`);
    resetConversation();
    state.activeId = response.id;
    deepThinkingToggle.checked = Boolean(response.deep_thinking);
    setReasoningEffort(response.reasoning_effort || "medium");
    makeMessage("user", response.original_input);
    if (response.clarification_question) makeMessage("assistant", response.clarification_question, "waiting_clarification");
    if (response.result) renderResponse(response);
    else if (response.status !== "waiting_clarification") renderResponse(response);
    else {
      state.awaitingClarification = true;
      makeMessage("assistant", "正在等待补充信息。", "waiting_clarification");
    }
    closeMenu();
  } catch { announce("无法打开这条记录"); }
}

function openMenu() {
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
window.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
refreshHistory();
refreshRuntimeInfo();
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshRuntimeInfo();
});
resizeInput();

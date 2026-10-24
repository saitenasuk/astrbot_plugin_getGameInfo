import { apiGet, apiPost, ready, translate } from "./api.js";
import {
  closeModal,
  copyText,
  escapeHtml,
  formatDateTime,
  formatNumber,
  formatTime,
  openModal,
  setText,
  showToast,
} from "./ui.js";
import { renderOverview } from "./pages/overview.js";
import { renderSubscriptions } from "./pages/subscriptions.js";
import { renderGames } from "./pages/games.js";
import { renderEvents } from "./pages/events.js";
import { renderMaintenance } from "./pages/maintenance.js";

const initialTab = location.hash.replace(/^#/, "");
const state = {
  tab: ["overview", "subscriptions", "games", "events", "maintenance"].includes(
    initialTab,
  )
    ? initialTab
    : "overview",
  status: null,
  subscriptions: null,
  events: null,
  games: null,
  gamesQuery: "",
  gamesLoading: false,
  eventsFilter: "all",
  eventsQuery: "",
  checking: false,
  checkMode: null,
  countdownBase: null,
  countdownFetchedAt: 0,
  lastUpdated: null,
};

const TAB_NAMES = new Set([
  "overview",
  "subscriptions",
  "games",
  "events",
  "maintenance",
]);

function showError(message) {
  const banner = document.getElementById("error-banner");
  const text = document.getElementById("error-text");
  if (banner && text) {
    text.textContent = message;
    banner.classList.remove("hidden");
  }
}

function hideError() {
  document.getElementById("error-banner")?.classList.add("hidden");
}

function formatRemaining(seconds) {
  const total = Math.max(0, Math.ceil(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${secs} 秒`;
  return `${secs} 秒`;
}

async function loadStatus() {
  const data = await apiGet("status");
  state.status = data;
  state.countdownBase = data?.schedule?.seconds_until_next ?? null;
  state.countdownFetchedAt = Date.now();
  state.lastUpdated = new Date();
}

async function loadEvents() {
  const data = await apiGet("events", { limit: 200 });
  state.events = data?.items || [];
}

async function loadSubscriptions() {
  const data = await apiGet("subscriptions");
  state.subscriptions = data;
}

async function loadGames(page = 1, force = false) {
  state.gamesLoading = true;
  render();
  try {
    const data = await apiGet("games", {
      page,
      force: force ? 1 : 0,
    });
    state.games = data;
    state.gamesQuery = "";
    hideError();
  } catch (error) {
    showError(error.message);
    showToast(error.message, "error");
  } finally {
    state.gamesLoading = false;
    render();
  }
}

async function searchGames(query) {
  const keyword = String(query || "").trim();
  if (!keyword) {
    await loadGames(1, true);
    return;
  }
  if (keyword.length > 100) {
    showToast("搜索关键词不能超过 100 个字符", "warning");
    return;
  }
  state.gamesLoading = true;
  render();
  try {
    const data = await apiGet("games/search", { q: keyword });
    state.games = {
      ...data,
      page: 1,
      total_pages: 1,
      fetched_at: data?.fetched_at || null,
      cached: false,
    };
    state.gamesQuery = keyword;
    hideError();
  } catch (error) {
    showError(error.message);
    showToast(error.message, "error");
  } finally {
    state.gamesLoading = false;
    render();
  }
}

function clearGameSearch() {
  state.gamesQuery = "";
  loadGames(1, true);
}

function renderGameModalBody(game, detail, error, loading) {
  if (loading) {
    return '<div class="loading-block">正在读取游戏详情…</div>';
  }
  if (error) {
    return `<p class="badge badge-danger">${escapeHtml(error)}</p>`;
  }
  const counts = [];
  if (detail?.coop_players != null) {
    counts.push(`合作人数 ${detail.coop_players}`);
  }
  if (detail?.multi_players != null) {
    counts.push(`多人人数 ${detail.multi_players}`);
  }
  return `
    ${
      game.poster_url
        ? `<img src="${escapeHtml(
            game.poster_url,
          )}" alt="" loading="lazy" referrerpolicy="no-referrer"
             style="width:100%;border-radius:8px;border:1px solid var(--line);display:block" />`
        : ""
    }
    <h3 style="margin:14px 0 6px">${escapeHtml(game.title)}</h3>
    <dl class="detail-list">
      <dt>发布日期</dt><dd>${escapeHtml(game.release_date || "—")}</dd>
      <dt>网站发布</dt><dd>${escapeHtml(game.post_date || "—")}</dd>
      <dt>平台</dt><dd>${escapeHtml((game.platforms || []).join(" / ") || "—")}</dd>
      <dt>模式</dt><dd>${escapeHtml((game.modes || []).join("、") || "—")}</dd>
      <dt>玩家人数</dt><dd>${
        counts.length ? escapeHtml(counts.join(" · ")) : "未在详情页找到"
      }</dd>
      <dt>更新情况</dt><dd>${escapeHtml(game.update_info || "—")}</dd>
      <dt>详情链接</dt>
      <dd class="mono">${escapeHtml(game.url || "—")}</dd>
    </dl>`;
}

async function openGameDetail(game) {
  const overlay = openModal(
    "游戏详情",
    renderGameModalBody(game, null, null, true),
    `<button class="button button-small" type="button" data-action="copy-link">复制详情链接</button>
     <button class="button button-small button-primary" type="button" data-modal-close>关闭</button>`,
  );
  overlay
    ?.querySelector('[data-action="copy-link"]')
    ?.addEventListener("click", () => copy(game.url || ""));
  try {
    const detail = await apiGet("games/detail", { url: game.url });
    const body = document.querySelector("#modal-overlay .modal-body");
    if (body) {
      body.innerHTML = renderGameModalBody(game, detail, null, false);
    }
  } catch (error) {
    const body = document.querySelector("#modal-overlay .modal-body");
    if (body) {
      body.innerHTML = renderGameModalBody(game, null, error.message, false);
    }
  }
}

function renderCheckResult(result, mode = "dry_run") {
  const parts = [];
  if (result.baseline_missing) {
    parts.push(`
      <div class="banner" style="margin:0 0 12px;background:var(--amber-soft);border-color:var(--amber);color:var(--amber)">
        还没有 seen 基线。${mode === "push" ? "本次检查只建立基线，不会推送。" : "本次仅检查不会建立基线，也不会推送。"}
      </div>`);
  }
  const items = result.new_games || [];
  if (items.length) {
    parts.push(`
      <p>${mode === "push" ? "本次涉及" : "如果现在推送，会发送"}以下
        <strong>${items.length}</strong> 款新游戏：</p>
      <ul style="margin:8px 0 0;padding-left:20px">
        ${items
          .map(
            (item) =>
              `<li>${escapeHtml(item.title)} <span class="faint mono">(${escapeHtml(
                item.id,
              )})</span></li>`,
          )
          .join("")}
      </ul>`);
  } else if (!result.baseline_missing) {
    parts.push("<p>没有发现新游戏。</p>");
  }
  if (mode === "push") {
    parts.push(`
      <div class="summary-chips" style="margin-top:12px">
        <span class="badge badge-success">推送成功 ${result.pushes || 0}</span>
        <span class="badge ${
          result.failures ? "badge-danger" : "badge-muted"
        }">推送失败 ${result.failures || 0}</span>
        <span class="badge badge-muted">跳过 ${result.skipped || 0} 个订阅</span>
        ${
          result.downgraded
            ? `<span class="badge badge-warning">纯文字降级 ${result.downgraded}</span>`
            : ""
        }
      </div>`);
    if (result.failures) {
      parts.push(
        '<p class="badge badge-danger" style="margin-top:10px">有群推送失败，详情见「事件」页。</p>',
      );
    }
  } else {
    parts.push(
      '<p class="muted" style="margin-top:12px">仅检查模式不会发送消息，也不会修改 seen 文件。</p>',
    );
  }
  return parts.join("");
}

function confirmModal({ title, bodyHtml, confirmText = "确认", tone = "button-primary", onConfirm }) {
  const overlay = openModal(
    title,
    bodyHtml,
    `<button class="button" type="button" data-confirm-cancel>取消</button>
     <button class="button ${tone}" type="button" data-confirm-ok>${escapeHtml(
       confirmText,
     )}</button>`,
  );
  overlay
    ?.querySelector("[data-confirm-cancel]")
    ?.addEventListener("click", closeModal);
  overlay?.querySelector("[data-confirm-ok]")?.addEventListener("click", async () => {
    closeModal();
    await onConfirm();
  });
}

async function executeCheck(mode) {
  state.checking = true;
  state.checkMode = mode;
  renderChrome();
  try {
    const result = await apiPost("check", { mode });
    const title = mode === "push" ? "检查并推送结果" : "仅检查结果";
    openModal(
      title,
      renderCheckResult(result, mode),
      '<button class="button button-primary" type="button" data-modal-close>知道了</button>',
    );
    if (mode === "push") {
      showToast(
        `推送完成：成功 ${result.pushes || 0}，失败 ${result.failures || 0}`,
        result.failures ? "warning" : "success",
      );
    } else {
      showToast("检查完成，未发送任何消息", "success");
    }
    await Promise.allSettled([loadStatus(), loadEvents()]);
    if (state.tab === "subscriptions") {
      await loadSubscriptions().catch(() => {});
    }
    render();
  } catch (error) {
    showError(error.message);
    showToast(error.message, "error");
  } finally {
    state.checking = false;
    state.checkMode = null;
    renderChrome();
  }
}

async function runCheck(mode = "dry_run") {
  if (state.checking || state.status?.runtime?.checking) return;
  if (!state.status?.runtime?.scraper_ready) {
    showToast("插件尚未完成初始化，请稍后再试", "warning");
    return;
  }
  if (mode === "push") {
    const count = state.status?.counts?.subscriptions || 0;
    if (!count) {
      showToast("还没有订阅群，无法推送", "warning");
      return;
    }
    confirmModal({
      title: "检查并推送",
      bodyHtml: `
        <p>将立即检查站点，并把新游戏推送到 <strong>${escapeHtml(String(count))}</strong> 个订阅群。</p>
        <p class="muted">如果还没有 seen 基线，本次只会建立基线，不会发送消息。</p>
        <p class="muted">推送会真实发送群消息且无法撤回，请确认后再继续。</p>`,
      confirmText: "开始推送",
      onConfirm: () => executeCheck("push"),
    });
    return;
  }
  await executeCheck("dry_run");
}

function runDryCheck() {
  return runCheck("dry_run");
}

function subscriptionPayload(item) {
  const payload = { group_id: item.group_id };
  if (item.platform_id) payload.platform_id = item.platform_id;
  return payload;
}

function testPush(item) {
  confirmModal({
    title: "测试推送",
    bodyHtml: `
      <p>向群 <span class="mono">${escapeHtml(item.group_id)}</span> 发送一条测试文字消息？</p>
      <p class="muted">将使用 ${escapeHtml(
        item.resolved_platform_id || item.platform_id || "当前机器人",
      )} 账号发送，不会附带游戏封面。</p>`,
    confirmText: "发送测试消息",
    onConfirm: async () => {
      try {
        const result = await apiPost("subscriptions/test", subscriptionPayload(item));
        showToast(
          `测试消息已发送（${result.platform_id || "未知账号"}）`,
          "success",
        );
        await loadEvents();
        renderChrome();
        if (state.tab === "events") render();
      } catch (error) {
        await loadEvents().catch(() => {});
        showError(error.message);
        showToast(error.message, "error");
        if (state.tab === "events" || state.tab === "maintenance") render();
      }
    },
  });
}

function removeSubscription(item) {
  confirmModal({
    title: "取消订阅",
    bodyHtml: `
      <p>确定取消群 <span class="mono">${escapeHtml(
        item.group_id,
      )}</span> 的订阅？</p>
      <p class="muted">取消后该群不会再收到新游戏推送；如果同群还绑定了其他账号，也会一并移除。</p>`,
    confirmText: "取消订阅",
    tone: "button-danger",
    onConfirm: async () => {
      try {
        await apiPost("subscriptions/remove", { group_id: item.group_id });
        showToast("已取消该群订阅", "success");
        await Promise.allSettled([
          loadStatus(),
          loadEvents(),
          loadSubscriptions(),
        ]);
        render();
      } catch (error) {
        showError(error.message);
        showToast(error.message, "error");
      }
    },
  });
}

function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function typedConfirmModal({
  title,
  bodyHtml,
  required,
  confirmText,
  tone = "button-primary",
  onConfirm,
}) {
  const overlay = openModal(
    title,
    `${bodyHtml}
     <label class="field-label" for="typed-confirm-input">
       输入 <span class="mono">${escapeHtml(required)}</span> 确认
     </label>
     <input class="search-input" id="typed-confirm-input"
       autocomplete="off" spellcheck="false" />`,
    `<button class="button" type="button" data-typed-cancel>取消</button>
     <button class="button ${tone}" type="button" data-typed-ok disabled>
       ${escapeHtml(confirmText)}
     </button>`,
  );
  const input = overlay.querySelector("#typed-confirm-input");
  const okButton = overlay.querySelector("[data-typed-ok]");
  input?.addEventListener("input", () => {
    okButton.disabled = input.value.trim() !== required;
  });
  overlay
    ?.querySelector("[data-typed-cancel]")
    ?.addEventListener("click", closeModal);
  okButton?.addEventListener("click", async () => {
    if (input?.value.trim() !== required) return;
    closeModal();
    await onConfirm();
  });
  window.setTimeout(() => input?.focus(), 30);
}

async function refreshAfterMutation() {
  await Promise.allSettled([loadStatus(), loadEvents()]);
  if (state.subscriptions) {
    await loadSubscriptions().catch(() => {});
  }
  render();
}

function resetBaseline() {
  const tracked = state.status?.counts?.tracked_games ?? 0;
  typedConfirmModal({
    title: "重置 seen 基线",
    bodyHtml: `
      <p>将抓取站点当前列表并写入 <span class="mono">seen_games.json</span>，
      之后这些游戏不会再被当作新游戏。<strong>不会发送任何消息。</strong></p>
      <p class="muted">当前追踪 ${escapeHtml(String(tracked))} 款。如果站点暂时异常，请不要重置。</p>`,
    required: "RESET",
    confirmText: "重置基线",
    onConfirm: async () => {
      try {
        const result = await apiPost("maintenance/seen", {
          action: "reset",
          confirm: "RESET",
        });
        showToast(result.message || "seen 基线已重置", "success");
        await refreshAfterMutation();
      } catch (error) {
        showError(error.message);
        showToast(error.message, "error");
      }
    },
  });
}

function clearBaseline() {
  typedConfirmModal({
    title: "清空 seen 基线",
    bodyHtml: `
      <p class="badge badge-danger">危险操作</p>
      <p>清空后，下一次检查会把站点当前<strong>全部游戏</strong>当作新游戏推送，
      可能一次发送大量消息且无法撤回。</p>
      <p class="muted">如果只想重新记录当前列表而不推送，请使用「重置基线」。</p>`,
    required: "CLEAR",
    confirmText: "清空 seen",
    tone: "button-danger",
    onConfirm: async () => {
      try {
        const result = await apiPost("maintenance/seen", {
          action: "clear",
          confirm: "CLEAR",
        });
        showToast(result.message || "seen 基线已清空", "warning");
        await refreshAfterMutation();
      } catch (error) {
        showError(error.message);
        showToast(error.message, "error");
      }
    },
  });
}

async function exportDiagnostics() {
  try {
    const data = await apiGet("diagnostics");
    const stamp = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .slice(0, 19);
    downloadJson(data, `getGameInfo-diagnostics-${stamp}.json`);
    showToast("诊断 JSON 已导出", "success");
  } catch (error) {
    showError(error.message);
    showToast(error.message, "error");
  }
}

async function probeHealth() {
  try {
    const data = await apiGet("health", { force: 1 });
    if (data.reachable) {
      showToast(
        `站点正常：HTTP ${data.status_code ?? "?"} · ${data.latency_ms ?? "?"} ms`,
        "success",
      );
    } else {
      showToast(
        `站点异常：${data.error || data.status_code || "无法连接"}`,
        "error",
      );
    }
    await loadStatus();
    render();
  } catch (error) {
    showError(error.message);
    showToast(error.message, "error");
  }
}

function openAddSubscription() {
  const accounts = (state.status?.accounts || []).filter(
    (account) => account.platform_id,
  );
  if (!accounts.length) {
    showToast("没有可用的 OneBot 账号", "warning");
    return;
  }
  let groups = [];
  const overlay = openModal(
    "添加订阅",
    `<label class="field-label" for="add-account">选择机器人账号</label>
     <select class="search-input" id="add-account">
       ${accounts
         .map(
           (account) =>
             `<option value="${escapeHtml(account.platform_id)}">${escapeHtml(
               `${account.platform_id}${account.self_id ? ` · ${account.self_id}` : ""}`,
             )}</option>`,
         )
         .join("")}
     </select>
     <label class="field-label" for="add-group">选择群</label>
     <div id="add-group-wrap">
       <div class="loading-block">正在读取群列表…</div>
     </div>`,
    `<button class="button" type="button" data-add-cancel>取消</button>
     <button class="button button-primary" type="button" data-add-confirm disabled>
       添加订阅
     </button>`,
  );
  const accountSelect = overlay.querySelector("#add-account");
  const groupWrap = overlay.querySelector("#add-group-wrap");
  const addButton = overlay.querySelector("[data-add-confirm]");

  async function loadGroupList() {
    groups = [];
    addButton.disabled = true;
    groupWrap.innerHTML = '<div class="loading-block">正在读取群列表…</div>';
    try {
      const data = await apiGet("groups", {
        platform_id: accountSelect.value,
      });
      groups = data.items || [];
      if (!groups.length) {
        groupWrap.innerHTML =
          '<p class="muted">该账号没有返回群列表，请确认机器人在群内且已登录。</p>';
        return;
      }
      groupWrap.innerHTML = `
        <select class="search-input" id="add-group">
          ${groups
            .map(
              (group) =>
                `<option value="${escapeHtml(group.group_id)}">${escapeHtml(
                  `${group.group_name || "未命名群"}（${group.group_id}）`,
                )}</option>`,
            )
            .join("")}
        </select>`;
      addButton.disabled = false;
    } catch (error) {
      groupWrap.innerHTML = `<p class="badge badge-danger">${escapeHtml(
        error.message,
      )}</p>`;
    }
  }

  accountSelect?.addEventListener("change", loadGroupList);
  overlay
    ?.querySelector("[data-add-cancel]")
    ?.addEventListener("click", closeModal);
  addButton?.addEventListener("click", async () => {
    const groupSelect = groupWrap.querySelector("#add-group");
    const groupId = groupSelect?.value;
    if (!groupId) return;
    addButton.disabled = true;
    try {
      const result = await apiPost("subscriptions/add", {
        group_id: groupId,
        platform_id: accountSelect.value,
      });
      closeModal();
      showToast(result.message || "已添加订阅", "success");
      await refreshAfterMutation();
    } catch (error) {
      addButton.disabled = false;
      showError(error.message);
      showToast(error.message, "error");
    }
  });
  loadGroupList();
}

function retryTestPush(event) {
  if (!event?.group_id) return;
  const body = `
    <p>重新向群 <span class="mono">${escapeHtml(event.group_id)}</span> 发送测试消息？</p>
    <p class="muted">上次结果：${escapeHtml(event.message || "测试推送失败")}</p>
    ${
      event.detail
        ? `<pre class="diagnostic-block">${escapeHtml(event.detail)}</pre>`
        : ""
    }`;
  confirmModal({
    title: "重试测试推送",
    bodyHtml: body,
    confirmText: "重试",
    onConfirm: async () => {
      try {
        await apiPost("subscriptions/test", {
          group_id: event.group_id,
          ...(event.platform_id ? { platform_id: event.platform_id } : {}),
        });
        showToast("测试消息已发送", "success");
        await loadEvents();
        render();
      } catch (error) {
        showError(error.message);
        showToast(error.message, "error");
      }
    },
  });
}

async function copy(text) {
  if (!text) return;
  const ok = await copyText(text);
  showToast(ok ? "已复制" : "复制失败，请手动选择文本", ok ? "success" : "warning");
}

function renderChrome() {
  const status = state.status;
  const runtime = status?.runtime;
  const pill = document.getElementById("runtime-pill");
  if (pill) {
    let text = "加载中";
    let tone = "muted";
    if (status) {
      if (state.checking || runtime?.checking) {
        text = "检查中";
        tone = "brand";
      } else if (runtime?.scraper_ready && runtime?.poll_task_running) {
        text = "运行中";
        tone = "success";
      } else if (!runtime?.scraper_ready) {
        text = "未初始化";
        tone = "warning";
      } else {
        text = "轮询已停止";
        tone = "danger";
      }
    }
    pill.className = `pill pill-${tone}`;
    pill.textContent = text;
  }

  if (status) {
    setText("subtitle", "轮询、订阅与推送状态；状态与事件每 10 秒刷新");
  }
  const plugin = status?.plugin || {};
  setText(
    "version-line",
    status
      ? `版本 ${plugin.version || "未知"}${
          plugin.astrbot_version ? ` · AstrBot ${plugin.astrbot_version}` : ""
        }`
      : "",
  );
  setText("updated-line", state.lastUpdated ? `更新于 ${formatTime(state.lastUpdated)}` : "");
  setText(
    "footer-status",
    status ? `订阅 ${status.counts?.subscriptions ?? 0} 个群` : "",
  );

  const tabCount = document.getElementById("tab-subscription-count");
  if (tabCount) {
    tabCount.textContent = status?.counts?.subscriptions
      ? String(status.counts.subscriptions)
      : "";
  }

  const kpi = document.getElementById("kpi-row");
  if (kpi) {
    const today = status?.counts?.today || {};
    kpi.innerHTML = [
      {
        label: "订阅群",
        value: status?.counts?.subscriptions ?? "—",
        hint: `${status?.counts?.accounts ?? 0} 个机器人账号`,
      },
      {
        label: "追踪游戏",
        value: status?.counts?.tracked_games ?? "—",
        hint: "seen 基线中的游戏数",
      },
      {
        label: "今日新游戏",
        value: today.new_games ?? "—",
        hint: "本日检测到的数量",
      },
      {
        label: "今日推送失败",
        value: today.failures ?? "—",
        hint: `成功推送 ${today.pushes ?? 0} 次`,
      },
    ]
      .map(
        (item) => `
        <div class="kpi-card">
          <div class="kpi-label">${item.label}</div>
          <div class="kpi-value">${
            typeof item.value === "number" ? formatNumber(item.value) : item.value
          }</div>
          <div class="kpi-hint">${escapeHtml(item.hint)}</div>
        </div>`,
      )
      .join("");
  }

  const busy = state.checking || runtime?.checking;
  const ready = Boolean(runtime?.scraper_ready);
  const checkButton = document.getElementById("check-button");
  if (checkButton) {
    checkButton.disabled = Boolean(busy || !ready);
    checkButton.textContent =
      state.checking && state.checkMode === "dry_run" ? "检查中…" : "仅检查";
  }
  const pushButton = document.getElementById("push-check-button");
  if (pushButton) {
    pushButton.disabled = Boolean(busy || !ready);
    pushButton.textContent =
      state.checking && state.checkMode === "push"
        ? "推送中…"
        : "检查并推送";
  }
  const refreshButton = document.getElementById("refresh-button");
  if (refreshButton) refreshButton.disabled = false;
}

function render() {
  renderChrome();
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.tab === state.tab);
  });
  const root = document.getElementById("content");
  if (!root) return;
  const handlers = {
    refreshAll,
    refreshEvents: async () => {
      try {
        await loadEvents();
        render();
      } catch (error) {
        showError(error.message);
      }
    },
    openTab: setTab,
    loadGames,
    searchGames,
    clearGameSearch,
    openGameDetail,
    runDryCheck,
    runPushCheck: () => runCheck("push"),
    testPush,
    removeSubscription,
    openAddSubscription,
    resetBaseline,
    clearBaseline,
    exportDiagnostics,
    probeHealth,
    retryTestPush,
    copy,
  };
  if (state.tab === "subscriptions") {
    renderSubscriptions(root, state, handlers);
  } else if (state.tab === "games") {
    renderGames(root, state, handlers);
  } else if (state.tab === "events") {
    renderEvents(root, state, handlers);
  } else if (state.tab === "maintenance") {
    renderMaintenance(root, state, handlers);
  } else {
    renderOverview(root, state, handlers);
  }
}

function updateCountdown() {
  const schedule = state.status?.schedule;
  const countdown = document.getElementById("next-check-countdown");
  const topLine = document.getElementById("countdown-line");
  if (schedule?.seconds_until_next == null) {
    if (countdown) countdown.textContent = "暂无计划";
    if (topLine) topLine.textContent = "";
    return;
  }
  const elapsed = (Date.now() - state.countdownFetchedAt) / 1000;
  const remaining = Math.max(0, schedule.seconds_until_next - elapsed);
  const text = remaining > 0 ? `还有 ${formatRemaining(remaining)}` : "即将检查";
  if (countdown) countdown.textContent = text;
  if (topLine) {
    topLine.textContent = schedule.next_check_at
      ? `下次检查 ${formatDateTime(schedule.next_check_at)} · ${text}`
      : text;
  }
}

function setTab(tab) {
  state.tab = TAB_NAMES.has(tab) ? tab : "overview";
  if (location.hash !== `#${state.tab}`) {
    try {
      history.replaceState(null, "", `#${state.tab}`);
    } catch {
      // Sandboxed iframes can reject History API writes; the view still works
      // without a shareable hash.
      location.hash = state.tab;
    }
  }
  if (state.tab === "subscriptions" && !state.subscriptions) {
    loadSubscriptions()
      .then(render)
      .catch((error) => showError(error.message));
  }
  if (state.tab === "games" && !state.games) {
    loadGames(1, false);
  }
  render();
}

async function refreshAll() {
  try {
    await Promise.all([loadStatus(), loadEvents()]);
    hideError();
  } catch (error) {
    showError(error.message);
  }
  if (state.tab === "subscriptions" && !state.subscriptions) {
    await loadSubscriptions().catch((error) => showError(error.message));
  }
  render();
}

async function refreshCurrent() {
  if (state.tab === "games") {
    await loadGames(state.games?.page || 1, true);
    return;
  }
  if (state.tab === "subscriptions") {
    const results = await Promise.allSettled([
      loadStatus(),
      loadEvents(),
      loadSubscriptions(),
    ]);
    const failed = results.find((item) => item.status === "rejected");
    if (failed) {
      showError(failed.reason?.message || "刷新失败");
    } else {
      hideError();
    }
    render();
    return;
  }
  await refreshAll();
}

async function autoRefresh() {
  if (document.visibilityState !== "visible") return;
  const activeTag = document.activeElement?.tagName;
  const typing = activeTag === "INPUT" || activeTag === "TEXTAREA";
  const tasks = [loadStatus(), loadEvents()];
  if (state.tab === "subscriptions") tasks.push(loadSubscriptions());
  const results = await Promise.allSettled(tasks);
  const firstError = results.find((item) => item.status === "rejected");
  if (firstError) {
    showError(firstError.reason?.message || "刷新失败");
  } else {
    hideError();
  }
  if (typing || state.tab === "games") {
    renderChrome();
    return;
  }
  render();
}

function bindEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  });
  document
    .getElementById("refresh-button")
    ?.addEventListener("click", () => refreshCurrent());
  document
    .getElementById("retry-button")
    ?.addEventListener("click", () => refreshCurrent());
  document
    .getElementById("check-button")
    ?.addEventListener("click", () => runDryCheck());
  document
    .getElementById("push-check-button")
    ?.addEventListener("click", () => runCheck("push"));
  window.addEventListener("hashchange", () => {
    const tab = location.hash.replace(/^#/, "");
    if (tab && tab !== state.tab) setTab(tab);
  });
}

async function boot() {
  bindEvents();
  try {
    await ready();
    document.title = translate("views.status.title", "运行状态");
  } catch (error) {
    setText("subtitle", "无法连接 AstrBot");
    showError(error.message);
    document.getElementById("content").innerHTML = `
      <div class="empty-state">
        <strong>无法连接 AstrBot</strong>
        <span>${escapeHtml(error.message)}</span>
      </div>`;
    return;
  }
  await refreshAll();
  if (state.tab === "games" && !state.games) {
    await loadGames(1, false);
  }
  updateCountdown();
  window.setInterval(updateCountdown, 1000);
  window.setInterval(autoRefresh, 10000);
}

boot();

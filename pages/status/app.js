import { apiGet, apiPost, ready, translate } from "./api.js";
import {
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

const initialTab = location.hash.replace(/^#/, "");
const state = {
  tab: ["overview", "subscriptions", "games", "events"].includes(initialTab)
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
  countdownBase: null,
  countdownFetchedAt: 0,
  lastUpdated: null,
};

const TAB_NAMES = new Set(["overview", "subscriptions", "games", "events"]);

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

function renderCheckResult(result) {
  if (result.baseline_missing) {
    return `
      <div class="banner" style="margin:0 0 12px;background:var(--amber-soft);border-color:var(--amber);color:var(--amber)">
        还没有 seen 基线。本次仅检查不会建立基线，也不会推送。
      </div>
      <p>下一次定时检查会先把当前列表记录为基线。</p>`;
  }
  const items = result.new_games || [];
  if (!items.length) {
    return '<p>没有发现新游戏，本次检查不会发送任何消息。</p>';
  }
  return `
    <p>如果现在推送，会发送以下 <strong>${items.length}</strong> 款新游戏：</p>
    <ul style="margin:8px 0 0;padding-left:20px">
      ${items
        .map(
          (item) =>
            `<li>${escapeHtml(item.title)} <span class="faint mono">(${escapeHtml(
              item.id,
            )})</span></li>`,
        )
        .join("")}
    </ul>
    <p class="muted" style="margin-top:12px">仅检查模式不会发送消息，也不会修改 seen 文件。</p>`;
}

async function runDryCheck() {
  if (state.checking || state.status?.runtime?.checking) return;
  if (!state.status?.runtime?.scraper_ready) {
    showToast("插件尚未完成初始化，请稍后再试", "warning");
    return;
  }
  state.checking = true;
  renderChrome();
  try {
    const result = await apiPost("check", { mode: "dry_run" });
    openModal(
      "仅检查结果",
      renderCheckResult(result),
      '<button class="button button-primary" type="button" data-modal-close>知道了</button>',
    );
    showToast("检查完成，未发送任何消息", "success");
    await Promise.allSettled([loadStatus(), loadEvents()]);
    render();
  } catch (error) {
    showError(error.message);
    showToast(error.message, "error");
  } finally {
    state.checking = false;
    renderChrome();
  }
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

  const checkButton = document.getElementById("check-button");
  if (checkButton) {
    const busy = state.checking || runtime?.checking;
    checkButton.disabled = Boolean(busy || !runtime?.scraper_ready);
    checkButton.textContent = state.checking ? "检查中…" : "仅检查";
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
    copy,
  };
  if (state.tab === "subscriptions") {
    renderSubscriptions(root, state, handlers);
  } else if (state.tab === "games") {
    renderGames(root, state, handlers);
  } else if (state.tab === "events") {
    renderEvents(root, state, handlers);
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

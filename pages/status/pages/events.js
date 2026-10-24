import {
  badge,
  emptyState,
  escapeHtml,
  formatDateTime,
  formatRelative,
  levelTone,
} from "../ui.js";

const LEVEL_LABELS = {
  info: "信息",
  warning: "警告",
  error: "错误",
};

const KIND_LABELS = {
  check_start: "开始检查",
  check_ok: "检查完成",
  check_error: "检查失败",
  new_games: "发现新游戏",
  push_ok: "推送成功",
  push_fail: "推送失败",
  push_fallback: "纯文字降级",
  push_skipped: "跳过推送",
  push_test: "测试推送",
  subscribe: "新增订阅",
  unsubscribe: "取消订阅",
  migrate: "订阅迁移",
};

function eventMatches(event, category, query) {
  const kind = String(event.kind || "");
  if (category === "check" && !kind.startsWith("check_")) return false;
  if (category === "push" && !kind.startsWith("push_")) return false;
  if (category === "error" && event.level !== "error") return false;
  if (query) {
    const needle = query.trim().toLowerCase();
    if (
      needle &&
      !String(event.message || "").toLowerCase().includes(needle)
    ) {
      return false;
    }
  }
  return true;
}

export function renderEventItem(event) {
  const level = event.level || "info";
  const meta = [];
  if (event.group_id) meta.push(`群 ${event.group_id}`);
  if (event.platform_id) meta.push(`平台 ${event.platform_id}`);
  if (event.count != null) meta.push(`数量 ${event.count}`);
  if (event.reason) meta.push(`原因 ${event.reason}`);
  if (event.retcode != null) meta.push(`retcode ${event.retcode}`);
  return `
    <li class="event-item level-${escapeHtml(level)}">
      <span class="event-time" title="${escapeHtml(
        formatDateTime(event.ts),
      )}">${escapeHtml(formatRelative(event.ts))}</span>
      <div class="event-body">
        <div class="event-message">
          ${badge(LEVEL_LABELS[level] || level, levelTone(level))}
          ${escapeHtml(event.message)}
        </div>
        <div class="event-meta">
          <span>${escapeHtml(KIND_LABELS[event.kind] || event.kind || "事件")}</span>
          ${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
        </div>
      </div>
    </li>`;
}

export function renderEvents(root, state, handlers) {
  const events = state.events || [];
  const filtered = events.filter((event) =>
    eventMatches(event, state.eventsFilter || "all", state.eventsQuery || ""),
  );
  root.innerHTML = `
    <div class="stack">
      <div class="filter-row">
        ${[
          ["all", "全部"],
          ["check", "检查"],
          ["push", "推送"],
          ["error", "错误"],
        ]
          .map(
            ([key, label]) => `
          <button class="filter-chip ${
            (state.eventsFilter || "all") === key ? "is-active" : ""
          }" type="button" data-event-filter="${key}">
            ${label}
          </button>`,
          )
          .join("")}
        <input
          class="search-input"
          id="event-search"
          type="search"
          placeholder="搜索事件内容"
          value="${escapeHtml(state.eventsQuery || "")}"
        />
        <button class="button button-small" type="button" data-action="refresh-events">
          刷新
        </button>
      </div>
      <section class="card">
        ${
          filtered.length
            ? `<ul class="event-list">${filtered
                .map(renderEventItem)
                .join("")}</ul>`
            : emptyState("没有匹配的事件", "调整筛选条件，或等待下一次检查。")
        }
      </section>
    </div>`;

  root.querySelectorAll("[data-event-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.eventsFilter = button.getAttribute("data-event-filter");
      renderEvents(root, state, handlers);
    });
  });
  const search = root.querySelector("#event-search");
  search?.addEventListener("input", () => {
    state.eventsQuery = search.value;
    renderEvents(root, state, handlers);
    const next = root.querySelector("#event-search");
    next?.focus();
    next?.setSelectionRange?.(next.value.length, next.value.length);
  });
  root
    .querySelector('[data-action="refresh-events"]')
    ?.addEventListener("click", () => handlers.refreshEvents());
}

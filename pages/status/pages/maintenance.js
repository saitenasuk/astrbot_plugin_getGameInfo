import {
  badge,
  escapeHtml,
  formatDateTime,
  formatDuration,
  formatUptime,
} from "../ui.js";

function renderBaseline(status) {
  const tracked = status.counts?.tracked_games ?? 0;
  return `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">seen 基线</h2>
          <p class="card-subtitle">当前追踪 ${escapeHtml(tracked)} 款游戏</p>
        </div>
        <span class="badge badge-muted">${escapeHtml(tracked)} 款</span>
      </div>
      <p class="muted">
        <strong>重置基线</strong>：抓取站点当前列表并记为 seen，不会发送消息。<br />
        <strong>清空基线</strong>：下次检查会把站点当前全部游戏当作新游戏推送，可能一次发送大量消息。
      </p>
      <div class="maintenance-actions">
        <button class="button" type="button" data-action="reset-baseline">
          重置基线
        </button>
        <button class="button button-danger" type="button" data-action="clear-baseline">
          清空 seen
        </button>
      </div>
      <p class="danger-note">两个操作都需要输入确认文本，清空后无法自动恢复。</p>
    </section>`;
}

function renderDiagnostics(status) {
  const health = status.health || {};
  return `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">诊断与导出</h2>
          <p class="card-subtitle">包含运行状态、订阅、事件和 seen 列表，不含凭据</p>
        </div>
      </div>
      <dl class="detail-list">
        <dt>站点探测</dt>
        <dd>
          ${badge(
            health.reachable === true
              ? `HTTP ${health.status_code ?? "200"}`
              : health.reachable === false
                ? "不可达"
                : "未探测",
            health.reachable === true
              ? "success"
              : health.reachable === false
                ? "danger"
                : "muted",
          )}
          ${
            health.latency_ms != null
              ? `<span class="faint"> · ${escapeHtml(
                  formatDuration(health.latency_ms),
                )}</span>`
              : ""
          }
          ${
            health.checked_at
              ? `<div class="faint">${escapeHtml(
                  formatDateTime(health.checked_at),
                )}</div>`
              : ""
          }
        </dd>
      </dl>
      <div class="maintenance-actions">
        <button class="button" type="button" data-action="probe-health">
          检测站点
        </button>
        <button class="button button-primary" type="button" data-action="export-diagnostics">
          导出诊断 JSON
        </button>
      </div>
    </section>`;
}

function renderSystem(status) {
  const runtime = status.runtime || {};
  const plugin = status.plugin || {};
  return `
    <section class="card">
      <div class="card-head"><h2 class="card-title">系统信息</h2></div>
      <dl class="detail-list">
        <dt>插件版本</dt><dd>${escapeHtml(plugin.version || "未知")}</dd>
        <dt>AstrBot</dt><dd>${escapeHtml(plugin.astrbot_version || "未知")}</dd>
        <dt>运行时长</dt><dd>${escapeHtml(formatUptime(runtime.uptime_seconds))}</dd>
        <dt>检查锁</dt><dd>${runtime.checking ? "占用中" : "空闲"}</dd>
        <dt>订阅 / 群</dt><dd>${escapeHtml(status.counts?.subscriptions ?? 0)} / ${escapeHtml(
          status.counts?.groups ?? 0,
        )}</dd>
        <dt>账号</dt><dd>${escapeHtml(status.counts?.accounts ?? 0)} 个</dd>
      </dl>
    </section>`;
}

function renderFailures(state, handlers) {
  const failures = (state.events || []).filter(
    (event) => event.kind === "push_test" && event.level !== "info",
  );
  if (!failures.length) {
    return `
      <section class="card">
        <div class="card-head"><h2 class="card-title">测试推送失败记录</h2></div>
        <p class="muted">暂无失败记录。失败后可在这里查看脱敏诊断并重试。</p>
      </section>`;
  }
  return `
    <section class="card">
      <div class="card-head">
        <h2 class="card-title">测试推送失败记录</h2>
        <span class="badge badge-warning">${failures.length} 条</span>
      </div>
      <div class="stack">
        ${failures
          .map(
            (event) => `
          <div class="event-item level-${escapeHtml(event.level)}">
            <span class="event-time">${escapeHtml(formatDateTime(event.ts))}</span>
            <div class="event-body">
              <div class="event-message">${escapeHtml(event.message)}</div>
              <div class="event-meta">
                ${event.group_id ? `<span>群 ${escapeHtml(event.group_id)}</span>` : ""}
                ${event.platform_id ? `<span>平台 ${escapeHtml(event.platform_id)}</span>` : ""}
                ${event.reason ? `<span>原因 ${escapeHtml(event.reason)}</span>` : ""}
                ${event.retcode != null ? `<span>retcode ${escapeHtml(event.retcode)}</span>` : ""}
              </div>
              ${
                event.detail
                  ? `<pre class="diagnostic-block">${escapeHtml(event.detail)}</pre>`
                  : ""
              }
              <button class="button button-small" type="button"
                data-retry-id="${escapeHtml(event.id)}">重试测试推送</button>
            </div>
          </div>`,
          )
          .join("")}
      </div>
    </section>`;
}

export function renderMaintenance(root, state, handlers) {
  const status = state.status;
  if (!status) {
    root.innerHTML = '<div class="loading-block">正在加载状态…</div>';
    return;
  }
  root.innerHTML = `
    <div class="stack">
      ${renderBaseline(status)}
      ${renderDiagnostics(status)}
      ${renderSystem(status)}
      ${renderFailures(state, handlers)}
    </div>`;

  root
    .querySelectorAll('[data-action="reset-baseline"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.resetBaseline()),
    );
  root
    .querySelectorAll('[data-action="clear-baseline"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.clearBaseline()),
    );
  root
    .querySelectorAll('[data-action="export-diagnostics"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.exportDiagnostics()),
    );
  root
    .querySelectorAll('[data-action="probe-health"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.probeHealth()),
    );
  root.querySelectorAll("[data-retry-id]").forEach((button) => {
    const event = (state.events || []).find(
      (item) => String(item.id) === button.getAttribute("data-retry-id"),
    );
    if (event) {
      button.addEventListener("click", () => handlers.retryTestPush(event));
    }
  });
}

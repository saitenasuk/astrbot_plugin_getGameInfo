import {
  badge,
  escapeHtml,
  formatDateTime,
  formatDuration,
  formatInterval,
  formatTime,
  formatUptime,
  pill,
} from "../ui.js";
import { renderEventItem } from "./events.js";

function triggerLabel(trigger) {
  return trigger === "manual" ? "手动" : "定时";
}

function renderSchedule(status) {
  const schedule = status.schedule || {};
  const runtime = status.runtime || {};
  const last = status.last_check;
  const health = status.health || {};
  const healthText =
    health.reachable === true
      ? "最近检查成功"
      : health.reachable === false
        ? "最近检查失败"
        : "暂无检查记录";
  const healthTone =
    health.reachable === true
      ? "success"
      : health.reachable === false
        ? "danger"
        : "muted";
  return `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">轮询计划</h2>
          <p class="card-subtitle">按整点对齐执行，不受插件启动时间影响</p>
        </div>
        ${pill(
          runtime.checking ? "检查进行中" : "等待下次检查",
          runtime.checking ? "brand" : "muted",
        )}
      </div>
      <div class="schedule-big">
        <span id="next-check-time" class="schedule-time">${
          schedule.next_check_at
            ? escapeHtml(formatTime(schedule.next_check_at))
            : "—"
        }</span>
        <span id="next-check-countdown" class="schedule-countdown">
          ${schedule.seconds_until_next != null ? "计算中…" : "暂无计划"}
        </span>
      </div>
      <dl class="detail-list" style="margin-top: 10px">
        <dt>检查间隔</dt>
        <dd>${escapeHtml(formatInterval(schedule.interval_seconds))}</dd>
        <dt>站点状态</dt>
        <dd>${badge(healthText, healthTone)}${
          health.latency_ms != null
            ? `<span class="faint"> · 最近耗时 ${escapeHtml(
                formatDuration(health.latency_ms),
              )}</span>`
            : ""
        }</dd>
        <dt>运行时长</dt>
        <dd>${escapeHtml(formatUptime(runtime.uptime_seconds))}</dd>
        <dt>最近检查</dt>
        <dd>${
          last
            ? `${escapeHtml(formatDateTime(last.finished_at))}（${escapeHtml(
                triggerLabel(last.trigger),
              )}${last.dry_run ? " · 仅检查" : ""}）`
            : "尚无记录"
        }</dd>
      </dl>
    </section>`;
}

function renderLastCheck(status) {
  const last = status.last_check;
  if (!last) {
    return `
      <section class="card">
        <div class="card-head"><h2 class="card-title">上次检查</h2></div>
        <p class="muted">插件启动后还没有执行过检查。下一次整点会自动执行，也可以点击右上角「仅检查」立即预检。</p>
      </section>`;
  }
  const resultTone = last.ok ? "success" : "danger";
  return `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">上次检查</h2>
          <p class="card-subtitle">${escapeHtml(
            formatDateTime(last.finished_at),
          )} · ${escapeHtml(triggerLabel(last.trigger))}${
            last.dry_run ? " · 仅检查" : ""
          }</p>
        </div>
        ${badge(last.ok ? "成功" : "失败", resultTone)}
      </div>
      <div class="summary-chips">
        <span class="badge badge-muted">耗时 ${escapeHtml(
          formatDuration(last.duration_ms),
        )}</span>
        <span class="badge badge-muted">抓取 ${escapeHtml(
          last.games_seen,
        )} 款</span>
        <span class="badge badge-brand">新增 ${escapeHtml(
          last.new_games,
        )} 款</span>
        ${
          last.dry_run
            ? ""
            : `<span class="badge badge-success">推送 ${escapeHtml(
                last.pushes,
              )}</span>
               <span class="badge ${
                 last.failures ? "badge-danger" : "badge-muted"
               }">失败 ${escapeHtml(last.failures)}</span>`
        }
        ${
          last.downgraded
            ? `<span class="badge badge-warning">纯文字降级 ${escapeHtml(
                last.downgraded,
              )}</span>`
            : ""
        }
        ${
          last.skipped
            ? `<span class="badge badge-warning">跳过 ${escapeHtml(
                last.skipped,
              )} 个订阅</span>`
            : ""
        }
      </div>
      ${
        last.baseline_missing
          ? '<p class="muted">本次检查时还没有 seen 基线，只记录了当前游戏，没有推送。</p>'
          : ""
      }
      ${
        last.error
          ? `<p class="badge badge-danger">${escapeHtml(last.error)}</p>`
          : ""
      }
    </section>`;
}

const ACCOUNT_STATUS_LABELS = {
  running: "运行中",
  pending: "待启动",
  stopped: "已停止",
  error: "异常",
};

function renderAccounts(status) {
  const accounts = status.accounts || [];
  if (!accounts.length) {
    return `
      <section class="card">
        <div class="card-head"><h2 class="card-title">机器人账号</h2></div>
        <p class="muted">没有检测到已加载的 OneBot 账号。</p>
      </section>`;
  }
  return `
    <section class="card">
      <div class="card-head">
        <h2 class="card-title">机器人账号</h2>
        <span class="faint">${accounts.length} 个</span>
      </div>
      <dl class="detail-list">
        ${accounts
          .map(
            (account) => `
          <dt class="mono">${escapeHtml(account.platform_id || "未知平台")}</dt>
          <dd>
            ${account.self_id ? `<span class="mono">${escapeHtml(account.self_id)}</span>` : "未连接"}
            <span class="faint"> · ${escapeHtml(account.subscriptions || 0)} 个订阅</span>
            ${badge(
              ACCOUNT_STATUS_LABELS[account.status] || account.status || "未知",
              account.status === "running"
                ? "success"
                : account.status === "error"
                  ? "danger"
                  : "muted",
            )}
          </dd>`,
          )
          .join("")}
      </dl>
    </section>`;
}

function renderConfig(status) {
  const config = status.config || {};
  const formatBool = (value) => (value ? "开启" : "关闭");
  return `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">配置摘要</h2>
          <p class="card-subtitle">修改配置请前往 AstrBot 插件配置页</p>
        </div>
      </div>
      <dl class="detail-list">
        <dt>请求超时</dt><dd>${escapeHtml(config.request_timeout)} 秒</dd>
        <dt>检查间隔</dt><dd>${escapeHtml(formatInterval(config.game_check_interval))}</dd>
        <dt>每页数量</dt><dd>${escapeHtml(config.max_display_items)}</dd>
        <dt>发送封面</dt><dd>${formatBool(config.send_game_posters)}</dd>
        <dt>上传失败降级</dt><dd>${formatBool(
          config.fallback_to_text_on_upload_failure,
        )}</dd>
      </dl>
    </section>`;
}

function renderRecentEvents(status, events) {
  const items = (events || []).slice(0, 8);
  return `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">最近事件</h2>
          <p class="card-subtitle">检查、推送与失败记录</p>
        </div>
        <button class="button button-small button-ghost" type="button" data-action="open-events">
          查看全部
        </button>
      </div>
      ${
        items.length
          ? `<ul class="event-list">${items
              .map(renderEventItem)
              .join("")}</ul>`
          : '<p class="muted">暂无事件。</p>'
      }
    </section>`;
}

export function renderOverview(root, state, handlers) {
  const status = state.status;
  if (!status) {
    root.innerHTML = '<div class="loading-block">正在加载状态…</div>';
    return;
  }
  root.innerHTML = `
    <div class="stack">
      <div class="grid overview-grid">
        <div class="stack">
          ${renderSchedule(status)}
          ${renderLastCheck(status)}
          ${renderRecentEvents(status, state.events)}
        </div>
        <div class="stack">
          ${renderAccounts(status)}
          ${renderConfig(status)}
          <section class="card">
            <div class="card-head"><h2 class="card-title">快捷操作</h2></div>
            <div class="card-actions">
              <button class="button" type="button" data-action="refresh">刷新状态</button>
              <button class="button button-primary" type="button" data-action="dry-check">
                仅检查（不发消息）
              </button>
            </div>
            <p class="muted" style="margin-bottom: 0">
              「仅检查」只抓取站点列表并与 seen 基线对比，不发送消息、不修改 seen 文件。
            </p>
          </section>
        </div>
      </div>
    </div>`;

  root.querySelector('[data-action="refresh"]')?.addEventListener("click", () => {
    handlers.refreshAll();
  });
  root
    .querySelectorAll('[data-action="open-events"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.openTab("events")),
    );
  root
    .querySelectorAll('[data-action="dry-check"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.runDryCheck()),
    );
}

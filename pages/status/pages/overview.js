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
      ? `HTTP ${health.status_code ?? "200"}`
      : health.reachable === false
        ? health.error || "无法连接"
        : "暂无探测记录";
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
        <div class="card-actions">
          ${pill(
            runtime.checking ? "检查进行中" : "等待下次检查",
            runtime.checking ? "brand" : "muted",
          )}
          <button class="button button-small button-ghost" type="button"
            data-action="probe-health">检测站点</button>
        </div>
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
            ? `<span class="faint"> · ${escapeHtml(
                formatDuration(health.latency_ms),
              )}</span>`
            : ""
        }${
          health.checked_at
            ? `<div class="faint">探测于 ${escapeHtml(
                formatDateTime(health.checked_at),
              )}</div>`
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
            ${
              account.last_delivery
                ? `<div class="faint">最近投递 ${escapeHtml(
                    formatDateTime(account.last_delivery.at),
                  )} ${badge(
                    account.last_delivery.ok ? "成功" : "失败",
                    account.last_delivery.ok ? "success" : "danger",
                  )}</div>`
                : ""
            }
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

function renderHistory(history) {
  const days = (history || []).slice(-7);
  if (!days.length) return "";
  const totalPushes = days.reduce((sum, day) => sum + (day.pushes || 0), 0);
  const totalFailures = days.reduce((sum, day) => sum + (day.failures || 0), 0);
  const attempts = totalPushes + totalFailures;
  const rate = attempts ? Math.round((totalPushes / attempts) * 100) : null;
  const maxNew = Math.max(1, ...days.map((day) => day.new_games || 0));
  const maxDuration = Math.max(1, ...days.map((day) => day.avg_duration_ms || 0));
  const width = 360;
  const height = 120;
  const padX = 26;
  const chartBottom = height - 24;
  const chartHeight = 74;
  const slot = (width - padX * 2) / days.length;
  const barWidth = Math.max(8, slot * 0.5);
  const bars = days
    .map((day, index) => {
      const value = day.new_games || 0;
      const barHeight = Math.max(2, (value / maxNew) * chartHeight);
      const x = padX + index * slot + (slot - barWidth) / 2;
      const y = chartBottom - barHeight;
      return `
        <rect class="chart-bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}"
          width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="3">
          <title>${escapeHtml(day.date)}：新游戏 ${value}</title>
        </rect>
        <text class="chart-value" x="${(x + barWidth / 2).toFixed(1)}"
          y="${(y - 4).toFixed(1)}" text-anchor="middle">${value || ""}</text>
        <text class="chart-label" x="${(x + barWidth / 2).toFixed(1)}"
          y="${height - 7}" text-anchor="middle">${escapeHtml(
            String(day.date || "").slice(5),
          )}</text>`;
    })
    .join("");
  const points = days
    .map((day, index) => {
      const value = day.avg_duration_ms || 0;
      const x = padX + index * slot + slot / 2;
      const y = chartBottom - (value / maxDuration) * chartHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const dots = days
    .map((day, index) => {
      const value = day.avg_duration_ms || 0;
      const x = padX + index * slot + slot / 2;
      const y = chartBottom - (value / maxDuration) * chartHeight;
      return `<circle class="chart-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(
        1,
      )}" r="2.5"><title>${escapeHtml(day.date)}：${value} ms</title></circle>`;
    })
    .join("");
  const ratePoints = days
    .map((day, index) => {
      const rate = day.success_rate;
      if (rate == null) return null;
      const x = padX + index * slot + slot / 2;
      const y = chartBottom - rate * chartHeight;
      return {
        x: x.toFixed(1),
        y: y.toFixed(1),
        rate: Math.round(rate * 100),
        date: day.date,
      };
    })
    .filter(Boolean);
  const ratePolyline = ratePoints.length
    ? `<polyline class="chart-line" points="${ratePoints
        .map((point) => `${point.x},${point.y}`)
        .join(" ")}" />`
    : "";
  const rateDots = ratePoints
    .map(
      (point) => `
      <circle class="chart-dot" cx="${point.x}" cy="${point.y}" r="2.5">
        <title>${escapeHtml(point.date)}：${point.rate}%</title>
      </circle>
      <text class="chart-value" x="${point.x}" y="${(
        Number(point.y) - 5
      ).toFixed(1)}" text-anchor="middle">${point.rate}%</text>`,
    )
    .join("");
  return `
    <section class="card history-card">
      <div class="card-head">
        <div>
          <h2 class="card-title">近 7 天趋势</h2>
          <p class="card-subtitle">每日新游戏与平均检查耗时</p>
        </div>
        <span class="badge ${
          rate == null ? "badge-muted" : rate >= 90 ? "badge-success" : "badge-warning"
        }">
          ${rate == null ? "暂无推送" : `推送成功率 ${rate}%`}
        </span>
      </div>
      <div class="trend-grid">
        <div>
          <div class="trend-label">每日新游戏</div>
          <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img"
            aria-label="近 7 天每日新游戏">
            <line class="chart-axis" x1="${padX}" y1="${chartBottom}"
              x2="${width - padX / 2}" y2="${chartBottom}" />
            ${bars}
          </svg>
        </div>
        <div>
          <div class="trend-label">平均检查耗时</div>
          <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img"
            aria-label="近 7 天平均检查耗时">
            <line class="chart-axis" x1="${padX}" y1="${chartBottom}"
              x2="${width - padX / 2}" y2="${chartBottom}" />
            <polyline class="chart-line" points="${points}" />
            ${dots}
          </svg>
          <div class="trend-foot">峰值 ${escapeHtml(formatDuration(maxDuration))}</div>
        </div>
        <div>
          <div class="trend-label">每日推送成功率</div>
          <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img"
            aria-label="近 7 天每日推送成功率">
            <line class="chart-axis" x1="${padX}" y1="${chartBottom}"
              x2="${width - padX / 2}" y2="${chartBottom}" />
            ${ratePolyline}
            ${rateDots}
          </svg>
          <div class="trend-foot">${
            rate == null ? "暂无推送记录" : `近 7 天 ${rate}%`
          }</div>
        </div>
      </div>
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
              <button class="button" type="button" data-action="dry-check">
                仅检查（不发消息）
              </button>
              <button class="button button-primary" type="button" data-action="push-check">
                检查并推送
              </button>
            </div>
            <p class="muted" style="margin-bottom: 0">
              「仅检查」只抓取列表并与 seen 基线对比，不发送消息；
              「检查并推送」会真实向订阅群发送新游戏消息，需要二次确认。
            </p>
          </section>
        </div>
      </div>
      ${renderHistory(status.history)}
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
  root
    .querySelectorAll('[data-action="push-check"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.runPushCheck()),
    );
  root
    .querySelectorAll('[data-action="probe-health"]')
    .forEach((button) =>
      button.addEventListener("click", () => handlers.probeHealth()),
    );
}

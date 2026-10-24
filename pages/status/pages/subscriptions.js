import {
  badge,
  emptyState,
  escapeHtml,
  formatDateTime,
  stateTone,
} from "../ui.js";

function renderDelivery(delivery) {
  if (!delivery) return '<span class="faint">暂无记录</span>';
  const tone = delivery.ok ? "success" : "danger";
  return `
    ${badge(delivery.ok ? "成功" : "失败", tone)}
    <div class="faint" style="margin-top: 3px">
      ${escapeHtml(formatDateTime(delivery.at))}
      ${delivery.fallback ? " · 已降级纯文字" : ""}
      ${delivery.error ? ` · ${escapeHtml(delivery.error)}` : ""}
    </div>`;
}

export function renderSubscriptions(root, state, handlers) {
  const data = state.subscriptions;
  if (!data) {
    root.innerHTML = '<div class="loading-block">正在加载订阅…</div>';
    return;
  }
  const items = data.items || [];
  const summary = data.summary || {};

  if (!items.length) {
    root.innerHTML = emptyState(
      "还没有订阅群",
      "在群聊中由机器人执行 /game subscribe 后，这里会出现订阅记录。",
    );
    return;
  }

  root.innerHTML = `
    <div class="stack">
      <div class="summary-chips">
        <span class="badge badge-muted">共 ${escapeHtml(summary.total)} 个</span>
        <span class="badge badge-success">可推送 ${escapeHtml(summary.ready)}</span>
        ${summary.legacy ? `<span class="badge badge-warning">旧订阅 ${escapeHtml(summary.legacy)}</span>` : ""}
        ${summary.blocked ? `<span class="badge badge-danger">被阻止 ${escapeHtml(summary.blocked)}</span>` : ""}
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>群号</th>
              <th>平台 / 账号</th>
              <th>推送状态</th>
              <th>最近投递</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${items
              .map((item) => {
                const resolved =
                  item.resolved_platform_id &&
                  item.resolved_platform_id !== item.platform_id
                    ? `<div class="faint">将使用 <span class="mono">${escapeHtml(
                        item.resolved_platform_id,
                      )}</span></div>`
                    : "";
                return `
                <tr>
                  <td class="mono">${escapeHtml(item.group_id)}</td>
                  <td>
                    <div class="mono">${escapeHtml(
                      item.platform_id || item.resolved_platform_id || "未绑定",
                    )}</div>
                    <div class="faint">
                      ${item.self_id ? `self_id ${escapeHtml(item.self_id)}` : "self_id 未知"}
                      ${resolved}
                    </div>
                  </td>
                  <td>
                    ${badge(item.state_label, stateTone(item.state))}
                    ${
                      item.will_migrate
                        ? '<div class="faint" style="margin-top: 3px">下次推送前自动迁移</div>'
                        : ""
                    }
                  </td>
                  <td>${renderDelivery(item.last_delivery)}</td>
                  <td>
                    <button class="button button-small button-ghost" type="button"
                      data-copy-group="${escapeHtml(item.group_id)}">
                      复制群号
                    </button>
                  </td>
                </tr>`;
              })
              .join("")}
          </tbody>
        </table>
      </div>
      <p class="muted" style="margin: 0">
        推送账号由收到 <span class="mono">/game subscribe</span> 的机器人决定；
        「配置文件未启用本插件」表示该账号绑定的配置文件没有勾选本插件。
      </p>
    </div>`;

  root.querySelectorAll("[data-copy-group]").forEach((button) => {
    button.addEventListener("click", () =>
      handlers.copy(button.getAttribute("data-copy-group")),
    );
  });
}

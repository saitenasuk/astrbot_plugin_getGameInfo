const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

export function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN") : "—";
}

export function formatInterval(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return "—";
  if (total % 86400 === 0) return `${total / 86400} 天`;
  if (total % 3600 === 0) return `${total / 3600} 小时`;
  if (total % 60 === 0) return `${total / 60} 分钟`;
  return `${Math.round(total)} 秒`;
}

export function formatDuration(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(1)} 秒`;
}

export function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  return `${minutes} 分钟`;
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatTime(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function formatDateTime(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function formatRelative(value) {
  const date = parseDate(value);
  if (!date) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

export function levelTone(level) {
  if (level === "error") return "danger";
  if (level === "warning") return "warning";
  return "muted";
}

export function stateTone(state) {
  if (state === "ready") return "success";
  if (state === "legacy") return "warning";
  if (state === "plugin_disabled" || state === "platform_missing") {
    return "warning";
  }
  if (state === "no_eligible_account" || state === "bot_unavailable") {
    return "danger";
  }
  return "muted";
}

export function badge(text, tone = "muted") {
  return `<span class="badge badge-${tone}">${escapeHtml(text)}</span>`;
}

export function pill(text, tone = "muted") {
  return `<span class="pill pill-${tone}">${escapeHtml(text)}</span>`;
}

export function emptyState(title, hint) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong>${
    hint ? `<span>${escapeHtml(hint)}</span>` : ""
  }</div>`;
}

export function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value ?? "";
}

export function showToast(message, tone = "info") {
  const region = document.getElementById("toast-region");
  if (!region) return;
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone}`;
  toast.textContent = message;
  region.appendChild(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

export async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Sandboxed iframes often reject the async clipboard API.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

const MODAL_CLOSE_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.3 5.71a1 1 0 0 0-1.42 0L12 10.59 7.12 5.7A1 1 0 0 0 5.7 7.12L10.59 12l-4.9 4.88a1 1 0 1 0 1.42 1.42L12 13.41l4.88 4.89a1 1 0 0 0 1.42-1.42L13.41 12l4.89-4.88a1 1 0 0 0 0-1.41Z"/></svg>';

/**
 * Open a modal. `bodyHtml` must already be escaped by the caller.
 */
export function openModal(title, bodyHtml, footerHtml = "") {
  closeModal();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(
      title,
    )}">
      <div class="modal-head">
        <h2 class="modal-title">${escapeHtml(title)}</h2>
        <button class="icon-button" type="button" data-modal-close aria-label="关闭">
          ${MODAL_CLOSE_SVG}
        </button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
      ${
        footerHtml
          ? `<div class="modal-foot">${footerHtml}</div>`
          : ""
      }
    </div>`;
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeModal();
    if (event.target.closest?.("[data-modal-close]")) closeModal();
  });
  document.addEventListener("keydown", onModalKeydown);
  document.body.appendChild(overlay);
  return overlay;
}

function onModalKeydown(event) {
  if (event.key === "Escape") closeModal();
}

export function closeModal() {
  document.getElementById("modal-overlay")?.remove();
  document.removeEventListener("keydown", onModalKeydown);
}

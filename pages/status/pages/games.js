import {
  emptyState,
  escapeHtml,
  formatDateTime,
} from "../ui.js";

function renderCard(game) {
  const poster = game.poster_url
    ? `<img data-poster src="${escapeHtml(game.poster_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
    : "";
  const tags = [...(game.platforms || []), ...(game.modes || [])].slice(0, 3);
  return `
    <button class="game-card" type="button" data-game-id="${escapeHtml(
      game.id,
    )}" aria-label="查看 ${escapeHtml(game.title)} 的详情">
      <span class="game-poster ${game.poster_url ? "" : "is-missing"}">
        ${poster}
        ${game.is_new ? '<span class="new-dot badge badge-brand">新</span>' : ""}
      </span>
      <span class="game-body">
        <span class="game-title">${escapeHtml(game.title)}</span>
        <span class="game-meta">
          ${game.post_date ? `<span>发布 ${escapeHtml(game.post_date)}</span>` : ""}
          ${game.release_date ? `<span>${escapeHtml(game.release_date)}</span>` : ""}
        </span>
        ${
          tags.length
            ? `<span class="game-tags">${tags
                .map((tag) => `<span class="game-tag">${escapeHtml(tag)}</span>`)
                .join("")}</span>`
            : ""
        }
      </span>
    </button>`;
}

export function renderGames(root, state, handlers) {
  const data = state.games;
  const query = state.gamesQuery || "";
  const loading = state.gamesLoading;

  let body;
  if (loading && !data) {
    body = '<div class="loading-block">正在获取游戏列表…</div>';
  } else if (!data) {
    body = emptyState("还没有加载游戏", "点击刷新或稍后重试。");
  } else if (!data.items?.length) {
    body = emptyState("没有找到游戏", query ? "换个关键词试试。" : "站点列表为空。");
  } else {
    body = `
      <div class="game-grid">
        ${data.items.map(renderCard).join("")}
      </div>
      ${
        query
          ? ""
          : `<div class="pager">
              <button class="button button-small" type="button" data-page="${
                data.page - 1
              }" ${data.page <= 1 ? "disabled" : ""}>上一页</button>
              <span class="muted">第 ${escapeHtml(data.page)} / ${escapeHtml(
                data.total_pages || 1,
              )} 页</span>
              <button class="button button-small" type="button" data-page="${
                data.page + 1
              }" ${
                data.page >= (data.total_pages || 1) ? "disabled" : ""
              }>下一页</button>
            </div>`
      }`;
  }

  root.innerHTML = `
    <div class="stack">
      <div class="games-toolbar">
        <input
          class="search-input"
          id="game-search"
          type="search"
          placeholder="搜索 online-fix.me 游戏"
          value="${escapeHtml(query)}"
        />
        <button class="button button-small button-primary" type="button" data-action="search-games">
          搜索
        </button>
        ${
          query
            ? '<button class="button button-small" type="button" data-action="clear-search">清除搜索</button>'
            : `<button class="button button-small" type="button" data-action="refresh-games">
                刷新列表
              </button>`
        }
        <span class="faint nowrap">
          ${
            data?.fetched_at
              ? `更新于 ${escapeHtml(formatDateTime(data.fetched_at))}`
              : ""
          }
          ${data?.cached ? " · 缓存" : ""}
        </span>
      </div>
      ${body}
      <p class="muted" style="margin: 0">
        「新」表示游戏不在 seen 基线中；点击卡片可以查看详情页的合作 / 多人人数。
        封面直接加载自站点，不经过 AstrBot 转发。
      </p>
    </div>`;

  const search = root.querySelector("#game-search");
  const submitSearch = () => handlers.searchGames(search?.value || "");
  root
    .querySelector('[data-action="search-games"]')
    ?.addEventListener("click", submitSearch);
  search?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") submitSearch();
  });
  root
    .querySelector('[data-action="refresh-games"]')
    ?.addEventListener("click", () => handlers.loadGames(1, true));
  root
    .querySelector('[data-action="clear-search"]')
    ?.addEventListener("click", () => handlers.clearGameSearch());
  root.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => {
      const page = Number(button.getAttribute("data-page"));
      if (page >= 1) handlers.loadGames(page, false);
    });
  });
  root.querySelectorAll("[data-game-id]").forEach((card) => {
    card.addEventListener("click", () => {
      const game = data.items.find(
        (item) => item.id === card.getAttribute("data-game-id"),
      );
      if (game) handlers.openGameDetail(game);
    });
  });
  root.querySelectorAll("img[data-poster]").forEach((image) => {
    image.addEventListener("error", () => {
      image.remove();
      image.closest(".game-poster")?.classList.add("is-missing");
    });
  });
}

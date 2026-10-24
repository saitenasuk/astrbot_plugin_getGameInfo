// AstrBot 4.26+ exposes AstrBotPluginView; 4.25.x only has the legacy alias.
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

function ensureBridge() {
  if (!bridge || typeof bridge.apiGet !== "function") {
    throw new Error("当前页面不在 AstrBot 插件视图中，无法连接后端");
  }
  return bridge;
}

export async function ready() {
  if (!bridge || typeof bridge.ready !== "function") {
    throw new Error("插件视图 bridge 未加载，请刷新页面或重载插件");
  }
  return bridge.ready();
}

export function getContext() {
  return bridge?.getContext?.() || null;
}

export function translate(key, fallback) {
  if (bridge && typeof bridge.t === "function") {
    return bridge.t(key, fallback);
  }
  return fallback;
}

export async function apiGet(endpoint, params) {
  const target = ensureBridge();
  try {
    return await target.apiGet(endpoint, params);
  } catch (error) {
    throw new Error(error?.message || "请求失败");
  }
}

export async function apiPost(endpoint, body) {
  const target = ensureBridge();
  try {
    return await target.apiPost(endpoint, body);
  } catch (error) {
    throw new Error(error?.message || "请求失败");
  }
}

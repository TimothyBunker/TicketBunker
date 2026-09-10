import type { AppState } from "./types";

type TauriGlobal = {
  core: { invoke<T>(name: string, payload?: Record<string, unknown>): Promise<T> };
  window?: { getCurrentWindow(): {
    onCloseRequested(callback: (event: { preventDefault(): void }) => void): Promise<() => void>;
    destroy(): Promise<void>;
  } };
  dialog?: {
    open(options: Record<string, unknown>): Promise<string | string[] | null>;
  };
  event?: {
    listen<T>(name: string, callback: (event: { payload: T }) => void): Promise<() => void>;
  };
};

declare global {
  interface Window {
    __TAURI__?: TauriGlobal;
  }
}

export const isNative = () => Boolean(window.__TAURI__?.core);

const browserToken = () => {
  const value = document.querySelector<HTMLMetaElement>('meta[name="ticketbunker-token"]')?.content || "";
  return value.startsWith("__TB_") ? "" : value;
};

const browserSession = globalThis.crypto?.randomUUID?.().replaceAll("-", "") ||
  `browser${Date.now()}${Math.random().toString(16).slice(2)}`;

export const OFFLINE_MESSAGE = "TicketBunker's local service is not running. Start it again, then reload this page.";
export const STALE_SERVICE_MESSAGE = "This page is newer than the TicketBunker service behind it. Restart the service command in your terminal, then reload this page.";

let offline = false;

function setOffline(value: boolean) {
  if (offline === value) return;
  offline = value;
  window.dispatchEvent(new CustomEvent("ticketbunker-service", { detail: { offline: value } }));
}

function isNetworkFailure(reason: unknown): boolean {
  return reason instanceof TypeError;
}

async function session(closed = false) {
  if (isNative() || !browserToken()) return;
  try {
    await fetch("/api/session", {
      method: "POST",
      keepalive: closed,
      headers: { "Content-Type": "application/json", "X-TicketBunker-Token": browserToken() },
      body: JSON.stringify({ session: browserSession, closed }),
    });
    if (!closed) setOffline(false);
  } catch (reason) {
    if (!closed && isNetworkFailure(reason)) setOffline(true);
  }
}

if (!isNative() && browserToken()) {
  window.setInterval(() => session(), 10_000);
  window.addEventListener("pagehide", () => session(true));
  // Browsers throttle timers in hidden tabs; announce presence as soon as the
  // page is visible or focused again instead of waiting for the next tick.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) session(); });
  window.addEventListener("focus", () => session());
}

export async function bootstrap(): Promise<AppState> {
  if (window.__TAURI__?.core) {
    return window.__TAURI__.core.invoke<AppState>("desktop_state");
  }
  await session();
  let response: Response;
  try {
    response = await fetch("/api/bootstrap", { credentials: "same-origin" });
  } catch (reason) {
    if (isNetworkFailure(reason)) {
      setOffline(true);
      throw new Error(OFFLINE_MESSAGE);
    }
    throw reason;
  }
  const value = (await response.json()) as AppState & { ok?: boolean; message?: string };
  if (!response.ok || value.ok === false) throw new Error(value.message || "TicketBunker could not open.");
  if (value.launch) value.launch.route = `${window.location.pathname}${window.location.search}`;
  return value;
}

export async function command<T>(
  action: string,
  payload: Record<string, unknown> = {},
  projectRoot?: string,
): Promise<T> {
  if (window.__TAURI__?.core) {
    return window.__TAURI__.core.invoke<T>("desktop_command", {
      action,
      payload,
      projectRoot: projectRoot || null,
    });
  }
  let response: Response;
  try {
    response = await fetch("/api/action", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-TicketBunker-Token": browserToken(),
      },
      body: JSON.stringify({ action, payload, project_root: projectRoot || null }),
    });
  } catch (reason) {
    if (isNetworkFailure(reason)) {
      setOffline(true);
      throw new Error(OFFLINE_MESSAGE);
    }
    throw reason;
  }
  setOffline(false);
  const value = (await response.json()) as T & { ok?: boolean; code?: string; message?: string };
  if (!response.ok || value.ok === false) {
    if (value.code === "command_unavailable") throw new Error(STALE_SERVICE_MESSAGE);
    throw new Error(value.message || "TicketBunker could not complete that action.");
  }
  return value;
}

export async function chooseGitDirectory(defaultPath?: string): Promise<string | null> {
  const selected = await window.__TAURI__?.dialog?.open({
    directory: true,
    multiple: false,
    title: "Attach Git repository",
    ...(defaultPath ? { defaultPath } : {}),
  });
  return typeof selected === "string" ? selected : null;
}

export async function nativeInvoke<T>(
  name: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  if (!window.__TAURI__?.core) {
    throw new Error("This setup action is available in the TicketBunker app.");
  }
  return window.__TAURI__.core.invoke<T>(name, payload);
}

export async function openExternal(url: string): Promise<void> {
  if (isNative()) {
    await nativeInvoke("open_release_url", { url });
    return;
  }
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (!opened) throw new Error("Allow TicketBunker to open the download page, then retry.");
}

export async function listenNative<T>(
  name: string,
  callback: (payload: T) => void,
): Promise<() => void> {
  if (!window.__TAURI__?.event) return () => undefined;
  return window.__TAURI__.event.listen<T>(name, (event) => callback(event.payload));
}

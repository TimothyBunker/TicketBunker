import type { AppState } from "./types";

type TauriGlobal = {
  core: { invoke<T>(name: string, payload?: Record<string, unknown>): Promise<T> };
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

async function session(closed = false) {
  if (isNative() || !browserToken()) return;
  await fetch("/api/session", {
    method: "POST",
    keepalive: closed,
    headers: { "Content-Type": "application/json", "X-TicketBunker-Token": browserToken() },
    body: JSON.stringify({ session: browserSession, closed }),
  }).catch(() => undefined);
}

if (!isNative() && browserToken()) {
  window.setInterval(() => session(), 10_000);
  window.addEventListener("pagehide", () => session(true));
}

export async function bootstrap(): Promise<AppState> {
  if (window.__TAURI__?.core) {
    return window.__TAURI__.core.invoke<AppState>("desktop_state");
  }
  await session();
  const response = await fetch("/api/bootstrap", { credentials: "same-origin" });
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
  const response = await fetch("/api/action", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-TicketBunker-Token": browserToken(),
    },
    body: JSON.stringify({ action, payload, project_root: projectRoot || null }),
  });
  const value = (await response.json()) as T & { ok?: boolean; message?: string };
  if (!response.ok || value.ok === false) {
    throw new Error(value.message || "TicketBunker could not complete that action.");
  }
  return value;
}

export async function chooseGitDirectory(): Promise<string | null> {
  const selected = await window.__TAURI__?.dialog?.open({
    directory: true,
    multiple: false,
    title: "Attach Git repository",
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

export async function listenNative<T>(
  name: string,
  callback: (payload: T) => void,
): Promise<() => void> {
  if (!window.__TAURI__?.event) return () => undefined;
  return window.__TAURI__.event.listen<T>(name, (event) => callback(event.payload));
}

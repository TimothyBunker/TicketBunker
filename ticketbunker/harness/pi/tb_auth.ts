/**
 * TicketBunker-owned authentication bridge for pi RPC mode.
 *
 * TicketBunker renders the dialogs, but pi's ModelRuntime performs the login
 * and remains the sole writer of provider credentials.  This bridge is loaded
 * alone with extension discovery disabled.
 */

import type {
  ExtensionAPI,
  ExtensionCommandContext,
} from "@earendil-works/pi-coding-agent";
import type {
  AuthEvent,
  AuthInteraction,
  AuthPrompt,
  AuthType,
  Credential,
} from "@earendil-works/pi-ai";

const EVENT_PREFIX = "TB_AUTH_EVENT:";

type LoginRuntime = {
  login(
    provider: string,
    method: AuthType,
    interaction: AuthInteraction,
  ): Promise<Credential>;
};

type RuntimeBackedRegistry = ExtensionCommandContext["modelRegistry"] & {
  // ModelRegistry.runtime is private in TypeScript but intentionally remains
  // a normal property in pi 0.84.2.  This pinned adapter is integration-tested
  // and must be reviewed whenever TicketBunker updates its pi pin.
  runtime: LoginRuntime;
};

function send(ctx: ExtensionCommandContext, payload: Record<string, unknown>): void {
  ctx.ui.notify(`${EVENT_PREFIX}${JSON.stringify(payload)}`, "info");
}

async function answerPrompt(
  ctx: ExtensionCommandContext,
  prompt: AuthPrompt,
): Promise<string> {
  let response: Promise<string | undefined>;
  if (prompt.type === "select") {
    const labels = prompt.options.map((option) => option.label);
    response = ctx.ui.select(prompt.message, labels);
    const selected = await withPromptCancellation(response, prompt.signal);
    const option = prompt.options.find((candidate) => candidate.label === selected);
    if (!option) throw new Error("Login cancelled");
    return option.id;
  }
  const marker = prompt.type === "secret" ? "TB_SECRET:" : "TB_INPUT:";
  response = ctx.ui.input(`${marker}${prompt.message}`, prompt.placeholder);
  const value = await withPromptCancellation(response, prompt.signal);
  if (value === undefined) throw new Error("Login cancelled");
  return value;
}

async function withPromptCancellation(
  response: Promise<string | undefined>,
  signal?: AbortSignal,
): Promise<string | undefined> {
  if (!signal) return response;
  if (signal.aborted) throw new Error("Login cancelled");
  let cancel: (() => void) | undefined;
  const cancelled = new Promise<never>((_resolve, reject) => {
    cancel = () => reject(new Error("Login cancelled"));
    signal.addEventListener("abort", cancel, { once: true });
  });
  try {
    return await Promise.race([response, cancelled]);
  } finally {
    if (cancel) signal.removeEventListener("abort", cancel);
  }
}

function relayAuthEvent(ctx: ExtensionCommandContext, event: AuthEvent): void {
  if (event.type === "auth_url") {
    send(ctx, {
      kind: "auth_url",
      url: event.url,
      instructions: event.instructions || "",
    });
  } else if (event.type === "device_code") {
    send(ctx, {
      kind: "device_code",
      user_code: event.userCode,
      verification_url: event.verificationUri,
      expires_in_seconds: event.expiresInSeconds || 0,
    });
  } else if (event.type === "info") {
    send(ctx, { kind: "info", message: event.message, links: event.links || [] });
  } else {
    send(ctx, { kind: "progress", message: event.message });
  }
}

function catalog(ctx: ExtensionCommandContext): Record<string, unknown> {
  const models = ctx.modelRegistry.getAll();
  const providerIds = [...new Set(models.map((model) => model.provider))].sort();
  const providers = providerIds.map((id) => {
    const provider = ctx.modelRegistry.getProvider(id);
    const methods: AuthType[] = [];
    if (provider?.auth.oauth?.login) methods.push("oauth");
    if (provider?.auth.apiKey?.login) methods.push("api_key");
    return {
      id,
      name: provider?.name || id,
      methods,
      models: models
        .filter((model) => model.provider === id)
        .map((model) => ({ id: model.id, name: model.name || model.id })),
    };
  });
  return { kind: "catalog", providers };
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand("tb-auth-catalog", {
    description: "Return the provider and model catalog to TicketBunker",
    handler: async (_args, ctx) => {
      send(ctx, catalog(ctx));
    },
  });

  pi.registerCommand("tb-auth-login", {
    description: "Run one provider login through TicketBunker",
    handler: async (args, ctx) => {
      const [provider, rawMethod] = args.trim().split(/\s+/, 2);
      const method = rawMethod as AuthType;
      if (!provider || !["oauth", "api_key"].includes(method)) {
        send(ctx, { kind: "error", message: "Choose a provider and login method." });
        return;
      }
      const providerDefinition = ctx.modelRegistry.getProvider(provider);
      const login = method === "oauth" ? providerDefinition?.auth.oauth?.login : providerDefinition?.auth.apiKey?.login;
      if (!login) {
        send(ctx, { kind: "error", message: "That login method is not available for this provider." });
        return;
      }
      try {
        const runtime = (ctx.modelRegistry as RuntimeBackedRegistry).runtime;
        await runtime.login(provider, method, {
          signal: ctx.signal,
          prompt: (prompt) => answerPrompt(ctx, prompt),
          notify: (event) => relayAuthEvent(ctx, event),
        });
        send(ctx, { kind: "complete", provider, method });
      } catch (error) {
        const cancelled = error instanceof Error && error.message === "Login cancelled";
        send(ctx, {
          kind: cancelled ? "cancelled" : "error",
          message: cancelled
            ? "Login cancelled."
            : "Login could not finish. Check your connection or credentials, then retry.",
        });
      }
    },
  });
}

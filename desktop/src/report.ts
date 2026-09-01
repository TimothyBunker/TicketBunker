export const TOPICS = ["who", "what", "where", "why"] as const;
export type Topic = (typeof TOPICS)[number];

function section(text: string, heading: string): string {
  const lines = text.split(/\r?\n/);
  const selected: string[] = [];
  let keep = false;
  for (const line of lines) {
    if (/^##\s+/.test(line) && !/^###\s+/.test(line)) {
      keep = line.replace(/^##\s+/, "").trim().toLowerCase() === heading.toLowerCase();
      continue;
    }
    if (keep) selected.push(line);
  }
  return selected.join("\n").trim();
}

function subsection(text: string, parent: string, heading: string): string {
  const lines = text.split(/\r?\n/);
  const selected: string[] = [];
  let inParent = false;
  let keep = false;
  for (const line of lines) {
    if (/^##\s+/.test(line) && !/^###\s+/.test(line)) {
      inParent = line.replace(/^##\s+/, "").trim().toLowerCase() === parent.toLowerCase();
      keep = false;
      continue;
    }
    if (inParent && /^###\s+/.test(line)) {
      keep = line.replace(/^###\s+/, "").trim().toLowerCase() === heading.toLowerCase();
      continue;
    }
    if (inParent && keep) selected.push(line);
  }
  return selected.join("\n").trim();
}

export function reportTopic(text: string, topic: Topic) {
  const label = topic[0].toUpperCase() + topic.slice(1);
  return {
    synthesis: subsection(text, "Reader brief", label),
    trail: section(text, label),
  };
}

export function sourceLinks(text: string): string {
  return text.replace(/@\[([^\]\n]+)\]:(\d+)/g, (_match, path: string, line: string) => {
    const label = `@${path}:${line}`;
    return `[${label}](#tb-source:${encodeURIComponent(path)}:${line})`;
  });
}

export function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value || "Not yet";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(timestamp);
}

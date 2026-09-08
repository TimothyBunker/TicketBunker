import type { ButtonHTMLAttributes } from "react";

export type IconName = "plus" | "settings" | "sync" | "grid" | "trash";
export function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "plus" && <path d="M12 5v14M5 12h14" />}
    {name === "settings" && <><path d="m10 3-.5 2.2-2 1.2-2.2-.6-2 3.4L5 10.8v2.4l-1.7 1.6 2 3.4 2.2-.6 2 1.2L10 21h4l.5-2.2 2-1.2 2.2.6 2-3.4-1.7-1.6v-2.4l1.7-1.6-2-3.4-2.2.6-2-1.2L14 3Z" /><circle cx="12" cy="12" r="3" /></>}
    {name === "sync" && <><path d="M20 9a8 8 0 0 0-14-3L3 9m0-5v5h5M4 15a8 8 0 0 0 14 3l3-3m0 5v-5h-5" /></>}
    {name === "grid" && <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>}
    {name === "trash" && <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>}
  </svg>;
}

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon: IconName; label: string; hint?: string; issue?: string; loading?: boolean;
};
export default function IconButton({ icon, label, hint, issue, loading, className = "", ...props }: Props) {
  const help = [hint || label, issue].filter(Boolean).join(". ");
  return <button {...props} type={props.type || "button"} className={`icon-action ${loading ? "is-loading" : ""} ${className}`} aria-label={label} title={help}>
    <Icon name={icon} />{issue && <span className="action-issue" aria-label={issue} />}
  </button>;
}

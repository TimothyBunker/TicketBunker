import type { ButtonHTMLAttributes } from "react";

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label" | "title"> & { label?: string };

export default function CloseButton({ label = "Close", className = "", ...props }: Props) {
  return <button {...props} type={props.type || "button"} className={`icon-control close-control ${className}`.trim()} aria-label={label} title={label}>
    <span aria-hidden="true">×</span>
  </button>;
}

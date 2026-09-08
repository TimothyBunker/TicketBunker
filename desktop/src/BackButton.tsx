import type { ButtonHTMLAttributes } from "react";

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label" | "title"> & {
  label?: string;
};

export default function BackButton({ label = "Back", className = "", ...props }: Props) {
  return <button {...props} type={props.type || "button"} className={`icon-control back-control ${className}`.trim()} aria-label={label} title={label}>
    <span aria-hidden="true">←</span>
  </button>;
}

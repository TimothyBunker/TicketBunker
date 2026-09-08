import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import CloseButton from "./CloseButton";

it("uses a cross with an accessible close action, not a back arrow", () => {
  const onClick = vi.fn();
  render(<CloseButton label="Close configuration" onClick={onClick} />);
  const button = screen.getByRole("button", { name: "Close configuration" });
  expect(button.textContent).toBe("×");
  expect(button).toHaveAttribute("title", "Close configuration");
  fireEvent.click(button);
  expect(onClick).toHaveBeenCalledOnce();
});

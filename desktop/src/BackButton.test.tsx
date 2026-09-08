import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import BackButton from "./BackButton";

afterEach(cleanup);

it("renders only an arrow while naming its destination for assistive technology and hover", () => {
  const onClick = vi.fn();
  render(<BackButton label="Back to tickets" onClick={onClick} />);
  const button = screen.getByRole("button", { name: "Back to tickets" });
  expect(button.textContent).toBe("←");
  expect(button).toHaveAttribute("title", "Back to tickets");
  expect(button).toHaveAttribute("type", "button");
  expect(button.firstElementChild).toHaveAttribute("aria-hidden", "true");
  fireEvent.click(button);
  expect(onClick).toHaveBeenCalledOnce();
});

it("keeps the default name and disabled behavior", () => {
  const onClick = vi.fn();
  render(<BackButton disabled onClick={onClick} />);
  const button = screen.getByRole("button", { name: "Back" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
});

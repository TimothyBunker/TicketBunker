import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Markdown from "./Markdown";
import { reportTopic } from "./report";

describe("standalone report presentation", () => {
  it("puts concise synthesis before the full evidence trail", () => {
    const report = [
      "## Reader brief",
      "### Who",
      "- [real] Tim owns the change.",
      "### What",
      "- [observation] The cache changed.",
      "### Where",
      "- [real] Start at @[src/cache.py]:4.",
      "### Why",
      "- [speculation] A stale key is likely.",
      "## Who",
      "- [observation] Git names Tim.",
      "  - [real] The commit is anchored.",
    ].join("\n");

    expect(reportTopic(report, "who")).toEqual({
      synthesis: "- [real] Tim owns the change.",
      trail: "- [observation] Git names Tim.\n  - [real] The commit is anchored.",
    });
  });

  it("renders nested lists and makes source references openable and copyable", () => {
    const open = vi.fn();
    const copy = vi.fn();
    const { container } = render(
      <Markdown onOpenSource={open} onCopySource={copy}>
        {"- First\n  - Nested\n\nCheck @[src/cache.py]:4."}
      </Markdown>,
    );

    expect(container.querySelector("ul ul")?.textContent).toContain("Nested");
    fireEvent.click(screen.getByRole("button", { name: "@src/cache.py:4" }));
    expect(open).toHaveBeenCalledWith("src/cache.py", 4);
    fireEvent.click(screen.getByRole("button", { name: /Copy absolute path/ }));
    expect(copy).toHaveBeenCalledWith("src/cache.py", 4);
  });
});

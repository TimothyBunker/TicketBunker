import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import Markdown, { claimTag, verdictOf } from "./Markdown";

describe("trust chips", () => {
  afterEach(cleanup);

  it("reads the tag and grounding from a claim line", () => {
    expect(claimTag("[observation] 25 commits (authorship).")).toEqual({ key: "observation", rest: "25 commits (authorship)." });
    expect(claimTag("[real] Found it (README.md:1).")?.key).toBe("real");
    expect(claimTag("[speculation] Grounded — The branch delivers X.")).toEqual({ key: "speculation:grounded", rest: "The branch delivers X." });
    expect(claimTag("[speculation] ungrounded: nothing found")).toEqual({ key: "speculation:ungrounded", rest: "nothing found" });
    expect(claimTag("Plain prose with [real] later")).toBeNull();
  });

  it("renders claims as chips instead of bracket tags", () => {
    render(
      <Markdown compact>{"- [observation] Assignee: Tim (ticket config).\n- [real] The baseline defines the schema (DESIGN.md:96).\n- [speculation] Grounded — The work lands in ticketbunker/cli.py.\n- A plain bullet."}</Markdown>,
    );

    expect(screen.getByText("Observed")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Speculation · grounded")).toBeInTheDocument();
    expect(screen.queryByText(/\[real\]/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\[speculation\]/)).not.toBeInTheDocument();
    expect(screen.getByText(/The baseline defines the schema/)).toBeInTheDocument();
    expect(screen.getByText("A plain bullet.")).toBeInTheDocument();
    expect(screen.getByText("Verified").closest("li")).toHaveClass("claim-real");
  });
});

describe("source references", () => {
  afterEach(cleanup);

  it("offers open, copy, and chat actions when a reference is clicked", () => {
    const opened: Array<[string, number]> = [];
    const copied: Array<[string, number]> = [];
    const asked: Array<[string, number]> = [];
    render(
      <Markdown compact onOpenSource={(p, l) => opened.push([p, l])} onCopySource={(p, l) => copied.push([p, l])} onAskSource={(p, l) => asked.push([p, l])}>
        {"Look at `src/app.py:12` and @docs/guide.md:3 plus @[README.md]:1, never `1.2`."}
      </Markdown>,
    );
    expect(screen.queryByRole("toolbar")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "src/app.py:12" }));
    const toolbar = screen.getByRole("toolbar", { name: "Actions for src/app.py" });
    expect(toolbar).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open in editor" }));
    expect(opened).toEqual([["src/app.py", 12]]);
    expect(screen.queryByRole("toolbar")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "@docs/guide.md:3" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy path" }));
    expect(copied).toEqual([["docs/guide.md", 3]]);

    fireEvent.click(screen.getByRole("button", { name: "@README.md:1" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to chat" }));
    expect(asked).toEqual([["README.md", 1]]);
    expect(screen.queryByRole("button", { name: "1.2" })).toBeNull();
  });

  it("links a bare @path mention to its first line", () => {
    const opened: Array<[string, number]> = [];
    render(<Markdown compact onOpenSource={(p, l) => opened.push([p, l])}>{"See @ticketbunker/cli.py."}</Markdown>);
    fireEvent.click(screen.getByRole("button", { name: "@ticketbunker/cli.py" }));
    fireEvent.click(screen.getByRole("button", { name: "Open in editor" }));
    expect(opened).toEqual([["ticketbunker/cli.py", 1]]);
  });

  it("leaves references plain when nothing can act on them", () => {
    render(<Markdown compact>{"Plain `src/app.py:12` here."}</Markdown>);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("src/app.py:12")).toBeTruthy();
  });
});


describe("risk and next chips", () => {
  afterEach(cleanup);

  it("marks proactive claims with an aspect chip and strips the prefix", () => {
    expect(claimTag("[speculation] grounded — Risk: the parser skips blank lines.")).toEqual({ key: "speculation:grounded", rest: "the parser skips blank lines.", aspect: "risk" });
    expect(claimTag("[real] Next: run the viewer tests (tests/test_viewer.py:1).")?.aspect).toBe("next");
    render(<Markdown compact>{"- [speculation] grounded — Risk: the parser skips blank lines (src/parse.py:4).\n- [real] Next: run the viewer tests (tests/test_viewer.py:1)."}</Markdown>);
    expect(screen.getByText("Risk")).toBeTruthy();
    expect(screen.getByText("Next")).toBeTruthy();
    expect(screen.queryByText(/Risk:/)).toBeNull();
    expect(document.querySelector("li.claim-speculation .aspect-chip.risk")).toBeTruthy();
    expect(document.querySelector("li.claim-speculation.grade-grounded.aspect-risk")).toBeTruthy();
  });
});

describe("standing and checklist", () => {
  afterEach(cleanup);

  it("reads the verdict and marks checklist items", () => {
    expect(verdictOf("[speculation] grounded — Verdict: blocked — PositionComponent does not compile (src/p.cpp:21).")).toEqual({ verdict: "blocked", rest: "PositionComponent does not compile (src/p.cpp:21)." });
    expect(verdictOf("[real] Next: fix it (src/p.cpp:21).")).toBeNull();
    render(<Markdown compact>{"- [real] Done: CommonTypes (src/types.h:11).\n- [real] Missing: WorldPos3 (ticket).\n- [real] Ask: Tim, the only author (people)."}</Markdown>);
    expect(screen.getByText("Done")).toBeTruthy();
    expect(screen.getByText("Missing")).toBeTruthy();
    expect(screen.getByText("Ask")).toBeTruthy();
    expect(document.querySelector("li.claim-real.aspect-done")).toBeTruthy();
    render(<Markdown compact>{"- [real] Defect: PositionComponent.cpp defines the Pos3 constructor twice (src/p.cpp:21)."}</Markdown>);
    expect(screen.getByText("Defect")).toBeTruthy();
    expect(document.querySelector("li.claim-real.aspect-defect")).toBeTruthy();
    render(<Markdown compact>{"- [real] Step: remove the duplicate constructor (src/p.cpp:21)."}</Markdown>);
    expect(screen.queryByText(/Step:/)).toBeNull();
    expect(document.querySelector("li.claim-real.aspect-step")).toBeTruthy();
    expect(document.querySelector("li.claim-real.aspect-missing")).toBeTruthy();
    expect(screen.queryByText(/Done:/)).toBeNull();
  });
});

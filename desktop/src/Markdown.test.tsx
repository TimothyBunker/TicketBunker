import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import Markdown, { claimTag } from "./Markdown";

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

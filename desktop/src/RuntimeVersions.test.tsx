import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import RuntimeVersions, { backendVersion, versionsMismatch } from "./RuntimeVersions";
import type { AppState } from "./types";

afterEach(cleanup);
it("names both live component versions without checking the release service", () => {
  const state = { runtime: { version: __TB_UI_VERSION__ } } as AppState;
  render(<dl><RuntimeVersions state={state} /></dl>);
  expect(screen.getByText("Interface version")).toBeInTheDocument();
  expect(screen.getByText("Backend version")).toBeInTheDocument();
  expect(screen.getAllByText(__TB_UI_VERSION__)).toHaveLength(2);
  expect(versionsMismatch(state)).toBe(false);
});
it("uses the running backend over release-cache information and explains mismatch", () => {
  const state = { runtime: { version: "0.0.1" }, release: { current: __TB_UI_VERSION__ } } as AppState;
  render(<dl><RuntimeVersions state={state} /></dl>);
  expect(backendVersion(state)).toBe("0.0.1");
  expect(versionsMismatch(state)).toBe(true);
  expect(screen.getByRole("alert")).toHaveTextContent("Close and reopen");
});
it("does not invent a backend version when an old worker reports none", () => {
  render(<dl><RuntimeVersions state={{} as AppState} /></dl>);
  expect(screen.getByText("Not reported")).toBeInTheDocument();
});

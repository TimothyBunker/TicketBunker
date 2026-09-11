import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TicketCatalog } from "./BunkerHub";
import type { Bunker, Project } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
const bunker = { id: "lab" } as Bunker;
const project = { id: "one" } as Project;
const empty = { tickets: [], total: 0, page: 1, page_count: 0, facets: { statuses: [], assignees: [], reporters: [], labels: [] } };
const populated = { ...empty, total: 1, tickets: [{ id: "QA-1", title: "Saved ticket", status: "Open", labels: [], updated: "", assignee: "" }] };
beforeEach(() => { transport.command.mockReset(); });
afterEach(cleanup);

it("retries the same filtered list without replaying sync and preserves saved rows on failure", async () => {
  transport.command.mockResolvedValue(populated);
  const props = { bunker, project, onTicket: vi.fn() };
  const view = render(<TicketCatalog {...props} version={0} />);
  await screen.findByText("QA-1");
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "all" } });
  await waitFor(() => expect(transport.command).toHaveBeenLastCalledWith("application.ticket.list", expect.objectContaining({ status: "all" })));
  await screen.findByText("QA-1");
  transport.command.mockRejectedValueOnce(new Error("Local service interrupted"));
  view.rerender(<TicketCatalog {...props} version={1} />);
  await screen.findByRole("alert");
  expect(screen.getByText("QA-1")).toBeVisible();
  expect(screen.queryByText("No tickets here")).not.toBeInTheDocument();
  const failedPayload = transport.command.mock.lastCall![1];
  fireEvent.click(screen.getByRole("button", { name: "Retry loading tickets" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(transport.command).toHaveBeenLastCalledWith("application.ticket.list", failedPayload);
  expect(transport.command.mock.calls.every(([action]) => action === "application.ticket.list")).toBe(true);
});

it("does not claim an empty list while a refresh is pending or failed", async () => {
  transport.command.mockResolvedValueOnce(empty);
  const props = { bunker, project, onTicket: vi.fn() };
  const view = render(<TicketCatalog {...props} version={0} />);
  await screen.findByText("No tickets here");
  let reject!: (reason: Error) => void;
  transport.command.mockImplementationOnce(() => new Promise((_, no) => { reject = no; }));
  view.rerender(<TicketCatalog {...props} version={1} />);
  expect(screen.getByRole("status")).toHaveTextContent("Updating tickets");
  expect(screen.queryByText("No tickets here")).not.toBeInTheDocument();
  await act(async () => reject(new Error("Offline")));
  expect(screen.queryByText("No tickets here")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry loading tickets" })).toBeVisible();
});

it("does not display the previous project's tickets when a new project fails to load", async () => {
  transport.command.mockResolvedValueOnce(populated).mockRejectedValue(new Error("Offline"));
  const view = render(<TicketCatalog bunker={bunker} project={project} onTicket={vi.fn()} />);
  await screen.findByText("QA-1");
  view.rerender(<TicketCatalog bunker={bunker} project={{ id: "two" } as Project} onTicket={vi.fn()} />);
  await screen.findByRole("alert");
  expect(screen.queryByText("QA-1")).not.toBeInTheDocument();
});

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import WorkspaceEditor from "./WorkspaceEditor";

const fake = vi.hoisted(() => {
  type Model = { getValue: () => string; setValue: (value: string) => void; dispose: () => void; getValueInRange: () => string };
  let model: Model | null = null;
  let contentChanged = () => {};
  let selectionChanged = () => {};
  let save = () => {};
  const marks = { set: vi.fn() };
  const instance = {
    createDecorationsCollection: vi.fn(() => marks),
    addCommand: vi.fn((_key: number, callback: () => void) => { save = callback; }),
    onDidChangeModelContent: vi.fn((callback: () => void) => { contentChanged = callback; return { dispose: vi.fn() }; }),
    onDidChangeCursorSelection: vi.fn((callback: () => void) => { selectionChanged = callback; return { dispose: vi.fn() }; }),
    getSelection: vi.fn(() => ({ positionLineNumber: 4, startLineNumber: 3, endLineNumber: 4, isEmpty: () => false })),
    getModel: vi.fn(() => model),
    getValue: vi.fn(() => model!.getValue()),
    setValue: vi.fn((value: string) => model!.setValue(value)),
    setModel: vi.fn((value: Model) => { model = value; }),
    setPosition: vi.fn(), revealLineInCenter: vi.fn(), restoreViewState: vi.fn(),
    saveViewState: vi.fn(() => ({ cursorState: [] })), updateOptions: vi.fn(), dispose: vi.fn(),
  };
  const createModel = vi.fn((initial: string) => {
    let value = initial;
    return { getValue: () => value, setValue: vi.fn((next: string) => { value = next; contentChanged(); }), dispose: vi.fn(), getValueInRange: () => "selected source" };
  });
  return { instance, marks, create: vi.fn(() => instance), createModel, type: (value: string) => model!.setValue(value), select: () => selectionChanged(), save: () => save() };
});
vi.mock("monaco-editor/editor/editor.api.js", () => ({
  editor: { create: fake.create, createModel: fake.createModel }, KeyMod: { CtrlCmd: 1 }, KeyCode: { KeyS: 2 },
  Range: class { constructor(public startLineNumber: number, public startColumn: number, public endLineNumber: number, public endColumn: number) {} },
}));
vi.mock("monaco-editor/editor/editor.worker.js?worker", () => ({ default: class {} }));
vi.mock("monaco-editor/editor/contrib/find/browser/findController.js", () => ({}));
vi.mock("monaco-editor/editor/contrib/folding/browser/folding.js", () => ({}));
vi.mock("monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/cpp/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/python/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/javascript/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/typescript/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/markdown/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/shell/register.js", () => ({}));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

it("updates only changed settings, not editor options or the model on each keystroke", () => {
  const props = { path: "src/queue.py", value: "queue = []", line: 1, tabSize: 4, wrap: false, tabs: false,
    onChange: vi.fn(), onLine: vi.fn(), onSelection: vi.fn(), onSave: vi.fn() };
  const rendered = render(<WorkspaceEditor {...props} />);
  expect(fake.instance.updateOptions).toHaveBeenCalledTimes(1);
  expect(fake.create).toHaveBeenCalledTimes(1);
  for (let index = 1; index <= 12; index++) {
    fake.type(`queue = [${index}]`);
    rendered.rerender(<WorkspaceEditor {...props} value={`queue = [${index}]`} line={index} />);
  }
  expect(props.onChange).toHaveBeenCalledTimes(12);
  expect(fake.instance.updateOptions).toHaveBeenCalledTimes(1);
  expect(fake.instance.setModel).toHaveBeenCalledTimes(1);
  expect(fake.instance.setValue).not.toHaveBeenCalled();
  rendered.rerender(<WorkspaceEditor {...props} value="queue = [12]" tabSize={2} wrap />);
  expect(fake.instance.updateOptions).toHaveBeenCalledTimes(2);
  expect(fake.instance.updateOptions).toHaveBeenLastCalledWith({ tabSize: 2, insertSpaces: true, wordWrap: "on" });
});

it("reuses each file model and view state while callbacks always target the latest file", () => {
  const first = { path: "src/queue.py", value: "queue = []", line: 1,
    onChange: vi.fn(), onLine: vi.fn(), onSelection: vi.fn(), onSave: vi.fn() };
  const second = { ...first, path: "src/worker.py", value: "run()", onChange: vi.fn(), onLine: vi.fn(), onSelection: vi.fn(), onSave: vi.fn() };
  const rendered = render(<WorkspaceEditor {...first} />);
  const firstModel = fake.createModel.mock.results[0].value;
  fake.type("queue = [task]");
  rendered.rerender(<WorkspaceEditor {...first} value="queue = [task]" />);
  rendered.rerender(<WorkspaceEditor {...second} />);
  fake.type("run(task)"); fake.select(); fake.save();
  expect(first.onSave).not.toHaveBeenCalled();
  expect(second.onSave).toHaveBeenCalledOnce();
  expect(second.onChange).toHaveBeenLastCalledWith("run(task)");
  expect(second.onSelection).toHaveBeenCalledWith({ text: "selected source", line_start: 3, line_end: 4 });
  rendered.rerender(<WorkspaceEditor {...first} value="queue = [task]" />);
  expect(fake.createModel).toHaveBeenCalledTimes(2);
  expect(fake.instance.getModel()).toBe(firstModel);
  expect(fake.instance.getValue()).toBe("queue = [task]");
  expect(fake.instance.restoreViewState).toHaveBeenCalledOnce();
  rendered.unmount();
  expect(fake.instance.dispose).toHaveBeenCalledOnce();
  for (const result of fake.createModel.mock.results) expect(result.value.dispose).toHaveBeenCalledOnce();
});

it("uses nontrusted note decorations and does not redecorate unchanged notes on typing", () => {
  const notes = [{ line: 3, endLine: 4, text: "DEMO-1: guard this input <script>bad()</script>" }];
  const props = { path: "src/queue.py", value: "queue = []", line: 1, notes,
    onChange: vi.fn(), onLine: vi.fn(), onSelection: vi.fn(), onSave: vi.fn() };
  const rendered = render(<WorkspaceEditor {...props} />);
  expect(fake.marks.set).toHaveBeenCalledWith([expect.objectContaining({ options: expect.objectContaining({ glyphMarginHoverMessage: { value: notes[0].text, isTrusted: false, supportHtml: false } }) })]);
  fake.type("queue = [task]");
  rendered.rerender(<WorkspaceEditor {...props} value="queue = [task]" />);
  expect(fake.marks.set).toHaveBeenCalledOnce();
});

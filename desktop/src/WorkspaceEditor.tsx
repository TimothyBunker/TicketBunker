import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import "monaco-editor/editor/contrib/find/browser/findController.js";
import "monaco-editor/editor/contrib/folding/browser/folding.js";
import "monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js";
import "monaco-editor/languages/definitions/cpp/register.js";
import "monaco-editor/languages/definitions/python/register.js";
import "monaco-editor/languages/definitions/javascript/register.js";
import "monaco-editor/languages/definitions/typescript/register.js";
import "monaco-editor/languages/definitions/markdown/register.js";
import "monaco-editor/languages/definitions/shell/register.js";

globalThis.MonacoEnvironment = { getWorker: () => new EditorWorker() };

export type CodeSelection = { text: string; line_start: number; line_end: number };
type Props = { path: string; value: string; line: number; tabSize?: number; wrap?: boolean; tabs?: boolean; onSave: () => void;
  jump?: { line: number; sequence: number }; notes?: Array<{ line: number; endLine: number; text: string }>;
  onChange: (value: string) => void; onLine: (line: number) => void; onSelection: (value: CodeSelection | null) => void };
const language = (path: string) => ({ cpp: "cpp", cc: "cpp", c: "cpp", h: "cpp", hpp: "cpp", py: "python", js: "javascript", ts: "typescript", tsx: "typescript", md: "markdown", sh: "shell" }[path.split(".").pop() || ""] || "plaintext");

export default function WorkspaceEditor(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const models = useRef(new Map<string, { model: monaco.editor.ITextModel; view: monaco.editor.ICodeEditorViewState | null }>());
  const current = useRef(props);
  current.current = props;
  const changing = useRef(false);
  const marks = useRef<monaco.editor.IEditorDecorationsCollection | null>(null);
  useEffect(() => {
    const instance = monaco.editor.create(host.current!, { automaticLayout: true, editContext: false, minimap: { enabled: false }, fontSize: 13,
      scrollBeyondLastLine: false, glyphMargin: true, ariaLabel: "Repository file editor", fixedOverflowWidgets: true });
    editor.current = instance;
    marks.current = instance.createDecorationsCollection();
    instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => current.current.onSave());
    const content = instance.onDidChangeModelContent(() => { if (!changing.current) current.current.onChange(instance.getValue()); });
    const selection = instance.onDidChangeCursorSelection(() => {
      if (changing.current) return;
      const range = instance.getSelection();
      if (range) current.current.onLine(range.positionLineNumber);
      current.current.onSelection(range && !range.isEmpty() ? { text: instance.getModel()!.getValueInRange(range), line_start: range.startLineNumber, line_end: range.endLineNumber } : null);
    });
    return () => { content.dispose(); selection.dispose(); instance.dispose(); models.current.forEach(({ model }) => model.dispose()); models.current.clear(); };
  }, []);
  useEffect(() => {
    const instance = editor.current!;
    changing.current = true;
    let state = models.current.get(props.path);
    if (!state) { state = { model: monaco.editor.createModel(props.value, language(props.path)), view: null }; models.current.set(props.path, state); }
    instance.setModel(state.model);
    if (state.model.getValue() !== props.value) state.model.setValue(props.value);
    if (state.view) instance.restoreViewState(state.view);
    else { instance.setPosition({ lineNumber: props.line || 1, column: 1 }); instance.revealLineInCenter(props.line || 1); }
    changing.current = false;
    return () => { state!.view = instance.saveViewState(); };
  }, [props.path]);
  useEffect(() => {
    if (props.jump) { editor.current?.setPosition({ lineNumber: props.jump.line, column: 1 }); editor.current?.revealLineInCenter(props.jump.line); }
  }, [props.jump?.sequence]);
  useEffect(() => {
    marks.current?.set((props.notes || []).map((note) => ({ range: new monaco.Range(note.line, 1, note.endLine, 1), options: { isWholeLine: true, glyphMarginClassName: "workspace-note-marker", glyphMarginHoverMessage: { value: note.text, isTrusted: false, supportHtml: false } } })));
  }, [props.path, props.notes]);
  useEffect(() => {
    const instance = editor.current!;
    if (instance.getModel() && instance.getValue() !== props.value) {
      changing.current = true; instance.setValue(props.value); changing.current = false;
    }
  }, [props.value]);
  useEffect(() => {
    editor.current?.updateOptions({ tabSize: props.tabSize || 4, insertSpaces: !props.tabs, wordWrap: props.wrap ? "on" : "off" });
  }, [props.tabSize, props.wrap, props.tabs]);
  return <div ref={host} className="workspace-code-editor" />;
}

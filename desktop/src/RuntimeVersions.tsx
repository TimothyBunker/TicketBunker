import type { AppState } from "./types";

export const backendVersion = (state: AppState) => state.runtime?.version || state.release?.current || "";
export const versionsMismatch = (state: AppState) => Boolean(backendVersion(state) && backendVersion(state) !== __TB_UI_VERSION__);

export default function RuntimeVersions({ state }: { state: AppState }) {
  return <>
    <div><dt>Interface version</dt><dd>{__TB_UI_VERSION__}</dd></div>
    <div><dt>Backend version</dt><dd>{backendVersion(state) || "Not reported"}</dd></div>
    {versionsMismatch(state) && <div><dt>Version mismatch</dt><dd role="alert">Close and reopen TicketBunker. If these still differ, reinstall the same release for both components.</dd></div>}
  </>;
}

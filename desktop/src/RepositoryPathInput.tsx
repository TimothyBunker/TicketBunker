import { useRef, useState } from "react";
import { chooseGitDirectory, command } from "./transport";
import IconButton from "./IconButton";

type Listing = { path: string; parent: string; locations: string[]; directories: Array<{ name: string; path: string }>; truncated: boolean };

export default function RepositoryPathInput({ value, onChange, disabled }: { value: string; onChange: (path: string) => void; disabled?: boolean }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(0);
  async function browse(path: string) {
    const id = ++request.current;
    setBusy(true); setError("");
    try {
      const result = await command<Listing>("application.directory.list", { path });
      if (id === request.current) setListing(result);
    } catch (reason) { if (id === request.current) setError(String(reason)); }
    finally { if (id === request.current) setBusy(false); }
  }
  async function choose() {
    if (!window.__TAURI__?.dialog) return browse(value);
    setBusy(true); setError("");
    try { const path = await chooseGitDirectory(value || undefined); if (path) onChange(path); }
    catch { setError("The system folder picker could not open. Enter the path or use Browse here."); }
    finally { setBusy(false); }
  }
  return <div className="repository-path-input">
    <div className="repository-path-row"><label>Local repository path<input autoFocus value={value} onChange={(event) => onChange(event.target.value)} required disabled={disabled} /></label><IconButton icon="folder" label="Browse folders" hint="Choose a local repository folder" disabled={disabled || busy} onClick={choose} /></div>
    {error && <div role="alert">{error}<button type="button" disabled={disabled || busy} onClick={() => browse("")}>Browse here</button></div>}
    {busy && <p role="status">Opening folder…</p>}
    {listing && <section className="repository-folder-picker" aria-label="Choose repository folder">
      <header><button type="button" aria-label="Parent folder" title="Parent folder" disabled={disabled || busy || listing.parent === listing.path} onClick={() => browse(listing.parent)}>↑</button><select aria-label="Folder location" value={listing.locations.includes(listing.path) ? listing.path : ""} disabled={disabled || busy} onChange={(event) => browse(event.target.value)}><option value="" disabled>Locations</option>{listing.locations.map((path) => <option key={path}>{path}</option>)}</select><span>{listing.path}</span></header>
      <ul>{listing.directories.map((folder) => <li key={folder.path}><button type="button" disabled={disabled || busy} onClick={() => browse(folder.path)}>{folder.name}</button></li>)}</ul>
      {!listing.directories.length && <p>No visible subfolders.</p>}
      {listing.truncated && <p>Large folder: some entries are omitted. Enter a more specific path above.</p>}
      <footer><button type="button" disabled={disabled} onClick={() => { request.current++; setBusy(false); setListing(null); }}>Cancel</button><button type="button" disabled={disabled || busy} onClick={() => { onChange(listing.path); setListing(null); }}>Use this folder</button></footer>
    </section>}
  </div>;
}

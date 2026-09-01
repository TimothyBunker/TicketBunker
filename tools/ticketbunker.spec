# PyInstaller release entry. Runtime dependencies remain stdlib-only; this is
# build tooling for producing a Python-free tb executable.
from pathlib import Path


root = Path(SPECPATH).parent
datas = [
    (str(root / "ticketbunker" / "app_assets"), "ticketbunker/app_assets"),
    (str(root / "ticketbunker" / "harness" / "pi" / "tb.ts"), "ticketbunker/harness/pi"),
    (str(root / "ticketbunker" / "harness" / "pi" / "tb_auth.ts"), "ticketbunker/harness/pi"),
    (str(root / "ticketbunker" / "runtime" / "pi-runtime.json"), "ticketbunker/runtime"),
    (str(root / "ticketbunker" / "licenses" / "pi-MIT.txt"), "ticketbunker/licenses"),
]

a = Analysis(
    [str(root / "tools" / "tb_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

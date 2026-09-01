# Product release signing

TicketBunker's installed application is the normal user interface. Tagged
releases therefore refuse to publish an unsigned Windows installer.

## Windows certificate

Obtain an Authenticode code-signing certificate from a trusted issuer. An EV
certificate receives immediate Microsoft SmartScreen reputation; an OV
certificate may need to build reputation. Export the certificate and private
key as a password-protected PFX, then add these GitHub Actions repository
secrets:

- `WINDOWS_CERTIFICATE`: the base64-encoded PFX bytes
- `WINDOWS_CERTIFICATE_PASSWORD`: the PFX export password

The release workflow imports the certificate into the ephemeral Windows runner,
signs the frozen `tb.exe`, lets Tauri sign the desktop executable and NSIS
installer, timestamps each signature, and rejects any output whose
Authenticode status is not `Valid`. Certificate bytes and passwords are never
written to the repository or release assets.

A manual workflow dispatch may build an unsigned artifact for testing. A `v*`
tag cannot publish through that path without both secrets.

## Release procedure

1. Confirm CI is green on the release commit.
2. Confirm the package version agrees in `pyproject.toml`, `desktop/package.json`,
   `desktop/src-tauri/Cargo.toml`, and `desktop/src-tauri/tauri.conf.json`.
3. Create and push the matching annotated `v<version>` tag.
4. The release workflow builds every platform from that tag, verifies the
   bundled core and Pi runtime, signs Windows output, creates adjacent SHA-256
   files, and publishes one GitHub Release.
5. Install the Windows setup executable and Linux Debian package on clean test
   machines. Open TicketBunker from the OS launcher, attach a repository through
   the folder picker, and verify Settings → System reports the published
   version.

The app's update check reads only GitHub's typed release response. It validates
the official repository URLs and opens the selected installer in the operating
system browser. It never loads release HTML into the privileged app or executes
a downloaded file.

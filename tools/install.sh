#!/bin/sh
set -eu

bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=$(sed -n 's/.*"ticketbunker_version": "\([^"]*\)".*/\1/p' "$bundle_dir/manifest.json")
if [ -z "$version" ]; then
  echo "cannot read TicketBunker version from $bundle_dir/manifest.json" >&2
  exit 1
fi
install_base=${XDG_DATA_HOME:-"$HOME/.local/share"}/ticketbunker/releases
install_dir=$install_base/$version
bin_dir=${TB_BIN_DIR:-"$HOME/.local/bin"}

mkdir -p "$install_base" "$bin_dir"
if [ -e "$install_dir" ]; then
  echo "TicketBunker $version is already installed at $install_dir" >&2
  exit 1
fi
if [ -e "$bin_dir/tb.$version" ] || [ -L "$bin_dir/tb.$version" ]; then
  echo "cannot install: $bin_dir/tb.$version already exists" >&2
  exit 1
fi
if { [ -e "$bin_dir/tb" ] || [ -L "$bin_dir/tb" ]; } && \
   { [ -e "$bin_dir/tb.previous" ] || [ -L "$bin_dir/tb.previous" ]; }; then
  echo "cannot preserve $bin_dir/tb: $bin_dir/tb.previous already exists" >&2
  exit 1
fi
cp -R "$bundle_dir" "$install_dir"
ln -s "$install_dir/bin/tb" "$bin_dir/tb.$version"
if [ -e "$bin_dir/tb" ] || [ -L "$bin_dir/tb" ]; then
  mv "$bin_dir/tb" "$bin_dir/tb.previous"
fi
ln -s "$install_dir/bin/tb" "$bin_dir/tb"

echo "installed TicketBunker $version at $install_dir"
echo "command: $bin_dir/tb"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "add $bin_dir to PATH, then run: tb init" ;;
esac

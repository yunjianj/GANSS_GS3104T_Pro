#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
builddir=$(mktemp -d)
trap 'rm -rf "$builddir"' EXIT
mkdir "$builddir/ganss-gs3104t-0.3.2"
cd "$root"
tar --exclude='__pycache__' -cf - pyproject.toml README.md NOTICE.md ganss udev docs packaging/assets | tar xf - -C "$builddir/ganss-gs3104t-0.3.2"
tar czf "$builddir/ganss-gs3104t-0.3.2.tar.gz" -C "$builddir" ganss-gs3104t-0.3.2
cp packaging/arch/PKGBUILD "$builddir/PKGBUILD"
cd "$builddir"
# Export both artifacts before the EXIT trap removes the temporary checkout.
makepkg --force --noconfirm
mkdir -p "$root/dist"
cp ./*.pkg.tar.zst "$root/dist/"
cp "$builddir/src/ganss-gs3104t-0.3.2/dist/"*.whl "$root/dist/"

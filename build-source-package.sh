#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${1:-$project_root/dist}"
version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$project_root/pyproject.toml" | head -n 1)"

if [[ -z "$version" ]]; then
  printf 'Could not read the project version from pyproject.toml.\n' >&2
  exit 1
fi

staging_dir="$(mktemp -d)"
trap 'rm -rf -- "$staging_dir"' EXIT

archive_root="xt005-$version"
staged_project="$staging_dir/$archive_root"
archive_name="xt005-$version-source.tar.gz"
mkdir -p "$staged_project" "$output_dir"

package_paths=(
  docs
  frontend
  .cdnlocal
  src
  .env.example
  build-source-package.sh
  xt005.service
  install.sh
  INSTALL.md
  pyproject.toml
  README.md
  requirements.txt
  requirements.lock
  rescue
  requirements-dev.txt
  run.sh
  uv.lock
)

if [[ -f "$project_root/LICENSE" ]]; then
  package_paths+=(LICENSE)
fi

for path in "${package_paths[@]}"; do
  if [[ ! -e "$project_root/$path" ]]; then
    printf 'Package input is missing: %s\n' "$path" >&2
    exit 1
  fi
  cp -a "$project_root/$path" "$staged_project/"
done

find "$staged_project" -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "$staged_project" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
chmod +x "$staged_project/build-source-package.sh" "$staged_project/install.sh" "$staged_project/run.sh"

archive_path="$(realpath -m "$output_dir")/$archive_name"
tar \
  --sort=name \
  --mtime="@${SOURCE_DATE_EPOCH:-0}" \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  -czf "$archive_path" \
  -C "$staging_dir" \
  "$archive_root"

printf '%s\n' "$archive_path"

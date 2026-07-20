#!/usr/bin/env bash
# Regenerate PNG application icons from the master SVG.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ICON_DIR="${PROJECT_DIR}/assets/icons"
SVG="${ICON_DIR}/byteback.svg"

if [[ ! -f "${SVG}" ]]; then
    echo "Missing ${SVG}" >&2
    exit 1
fi

if ! command -v convert >/dev/null 2>&1; then
    echo "ImageMagick 'convert' is required." >&2
    exit 1
fi

for size in 256 128 64 48; do
    convert -background none -density 384 "${SVG}" \
        -resize "${size}x${size}" \
        "${ICON_DIR}/byteback-${size}.png"
done

cp "${ICON_DIR}/byteback-256.png" "${ICON_DIR}/byteback.png"
echo "Icons written to ${ICON_DIR}"

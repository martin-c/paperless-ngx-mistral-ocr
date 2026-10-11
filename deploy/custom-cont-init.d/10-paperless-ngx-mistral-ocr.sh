#!/usr/bin/env bash
# Install the paperless-ngx-mistral-ocr parser plugin into the Paperless-ngx container.
#
# Paperless-ngx runs every file in /custom-cont-init.d as root with
# "/command/with-contenv /bin/bash <file>" before the webserver starts
# (docker/rootfs/etc/s6-overlay/s6-rc.d/init-custom-init/run, v3.3.0). The
# directory and its entries must be owned by root and not world-writable, or
# the runner refuses to execute.
#
# Expected layout of the mounted directory, either a built wheel:
#
#   /custom-cont-init.d/10-paperless-ngx-mistral-ocr.sh         (this file)
#   /custom-cont-init.d/paperless_ngx_mistral_ocr-<version>-py3-none-any.whl
#
# or the plugin source tree:
#
#   /custom-cont-init.d/paperless-ngx-mistral-ocr/pyproject.toml
#   /custom-cont-init.d/paperless-ngx-mistral-ocr/src/paperless_mistral_ocr/...
#
# A wheel is preferred when both exist. PAPERLESS_MISTRAL_OCR_SRC overrides
# the source directory location.
#
# Paperless-ngx v3.3.0 installs its own Python dependencies with
# "uv pip install --system" into the image's system interpreter (Dockerfile),
# so the plugin is installed into that same environment the same way.
set -euo pipefail

log() { echo "[mistral-ocr-init] $*"; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_src="${PAPERLESS_MISTRAL_OCR_SRC:-${script_dir}/paperless-ngx-mistral-ocr}"
package="paperless-ngx-mistral-ocr"

# Pick the newest wheel next to this script, if any.
wheel=""
for candidate in "${script_dir}"/paperless_ngx_mistral_ocr-*.whl; do
    [ -f "${candidate}" ] || continue
    if [ -z "${wheel}" ] || [ "${candidate}" -nt "${wheel}" ]; then
        wheel="${candidate}"
    fi
done

if [ -n "${wheel}" ]; then
    install_target="${wheel}"
    wanted="$(basename "${wheel}" | sed -n 's/^paperless_ngx_mistral_ocr-\([^-]*\)-.*\.whl$/\1/p')"
elif [ -f "${plugin_src}/pyproject.toml" ]; then
    install_target="${plugin_src}"
    wanted="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' "${plugin_src}/pyproject.toml" | head -n 1)"
else
    log "ERROR: neither a paperless_ngx_mistral_ocr-*.whl next to this script nor a source tree at ${plugin_src}"
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    log "ERROR: uv not found on PATH (${PATH}); cannot install ${package}"
    exit 1
fi

installed="$(uv pip show --system "${package}" 2>/dev/null | sed -n 's/^Version: //p' || true)"

if [ -n "${wanted}" ] && [ "${installed}" = "${wanted}" ]; then
    log "${package} ${installed} is already installed; nothing to do"
    exit 0
fi

log "installing ${package} ${wanted:-?} from ${install_target} (currently: ${installed:-not installed})"
uv pip install --system --no-cache --no-python-downloads --python-preference system "${install_target}"
log "done: $(uv pip show --system "${package}" | sed -n 's/^Version: /installed version /p')"

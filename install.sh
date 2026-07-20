#!/usr/bin/env bash
#
# ByteBack installation script for Linux.
#
# Usage:
#   ./install.sh              # user install (default)
#   ./install.sh --system     # system-wide install (requires sudo)
#   ./install.sh --uninstall  # remove installed files
#

set -euo pipefail

APP_NAME="ByteBack"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_MODE="user"
ACTION="install"

usage() {
    cat <<EOF
${APP_NAME} installer

Usage:
  ./install.sh [--user|--system] [--uninstall]

Options:
  --user       Install for the current user only (default)
  --system     Install system-wide under /usr/local (requires sudo)
  --uninstall  Remove files created by a previous install
  -h, --help   Show this help message
EOF
}

log() {
    printf '[%s] %s\n' "${APP_NAME}" "$*"
}

die() {
    printf '[%s] ERROR: %s\n' "${APP_NAME}" "$*" >&2
    exit 1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --user)
                INSTALL_MODE="user"
                ;;
            --system)
                INSTALL_MODE="system"
                ;;
            --uninstall)
                ACTION="uninstall"
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
        shift
    done
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        die "Required command not found: $1"
    fi
}

install_packages_debian() {
    if ! command -v apt-get >/dev/null 2>&1; then
        log "apt-get not found – skipping automatic package installation."
        return
    fi

    local packages=(python3 python3-tk parted util-linux)
    local missing=()

    for package in "${packages[@]}"; do
        if ! dpkg -s "${package}" >/dev/null 2>&1; then
            missing+=("${package}")
        fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
        log "All required apt packages are already installed."
        return
    fi

    log "Installing missing packages: ${missing[*]}"
    if [[ "${EUID}" -eq 0 ]]; then
        apt-get update
        apt-get install -y "${missing[@]}"
    elif command -v pkexec >/dev/null 2>&1; then
        pkexec apt-get update
        pkexec apt-get install -y "${missing[@]}"
    elif command -v sudo >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    else
        die "Missing packages (${missing[*]}). Install them manually and rerun."
    fi
}

install_python_deps() {
    if [[ ! -f "${SCRIPT_DIR}/requirements.txt" ]]; then
        return
    fi

    log "Installing optional Python dependencies…"
    if python3 -m pip --version >/dev/null 2>&1; then
        python3 -m pip install --user -r "${SCRIPT_DIR}/requirements.txt" || \
            log "Optional pip install failed – continuing without python-magic."
        return
    fi

    if command -v pip3 >/dev/null 2>&1; then
        pip3 install --user -r "${SCRIPT_DIR}/requirements.txt" || \
            log "Optional pip install failed – continuing without python-magic."
    fi
}

set_install_paths() {
    if [[ "${INSTALL_MODE}" == "system" ]]; then
        APP_DIR="/usr/local/share/byteback"
        BIN_DIR="/usr/local/bin"
        DESKTOP_DIR="/usr/local/share/applications"
        ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"
        LAUNCHER="${BIN_DIR}/byteback"
    else
        APP_DIR="${HOME}/.local/share/byteback"
        BIN_DIR="${HOME}/.local/bin"
        DESKTOP_DIR="${HOME}/.local/share/applications"
        ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
        LAUNCHER="${BIN_DIR}/byteback"
    fi
}

install_files() {
    set_install_paths
    log "Installing to ${APP_DIR} (${INSTALL_MODE} mode)"

    if [[ "${INSTALL_MODE}" == "system" && "${EUID}" -ne 0 ]]; then
        die "System install requires root. Run: sudo ./install.sh --system"
    fi

    mkdir -p "${APP_DIR}" "${BIN_DIR}" "${DESKTOP_DIR}" "${ICON_DIR}"

    rsync -a --delete \
        --exclude '.git' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        "${SCRIPT_DIR}/" "${APP_DIR}/"

    cat >"${LAUNCHER}" <<EOF
#!/usr/bin/env bash
cd "${APP_DIR}"
exec python3 "${APP_DIR}/run.py" "\$@"
EOF
    chmod +x "${LAUNCHER}"

    sed "s|@LAUNCHER@|${LAUNCHER}|g" \
        "${SCRIPT_DIR}/assets/ByteBack.desktop" \
        >"${DESKTOP_DIR}/ByteBack.desktop"

    sed "s|@LAUNCHER@|${LAUNCHER}|g" \
        "${SCRIPT_DIR}/assets/ByteBack-Admin.desktop" \
        >"${DESKTOP_DIR}/ByteBack-Admin.desktop"

    cp "${SCRIPT_DIR}/assets/icons/byteback.svg" "${ICON_DIR}/byteback.svg"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
    fi

    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "${ICON_DIR}/.." >/dev/null 2>&1 || true
    fi

    log "Installation complete."
    log "Launcher: ${LAUNCHER}"
    log "Desktop entries: ${DESKTOP_DIR}/ByteBack.desktop"
    log "Admin desktop:   ${DESKTOP_DIR}/ByteBack-Admin.desktop"
    log ""
    log "Use 'ByteBack' for mounted partitions."
    log "Use 'ByteBack (Admin)' for raw disks and unallocated space."
}

uninstall_files() {
    set_install_paths
    log "Removing ${APP_NAME} (${INSTALL_MODE} mode)"

    rm -f "${LAUNCHER}"
    rm -f "${DESKTOP_DIR}/ByteBack.desktop"
    rm -f "${DESKTOP_DIR}/ByteBack-Admin.desktop"
    rm -f "${ICON_DIR}/byteback.svg"
    rm -rf "${APP_DIR}"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
    fi

    log "Uninstall complete."
}

main() {
    parse_args "$@"
    require_command python3
    require_command rsync

    if [[ "${ACTION}" == "uninstall" ]]; then
        uninstall_files
        exit 0
    fi

    install_packages_debian
    install_python_deps
    install_files
}

main "$@"

#!/usr/bin/env bash

set -Eeuo pipefail

readonly MIN_PYTHON="3.12"
readonly DEFAULT_MARIVO_EXTRAS="duckdb,trino,clickhouse"
readonly PYPI_INDEX_URL="https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"
readonly UV_PYTHON_INSTALL_MIRROR="https://registry.npmmirror.com/-/binary/python-build-standalone"
readonly UV_INSTALL_SH_URL="https://astral.sh/uv/install.sh"
TARGET_DIR="$(pwd -P)"
readonly TARGET_DIR
readonly VENV_DIR="$TARGET_DIR/.venv"
PLATFORM=""
VENV_PYTHON=""
VENV_MARIVO=""
VENV_ACTIVATE=""
ASSUME_YES=0
CURRENT_STAGE="startup"

on_error() {
    local status=$?
    printf 'error: stage "%s" failed with exit code %s\n' "$CURRENT_STAGE" "$status" >&2
    exit "$status"
}
trap on_error ERR

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

stage() {
    CURRENT_STAGE=$1
    printf '\n==> %s\n' "$CURRENT_STAGE"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --yes) ASSUME_YES=1 ;;
            *) die "unknown argument: $1 (supported: --yes)" ;;
        esac
        shift
    done
}

is_windows_bash() {
    [[ "$PLATFORM" == MINGW* || "$PLATFORM" == MSYS* || "$PLATFORM" == CYGWIN* ]]
}

validate_platform() {
    PLATFORM="$(uname -s)"
    case "$PLATFORM" in
        Darwin|Linux)
            VENV_PYTHON="$VENV_DIR/bin/python"
            VENV_MARIVO="$VENV_DIR/bin/marivo"
            VENV_ACTIVATE="$VENV_DIR/bin/activate"
            ;;
        MINGW*|MSYS*|CYGWIN*)
            VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
            VENV_MARIVO="$VENV_DIR/Scripts/marivo.exe"
            VENV_ACTIVATE="$VENV_DIR/Scripts/activate"
            ;;
        *) die "unsupported operating system: $PLATFORM (supported: macOS, Linux, WSL, and Windows Bash shells)" ;;
    esac
}

validate_target() {
    local command_name
    for command_name in uname mktemp rm mkdir; do
        command -v "$command_name" >/dev/null 2>&1 || \
            die "required command is unavailable: $command_name"
    done
    [ -d "$TARGET_DIR" ] || die "target directory does not exist: $TARGET_DIR"
    [ -w "$TARGET_DIR" ] || die "target directory is not writable: $TARGET_DIR"
}

python_is_supported() {
    local interpreter=$1
    [ -x "$interpreter" ] && "$interpreter" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
        >/dev/null 2>&1
}

venv_matches_target() {
    python_is_supported "$VENV_PYTHON" && "$VENV_PYTHON" -c \
        'import os, sys; expected = os.path.realpath(sys.argv[1]); actual = os.path.realpath(sys.prefix); raise SystemExit(0 if actual == expected else 1)' \
        "$VENV_DIR" >/dev/null 2>&1
}

confirm_venv_replacement() {
    if [ "$ASSUME_YES" -eq 1 ]; then
        return
    fi
    if [ ! -t 0 ]; then
        die "invalid virtual environment at $VENV_DIR; rerun with --yes to replace it"
    fi
    printf 'Replace invalid virtual environment %s? [y/N] ' "$VENV_DIR" >&2
    local answer
    read -r answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *) die "virtual environment replacement declined" ;;
    esac
}

prepare_existing_venv() {
    if [ ! -e "$VENV_DIR" ]; then
        return 1
    fi
    if venv_matches_target; then
        printf 'Reusing valid virtual environment: %s\n' "$VENV_DIR"
        return 0
    fi
    confirm_venv_replacement
    rm -rf -- "$VENV_DIR"
    [ ! -e "$VENV_DIR" ] || die "could not remove invalid virtual environment: $VENV_DIR"
    return 1
}

ensure_download_tool() {
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || \
        die "uv installation requires curl or wget"
}

ensure_uv() {
    if command -v uv >/dev/null 2>&1 && uv --version >/dev/null 2>&1; then
        command -v uv
        return
    fi

    if is_windows_bash; then
        local powershell
        powershell="$(command -v powershell.exe || command -v powershell || command -v pwsh || true)"
        [ -n "$powershell" ] || die "Windows uv installation requires PowerShell"
        if ! "$powershell" -NoProfile -ExecutionPolicy Bypass -Command \
            '$env:UV_NO_MODIFY_PATH="1"; irm https://astral.sh/uv/install.ps1 | iex' >&2; then
            die "could not install uv with the official PowerShell installer"
        fi
        local windows_candidate
        for windows_candidate in "$HOME/.local/bin/uv.exe" "${USERPROFILE:-}/.local/bin/uv.exe"; do
            if [ -x "$windows_candidate" ] && "$windows_candidate" --version >/dev/null 2>&1; then
                printf '%s\n' "$windows_candidate"
                return
            fi
        done
        die "uv installation completed but no working uv executable was found"
    fi

    ensure_download_tool
    local installer
    installer="$(mktemp "${TMPDIR:-/tmp}/marivo-uv-install.XXXXXX")"
    if command -v curl >/dev/null 2>&1; then
        if ! curl -LsSf "$UV_INSTALL_SH_URL" -o "$installer"; then
            rm -f "$installer"
            die "could not download the uv installer from $UV_INSTALL_SH_URL"
        fi
    elif ! wget -qO "$installer" "$UV_INSTALL_SH_URL"; then
        rm -f "$installer"
        die "could not download the uv installer from $UV_INSTALL_SH_URL"
    fi
    if ! UV_NO_MODIFY_PATH=1 sh "$installer" >&2; then
        rm -f "$installer"
        die "uv installation failed; rerun after checking network and filesystem permissions"
    fi
    rm -f "$installer"

    local candidate
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ] && "$candidate" --version >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    die "uv installation completed but no working uv executable was found"
}

find_managed_python() {
    local uv_bin=$1
    UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_INSTALL_MIRROR" "$uv_bin" python install "$MIN_PYTHON" >&2
    local interpreter
    interpreter="$("$uv_bin" python find --managed-python "$MIN_PYTHON")"
    python_is_supported "$interpreter" || \
        die "uv-managed Python failed the >=$MIN_PYTHON validation"
    printf '%s\n' "$interpreter"
}

create_venv() {
    local uv_bin=$1
    local interpreter=$2
    "$uv_bin" venv --python "$interpreter" --seed "$VENV_DIR"
    venv_matches_target || die "created virtual environment failed validation: $VENV_DIR"
}

install_marivo() {
    local uv_bin=$1
    UV_INDEX_URL="$PYPI_INDEX_URL" "$uv_bin" pip install --python "$VENV_PYTHON" --upgrade "marivo[$DEFAULT_MARIVO_EXTRAS]"
}

validate_marivo() {
    [ -x "$VENV_MARIVO" ] || die "Marivo executable is missing: $VENV_MARIVO"
    "$VENV_PYTHON" -c 'import marivo; print(f"marivo {marivo.__version__}")'
    "$VENV_MARIVO" --version
}

warn_missing_skill_links() {
    local skill_path
    for skill_path in \
        .agents/skills/marivo-semantic \
        .agents/skills/marivo-analysis \
        .claude/skills/marivo-semantic \
        .claude/skills/marivo-analysis \
        .codex/skills/marivo-semantic \
        .codex/skills/marivo-analysis; do
        if [ ! -e "$TARGET_DIR/$skill_path" ] && [ ! -L "$TARGET_DIR/$skill_path" ]; then
            printf 'warning: optional init artifact is missing: %s\n' \
                "$TARGET_DIR/$skill_path" >&2
        fi
    done
}

initialize_project() {
    "$VENV_MARIVO" init
    [ -f "$TARGET_DIR/marivo.toml" ] || \
        die "missing required init artifact: $TARGET_DIR/marivo.toml; rerun $VENV_MARIVO init"
    [ -d "$TARGET_DIR/models" ] || \
        die "missing required init artifact: $TARGET_DIR/models; rerun $VENV_MARIVO init"
    [ -d "$TARGET_DIR/.marivo" ] || \
        die "missing required init artifact: $TARGET_DIR/.marivo; rerun $VENV_MARIVO init"
    warn_missing_skill_links
}

print_summary() {
    printf '\nMarivo setup completed.\n'
    printf '  Project: %s\n' "$TARGET_DIR"
    printf '  Python:  %s\n' "$VENV_PYTHON"
    printf '  Marivo:  %s\n' "$VENV_MARIVO"
    printf 'Activate with: source %s\n' "${VENV_ACTIVATE#"$TARGET_DIR/"}"
}

main() {
    parse_args "$@"

    stage "Validate platform and target"
    validate_platform
    validate_target
    printf 'Target project: %s\n' "$TARGET_DIR"

    stage "Inspect virtual environment"
    local has_venv=0
    if prepare_existing_venv; then
        has_venv=1
    fi

    stage "Prepare uv and Python >=$MIN_PYTHON"
    local uv_bin interpreter
    uv_bin="$(ensure_uv)"
    interpreter="$(find_managed_python "$uv_bin")"
    printf 'Using uv-managed Python: %s\n' "$interpreter"

    if [ "$has_venv" -eq 0 ]; then
        stage "Create virtual environment"
        create_venv "$uv_bin" "$interpreter"
    fi

    stage "Install or upgrade Marivo"
    install_marivo "$uv_bin"

    stage "Validate Marivo installation"
    validate_marivo

    stage "Initialize Marivo project"
    initialize_project
    print_summary
}

main "$@"

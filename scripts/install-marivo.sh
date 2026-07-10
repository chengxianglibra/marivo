#!/usr/bin/env bash

set -Eeuo pipefail

readonly MIN_PYTHON="3.12"
TARGET_DIR="$(pwd -P)"
readonly TARGET_DIR
readonly VENV_DIR="$TARGET_DIR/.venv"
readonly VENV_PYTHON="$VENV_DIR/bin/python"
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

validate_platform() {
    local platform
    platform="$(uname -s)"
    case "$platform" in
        Darwin|Linux) ;;
        MINGW*|MSYS*|CYGWIN*)
            die "native Windows Bash is not supported. Use Windows Subsystem for Linux (WSL)."
            ;;
        *) die "unsupported operating system: $platform (supported: macOS and Linux, including WSL)" ;;
    esac
}

validate_target() {
    local command_name
    for command_name in uname mktemp rm mkdir grep; do
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
    if venv_matches_target && { \
        "$VENV_PYTHON" -m pip --version >/dev/null 2>&1 || \
        { "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 && \
            "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; }; \
    }; then
        printf 'Reusing valid virtual environment: %s\n' "$VENV_DIR"
        return 0
    fi
    confirm_venv_replacement
    rm -rf -- "$VENV_DIR"
    [ ! -e "$VENV_DIR" ] || die "could not remove invalid virtual environment: $VENV_DIR"
    return 1
}

find_local_python() {
    local name candidate
    for name in python3.14 python3.13 python3.12 python3; do
        if command -v "$name" >/dev/null 2>&1; then
            candidate="$(command -v "$name")"
            if python_is_supported "$candidate"; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    done
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

    ensure_download_tool
    local installer
    installer="$(mktemp "${TMPDIR:-/tmp}/marivo-uv-install.XXXXXX")"
    if command -v curl >/dev/null 2>&1; then
        if ! curl -LsSf https://astral.sh/uv/install.sh -o "$installer"; then
            rm -f "$installer"
            die "could not download the uv installer from https://astral.sh/uv/install.sh"
        fi
    elif ! wget -qO "$installer" https://astral.sh/uv/install.sh; then
        rm -f "$installer"
        die "could not download the uv installer from https://astral.sh/uv/install.sh"
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
    "$uv_bin" python install "$MIN_PYTHON" >&2
    local interpreter
    interpreter="$("$uv_bin" python find --managed-python "$MIN_PYTHON")"
    python_is_supported "$interpreter" || \
        die "uv-managed Python failed the >=$MIN_PYTHON validation"
    printf '%s\n' "$interpreter"
}

create_venv() {
    local interpreter=$1
    local uv_bin=${2:-}
    local needs_uv=0

    if ! "$interpreter" -m venv "$VENV_DIR"; then
        needs_uv=1
    elif ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1 && \
        ! "$VENV_PYTHON" -m ensurepip --upgrade; then
        needs_uv=1
    fi

    if [ "$needs_uv" -eq 1 ]; then
        rm -rf -- "$VENV_DIR"
        if [ -z "$uv_bin" ]; then
            uv_bin="$(ensure_uv)"
        fi
        "$uv_bin" venv --python "$interpreter" --seed "$VENV_DIR"
    fi

    venv_matches_target || die "created virtual environment failed validation: $VENV_DIR"
    "$VENV_PYTHON" -m pip --version >/dev/null 2>&1 || \
        die "created virtual environment has no working pip: $VENV_DIR"
}

ensure_pip() {
    if "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
        return
    fi
    "$VENV_PYTHON" -m ensurepip --upgrade
    "$VENV_PYTHON" -m pip --version >/dev/null 2>&1 || \
        die "pip is unavailable in $VENV_DIR"
}

install_marivo() {
    ensure_pip
    "$VENV_PYTHON" -m pip install --upgrade pip
    "$VENV_PYTHON" -m pip install --upgrade "marivo[all]"
}

validate_marivo() {
    local marivo_bin="$VENV_DIR/bin/marivo"
    [ -x "$marivo_bin" ] || die "Marivo executable is missing: $marivo_bin"
    "$VENV_PYTHON" -m pip --version | grep -F "$VENV_DIR" >/dev/null || \
        die "pip does not resolve inside $VENV_DIR"
    "$VENV_PYTHON" -c 'import marivo; print(f"marivo {marivo.__version__}")'
    "$marivo_bin" --version
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
    local marivo_bin="$VENV_DIR/bin/marivo"
    "$marivo_bin" init
    [ -f "$TARGET_DIR/marivo.toml" ] || \
        die "missing required init artifact: $TARGET_DIR/marivo.toml; rerun $marivo_bin init"
    [ -d "$TARGET_DIR/models" ] || \
        die "missing required init artifact: $TARGET_DIR/models; rerun $marivo_bin init"
    [ -d "$TARGET_DIR/.marivo" ] || \
        die "missing required init artifact: $TARGET_DIR/.marivo; rerun $marivo_bin init"
    warn_missing_skill_links
}

print_summary() {
    printf '\nMarivo setup completed.\n'
    printf '  Project: %s\n' "$TARGET_DIR"
    printf '  Python:  %s\n' "$VENV_PYTHON"
    printf '  Marivo:  %s\n' "$VENV_DIR/bin/marivo"
    printf 'Activate with: source .venv/bin/activate\n'
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

    if [ "$has_venv" -eq 0 ]; then
        stage "Select Python >=$MIN_PYTHON"
        local interpreter uv_bin=""
        if interpreter="$(find_local_python)"; then
            printf 'Using local Python: %s\n' "$interpreter"
        else
            stage "Install managed Python $MIN_PYTHON"
            uv_bin="$(ensure_uv)"
            interpreter="$(find_managed_python "$uv_bin")"
            printf 'Using uv-managed Python: %s\n' "$interpreter"
        fi

        stage "Create virtual environment"
        create_venv "$interpreter" "$uv_bin"
    fi

    stage "Install or upgrade Marivo"
    install_marivo

    stage "Validate Marivo installation"
    validate_marivo

    stage "Initialize Marivo project"
    initialize_project
    print_summary
}

main "$@"

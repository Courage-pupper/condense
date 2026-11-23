#!/usr/bin/env bash
# Condense installer and updater (macOS on Apple Silicon, Linux).
#
# Install — paste into Terminal:
#   curl -fsSL https://raw.githubusercontent.com/s-hurtado26/condense/main/install.sh | bash
# With options for the platform installer, e.g. neural speaker detection or a Linux GPU type:
#   curl -fsSL https://raw.githubusercontent.com/s-hurtado26/condense/main/install.sh | bash -s -- --with-pyannote
#
# Update: Condense ▸ Check for Updates…, or run
#   ~/.condense/install.sh --update
#
# Options of this script (all others go to scripts/install-mac.sh / scripts/install-linux.sh and are remembered):
#   --update          update an existing installation with the options used before; no sudo / Homebrew needed
#   --version vX.Y.Z  install that release instead of the latest
#   --ref BRANCH      install from a branch (for testing unreleased changes)
#   --check           only print the installed and the latest version
set -euo pipefail

REPO="${CONDENSE_REPO:-s-hurtado26/condense}"
API="${CONDENSE_API:-https://api.github.com/repos/$REPO}"
DOWNLOAD="${CONDENSE_DOWNLOAD:-https://github.com/$REPO/archive/refs}"
CONDENSE_HOME="${CONDENSE_HOME:-$HOME/.condense}"
OPTIONS_FILE="$CONDENSE_HOME/install-options"

# Is there a terminal to ask for passwords on? (/dev/tty can exist without being usable, e.g. when started from the app)
have_tty() { (exec < /dev/tty) 2>/dev/null; }

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

# Everything runs from main(), called on the last line: with `curl … | bash` the whole script is read first,
# and a cut-off download cannot run half a script.
main() {
  local update=0 version="" ref="" check=0
  local -a pass=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --update) update=1; shift ;;
      --version) version="${2:-}"; [ -n "$version" ] || die "--version needs a value like v0.10.0"; shift 2 ;;
      --ref) ref="${2:-}"; [ -n "$ref" ] || die "--ref needs a branch name"; shift 2 ;;
      --check) check=1; shift ;;
      -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null | sed 's/^# \{0,1\}//' || true; exit 0 ;;
      *) pass+=("$1"); shift ;;
    esac
  done

  local os="${CONDENSE_OS:-$(uname -s)}"
  case "$os" in
    Darwin) os="mac" ;;
    Linux) os="linux" ;;
    *) die "Condense runs on macOS and Linux (this is $os)." ;;
  esac
  command -v curl >/dev/null 2>&1 || die "curl is needed (Linux: sudo apt install curl)."
  command -v tar >/dev/null 2>&1 || die "tar is needed."

  # ---- which version
  local tag url
  if [ -n "$ref" ]; then
    tag="$ref"; url="$DOWNLOAD/heads/$ref.tar.gz"
  else
    local meta
    if [ -n "$version" ]; then
      meta="$(curl -fsSL "$API/releases/tags/$version")" || die "Release $version not found."
    elif ! meta="$(curl -fsSL "$API/releases/latest" 2>/dev/null)"; then
      if curl -fsS -o /dev/null "$API" 2>/dev/null; then
        die "No Condense release has been published yet: https://github.com/$REPO/releases"
      fi
      die "Could not reach GitHub to find the latest release (offline?)."
    fi
    tag="$(printf '%s' "$meta" | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
    [ -n "$tag" ] || die "Could not read the release information from GitHub."
    url="$DOWNLOAD/tags/$tag.tar.gz"
  fi

  local installed=""
  [ -f "$CONDENSE_HOME/installed-version" ] && installed="$(cat "$CONDENSE_HOME/installed-version")"
  if [ "$check" = "1" ]; then
    echo "installed: ${installed:-none}"; echo "latest:    $tag"; exit 0
  fi

  # ---- options: given now, or the ones remembered from the first installation
  if [ "$update" = "1" ] && [ "${#pass[@]}" -eq 0 ] && [ -f "$OPTIONS_FILE" ]; then
    while IFS= read -r line; do [ -n "$line" ] && pass+=("$line"); done < "$OPTIONS_FILE"
  fi
  if [ "$update" = "1" ] && [ -z "$installed" ] && [ ! -d "$CONDENSE_HOME/env" ] && [ ! -f "$OPTIONS_FILE" ]; then
    die "No Condense installation found to update. Install it first (without --update)."
  fi

  # ---- macOS: Homebrew (only for a new installation; updates do not touch it)
  if [ "$os" = "mac" ] && [ "$update" = "0" ]; then
    if ! command -v brew >/dev/null 2>&1; then
      for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
      done
    fi
    if ! command -v brew >/dev/null 2>&1; then
      have_tty || die "Homebrew is needed. Run this command in Terminal, or install Homebrew from https://brew.sh first."
      say "Installing Homebrew first (https://brew.sh) — it asks for your Mac password"
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty
      for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
      done
      command -v brew >/dev/null 2>&1 || die "Homebrew did not install. Install it from https://brew.sh and run this again."
    fi
  fi

  # ---- download and unpack the release
  say "Downloading Condense $tag"
  local dir="$CONDENSE_HOME/releases/$tag"
  rm -rf "$dir.part"
  mkdir -p "$dir.part"
  curl -fsSL "$url" | tar xz -C "$dir.part" --strip-components 1 || die "Download of $url failed."
  [ -f "$dir.part/scripts/install-$os.sh" ] || die "The download does not contain scripts/install-$os.sh."
  rm -rf "$dir"
  mv "$dir.part" "$dir"

  # ---- run the platform installer
  local -a extra=()
  if [ "$update" = "1" ]; then
    if [ "$os" = "mac" ]; then extra=(--skip-brew); else extra=(--skip-system); fi
  fi
  say "Running the $os installer"
  # sudo / apt may ask for a password: give them the terminal, not the piped script
  if [ -t 0 ] || [ "$update" = "1" ] || ! have_tty; then
    bash "$dir/scripts/install-$os.sh" ${pass[@]+"${pass[@]}"} ${extra[@]+"${extra[@]}"}
  else
    bash "$dir/scripts/install-$os.sh" ${pass[@]+"${pass[@]}"} ${extra[@]+"${extra[@]}"} < /dev/tty
  fi

  # ---- remember what was installed, and how
  mkdir -p "$CONDENSE_HOME"
  if [ "$update" = "0" ]; then
    : > "$OPTIONS_FILE"
    for o in ${pass[@]+"${pass[@]}"}; do printf '%s\n' "$o" >> "$OPTIONS_FILE"; done
  fi
  printf '%s\n' "$tag" > "$CONDENSE_HOME/installed-version"
  cp "$dir/install.sh" "$CONDENSE_HOME/install.sh"   # the updater updates itself
  chmod +x "$CONDENSE_HOME/install.sh"

  # keep the two newest releases (the previous one is a fallback), remove older ones
  local keep
  keep="$(ls -1t "$CONDENSE_HOME/releases" 2>/dev/null | grep -v '\.part$' | head -n 2 | tr '\n' ' ')"
  for d in "$CONDENSE_HOME/releases"/*; do
    [ -d "$d" ] || continue
    case " $keep " in *" $(basename "$d") "*) ;; *) rm -rf "$d" ;; esac
  done

  say "Condense $tag is installed"
  if [ "$update" = "1" ]; then
    echo "Restart Condense to use the new version."
  elif [ "$os" = "mac" ]; then
    echo "Open it from Launchpad or Spotlight (Condense). Later updates: Condense ▸ Check for Updates…"
  else
    echo "Open it from your app menu (Condense). Later updates: Help ▸ Check for Updates…"
  fi
}

main "$@"

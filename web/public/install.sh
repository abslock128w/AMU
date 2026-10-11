#!/usr/bin/env sh
# Keep this file identical to web/public/install.sh (covered by tests).
set -eu

fail() {
  printf '%s\n' "Install failed: $*" >&2
  exit 3
}
usage() {
  printf '%s\n' "Usage: sh install.sh (--commit <full-40-character-commit> | --local <wheel-or-project-directory>) [--entire-bin <absolute-entire-path> --entire-sha256 <digest> --graph-bin <absolute-graph-path> --graph-sha256 <digest>]"
  printf '%s\n' "Supply all four engine flags to verify existing binaries without installing or changing the engine. Obtain SHA256 pins from a trusted release/build record. Default engine installation is unpinned and is not production verified."
}
if [ "$#" -eq 1 ] && [ "$1" = "--help" ]; then
  usage
  exit 0
fi
source_kind=''
source_value=''
entire_bin=''
entire_sha256=''
graph_bin=''
graph_sha256=''
strict_engine=0
while [ "$#" -gt 0 ]; do
  [ "$#" -ge 2 ] && [ -n "$2" ] || { usage >&2; exit 2; }
  case "$1" in
    --commit|--local)
      [ -z "$source_kind" ] || fail "select exactly one package source"
      source_kind="$1"; source_value="$2";;
    --entire-bin) [ -z "$entire_bin" ] || fail "duplicate --entire-bin"; entire_bin="$2"; strict_engine=1;;
    --entire-sha256) [ -z "$entire_sha256" ] || fail "duplicate --entire-sha256"; entire_sha256="$2"; strict_engine=1;;
    --graph-bin) [ -z "$graph_bin" ] || fail "duplicate --graph-bin"; graph_bin="$2"; strict_engine=1;;
    --graph-sha256) [ -z "$graph_sha256" ] || fail "duplicate --graph-sha256"; graph_sha256="$2"; strict_engine=1;;
    *) usage >&2; exit 2;;
  esac
  shift 2
done
case "$source_kind" in
  --commit)
    case "$source_value" in ''|*[!0-9a-fA-F]*) fail "use a full hexadecimal commit, not a branch or tag";; esac
    [ "${#source_value}" -eq 40 ] || fail "commit must have 40 hexadecimal characters"
    target="git+https://github.com/fayasfarmisofficial-cyber/TWC-Thugs@$source_value"
    constraints="https://raw.githubusercontent.com/fayasfarmisofficial-cyber/TWC-Thugs/$source_value/constraints.txt"
    ;;
  --local)
    case "$source_value" in /*) target="$source_value";; *) target="$PWD/$source_value";; esac
    if [ -d "$target" ] && [ -f "$target/pyproject.toml" ]; then
      constraints="$target/constraints.txt"
    else
      case "$target" in
        *.whl)
          [ -f "$target" ] || fail "local wheel does not exist"
          constraints="${target%/*}/constraints.txt"
          ;;
        *) fail "local target must be a wheel or a project with pyproject.toml";;
      esac
    fi
    [ -r "$constraints" ] && [ -s "$constraints" ] || fail "matching constraints.txt is required beside the wheel or in the project"
    ;;
  *) usage >&2; exit 2;;
esac

verify_engine_identity() {
  python3 - "$entire_bin" "$entire_sha256" "$graph_bin" "$graph_sha256" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

def reject(message):
    raise ValueError(message)

def binary(raw, expected, label):
    path = Path(raw)
    if not path.is_absolute() or any(char in raw for char in ('\n', '\r', ':')):
        reject(label + ' must be an absolute executable path without PATH separators')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        reject(label + ' SHA256 must contain exactly 64 hexadecimal characters')
    path = path.resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        reject(label + ' must be an existing executable file')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else None
        if digest is None:
            hash_value = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hash_value.update(chunk)
            digest = hash_value.hexdigest()
    if digest != expected.lower():
        reject(label + ' SHA256 mismatch; selected binary was not executed')
    return path

def run(executable, *args):
    result = subprocess.run([str(executable), *args], capture_output=True, text=True, timeout=30)
    if result.returncode or not result.stdout.strip():
        reject('engine preflight failed: ' + ' '.join(args) + ' (exit ' + str(result.returncode) + ')')
    return result.stdout

try:
    entire_raw, entire_hash, graph_raw, graph_hash = sys.argv[1:]
    # Check BOTH pins before executing either selected binary.
    entire = binary(entire_raw, entire_hash, 'Entire dispatcher')
    graph = binary(graph_raw, graph_hash, 'Graph engine')
    if Path(entire_raw).name != 'entire':
        reject('--entire-bin must select an executable named entire so AMU resolves the same dispatcher')
    path_entries = os.environ.get('PATH', '').split(os.pathsep)
    if any(not item or not Path(item).is_absolute() for item in path_entries):
        reject('verified-engine mode requires absolute, nonempty PATH entries')
    selected = shutil.which('entire')
    if not selected or Path(selected).resolve(strict=True) != entire:
        reject('PATH does not resolve the selected Entire dispatcher')
    root_help = run(entire_raw, '--help')
    if 'entire [command]' not in root_help or re.search(r'^\s{2,}graph\s{2,}', root_help, re.MULTILINE):
        reject('cannot prove graph dispatch: unknown CLI command layout or built-in graph command')
    help_text = run(entire_raw, 'plugin', '--help')
    if ("external executables named 'entire-<name>'" not in help_text
            or 'auto-prepended to PATH at startup' not in help_text):
        reject('cannot prove graph dispatch: selected CLI does not document managed-directory PATH precedence')
    listing = run(entire_raw, 'plugin', 'list')
    directories = re.findall(r'^Managed plugin directory: (.+)$', listing, re.MULTILINE)
    if len(directories) != 1 or not Path(directories[0]).is_absolute():
        reject('cannot prove graph dispatch: CLI did not report one absolute managed plugin directory')
    # This is the documented dispatcher lookup order, including managed symlinks.
    effective = shutil.which('entire-graph', path=os.pathsep.join([directories[0], *path_entries]))
    if not effective or Path(effective).resolve(strict=True) != graph:
        reject('graph dispatch mismatch: effective provider ' + str(effective) + ' differs from --graph-bin')
    version = json.loads(run(entire_raw, 'graph', 'version', '--json'))
    direct_version = json.loads(run(graph_raw, 'version', '--json'))
    if (not isinstance(version, dict) or version.get('provider') != 'entire-graph'
            or not isinstance(version.get('version'), str) or not version['version'].strip()
            or version != direct_version):
        reject('graph dispatch preflight returned inconsistent provider/version identity')
    capabilities = json.loads(run(entire_raw, 'graph', 'capabilities', '--json'))
    if (not isinstance(capabilities, dict) or capabilities.get('provider') != 'entire-graph'
            or not isinstance(capabilities.get('supported_languages'), list)
            or not capabilities['supported_languages']):
        reject('graph capabilities preflight did not identify an operational entire-graph provider')
    # Catch replacements or retargeted symlinks during the preflight too.
    if binary(entire_raw, entire_hash, 'Entire dispatcher') != entire or binary(graph_raw, graph_hash, 'Graph engine') != graph:
        reject('selected engine path changed during verification')
    if Path(effective).resolve(strict=True) != graph:
        reject('effective graph provider changed during verification')
    print('Engine identity verified against supplied SHA256 pins: ' + str(entire) + ' + ' + str(graph))
except (OSError, ValueError, subprocess.SubprocessError) as error:
    print('Install failed: ' + str(error), file=sys.stderr)
    sys.exit(3)
PY
}

if [ "$strict_engine" -eq 1 ]; then
  [ -n "$entire_bin" ] && [ -n "$entire_sha256" ] && [ -n "$graph_bin" ] && [ -n "$graph_sha256" ] || fail "verified-engine mode requires all four engine flags"
  case "$entire_bin" in /*) :;; *) fail "--entire-bin must be absolute";; esac
  command -v python3 >/dev/null 2>&1 || fail "verified-engine mode requires Python 3 for local SHA256 and dispatch checks"
  entire_dir="${entire_bin%/*}"
  PATH="$entire_dir:$PATH"
  export PATH
  verify_engine_identity || fail "engine identity or preflight failed; no package was installed"
else
  entire_bin=entire
  printf '%s\n' "Engine identity: unpinned; default engine installation is not production verified."
fi

command -v pipx >/dev/null 2>&1 || fail "install pipx first; no fallback to system Python"
amu_bin=$(pipx environment --value PIPX_BIN_DIR) || fail "cannot locate pipx executable directory"
case "$amu_bin" in /*) :;; *) fail "pipx executable directory must be absolute";; esac

if [ "$strict_engine" -eq 0 ] && ! command -v entire >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || fail "install Entire CLI first: https://docs.entire.io"
  brew tap entireio/tap || fail "Homebrew tap failed"
  brew trust entireio/tap || fail "Homebrew trust failed"
  brew install --cask entire || fail "Entire CLI installation failed"
fi
if [ "$strict_engine" -eq 0 ] && ! entire graph version >/dev/null 2>&1; then
  entire plugin install graph || fail "graph plugin installation failed"
  entire graph version >/dev/null 2>&1 || fail "graph plugin is still unavailable"
fi

printf '%s\n' "Installing selected source: $target"
PIP_CONSTRAINT="$constraints" pipx install --force --backend pip --pip-args=--force-reinstall "$target" || fail "constrained package install/upgrade failed (pipx with --backend support required); inspect pipx state before retrying"
pipx runpip amu check || fail "installed dependencies failed validation; no automatic rollback"
[ -x "$amu_bin/amu" ] || fail "pipx did not expose the amu command"
if [ "$strict_engine" -eq 1 ]; then
  PATH="$entire_dir:$amu_bin:$PATH"
else
  PATH="$amu_bin:$PATH"
fi
export PATH
version=$("$amu_bin/amu" --version) || fail "installed command failed; no automatic rollback"
if [ "$strict_engine" -eq 1 ]; then
  verify_engine_identity || fail "engine changed after package installation; package is not ready"
fi
"$entire_bin" amu doctor || fail "health check failed; package may be installed but is not ready"
if [ "$strict_engine" -eq 1 ]; then
  verify_engine_identity || fail "engine changed during health check; package is not ready"
fi
printf '%s\n' "installed: $version"
printf '%s\n' "For future shells, ensure $amu_bin is on PATH."
printf '%s\n' "next: cd <your repo> && amu init && amu map --file <file>"

#!/usr/bin/env bash
# Build rind and publish a release on GitHub.
#
#   ./build.sh                 release the version already in pyproject.toml
#   ./build.sh patch|minor|major   bump the version first (0.8.0 -> 0.8.1 / 0.9.0 / 1.0.0)
#   ./build.sh 1.2.3           release exactly that version
#
# What it does: checks the repo is clean and up to date, runs the tests, builds the wheel and
# source archive into dist/, smoke-tests the wheel in a fresh virtual environment, writes
# SHA256SUMS, commits the version bump, tags vX.Y.Z, pushes the branch and the tag, and creates
# the GitHub release with the files attached. `rind --update` on other machines then finds it.
#
# GitHub access: the `gh` CLI (gh auth login), or a token in GITHUB_TOKEN / GH_TOKEN with
# permission to write repository contents. With neither, the script stops after pushing the tag
# and tells you how to finish in the browser.
#
# Options:  --notes FILE   release notes (default: the commit messages since the last tag)
#           --draft        create the release as a draft
#           --dry-run      do everything except commit, tag, push and publish
#           --quick        skip the tests and the wheel smoke test
#           --yes          don't ask for confirmation
#           --remote NAME  git remote to push to (default: origin)
#           --any-branch   allow releasing from a branch other than main
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

REMOTE=origin; NOTES_FILE=""; DRAFT=0; DRY=0; QUICK=0; YES=0; ANY_BRANCH=0; BUMP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --notes)      NOTES_FILE="${2:?--notes needs a file}"; shift ;;
    --draft)      DRAFT=1 ;;
    --dry-run)    DRY=1 ;;
    --quick)      QUICK=1 ;;
    --yes|-y)     YES=1 ;;
    --remote)     REMOTE="${2:?--remote needs a name}"; shift ;;
    --any-branch) ANY_BRANCH=1 ;;
    -h|--help)    sed -n '2,/^set -euo/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    patch|minor|major) BUMP="$1" ;;
    [0-9]*.[0-9]*.[0-9]*) BUMP="$1" ;;
    *) echo "Unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\nError: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

PY="$(command -v python3 || command -v python || true)"; [ -n "$PY" ] || die "python3 is required"
have git || die "git is required"
git rev-parse --git-dir >/dev/null 2>&1 || die "run this from the rind git repository"

TMP="$(mktemp -d)"
BUMPED=0
cleanup() {
  rm -rf "$TMP"
  if [ "$DRY" = 1 ] && [ "$BUMPED" = 1 ]; then git checkout -q -- pyproject.toml rind/__init__.py; fi
}
trap cleanup EXIT

# ---------------------------------------------------------------- repository state
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || [ "$ANY_BRANCH" = 1 ] || die "you are on '$BRANCH'; release from main (or pass --any-branch)"
[ -z "$(git status --porcelain)" ] || die "uncommitted changes. Commit or stash them first:
$(git status --short)"
git remote get-url "$REMOTE" >/dev/null 2>&1 || die "no git remote called '$REMOTE'"
REMOTE_URL="$(git remote get-url "$REMOTE")"
SLUG="$(printf '%s' "$REMOTE_URL" | sed -E 's#^(git@|ssh://git@|https?://)(www\.)?github\.com[:/]##; s#\.git$##; s#/$##')"
case "$SLUG" in */*) ;; *) die "'$REMOTE' ($REMOTE_URL) doesn't look like a GitHub repository" ;; esac

say "Checking $REMOTE ($SLUG)"
git fetch --quiet --tags "$REMOTE" || die "could not reach $REMOTE"
if git rev-parse --verify --quiet "$REMOTE/$BRANCH" >/dev/null; then
  [ "$(git rev-list --count "HEAD..$REMOTE/$BRANCH")" = 0 ] || die "$REMOTE/$BRANCH has commits you don't have. Pull first."
fi

# ---------------------------------------------------------------- versions
read_version() { sed -nE 's/^version = "([^"]+)".*/\1/p' pyproject.toml | head -n1; }
read_init()    { sed -nE 's/^__version__ = "([^"]+)".*/\1/p' rind/__init__.py | head -n1; }
CUR="$(read_version)"; INIT="$(read_init)"
[ -n "$CUR" ] || die "no version found in pyproject.toml"
[ "$CUR" = "$INIT" ] || die "pyproject.toml says $CUR but rind/__init__.py says $INIT. Make them match."

case "$BUMP" in
  "")  VER="$CUR" ;;
  patch|minor|major)
    IFS=. read -r MA MI PA <<<"$CUR"
    case "$BUMP" in
      patch) PA=$((PA + 1)) ;;
      minor) MI=$((MI + 1)); PA=0 ;;
      major) MA=$((MA + 1)); MI=0; PA=0 ;;
    esac
    VER="$MA.$MI.$PA" ;;
  *) VER="$BUMP" ;;
esac
[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version '$VER' must look like 1.2.3"
TAG="v$VER"
if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
  die "$TAG already exists. Use ./build.sh patch|minor|major for a new version."
fi
if [ "$VER" != "$CUR" ]; then
  "$PY" - "$VER" <<'PYEOF'
import re, sys
v = sys.argv[1]
for path, pat, rep in (("pyproject.toml", r'(?m)^version = ".*"$', f'version = "{v}"'),
                       ("rind/__init__.py", r'(?m)^__version__ = ".*"$', f'__version__ = "{v}"')):
    s = open(path).read()
    new, n = re.subn(pat, rep, s, count=1)
    if n != 1:
        sys.exit(f"could not update the version in {path}")
    open(path, "w").write(new)
PYEOF
  BUMPED=1
  say "Version $CUR -> $VER"
else
  say "Releasing version $VER"
fi

# ---------------------------------------------------------------- tests
if [ "$QUICK" = 0 ]; then
  say "Running tests"
  "$PY" -m compileall -q rind || die "syntax error"
  FAILED=0
  for t in tests/test_*.py; do
    if timeout 300 "$PY" "$t" >"$TMP/test.log" 2>&1; then note "ok    $t"
    else note "FAIL  $t"; tail -n 8 "$TMP/test.log" | sed 's/^/          /'; FAILED=1; fi
  done
  [ "$FAILED" = 0 ] || die "tests failed; fix them (or use --quick to skip, at your own risk)"
fi

# ---------------------------------------------------------------- build
say "Building"
rm -rf dist build ./*.egg-info
if "$PY" -m build --version >/dev/null 2>&1; then
  "$PY" -m build --outdir dist >"$TMP/build.log" 2>&1 || { cat "$TMP/build.log"; die "build failed"; }
elif have uv; then
  uv build --out-dir dist >"$TMP/build.log" 2>&1 || { cat "$TMP/build.log"; die "build failed"; }
else
  "$PY" -m venv "$TMP/buildenv" && "$TMP/buildenv/bin/pip" install -q build \
    && "$TMP/buildenv/bin/python" -m build --outdir dist >"$TMP/build.log" 2>&1 \
    || { cat "$TMP/build.log" 2>/dev/null; die "build failed (install the 'build' package, or uv)"; }
fi
WHEEL="$(ls dist/rind-"$VER"-*.whl 2>/dev/null | head -n1 || true)"
SDIST="$(ls dist/rind-"$VER".tar.gz 2>/dev/null | head -n1 || true)"
[ -n "$WHEEL" ] && [ -n "$SDIST" ] || die "expected rind-$VER wheel and tar.gz in dist/, found: $(ls dist 2>/dev/null | tr '\n' ' ')"
( cd dist && sha256sum "$(basename "$WHEEL")" "$(basename "$SDIST")" > SHA256SUMS )
note "$(basename "$WHEEL"), $(basename "$SDIST"), SHA256SUMS"

if [ "$QUICK" = 0 ]; then
  say "Smoke-testing the wheel in a fresh environment"
  "$PY" -m venv "$TMP/smoke" || die "could not create a virtual environment"
  "$TMP/smoke/bin/pip" install -q "$WHEEL" >"$TMP/smoke.log" 2>&1 || { cat "$TMP/smoke.log"; die "the wheel does not install"; }
  GOT="$("$TMP/smoke/bin/rind" --version 2>&1 || true)"
  [ "$GOT" = "rind $VER" ] || die "installed wheel reports '$GOT', expected 'rind $VER'"
  "$TMP/smoke/bin/rind" --keys >/dev/null 2>&1 || die "the installed wheel fails on 'rind --keys'"
  note "installs and runs: $GOT"
fi

# ---------------------------------------------------------------- release notes
if [ -n "$NOTES_FILE" ]; then
  [ -f "$NOTES_FILE" ] || die "notes file '$NOTES_FILE' not found"
  cp "$NOTES_FILE" "$TMP/notes.md"
else
  PREV="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
  {
    echo "## rind $VER"; echo
    if [ -n "$PREV" ]; then git log --no-merges --pretty='- %s' "$PREV..HEAD"; else git log --no-merges --pretty='- %s' -n 30; fi
    echo; echo "Install: \`curl -fsSL https://raw.githubusercontent.com/$SLUG/main/install.sh | bash\`  ·  Update: \`rind --update\`"
  } | grep -v '^- Release v' > "$TMP/notes.md"
fi
say "Release notes"; sed 's/^/    /' "$TMP/notes.md"

if [ "$DRY" = 1 ]; then
  say "Dry run finished. Nothing was committed, tagged, pushed or published."
  note "The built files are in dist/."
  exit 0
fi

# ---------------------------------------------------------------- confirm, commit, tag, push
if [ "$YES" = 0 ]; then
  printf '\nPublish %s to %s (branch %s)? [y/N] ' "$TAG" "$SLUG" "$BRANCH"
  read -r ANSWER </dev/tty || ANSWER=""
  case "$ANSWER" in y|Y|yes|YES) ;; *) say "Cancelled."; [ "$BUMPED" = 1 ] && git checkout -q -- pyproject.toml rind/__init__.py; BUMPED=0; exit 1 ;; esac
fi

say "Committing and tagging"
if [ "$BUMPED" = 1 ]; then
  git add pyproject.toml rind/__init__.py
  git commit -q -m "Release $TAG"
  BUMPED=0
fi
git tag -a "$TAG" -m "rind $VER"

say "Pushing to $REMOTE"
if ! git push "$REMOTE" "$BRANCH" || ! git push "$REMOTE" "$TAG"; then
  die "push failed. Nothing is published yet. To undo the local tag and retry:  git tag -d $TAG"
fi

# ---------------------------------------------------------------- GitHub release
FILES=("$WHEEL" "$SDIST" dist/SHA256SUMS)
URL="https://github.com/$SLUG/releases/tag/$TAG"
if have gh && gh auth status >/dev/null 2>&1; then
  say "Creating the GitHub release (gh)"
  ARGS=(--repo "$SLUG" --title "rind $VER" --notes-file "$TMP/notes.md" --verify-tag)
  [ "$DRAFT" = 1 ] && ARGS+=(--draft)
  gh release create "$TAG" "${FILES[@]}" "${ARGS[@]}" || die "gh could not create the release. The tag is pushed; finish at $URL"
elif [ -n "${GITHUB_TOKEN:-${GH_TOKEN:-}}" ]; then
  say "Creating the GitHub release (API token)"
  TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}" SLUG="$SLUG" TAG="$TAG" VER="$VER" DRAFT="$DRAFT" NOTES="$TMP/notes.md" \
  "$PY" - "${FILES[@]}" <<'PYEOF' || die "the API call failed. The tag is pushed; finish at $URL"
import json, os, sys, urllib.request, urllib.error

tok, slug = os.environ["TOKEN"], os.environ["SLUG"]
hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
       "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rind-build"}

def call(url, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, headers={**hdr, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub said {e.code}: {e.read().decode()[:300]}")

body = {"tag_name": os.environ["TAG"], "name": f"rind {os.environ['VER']}",
        "body": open(os.environ["NOTES"]).read(), "draft": os.environ["DRAFT"] == "1", "prerelease": False}
rel = call(f"https://api.github.com/repos/{slug}/releases", json.dumps(body).encode())
up = rel["upload_url"].split("{")[0]
for path in sys.argv[1:]:
    name = os.path.basename(path)
    with open(path, "rb") as f:
        call(f"{up}?name={name}", f.read(), "application/octet-stream")
    print(f"    uploaded {name}")
PYEOF
else
  say "The tag is pushed, but there is no way to create the release from here"
  note "Install the GitHub CLI and run 'gh auth login', or export GITHUB_TOKEN, then run:"
  note "  gh release create $TAG dist/* --repo $SLUG --title 'rind $VER' --notes-file <notes>"
  note "Or open https://github.com/$SLUG/releases/new?tag=$TAG and attach the three files in dist/."
  exit 0
fi

say "Released $TAG: $URL"
note "Installed copies pick it up with:  rind --update"

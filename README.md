# rind

A mouse-friendly terminal file manager for Linux, in the spirit of the Fresh text
editor: everything works by pointing and clicking, and everything also has a key.

- Sidebar of places (Home, `/`, XDG folders, Trash, your own entries, most-visited folders)
- Editable, copyable path bar with Tab completion
- Cut, copy, paste, rename, new folder, delete to a real trash, **undo**
- Conflict dialog (replace / skip / keep both), progress bar and cancel for big jobs
- **Auto-refresh** when files change on disk
- **Preview pane** for text, folders, archives, images and binaries
- **Filter** the current folder as you type, and **search** all subfolders
- Right-click context menus; sort by name, size, modified time or type
- A **menu bar** (File, Edit, View, Go, Tools, Help) with every command and its shortcut
- **Archives**: open zip/tar files like folders (read-only), extract, compress
- A clickable **breadcrumb** path, back **and forward** history, new file and duplicate
- **Tabs** and a true **split view** (two panes side by side), with copy/move to the other pane and **drag and drop** onto folders, sidebar places, tabs, panes, path segments and the Trash
- **Redo** as well as undo
- **Properties** (details, permissions and group/owner editor), an **Open with…** chooser, and a **Devices** list with mount/unmount
- **Jump** to frequently used folders by typing part of the path
- **Run a command** on the selection, or open a shell in the current folder
- **Git status markers** and the current branch
- Optional **permission / owner / group columns**, symlink targets, and **folder sizes** on demand
- **Bulk rename**: pattern (regex + counter) or in your `$EDITOR`
- Open files with the system default or tools you configure per extension
- **cd on exit**: your shell lands in the folder you were browsing
- **Drag files in**: drop files from your file manager onto the terminal window to copy or move them here
- **Get files out** with three workarounds (system clipboard, a ripdrag/dragon window, or your file manager)
- **Configurable shortcuts**: every key can be changed under `[keys]` in `config.toml`
- **Built-in updater**: `rind --update` (from GitHub releases)

rind is a small Python package built on [Textual](https://textual.textualize.io/).

---

## 1. Requirements

| Needed | Why |
|---|---|
| Python 3.10 or newer | runs rind (3.10 also installs `tomli` to read the config) |
| A terminal with mouse reporting | GNOME Terminal/Console, Konsole, kitty, Alacritty, WezTerm, foot, xterm and most others |

Optional:

| Package | Used for |
|---|---|
| `Pillow` (`pip install 'rind[images]'`) | image previews |
| `xdg-utils` | default "open file" behaviour (`xdg-open`) |
| `wl-clipboard` (Wayland) or `xclip` / `xsel` (X11) | "Copy path" into the system clipboard |

The trash needs nothing extra: rind implements the freedesktop.org Trash
specification itself, so `gio` is not required.

---

## 2. Installing

### Quick install (recommended)

```sh
curl -fsSL https://raw.githubusercontent.com/elvishcraftsman/rind/main/install.sh | bash
```

`install.sh` installs the newest release (or `main` until a release exists) with **uv**, else
**pipx**, else a private virtual environment under `~/.local/share/rind/venv` linked from
`~/.local/bin/rind`. It also adds Pillow for image previews (`RIND_NO_IMAGES=1` skips that),
needs Python 3.10 or newer, and can be re-run any time to upgrade. `RIND_REF=v0.8.0` installs a
specific tag or branch; `./install.sh --uninstall` removes rind again. Run from a checkout,
it installs that folder instead of downloading.

The manual methods below all start from the project folder (the one containing `pyproject.toml`).

### Option A: pipx or uv

```sh
cd rind
pipx install .                 # or: uv tool install .
rind
```

With image previews: `pipx install '.[images]'` (or `uv tool install '.[images]'`).

### Option B: a plain virtual environment (works everywhere)

```sh
python3 -m venv ~/.local/share/rind-venv
~/.local/share/rind-venv/bin/pip install './rind[images]'     # drop [images] if you don't want it
mkdir -p ~/.local/bin
ln -sf ~/.local/share/rind-venv/bin/rind ~/.local/bin/rind
```

Make sure `~/.local/bin` is on your `PATH`.

### Option C: run from the folder without installing

```sh
cd rind
python3 -m venv .venv && .venv/bin/pip install textual
.venv/bin/python -m rind
```

(A source folder can't update itself with `rind --update`; use `git pull`.)

### Prerequisites by distribution

| Distribution | Command |
|---|---|
| Fedora | `sudo dnf install python3 pipx xdg-utils wl-clipboard xclip` |
| Debian / Ubuntu / Mint | `sudo apt install python3-venv pipx xdg-utils wl-clipboard xclip` |
| Arch / Manjaro | `sudo pacman -S python python-pipx xdg-utils wl-clipboard xclip` |
| Other | Install Python 3.10+, then use Option B |

### Immutable distributions (Silverblue, Kinoite, Bazzite, and similar)

Install and run rind **on the host**, not inside a toolbox or distrobox container.
Inside a container, `xdg-open` and the trash act on the container, so opened files
may not launch host applications and trashed files land in the container's trash.
Option B needs no layered packages: the base image already ships Python.

### Shell integration (cd on exit)

Add one line to your shell startup file so quitting rind changes your shell's
folder to the one you were browsing:

```sh
# ~/.bashrc or ~/.zshrc
eval "$(rind --init bash)"        # use "zsh" in .zshrc

# ~/.config/fish/config.fish
rind --init fish | source
```

This defines a `rind` function that runs the real program and `cd`s afterwards.
Quit with `q` to change folder, or `Shift+Q` to leave your shell where it was.
(Tested with bash; the zsh version is the same script, the fish snippet is untested.)

### Uninstalling

```sh
pipx uninstall rind                       # or: uv tool uninstall rind
rm -rf ~/.local/share/rind-venv ~/.local/bin/rind     # if you used Option B
rm -rf ~/.config/rind ~/.local/state/rind             # optional: config and history
```

---

## 3. Updating

rind can update itself from published **GitHub releases**. The default source is
`github:elvishcraftsman/rind`, so out of the box rind checks that repository (at most once a day,
at start-up) and tells you when a newer release exists. It never installs anything on its own.
To turn the automatic check off, set `check = false` below; to use a fork, set `source`.

### Set up a source

Edit `~/.config/rind/config.toml`:

```toml
[update]
source = "github:elvishcraftsman/rind"   # the default; https://github.com/owner/repo works too
check = true                         # check automatically at start-up (default true)
interval_hours = 24                  # how often the automatic check runs
```

Two kinds of source are supported:

| Source | What rind does |
|---|---|
| `github:owner/repo` | asks GitHub for the latest **release**, then downloads its `rind-*.whl` file (checked against the release's `SHA256SUMS`), or the tag's source archive if there is no wheel |
| `https://host/path/latest.json` | reads a manifest and downloads the archive it points to |

A manifest looks like this (`sha256` is optional but recommended; relative URLs are
resolved against the manifest's address):

```json
{ "version": "0.4.0", "url": "rind-0.4.0.tar.gz", "sha256": "<hex digest>", "notes": "What changed" }
```

To bake a different default into your own copy, change `DEFAULT_SOURCE` at the top of
`rind/updater.py`.

### Updating

```sh
rind --update --check     # just report whether a newer version exists
rind --update             # show the new version, ask, then install
rind --update --yes       # no confirmation prompt
rind --update --update-source github:owner/repo     # one-off source override
```

Inside rind, press `u` to check now (a dialog offers to install). Unless `check = false`, rind also
checks quietly in the background at start-up (at most once per `interval_hours`) and shows a
notification. Until the first release is published, the check reports that there is none.

After an update, restart rind to run the new version.

### How the update is installed

rind detects how it was installed and uses the matching tool on the downloaded
archive: `pipx install --force` for pipx, `uv tool install --force` for uv, and
`pip install --upgrade` (in rind's own environment) otherwise. If rind is running from
a source folder, it won't touch your files; update the folder yourself.

### Safety and privacy

- Only `https://` sources are accepted (plain `http://` only for `localhost`).
- If a manifest gives a `sha256`, or a release carries `SHA256SUMS`, a mismatching download is refused.
- Installing runs the downloaded package's build, so **only point `source` at a
  repository you trust**. GitHub release archives carry no checksum, so for them
  you are trusting the repository and GitHub.
- The check sends a normal HTTPS request with a `User-Agent: rind/<version>` header
  and nothing else. Set `check = false` to disable the background check.

### For maintainers: publishing a release

```sh
./build.sh patch          # or minor / major / 1.2.3; with no argument it releases the current version
./build.sh --dry-run patch    # everything except commit, tag, push and publish
```

`build.sh` checks that the tree is clean and up to date, runs the tests, builds the wheel and
source archive into `dist/`, installs the wheel into a fresh environment to prove it runs, writes
`SHA256SUMS`, commits the version bump (both `pyproject.toml` and `rind/__init__.py`), tags
`vX.Y.Z`, pushes the branch and the tag, and creates the GitHub release with the three files
attached. Release notes default to the commit messages since the last tag (`--notes FILE` to write
your own, `--draft` to review on GitHub first, `--quick` to skip tests and the smoke test).

It publishes with the `gh` CLI if you ran `gh auth login`, otherwise with a token in
`GITHUB_TOKEN` (permission to write repository contents). With neither, it pushes the tag and tells
you how to finish in the browser. Only published releases count for updates; a bare tag is not
enough.

For a manifest-based source: build the archive (`python -m build --sdist`), compute its SHA-256,
upload it with `latest.json`.

---

## 4. Running

```sh
rind                 # start in the current folder
rind ~/Music         # start in a given folder
rind --help
rind --version
```

Quit with `q`, `Shift+Q` (without changing your shell's folder, see above) or `Ctrl+Q`.

- **tmux**: enable the mouse with `set -g mouse on`.
- **SSH**: works as usual. Mouse support depends on your local terminal, and "Copy
  path" uses OSC 52, which the terminal must allow.
- **Selecting text with the terminal's own selection**: hold `Shift` while dragging.
- **Right-click does nothing**: some terminals keep it for their own menu. Use `m`
  (or `Menu` / `Shift+F10`) for the same menu from the keyboard.

---

## 5. Using rind

### Layout

```
 File  Edit  View  Go  Tools  Help
 [←] [→] [↑] [↻]  [ /home/you/Documents              ] [Copy path]
 Cut Copy Paste Rename Delete New Undo Filter Find Preview Sort
 1 Documents ×   2 src ×   +        (the tab bar appears with two or more tabs)
 ~ › Documents › reports
┌ Places ──────┬──────────────────────────────┬─ Preview ───────────┐
│ Home         │ Name ▲        Size  Modified │ notes.md            │
│ Root  /      │ projects/                    │ Type     text/...   │
│ Documents    │ notes.md      3.4K  2026-... │ Size     3.4K ...   │
│ Trash        │ ...                          │ # Notes ...         │
└──────────────┴──────────────────────────────┴─────────────────────┘
 12 items · 2 marked · sort: name ▲ · z undoes: 3 items moved to trash
```

- **Menu bar**: click a title (or press `F10`, `F9` or `Alt+M`) to open a menu; `←`/`→` switch menus, and clicking another title while a menu is open switches to it. Each entry shows its shortcut, and entries that don't apply right now are greyed out.
- **Top bar**: back, forward, up, reload, the path box, and a button that copies the folder path.
- **Breadcrumb line**: click any folder name to jump to it (long paths are shortened from the left).
- **Toolbar**: the main actions as buttons (it scrolls sideways in a narrow terminal).
- **Sidebar**: click a place to go there; right-click one to open it in a new tab. *Frequent*
  lists your five most-visited folders not already listed (refreshed at the next start).
  *Devices* lists USB drives and other removable or network mounts, mounted or not.
- **Tab bar**: shown once you have two or more tabs. Click a tab to switch, `×` to close it, `+` for a new one.
- **File list**: folders first by default, coloured by type. Marked items show `●`.
- **Preview pane**: toggle with `p` or the Preview button.
- **Status line**: item count, marks, clipboard, filter, sort and what `z` would undo.

### Mouse

| Action | Result |
|---|---|
| Click a row | select it |
| Double-click | open a folder, or open a file with its configured tool |
| Right-click a row | context menu for that item (or all marked items) |
| Right-click empty space | folder menu (paste, new folder, find, sort, hidden files, ...) |
| `Ctrl`+click | add/remove the row from the marked set |
| `Shift`+click | mark everything from the cursor to the clicked row |
| Click a column header | sort by that column; click again to reverse |
| Click a sidebar place | go there |
| Click a menu bar title | open that menu |
| Click a folder in the breadcrumb line | go to that folder |
| Drag items onto a folder, sidebar place, tab, path segment or Trash | move them there (`Ctrl` held at release = copy) |
| Right-click a sidebar entry | open it, open in a new tab, mount or unmount |
| Mouse wheel | scroll |

Click outside a popup menu to close it.

### Keyboard (file list focused)

| Key | Action |
|---|---|
| `Enter` | open |
| `g` | **drag out** menu: system clipboard / drag window / file manager |
| `Alt+C` | copy the selected files to the **system clipboard** as a file list |
| `Alt+D` | open a **drag window** (ripdrag or dragon) |
| `Alt+F` | **show** the folder (and selection) **in your file manager** |
| `Backspace` or `Alt+Up` | go up one folder |
| `Alt+Left` | go back |
| `Ctrl+L` | edit the path |
| `F10` / `F9` / `Alt+M` | open the menu bar |
| `Alt+Right` | go forward |
| `Ctrl+T` / `Ctrl+W` | new tab / close tab |
| `F4` | split view on/off (two panes) |
| `Tab` | in split view: switch to the other pane |
| `Alt+1` … `Alt+9`, `Ctrl+PgUp`/`Ctrl+PgDn` | switch tabs |
| `/` | filter this folder as you type |
| `Ctrl+F` | search names in this folder and all subfolders |
| `p` | show/hide the preview pane |
| `j` | jump to a frequently used folder |
| `:` | run a shell command in this folder (`{}` = selected items) |
| `t` | open a shell (or terminal window) in this folder |
| `i` | calculate the size of the selected folders |
| `x` / `Ctrl+X` | cut |
| `c` / `Ctrl+C` | copy |
| `v` / `Ctrl+V` | paste into the current folder |
| `F2` or `r` | rename (with several items marked: pattern rename) |
| `Ctrl+R` | rename the selection in your `$EDITOR` |
| `Delete` or `d` | move to trash (permanent delete inside the Trash) |
| `z` / `Ctrl+Z` | undo |
| `Z` (Shift+Z) / `Ctrl+Y` | redo |
| `R` | restore from the Trash (Shift+R, inside the Trash) |
| `n` / `Ctrl+N` | new folder |
| `N` (Shift+N) | new empty file |
| `Ctrl+D` | duplicate (as `name (copy)`) |
| `y` / `Y` | copy / move the selection to the other tab |
| `o` | choose an application to open the file with |
| `P` (Shift+P) | properties and permissions |
| `U` (Shift+U) | unmount a device |
| `e` | extract the selected archive(s) |
| `C` (Shift+C) | compress the selection into an archive |
| `s` | sort menu |
| `m`, `Menu` or `Shift+F10` | context menu for the selected row |
| `.` or `Ctrl+H` | show/hide hidden files |
| `Space` | mark/unmark and move down |
| `Ctrl+A` | mark everything |
| `Esc` | clear marks and the filter |
| `u` | check for updates |
| `F5` | reload |
| `q` / `Shift+Q` | quit / quit without changing the shell's folder |

### The path bar

Click it (or press `Ctrl+L`) and edit. `Enter` goes there; a file path opens its folder
and highlights the file. `~` and `$VARIABLES` are expanded, and relative paths start from
the current folder. `Tab` completes: a single match completes fully, several complete to
their common prefix, and pressing `Tab` again shows a pick list. Dotfiles appear only
once you type a leading `.`. `Esc` restores the current path.

### Filter and search

- **Filter** (`/`): the list narrows as you type. Every word must appear in the name
  (case-insensitive); a single word of 3+ letters also matches fuzzily (`rpt` finds
  `report.md`). `Enter` returns to the list keeping the filter, `Esc` clears it. The
  filter resets when you change folder.
- **Search** (`Ctrl+F`): the same matching across the current folder and everything
  below it. Results are ranked by name match, then depth. `Enter` opens the first
  result, `↓` browses the list, and a click chooses one. Folders named `.git`,
  `node_modules` and `__pycache__` are skipped, hidden items only when hidden files
  are shown, and results are capped at 300 or five seconds.

### Preview

| Item | Shown |
|---|---|
| Text and code | first 64 KB / 300 lines, syntax-coloured |
| Folder | contents (first 200 entries) |
| Zip / tar archive | entry list |
| Image | picture drawn with half-blocks (needs Pillow and a colour terminal) |
| Binary | hex dump of the first bytes |

Every item also shows its type, size, modified time, mode, owner, symlink target and,
inside the Trash, where it was deleted from. Control characters in files are shown as
`·` so a file can't drive your terminal.

### Cut, copy and paste

- Cut and copy use the marked items, or the selected row if nothing is marked.
- If a name already exists, a dialog asks: **Replace**, **Skip** or **Keep both**
  (`r` / `s` / `k`), with *Apply to all remaining conflicts*. Folders with the same name
  are merged. Replaced items go to the trash, so undo brings them back.
- Big jobs show a progress bar and a Cancel button. Cancelling removes the partly
  copied file. A move across drives copies, then deletes the original.
- Pasting a folder into itself is refused; cutting and pasting in the same folder
  does nothing; copying in the same folder creates `name (copy)`.

### Trash, delete and undo

- `d` moves items to the trash without asking (configurable, see below).
  rind follows the freedesktop.org Trash specification: `~/.local/share/Trash` for your
  home drive and `.Trash-<uid>` at the top of other drives. If an item can't be
  trashed, rind offers to delete it permanently instead.
- **Sidebar → Trash** browses the home trash. There: `R` restores (to the original
  place, or `name (copy)` if that is taken), `d` deletes permanently, and the context
  menu has *Empty trash*.
- **Undo** (`z`, the Undo button, or the context menu) reverses, most recent first, up
  to 50 steps in this session: copy, move, trash, rename, new file/folder, permission and
  ownership changes, extraction and compression. Permanent deletes and emptying the trash
  can't be undone. **Redo** (`Z`/`Ctrl+Y`, or Edit → Redo) repeats what you just undid, in
  order; doing anything new clears the redo list. Extraction and compression can be undone
  but not redone (undoing one drops any older redo steps, so they never run out of order).

### Tabs

`Ctrl+T` opens a new tab on the current folder (the menu's *Open folder in new tab* and the
context menu's *Open in new tab* open the selected folder instead). Each tab keeps its own
folder, back/forward history, marked items, filter and cursor position. Switch with a click,
`Alt+1`…`Alt+9`, or `Ctrl+PgUp`/`Ctrl+PgDn` (the Go menu has *Next/Previous tab* for
terminals that don't send those keys). `Ctrl+W` or the `×` closes a tab; the last one can't be
closed. Sorting, hidden files, columns and the preview setting are shared by all tabs.

For two folders side by side, use the split view below. Without it, `y` (copy) or `Y` (move)
sends the selection to the other tab (a menu asks which tab when there are more than two),
or you can drag onto a tab label.

### Split view

`F4` (or *View → Split view*) shows two file lists side by side. Under the hood the panes are
two tabs, so each keeps its own folder, history, marks and filter. The pane you last clicked,
focused or switched to (`Tab` moves between them) is the **active** one: the path bar,
breadcrumb line, status line, menus and keyboard commands all act on it. With one tab open,
the right pane starts as a copy of the current folder in a new tab; with several, it shows the
next tab.

- **Copy / move between panes**: `y` copies and `Y` moves the selection into the other pane's
  folder (Edit menu: *Copy/Move to other pane*), or drag items across and drop them anywhere
  on the other pane, or onto one of its folders. It is the same job as a paste, with conflict
  dialog, progress and undo.
- The inactive pane updates itself when its folder changes (checked every two seconds),
  and sort order, columns and hidden files apply to both panes.
- Opening or closing tabs while split replaces or removes the pane's tab as you'd expect, and
  closing the other pane's tab leaves split view. `F4` again returns to a single list; the
  other pane's folder stays open as an ordinary tab.

### Drag and drop

Press on an item (or on any marked item to drag the whole marked set), move the mouse, and
release over:

- a **folder** in the list, **anywhere on the other pane** in split view, a **sidebar place**
  (including a mounted device), a **path segment** in the breadcrumb line, or a **tab label**:
  the items are **moved** there, or **copied** if `Ctrl` is held when you release;
- the **Trash** place: the items are moved to the trash.

While you drag, the status line names the target. `Esc` cancels. It uses the same conflict
dialog, progress bar and undo as paste. Items dragged out of an archive are always copied;
nothing can be dropped into an archive. Mouse drag and drop like this works inside rind. A terminal
program can't start a real drag to another window, or receive one directly, but see the next
two sections for what does work. It needs a terminal that reports
mouse movement while a button is held (most do; some multiplexer setups don't, so use `y`/`Y`
or cut and paste instead).

### Dropping files onto the terminal (drag in)

Most terminals (GNOME Terminal and Console, Konsole, kitty, WezTerm, Alacritty, foot, and others)
paste the path when you drop a file onto their window. rind reads that: **drag files from your
file manager onto the terminal running rind** and a small menu asks *Copy N items here*, *Move N
items here* or *Cancel*. It uses the same conflict dialog, progress bar and undo as paste, and
copies or moves into the folder you are looking at. Quoted paths, backslash-escaped paths and
`file://` URIs are all understood; a drop that isn't an existing path is ignored with a notice.
Dropping into the trash, or into an archive, is refused.

To skip the question: `[drop] action = "copy"` (or `"move"`) in `config.toml`. If the path bar or
filter box has the focus, the dropped text goes into that box instead, as you'd expect.

### Getting files out of rind (drag out)

Terminals can't start a drag to another application, so rind offers three workarounds for the
selected files (the marked files, or the one under the cursor). Press `g` for a menu of them, or
use the keys directly; they are also in the File menu and the right-click menu.

1. **System clipboard** (`Alt+C`): puts the files on the clipboard as a *file list*, so `Ctrl+V` in
   your file manager pastes (copies) them. It needs `wl-clipboard` (Wayland) or `xclip` (X11).
   The format follows your desktop: GNOME Files, Nemo, Caja, Thunar and PCManFM read
   `x-special/gnome-copied-files`; Dolphin and other Qt managers read `text/uri-list`. rind picks
   one from `XDG_CURRENT_DESKTOP`; override it with `[drag] clipboard_format = "gnome"` or
   `"uri-list"`. Only one format can be offered at a time.
2. **Drag window** (`Alt+D`): opens a small window with the files that you drag from, using
   `ripdrag` if installed, else `dragon-drop`, `xdragon` or `dragon`. It closes after the drop
   (`--and-exit`); several files are dragged together (`--all`). Choose another tool or flags with
   `[drag] helper` and `helper_args`.
3. **File manager** (`Alt+F`): opens the current folder in your file manager. When something is
   selected, rind first asks the file manager to highlight it (the `FileManager1` D-Bus service that
   GNOME Files and Dolphin provide), then falls back to just opening the folder. Use `[drag]
   file_manager = "nautilus {}"` for a specific program, or `reveal_selection = false` to never
   highlight.

Inside an archive, extract first (`e`). See section 6 for the `[drag]` settings.

### Properties and permissions

`P`, *Properties…* in the File menu or the context menu shows details for the selection:
name, location, type, size (folders are totalled in the background), link target, modified,
accessed and changed times, owner and group. For one item you can also change its
permissions with the nine read/write/execute boxes (the octal mode updates as you go) and
*Apply*. For a folder, *Apply to everything inside* applies the mode to its contents too:
subfolders get the same mode and files get it without execute bits, except files that were
already executable keep theirs. Special bits (setuid, setgid, sticky) are left as they were.
The **Ownership** row shows the owner and group. You can move a file to another **group you
belong to** with the group list; changing the **owner** needs root (the field is greyed out
otherwise, and an unknown user name is refused). *Apply to everything inside* covers ownership
too. Only the owner (or root) can change permissions or the group. Permissions and ownership
together are one undo/redo step, and a failure part-way is rolled back.

### Open with

`o` (or *Open with…* in the File and context menus) lists installed applications that can open
the file, built from the system's `.desktop` files: your defaults from `mimeapps.list` first,
then the rest by name, then *Default application*. You can also type a command (`{}` = the
file). Tick *Always use this for .ext files* to remember the choice (stored in
`~/.local/state/rind/openwith.json`, not in your config). Terminal applications run in rind's
terminal and return when they exit.

If a file doesn't open, rind now tells you why: it waits a moment, and if the program exits with
an error, the notification shows its exit code and message (for example `xdg-open exited with 3`
means the system has no program for that file type). If several ways exist (`xdg-open`,
`gio open`, `kde-open`, `exo-open`), the next one is tried before giving up. `rind --doctor` prints
what rind found: which openers, the clipboard tool, drag helpers, and whether it is running inside
a Flatpak/Toolbox/Distrobox container (where it reaches the host's tools through `flatpak-spawn
--host` or `distrobox-host-exec` when the container has none).

### Devices

The sidebar's *Devices* section lists removable drives (USB sticks, SD cards, optical discs),
other disks mounted under `/media`, `/run/media` or `/mnt`, and network mounts (NFS, SMB,
sshfs and similar). Click one to open it; an unmounted drive is mounted first, the way a
desktop does it. Right-click for *Unmount*, or use `U` or *File → Unmount device…*. The list
refreshes every few seconds. Mounting and unmounting block devices uses `udisksctl` (the
`udisks2` package, present on most desktops) or `gio`; network mounts are unmounted with
`gio`, `fusermount` or `umount`. System disks (`/`, `/home`, swap) aren't listed.

### Menu bar and help

The menu bar mirrors everything above: **File** (new folder/file, open, open with, tabs,
extract, compress, properties, size, unmount, quit), **Edit** (undo, cut/copy/paste, copy or
move to the other tab, duplicate, rename, trash, select),
**View** (preview, hidden files, sort, columns, filter, reload), **Go** (back, forward,
up, tabs, home, root, trash, jump, find, edit path), **Tools** (run command, shell, check for
updates) and **Help** (*Keyboard shortcuts* lists every key; *About* shows the version,
config path and update source). GNOME Terminal reserves `F10` for its own menu; use `F9`,
`Alt+M`, or a click instead, or turn that accelerator off in its preferences.

### Archives

- **Browse**: press `Enter` on a `.zip`, `.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`/`.tbz2` or
  `.tar.xz`/`.txz` file to open it like a folder. rind unpacks it into a private cache
  (`~/.cache/rind/archives`, deleted when you quit) with a progress bar, then shows
  `archive.zip!/inner/path` in the path bar. It is **read-only**: you can open, preview and
  **copy files out**, but editing commands are refused. `Backspace` at the top leaves the
  archive. If you've set an `[open]` entry for the extension, that tool is used instead.
- **Extract** (`e`, or *Extract here*): unpacks next to the archive. An archive with one
  top-level folder gives that folder; otherwise a folder named after the archive is made.
  An existing name becomes `name (copy)`. It's one undo step (the result goes to the trash).
- **Compress** (`C`): asks for a name and picks the format from its ending: `.zip`, `.tar`,
  `.tar.gz`, `.tar.xz`, `.tar.bz2` (and `.tgz`, `.txz`, `.tbz2`). Symlinks are stored as
  links. Undo moves the new archive to the trash.
- **Safety**: extraction never trusts the archive. Absolute paths and `..` are skipped, so
  are links that point outside the destination, device files and hard links, and an archive
  that would expand beyond a limit (default 10 GiB; `[behavior] max_extract_gb`) or has over
  500,000 entries is refused. Skipped entries are reported.

### New file, duplicate, history

`N` creates an empty file (undo removes it while it's still empty). `Ctrl+D` duplicates the
selection as `name (copy)` beside the original. `Alt+Left`/`Alt+Right` (or the ←/→
buttons) move back and forward through the folders you've visited.

### Jump to a frequent folder

`j` opens a list of folders you've visited, best first (visit count weighted by how
recently you used them). Type part of a path to narrow it; several words must all
appear. `Enter` goes to the first match, `↓` browses. Only folders you've visited in
rind are remembered (the history is `~/.local/state/rind/visits.json`); folders that no
longer exist are left out.

### Run a command, open a shell

- `:` asks for a shell command and runs it **in the current folder**. `{}` stands for
  the selected items (properly quoted), for example `tar czf backup.tgz {}` or
  `git add {}`. Without `{}` the command runs exactly as typed. rind steps aside so the
  command can use the whole terminal, shows its exit status, and returns when you press
  Enter. The command is run with `/bin/sh`, with your privileges, exactly as typed.
- `t` opens your `$SHELL` in the current folder; type `exit` to return. To open a new
  terminal window instead, set `[behavior] terminal` (see Configuration). In either
  case the environment variable `RIND_LEVEL` is set, so you can show a marker in your
  prompt.

### Git markers

Inside a git repository a **Git** column shows each item's status in git's own short
form: ` M` modified, `M ` staged, `A ` added, ` D` deleted, `R ` renamed, `??`
untracked, `UU` conflict. Folders show `M` or `??` if anything inside them changed. The
status bar shows the branch and the number of changed paths. rind only reads the
repository (`git status` with optional locks off and fsmonitor disabled) and gives up
quietly if git is missing or the repository takes more than three seconds. Turn the
column off with `[view] git = false` or from the context menu (View).

### Columns, symlinks and folder sizes

- The context menu's **View** submenu toggles columns: Size, Modified, Type, Permissions,
  Owner, Group and Git. Set the starting set with `[view] columns`.
- Symlinks show their target after the name (`link  → target`).
- Folder sizes aren't calculated automatically (it can be slow). Press `i` (or choose
  *Calculate size*) on the selected items: rind totals them in the background with a
  progress bar and Cancel, shows the grand total in a notification, and fills in the Size
  column for folders. A calculated size is dropped once the folder's own timestamp
  changes, and folders sort by calculated size (0 until calculated) in a size sort.

### Bulk rename

Mark several items (`Space`, or `Ctrl`/`Shift`+click), then:

- **`F2` (pattern)**: enter `find=>replace`. `find` is a regular expression; `\1`..`\9`
  refer to groups; `{n}` is a running counter starting at 1, and `{n:03}` pads it to
  three digits. Example: `IMG_(\d+)=>Holiday_{n:02}_\1`. Without a `=>` nothing is changed.
- **`Ctrl+R` (editor)**: opens the names in `$VISUAL` / `$EDITOR` (or nano/vim/vi), one
  per line. Edit them, save and quit; the number of lines must stay the same.

Either way rind lists the first eight changes and asks before renaming. It refuses
empty names, `/`, duplicates and names that already exist. Swaps and chains (`a→b`,
`b→a`) work, and if anything fails everything is rolled back. The whole rename is one
undo step.

### Automatic refresh

The list updates when files are created, removed, renamed or changed in the current
folder, using the kernel's inotify. If inotify isn't available for a folder (some
network drives), rind falls back to noticing folder-level changes every two seconds.

### Sorting

By **Name** (natural order: `file2` before `file10`), **Size**, **Modified** or
**Type** (extension): click a column header, use the Sort button, the context menu, or
`s`. The sort menu also has *Reverse order* and *Folders first*. Folders have no size,
so a size sort orders them by name.

---

## 6. Configuration

rind works without any configuration. To customise it, create
`~/.config/rind/config.toml` (or `$XDG_CONFIG_HOME/rind/config.toml`). If the file has an
error, rind starts anyway and shows a warning.

### Complete example

```toml
# Extra sidebar places (~ is expanded)
places = ["~/src", "/mnt/data"]

[view]
sort        = "name"     # name | size | modified | type
reverse     = false
dirs_first  = true
show_hidden = false
preview     = false      # start with the preview pane open
git         = true       # Git status column inside repositories
columns     = ["size", "modified", "type"]   # also: "perms", "owner", "group"

[behavior]
confirm_trash = false    # ask before moving items to the trash
max_extract_gb = 10      # refuse to extract archives that expand beyond this
# Open a new terminal window for the "t" key; {} = current folder. Omit to use a
# shell inside rind's own terminal instead.
terminal = "gnome-terminal --working-directory={}"

[update]
source         = "github:youruser/rind"
check          = true
interval_hours = 24

[open]
# Used for extensions not listed below. Omit to auto-detect xdg-open, then "gio open".
default = "xdg-open"

# Extensions: no dots, case-insensitive, comma-separate to share a tool.
"md,txt,log" = "gnome-text-editor"
"png,jpg,jpeg,webp" = "loupe"
pdf = "evince"

# Terminal programs need terminal = true: rind pauses, hands the screen over,
# and comes back when the program exits.
py   = { cmd = "nvim {}", terminal = true }

# {} marks where the file goes; without it the path is added as the last argument.
mkv = "mpv --fs {}"

[keys]       # section 6.1: change any shortcut; `rind --keys` lists the action names
cut_files    = "ctrl+x"            # replaces the defaults for that action ("x" and "ctrl+x")
copy_files   = ["c", "ctrl+c"]     # several keys
delete_items = ["delete"]
quit         = ""                  # empty turns a shortcut off
drag_out     = "g"

[drop]       # files dropped onto the terminal
action = "ask"                     # ask | copy | move

[drag]       # getting files out
clipboard_format = "auto"          # auto | gnome | uri-list
# helper       = "ripdrag"         # default: first of ripdrag, dragon-drop, xdragon, dragon
# helper_args  = "--and-exit --all"
# file_manager = "nautilus {}"     # default: the system's (xdg-open)
reveal_selection = true            # highlight the selection when opening the file manager
```

### 6.1 Changing keyboard shortcuts

Every action has an id (its name in `rind --keys`). Under `[keys]`, give an id a key, a list of keys,
or `""` to switch it off. The configured keys **replace** the defaults for that action, and a key
you assign is taken away from whatever used it before. Key names are Textual's: letters (`"x"`;
a capital such as `"Z"` or `"shift+z"` means Shift), `ctrl+x`, `alt+left`, `f2`, `enter`, `escape`,
`space`, `backspace`, `delete`, `pageup`, `pagedown`, `tab`; punctuation may be written as itself
(`"/"`, `":"`, `"."`). Menus, the Help screen and the context menus show your keys.

```sh
rind --keys          # every action with its keys; changed ones are marked *
```

Unknown action names are reported at start-up and ignored. The path bar, menus and dialogs keep
their fixed keys; `Ctrl+L` (`focus_path`) and `F10` (`menu_bar`) can be changed too. Some terminals
can't tell certain combinations apart (`Ctrl+I` is `Tab`, `Ctrl+M` is `Enter`, `Ctrl+Shift+letter`
is `Ctrl+letter`), so pick keys your terminal reports.

### Reference

| Key | Type | Default | Meaning |
|---|---|---|---|
| `places` | list of paths | `[]` | extra sidebar entries |
| `view.sort` | string | `"name"` | initial sort: `name`, `size`, `modified`, `type` |
| `view.reverse` | bool | `false` | initial sort direction |
| `view.dirs_first` | bool | `true` | list folders before files |
| `view.show_hidden` | bool | `false` | show dotfiles at start-up |
| `view.preview` | bool | `false` | open the preview pane at start-up |
| `view.git` | bool | `true` | Git status column in repositories |
| `view.columns` | list | size, modified, type | columns after the name: `size`, `modified`, `type`, `perms`, `owner`, `group` |
| `behavior.confirm_trash` | bool | `false` | ask before trashing |
| `behavior.max_extract_gb` | number | `10` | largest size an archive may expand to |
| `behavior.terminal` | string | none | command that opens a terminal window for `t`; `{}` is replaced by the folder |
| `update.source` | string | none | where to look for new versions (section 3) |
| `update.check` | bool | `true` | automatic background check, when a source is set |
| `update.interval_hours` | number | `24` | minimum time between automatic checks |
| `open.default` | string or table | auto | opener for extensions without an entry |
| `open."<ext>[,<ext>...]"` | string or table | none | opener for those extensions |

An opener is a command string (`"loupe"`) or `{ cmd = "...", terminal = true|false }`.
Commands are split like a shell would (quote arguments with spaces), run directly (no
shell, so no pipes or globbing), and launched detached so closing rind doesn't close
the app.

### How a file is opened

1. The extension's entry in `[open]`, if any.
2. Otherwise an application you chose with *Open with… → Always use this*.
3. Otherwise `open.default`, if set.
4. Otherwise the system default: `xdg-open`, then `gio open` (then `kde-open`, `exo-open`).

Archives open inside rind unless step 1 names a tool. Files without an extension use steps 3
and 4. Folders always open inside rind.

### Files and variables

| Item | Purpose |
|---|---|
| `~/.local/state/rind/visits.json` | visit counts and last-visit times for *Frequent* and Jump (delete to reset) |
| `~/.local/state/rind/update.json` | time of the last update check |
| `~/.local/state/rind/openwith.json` | applications remembered with *Open with… → Always use this* (delete to forget) |
| `~/.cache/rind/archives/` | temporary unpacked archives while browsing (removed on exit) |
| `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME` | relocate the config, state and trash |

rind never modifies your config.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Clicks do nothing | Mouse reporting is off in your terminal or tmux (`set -g mouse on`). |
| Right-click opens the terminal's menu | Use `m`, or enable sending right-clicks to applications in the terminal. |
| "No opener found" | Install `xdg-utils`, or set `open.default`. Run `rind --doctor` to see what rind found. |
| "Could not open …: xdg-open exited with N" | The message after it is the program's own error. `3` usually means no tool or no handler for that type: install `xdg-utils`/`gvfs`, or set an application with *Open with…* or `[open]`. |
| Dropping files onto the terminal does nothing | The terminal must paste the path when you drop (most do). If the path bar has focus the text goes there; click the file list first. |
| Alt+C says it needs wl-clipboard or xclip | Install `wl-clipboard` (Wayland) or `xclip` (X11). |
| Ctrl+V in my file manager doesn't paste the files | Try the other format: `[drag] clipboard_format = "gnome"` or `"uri-list"`. |
| Alt+D says no drag helper | Install `ripdrag` (e.g. `cargo install ripdrag`) or `dragon`, or set `[drag] helper`. |
| A `[keys]` change has no effect | Run `rind --keys`: a typo in the action name is reported at start-up, and the key must be one your terminal sends. |
| A file doesn't open in my tool | Check the command runs from a shell; use `terminal = true` for terminal programs; on immutable distros run rind on the host. |
| "Config error: ..." | Fix the TOML shown. Keys with commas or dots must be quoted, as `"md,txt"`. |
| No image preview | `pip install pillow` into rind's environment, or reinstall with `[images]`; use a true-colour terminal. |
| List doesn't refresh on a network drive | inotify doesn't see remote changes; press `F5`. |
| "Couldn't move ... to the trash" | The drive has no writable trash location; rind offers permanent delete instead. |
| `u` says no update source | Set `[update] source` (section 3). |
| Dragging does nothing | The terminal or multiplexer isn't reporting motion while a button is held. Use `y`/`Y`, or cut and paste. |
| A drive won't mount | Install `udisks2` (provides `udisksctl`), or `gio`. The error from the tool is shown. |
| *Open with* lists no applications | There are no `.desktop` files for that file type under `XDG_DATA_DIRS`; type a command instead. |
| Can't change permissions | Only the owner (or root) can; the boxes are greyed out otherwise. |
| An archive won't open | The message says why: unreadable file, too many entries, or over the size limit (raise `max_extract_gb` if you trust it). |
| `F10` opens the terminal's own menu | Use `F9` or `Alt+M`, click the menu bar, or disable the terminal's menu accelerator. |
| No Git column in a repository | Install `git`; very large repositories can exceed the three-second limit. Check `[view] git` isn't `false`. |
| `:` or `t` shows an error | rind needs a real terminal to hand over; inside some embedded terminals this isn't possible. Set `[behavior] terminal` to use a separate window for `t`. |
| Ctrl+R says to set `$EDITOR` | `export EDITOR=nano` (or your editor) in your shell startup file. |
| Update fails with "installer failed" | The message includes pip/pipx output; usually a network or build-tool issue. Retry, or reinstall by hand. |
| "Copy path" doesn't reach the clipboard | Install `wl-clipboard` or `xclip`, or use a terminal with OSC 52. |
| Odd characters instead of `▲ ● ✓ ▀` | Use a terminal font with those symbols. |

---

## 8. Known limitations

- No dragging files to or from other applications (terminals can't), and no more than two panes.
- Split view is most comfortable on a wide terminal (it was tested at 170 columns). On narrower ones, hide columns from the View menu and keep the preview pane off to give the panes room.
- Mounting and unmounting needs `udisks2` (or `gio`); this was tested with stand-ins for those tools, not real hardware.
- Archives are read-only; rind can't add to or edit one in place (compress creates a new archive). Browsing unpacks the whole archive first, so very large ones take a moment.
- The Trash view shows the home trash only; items trashed on other drives can still be
  undone but aren't listed there.
- Automatic updates need a source you provide; rind has no built-in default.
- Tab completion applies to the path bar only.

---

## 9. Development

```sh
git clone https://github.com/elvishcraftsman/rind && cd rind
python3 -m venv .venv && .venv/bin/pip install -e '.[images]'
.venv/bin/python tests/test_fileops.py      # engine: trash, copy/move, conflicts, cancel
.venv/bin/python tests/test_app.py          # end-to-end UI test with a headless Textual pilot
.venv/bin/python tests/test_progress.py     # progress dialog and cancel
.venv/bin/python tests/test_new_engines.py  # bulk rename, jump ranking, git status
.venv/bin/python tests/test_app2.py         # columns, folder sizes, rename, run command, jump, git
.venv/bin/python tests/test_archives.py     # archive engine, including hostile archives
.venv/bin/python tests/test_app3.py         # menu bar, archives, breadcrumbs, history, new file
.venv/bin/python tests/test_engines3.py     # open-with, devices, chmod
.venv/bin/python tests/test_app4.py         # tabs, drag and drop, devices, properties, open with
.venv/bin/python tests/test_app5.py         # redo, ownership
.venv/bin/python tests/test_v08.py          # drop-in paste, drag-out helpers, [keys], opener errors, updater
.venv/bin/python tests/test_app6.py         # split view
```

Layout: `rind/app.py` (UI), `screens.py` (dialogs, menus, search, jump), `fileops.py`
(trash, copy/move and rename engine), `preview.py`, `watch.py` (inotify), `gitstatus.py`,
`places.py` (visit history), `archives.py` (zip/tar engine), `devices.py`, `openwith.py`,
`updater.py`, `search.py`, `config.py`, `cli.py`. Developed and tested on Textual 8.2.

import os, subprocess, sys, tempfile, time
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k in ("HOME","XDG_DATA_HOME","XDG_STATE_HOME","XDG_CONFIG_HOME"): os.environ[k] = str(home)
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
from rind import fileops as F, places, gitstatus, config

def mk(p, t="x"): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(t); return p

# ---- bulk rename: validation, swap, chain, rollback, patterns
d = home / "r"; a, b, c = mk(d / "a"), mk(d / "b"), mk(d / "c")
ch = F.plan_renames([(a, "b"), (b, "a")]); F.apply_renames(ch)
assert (d / "a").read_text() == "x" and not list(d.glob(".rind-*"))
mk(d / "a", "A"); mk(d / "b", "B"); mk(d / "c", "C")
F.apply_renames(F.plan_renames([(d / "a", "b"), (d / "b", "c"), (d / "c", "d")]))   # chain
assert [(n, (d / n).read_text()) for n in ("b", "c", "d")] == [("b", "A"), ("c", "B"), ("d", "C")]
for bad in ([(d / "b", "c")], [(d / "b", "x"), (d / "c", "x")], [(d / "b", "a/b")], [(d / "b", "")]):
    try: F.plan_renames(bad); raise SystemExit("should fail: %r" % (bad,))
    except ValueError: pass
real = os.rename; n = [0]
def flaky(s, t, *a, **k):
    n[0] += 1
    if n[0] == 3: raise OSError(13, "denied")
    return real(s, t, *a, **k)
os.rename = flaky
try: F.apply_renames(F.plan_renames([(d / "b", "c"), (d / "c", "b")])); raise SystemExit("expected failure")
except OSError: pass
os.rename = real
assert (d / "b").read_text() == "A" and (d / "c").read_text() == "B" and not list(d.glob(".rind-*")), "rollback failed"
names = F.pattern_names([Path("IMG_001.jpg"), Path("IMG_002.jpg")], r"IMG_(\d+)=>Photo_{n:03}_\1")
assert names == ["Photo_001_001.jpg", "Photo_002_002.jpg"], names
assert F.pattern_names([Path("a b.txt")], " =>_") == ["a_b.txt"]
for bad in ("nopattern", "(=>x", r"a=>\5"):
    try: F.pattern_names([Path("a")], bad); raise SystemExit("should fail " + bad)
    except ValueError: pass
print("rename engine ok")

# ---- frecency / jump
now = time.time()
v = {str(home / "proj"): {"n": 10, "t": now}, str(home / "old"): {"n": 10, "t": now - 30 * 86400},
     str(home / "ghost"): {"n": 99, "t": now}, "/tmp": {"n": 1, "t": 0.0}}
(home / "proj").mkdir(); (home / "old").mkdir()
r = places.rank_jump(v, "")
assert [p.name for p in r][:2] == ["proj", "old"] and "ghost" not in [p.name for p in r], r
assert [p.name for p in places.rank_jump(v, "pro")] == ["proj"]
config.write_json(config.VISITS_FILE, {"/tmp": 3})            # legacy format
assert places.load_visits()["/tmp"] == {"n": 3, "t": 0.0}
assert places.tilde(Path.home() / "x") == "~/x"
print("jump ok")

# ---- git status
if subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
    g = home / "repo"; g.mkdir()
    def git(*a): subprocess.run(["git", "-C", str(g), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main"); mk(g / "tracked.txt", "1"); mk(g / "sub" / "inner.txt", "1"); mk(g / "gone.txt")
    git("add", "."); git("commit", "-qm", "init")
    mk(g / "tracked.txt", "2"); mk(g / "new.txt"); mk(g / "sub" / "inner.txt", "2"); (g / "gone.txt").unlink()
    git("mv", "sub/inner.txt", "sub/moved.txt"); mk(g / "untracked_dir" / "f.txt")
    info = gitstatus.git_status(g)
    assert info and info.branch == "main", info
    rd = os.path.realpath(g)
    codes = {n: info.code_for(rd, n, isd) for n, isd in [("tracked.txt", 0), ("new.txt", 0), ("gone.txt", 0), ("sub", 1), ("untracked_dir", 1), ("nothing", 0)]}
    print(codes)
    assert codes["tracked.txt"].strip() == "M" and codes["new.txt"] == "??" and codes["gone.txt"].strip() == "D"
    assert codes["sub"] == "R " or codes["sub"] == "M" and codes["untracked_dir"] == "??" and codes["nothing"] == ""
    assert gitstatus.git_status(home) is None
    print("git ok")
else:
    print("git not installed: skipped")

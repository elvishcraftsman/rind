import errno, os, sys, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(tmp / "data")
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
from rind import fileops as F
from rind.search import match_name, find_files

def mk(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text); return p

# --- trash + restore
a = mk(tmp / "w" / "a.txt", "A")
rec = F.trash_item(a)
assert not a.exists() and rec.stored.exists() and rec.info.exists()
assert "Path=" + str(a) in rec.info.read_text()
assert F.trash_origin(rec.stored) == a
mk(a, "A2"); rec2 = F.trash_item(a)           # same name again -> a.txt.2
assert rec2.name == "a.txt.2"
back = F.restore_record(rec)
assert back == a and a.read_text() == "A" and not rec.info.exists()
back2 = F.restore_record(rec2)                 # orig occupied -> unique name
assert back2.name == "a (copy).txt", back2
d = tmp / "w" / "dir"; mk(d / "f.txt"); r = F.trash_item(d); assert not d.exists()
F.restore_record(r); assert (d / "f.txt").exists()
assert F.empty_trash() == 0
F.trash_item(a); assert F.empty_trash() == 1
print("trash ok")

# --- copy with conflicts
src = tmp / "src"; mk(src / "one.txt", "1"); mk(src / "sub" / "two.txt", "22"); (src / "ln").symlink_to("one.txt")
dst = tmp / "dst"; dst.mkdir()
job = F.Job(); res = F.run_copy([(src, dst / "src")], job, F.Resolver())
assert not res.errors and (dst / "src" / "sub" / "two.txt").read_text() == "22"
assert os.readlink(dst / "src" / "ln") == "one.txt" and job.total == 3 and job.done == 3, (job.total, job.done)
# merge again: files conflict -> skip policy (ask None)
mk(src / "one.txt", "NEW")
res = F.run_copy([(src, dst / "src")], F.Job(), F.Resolver())
assert (dst / "src" / "one.txt").read_text() == "1" and len(res.skipped) >= 1
# replace policy, replaced file goes to trash
res = F.run_copy([(src, dst / "src")], F.Job(), F.Resolver(sticky="replace"))
assert (dst / "src" / "one.txt").read_text() == "NEW" and len(res.replaced) >= 1 and not res.irreversible
F.restore_record(res.replaced[0]) if False else None
# both policy
res = F.run_copy([(src / "one.txt", dst / "src" / "one.txt")], F.Job(), F.Resolver(sticky="both"))
assert (dst / "src" / "one (copy).txt").exists()
# ask callback with apply-all and cancel
calls = []
def ask(s, d): calls.append(d.name); return ("cancel", False)
res = F.run_copy([(src / "one.txt", dst / "src" / "one.txt")], F.Job(), F.Resolver(ask))
assert res.cancelled and calls == ["one.txt"]
print("copy ok")

# --- cancel mid-copy removes partial file
big = tmp / "big.bin"; big.write_bytes(os.urandom(5 * 1024 * 1024))
job = F.Job()
orig_advance = job.advance
def adv(n):
    orig_advance(n)
    if job.done >= 2 * 1024 * 1024: job.cancel()
job.advance = adv
res = F.run_copy([(big, dst / "big.bin")], job, F.Resolver())
assert res.cancelled and not (dst / "big.bin").exists() and not list(dst.glob(".rind-*"))
print("cancel ok")

# --- move: same fs, conflicts, merge, cross-device fallback
m1 = tmp / "m1"; mk(m1 / "a.txt", "a"); mk(m1 / "d" / "x.txt", "x")
m2 = tmp / "m2"; mk(m2 / "d" / "y.txt", "y"); mk(m2 / "a.txt", "old")
res = F.run_move([(m1 / "a.txt", m2 / "a.txt"), (m1 / "d", m2 / "d")], F.Job(), F.Resolver(sticky="replace"))
assert (m2 / "a.txt").read_text() == "a" and (m2 / "d" / "x.txt").exists() and (m2 / "d" / "y.txt").exists()
assert not (m1 / "d").exists() and not res.errors and len(res.replaced) == 1
real_rename = os.rename
def fake_rename(s, d, *a, **k):
    if str(s).startswith(str(tmp / "xdev")) and not str(d).endswith(".trashinfo"): raise OSError(errno.EXDEV, "cross-device")
    return real_rename(s, d, *a, **k)
os.rename = fake_rename
mk(tmp / "xdev" / "t" / "f.txt", "ff"); (tmp / "out").mkdir()
job = F.Job()
res = F.run_move([(tmp / "xdev" / "t", tmp / "out" / "t")], job, F.Resolver())
os.rename = real_rename
assert (tmp / "out" / "t" / "f.txt").read_text() == "ff" and not (tmp / "xdev" / "t").exists() and not res.errors
assert job.done == job.total == 2
print("move ok")

# --- delete
res = F.run_delete([tmp / "out" / "t"], F.Job()); assert not (tmp / "out" / "t").exists() and len(res.done) == 1

# --- search helpers
assert match_name("rep md", "report-final.md") and not match_name("zzz", "report.md")
assert match_name("rpt", "report.md") and not match_name("rp", "xxrxx")  # fuzzy only for 3+ chars
root = tmp / "sr"; mk(root / "docs" / "Report.md"); mk(root / ".git" / "report.txt"); mk(root / "a" / "b" / "reporting.txt")
res, trunc = find_files(root, "report", False, lambda: False)
names = [str(p.relative_to(root)) for p in res]
assert names == ["docs/Report.md", "a/b/reporting.txt"], names
print("search ok")

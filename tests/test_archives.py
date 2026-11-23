import io, os, stat, sys, tarfile, tempfile, zipfile
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k in ("HOME","XDG_DATA_HOME","XDG_STATE_HOME","XDG_CONFIG_HOME","XDG_CACHE_HOME"): os.environ[k] = str(home)
sys.path.insert(0, "/home/claude/rind")
from rind import archives as A
from rind.fileops import Job, Cancelled

def tree(root):
    out = {}
    for dp, dns, fns in os.walk(root):
        for n in dns: out[str((Path(dp)/n).relative_to(root)) + "/"] = None
        for n in fns:
            p = Path(dp)/n
            out[str(p.relative_to(root))] = os.readlink(p) if p.is_symlink() else p.read_bytes()
    return out

assert A.archive_kind("x.TAR.GZ") == "tar" and A.archive_kind("a.zip") == "zip" and A.archive_kind("a.docx") is None
assert A.archive_stem("proj-1.2.tar.xz") == "proj-1.2" and A.archive_stem("a.zip") == "a"

# ---- roundtrip: every format, symlink, exec bit, empty dir, unicode name
src = home / "src" / "proj"; (src / "sub").mkdir(parents=True); (src / "empty").mkdir()
(src / "a.txt").write_text("alpha"); (src / "sub" / "é ünï.sh").write_bytes(b"#!/bin/sh\n" * 1000)
os.chmod(src / "sub" / "é ünï.sh", 0o755); (src / "ln").symlink_to("a.txt")
for ext in (".zip", ".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz"):
    job = Job(); arc = A.compress([src], home / f"out{ext}", job)
    assert arc.exists() and not list(home.glob(".rind-pack*")), ext
    assert job.done == job.total > 0, (ext, job.done, job.total)
    dest = home / ("x" + ext.replace(".", "_")); job = Job()
    res = A.extract_archive(arc, dest, job)
    got = tree(dest / "proj")
    assert got == tree(src), (ext, got.keys(), tree(src).keys())
    assert os.access(dest / "proj" / "sub" / "é ünï.sh", os.X_OK) and not res.skipped, (ext, res.skipped)
print("roundtrip ok")

# ---- hostile archives
def zip_with(entries, links=()):
    p = home / "evil.zip"
    with zipfile.ZipFile(p, "w") as z:
        for n, data in entries: z.writestr(n, data)
        for n, target in links:
            zi = zipfile.ZipInfo(n); zi.create_system = 3; zi.external_attr = (stat.S_IFLNK | 0o777) << 16; z.writestr(zi, target)
    return p
d = home / "ev1"
res = A.extract_archive(zip_with([("../escape.txt", "x"), ("/abs.txt", "x"), ("a/../../b.txt", "x"), ("ok.txt", "fine")]), d, Job())
assert (d / "ok.txt").exists() and not (home / "escape.txt").exists() and not Path("/abs.txt").exists() and len(res.skipped) == 3, res
d = home / "ev2"
res = A.extract_archive(zip_with([("f.txt", "x")], links=[("abs", "/etc"), ("up", "../../.."), ("ok", "f.txt")]), d, Job())
assert os.readlink(d / "ok") == "f.txt" and not os.path.lexists(d / "abs") and not os.path.lexists(d / "up"), res.skipped
# a symlink chain that escapes via a link to a directory, then a file written through it
d = home / "ev3"
res = A.extract_archive(zip_with([("x/y/z/f", "1")], links=[("x/y/z/s", "../../.."), ("m", "x/y/z/s/../../../..")]), d, Job())
assert not os.path.lexists(d / "m"), "escaping chain accepted"
d = home / "ev4"
A.extract_archive(zip_with([], links=[("lnk", "sub")]) , d, Job())          # link to a not-yet-existing dir is fine
res = A.extract_archive(zip_with([("lnk/payload", "x")], links=[("lnk", "..")]), home / "ev5", Job())
assert not (home / "payload").exists()
# tar: absolute names, device nodes, hardlinks, evil symlink
tp = home / "evil.tar"
with tarfile.open(tp, "w") as t:
    for n, kind, extra in [("/abs", tarfile.REGTYPE, None), ("../up", tarfile.REGTYPE, None), ("dev", tarfile.CHRTYPE, None), ("hl", tarfile.LNKTYPE, "good.txt"), ("sl", tarfile.SYMTYPE, "/etc/passwd"), ("good.txt", tarfile.REGTYPE, None)]:
        ti = tarfile.TarInfo(n); ti.type = kind
        if kind == tarfile.REGTYPE: data = b"data"; ti.size = 4; t.addfile(ti, io.BytesIO(data))
        else:
            if extra: ti.linkname = extra
            t.addfile(ti)
d = home / "ev6"; res = A.extract_archive(tp, d, Job())
assert sorted(os.listdir(d)) == ["good.txt"] and len(res.skipped) == 5, (os.listdir(d), res.skipped)
print("hostile archives ok")

# ---- bomb guard, bad archive, cancel
big = home / "bomb.zip"
with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z: z.writestr("zeros", b"\0" * (3 << 20))
try: A.extract_archive(big, home / "ev7", Job(), limit=1 << 20); raise SystemExit("limit not enforced")
except A.ArchiveError as e: print("limit ->", e)
(home / "broken.zip").write_bytes(b"not a zip at all")
try: A.extract_archive(home / "broken.zip", home / "ev8", Job()); raise SystemExit("bad archive accepted")
except A.ArchiveError as e: print("broken ->", e)
job = Job(); orig = job.advance
def adv(n):
    orig(n)
    if job.done > (1 << 20): job.cancel()
job.advance = adv
try: A.extract_archive(big, home / "ev9", job); raise SystemExit("not cancelled")
except Cancelled: pass
print("limits/cancel ok")

# ---- extract_here: single top-level entry vs several; cache
parent = home / "par"; parent.mkdir()
final, sk = A.extract_here(home / "out.zip", parent, Job())
assert final == parent / "proj" and (final / "a.txt").exists() and not list(parent.glob(".rind-*")), final
final2, _ = A.extract_here(home / "out.zip", parent, Job()); assert final2.name == "proj (copy)", final2
multi = home / "multi.zip"
with zipfile.ZipFile(multi, "w") as z: z.writestr("one.txt", "1"); z.writestr("two.txt", "2")
final3, _ = A.extract_here(multi, parent, Job())
assert final3 == parent / "multi" and sorted(os.listdir(final3)) == ["one.txt", "two.txt"] and stat.S_IMODE(os.stat(final3).st_mode) & 0o555 == 0o555
root = A.open_cache(home / "out.zip", Job()); assert (root / "proj" / "a.txt").exists()
assert A.open_cache(home / "out.zip", Job()) == root          # reused
A.clean_cache(); assert not root.exists()
print("extract_here + cache ok")

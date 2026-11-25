import asyncio, os, sys, tempfile, time
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k in ("HOME","XDG_DATA_HOME","XDG_STATE_HOME","XDG_CONFIG_HOME"): os.environ[k]=str(home)
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
from rind.app import RindApp
from rind.screens import ProgressScreen
async def main():
    app = RindApp(str(home))
    out = []
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        def slow(job):
            job.total = 100
            for i in range(100):
                job.check(); job.current = f"file{i}"; job.advance(1); time.sleep(0.02)
            return "finished"
        app.run_job("Testing", slow, lambda r: out.append(r))
        await pilot.pause(0.9)
        print("progress shown:", isinstance(app.screen, ProgressScreen), "done so far:", app.job_state["job"].done)
        await pilot.pause(2.0)
        print("result:", out, "screen back:", type(app.screen).__name__, "job_state:", app.job_state)
        # cancel path
        def slow2(job):
            for i in range(1000):
                job.check(); time.sleep(0.01)
            return "should not finish"
        res = []
        app.run_job("Cancelling", lambda job: slow2(job), lambda r: res.append(r))
        await pilot.pause(0.7)
        await pilot.click("#cancel"); await pilot.pause(0.8)
        print("cancel result:", [type(r).__name__ for r in res], type(app.screen).__name__)
asyncio.run(main())

"""Run agent.py with all network access blocked except openrouter.ai (simulates the assessment sandbox)."""
import runpy, socket, sys
from pathlib import Path
_orig = socket.getaddrinfo
def guarded(host, *a, **k):
    if not str(host).endswith("openrouter.ai"):
        raise socket.gaierror("blocked by run_offline.py: " + str(host))
    return _orig(host, *a, **k)
socket.getaddrinfo = guarded
root = Path(__file__).resolve().parent.parent
sys.argv = [str(root / "agent.py")] + sys.argv[1:]
runpy.run_path(str(root / "agent.py"), run_name="__main__")

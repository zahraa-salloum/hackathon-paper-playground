"""Pipeline self-test with a fixture reply (no network, no API key). Not used by agent.py."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import agent

reply = (Path(__file__).parent / sys.argv[1]).read_text(encoding="utf-8")
blocks, problems = agent.parse_reply(reply)
print("parse problems:", problems, "blocks:", sorted(blocks))
spec = dict(blocks["spec"]); spec["compute"] = blocks["compute"]; spec["draw"] = blocks["draw"]
fails, summary = agent.check_all(spec)
print("failures:", fails)
print("summary:", summary)
case = {"source_url": "https://example.org/p", "audience": "undergrad", "focus": "x"}
page = agent.build_page(spec, case, "excerpt")
print("static issues:", agent.static_page_checks(page, "KEY"))
out = ROOT / "tests" / "tmp_out"; out.mkdir(exist_ok=True)
(out / "index.html").write_text(page, encoding="utf-8")
print("wrote", out / "index.html", len(page))

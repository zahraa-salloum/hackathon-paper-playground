#!/bin/bash
# Run every practice case; print one summary line per case. Usage: bash tests/run_all.sh
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
python tests/selftest.py fixture_reply_attention.txt | sed -n 2p | sed 's/^/selftest /'
for f in tests/cases/*.json; do
  c=$(basename "$f" .json); rm -rf "tests/o_$c"
  python agent.py --input "$f" --output "tests/o_$c" --model "${MODEL:-deepseek/deepseek-v4.1-flash}" >/dev/null 2>&1; rc=$?
  PYTHONIOENCODING=utf-8 python - "$c" "$rc" <<'PY'
import json,sys
c,rc=sys.argv[1],sys.argv[2]; fin={}; end={}; fails=[]
for l in open(f'tests/o_{c}/trace.jsonl',encoding='utf-8'):
    r=json.loads(l)
    if r['stage']=='finish': fin=r
    if r['stage']=='end': end=r
    if r['stage']=='check' and r['action']=='validate_spec_and_execute_js' and r['result']=='fail': fails=r['failures']
print(f"{c:12s} exit={rc} checks_passed={fin.get('checks_passed')} calls={end.get('api_calls')} tokens={end.get('prompt_tokens',0)}+{end.get('completion_tokens',0)} time={end.get('elapsed_s')}s src={fin.get('source_mode')}")
for f in (fin.get('remaining_failures') or [])[:2]: print('     remaining:',f[:170])
PY
done

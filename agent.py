#!/usr/bin/env python3
"""Paper to Playground: turn a paper excerpt + learning brief into a self-contained interactive HTML explanation.

Usage: python agent.py --input case.json --output out --model MODEL_ID
Reads OPENROUTER_API_KEY from the environment. All model calls go through OpenRouter.
"""
import argparse
import html
import io
import json
import os
import random
import re
import sys
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

import logging
import requests

logging.getLogger("pypdf").setLevel(logging.ERROR)

START = time.time()
HERE = Path(__file__).resolve().parent
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Hard limits imposed by the assessment (kept slightly conservative).
MAX_CALLS = 8                 # limit is 10 including retries
MAX_COMPLETION_TOKENS = 28500  # limit is 30,000 total completion tokens
DEADLINE_S = 480              # limit is 10 minutes
FIRST_MAX_TOKENS = 14000
REVISE_MAX_TOKENS = 9000
MAX_REVISIONS = 3


# --------------------------------------------------------------------------- trace
class Trace:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def log(self, stage, action, result, **extra):
        rec = {"t": round(time.time() - START, 2), "stage": stage, "action": action, "result": result}
        rec.update(extra)
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


# --------------------------------------------------------------------------- source acquisition
class _Text(HTMLParser):
    """HTML -> text; math elements are replaced by their LaTeX alttext when present."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "section", "table", "figure", "ul", "ol", "dd", "dt"}
    SKIP = {"script", "style", "nav", "head", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.math = [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "math":
            alt = dict(attrs).get("alttext")
            if alt and not self.math:
                self.out.append(" " + alt + " ")
            self.math += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag.startswith("h"):
                self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "math":
            self.math = max(0, self.math - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip and not self.math:
            self.out.append(data)


def html_to_text(src):
    p = _Text()
    p.feed(src)
    t = "".join(p.out)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def fetch_source(url):
    m = re.match(r"https?://arxiv\.org/abs/([^\s?#]+)", url)
    if m:
        url = "https://arxiv.org/pdf/" + m.group(1)
    r = requests.get(url, timeout=(3, 10), headers={"User-Agent": "Mozilla/5.0 (paper-to-playground)"})
    r.raise_for_status()
    if "pdf" in r.headers.get("content-type", "").lower() or url.lower().split("?")[0].endswith(".pdf"):
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(r.content))
        return "\n\n".join((pg.extract_text() or "") for pg in rd.pages)
    return html_to_text(r.text)


STOP = set("this that with from into what when which their there about have been each show shows using should learner "
           "explain explanation page change changes number small values value paper section concept".split())


def keywords(s):
    return [w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP]


def extract_relevant(text, focus, limit=9000):
    """Pick the excerpt-sized window of `text` that best matches the focus/brief."""
    if len(text) <= limit:
        return text
    kws = keywords(focus)

    def score(chunk):
        low = chunk.lower()
        return sum(min(low.count(k), 3) for k in set(kws))

    best, best_s = None, -1
    # explicit section number in the brief, e.g. "Section 3.2.1" -> look for that heading
    for num in set(re.findall(r"(?:section|sec\.?|§)\s*(\d+(?:\.\d+)*)", focus, flags=re.I)):
        for m in re.finditer(r"(?m)^\s*" + re.escape(num) + r"\.?\s+[A-Z]", text):
            s = score(text[m.start():m.start() + 2500]) + 8
            if s > best_s:
                best, best_s = max(0, m.start() - 80), s
    # fallback: best-scoring sliding window
    step = 1000
    for st in range(0, max(1, len(text) - 1500), step):
        s = score(text[st:st + 2500])
        if s > best_s:
            best, best_s = max(0, st - 300), s
    return text[best:best + limit]


STD_FIELDS = {"source_url", "focus", "audience"}


def acquire_source(case, trace):
    extra = {k: v for k, v in case.items() if k not in STD_FIELDS and isinstance(v, str) and v.strip()}
    excerpt_keys = [k for k, v in extra.items() if len(v) > 300]
    if excerpt_keys:
        txt = "\n\n".join(extra[k] for k in excerpt_keys)
        trace.log("source", "use_case_excerpt", "ok", fields=excerpt_keys, chars=len(txt))
        return extract_relevant(txt, case["focus"]), "excerpt", extra
    try:
        raw = fetch_source(case["source_url"])
        txt = extract_relevant(raw, case["focus"])
        trace.log("source", "fetch_url", "ok", chars_total=len(raw), chars_used=len(txt))
        return txt, "fetched", extra
    except Exception as e:  # network is restricted during assessment
        trace.log("source", "fetch_url", "failed", error=type(e).__name__ + ": " + str(e)[:160])
        return "", "none", extra


# --------------------------------------------------------------------------- prompts
SYSTEM = r"""You write the mechanism-specific parts of an interactive explanation page for an engineering undergraduate. A fixed template renders layout, controls, readouts, explorations and checks. Reply with EXACTLY three tagged blocks and no other text or markdown fences:
<spec>{JSON}</spec>
<compute>JS function body</compute>
<draw>JS function body</draw>

SPEC JSON keys (all required):
title: short page title.
paper: {title, authors, year, section} - only facts from the source text or that you are sure of; use "" if unknown.
equations: 1-3 strings, the key equation(s) as the paper states them (unicode text).
idea: 2-4 plain sentences naming the mechanism for the stated audience. why: 1-2 sentences on why it matters.
symbols: [{sym, meaning}] for every symbol used in equations/visual/controls.
steps: 2-4 short strings on how to read the visual.
controls: >=2 controls that change the result, each {id,label,type,help,...}. ONLY these four types exist (no text/number/list inputs): for a short list of numbers (e.g. a mini-batch of 4-6 values) use a ONE-ROW matrix control, default [[v1,v2,...]] with min,max,step. type "range": min,max,step,default(number). "toggle": default(bool). "select": options[{value,label}], default(one option value). "matrix": default = 2D array (<=4x4), min,max,step for entries, optional rowLabels,colLabels. If the brief asks to change a count (e.g. number of outcomes), make it a range/select that the compute function uses.
explorations: EXACTLY 2 of {title,change,observe,why,set}. set = {controlId:value} (matrix = full 2D array) that realises the exploration. change = what to do, observe = what to look for (quote real numbers that compute produces from those settings), why = the mechanism behind it.
limitation: one assumption, limitation or common misunderstanding (2-3 sentences).
invariants: 2-4 {label,test}; test is a JS expression over (s,p,H) that must be true for EVERY valid setting (floats: compare with 1e-9 tolerance). Choose the checks the brief asks for.
tests: 2-3 {label,set,test}: fixed edge cases (set overrides defaults; test over (s,p,H)) taken from the brief or the paper's own special cases.
supported: 3-5 strings - statements the SOURCE EXCERPT itself makes (cite equation/section). ours: 2-4 strings - toy values, simplifications and examples WE chose; never imply the toy reproduces the paper's experiments.

<compute> is the body of function(p,H) (statements ending in a top-level `return {...};`; do NOT wrap it in a function declaration). p[id] = current control values. Return {show:[{label,value}, ...], ...anything draw needs}. value = number | string | 2D array | {matrix,rows,cols}. List key intermediate values in calculation order. Do real arithmetic from p (no hard-coded results); must never throw or yield NaN/Infinity for any valid control values (handle zeros, ties, ranges' endpoints).
<draw> is the body of function(s,p,H) returning an SVG string built with H.svg(w,h,inner). It must show the mechanism (labelled axes/cells/legend, readable text >=11px) and react visibly to every control. Space out blocks so no text overlaps (each heat block needs title+labels height ~45px above and cell rows below). No DOM, no network, no external libraries, no template literals with user data, plain ES6.

Helpers in H (all return strings): H.fmt(x,digits) H.esc(s) H.color(t0to1) H.ink(t) H.svg(w,h,inner,ariaLabel) H.rect(x,y,w,h,fill,extra) H.text(x,y,str,{size,anchor,fill,weight,rotate}) H.line(x1,y1,x2,y2,stroke,width,dash) H.circle(x,y,r,fill)
H.heat(M,{x,y,cw,ch,d,max,rows,cols,title}) coloured numeric matrix cells. rows/cols MUST be arrays of label strings (e.g. ['q1','q2']) or omitted - never numbers. heat draws the row labels, column labels and title itself: do NOT draw them again with H.text. Leave ~45px left and ~45px top margin (title + column labels).
H.bars(vals,{x,y,w,h,labels,max,d,fill,fills,title,ylabel}) vertical bar chart
H.plot([{xs,ys,color,label,dash}],{x,y,w,h,xmin,xmax,ymin,ymax,xlabel,ylabel,title,marks:[{x,y,label}]}) line plot
H.table(M,{rows,cols,d}) HTML table.
Colours: the page has 3 colour themes switched live, so NEVER hard-code hex/rgb colours in draw. Use the theme variables: H.c.ink (text), H.c.mute (secondary text), H.c.chart1/chart2/chart3 (series, in that order), H.c.good, H.c.bad, H.c.grid, H.c.axis, H.color(t0to1) for heat cells; H.heat/H.bars/H.plot already use them by default.
Numeric helpers (use them in compute and in invariant/test expressions; never compare floats with ===): H.sum(arr) H.close(a,b,tol=1e-9) H.rowSums(M) H.transpose(M) H.matmul(A,B).
Invariant/test expressions read the compute result as s.<key> and the control values as p.<controlId>; use only keys your compute returns.
In "observe" quote only numbers that your compute produces at that exploration's `set` values (they are machine-checked); write inputs as given, results to the digits the readouts show.

Quality rules: be scientifically exact and consistent with the source; use the source's notation; define terms for the audience; keep the page focused on the requested concept only; prefer a small default example whose numbers are easy to follow. If source text is missing, rely on your knowledge of the paper and say nothing you are unsure of. Be concise: no filler, compact code."""


def build_user(case, source_text, mode, extra):
    parts = [
        "Paper URL: " + case["source_url"],
        "Audience: " + case["audience"],
        "Learning brief (concept and required outcomes): " + case["focus"],
    ]
    short = {k: v for k, v in extra.items() if len(v) <= 300}
    if short:
        parts.append("Other case fields: " + json.dumps(short, ensure_ascii=False))
    if source_text:
        parts.append("SOURCE TEXT (" + ("provided excerpt" if mode == "excerpt" else "fetched, best-matching window; may be noisy") + "):\n<<<\n" + source_text + "\n>>>")
    else:
        parts.append("SOURCE TEXT: unavailable (no network). Use your knowledge of this paper; state only what you are sure of.")
    parts.append("Produce the three blocks now.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- OpenRouter client
class Budget(Exception):
    pass


CALL_TIMEOUT_S = 200   # normal calls take 10-60 s; anything past this is a hung connection
REVISE_TIMEOUT_S = 140


def post_with_deadline(url, headers, data, total_s):
    """requests' timeout is per read, so a server that keeps trickling bytes can hang a call for minutes.
    Run the request in a daemon thread and give up on it after a hard wall-clock limit for the whole call."""
    box = {}

    def work():
        try:
            box["r"] = requests.post(url, headers=headers, data=data, timeout=(10, total_s))
        except BaseException as e:  # reported to the caller below
            box["e"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(total_s)
    if th.is_alive():
        raise requests.Timeout("call exceeded %ds" % total_s)
    if "e" in box:
        raise box["e"]
    return box["r"]


def llm_call(trace, key, model, messages, max_tokens, purpose):
    if trace.calls >= MAX_CALLS:
        raise Budget("call limit reached")
    remaining_tok = MAX_COMPLETION_TOKENS - trace.completion_tokens
    max_tokens = min(max_tokens, remaining_tok)
    if max_tokens < 1500:
        raise Budget("completion-token budget exhausted")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2,
            "reasoning": {"enabled": False}}
    last_err = None
    for attempt in range(3):
        left = DEADLINE_S - (time.time() - START)
        if left < 20 or trace.calls >= MAX_CALLS:
            raise Budget("time or call limit reached")
        trace.calls += 1
        t0 = time.time()
        try:
            r = post_with_deadline(API_URL, {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                                   json.dumps(body), min(CALL_TIMEOUT_S if purpose == "generate" else REVISE_TIMEOUT_S, left - 5))
            dt = round(time.time() - t0, 2)
            if r.status_code == 400 and "reasoning" in body and "reason" in r.text.lower():
                body.pop("reasoning")  # model rejects the reasoning knob; retry without it
                trace.log("llm", "chat_completion", "rejected_param_retry", call=trace.calls, elapsed_s=dt, purpose=purpose)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = "HTTP %d" % r.status_code
                trace.log("llm", "chat_completion", "transient_error", call=trace.calls, elapsed_s=dt, error=last_err, purpose=purpose)
                time.sleep(min(3 * (attempt + 1), 8))
                continue
            r.raise_for_status()
            data = r.json()
            u = data.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            trace.prompt_tokens += pt
            trace.completion_tokens += ct
            ch = (data.get("choices") or [{}])[0]
            content = (ch.get("message") or {}).get("content") or ""
            trace.log("llm", "chat_completion", "ok", call=trace.calls, purpose=purpose, model=data.get("model", model),
                      prompt_tokens=pt, completion_tokens=ct, elapsed_s=dt, finish_reason=ch.get("finish_reason"),
                      total_prompt_tokens=trace.prompt_tokens, total_completion_tokens=trace.completion_tokens)
            return content
        except requests.RequestException as e:
            last_err = type(e).__name__ + ": " + str(e)[:160]
            trace.log("llm", "chat_completion", "request_error", call=trace.calls, elapsed_s=round(time.time() - t0, 2),
                      error=last_err, purpose=purpose)
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code < 500:
                break
            time.sleep(2)
    raise RuntimeError("OpenRouter call failed: " + str(last_err))


# --------------------------------------------------------------------------- parsing the model reply
def normalize_body(code, call_args):
    """Models sometimes wrap the body in `function name(args){...}`; turn that into a plain body."""
    m = re.match(r"\s*(?:async\s+)?function\s*([A-Za-z_$][\w$]*)?\s*\(", code)
    if m and code.rstrip().endswith("}"):
        if m.group(1):
            return code + "\nreturn %s(%s);" % (m.group(1), call_args)
        return "return (%s)(%s);" % (code.rstrip().rstrip(";"), call_args)
    return code



def parse_reply(text):
    """Return ({block: content}, [problems])."""
    blocks, problems = {}, []
    for tag in ("spec", "compute", "draw"):
        m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), text, flags=re.S)
        if not m:  # tolerate a missing closing tag only when the output was cut off
            m2 = re.search(r"<%s>(.*)$" % tag, text, flags=re.S)
            if m2:
                problems.append("block <%s> is not closed (output truncated?)" % tag)
            continue
        c = m.group(1).strip()
        c = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", c).strip()
        blocks[tag] = c
    if "spec" in blocks:
        try:
            blocks["spec"] = json.JSONDecoder().raw_decode(blocks["spec"])[0]  # first JSON value; ignore trailing text
        except json.JSONDecodeError as e:
            problems.append("spec is not valid JSON: %s" % e)
            del blocks["spec"]
    return blocks, problems


# --------------------------------------------------------------------------- checks
MAX_MATRIX = 10


def validate_spec(spec):
    """Structural validation of the spec; returns a list of problems."""
    pr = []

    def need(key, typ, minlen=1):
        v = spec.get(key)
        if not isinstance(v, typ) or (hasattr(v, "__len__") and len(v) < minlen):
            pr.append("spec.%s missing or wrong type" % key)
            return False
        return True

    for k in ("title", "idea", "why", "limitation"):
        need(k, str)
    need("paper", dict)
    for k, n in (("equations", 1), ("symbols", 1), ("explorations", 2), ("invariants", 1), ("supported", 2), ("ours", 1)):
        need(k, list, n)
    if len(spec.get("explorations", [])) != 2:
        pr.append("spec.explorations must contain exactly 2 items")
    if not need("controls", list, 2):
        return pr
    ids = set()
    for c in spec["controls"]:
        if not isinstance(c, dict) or not c.get("id") or "default" not in c or c.get("type") not in ("range", "toggle", "select", "matrix"):
            pr.append("bad control (needs id, default and type exactly one of range/toggle/select/matrix; use a one-row matrix for lists of numbers): %s" % json.dumps(c)[:120])
            continue
        ids.add(c["id"])
        t = c["type"]
        if t == "range":
            if not all(isinstance(c.get(k), (int, float)) for k in ("min", "max", "default")) or not (c["min"] <= c["default"] <= c["max"]) or c["min"] >= c["max"]:
                pr.append("range control %s needs numeric min<max and default within" % c["id"])
        elif t == "select":
            vals = [o.get("value") if isinstance(o, dict) else o for o in c.get("options", [])]
            if len(vals) < 2 or c["default"] not in vals:
                pr.append("select control %s needs >=2 options and default among them" % c["id"])
        elif t == "matrix":
            d = c["default"]
            if not (isinstance(d, list) and d and all(isinstance(r, list) and r for r in d)):
                pr.append("matrix control %s: default must be a non-empty 2D array like [[1,2],[3,4]] (a list of rows)" % c["id"])
            elif len({len(r) for r in d}) != 1:
                pr.append("matrix control %s: all rows must have the same length" % c["id"])
            elif not all(isinstance(v, (int, float)) and not isinstance(v, bool) for r in d for v in r):
                pr.append("matrix control %s: every entry must be a number" % c["id"])
            elif len(d) > MAX_MATRIX or len(d[0]) > MAX_MATRIX:
                pr.append("matrix control %s is %dx%d but at most %dx%d is allowed; use fewer entries" % (c["id"], len(d), len(d[0]), MAX_MATRIX, MAX_MATRIX))
            elif not (isinstance(c.get("min"), (int, float)) and isinstance(c.get("max"), (int, float))):
                pr.append("matrix control %s needs numeric min and max" % c["id"])
        elif t == "toggle" and not isinstance(c["default"], bool):
            pr.append("toggle %s default must be boolean" % c["id"])
    by_id = {c["id"]: c for c in spec["controls"] if isinstance(c, dict) and "id" in c}

    def check_set(where, st):
        if not isinstance(st, dict) or not st:
            pr.append("%s.set must be a non-empty object" % where)
            return
        for k, v in st.items():
            c = by_id.get(k)
            if not c:
                pr.append("%s.set uses unknown control %s" % (where, k))
            elif c["type"] == "range" and not (isinstance(v, (int, float)) and c["min"] <= v <= c["max"]):
                pr.append("%s.set[%s]=%s outside [%s,%s]" % (where, k, v, c["min"], c["max"]))
            elif c["type"] == "toggle" and not isinstance(v, bool):
                pr.append("%s.set[%s] must be boolean" % (where, k))
            elif c["type"] == "select" and v not in [o.get("value") if isinstance(o, dict) else o for o in c["options"]]:
                pr.append("%s.set[%s]=%s is not an option" % (where, k, v))
            elif c["type"] == "matrix":
                d = c["default"]
                ok = isinstance(v, list) and len(v) == len(d) and all(isinstance(r, list) and len(r) == len(d[0]) and all(isinstance(x, (int, float)) and c["min"] <= x <= c["max"] for x in r) for r in v)
                if not ok:
                    pr.append("%s.set[%s] must be a %dx%d matrix with entries in [%s,%s]" % (where, k, len(d), len(d[0]), c["min"], c["max"]))

    for i, x in enumerate(spec.get("explorations", [])):
        if not isinstance(x, dict) or not all(isinstance(x.get(k), str) and x.get(k) for k in ("title", "change", "observe", "why")):
            pr.append("exploration %d needs title/change/observe/why strings" % (i + 1))
            continue
        check_set("exploration %d" % (i + 1), x.get("set"))
    for i, t in enumerate(spec.get("tests", [])):
        if not isinstance(t, dict) or not t.get("label") or not t.get("test"):
            pr.append("test %d needs label and test" % (i + 1))
        else:
            check_set("test %d" % (i + 1), t.get("set"))
    for i, v in enumerate(spec.get("invariants", [])):
        if not isinstance(v, dict) or not v.get("label") or not v.get("test"):
            pr.append("invariant %d needs label and test" % (i + 1))
    return pr


def sample_sets(spec, n_random=8, seed=7):
    rng = random.Random(seed)
    ctrls = spec["controls"]
    base = {c["id"]: c["default"] for c in ctrls}

    def rnd(c):
        t = c["type"]
        if t == "range":
            v = rng.uniform(c["min"], c["max"])
            st = c.get("step")
            if isinstance(st, (int, float)) and st > 0:
                v = c["min"] + round((v - c["min"]) / st) * st
                v = min(c["max"], max(c["min"], v))
            return v
        if t == "toggle":
            return rng.random() < 0.5
        if t == "select":
            o = rng.choice(c["options"])
            return o.get("value") if isinstance(o, dict) else o
        d = c["default"]
        return [[rng.uniform(c["min"], c["max"]) for _ in r] for r in d]

    sets = [dict(base)]
    variations = []
    for c in ctrls:
        alts = []
        t = c["type"]
        if t == "range":
            alts = [c["min"], c["max"]]
        elif t == "toggle":
            alts = [not c["default"]]
        elif t == "select":
            alts = [o.get("value") if isinstance(o, dict) else o for o in c["options"]]
        else:
            d = c["default"]
            lo, hi = c["min"], c["max"]
            mid = (lo + hi) / 2
            alts = [[[mid] * len(r) for r in d], [[hi if (i, j) == (0, 0) else lo for j in range(len(r))] for i, r in enumerate(d)], [[hi] * len(r) for r in d], [[lo] * len(r) for r in d]]
            if lo <= 0 <= hi:
                alts.append([[0] * len(r) for r in d])
        for a in alts:
            p = dict(base)
            p[c["id"]] = a
            sets.append(p)
            variations.append({"id": c["id"], "alt": p})
    for _ in range(n_random):
        sets.append({c["id"]: rnd(c) for c in ctrls})
    # all-extreme corner sets
    for pick in ("min", "max"):
        p = dict(base)
        for c in ctrls:
            if c["type"] == "range":
                p[c["id"]] = c[pick]
        sets.append(p)
    return base, sets, variations


HARNESS = r"""
function mkFn(args, src){ var wrap=function(b){ return 'return (function(){\n'+b+'\n}).call(this);'; };
  try{ return new Function(args, wrap('return (' + src + '\n);')); }catch(e){ return new Function(args, wrap(src)); } }
function clone(x){ return JSON.parse(JSON.stringify(x)); }
function nonfinite(v){ if(typeof v==='number') return !isFinite(v); if(Array.isArray(v)) return v.some(nonfinite);
  if(v&&typeof v==='object') return Object.keys(v).some(function(k){return nonfinite(v[k]);}); return false; }
function runChecks(spec, cfg){
  var out={errors:[],influential:[],drawn:0,computed:0}, seen={}, lastKeys='';
  function err(key,msg){ if(!seen[key]){ seen[key]=1; out.errors.push(msg.slice(0,300)); } }
  var compute, draw, invs=[], tests=[];
  try{ compute=mkFn('p,H',spec.compute); }catch(e){ err('c0','[compute] syntax error: '+e.message); return out; }
  try{ draw=mkFn('s,p,H',spec.draw); }catch(e){ err('d0','[draw] syntax error: '+e.message); return out; }
  (spec.invariants||[]).forEach(function(i){ try{ invs.push({l:i.label,f:mkFn('s,p,H',i.test)}); }catch(e){ err('iv'+i.label,'[spec.invariants] "'+i.label+'" has a syntax error: '+e.message); } });
  (spec.tests||[]).forEach(function(t){ try{ tests.push({l:t.label,set:t.set||{},f:mkFn('s,p,H',t.test)}); }catch(e){ err('tv'+t.label,'[spec.tests] "'+t.label+'" has a syntax error: '+e.message); } });
  function short(p){ return JSON.stringify(p).slice(0,140); }
  function evalSet(p, tag){
    var s;
    try{ s=compute(clone(p),H); }catch(e){ err('ct'+e.message,'[compute] threw "'+e.message+'" for '+short(p)); return null; }
    if(!s||typeof s!=='object'||!Array.isArray(s.show)||s.show.length<2){
      err('cs','[compute] body returned '+(s===undefined?'undefined (it must END with `return {show:[...],...};` at top level, not define a function)':(Array.isArray(s)?'an array':typeof s==='object'&&s?'an object with keys ['+Object.keys(s).join(',')+'] and show='+typeof s.show:typeof s))+'; it must return an object with show:[at least 2 {label,value}]'); return null; }
    if(nonfinite(s)){ err('nf','[compute] output contains NaN/Infinity for '+short(p)); }
    out.computed++; lastKeys=Object.keys(s).join(',');
    try{ var h=draw(s,clone(p),H);
      if(typeof h!=='string'||h.length<80||h.indexOf('<svg')<0){ err('dh','[draw] must return an SVG string built with H.svg'); }
      else { var bm=/NaN|undefined|Infinity/.exec(h); if(bm){ err('dn','[draw] output contains '+bm[0]+' for '+short(p)+' near: ...'+h.slice(Math.max(0,bm.index-90),bm.index+30).replace(/\s+/g,' ')+'...'); } out.drawn++; }
    }catch(e){ err('dt'+e.message,'[draw] threw "'+e.message+'" for '+short(p)); }
    return s;
  }
  cfg.sets.forEach(function(p){
    var s=evalSet(p); if(!s) return;
    invs.forEach(function(i){ try{ if(i.f(s,clone(p),H)!==true) err('if'+i.l,'[spec.invariants] "'+i.l+'" is not true for '+short(p)+' (compute returns keys: '+lastKeys+'; its readouts there: '+JSON.stringify(s.show).slice(0,320)+')'); }catch(e){ err('ie'+i.l,'[spec.invariants] "'+i.l+'" threw "'+e.message+'" (test sees s=compute output with keys: '+lastKeys+'; p=control values keyed by control id)'); } });
  });
  tests.forEach(function(t){
    var p=clone(cfg.base); for(var k in t.set) p[k]=clone(t.set[k]);
    var s=evalSet(p); if(!s) return;
    try{ if(t.f(s,clone(p),H)!==true) err('tf'+t.l,'[spec.tests] "'+t.l+'" is not true for '+short(t.set)+' (compute returns keys: '+lastKeys+'; its readouts there: '+JSON.stringify(s.show).slice(0,320)+')'); }catch(e){ err('te'+t.l,'[spec.tests] "'+t.l+'" threw "'+e.message+'" (compute returns keys: '+lastKeys+')'); }
  });
  // do the controls matter? compare outputs with each control varied alone
  var sig=function(p){ try{ var s=compute(clone(p),H); return JSON.stringify(s.show)+draw(s,clone(p),H); }catch(e){ return null; } };
  var b=sig(cfg.base), infl={};
  cfg.variations.forEach(function(v){ var x=sig(v.alt); if(x!==null && b!==null && x!==b) infl[v.id]=1; });
  out.influential=Object.keys(infl);
  // numbers the calculation produces at each exploration's preset (used to verify the "observe" text)
  out.expl=(spec.explorations||[]).map(function(x){
    var p=clone(cfg.base); for(var k in (x.set||{})) p[k]=clone(x.set[k]);
    var nums=[], show='', sc=[];
    try{ var s=compute(clone(p),H); show=JSON.stringify(s.show).slice(0,400);
      sc=s.show.filter(function(it){ return typeof it.value==='number'||typeof it.value==='string'; }).slice(0,8)
        .map(function(it){ return {label:String(it.label),value:it.value}; });
      (function w(v,d){ if(nums.length>3000||d>6) return; if(typeof v==='number'){ if(isFinite(v)) nums.push(v); }
        else if(typeof v==='string'){ (v.match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/gi)||[]).forEach(function(t){ nums.push(parseFloat(t)); }); }
        else if(Array.isArray(v)) v.forEach(function(y){ w(y,d+1); });
        else if(v&&typeof v==='object') Object.keys(v).forEach(function(k){ w(v[k],d+1); }); })(s,0);
    }catch(e){}
    return {nums:nums, show:show, scalars:sc};
  });
  return out;
}
"""

_RUNTIME = (HERE / "runtime.js").read_text(encoding="utf-8")


class EngineUnavailable(Exception):
    """The QuickJS wheel could not be imported on this machine."""


def run_js_checks(spec):
    try:
        import quickjs
    except Exception as e:
        raise EngineUnavailable(type(e).__name__ + ": " + str(e)[:160])
    base, sets, variations = sample_sets(spec)
    ctx = quickjs.Context()
    ctx.set_time_limit(20)
    ctx.set_memory_limit(256 * 1024 * 1024)
    ctx.eval(_RUNTIME)
    ctx.eval(HARNESS)
    ctx.eval("var SPEC = %s; var CFG = %s;" % (json.dumps(spec), json.dumps({"base": base, "sets": sets, "variations": variations})))
    return json.loads(ctx.eval("JSON.stringify(runChecks(SPEC, CFG))"))


FORBIDDEN_CODE = re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|importScripts|eval|document|window|localStorage)\b|https?://|import\s*\(")


def check_all(spec):
    """Run every check; returns (failures, summary)."""
    failures = ["[spec] " + f for f in validate_spec(spec)]
    summary = {}
    if failures:
        return failures, summary
    for k in ("compute", "draw"):
        if not isinstance(spec.get(k), str) or len(spec[k].strip()) < 20:
            failures.append("[%s] block is missing or empty" % k)
        elif FORBIDDEN_CODE.search(spec[k]):
            failures.append("[%s] must not use network/DOM/eval APIs or URLs" % k)
    if failures:
        return failures, summary
    try:
        res = run_js_checks(spec)
    except EngineUnavailable as e:
        # structural checks passed; the page still runs its own invariants live in the browser
        return [], {"js_checks": "skipped: engine unavailable (%s)" % e}
    except Exception as e:
        return ["checker could not execute the code: %s" % str(e)[:300]], summary
    failures += res["errors"]
    summary = {"param_sets_computed": res["computed"], "param_sets_drawn": res["drawn"], "controls_that_change_output": res["influential"]}
    if len(res["influential"]) < 2:
        failures.append("[spec.controls] at least 2 controls must visibly change the readouts or the picture; only these do: %s" % res["influential"])
    if not failures:
        claims = check_exploration_claims(spec, res.get("expl", []))
        summary["exploration_claims_checked"] = len(res.get("expl", []))
        failures += claims
    return failures, summary


def _fmt_value(v):
    if isinstance(v, (int, float)):
        return ("%.4g" % v) if abs(v) >= 1e-4 or v == 0 else ("%.3e" % v)
    return str(v)


def repair_unverified_observations(spec):
    """Replace any `observe` text whose numbers the calculation does not reproduce with a sentence built from the
    calculation's own readouts at that exploration's settings. Returns (spec, repaired_indices)."""
    try:
        res = run_js_checks({**spec, "invariants": [], "tests": []})
    except Exception:
        return spec, []
    expl = res.get("expl", [])
    fixed = []
    new = dict(spec)
    new["explorations"] = [dict(x) for x in spec["explorations"]]
    for i, (x, e) in enumerate(zip(spec["explorations"], expl)):
        if check_exploration_claims({**spec, "explorations": [x]}, [e]):
            vals = "; ".join("%s = %s" % (s["label"], _fmt_value(s["value"])) for s in e.get("scalars", []))
            new["explorations"][i]["observe"] = ("With these settings the readouts above show: " + vals + ". "
                                                 "(These values come straight from the page's calculation.) " +
                                                 "Compare them with the starting values and note what moved.") if vals else x["observe"]
            fixed.append(i + 1)
    return new, fixed


NUM_RE = re.compile(r"(?<![\w.])(-?\d+)\s*/\s*(\d+)(?![\w.])|(?<![\w.])-?\d+\.\d+")


def _numbers_in(v, out):
    if isinstance(v, bool):
        return
    if isinstance(v, (int, float)):
        out.append(float(v))
    elif isinstance(v, list):
        for i in v:
            _numbers_in(i, out)
    elif isinstance(v, dict):
        for i in v.values():
            _numbers_in(i, out)


def check_exploration_claims(spec, expl):
    """Each decimal/fraction quoted in an exploration's `observe` text must be an input value or appear in the
    calculation's output at that exploration's settings (within the quoted precision)."""
    problems = []
    for i, (x, e) in enumerate(zip(spec.get("explorations", []), expl)):
        have = list(e.get("nums", []))
        _numbers_in(x.get("set"), have)
        for c in spec["controls"]:
            _numbers_in(c.get("default"), have)
        bad = []
        for m in NUM_RE.finditer(x.get("observe", "")):
            tok = m.group(0)
            if m.group(1) is not None:
                if int(m.group(2)) == 0:
                    continue
                val, tol = int(m.group(1)) / int(m.group(2)), 5e-3
            else:
                val, tol = float(tok), 10.0 ** -len(tok.split(".")[1])
            if not any(abs(abs(c) - abs(val)) <= tol * 1.0001 + 1e-12 for c in have):
                bad.append(tok)
        if bad:
            problems.append('[spec.explorations] exploration %d "observe" cites %s, which the calculation does not produce at that '
                            'exploration\'s settings (its readouts there: %s). Rewrite "observe" using only numbers it produces.'
                            % (i + 1, ", ".join(sorted(set(bad))), e.get("show", "")[:300]))
    return problems


# --------------------------------------------------------------------------- assembling the page
def build_page(spec, case, mode):
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    spec = dict(spec)
    spec["source_url"] = case["source_url"]
    spec["audience"] = case["audience"]
    sj = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    note = ""
    if mode == "none":
        note = ("The paper text could not be retrieved when this page was generated, so the equations and statements above come from the "
                "generator's prior knowledge of the paper and should be verified against the source.")
    page = (tpl.replace("__TITLE__", html.escape(spec.get("title", "Paper to Playground")))
               .replace("__SRCNOTE__", html.escape(note))
               .replace("__SUPPHEAD__", "The paper, as recalled by the generator (not checked against the text)" if mode == "none"
                        else "Supported by the paper excerpt")
               .replace("__RUNTIME__", _RUNTIME)
               .replace("__SPEC_JSON__", sj))
    return page


def static_page_checks(page, key):
    bad = []
    if re.search(r"<script[^>]+src=|<link\b|@import|url\(\s*['\"]?https?:|<img[^>]+src=['\"]?https?:|<iframe", page, flags=re.I):
        bad.append("page references an external resource")
    if key and key in page:
        bad.append("API key present in page")
    bad += script_syntax_errors(page)
    return bad


def script_syntax_errors(page):
    """Compile every inline script of the final page (without running it) so a syntax error can never ship as a blank page."""
    try:
        import quickjs
    except Exception:
        return []
    ctx = quickjs.Context()
    errors = []
    for m in re.finditer(r"<script(?P<attrs>[^>]*)>(?P<src>.*?)</script>", page, flags=re.S):
        if "application/json" in m.group("attrs"):
            continue
        try:
            ctx.eval("new Function(%s)" % json.dumps(m.group("src")))
        except Exception as e:
            errors.append("page script has a syntax error: %s" % str(e)[:160])
    return errors


# --------------------------------------------------------------------------- main loop
def revision_message(failures):
    lst = "\n".join("- " + f for f in failures[:8])
    return ("Automated checks of your output found these problems (the bracket names the block to fix):\n" + lst +
            "\n\nReturn a complete replacement for EVERY block that has a problem: [spec*] items -> resend the full <spec> JSON "
            "(invariants/tests live in the spec and must use the keys your <compute> returns); [compute] -> <compute>; [draw] -> <draw>. "
            "Blocks without problems may be omitted. No commentary.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", required=True)
    a = ap.parse_args()

    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    trace = Trace(out / "trace.jsonl")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    code = 1
    try:
        trace.log("start", "begin", "ok", model=a.model, limits={"max_calls": MAX_CALLS, "max_completion_tokens": MAX_COMPLETION_TOKENS, "deadline_s": DEADLINE_S})
        case = json.loads(Path(a.input).read_text(encoding="utf-8"))
        for f in ("source_url", "focus", "audience"):
            if not isinstance(case.get(f), str) or not case[f].strip():
                raise ValueError("case.json is missing string field: " + f)
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        trace.log("input", "validate_case", "ok", fields=sorted(case.keys()))

        source_text, mode, extra = acquire_source(case, trace)
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_user(case, source_text, mode, extra)}]

        spec, blocks_have, summary, failures = None, {}, {}, ["no output yet"]
        revisions = 0
        prev_failures = None
        for attempt in range(1 + MAX_REVISIONS):
            try:
                reply = llm_call(trace, key, a.model, messages, FIRST_MAX_TOKENS if attempt == 0 else REVISE_MAX_TOKENS,
                                 "generate" if attempt == 0 else "revise_%d" % attempt)
            except Budget as e:
                trace.log("budget", "stop", "limit", reason=str(e))
                break
            blocks, problems = parse_reply(reply)
            if "spec" in blocks:
                blocks_have["spec"] = blocks["spec"]
            for k, args in (("compute", "p,H"), ("draw", "s,p,H")):
                if k in blocks:
                    blocks_have[k] = normalize_body(blocks[k], args)
            trace.log("parse", "extract_blocks", "ok" if not problems else "problems", blocks=sorted(blocks.keys()), problems=problems)
            missing = [k for k in ("spec", "compute", "draw") if k not in blocks_have]
            if missing or problems:
                failures = problems + ["missing block(s): " + ", ".join(missing)] if missing else problems
            else:
                spec = dict(blocks_have["spec"])
                spec["compute"], spec["draw"] = blocks_have["compute"], blocks_have["draw"]
                failures, summary = check_all(spec)
                trace.log("check", "validate_spec_and_execute_js", "pass" if not failures else "fail", failures=failures, **summary)
                if not failures:
                    break
                if attempt >= 1 and all(f.startswith("[spec.explorations]") for f in failures):
                    break  # only prose-number mismatches left: stop spending tokens, report them in the trace
            if attempt == MAX_REVISIONS:
                break
            if attempt >= 1 and failures == prev_failures:
                trace.log("revise", "stop_no_progress", "same_failures_twice", failures=failures[:4])
                break
            prev_failures = list(failures)
            revisions += 1
            trace.log("revise", "request_fix", "scheduled", revision=revisions, failures=failures[:8])
            # keep the context small: original request + the latest merged blocks + the new feedback
            latest = "".join("<%s>%s</%s>\n" % (k, json.dumps(blocks_have[k], ensure_ascii=False) if k == "spec" else blocks_have[k], k)
                             for k in ("spec", "compute", "draw") if k in blocks_have)
            messages = messages[:2] + [{"role": "assistant", "content": latest or reply},
                                       {"role": "user", "content": revision_message(failures)}]

        if spec is not None and failures and all(re.match(r'\[spec\.(invariants|tests)\] "', f) for f in failures):
            # Only self-checks written by the model still fail: do not publish claims we could not verify.
            bad_labels = {re.match(r'\[spec\.(?:invariants|tests)\] "(.*?)"', f).group(1) for f in failures}
            kept = dict(spec)
            for k in ("invariants", "tests"):
                kept[k] = [x for x in spec.get(k, []) if x.get("label") not in bad_labels]
            if kept["invariants"]:
                f2, s2 = check_all(kept)
                f2 = [f for f in f2 if not f.startswith("[spec.explorations]")]  # prose numbers are repaired below
                trace.log("check", "drop_unverifiable_checks", "pass" if not f2 else "fail", dropped=sorted(bad_labels), failures=f2, **s2)
                if not f2:
                    kept["dropped_checks"] = sorted(bad_labels)  # disclosed on the page, never silent
                    spec, failures = kept, []
        if spec is not None and all(k in spec for k in ("compute", "draw")) and not validate_spec(spec):
            spec, fixed = repair_unverified_observations(spec)
            if fixed:
                trace.log("revise", "replace_unverified_observations", "ok", explorations=fixed,
                          note="quoted numbers did not match the calculation; text rebuilt from computed readouts")
            failures = [f for f in failures if not f.startswith("[spec.explorations]")]
        usable = spec is not None and all(k in spec for k in ("compute", "draw"))
        if usable and failures:
            # ship best effort only if it actually computes and draws at the defaults
            base, _, _ = sample_sets(spec) if not validate_spec(spec) else ({}, [], [])
            try:
                r = run_js_checks({**spec, "invariants": [], "tests": []}) if base else {"computed": 0}
                usable = r.get("computed", 0) > 0 and r.get("drawn", 0) > 0
            except EngineUnavailable:
                usable = True
            except Exception:
                usable = False
        if not usable:
            trace.log("finish", "no_usable_page", "failed", failures=failures[:8])
            code = 2
        else:
            page = build_page(spec, case, mode)
            bad = static_page_checks(page, key)
            trace.log("check", "static_page_checks", "pass" if not bad else "fail", failures=bad)
            if bad:
                code = 3
            else:
                (out / "index.html").write_text(page, encoding="utf-8")
                trace.log("finish", "write_index_html", "ok", bytes=len(page.encode("utf-8")), checks_passed=not failures,
                          remaining_failures=failures[:8], revisions=revisions, source_mode=mode,
                          total_prompt_tokens=trace.prompt_tokens, total_completion_tokens=trace.completion_tokens,
                          api_calls=trace.calls, elapsed_s=round(time.time() - START, 2))
                code = 0
    except Exception as e:
        trace.log("finish", "error", "failed", error=type(e).__name__ + ": " + str(e)[:300])
        code = 1
    finally:
        trace.log("end", "exit", "code_%d" % code, elapsed_s=round(time.time() - START, 2), api_calls=trace.calls,
                  prompt_tokens=trace.prompt_tokens, completion_tokens=trace.completion_tokens)
        trace.close()
    sys.exit(code)


if __name__ == "__main__":
    main()

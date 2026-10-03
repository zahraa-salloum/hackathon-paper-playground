# Paper to Playground

An agent that turns a paper URL and a short learning brief into a single, self-contained, interactive HTML explanation for an engineering undergraduate. It identifies the mechanism, plans the explanation, generates the page, **runs the generated code to check it**, and revises when a check fails. Built for the EECE503P / EECE798S "Paper to Playground" hackathon.

| | |
|---|---|
| **Team** | Zahraa Salloum, Aseel Mousa, Razan Al Moghrabi |
| **Repository** | https://github.com/zahraa-salloum/hackathon-paper-playground |
| **Model** | `deepseek/deepseek-v4.1-flash` (DeepSeek V4.1 Flash via OpenRouter). Any OpenRouter model ID works with `--model`. |
| **Python** | 3.11 |

## Setup and run

```bash
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY=...            # read from the environment; never stored in the repo or the page
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

Open `out/index.html` in Chromium (double-click, or `python -m http.server --directory out`). It needs no internet and no API key.

**Input.** `case.json` is UTF-8 JSON with the string fields `source_url`, `focus` (the concept and required learning outcomes) and `audience`. Any extra string field longer than 300 characters is treated as a supplied excerpt of the paper.

**Output.**
- `out/index.html` is one file with embedded CSS, JavaScript and SVG visuals. It uses no CDN, remote fonts or images.
- `out/trace.jsonl` has one JSON event per line: stage, action and result, per-call prompt and completion tokens, elapsed seconds, checks, failures and revisions. It contains no credentials or hidden reasoning.

**Exit code.** 0 when a usable page was written. Nonzero if no usable page could be produced.

An example input/output pair is in `example/` (`case.json` and `out/`). It is for showcasing; graded outputs are generated afresh.

## How it works

```
case.json ─► 1 source ─► 2 generate ─► 3 check ─► (fail) 4 revise ─┐
                              ▲                        │            │
                              └────────────────────────┴────────────┘
                                   (pass) ─► 5 assemble page ─► out/index.html
```

1. **Source (0 tokens).** Use a supplied excerpt, otherwise fetch `source_url` (HTML, keeping LaTeX alt-text for math, or PDF via `pypdf`). Keep the ~9,000-character window that best matches the brief, preferring an explicitly named section. If the network is blocked (as in the assessment), the model relies on its own knowledge of the paper and the page says so.
2. **Generate (usually 1 call).** The model replies with three blocks:
   - a JSON **spec**: title, equations, plain-language idea, symbol glossary, controls, **two guided explorations**, one limitation or misunderstanding, self-checks, and source grounding;
   - a `compute` JavaScript body that does the real arithmetic and returns the intermediate values to display;
   - a `draw` JavaScript body that returns the SVG for the mechanism.

   A fixed, generic template (`template.html`) and helper library (`runtime.js`) render everything else: layout, controls (sliders, toggles, selects, editable matrices), readouts, exploration buttons, grounding, and a live self-check panel. The repo contains no paper-specific content.
3. **Check (0 tokens).** The generated code is executed in an embedded JavaScript engine (QuickJS) on ~30 parameter sets: defaults, each control's extremes, and random values. The checks:
   - the spec has the required sections; control ranges and exploration settings are valid;
   - `compute` never throws and never produces NaN or Infinity; `draw` returns clean SVG with no "undefined" or NaN text;
   - at least two controls actually change the readouts or the picture;
   - every **invariant** and **edge-case test** the model wrote holds (for example "each row of weights sums to 1", "certainty gives 0 bits");
   - every decimal or fraction quoted in an exploration's "What to observe" text is reproduced by the calculation at that exploration's settings;
   - the code uses no network, DOM or `eval` APIs, and the final page has no external resources, no API key, and no JavaScript syntax errors (every inline script is compiled before the page is written).
4. **Revise (only on failure, at most 3 rounds).** Failures are tagged by block (`[spec]`, `[compute]`, `[draw]`) and include the actual computed readouts, so the model can tell whether the code or the check is wrong. Only the latest blocks plus the feedback are re-sent, to keep prompts small. Revision stops early if the same failures come back twice.
5. **Assemble and finish.** If a model-written self-check still cannot be verified, it is removed and **listed on the page**, so nothing unverified is presented as passing. If an exploration's quoted numbers still do not match, that text is replaced with a sentence built from the calculation's own readouts. All checks and drops are logged to the trace.

### Design decisions

- **Few tokens, few calls.** The model writes only the mechanism-specific parts, not boilerplate. Reasoning is switched off, outputs are compact, and typical cases finish in 1 to 4 calls.
- **Honest grounding.** Every page separates "supported by the paper excerpt" from "our own examples and simplifications", and states that it does not reproduce the paper's experiments. When the paper text could not be fetched, the heading and a note say the content comes from the generator's recall and was not checked against the text.
- **The numbers are computed.** Every displayed value comes from running the `compute` code on the current inputs. The same invariants run live in the browser's self-check panel.
- **Limits enforced in code.**

  | | Assessment limit | Enforced |
  |---|---|---|
  | API requests (including retries) | 10 | 8 |
  | Completion tokens | 30,000 | 28,500, via `usage` and a per-call `max_tokens` cap |
  | Total time | 10 minutes | 8 minutes |

  Each API call also has a hard wall-clock limit (200 s for generation, 140 s for revisions), because a hung connection was seen to stall a run for 7 minutes. Transient errors (HTTP 429/5xx, dropped connections) are retried within the same budget.
- **Three switchable colour themes.** Every page has a theme switcher in the header: **Basic**, **Ink wash** (charcoal, gray, ivory, slate) and **Cherry blossom** (pink, white, mint, rose). All colours, including the SVG charts and heat-map cells, come from CSS variables, so the whole page and its visuals change together. The model is told to use the theme colour helpers (`H.c.*`, `H.color`) instead of hard-coded colours. The choice is remembered in the browser when storage is available.
- **Graceful degradation.** If the JavaScript engine cannot be loaded on the host, execution checks are skipped (and logged) instead of failing the run.

## Repository layout

| Path | Purpose |
|---|---|
| `agent.py` | CLI, OpenRouter client, source retrieval, prompts, checks, revision loop, trace |
| `template.html` | Generic page template (layout, three colour themes and switcher, page logic) |
| `runtime.js` | Drawing and numeric helpers (`H.heat`, `H.bars`, `H.plot`, `H.close`, `H.matmul`, ...) shared by the page and the checker |
| `requirements.txt` | Pinned dependencies |
| `example/` | Showcase input and generated output |
| `tests/` | Practice cases and test scripts (see below) |

## Testing

`tests/cases/` holds practice inputs for several kinds of mechanism: attention, entropy, Adam, batch normalization, softmax temperature, diffusion forward process and rotary embeddings.

```bash
bash tests/run_all.sh                       # runs every case, prints one summary line each (needs OPENROUTER_API_KEY)
python tests/run_offline.py --input tests/cases/entropy.json --output out --model MODEL_ID
                                            # same as agent.py but blocks all network except openrouter.ai
python tests/selftest.py fixture_reply_attention.txt
                                            # check and build pipeline on a saved model reply; no network or key
```

The fixture reply is a test aid for the checker and is never used by `agent.py`. Latest full run (7 cases, `deepseek/deepseek-v4.1-flash`): all produced a page with every check passing, in 2 to 5 API calls, well inside the limits.

## Known limitations

- Without the paper text (as in the assessment sandbox), the model recalls the paper from memory. Equations for obscure papers may be wrong; the page labels this.
- Only quoted decimals and fractions in "What to observe" are machine-checked. Numbers in the "why it happens" text are not.
- Checks written by the model can themselves be wrong; such checks are dropped and disclosed rather than shown as passing.

## Reuse credits

- Python libraries: [`requests`](https://requests.readthedocs.io), [`quickjs`](https://github.com/PetterS/quickjs) (QuickJS bindings, used to execute generated code during checks), [`pypdf`](https://pypdf.readthedocs.io) (PDF text extraction).
- All HTML, CSS, JavaScript, SVG helpers and prompts are our own; there are no external fonts, images or CDN assets.
- Developed with the help of an AI coding assistant (Claude Code).
- Models are called through [OpenRouter](https://openrouter.ai).

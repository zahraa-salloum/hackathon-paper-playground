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

- **Input.** `case.json` is UTF-8 JSON with the string fields `source_url`, `focus` (the concept and required learning outcomes) and `audience`. Any extra string field longer than 300 characters is treated as a supplied excerpt of the paper.
- **Output.** `out/index.html` is one file with embedded CSS, JavaScript and SVG visuals (no CDN, remote fonts or images). `out/trace.jsonl` has one JSON event per line: stage, action and result, per-call prompt and completion tokens, elapsed seconds, checks, failures and revisions. It contains no credentials or hidden reasoning.
- **Exit code.** 0 when a usable page was written; nonzero if no usable page could be produced.

## Examples

`example/` contains seven input/output pairs, each produced by one run of `agent.py` with no manual editing (for example `python agent.py --input example/attention/case.json --output example/attention/out --model deepseek/deepseek-v4.1-flash`). They are for showcasing; assessed outputs are generated afresh.

| Example | Paper and focus | API calls | Prompt tokens | Completion tokens | Total tokens | Time |
|---|---|---|---|---|---|---|
| `attention` | Attention Is All You Need, §3.2.1: scaled dot-product attention | 2 | 11,008 | 5,528 | 16,536 | 39 s |
| `entropy` | Shannon, A Mathematical Theory of Communication, §6: discrete entropy | 2 | 11,981 | 4,424 | 16,405 | 19 s |
| `adam` | Adam, Algorithm 1: bias-corrected moment estimates | 4 | 26,840 | 10,605 | 37,445 | 35 s |
| `batchnorm` | Batch Normalization, Algorithm 1 | 1 | 4,243 | 3,844 | 8,087 | 16 s |
| `diffusion` | DDPM, §2 Eq. 4: closed-form forward process | 1 | 2,840 | 2,910 | 5,750 | 17 s |
| `rope` | RoFormer, §3.2: rotary position embedding (2-D) | 4 | 30,627 | 8,755 | 39,382 | 85 s |
| `temperature` | Distilling the Knowledge in a Neural Network, §2: softmax temperature | 1 | 3,843 | 2,741 | 6,584 | 36 s |

The assessment limit of 30,000 applies to **completion** tokens per case; the largest example used 10,605 (the agent also stops itself at 28,500). Total tokens (prompt + completion) are what the efficiency score counts.

All seven passed every check. In a scripted browser audit of 86 interactive states (every exploration button, each slider at its minimum and maximum, toggles, selects, extreme matrix values and all three themes) no page showed NaN, clipped text, cut-off inputs, a failing self-check or a JavaScript error, and every exploration's claim held at its preset.

## How it works

```
case.json ─► 1 source ─► 2 generate ─► 3 check ─► (fail) 4 revise ─┐
                              ▲                        │            │
                              └────────────────────────┴────────────┘
                                   (pass) ─► 5 assemble page ─► out/index.html
```

1. **Source (0 tokens).** Use a supplied excerpt, otherwise fetch `source_url` (HTML, keeping LaTeX alt-text for math, or PDF via `pypdf`) and keep the ~9,000-character window that best matches the brief, preferring an explicitly named section. If the network is blocked (as in the assessment), the model relies on its own knowledge of the paper and the page says so.
2. **Generate (usually 1 call).** The model replies with three blocks:
   - a JSON **spec**: title, equations, plain-language idea, symbol glossary, controls, **two guided explorations** (what to change, a qualitative "what to observe", why it happens, the preset values, and a machine-checkable claim), one limitation or misunderstanding, self-checks, and source grounding;
   - a `compute` JavaScript body that does the real arithmetic and returns the intermediate values to display;
   - a `draw` JavaScript body that returns the SVG for the mechanism.

   A fixed, generic template (`template.html`) and helper library (`runtime.js`) render everything else. The repo contains no paper-specific content.
3. **Check (0 tokens).** The generated code is executed in an embedded JavaScript engine (QuickJS) on ~30 parameter sets: defaults, each control's extremes, and random values. The checks:
   - the spec has the required sections; control ranges and preset values are valid;
   - `compute` never throws and never produces NaN or Infinity; `draw` returns clean SVG with no "undefined" or NaN text;
   - at least two controls actually change the readouts or the picture;
   - every **invariant** and **edge-case test** the model wrote holds (for example "each row of weights sums to 1", "certainty gives 0 bits");
   - each **exploration's claim** is true at its own preset (for example "all weights are equal" for an equal-scores exploration);
   - numbers quoted in an exploration are reproduced by the page at that preset: "what to observe" strictly against the labelled readouts (to the quoted precision), "why it happens" against anything the calculation produces;
   - the code uses no network, DOM or `eval` APIs, and the final page has no external resources, no API key and no JavaScript syntax errors.
4. **Revise (only on failure, at most 3 rounds).** Failures are tagged by block (`[spec]`, `[compute]`, `[draw]`) and include the computed readouts; thrown errors name the failing line and the available helpers. Only the latest blocks plus the feedback are re-sent, to keep prompts small. Exploration claims and quoted numbers get one revision. Revision stops early if the same failures come back twice (except for NaN, which always gets the full budget), and if there is still no usable page the generation is restarted once from scratch, within the call budget.
5. **Assemble and finish.** A model-written self-check that still fails is removed and **listed on the page**; an exploration whose claim still fails is **flagged on the page**; sentences with numbers the page does not reproduce are dropped. All checks, drops and flags are logged to the trace.

### The page

- **Sections:** start here (idea, why it matters, equations, symbols), playground (controls, visual, readouts), two guided explorations, limitation, source grounding, and live self-checks.
- **Every number is computed.** Readouts come from running `compute` on the current inputs, and each exploration also shows the readouts the page computes at its preset, so the text never has to quote a number that could go stale. The same invariants run live in the self-check panel.
- **Robust visuals.** The drawing area grows to fit everything drawn, overlapping labels are nudged apart, and any NaN that slips through is shown as "—". Bar charts support negative values; very small values and floating-point noise are formatted sensibly.
- **Three switchable colour themes:** **Basic**, **Ink wash** (charcoal, gray, ivory, slate) and **Cherry blossom** (pink, white, mint, rose). All colours, including the SVG charts, come from CSS variables, so the whole page changes together.

### Design decisions

- **Few tokens, few calls.** The model writes only the mechanism-specific parts, not boilerplate; reasoning is switched off and outputs are compact. The seven examples used 1 to 4 calls and 5.8k to 39k total tokens (mean 18.6k), of which at most 10,605 were completion tokens.
- **Honest grounding.** Every page separates "supported by the paper excerpt" from "our own examples and simplifications", and states that it does not reproduce the paper's experiments. When the paper text could not be fetched, the heading and a note say the content comes from the generator's recall.
- **Limits enforced in code.**

  | | Assessment limit | Enforced |
  |---|---|---|
  | API requests (including retries) | 10 | 8 |
  | Completion tokens | 30,000 | 28,500, via `usage` and a per-call `max_tokens` cap |
  | Total time | 10 minutes | 8 minutes |

  Each API call also has a hard wall-clock limit (200 s for generation, 140 s for revisions), because a hung connection was seen to stall a run for 7 minutes. Transient errors (HTTP 429/5xx, dropped connections) are retried within the same budget.
- **Graceful degradation.** If the JavaScript engine cannot be loaded on the host, execution checks are skipped (and logged) instead of failing the run.

## Repository layout

| Path | Purpose |
|---|---|
| `agent.py` | CLI, OpenRouter client, source retrieval, prompts, checks, revision loop, trace |
| `template.html` | Generic page template (layout, themes, page logic, layout fitting) |
| `runtime.js` | Drawing and numeric helpers (`H.heat`, `H.bars`, `H.plot`, `H.close`, `H.matmul`, ...) shared by the page and the checker |
| `requirements.txt` | Pinned dependencies |
| `example/` | Seven showcase input/output pairs |
| `tests/` | Practice cases and test scripts |

## Testing

```bash
bash tests/run_all.sh                       # runs every case in tests/cases, one summary line each (needs OPENROUTER_API_KEY)
python tests/run_offline.py --input tests/cases/entropy.json --output out --model MODEL_ID
                                            # same as agent.py but blocks all network except openrouter.ai
python tests/selftest.py fixture_reply_attention.txt
                                            # check and build pipeline on a saved model reply; no network or key
```

The fixture reply is a test aid for the checker and is never used by `agent.py`.

## Known limitations

- Without the paper text (as in the assessment sandbox), the model recalls the paper from memory. Equations for obscure papers may be wrong; the page labels this.
- Exploration claims and self-checks are written by the model. A claim can be too lenient to catch a weak preset; failing ones are flagged or dropped and disclosed rather than shown as passing.
- Numbers in "why it happens" are checked leniently (against anything the calculation produces), so a coincidental match can slip through.
- Label de-collision and drawing-area fitting are generic; a very crowded drawing can still look busy.
- Cost varies by case and run: most cases need 1 or 2 calls, but some need up to 4.

## Reuse credits

- Python libraries: [`requests`](https://requests.readthedocs.io), [`quickjs`](https://github.com/PetterS/quickjs) (QuickJS bindings, used to execute generated code during checks), [`pypdf`](https://pypdf.readthedocs.io) (PDF text extraction).
- All HTML, CSS, JavaScript, SVG helpers and prompts are our own; there are no external fonts, images or CDN assets.
- Developed with the help of an AI coding assistant (Claude Code).
- Models are called through [OpenRouter](https://openrouter.ai).

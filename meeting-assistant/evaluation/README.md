# Evaluation protocol

Run `python evaluation/run.py` from the project directory. It creates a temporary archive, loads three original synthetic meetings, evaluates ten fixed questions with three retrieval strategies, and writes `evaluation/results.local.json`. It needs no API keys, models, audio hardware, or network.

`questions.json` contains the questions, optional prior conversation and meeting scope, answerability labels, and gold passage indices `[meeting_index, segment_index]` into `meeting_assistant/demo.py`. The same fixture is used in the UI demo. Gold labels intentionally include relevant context from multiple meetings rather than only the final date.

| Strategy | What it represents |
|---|---|
| `lexical` | Keyword-search baseline; a proxy for search results a person would inspect |
| `basic` | One-pass retrieval without conversational follow-up or a second evidence-gathering step |
| `contextual` | Bounded retrieval that uses conversation context and expands evidence when useful |

Reported metrics: mean gold-passage recall among the first six citations; mean reciprocal rank of the first gold passage on answerable questions; correct evidence abstention over all questions; compliance with selected meeting scope; and elapsed time. Exact lexical/embedding algorithms are documented in the main README and source. Default local retrieval is deterministic. Wall-clock timing is not.

These results measure retrieval behavior on a small synthetic fixture. They do **not** establish the proposal's research claim, measure user effort, or prove factual answer accuracy. More retrieved passages can improve recall while increasing reading effort. The lexical strategy is not a measured human manual-search experiment. The generated chat provider is disabled during this benchmark.

## Reproducible study with consented meetings

1. Obtain permission, anonymize transcripts, and retain both original audio and corrected timestamped transcripts only with agreement.
2. Have two annotators write answerable, unanswerable, contradictory, follow-up, and cross-session questions. Label required source spans and adjudicate disagreements before running the system.
3. Reserve meetings/questions as a held-out test set. Do not tune retrieval on that set.
4. Compare manual transcript search, one-pass transcript chat, and contextual retrieval with the same providers and transcripts. Counterbalance condition order and question assignment to reduce practice effects.
5. Record task time, passages inspected, correct/partially correct/incorrect answers, unsupported claims, abstention errors, citation precision/recall, and external API cost. Review answers blind to condition.
6. Report per-question data, participant count, audio conditions, model/version settings, uncertainty intervals, and limitations. Evaluate transcription word error rate separately from retrieval using corrected reference text.

For recorded meetings, audio channel labels indicate source, not verified speaker identity. A statement in a transcript is evidence of what was said, not proof that the statement is true.

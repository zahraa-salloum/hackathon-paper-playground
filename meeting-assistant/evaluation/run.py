"""Reproducible, no-network retrieval evaluation on original synthetic meetings."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meeting_assistant.demo import seed_demo
from meeting_assistant.retrieval import RetrievalEngine
from meeting_assistant.storage import Store


def evaluate():
    questions = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
    rows = []
    with tempfile.TemporaryDirectory(prefix="minutes-eval-") as directory:
        store = Store(Path(directory))
        meetings = seed_demo(store)["meetings"]
        meetings = [store.get_meeting(m["id"]) for m in meetings]
        engine = RetrievalEngine(store)
        for strategy in ("lexical", "basic", "contextual"):
            for item in questions:
                gold = {meetings[m]["segments"][s]["id"] for m, s in item["gold"]}
                scope = [meetings[m]["id"] for m in item["scope"]] if "scope" in item else None
                began = time.perf_counter()
                result = engine.answer(item["question"], meeting_ids=scope, history=item.get("history", []), strategy=strategy)
                elapsed = (time.perf_counter() - began) * 1000
                retrieved = [c["segment_id"] for c in result["citations"]][:6]
                hit_positions = [i + 1 for i, sid in enumerate(retrieved) if sid in gold]
                rows.append({
                    "strategy": strategy, "question_id": item["id"],
                    "recall_at_6": round(len(set(retrieved) & gold) / len(gold), 4) if gold else None,
                    "reciprocal_rank": round(1 / min(hit_positions), 4) if hit_positions else 0,
                    "abstention_correct": bool(result["insufficient_evidence"]) == (not item["answerable"]),
                    "scope_correct": all(c["meeting_id"] in scope for c in result["citations"]) if scope else True,
                    "citation_count": len(retrieved), "elapsed_ms": round(elapsed, 2),
                })
    summaries = []
    for strategy in ("lexical", "basic", "contextual"):
        group = [r for r in rows if r["strategy"] == strategy]
        supported = [r for r in group if r["recall_at_6"] is not None]
        summaries.append({"strategy": strategy,
                          "mean_recall_at_6": round(statistics.mean(r["recall_at_6"] for r in supported), 4),
                          "mean_reciprocal_rank": round(statistics.mean(r["reciprocal_rank"] for r in supported), 4),
                          "abstention_accuracy": round(statistics.mean(r["abstention_correct"] for r in group), 4),
                          "scope_accuracy": round(statistics.mean(r["scope_correct"] for r in group), 4),
                          "mean_latency_ms": round(statistics.mean(r["elapsed_ms"] for r in group), 2)})
    return {"dataset": "original-synthetic-atlas-v1", "question_count": len(questions),
            "network_used": False, "summary": summaries, "cases": rows,
            "limitations": "Small synthetic retrieval benchmark, not a measurement of human effort, transcription quality, answer factual accuracy, or performance on real meetings. Latency varies by machine. No fitted/tuned model or paid provider used."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results.local.json")
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{'Strategy':12} {'Recall@6':>10} {'MRR':>9} {'Abstention':>12} {'Scope':>9} {'Mean ms':>10}")
    for row in result["summary"]:
        print(f"{row['strategy']:12} {row['mean_recall_at_6']:10.3f} {row['mean_reciprocal_rank']:9.3f} {row['abstention_accuracy']:12.1%} {row['scope_accuracy']:9.1%} {row['mean_latency_ms']:10.2f}")
    print(f"\nSaved {args.output.resolve()}")
    print(result["limitations"])


if __name__ == "__main__":
    main()

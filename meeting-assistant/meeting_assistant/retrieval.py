"""Bounded, inspectable retrieval with source-backed answers.

The built-in representation is lexical with a small explicit synonym map; it is
not a trained semantic embedding. Optional embedding/chat providers can improve
recall and synthesis without changing the source-citation contract.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re


STOP_WORDS = set("""a an and are as at be been being but by can could did do does for
from had has have how i if in into is it its me my of on or our please say said should
so some tell than that the their them then there these they this those to us was we
were what when where which who why will with would you your about any all meeting
meetings transcript discuss discussed discussion summarize summary give list find
show explain happen happened again also""".split())
SYNONYMS = {
    "decided": "decision", "decide": "decision", "decisions": "decision", "agreed": "decision",
    "launching": "launch", "release": "launch", "shipping": "launch", "ship": "launch",
    "clients": "customer", "client": "customer", "customers": "customer",
    "owners": "owner", "owns": "owner", "assigned": "owner", "responsible": "owner",
    "cost": "budget", "costs": "budget", "spending": "budget",
    "deadlines": "deadline", "due": "deadline", "timeline": "deadline",
    "postponed": "delay", "delayed": "delay", "slipped": "delay",
    "blockers": "risk", "blocker": "risk", "risks": "risk", "blocked": "risk",
    "approval": "approved", "approve": "approved", "approves": "approved",
}
MAX_EMBEDDING_SEGMENTS = 256
MAX_EVIDENCE = 6


def _terms(text):
    result = []
    for word in re.findall(r"[^\W_]+", str(text).casefold(), re.UNICODE):
        if word not in STOP_WORDS and (len(word) > 1 or word.isdigit()):
            result.append(SYNONYMS.get(word, word))
    return result


def _cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    try:
        left = [float(v) for v in left]
        right = [float(v) for v in right]
        if not all(math.isfinite(v) for v in left + right):
            return 0.0
        denominator = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
        return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _overlap(query_terms, text):
    words = Counter(_terms(text))
    if not query_terms or not words:
        return 0.0
    shared = sum(1 for term in set(query_terms) if term in words)
    return shared / math.sqrt(len(set(query_terms)) * len(words))


class RetrievalEngine:
    def __init__(self, store):
        self.store = store

    def answer(self, question, meeting_ids=None, history=None, strategy="contextual"):
        if not isinstance(question, str) or not question.strip() or len(question) > 10000:
            raise ValueError("Ask a question of 1 to 10000 characters")
        if strategy not in ("lexical", "basic", "contextual"):
            raise ValueError("Unknown retrieval strategy")
        question = question.strip()
        settings = self.store.get_settings()
        steps = [{"stage": "scope", "detail": "Search all archived meetings." if meeting_ids is None
                  else f"Search only the {len(meeting_ids)} selected meeting(s)."}]
        records = self.store.all_segments(meeting_ids)
        if not records:
            return self._unsupported(steps, "The selected archive has no transcript evidence. Import or record a meeting first.")

        query = question
        if strategy == "contextual":
            previous = self._history_questions(history)
            is_followup = len(_terms(question)) <= 5 or bool(re.search(r"\b(it|that|those|they|earlier|previous|same)\b", question, re.I))
            if previous and is_followup:
                query += " " + " ".join(previous[-2:])
                steps.append({"stage": "history", "detail": "Added up to two earlier user questions to resolve follow-up context."})

        terms = _terms(query)
        # Broad summary requests may inspect excerpts, but cannot invent coverage.
        summary_request = bool(re.search(r"\b(summarize|summary|overview)\b", question, re.I))
        if not terms and not summary_request:
            return self._unsupported(steps, "I could not find a specific topic to search. Ask about a decision, person, task, or date in the archive.")
        lexical = self.store.search(" ".join(terms), meeting_ids, limit=48) if terms else []
        by_id = {row["id"]: row for row in records}
        scores = {}
        lexical_rank = {row["id"]: rank for rank, row in enumerate(lexical)}
        for row in records:
            overlap = _overlap(terms, row["text"])
            if strategy == "lexical":
                score = 1 / (1 + lexical_rank[row["id"]]) if row["id"] in lexical_rank else 0
            else:
                score = overlap + (0.15 / (1 + lexical_rank[row["id"]]) if row["id"] in lexical_rank else 0)
            if score > 0:
                scores[row["id"]] = score
        steps.append({"stage": "search", "detail": f"Lexical search found {len(lexical)} candidate excerpt(s) across {len(records)} scoped segments."})

        if strategy != "lexical" and settings.get("embedding_provider", "local") != "local":
            self._embed(query, records, lexical, scores, settings, steps)
        elif strategy != "lexical":
            steps.append({"stage": "rank", "detail": "Ranked word overlap with an explicit local synonym map; no trained embedding model is configured."})

        if not scores and summary_request:
            # Even sampling across sessions; explicitly described as excerpts.
            seen = set()
            for row in records:
                if row["meeting_id"] not in seen:
                    scores[row["id"]] = 0.1
                    seen.add(row["meeting_id"])
                if len(seen) >= MAX_EVIDENCE:
                    break
            if len(scores) < MAX_EVIDENCE:
                for row in records:
                    scores.setdefault(row["id"], 0.05)
                    if len(scores) >= MAX_EVIDENCE:
                        break
            steps.append({"stage": "sample", "detail": "Selected a bounded sample for a broad overview; this does not cover every transcript detail."})

        if not scores:
            return self._unsupported(steps)
        ranked = sorted(scores, key=lambda key: scores[key], reverse=True)
        if strategy == "contextual":
            self._expand(records, by_id, ranked, scores, terms, steps)
        selected = self._select(scores, by_id, strategy)
        citations = [self._citation(row, index + 1) for index, row in enumerate(selected)]
        if not citations:
            return self._unsupported(steps)
        steps.append({"stage": "source_check", "detail": f"Matched {len(citations)} selected excerpts to stored segment IDs inside the requested scope. This checks source correspondence, not whether transcript statements are true."})

        answer = self._extractive(citations, summary_request)
        mode = "extractive"
        if settings.get("chat_provider", "extractive") != "extractive":
            try:
                from . import providers
                generated = providers.generate_answer(question, citations, settings, history=history)
                used = self._valid_citations(generated, citations)
                if used:
                    answer = generated.strip()
                    citations = [item for item in citations if item["id"] in used]
                    mode = "generated"
                    steps.append({"stage": "answer", "detail": "Generated a cited answer and checked that every cited ID exists in the selected evidence. Citation checks do not establish factual entailment."})
                else:
                    steps.append({"stage": "fallback", "detail": "The generated response lacked valid paragraph citations; returned source excerpts instead."})
            except Exception:
                # Never relay provider exception text, which may contain keys/URLs.
                steps.append({"stage": "fallback", "detail": "The configured answer provider was unavailable or declined processing. Returned local source excerpts; review provider settings and consent."})
        else:
            steps.append({"stage": "answer", "detail": "Returned exact transcript excerpts with timestamps. Speaker labels are unverified."})
        return {"answer": answer, "citations": citations, "steps": steps, "insufficient_evidence": False, "mode": mode}

    @staticmethod
    def _history_questions(history):
        if not isinstance(history, (list, tuple)):
            return []
        return [item["content"][:2000] for item in history[-10:] if isinstance(item, dict)
                and item.get("role") == "user" and isinstance(item.get("content"), str)]

    @staticmethod
    def _embed(query, records, lexical, scores, settings, steps):
        pool = list({row["id"]: row for row in [*lexical, *records]}.values())[:MAX_EMBEDDING_SEGMENTS]
        try:
            from . import providers
            vectors = providers.embed_texts([query, *(row["text"][:8000] for row in pool)], settings)
            if vectors is None or len(vectors) != len(pool) + 1:
                raise ValueError("Invalid embeddings")
            matches = 0
            for row, vector in zip(pool, vectors[1:]):
                similarity = _cosine(vectors[0], vector)
                # Conservative acceptance for semantic-only candidates; not a probability.
                if similarity >= 0.30:
                    scores[row["id"]] = scores.get(row["id"], 0) + similarity * 0.65
                    matches += 1
            steps.append({"stage": "embedding", "detail": f"Compared {len(pool)} bounded candidate segments with the configured embedding model; {matches} exceeded the similarity threshold."})
        except Exception:
            steps.append({"stage": "fallback", "detail": "The embedding provider was unavailable or declined processing. Continued with local lexical and synonym matching."})

    @staticmethod
    def _expand(records, by_id, ranked, scores, terms, steps):
        grouped = defaultdict(list)
        for row in records:
            grouped[row["meeting_id"]].append(row)
        for rows in grouped.values():
            rows.sort(key=lambda row: (row["start"], row["end"]))
        positions = {row["id"]: (rows, index) for rows in grouped.values() for index, row in enumerate(rows)}
        neighbors = 0
        anchors = ranked[:3]
        for key in anchors:
            rows, position = positions[key]
            for index in (position - 1, position + 1):
                if 0 <= index < len(rows):
                    row = rows[index]
                    gap = max(row["start"] - by_id[key]["end"], by_id[key]["start"] - row["end"], 0)
                    if gap <= 120 and row["id"] not in scores:
                        scores[row["id"]] = scores[key] * 0.28
                        neighbors += 1
        steps.append({"stage": "neighbors", "detail": f"Expanded the top three hits to {neighbors} neighboring segment(s) within two minutes for nearby context."})
        anchor_meetings = {by_id[key]["meeting_id"] for key in anchors}
        anchor_terms = Counter(word for key in anchors for word in _terms(by_id[key]["text"]))
        bridge_terms = [term for term, _ in anchor_terms.most_common(12) if term not in set(terms)]
        added = 0
        related = []
        for row in records:
            if row["meeting_id"] in anchor_meetings or row["id"] in scores:
                continue
            overlap = _overlap(bridge_terms, row["text"])
            shared = len(set(bridge_terms) & set(_terms(row["text"])))
            if shared >= 2 and overlap >= 0.18:
                related.append((overlap, row["id"]))
        for overlap, key in sorted(related, reverse=True)[:3]:
            scores[key] = min(scores[anchors[0]] * 0.35, overlap * 0.5)
            added += 1
        steps.append({"stage": "related", "detail": f"Used shared transcript terms to add {added} related excerpt(s) from other meetings within scope. Expansion stops after these two passes."})

    @staticmethod
    def _select(scores, by_id, strategy):
        # Permit two strong excerpts per session before filling remaining slots.
        ranked = sorted(scores, key=lambda key: scores[key], reverse=True)
        result, deferred, counts = [], [], Counter()
        maximum = MAX_EVIDENCE
        for key in ranked:
            row = by_id.get(key)
            if row is None:
                continue
            if counts[row["meeting_id"]] >= 2:
                deferred.append(row)
                continue
            result.append(row)
            counts[row["meeting_id"]] += 1
            if len(result) >= maximum:
                break
        if len(result) < maximum:
            result.extend(deferred[:maximum - len(result)])
        return result

    @staticmethod
    def _citation(row, index):
        return {"id": f"E{index}", "segment_id": row["id"], "meeting_id": row["meeting_id"],
                "meeting_title": row["meeting_title"], "start": row["start"], "end": row["end"],
                "text": row["text"], "channel": row["channel"], "speaker": row["speaker"],
                "audio_file": row.get("audio_file"), "audio_offset": row.get("audio_offset", 0)}

    @staticmethod
    def _extractive(citations, summary_request=False):
        introduction = ("Here is a sample of relevant transcript excerpts; this is not an exhaustive summary."
                        if summary_request else "The archive contains these relevant transcript excerpts:")
        parts = [introduction]
        for item in citations:
            text = item["text"]
            if len(text) > 1600:
                text = text[:1600].rstrip() + "…"
            parts.append(f'“{text}” [{item["id"]}]')
        return "\n\n".join(parts)

    @staticmethod
    def _valid_citations(answer, citations):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 50000:
            return None
        known = {item["id"] for item in citations}
        used = set(re.findall(r"\[(E\d+)\]", answer))
        suspicious = re.findall(r"\[[^\]\n]*\bE\s*\d+[^\]\n]*\]", answer)
        if not used or not used <= known or any(not re.fullmatch(r"\[E\d+\]", value) for value in suspicious):
            return None
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n(?=\s*[-*]\s)", answer) if part.strip()]
        for paragraph in paragraphs:
            if paragraph.startswith("#") and "\n" not in paragraph and len(paragraph) < 120:
                continue
            if not re.search(r"\[E\d+\]", paragraph):
                return None
        return used

    @staticmethod
    def _unsupported(steps, message=None):
        steps.append({"stage": "answer", "detail": "No supporting transcript evidence was selected; no factual answer was generated."})
        return {"answer": message or "I could not find supporting transcript evidence in the selected meetings. Try a more specific topic or a wider meeting scope.",
                "citations": [], "steps": steps, "insufficient_evidence": True, "mode": "extractive"}

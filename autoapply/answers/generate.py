"""Tier 3: generated prose for free text with no finite answer space ("Why do you want to join us?",
"Why should you be hired for this role?", "Tell us about a project").

  1. Gate. Off until the story pass at /intake is complete: every story-bank question (vault_answer
     rows of category "story") CONFIRMED. Until then the field parks, as before; nothing is
     generated from nothing. `autoapply vault status` says so.
  2. Retrieve. The candidate's confirmed story-bank answers [S1..] and the company brief [B1..]
     (services/company_brief.py, fetched once and cached). Nothing else: no CV facts, no vault
     answers, no job description. The question, the role title and the company name are the only
     other text in the prompt.
  3. Generate with the local model (Ollama, config/answers.yaml `generate`).
  4. Verify, in a SEPARATE call: the model lists every factual claim in the draft with the passage
     id and the exact quote that supports it. Then, deterministically: every claim must cite a real
     passage, its quote must appear in that passage, and every number and capitalised name in the
     draft must appear in the context (or in the question / role / company). Any failure rejects the
     draft: it parks with the reasons. It is NOT retried into something vaguer; a new draft is made
     only when the retrieved context itself changes.
  5. Cache per (canonical_key, company) in generated_answer, so the same essay is never regenerated.
  6. Every draft goes to /review. An attempt carrying an unapproved draft parks, even on a platform
     promoted to live (harness). Generated prose is the one thing that never auto-submits.

SENSITIVE keys never reach this tier: the engine parks them at tier 1 (ParkApplication) before
tier 3 is considered, generate_answer refuses a SENSITIVE key outright, and a prompt that would
contain any stored SENSITIVE value is refused (entail.SensitiveLeak).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from autoapply.candidate.sensitive import is_sensitive_key
from autoapply.logging import get_logger
from autoapply.models.generated_answer import APPROVED, PENDING, REJECTED, GeneratedAnswer

log = get_logger(__name__)

OPEN_QUESTION = re.compile(r"\b(why|describe|tell us|tell me|explain|project|motivat|cover letter|what makes you"
                           r"|interest(ed)? in|hire you|additional information|anything else|about yourself)\b", re.I)
_STOP = {"i", "my", "the", "a", "an", "at", "in", "on", "and", "for", "to", "of", "with", "as", "this", "that",
         "it", "we", "our", "your", "you", "me", "is", "am", "are", "was", "be", "have", "has", "will", "would",
         "can", "also", "which", "what", "why", "how", "when", "where", "while", "during", "through", "from",
         "there", "their", "they", "these", "those", "its", "by", "or", "but", "so", "if", "because", "since",
         "having", "being", "dear", "hello", "hi", "thank", "thanks", "looking", "excited", "eager"}


class Tier3Unavailable(Exception):
    pass


# ── gate ─────────────────────────────────────────────────────────────────────

def story_status(session: Session) -> tuple[int, int]:
    """(confirmed, total) story-bank questions."""
    from autoapply.models.vault import VaultAnswer
    rows = session.query(VaultAnswer).filter(VaultAnswer.category == "story").all()
    return sum(1 for a in rows if a.status == "CONFIRMED" and a.answer), len(rows)


def tier3_status(session: Session) -> tuple[bool, str]:
    done, total = story_status(session)
    if total == 0:
        return False, "tier 3 is disabled: the vault has no story-bank questions (run `autoapply vault seed`)"
    if done < total:
        return False, (f"tier 3 is disabled until the story pass is complete: story bank {done} of {total} "
                       "confirmed (finish it at /intake)")
    return True, f"tier 3 is enabled: story bank {done} of {total} confirmed"


def is_open_question(label: str, field_type: str, options: list[str] | None) -> bool:
    if options or field_type in ("file", "select", "multi_select", "boolean", "radio", "checkbox", "date",
                                 "email", "phone", "number", "url", "location"):
        return False
    return field_type == "textarea" or (len(label) >= 20 and bool(OPEN_QUESTION.search(label)))


# ── retrieval ────────────────────────────────────────────────────────────────

def retrieve(session: Session, job, fetch_brief: bool = True) -> list[dict[str, str]]:
    from autoapply.models.vault import VaultAnswer
    from autoapply.services.company_brief import brief_for, company_of, passages
    ctx = []
    for i, a in enumerate(session.query(VaultAnswer).filter(VaultAnswer.category == "story",
                                                            VaultAnswer.status == "CONFIRMED")
                          .order_by(VaultAnswer.id), 1):
        if a.answer and a.sensitivity != "SENSITIVE":
            ctx.append({"id": f"S{i}", "source": f"story bank: {a.question}", "text": a.answer.strip()})
    brief = brief_for(session, company_of(session, job), fetch=fetch_brief)
    for i, p in enumerate(passages(brief), 1):
        ctx.append({"id": f"B{i}", "source": "company brief", "text": p})
    return ctx


def digest(ctx: list[dict[str, str]]) -> str:
    return hashlib.sha1(json.dumps(ctx, sort_keys=True).encode()).hexdigest()


# ── prompts ──────────────────────────────────────────────────────────────────

def _context_block(ctx: list[dict[str, str]]) -> str:
    return "\n".join(f"[{c['id']}] ({c['source']}) {c['text']}" for c in ctx)


def draft_prompt(question: str, role: str, company: str, ctx: list[dict[str, str]], max_words: int) -> str:
    return (
        f"Write the candidate's answer to a question on an internship application for the role \"{role}\" at "
        f"{company}.\nUse ONLY facts stated in the CONTEXT below: the candidate's own story-bank answers [S..] and "
        "facts about the company [B..]. Do not add any fact, number, name, technology, achievement or company detail "
        "that is not in the CONTEXT. If the CONTEXT is thin, write a shorter answer. First person, plain and "
        f"specific, at most {max_words} words, no greeting or sign-off.\n\n"
        f"CONTEXT:\n{_context_block(ctx)}\n\nQUESTION: {question}\n"
        'Reply as JSON: {"answer": "<the answer>"}')


def verify_prompt(draft: str, ctx: list[dict[str, str]]) -> str:
    return (
        "You check a draft answer against its source passages. List EVERY factual claim the draft makes (about the "
        "candidate, their work, skills, projects, motivations stated as facts, or the company). For each, give the "
        "id of the passage that states it and an exact quote (copied verbatim, a few words) from that passage. If no "
        "passage states it, use null for both.\n\n"
        f"PASSAGES:\n{_context_block(ctx)}\n\nDRAFT:\n{draft}\n\n"
        'Reply as JSON: {"claims": [{"claim": "...", "source": "S1" or null, "quote": "..." or null}]}')


# ── verification (deterministic checks on the model's claim list) ────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def check(draft: str, claims: list[dict[str, Any]] | None, ctx: list[dict[str, str]], given: str) -> list[str]:
    """Reasons to reject the draft; empty = verified."""
    reasons: list[str] = []
    by_id = {c["id"]: _norm(c["text"]) for c in ctx}
    if not isinstance(claims, list) or (draft.strip() and not claims):
        return ["the verification pass returned no claim list"]
    for c in claims:
        claim, src, quote = str(c.get("claim") or "")[:160], c.get("source"), c.get("quote")
        if not src or src not in by_id:
            reasons.append(f"unsupported claim: {claim!r}")
        elif not quote or _norm(quote) not in by_id[src]:
            reasons.append(f"claim {claim!r}: quote not found in [{src}]")
    allowed = _norm(" ".join(c["text"] for c in ctx) + " " + given)
    allowed_words = set(allowed.split())
    for num in set(re.findall(r"\d+(?:[.,]\d+)*%?", draft)):
        if _norm(num) not in allowed:
            reasons.append(f"number {num!r} is not in the retrieved context")
    for sent in re.split(r"(?<=[.!?])\s+", draft):
        words = [w.strip(".,;:!?'\"()") for w in sent.split()]
        for w in words[1:]:                       # a sentence's first word is capitalised anyway
            if w[:1].isupper() and w.lower() not in _STOP and _norm(w) and _norm(w) not in allowed_words \
                    and _norm(w) not in allowed:
                reasons.append(f"name {w!r} is not in the retrieved context")
    return list(dict.fromkeys(reasons))


# ── the local model ──────────────────────────────────────────────────────────

class Generator:
    """Ollama chat with a JSON schema reply. Two separate calls: draft, then verify."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "qwen3:8b", timeout_s: float = 120,
                 transport: httpx.BaseTransport | None = None):
        from autoapply.answers.entail import Entailer
        self.model = model
        self._probe = Entailer(url, model, 5, transport=transport)
        self.url = url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def available(self) -> bool:
        return self._probe.available()

    def _chat(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "think": False, "format": schema,
            "options": {"temperature": 0.2}, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])

    def draft(self, prompt: str) -> str:
        out = self._chat(prompt, {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]})
        return str(out.get("answer") or "").strip()

    def claims(self, prompt: str) -> list[dict[str, Any]]:
        item = {"type": "object", "properties": {"claim": {"type": "string"}, "source": {"type": ["string", "null"]},
                                                 "quote": {"type": ["string", "null"]}}, "required": ["claim", "source", "quote"]}
        out = self._chat(prompt, {"type": "object", "properties": {"claims": {"type": "array", "items": item}},
                                  "required": ["claims"]})
        return out.get("claims")


def default_generator(cfg: dict[str, Any]) -> Generator:
    g, o = cfg.get("generate") or {}, cfg.get("ollama") or {}
    return Generator(o.get("url", "http://localhost:11434"), g.get("model", o.get("model", "qwen3:8b")),
                     float(g.get("timeout_s", 120)))


# ── one answer ───────────────────────────────────────────────────────────────

@dataclass
class Generated:
    value: str | None
    status: str | None = None          # pending | approved | rejected | None (parked before generating)
    generated_id: int | None = None
    park_reason: str | None = None
    reasons: list[str] = field(default_factory=list)


def generate_answer(session: Session, question: str, canonical_key: str, job, generator, cfg: dict[str, Any],
                    fetch_brief: bool = True) -> Generated:
    from autoapply.answers.entail import SensitiveLeak, sensitive_values
    from autoapply.services.dedup import normalize_brand
    if is_sensitive_key(canonical_key):
        raise SensitiveLeak(f"{canonical_key} is SENSITIVE: tier 3 never generates it")
    ok, why = tier3_status(session)
    if not ok:
        return Generated(None, park_reason=why)
    company_key = normalize_brand(job.company) or "?"
    cached = session.query(GeneratedAnswer).filter_by(canonical_key=canonical_key, company_key=company_key).first()
    if cached is not None and cached.status in (APPROVED, PENDING):
        return Generated(cached.draft, cached.status, cached.id)
    ctx = retrieve(session, job, fetch_brief)
    dg = digest(ctx)
    if cached is not None and cached.status == REJECTED and cached.context_digest == dg:
        return Generated(None, REJECTED, cached.id, "tier 3: an earlier draft failed verification and the context "
                         "has not changed since; answer it in /review")
    if not any(c["id"].startswith("S") for c in ctx):
        return Generated(None, park_reason="tier 3: no confirmed story-bank answer to draw from")
    if not generator.available():
        return Generated(None, park_reason="tier 3: the local model is not running (Ollama)")
    max_words = int((cfg.get("generate") or {}).get("max_words", 150))
    prompt = draft_prompt(question, job.title or "", job.company or "", ctx, max_words)
    forbidden = sensitive_values(session)
    leaked = [v for v in forbidden if v.lower() in prompt.lower()]
    if leaked:
        raise SensitiveLeak(f"refusing a tier-3 prompt containing {len(leaked)} SENSITIVE value(s)")
    draft = generator.draft(prompt)
    claims = generator.claims(verify_prompt(draft, ctx)) if draft else []
    reasons = check(draft, claims, ctx, f"{question} {job.title or ''} {job.company or ''}") if draft else ["empty draft"]
    if len(draft.split()) > max_words * 1.3:
        reasons.append(f"draft is {len(draft.split())} words (limit {max_words})")
    row = cached or GeneratedAnswer(canonical_key=canonical_key, company_key=company_key)
    row.question, row.draft, row.context, row.context_digest = question[:2000], draft, ctx, dg
    row.verification = {"claims": claims, "reasons": reasons}
    row.status, row.model = (REJECTED if reasons else PENDING), getattr(generator, "model", None)
    row.created_at = datetime.now(timezone.utc)
    session.add(row)
    session.flush()
    log.info("tier3_draft", key=canonical_key, company=company_key, status=row.status, reasons=len(reasons))
    if reasons:
        return Generated(None, REJECTED, row.id, "tier 3 draft rejected by verification: " + "; ".join(reasons[:3]), reasons)
    return Generated(draft, PENDING, row.id)


def decide(session: Session, generated_id: int, approve: bool, text: str | None = None) -> None:
    """The candidate's decision in /review. An edit replaces the draft with their own text."""
    row = session.get(GeneratedAnswer, generated_id)
    if row is None:
        return
    if text is not None and text.strip() and text.strip() != (row.draft or "").strip():
        row.draft = text.strip()
        row.verification = {**(row.verification or {}), "edited_by_candidate": True}
    row.status = APPROVED if approve and row.draft else row.status
    row.decided_at = datetime.now(timezone.utc)

"""Answer engine, tiers 1 and 2. No generative tier: free text with no finite answer space parks.

TIER 1, deterministic, no model:
  1a  normalise the label (questions.harvest.normalize_label) and look it up in question_alias
      (seeded from every phrasing in docs/QUESTIONS.md and the form_question harvest), then the
      clustering rules (questions.cluster.rule_key) that built that report
  1b  on a miss, nearest neighbour over every known phrasing with a local embedding model
      (fastembed, bge-small). Accepted only at or above similarity_floor (stricter for SENSITIVE
      keys), and only if the label names no other country than the matched key
  1c  resolve the canonical_key against the vault: a CONFIRMED answer is used verbatim; NEEDS
      REVIEW is never used (missing). SENSITIVE keys resolve here and only here; missing -> park.
TIER 2, constrained entailment via Ollama (entail.py): only when tier 1 found no usable answer
  and the field has a finite answer space. The prompt holds the candidate's non-sensitive policy
  rules and the question; the reply must be one of the form's options or the field parks.

Write-back, so the model shrinks out of the loop:
  - a tier-1b match on a non-SENSITIVE key adds the label as a new alias (next time: tier 1a)
  - a question nothing knew becomes a new vault_answer (custom.<slug>, NEEDS REVIEW, never
    auto-confirmed) plus an alias, so intake can ask it
  - a correction made in /review (record_correction) adds the alias and stores the value as a
    suggestion on the question's vault_answer (NEEDS REVIEW)
Every resolution is counted per tier (AnswerEngine.stats).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from autoapply.candidate.sensitive import SENSITIVE, ParkApplication, is_sensitive_key
from autoapply.config import DATA_DIR, PROJECT_ROOT
from autoapply.logging import get_logger
from autoapply.models.answer_alias import QuestionAlias
from autoapply.questions.cluster import countries_in, rule_key
from autoapply.questions.harvest import normalize_label

log = get_logger(__name__)

CONFIG = PROJECT_ROOT / "config" / "answers.yaml"
FINITE_TYPES = {"select", "multi_select", "boolean", "radio", "checkbox"}


@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


@dataclass
class Resolution:
    value: Any = None
    canonical_key: str | None = None
    tier: str | None = None          # 1a | 1a-rule | 1b | 2
    confidence: float = 0.0
    park_reason: str | None = None
    matched: str | None = None       # the alias / phrasing it matched


# ── aliases ──────────────────────────────────────────────────────────────────

def _add_alias(session: Session, label: str, key: str, source: str) -> bool:
    alias = normalize_label(label)
    if not alias or session.query(QuestionAlias.id).filter_by(alias=alias).first():
        return False
    session.add(QuestionAlias(alias=alias, canonical_key=key, source=source, example=label[:1000], hits=0))
    session.flush()
    return True


def seed_aliases(session: Session, report: Path | None = None) -> dict[str, int]:
    """Every surface phrasing in docs/QUESTIONS.md (its table lists the top phrasings of each
    cluster) and every harvested label in form_question with its canonical_key (the full set the
    report was built from)."""
    from autoapply.candidate.catalog import QUESTIONS_MD
    from autoapply.models.form_question import FormQuestion
    added = Counter()
    row = re.compile(r"^\|\s*\d+\s*\|\s*`([^`]+)`[^|]*\|(?:[^|]*\|){5}(.*)\|\s*$")
    for line in (report or QUESTIONS_MD).read_text(encoding="utf-8").splitlines():
        m = row.match(line)
        if m:
            key, phrasings = m.groups()
            for p in phrasings.split(";"):
                p = re.sub(r"\s*\(\d+\)\s*$", "", p.strip())
                if p and not p.startswith("…"):
                    added["report"] += _add_alias(session, p, key, "report")
    for label, key in session.query(FormQuestion.raw_label, FormQuestion.canonical_key).filter(
            FormQuestion.canonical_key.isnot(None)):
        added["harvest"] += _add_alias(session, label, key, "harvest")
    session.commit()
    return dict(added)


# ── tier 1b: embeddings ──────────────────────────────────────────────────────

class EmbeddingIndex:
    """Nearest neighbour over alias phrasings. Vectors are cached on disk by alias set."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._keys: list[str] = []
        self._texts: list[str] = []
        self._vecs = None

    def _embed(self, texts: list[str]):
        import numpy as np
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(self.model_name)
        v = np.array(list(self._model.embed(texts)), dtype="float32")
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def build(self, session: Session) -> None:
        import hashlib

        import numpy as np
        rows = session.query(QuestionAlias.alias, QuestionAlias.canonical_key).order_by(QuestionAlias.id).all()
        self._texts, self._keys = [r[0] for r in rows], [r[1] for r in rows]
        if not rows:
            self._vecs = None
            return
        digest = hashlib.sha1(("\n".join(self._texts) + self.model_name).encode()).hexdigest()[:16]
        cache = DATA_DIR / "cache" / "answers" / f"aliases_{digest}.npy"
        if cache.exists():
            self._vecs = np.load(cache)
        else:
            self._vecs = self._embed(self._texts)
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.save(cache, self._vecs)

    def nearest(self, label: str) -> tuple[str, str, float] | None:
        if self._vecs is None or not len(self._texts):
            return None
        q = self._embed([normalize_label(label)])[0]
        sims = self._vecs @ q
        i = int(sims.argmax())
        return self._keys[i], self._texts[i], float(sims[i])


# ── option matching (tier 1c values against the form's options) ─────────────

def match_option(value: str, options: list[str]) -> str | None:
    """Map a stored answer onto one of the form's options, deterministically, or None."""
    v = str(value).strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    for o in options:
        ol = o.strip().lower()
        if v in ("yes", "no") and (ol == v or ol.startswith(v + ",") or ol.startswith(v + " ")):
            return o
    hits = [o for o in options if v and (v in o.lower() or o.lower() in v)]
    return hits[0] if len(hits) == 1 else None


# ── the engine ───────────────────────────────────────────────────────────────

class AnswerEngine:
    def __init__(self, session: Session, cfg: dict[str, Any] | None = None, entailer=None, index=None,
                 writeback: bool = True):
        self.session = session
        self.cfg = load_config() if cfg is None else cfg
        self.writeback = writeback
        self.stats: Counter = Counter()
        self._index = index
        self._entailer = entailer
        if not session.query(QuestionAlias.id).first():
            seed_aliases(session)

    # tier 1a / 1b: which canonical question is this?
    def identify(self, label: str) -> Resolution:
        norm = normalize_label(label)
        row = self.session.query(QuestionAlias).filter_by(alias=norm).first()
        if row is not None:
            row.hits += 1
            row.last_used_at = datetime.now(timezone.utc)
            return Resolution(canonical_key=row.canonical_key, tier="1a", confidence=1.0, matched=row.alias)
        hit = rule_key(norm)
        if hit:
            return Resolution(canonical_key=hit[0], tier="1a-rule", confidence=0.95, matched=norm)
        if self._index is None:
            self._index = EmbeddingIndex(self.cfg.get("embedding_model", "BAAI/bge-small-en-v1.5"))
            self._index.build(self.session)
        near = self._index.nearest(label)
        if near:
            key, text, sim = near
            floor = float(self.cfg.get("sensitive_similarity_floor" if is_sensitive_key(key) else "similarity_floor",
                                       0.93 if is_sensitive_key(key) else 0.86))
            asked, matched = countries_in(norm), countries_in(text) | ({key.split(".")[1]} if "." in key and
                                                                          not key.startswith("custom.") else set())
            if sim >= floor and not (asked and asked != matched):
                return Resolution(canonical_key=key, tier="1b", confidence=round(sim, 3), matched=text)
        return Resolution(confidence=round(near[2], 3) if near else 0.0, matched=near[1] if near else None)

    # tier 1c: the candidate's own answer
    def vault_value(self, key: str) -> tuple[str | None, bool]:
        """(confirmed answer or None, is_sensitive). NEEDS REVIEW never counts."""
        from autoapply.models.vault import VaultAnswer
        keys = [key] + ([key.split(".")[0]] if "." in key and not key.startswith("custom.") else [])
        sensitive = is_sensitive_key(key)
        for k in keys:
            a = self.session.query(VaultAnswer).filter_by(canonical_key=k).first()
            if a is None:
                continue
            sensitive = sensitive or a.sensitivity == SENSITIVE
            if a.answer and a.status == "CONFIRMED" and (not sensitive or a.source == "USER ENTERED"):
                return a.answer, sensitive
        return None, sensitive

    def resolve(self, label: str, field_type: str = "text", options: list[str] | None = None,
                required: bool = False) -> Resolution:
        options = [o for o in options or [] if o and o.strip() and not re.match(r"^(select|choose)\b", o, re.I)]
        ident = self.identify(label)
        if ident.canonical_key:
            value, sensitive = self.vault_value(ident.canonical_key)
            if value is not None:
                if options:
                    mapped = match_option(value, options)
                    if mapped is None:
                        return self._count(Resolution(canonical_key=ident.canonical_key, tier=ident.tier,
                                                      confidence=ident.confidence,
                                                      park_reason="stored answer matches none of the form's options"))
                    value = mapped
                self._learn(label, ident)
                return self._count(Resolution(value, ident.canonical_key, ident.tier, ident.confidence,
                                              matched=ident.matched))
            if sensitive:
                # SENSITIVE resolves at tier 1 or not at all: never sent to a model.
                raise ParkApplication(f"{ident.canonical_key}: no confirmed answer from the candidate")
        finite = options or (field_type in ("boolean", "checkbox") and ["Yes", "No"])
        if finite:
            r = self._tier2(label, list(finite), ident)
            if r is not None:
                return self._count(r)
        if ident.canonical_key is None:
            self._new_question(label, field_type)
        reason = ("no confirmed answer in the vault" if ident.canonical_key
                  else "free text: parks in this phase (no generative tier)" if not finite
                  else "unknown question; tier 2 could not decide it")
        return self._count(Resolution(canonical_key=ident.canonical_key, tier=ident.tier, confidence=ident.confidence,
                                      park_reason=reason, matched=ident.matched))

    def _tier2(self, label: str, options: list[str], ident: Resolution) -> Resolution | None:
        from autoapply.answers.entail import Entailer, SensitiveLeak, build_prompt, prompt_rules, sensitive_values
        if self._entailer is None:
            o = self.cfg.get("ollama") or {}
            self._entailer = Entailer(o.get("url", "http://localhost:11434"), o.get("model", "qwen3:8b"),
                                      float(o.get("timeout_s", 60)))
        if not self._entailer.available():
            return None
        rules = prompt_rules(self.session)
        if not rules:
            return None
        try:
            prompt = build_prompt(label, options, rules, sensitive_values(self.session))
            answer = self._entailer.ask(prompt, options)
        except SensitiveLeak as e:
            log.error("tier2_refused", reason=str(e))
            return None
        except Exception as e:
            log.warning("tier2_failed", error=str(e)[:200])
            return None
        if answer is None:
            return None
        if ident.canonical_key is None:
            self._new_question(label, "select", suggestion=answer)
        return Resolution(answer, ident.canonical_key, "2", 0.7, matched="policy rules")

    # write-back
    def _learn(self, label: str, ident: Resolution) -> None:
        if self.writeback and ident.tier in ("1b", "1a-rule") and not is_sensitive_key(ident.canonical_key):
            if _add_alias(self.session, label, ident.canonical_key, "embedding" if ident.tier == "1b" else "rule"):
                self.stats["writeback_alias"] += 1

    def _new_question(self, label: str, field_type: str, suggestion: str | None = None) -> str | None:
        if not self.writeback:
            return None
        from autoapply.models.vault import VaultAnswer
        norm = normalize_label(label)
        key = "custom." + "_".join(norm.split()[:8])[:100]
        if not norm or self.session.query(VaultAnswer.id).filter_by(canonical_key=key).first():
            return key
        self.session.add(VaultAnswer(canonical_key=key, question=label[:1000], category="custom", field_type=field_type,
                                     source="FORM", status="NEEDS REVIEW", suggested_answer=suggestion,
                                     intake_pass="story" if field_type == "textarea" else "gap"))
        _add_alias(self.session, label, key, "new")
        self.stats["writeback_new_question"] += 1
        return key

    def _count(self, r: Resolution) -> Resolution:
        self.stats["parked" if r.value is None else f"tier {r.tier}"] += 1
        return r

    def commit(self) -> None:
        self.session.commit()


def record_correction(session: Session, label: str, value: Any, platform: str | None = None) -> str | None:
    """A correction made in /review: learn the label's alias and keep the value as a suggestion
    (NEEDS REVIEW) on its vault_answer. Never confirms anything."""
    from autoapply.models.vault import VaultAnswer
    eng = AnswerEngine(session, writeback=True)
    ident = eng.identify(label)
    key = ident.canonical_key or eng._new_question(label, "text")
    if key:
        _add_alias(session, label, key, "review")
        a = session.query(VaultAnswer).filter_by(canonical_key=key).first()
        if a is not None and a.status != "CONFIRMED":
            a.suggested_answer = str(value)
    session.commit()
    return key


def harness_answerer(session: Session | None = None):
    """An Answerer for autoapply.appliers.harness (one engine per run; stats accumulate)."""
    from autoapply.appliers.harness import Answer
    state: dict[str, Any] = {}

    def answer(field, job) -> Answer:
        if "engine" not in state:
            from autoapply.config import get_settings
            from autoapply.models.base import engine_from_settings, get_session_factory
            sess = session or get_session_factory(engine_from_settings(get_settings().db.url))()
            state["engine"] = AnswerEngine(sess)
        eng = state["engine"]
        r = eng.resolve(field.label, field.field_type, field.options, field.required)
        eng.commit()
        return Answer(r.value, r.canonical_key, r.tier, r.confidence, r.park_reason)

    answer.state = state
    return answer


def explain(session: Session, fields: list[tuple[str, str, list[str] | None]]) -> list[dict[str, Any]]:
    """Per field: tier, canonical_key, confidence, value. Fills nothing and writes nothing back."""
    eng = AnswerEngine(session, writeback=False)
    out = []
    for label, ftype, options in fields:
        try:
            r = eng.resolve(label, ftype, options)
            out.append({"label": label, "tier": r.tier if r.value is not None else "parked", "key": r.canonical_key,
                        "confidence": r.confidence, "value": r.value, "reason": r.park_reason, "matched": r.matched})
        except ParkApplication as e:
            ident = eng.identify(label)
            out.append({"label": label, "tier": "parked", "key": ident.canonical_key, "confidence": ident.confidence,
                        "value": None, "reason": f"SENSITIVE: {e}", "matched": ident.matched})
    session.rollback()   # nothing an explanation touched (alias hit counters) is kept
    return out

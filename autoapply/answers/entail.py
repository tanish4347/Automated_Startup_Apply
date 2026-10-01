"""Tier 2: constrained entailment with a local model (Ollama).

Only for questions tier 1 could not resolve AND whose answer space is finite: yes/no, or a
select/radio with options scraped from the form. The prompt carries the candidate's structured
policy rules (vault_policy: where on-site work is possible, remote, relocation) and the question,
nothing else: no vault answers, no CV facts, and never a SENSITIVE value. build_prompt() refuses
to produce a prompt containing any SENSITIVE value (sensitive_values()), whatever the cause.

The model must answer with exactly one of the form's options (Ollama's JSON-schema `format` with
an enum); anything not in the option list is discarded and the field parks.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from autoapply.logging import get_logger

log = get_logger(__name__)

# Policy rules the model may see. The others (availability window, notice period, stipend floor)
# mirror SENSITIVE answers and never reach a prompt.
PROMPT_POLICY_KEYS = ("can_work_onsite_in", "remote_ok", "will_relocate")
YES_NO = ["Yes", "No"]


class SensitiveLeak(Exception):
    """A prompt was about to contain a SENSITIVE value. Never sent."""


def sensitive_values(session: Session) -> set[str]:
    """Every SENSITIVE value the candidate has stored (answers, suggestions, sensitive policy rows,
    education grades and graduation dates). None of these may appear in a prompt."""
    from autoapply.models.vault import VaultAnswer, VaultEducation, VaultPolicy
    vals: set[str] = set()
    for a in session.query(VaultAnswer).filter(VaultAnswer.sensitivity == "SENSITIVE"):
        vals.update(v for v in (a.answer, a.suggested_answer) if v)
    for p in session.query(VaultPolicy).filter(~VaultPolicy.key.in_(PROMPT_POLICY_KEYS)):
        vals.update(str(v) for v in (p.value or {}).values() if v not in (None, "", [], {}))
    for e in session.query(VaultEducation):
        vals.update(v for v in (e.cgpa, e.end_date) if v)
    # Short values ("No", "0") would match ordinary words; the leak check is for real values.
    return {v.strip() for v in vals if len(v.strip()) >= 3}


def prompt_rules(session: Session) -> dict[str, Any]:
    from autoapply.models.vault import VaultPolicy
    return {p.key: p.value for p in session.query(VaultPolicy).filter(VaultPolicy.key.in_(PROMPT_POLICY_KEYS))
            if p.status == "CONFIRMED"}


def build_prompt(question: str, options: list[str], rules: dict[str, Any], forbidden: set[str]) -> str:
    prompt = (
        "You answer one job-application question for a candidate, using only the candidate's rules below.\n"
        "Pick exactly one of the options. If the rules do not decide the question, pick nothing: answer "
        '{"answer": null}.\n\n'
        f"Candidate rules (JSON): {json.dumps(rules, sort_keys=True)}\n"
        f"Question: {question}\n"
        f"Options: {json.dumps(options)}\n"
        'Answer as JSON: {"answer": <one option, verbatim, or null>}'
    )
    low = prompt.lower()
    leaked = [v for v in forbidden if v.lower() in low]
    if leaked:
        raise SensitiveLeak(f"refusing to build a prompt containing {len(leaked)} SENSITIVE value(s)")
    return prompt


class Entailer:
    def __init__(self, url: str = "http://localhost:11434", model: str = "qwen3:8b", timeout_s: float = 60,
                 transport: httpx.BaseTransport | None = None):
        self.url, self.model = url.rstrip("/"), model
        self._client = httpx.Client(timeout=timeout_s, transport=transport)
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                r = self._client.get(f"{self.url}/api/tags", timeout=3)
                names = {m.get("name") for m in r.json().get("models", [])} if r.status_code == 200 else set()
                self._available = r.status_code == 200 and (self.model in names or f"{self.model}:latest" in names)
                if r.status_code == 200 and not self._available:
                    log.warning("tier2_skipped", reason=f"Ollama is running but model {self.model} is not pulled "
                                                       f"(run: ollama pull {self.model})")
            except Exception:
                self._available = False
                log.warning("tier2_skipped", reason=f"Ollama is not running at {self.url}; tier 2 is off for this run")
        return self._available

    def ask(self, prompt: str, options: list[str]) -> str | None:
        """One option from `options`, verbatim, or None. Anything else is discarded."""
        schema = {"type": "object", "properties": {"answer": {"type": ["string", "null"], "enum": options + [None]}},
                  "required": ["answer"]}
        r = self._client.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "format": schema, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        try:
            answer = json.loads(r.json()["message"]["content"]).get("answer")
        except (ValueError, KeyError, AttributeError):
            return None
        return answer if answer in options else None

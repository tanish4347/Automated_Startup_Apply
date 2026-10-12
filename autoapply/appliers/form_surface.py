"""Bounded, framework-neutral discovery of interactive form surfaces.

Discovery uses Playwright locators (which pierce open shadow roots) in every attached
frame, then supplements them with compact ARIA snapshots and a read-only CDP inspection
for nodes Playwright cannot target. Closed roots are reported as unsupported; this module
never guesses a way to mutate them.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class FormTarget:
    frame_index: int = 0
    frame_chain: list[dict[str, str]] = field(default_factory=list)
    shadow_chain: list[str] = field(default_factory=list)
    strategies: list[dict[str, Any]] = field(default_factory=list)
    discovery_source: str = "playwright"
    unsupported_reason: str | None = None


@dataclass
class FormField:
    field_id: str
    label: str
    accessible_name: str
    role: str
    field_type: str
    native_type: str
    required: bool
    disabled: bool
    visible: bool
    current_value: Any
    options: list[str]
    target: FormTarget
    confidence: float = 0.0
    discovery_source: str = "playwright"
    control: str | None = None
    option_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FormSurface:
    fields: list[FormField]
    status: str
    diagnostics: list[str] = field(default_factory=list)
    frame_count: int = 0
    elapsed_ms: int = 0
    accessibility: list[str] = field(default_factory=list)


_SELECTORS = (
    "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=password]),"
    "textarea,select,[contenteditable=true],[role=textbox],[role=combobox],"
    "[role=checkbox],[role=radio],[role=switch]"
)

_EXTRACT_JS = r"""(el, id) => {
  const txt = n => (n && (n.innerText || n.textContent) || '').replace(/\s+/g, ' ').trim();
  const root = el.getRootNode();
  const attr = n => n && (n.getAttribute('aria-label') || txt(n));
  const labelled = el.labels ? [...el.labels].map(txt).filter(Boolean).join(' ') : '';
  const by = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
    .map(x => root.getElementById ? root.getElementById(x) : document.getElementById(x)).map(txt).filter(Boolean).join(' ');
  const parentLabel = txt(el.closest('label'));
  const legend = txt(el.closest('fieldset')?.querySelector('legend'));
  const container = el.closest('.ashby-application-form-field-entry, [class*="field-entry"], [class*="form-group"], [class*="form-field"], [role="group"]');
  const containerLabel = container ? txt(container.querySelector('label, [class*="label"], [class*="title"], [class*="question"]')) : '';
  let label = labelled || by || el.getAttribute('aria-label') || parentLabel || legend || containerLabel ||
    el.getAttribute('placeholder') || el.getAttribute('name') || '';
  const role = el.getAttribute('role') || ({INPUT: ({checkbox:'checkbox',radio:'radio',file:'button'}[(el.type||'').toLowerCase()] || 'textbox'),
    TEXTAREA:'textbox',SELECT:el.multiple?'listbox':'combobox'}[el.tagName]) || (el.isContentEditable?'textbox':'');
  const native = (el.getAttribute('type') || el.tagName.toLowerCase()).toLowerCase();
  const type = native === 'select' ? (el.multiple ? 'multi_select' : 'select') : role === 'combobox' ? 'combobox' :
    role === 'listbox' ? 'select' : role === 'checkbox' || role === 'switch' ? 'checkbox' : role === 'radio' ? 'radio' :
    native === 'textarea' || el.isContentEditable ? 'textarea' :
    ({email:'email',tel:'phone',number:'number',date:'date',file:'file',checkbox:'checkbox',radio:'radio'}[native] || 'text');
  let value = el.isContentEditable ? txt(el) : (role === 'checkbox' || role === 'radio' || role === 'switch' ?
    (el.getAttribute('aria-checked') === 'true' || !!el.checked) : (el.getAttribute('aria-valuetext') || el.value || ''));
  let options = [];
  if (el.tagName === 'SELECT') options = [...el.options].filter(o=>o.value && !o.disabled).map(o=>txt(o));
  if (!options.length && (role === 'combobox' || role === 'listbox') && root.querySelectorAll) options = [...root.querySelectorAll('[role=option]')]
    .filter(o=>{const r=o.getBoundingClientRect(),s=getComputedStyle(o);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'})
    .slice(0,100).map(txt).filter(Boolean);
  const group = (native === 'radio' || native === 'checkbox') && el.name ?
    [...root.querySelectorAll('input[name="'+CSS.escape(el.name)+'"]')].filter(x=>x.type===el.type) : [el];
  if (group.length > 1) {
    if (legend) label = legend;
    else if (containerLabel) label = containerLabel;
    options = group.map(x => txt(x.closest('label')) || x.getAttribute('aria-label') || x.value).filter(Boolean);
    value = group.filter(x=>x.checked).map(x=>x.value);
  }
  const visible = (()=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'})();
  const shadows=[]; let n=el.getRootNode(); while(n && n.host){shadows.unshift(n.host.tagName.toLowerCase()+(n.host.id?'#'+n.host.id:'')); n=n.host.getRootNode()}
  return {label, role, field_type:group.length>1?(native==='radio'?'select':'multi_select'):type, native_type:native,
    required:!!(el.required || el.getAttribute('aria-required')==='true' || /\*\s*$/.test(label)), disabled:!!(el.disabled || el.getAttribute('aria-disabled')==='true'),
    visible, value, options, control:group.length>1?native:(el.tagName==='SELECT'?'select':el.getAttribute('role')==='combobox'?'combobox':native),
    option_ids:group.length>1?group.map(x=>x.getAttribute('data-aa')):[], group_name:group.length>1?el.name:'', shadow_chain:shadows,
    placeholder:el.getAttribute('placeholder'), name:el.getAttribute('name'), id:el.id};
}"""


def _frames(page, max_depth: int) -> tuple[list[Any], list[dict[str, str]]]:
    frames = list(getattr(page, "frames", []) or [])
    main = getattr(page, "main_frame", None)
    if not frames and main is not None:
        frames = [main]
    if not frames:
        frames = [page]  # small test doubles and frame-like page adapters
    kept, chains = [], []
    for i, frame in enumerate(frames):
        chain, cur, depth = [], frame, 0
        while cur is not None and depth < max_depth:
            chain.append({"name": str(getattr(cur, "name", "") or ""), "url": str(getattr(cur, "url", "") or "")[:300]})
            cur = getattr(cur, "parent_frame", None)
            depth += 1
        if cur is not None:
            continue
        kept.append(frame)
        chains.append(list(reversed(chain)))
    return kept, chains


def wait_for_form(page, *, timeout_ms: int = 8000, poll_ms: int = 150, stable_polls: int = 2,
                  max_frame_depth: int = 8) -> None:
    """Wait for a stable set of meaningful controls, within a strict timeout."""
    start, prior, stable = time.monotonic(), None, 0
    while (time.monotonic() - start) * 1000 < timeout_ms:
        frames, _ = _frames(page, max_frame_depth)
        signature = []
        for frame in frames:
            try:
                locator = frame.locator(_SELECTORS)
                signature.append(str(locator.count()))
            except Exception:
                signature.append("?")
        current = ":".join(signature)
        if current != ":".join("0" for _ in frames) and current == prior:
            stable += 1
            if stable >= stable_polls:
                return
        else:
            stable = 0
        prior = current
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            time.sleep(poll_ms / 1000)


class FormDiscovery:
    def __init__(self, *, timeout_ms: int = 8000, max_frame_depth: int = 8, use_cdp: bool = True):
        self.timeout_ms = timeout_ms
        self.max_frame_depth = max_frame_depth
        self.use_cdp = use_cdp

    def discover(self, page) -> FormSurface:
        started = time.monotonic()
        wait_for_form(page, timeout_ms=self.timeout_ms, max_frame_depth=self.max_frame_depth)
        frames, chains = _frames(page, self.max_frame_depth)
        fields: list[FormField] = []
        seen_groups: set[tuple[int, str, str]] = set()
        accessibility: list[str] = []
        diagnostics: list[str] = []
        for frame_index, (frame, chain) in enumerate(zip(frames, chains)):
            try:
                snap = frame.locator("body").aria_snapshot()
                if snap:
                    accessibility.extend(line.strip()[:240] for line in snap.splitlines()
                                          if re.search(r"\b(textbox|combobox|listbox|option|checkbox|radio|button|switch)\b", line, re.I))
            except Exception:
                pass
            try:
                locator = frame.locator(_SELECTORS)
                count = min(locator.count(), 500)
            except Exception as exc:
                diagnostics.append(f"frame {frame_index}: Playwright discovery failed: {type(exc).__name__}")
                continue
            for pos in range(count):
                item = locator.nth(pos)
                try:
                    item.evaluate("(el,id)=>el.setAttribute('data-aa',id)", f"f{frame_index}-{pos}")
                    raw = item.evaluate(_EXTRACT_JS, f"f{frame_index}-{pos}")
                except Exception:
                    continue
                if not raw.get("visible") and raw.get("native_type") != "file":
                    continue
                if raw.get("group_name"):
                    group_key = (frame_index, raw["group_name"], raw.get("native_type", ""))
                    if group_key in seen_groups:
                        continue
                    seen_groups.add(group_key)
                label = (raw.get("label") or "").strip()
                if not label:
                    # Maintain an explicit target for unlabeled controls but lower confidence.
                    label = raw.get("name") or raw.get("id") or ""
                if not label:
                    continue
                field_id = hashlib.sha1(f"{frame_index}:{pos}:{label}".encode()).hexdigest()[:12]
                aa = f"f{frame_index}-{pos}"
                strategies = [
                    {"kind": "role", "value": raw.get("role"), "name": label},
                    {"kind": "label", "value": label},
                    {"kind": "placeholder", "value": raw.get("placeholder")},
                    {"kind": "native", "name": raw.get("name"), "id": raw.get("id"), "data_aa": aa},
                ]
                fields.append(FormField(field_id, label, label, raw.get("role") or "", raw.get("field_type") or "text",
                    raw.get("native_type") or "", bool(raw.get("required")), bool(raw.get("disabled")), True,
                    raw.get("value"), raw.get("options") or [], FormTarget(frame_index, chain, raw.get("shadow_chain") or [],
                    strategies, "playwright"), 0.9 if raw.get("label") else 0.55, "playwright",
                    raw.get("control"), raw.get("option_ids") or []))

        # Add compact ARIA-only controls (e.g. custom div-based widgets) as targetable semantic refs.
        # These entries are actionable only when a role/name locator resolves uniquely.
        known = {(f.role, f.accessible_name.lower()) for f in fields}
        role_map = {"textbox":"text", "combobox":"combobox", "listbox":"select", "checkbox":"checkbox",
                    "radio":"select", "switch":"checkbox"}
        for line in accessibility:
            m = re.match(r"-\s+(textbox|combobox|listbox|checkbox|radio|button|switch)(?:\s+\[.*?\])?(?:\s+\"([^\"]*)\")?", line, re.I)
            if not m:
                continue
            role, name = m.group(1).lower(), (m.group(2) or "").strip()
            if not name or (role, name.lower()) in known or role not in role_map:
                continue
            idx = next((i for i, c in enumerate(chains) if i < len(frames)), 0)
            field_id = hashlib.sha1(f"aria:{idx}:{role}:{name}".encode()).hexdigest()[:12]
            fields.append(FormField(field_id, name, name, role, role_map[role], "", False, False, True, None, [],
                FormTarget(idx, chains[idx] if chains else [], [], [{"kind":"role","value":role,"name":name}], "aria"),
                0.55, "aria", role))
            known.add((role, name.lower()))

        if self.use_cdp:
            closed = self._closed_root_diagnostics(page)
            diagnostics.extend(closed)
        status = ("partial" if fields and diagnostics else "ready") if fields else (
            "unsupported" if any("closed shadow" in d.lower() for d in diagnostics) else "empty")
        return FormSurface(fields, status, diagnostics, len(frames), int((time.monotonic()-started)*1000), accessibility[:100])

    def _closed_root_diagnostics(self, page) -> list[str]:
        hits: list[str] = []
        frames, _ = _frames(page, self.max_frame_depth)
        for frame_index, frame in enumerate(frames):
          try:
            target = page if frame is getattr(page, "main_frame", None) else frame
            session = page.context.new_cdp_session(target)
            doc = session.send("DOM.getDocument", {"depth": 32, "pierce": True})
            visited = 0
            def walk(node, depth=0):
                nonlocal visited
                visited += 1
                if depth > 32 or visited > 10000:
                    return
                for root in node.get("shadowRoots", []) or []:
                    mode = root.get("shadowRootType", "unknown")
                    if mode == "closed":
                        for child in root.get("children", []) or []:
                            if child.get("nodeName") in ("INPUT", "TEXTAREA", "SELECT"):
                                attrs = dict(zip(child.get("attributes", [])[::2], child.get("attributes", [])[1::2]))
                                hits.append(f"frame {frame_index}: closed shadow control {child.get('nodeName')} "
                                            f"name={attrs.get('name','')!r}; discovery only")
                    walk(root, depth + 1)
                for child in node.get("children", []) or []:
                    walk(child, depth + 1)
            walk(doc.get("root", {}))
            try: session.detach()
            except Exception: pass
          except Exception:
            continue
        return hits[:20]


def target_locator(page, field: FormField):
    target = getattr(field, "target", field)
    frames, _ = _frames(page, 8)
    if target.frame_index >= len(frames):
        raise LookupError("target frame no longer exists; rediscovery required")
    frame = frames[target.frame_index]
    for strategy in target.strategies:
        kind, value = strategy.get("kind"), strategy.get("value")
        try:
            if kind == "role" and value:
                loc = frame.get_by_role(value, name=strategy.get("name"), exact=True)
            elif kind == "label" and value:
                loc = frame.get_by_label(value, exact=True)
            elif kind == "placeholder" and value:
                loc = frame.get_by_placeholder(value, exact=True)
            elif kind == "native" and strategy.get("data_aa"):
                loc = frame.locator(f'[data-aa="{strategy["data_aa"]}"]')
            elif kind == "native" and strategy.get("id"):
                loc = frame.locator(f'[id={json.dumps(strategy["id"])}]')
            elif kind == "native" and strategy.get("name"):
                loc = frame.locator(f'[name={json.dumps(strategy["name"])}]')
            else:
                continue
            if loc.count() == 1:
                return loc
        except Exception:
            continue
    raise LookupError(f"could not uniquely retarget {field.label!r}; rediscovery required")

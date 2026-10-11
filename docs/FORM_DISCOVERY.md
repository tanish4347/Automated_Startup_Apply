# Modern form discovery

Updated 2026-10-08. This records the implemented browser layer and its tested boundaries.

## Pipeline

`FormApplier.read_fields()` now asks `FormDiscovery` to wait for a stable set of controls for up
to eight seconds, then inspects each attached Playwright frame. It collects native controls with
Playwright locators, which naturally cross open Shadow DOM, and captures only control-related lines
from each frame's ARIA snapshot. The normalizer produces `FormSurface` and `FormField` values with
accessible name, label, role, type, required/disabled/visible state, current value, options, frame
and shadow chains, locator strategies, confidence, and source.

The answerer receives each normalized field; it does not receive page HTML or an entire ARIA tree.
The step's answers are resolved before interaction. A field is added to `filled_fields` only after
the interaction engine reads its value or selected state back successfully. Failed or unsupported
interactions become `USER_INPUT_REQUIRED`. Choice changes cause bounded re-discovery so conditional
fields are added and old locators are refreshed. A Next step is discovered again from scratch.

Targeting reacquires a unique role/name locator first, then label, placeholder, and generated stable
native attribute. Frame selection is explicit. Open shadow roots use ordinary Playwright locators.
CDP `DOM.getDocument` with `pierce` is a discovery-only fallback for difficult closed roots; the
walker is bounded by 32 DOM levels, 10,000 nodes, and the configured frame depth. A closed-root
control is never reported as fillable.

The old zero-field path came from `FormApplier.read_fields()` evaluating `FORM_DUMP_JS` once in the
top page document and treating that dump as the complete form. That misses controls that mount
after the fixed delay, live in an iframe, are exposed through semantic ARIA roles rather than
native tags, or sit behind inaccessible/closed roots. The new generic layer addresses those
architectural gaps. The exact cause on the previously observed SmartRecruiters session was not
reproduced in this environment: DNS lookup for `jobs.smartrecruiters.com` failed during the live
review-only acceptance attempt.

## Interaction and safety

- Text, textarea/contenteditable, email, phone, number, date, file, native select, checkbox, radio,
  multi-select, and role-based combobox controls use Playwright interactions.
- A custom combobox is opened, visible `option` elements are inspected, and only an offered option
  can be selected. An unmatched or ambiguous answer fails for review; the first option is never
  guessed.
- Text values, selected options, checked state, and uploaded file name are verified after fill.
- `review_only` remains the default. The BrowserSession request guard blocks non-GET traffic, and
  no application submit control is clicked in review-only mode. CAPTCHA/DataDome remains blocked.

## Tested boundaries

Local Chromium fixtures cover basic HTML, radio groups, Material UI-style role widgets, dynamic
late and conditional controls, nested open Shadow DOM, Web Components, closed-root CDP discovery,
iframe plus Shadow DOM combinations, native controls, contenteditable, custom comboboxes, and
multi-step re-discovery. These fixtures demonstrate the browser layer, not every React component
library or production ATS.

Remaining unsupported surfaces include closed-root interaction, controls with no usable label or
accessible role, frames that browser security does not expose to Playwright, canvas-only forms,
native OS dialogs, and any external challenge. The SmartRecruiters production-form acceptance test
could not reach the host because the execution environment returned `ERR_NAME_NOT_RESOLVED`; this is
an environment/network failure, not evidence that the form was blocked by DataDome.

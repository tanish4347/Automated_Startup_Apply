"""Local Chromium fixtures for modern form discovery and verified interaction."""

import pytest

from autoapply.appliers.form_surface import FormDiscovery, FormTarget, target_locator
from autoapply.appliers.harness import Answer, FormApplier, FormField, _fill_page, _validation_message, run_attempt
from autoapply.appliers.ashby import AshbyApplier
from autoapply.models.base import Base
from autoapply.models.job import Job
from autoapply.models.application import Application, ApplicationStatus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="module")
def page():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()
    browser.close()
    pw.stop()


class _Applier(FormApplier):
    def open_form(self, bs, job): pass
    def submit_control(self, bs): return bs.page.get_by_role("button", name="Submit")
    def success_assertion(self, bs, before_url): return None


def test_discovers_basic_custom_and_contenteditable_controls(page):
    page.set_content("""<form>
      <label for='n'>Full name</label><input id='n' required>
      <label for='e'>Email</label><input id='e' type='email'>
      <label for='tel'>Phone</label><input id='tel' type='tel'>
      <label for='year'>Graduation year</label><input id='year' type='number'>
      <label for='start'>Start date</label><input id='start' type='date'>
      <label for='s'>Country</label><select id='s'><option value=''>Choose</option><option>India</option></select>
      <fieldset><legend>Work mode</legend><label><input type='radio' name='mode' value='remote'>Remote</label>
      <label><input type='radio' name='mode' value='onsite'>On-site</label></fieldset>
      <label><input type='checkbox' name='agree'>Consent</label>
      <div role='textbox' aria-label='Additional details' contenteditable='true'></div>
    </form>""")
    surface = FormDiscovery(timeout_ms=1500, use_cdp=False).discover(page)
    by_label = {f.label: f for f in surface.fields}
    assert {"Full name", "Email", "Phone", "Graduation year", "Start date", "Country", "Work mode",
            "Consent", "Additional details"} <= set(by_label)
    assert by_label["Full name"].required
    assert by_label["Work mode"].options == ["Remote", "On-site"]
    assert by_label["Country"].options == ["India"]
    assert by_label["Phone"].field_type == "phone"
    assert by_label["Graduation year"].field_type == "number"
    assert by_label["Start date"].field_type == "date"
    assert by_label["Additional details"].field_type == "textarea"
    assert by_label["Work mode"].target.frame_chain


def test_open_nested_shadow_dom_and_web_component(page):
    page.set_content('''<x-profile></x-profile><script>
      class XInner extends HTMLElement { connectedCallback(){
        this.attachShadow({mode:'open'}).innerHTML='<label for="city">City</label><input id="city" name="city">';
      }}
      class XProfile extends HTMLElement { connectedCallback(){
        const r=this.attachShadow({mode:'open'}); r.innerHTML='<x-inner></x-inner>';
      }}
      customElements.define('x-inner',XInner); customElements.define('x-profile',XProfile);
    </script>''')
    surface = FormDiscovery(timeout_ms=1500, use_cdp=False).discover(page)
    city = next(f for f in surface.fields if f.label == "City")
    assert city.target.shadow_chain == ["x-profile", "x-inner"]
    assert city.discovery_source == "playwright"


def test_dynamic_late_field_is_found_after_bounded_polling(page):
    page.set_content('''<main id="root"></main><script>setTimeout(()=>root.innerHTML='<label for="late">Late field</label><input id="late">',250)</script>''')
    surface = FormDiscovery(timeout_ms=1800, use_cdp=False).discover(page)
    assert any(f.label == "Late field" for f in surface.fields)
    assert surface.elapsed_ms < 1800


def test_mui_style_custom_combobox_and_exact_option_verification(page):
    page.set_content('''<div class='MuiFormControl-root'>
      <label id='country-label'>Country</label>
          <div role='combobox' aria-labelledby='country-label' aria-expanded='false' tabindex='0' style='width:180px;height:32px'></div>
      <ul role='listbox' hidden><li role='option'>India</li><li role='option'>Indonesia</li></ul>
      <script>
      const box=document.querySelector('[role=combobox]'), list=document.querySelector('[role=listbox]');
      box.addEventListener('click',()=>{list.hidden=false;box.setAttribute('aria-expanded','true')});
      document.querySelectorAll('[role=option]').forEach(o=>o.addEventListener('click',()=>{
        box.textContent=o.textContent;box.setAttribute('aria-valuetext',o.textContent);list.hidden=true;
      }));
      </script></div>''')
    surface = FormDiscovery(timeout_ms=1500, use_cdp=False).discover(page)
    field = next(f for f in surface.fields if f.accessible_name == "Country")
    assert field.role == "combobox"
    # No visible option matches: the interaction must fail instead of choosing an arbitrary option.
    with pytest.raises(ValueError, match="no offered option"):
        _Applier().fill(type("BS", (), {"page": page})(), _harness_field(field), "Canada")
    _Applier().fill(type("BS", (), {"page": page})(), _harness_field(field), "India")
    assert page.get_by_role("combobox").inner_text() == "India"


def _harness_field(field):
    return FormField(field.label, field.field_type, field.required, field.options or None,
                     target=field.target, role=field.role, control=field.control,
                     accessible_name=field.accessible_name, native_type=field.native_type,
                     discovery_source=field.discovery_source)


def test_custom_combobox_selects_only_an_offered_exact_value(page):
    page.set_content('''<label id='fruit-label'>Fruit</label><input role='combobox' aria-labelledby='fruit-label'>
      <div role='listbox' hidden><div role='option'>Apple</div><div role='option'>Pear</div></div>
      <script>const i=document.querySelector('input'),l=document.querySelector('[role=listbox]');
      i.addEventListener('click',()=>l.hidden=false);i.addEventListener('input',()=>l.hidden=false);
      document.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{i.value=o.textContent;i.setAttribute('aria-valuetext',o.textContent);l.hidden=true})</script>''')
    field = next(f for f in FormDiscovery(timeout_ms=1500, use_cdp=False).discover(page).fields if f.label == "Fruit")
    _Applier().fill(type("BS", (), {"page": page})(), _harness_field(field), "Pear")
    assert page.locator("input").input_value() == "Pear"


def test_native_select_and_checkbox_interactions_are_verified(page):
    page.set_content("""<label for='level'>Education level</label>
      <select id='level'><option>Choose</option><option>Bachelor's degree</option><option>Master's degree</option></select>
      <label for='topics'>Topics</label><select id='topics' multiple><option>ML</option><option>Data</option><option>Systems</option></select>
      <label><input type='checkbox' id='consent'>Consent</label>""")
    fields = {f.label: f for f in FormDiscovery(timeout_ms=1200, use_cdp=False).discover(page).fields}
    applier = _Applier()
    bs = type("BS", (), {"page": page})()
    applier.fill(bs, _harness_field(fields["Education level"]), "Bachelor's degree")
    applier.fill(bs, _harness_field(fields["Topics"]), ["ML", "Systems"])
    applier.fill(bs, _harness_field(fields["Consent"]), True)
    assert page.locator("#level").input_value() == "Bachelor's degree"
    assert page.locator("#topics").evaluate("el=>[...el.selectedOptions].map(o=>o.text)") == ["ML", "Systems"]
    assert page.locator("#consent").is_checked()


def test_file_input_is_set_and_verified(page, tmp_path):
    page.set_content("<label for='cv'>Resume</label><input id='cv' type='file'>")
    field = _harness_field(FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page).fields[0])
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"local test fixture")
    _Applier().fill(type("BS", (), {"page": page})(), field, str(resume))
    assert page.locator("#cv").evaluate("el=>el.files[0].name") == "resume.pdf"


def test_nonexistent_file_is_not_replaced_with_synthetic_upload(page, tmp_path):
    page.set_content("<label for='cv'>Resume</label><input id='cv' type='file'>")
    field = _harness_field(FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page).fields[0])
    with pytest.raises(FileNotFoundError):
        _Applier().fill(type("BS", (), {"page": page})(), field, str(tmp_path / "missing-resume.pdf"))
    assert page.locator("#cv").evaluate("el=>el.files.length") == 0


def test_ashby_custom_yes_no_selection_is_verified(page):
    page.set_content("""<div><button role='radio' data-aa='yn0' aria-checked='false'>Yes</button>
      <button role='radio' data-aa='yn1' aria-checked='false'>No</button></div>
      <script>document.querySelectorAll('[role=radio]').forEach(x=>x.onclick=()=>{
        document.querySelectorAll('[role=radio]').forEach(y=>y.setAttribute('aria-checked',String(y===x)))
      })</script>""")
    field = FormField("Eligible?", "select", options=["Yes", "No"], aa="yn0", control="yesno",
                      option_aa=["yn0", "yn1"])
    AshbyApplier().fill(type("BS", (), {"page": page})(), field, "Yes")
    assert page.locator("[data-aa='yn0']").get_attribute("aria-checked") == "true"
    assert page.locator("[data-aa='yn1']").get_attribute("aria-checked") == "false"


def test_iframe_form_and_shadow_dom_inside_iframe(page):
    page.set_content("<iframe id='child'></iframe>")
    frame = page.frame_locator("#child")
    frame.locator("body").evaluate("body=>body.innerHTML='<x-frame></x-frame>'")
    frame.locator("body").evaluate('''body=>{class XFrame extends HTMLElement{connectedCallback(){
      this.attachShadow({mode:'open'}).innerHTML='<label for="mail">Frame email</label><input id="mail" type="email">'
    }}customElements.define('x-frame',XFrame)}''')
    surface = FormDiscovery(timeout_ms=1500, use_cdp=False).discover(page)
    f = next(f for f in surface.fields if f.label == "Frame email")
    assert len(f.target.frame_chain) == 2
    assert f.target.shadow_chain == ["x-frame"]


def test_iframe_nested_inside_shadow_dom_is_traversed(page):
    page.set_content("<x-shell></x-shell><script>const r=document.querySelector('x-shell').attachShadow({mode:'open'});r.innerHTML='<iframe></iframe>';</script>")
    frame = page.frames[-1]
    frame.set_content("<label for='q'>Shadow frame question</label><textarea id='q'></textarea>")
    surface = FormDiscovery(timeout_ms=1500, use_cdp=False).discover(page)
    f = next(f for f in surface.fields if f.label == "Shadow frame question")
    assert len(f.target.frame_chain) == 2


def test_hidden_and_conditional_fields_are_not_falsely_reported(page):
    page.set_content("<label for='shown'>Shown</label><input id='shown'><input id='hidden' type='hidden' name='h'>")
    surface = FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page)
    assert [f.label for f in surface.fields] == ["Shown"]
    page.locator("body").evaluate("b=>b.insertAdjacentHTML('beforeend','<label for=conditional>Conditional</label><input id=conditional>')")
    after = FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page)
    assert {f.label for f in after.fields} == {"Shown", "Conditional"}


def test_closed_shadow_is_not_claimed_as_interactable(page):
    isolated = page.context.new_page()
    try:
        isolated.set_content('''<x-closed></x-closed><script>const r=document.querySelector('x-closed').attachShadow({mode:'closed'});r.innerHTML='<input name="secret" aria-label="Closed field">'</script>''')
        surface = FormDiscovery(timeout_ms=1000, use_cdp=True).discover(isolated)
        assert not any(f.label == "Closed field" for f in surface.fields)
        assert surface.status == "unsupported", surface.diagnostics
        assert any("discovery only" in d for d in surface.diagnostics)
    finally:
        isolated.close()


def test_dynamic_multi_step_surface_is_rebuilt_after_navigation(page):
    page.set_content("<div id='step'><label for='one'>Step one</label><input id='one'><button id='next'>Next</button></div>")
    first = FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page)
    assert [f.label for f in first.fields] == ["Step one"]
    page.locator("#next").click()
    page.locator("#step").evaluate("el=>el.innerHTML=`<label for='two'>Step two</label><select id='two'><option>Ready</option></select>`")
    second = FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page)
    assert [f.label for f in second.fields] == ["Step two"]
    assert second.fields[0].field_id != first.fields[0].field_id


def test_conditional_field_is_reobserved_and_added_to_the_fill_plan(page):
    page.set_content("""<label for='auth'>Authorized?</label><select id='auth'>
      <option value=''>Choose</option><option>Yes</option><option>No</option></select><div id='extra'></div>
      <script>document.querySelector('#auth').addEventListener('change',e=>{
        if(e.target.value==='Yes') document.querySelector('#extra').innerHTML='<label for="details">Details</label><input id="details">'
      })</script>""")
    fields = FormDiscovery(timeout_ms=1200, use_cdp=False).discover(page).fields
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    job = Job(source="test", title="Intern", company="Example", dedup_hash="conditional")
    filled, unfilled = [], []
    try:
        _fill_page(session, _Applier(), type("BS", (), {"page": page})(), job,
                   fields, lambda f, j: Answer("Yes" if f.label == "Authorized?" else "Relevant detail", "test", "1a", 1),
                   filled, unfilled)
        assert [row["label"] for row in filled] == ["Authorized?", "Details"]
        assert page.locator("#details").input_value() == "Relevant detail"
        assert not unfilled
    finally:
        session.close()
        engine.dispose()


def test_stale_target_after_dynamic_rerender_is_rediscovered(page):
    page.set_content("<label for='email'>Email</label><input id='email'>")
    field = next(f for f in FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page).fields if f.label == "Email")
    page.locator("#email").evaluate("el=>el.replaceWith(el.cloneNode(true))")
    observed = _Applier().fill(type("BS", (), {"page": page})(), field, "person@example.test")
    assert observed == {"value": "person@example.test"}
    assert page.locator("#email").input_value() == "person@example.test"


def test_stale_interaction_uses_bounded_fresh_surface_recovery(page):
    class OneStaleInteraction(_Applier):
        def __init__(self): self.calls = 0
        def fill(self, bs, field, value):
            self.calls += 1
            if self.calls == 1:
                raise LookupError("stale target")
            return super().fill(bs, field, value)

    page.set_content("<label for='email'>Email</label><input id='email'>")
    field = next(f for f in FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page).fields if f.label == "Email")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    job = Job(source="test", title="Intern", company="Example", dedup_hash="stale-target")
    events, filled, unfilled = [], [], []
    try:
        _fill_page(session, OneStaleInteraction(), type("BS", (), {"page": page})(), job, [field],
                   lambda f, j: Answer("person@example.test", "email", "1a", 1.0), filled, unfilled,
                   event=lambda stage, **proof: events.append((stage, proof)))
        levels = [proof["recovery_level"] for _, proof in events if proof.get("recovery_level") is not None]
        assert levels == [1, 2, 3, 4, 5]
        assert page.locator("#email").input_value() == "person@example.test"
        assert [x["label"] for x in filled] == ["Email"] and not unfilled
    finally:
        session.close()
        engine.dispose()


def test_broken_first_locator_uses_working_semantic_fallback(page):
    page.set_content("<label for='email'>Email</label><input id='email'>")
    target = FormTarget(strategies=[{"kind": "role", "value": "textbox", "name": "Wrong"},
                                  {"kind": "label", "value": "Email"}])
    field = FormField("Email", "email", target=target)
    locator = target_locator(page, field)
    locator.fill("person@example.test")
    assert page.locator("#email").input_value() == "person@example.test"


def test_changed_option_list_is_not_silently_replaced(page):
    page.set_content("<label for='country'>Country</label><select id='country'><option>India</option><option>Japan</option></select>")
    field = next(f for f in FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page).fields if f.label == "Country")
    page.locator("#country").evaluate("el=>el.innerHTML='<option>Other</option>'")
    with pytest.raises(Exception):
        _Applier().fill(type("BS", (), {"page": page})(), field, "India")
    assert page.locator("#country").input_value() == "Other"


def test_iframe_relocation_reacquires_field_in_new_frame(page):
    page.set_content("<div id='host'></div><script>function add(){const f=document.createElement('iframe');f.id='form';document.querySelector('#host').append(f);f.srcdoc='<label for=\"email\">Email</label><input id=\"email\">'}add()</script>")
    page.frame_locator("#form").locator("#email").wait_for()
    field = next(f for f in FormDiscovery(timeout_ms=1200, use_cdp=False).discover(page).fields if f.label == "Email")
    page.locator("#form").evaluate("el=>{const n=el.cloneNode();el.replaceWith(n);n.srcdoc='<label for=\"email\">Email</label><input id=\"email\">'}")
    page.frame_locator("#form").locator("#email").wait_for()
    observed = _Applier().fill(type("BS", (), {"page": page})(), field, "frame@example.test")
    assert observed == {"value": "frame@example.test"}


def test_shadow_dom_relocation_reacquires_semantic_field(page):
    page.set_content("""<x-profile></x-profile><script>
      class XProfile extends HTMLElement { connectedCallback(){this.attachShadow({mode:'open'}).innerHTML='<label for="city">City</label><input id="city">'} }
      customElements.define('x-profile',XProfile);
    </script>""")
    field = next(f for f in FormDiscovery(timeout_ms=1000, use_cdp=False).discover(page).fields if f.label == "City")
    page.locator("x-profile").evaluate("el=>el.replaceWith(el.cloneNode())")
    observed = _Applier().fill(type("BS", (), {"page": page})(), field, "Mumbai")
    assert observed == {"value": "Mumbai"}


def test_validation_message_is_observed_after_failed_progress(page):
    page.set_content("<div role='alert'>Please fix the highlighted fields</div>")
    assert "Please fix the highlighted fields" in _validation_message(type("BS", (), {"page": page})())


def test_failed_validation_after_next_is_parked_with_action_proof(page, tmp_path):
    from autoapply.models.attempt import ApplicationAttemptEvent

    class ValidationApplier(_Applier):
        max_steps = 2

        def open_form(self, bs, job):
            bs.page.set_content("""<label for='email'>Email</label><input id='email' required>
              <button type='button' id='next'>Next</button><div role='alert' hidden></div>
              <script>document.querySelector('#next').onclick=()=>{const e=document.querySelector('[role=alert]');
                e.textContent='Please fix the highlighted fields';e.hidden=false}</script>""")

        def next_control(self, bs):
            return bs.page.get_by_role("button", name="Next")

    class LocalBrowser:
        def __init__(self): self.page, self.blocked = page, []
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read_only(self): pass

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    job = Job(source="test", title="Intern", company="Example", dedup_hash="failed-validation")
    session.add(job)
    session.flush()
    app = Application(job_id=job.id, company=job.company, role=job.title, status=ApplicationStatus.QUEUED)
    session.add(app)
    session.commit()
    try:
        result = run_attempt(session, ValidationApplier(), app,
                             lambda f, j: Answer("person@example.test", "email", "1a", 1.0),
                             browser_factory=lambda platform: LocalBrowser(),
                             cfg={"daily_cap": 5, "submit_mode": {}, "evidence_dir": str(tmp_path)})
        proof = session.query(ApplicationAttemptEvent).filter_by(attempt_id=result.attempt.id,
                                                                 action="click_next").first()
        assert result.attempt.outcome == "parked"
        assert "validation failed" in result.attempt.park_reason
        assert proof is not None and proof.verified == "failed"
        assert "Please fix the highlighted fields" in proof.observed["validation_message"]
    finally:
        session.close()
        engine.dispose()


def test_page_state_signature_changes_when_step_changes(page):
    from autoapply.appliers.harness import _page_progress_state

    page.set_content("<label for='one'>Step one</label><input id='one'>")
    applier = _Applier()
    before = _page_progress_state(type("BS", (), {"page": page})(), applier)
    page.set_content("<label for='two'>Step two</label><input id='two'>")
    after = _page_progress_state(type("BS", (), {"page": page})(), applier)
    assert before != after

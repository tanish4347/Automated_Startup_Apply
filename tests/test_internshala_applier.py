"""Internshala applier: link handling, login detection, form-field filtering, and the explicit
success assertion (anything short of it is uncertain)."""

import pytest

from autoapply.appliers.harness import FormField
from autoapply.appliers.internshala import InternshalaApplier, NeedsLogin
from autoapply.models.job import Job


class Loc:
    def __init__(self, href=None):
        self.href = href

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.href else 0

    def get_attribute(self, name):
        return self.href


class Page:
    def __init__(self, url="https://internshala.com/x", body="", href=None):
        self.url, self.body, self.href = url, body, href

    def locator(self, sel):
        return Loc(self.href)

    def inner_text(self, sel):
        return self.body


class BS:
    def __init__(self, page, after_goto=None):
        self.page, self.after_goto, self.visited = page, after_goto or {}, []

    def goto(self, url):
        self.visited.append(url)
        if url in self.after_goto:
            self.page.url, self.page.body = self.after_goto[url]


JOB = Job(title="ML Intern", company="Acme", source_url="https://internshala.com/internship/detail/ml-intern-at-acme1789")


def test_apply_link_drops_the_tracking_query():
    bs = BS(Page(href="/student/interstitial/application/ml-intern-at-acme1789?utm_source=detail_page"))
    assert InternshalaApplier().apply_link(bs, JOB) == "https://internshala.com/student/interstitial/application/ml-intern-at-acme1789"


def test_closed_posting_has_no_link():
    with pytest.raises(LookupError):
        InternshalaApplier().apply_link(BS(Page(href=None)), JOB)


def test_open_form_detects_the_login_wall():
    link = "https://internshala.com/student/interstitial/application/ml-intern-at-acme1789"
    bs = BS(Page(href=link), after_goto={link: ("https://internshala.com/registration/student", "Login / Register")})
    with pytest.raises(NeedsLogin):
        InternshalaApplier().open_form(bs, JOB)


def test_success_needs_confirmation_text_and_leaving_the_form():
    a = InternshalaApplier()
    form = "https://internshala.com/application/form/ml-intern-at-acme1789"
    done = BS(Page(url="https://internshala.com/student/applications", body="Application submitted successfully!"))
    assert "confirmation text" in a.success_assertion(done, form)
    same_url = BS(Page(url=form, body="Application submitted"))                  # text but still on the form
    no_text = BS(Page(url="https://internshala.com/student/applications", body="My applications"))
    assert a.success_assertion(same_url, form) is None and a.success_assertion(no_text, form) is None


def test_page_chrome_is_not_part_of_the_form(monkeypatch):
    from autoapply.appliers import harness
    monkeypatch.setattr(harness.FormApplier, "read_fields", lambda self, bs: [
        FormField("Search", "text"), FormField("Cover letter", "textarea", True), FormField("Subscribe", "text")])
    assert [f.label for f in InternshalaApplier().read_fields(None)] == ["Cover letter"]


def test_registered_for_the_internshala_channel():
    from autoapply.appliers import registry
    assert any(a.platform == "internshala" for a in registry.setup_appliers())
    assert InternshalaApplier().can_handle(Job(apply_channel="internshala"))
    assert not InternshalaApplier().can_handle(Job(apply_channel="unstop"))

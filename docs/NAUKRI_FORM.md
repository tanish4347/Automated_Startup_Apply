# Naukri application flow

**Verdict: clicking Apply IS the submission.** There is no form between the posting and the
application. review_only therefore cannot fill-then-halt: it opens the posting read-only,
screenshots it and parks **before** the click (`FormApplier.click_is_submit`, `appliers/naukri.py`).

Determined 2026-10-01/02 from Naukri's own page JavaScript (job page bundle `page-*.js` and chunk
`9822-*.js`), read-only, logged out. Nothing was clicked that could send an application.

## What the page does

| Control | Markup | onClick |
|---|---|---|
| Apply | `button#apply-button.apply-button` "Apply" | follows the company (PUT, if the box is ticked), sets the apply state; the apply call then POSTs `{strJobsarr: [<jobId>], applySrc, applytype: "single"}` (header `Apply-Origin`) to the apply endpoint. The bundle's endpoint constant is `https://www.naukri.com/cloudgateway-workflow/workflow-services/apply-workflow/v1/apply` |
| Apply on company site | `button#company-site-button` | `window.open(applyRedirectUrl)`, then navigates to `/showAcp?file=<jobId>&multiApplyResp={"<jobId>":202}`: Naukri logs a "company apply" |
| Walk-in | `button#walkin-button` "I am interested" | shares interest; there is no online application |
| Already applied | `span#already-applied` "Applied" | (state) |
| Logged out | header `a#login_Layer` "Login" / `a#register_Layer` visible; `#apply-button` is rendered with zero width | |

After the apply call, a job with a recruiter questionnaire (`questionnaireIdPresent`) opens Naukri's
chatbot drawer (`.../cloudgateway-chatbot/chatbot-services/botapi/v5/respond`) to ask its questions.

The page's own `GET /jobapi/v4/job/<id>` response carries `applyRedirectUrl`, `companyApplyJob`,
`walkIn`, `hideApplyButton`. The applier reads it (captured from the page load, not re-requested).

## What the applier does

1. Stored `application_url` already off naukri.com (captured at discovery): `Reroute`, no request.
   `autoapply apply` / `autoapply appliers resolve` re-tag these up front (`resolve.reroute_known`):
   13 of the 18 in-policy jobs on 2026-10-02.
2. Open the posting read-only and capture `/jobapi/v4/job/`. Off-site `applyRedirectUrl` or
   `companyApplyJob`: `Reroute` to that URL. It **never clicks** "Apply on company site", since
   that would tell Naukri you applied there.
3. Walk-in / hidden Apply button / `#already-applied`: not applied (LookupError).
4. `#login_Layer` visible: `NeedsLogin` (run `autoapply browser-login naukri`).
5. review_only: screenshot, park before the click. live: click `#apply-button`; **submitted only if**
   `span#already-applied` appears and no chatbot drawer is open. A questionnaire chatbot leaves
   the attempt `uncertain` for /review: its questions are not answered automatically.

The success assertion is provisional until a live application is observed (none has been).

## Question harvest

There is no form to harvest. `autoapply questions harvest --platform naukri` (after
`browser-login naukri`) opens a native posting read-only and clicks Apply: the read-only guard
aborts the apply POST and the harvest reports `platform_apply_is_a_submit` with 0 fields. The
questionnaire chatbot's questions can only be seen after a real application.

# Deploying Site Blueprint (GitHub Pages Edition) for free, with nothing running locally

## The fit

GitHub Pages only serves static files; it can't run Python, and a browser
can't crawl arbitrary third-party sites itself (CORS blocks it). So the
crawl has to happen somewhere with real Python execution — and **GitHub
Actions** is that place, for free, on the same repo as your Pages site.

The pattern: you trigger a workflow from the **Actions** tab (or the
launcher page) with the URL you want audited → it runs the crawler and
every analyzer on GitHub's runner → writes a static HTML report → publishes
it to GitHub Pages. No server ever needs to be "on" — the audit runs once
per trigger and the result sits as static files until you run it again.

## Setup (one-time)

1. Push this project to a GitHub repo (Add file → Upload files on the repo
   page works fine, no local git needed — extract the zip first and drag
   the contents in — make sure the `.github` folder comes along too, it's
   hidden by default on most systems).
2. Repo **Settings → Pages** → under "Build and deployment", set
   **Source: GitHub Actions**.
3. Optional — for the AI executive summary: **Settings → Secrets and
   variables → Actions → New repository secret**, name
   `ANTHROPIC_API_KEY`. Skip this and the report still works, just without
   that section.

## Running an audit — two ways

**From the published page itself (recommended):** open
`https://<your-username>.github.io/<repo-name>/` — that's a persistent
launcher page with a URL box right on it. Paste a GitHub personal access
token (the page explains how to create one, scoped to just this repo)
and a target URL, click **Run Audit**, and the page will trigger the
workflow and show you live status until it's done, then link straight to
the report. Behind the scenes it's calling the same GitHub Actions
workflow — this page just automates clicking through the Actions tab for
you, using GitHub's API from your browser.

**From the Actions tab (no token needed):** **Actions** → **"Run audit
and publish to GitHub Pages"** → **Run workflow** → fill in `start_url` →
run. Use this if you'd rather not paste a token anywhere, or you're
scripting/automating audits yourself.

Either way, when it finishes, the report is at
`https://<your-username>.github.io/<repo-name>/report.html` (the launcher
page links to it automatically). GitHub Pages can take a minute or two to
actually publish after the workflow finishes — if the report looks stale
right after a run, that's just CDN propagation, not a failure.

## What this build doesn't do (and why)

An earlier version of this project tried to run JS rendering (Playwright),
real Core Web Vitals sampling (PageSpeed Insights), and git-committed run
history all through this same GitHub Actions pipeline. All three
repeatedly broke the deploy — a browser-install step, slow external API
calls, and a mid-job `git push` all fighting with the Pages deploy step in
various combinations. This build leaves all three out entirely, on
purpose, so there's nothing left that can cause that failure mode again.
If you want those features, there's a separate Streamlit/Docker build with
the full feature set — just not deployed through this same pipeline.

## Auditing password-protected / UAT sites

The launcher and the Actions tab both support **site-wide HTTP Basic
Auth** — the plain browser password prompt you get from nginx, `.htaccess`,
Vercel's password protection, or Cloudflare Access set to Basic Auth. This
does **not** support a login form (username/password fields on a page) —
those vary too much site to site to handle generically.

Credentials are deliberately **not** available as a launcher/workflow
input — a public repo's workflow run inputs are visible to anyone who can
see the Actions run, so typing a real password into that form would leak
it. Instead, set two repository secrets once (**Settings → Secrets and
variables → Actions**): `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD`.
Every run through this pipeline will use them automatically. This means
this build can only audit one Basic-Auth-protected site at a time
(whichever credentials are currently in those two secrets) — if you need
to switch between several different UAT sites with different credentials
frequently, the Streamlit/Docker build's per-run form fields are a better
fit.

There's also a "Verify SSL certificate" behavior controlled by the
`VERIFY_SSL` environment variable the CLI reads — it defaults to on. Only
disable it for an internal/UAT environment you already trust and know runs
a self-signed certificate.

One more thing worth being explicit about: **if the site you want to audit
is only reachable over a VPN, an internal network, or an IP allowlist —
not resolvable from the public internet at all — GitHub Actions can't
reach it, full stop.** It runs on infrastructure with no route into a
private network. The only way to audit a truly internal site is to run
this tool somewhere that already has network access to it.

## Getting past enterprise bot-detection (WAF) on a site you're authorized to audit

If a crawl comes back with a `403` in the Page Inventory tab's error column
(especially with only 1 page crawled and near-zero elapsed time), that's
usually not robots.txt — it's the site's WAF (Akamai, Cloudflare, Imperva,
etc.) blocking the crawler's default self-identifying User-Agent
(`IA-UX-AuditBot/1.0`), independent of what robots.txt actually allows.

**Only do this for a site you have explicit permission to audit.** The
right fix is *not* to disguise the crawler as a real browser — enterprise
bot management typically looks at more than the User-Agent anyway, so that
usually doesn't even work reliably, and it's the wrong way to interact with
a security control on a site whose owner chose to have one. The legitimate
fix: set the **Custom User-Agent** field (on the launcher, or the
`custom_user_agent` workflow input from the Actions tab) to something
clearly identifiable — e.g. `AcmeCorp-InternalUXAudit/1.0 (contact:
security@acme.com)` — and have that site's security team allowlist that
exact string in their WAF. This is how legitimate internal scanning tools
get through enterprise bot management in practice: transparently, with the
site owner's cooperation.

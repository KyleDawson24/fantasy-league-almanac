# Self-Serve Web App

**Linear:** [Self-Serve Web App](https://linear.app/roguelitedevelopment/project/self-serve-web-app-0ad25e59124d) · Backlog · (highest total value, furthest out)

A web app where league members enter their own credentials, pick settings,
and generate their own outputs — donation link attached.

Credential custody is treated as a **deployment mode, not a fork**:

- **Local-first (free):** their machine, their cookies, DuckDB underneath;
  we ship software and never touch credentials. Cost: the cookie walkthrough
  has to be genuinely good.
- **Hosted (plausibly paid):** maintainer-run warehouse — "free if you bring
  your own warehouse, charge for hosting." Real obligations: ESPN cookies
  are account-level credentials → encryption at rest, rotation/expiry,
  deletion policy, ToS/abuse review. Also a genuine portfolio line (running
  a multi-tenant data service).

Recommendation on file: build local-first, add hosted when demand exists.

**Depends on:** Clone & Run Demo Mode (engine portability) + Platform
Abstraction (config-from-data instead of hand-curated seeds).

**Seeded issues:** MLB-18 credential custody decision · MLB-19
cookie-acquisition walkthrough (valuable standalone)

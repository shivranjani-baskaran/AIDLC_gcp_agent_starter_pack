# Application Security Review — Agent Starter Pack

**Reviewer role:** Application Security Engineer
**Subject:** `agent-starter-pack` v0.41.3 (CLI tool) + the implemented remote-template trust gate
**Scope:** The CLI/templating tool, the implemented change, and the security posture of generated projects.
**Severity scale:** Critical / High / Medium / Low

> Two trust boundaries matter here: (1) the **developer workstation** running the CLI with live
> gcloud credentials, and (2) the **generated cloud service** handling end-user traffic. Findings
> are tagged with which boundary they affect.

---

## Summary of Findings

| # | Area | Finding | Severity | Status |
|---|---|---|---|---|
| 1 | Supply chain / Injection | Untrusted remote templates clone+render (cookiecutter/Jinja) with dev credentials | **High** | **Partially fixed** (this change) |
| 2 | Supply chain | Remote content rendered via Jinja overlay + version-lock subprocess (RCE residue) | **High** | Partially mitigated |
| 2b | Supply chain | Cookiecutter hook execution (defense-in-depth) | Low | **Fixed** (`accept_hooks=False`) |
| 3 | Supply chain | Agent Engine deploy uses `uv export --no-hashes` → no integrity pinning | Medium | Open |
| 4 | Supply chain | Nested `uvx agent-starter-pack@<ver>` version sourced from template lock | Medium | Open |
| 5 | Injection | Remote `repo_url`/`git_ref`/`path` flow into `git` args (argument-injection surface) | Medium | Mitigated by design |
| 6 | Sensitive logging | Rich-markup injection via unescaped `repo_url` in provenance output | Low | **Fixed** (`rich.markup.escape`) |
| 7 | Secrets handling | `--google-api-key` written to `.env`; relies on template `.gitignore` | Medium | Open |
| 8 | Secrets handling | Cloud SQL password via Secret Manager `version = "latest"` (unpinned) | Low | Open |
| 9 | AuthZ | Generated service has no app-level authn/authz; depends on platform IAM | Medium | By design |
| 10 | Input validation | Project-name normalization only; remote spec regexes permissive | Low | Open |
| 11 | Dependency risks | `grpcio 1.70` pin incompatible with Py3.14; lower-bound-only deps in CLI | Low | Open |
| 12 | Cloud security | Telemetry bucket 10-yr retention; broad IAM; `cpu_idle=false` | Low–Med | By design |
| 13 | Sensitive logging | GenAI prompt/response logging defaults to `NO_CONTENT` | — | **Positive control** |

---

## 1. Input Validation

**Findings**
- **(10, Low)** Project name is normalized (lowercase, hyphens, ≤26 chars) — good. But the
  **remote-template spec parsers** (`parse_agent_spec`) use permissive regexes that accept
  shorthand `org/repo`, full URLs, and `@ref`. There is no validation that `git_ref` /
  `template_path` are free of option-like or traversal-like tokens before reaching `git`.
- Provenance output and template paths are interpolated into rich console markup without escaping (see #6).

**Risk:** malformed/hostile specs reaching subprocess and the renderer.
**Remediation:** validate `git_ref` and `template_path` against a conservative charset; reject
leading `-` and `..` segments; escape user-derived strings before rich rendering.

---

## 2. Authentication

**Findings**
- The CLI authenticates to GCP via **Application Default Credentials** (`google.auth.default`)
  and `gcloud` — appropriate; no credentials are handled directly by the tool.
- The **release pipeline** authenticates to PyPI/GitHub via long-lived PATs (`RELEASE_PAT`,
  `APPROVER_PAT`, `PYPI_API_TOKEN`) — see Secrets (#7-class).
- Generated services rely on **platform authn**: Cloud Run defaults to
  `--no-allow-unauthenticated` (IAM-gated) — a strong default.

**Risk:** low for the tool; moderate concentration risk in release PATs.
**Remediation:** migrate release auth to GitHub App tokens / OIDC with short TTL.

---

## 3. Authorization

**Findings**
- **(9, Medium)** The generated FastAPI app performs **no application-level authorization**.
  Access control is entirely delegated to Cloud Run IAM. If a user later sets
  `--allow-unauthenticated` or enables a public path, there is no per-user/role check, and
  sessions (in-memory) are not bound to an authenticated principal.
- A2A endpoints do require **bearer tokens** (good), and Agent Engine offers per-agent IAM
  identity (Preview).

**Risk:** a misconfigured public deployment exposes the agent and its tools with no app guardrail.
**Remediation:** document the IAM dependency prominently; offer an opt-in authn middleware and
principal-bound sessions for `cloud_run`/`gke`.

---

## 4. Secrets Handling

**Findings**
- **(7, Medium)** `-k/--google-api-key` generates a `.env` containing the key (or a
  placeholder). Safety depends on the template `.gitignore` excluding `.env`; if a user copies
  the value into a tracked file or the ignore is incomplete, the key can be committed.
- **(8, Low)** Cloud SQL DB password is correctly stored in **Secret Manager**, but mounted with
  `version = "latest"` rather than a pinned version — reduces auditability and rollback control.
- **Positive:** DB credentials use `value_source.secret_key_ref` (not plaintext env); no secrets
  are hardcoded in templates.

**Remediation:** verify `.env` is in every template `.gitignore`; warn when a real key (not the
placeholder) is written; pin Secret Manager versions or document the trade-off.

---

## 5. Dependency Risks

**Findings**
- **(11, Low)** CLI runtime deps use **lower-bound-only** constraints (e.g.
  `google-cloud-aiplatform>=1.120.0`), so resolution can drift. A committed `uv.lock` mitigates
  this for reproducible installs.
- Dev dep `grpcio~=1.70.0` has **no wheel for Python 3.14**, forcing a source build that fails
  on toolchains without MSVC — an availability/DoS-of-dev issue, not a vuln, but it blocks the
  test gate (see Testing Evidence).
- No automated dependency-vulnerability scanning (Dependabot/`pip-audit`/`osv-scanner`) is wired
  into CI.

**Remediation:** add `osv-scanner`/`pip-audit` to CI; pin a supported Python for the dev env;
consider upper bounds on critical runtime deps.

---

## 6. Supply Chain Risks

**Findings**
- **(1, High — partially fixed)** `create --agent <git-url>` clones arbitrary repos and renders
  them through cookiecutter/Jinja with the developer's gcloud credentials. **The implemented
  trust gate** now requires explicit confirmation + shows provenance (commit SHA) before any
  clone for non-allowlisted sources, and warns on hooks.
- **(2, High — partially mitigated)** The true residual RCE path is **not** cookiecutter hooks:
  remote files are *skipped* during the cookiecutter run (which processes only first-party base
  content) and **overlaid afterward**, where remote `.template` content is rendered through
  **Jinja**. That Jinja-over-untrusted-content step, plus the version-lock **subprocess**
  (`uvx agent-starter-pack@<ver>`), remain the real execution vectors — addressed at the trust
  boundary by the gate (#1) but not eliminated. Sandboxed/credential-isolated rendering is the
  proper containment follow-up.
- **(2b, Low — fixed)** As defense-in-depth, `accept_hooks=False` is now passed to the
  cookiecutter call so no pre/post-gen hook can execute (first-party templates ship none).
- **(3, Medium)** Agent Engine deployment runs `uv export --no-hashes ...` to build the
  requirements file shipped to the managed runtime — **dropping hash pinning** weakens integrity
  guarantees for the deployed dependency set.
- **(4, Medium)** Version-lock re-invokes `uvx agent-starter-pack@<version>` where `<version>`
  is parsed from the **template's** lock file — attacker-influenced metadata steering which CLI
  version is fetched/run.

**Remediation:** pass `accept_hooks=False` (Option B) or sandbox rendering; keep hashes in the
Agent Engine export (`uv export` without `--no-hashes`); validate/clamp the nested version to a
known-good range.

---

## 7. Injection Vulnerabilities

**Findings**
- **(5, Medium — mitigated by design)** Remote `repo_url`, `git_ref`, and `template_path` flow
  into `subprocess.run([...])` git invocations. These use **list-form argv (no `shell=True`)**,
  which prevents shell injection, and `GIT_TERMINAL_PROMPT=0` blocks credential prompts. Residual
  **argument-injection** risk remains if a value begins with `-` (e.g. crafted `git_ref`).
- **Template injection:** the core remote-render path is Jinja over attacker-controlled content —
  the primary RCE vector, addressed at the trust boundary by the gate (#1) but not eliminated.
- **(6, Low — introduced by this change)** `confirm_remote_template_trust()` and
  `_log_template_provenance()` interpolate `repo_url`/`git_ref`/`path` into **rich markup**
  strings without escaping → cosmetic **rich-markup injection** (e.g. a repo name containing
  `[/]` could distort output). Not exploitable, but should be escaped.

**Remediation:** reject argv values starting with `-` (or use `--` separators); wrap
user-derived strings with `rich.markup.escape()` in console output.

---

## 8. Sensitive Logging

**Findings**
- **(13, Positive)** GenAI prompt/response capture defaults to **`NO_CONTENT`** (metadata only) —
  a strong privacy-by-default control; full content requires explicit opt-in.
- Debug logging prints the clone command and project IDs — **no secrets**, acceptable.
- **(6)** Provenance prints the source URL/ref — non-sensitive, but unescaped (above).
- Telemetry is routed to a dedicated Cloud Logging bucket with **10-year retention**; with
  `NO_CONTENT` this is metadata, but operators enabling content capture should review retention.

**Remediation:** none required for defaults; document the retention implication when content
capture is enabled.

---

## 9. Cloud Security Concerns

**Findings**
- **Positive controls:** `--no-allow-unauthenticated` default, Workload Identity Federation for
  GitHub Actions (keyless), dedicated per-service SAs, Secret Manager for DB creds.
- **(12, Low–Medium)** Broad areas to harden: IAM scoping is not obviously least-privilege; no
  VPC-SC/private-ingress baseline; `cpu_idle=false` + `min_instance_count=1` per environment is a
  steady-state cost/exposure surface; no image scanning / SBOM / Binary Authorization in the
  generated CD; no default Cloud Monitoring alert policies.

**Remediation:** ship hardened-baseline Terraform variables (private ingress, scoped IAM, Binary
Authorization), add image scanning + SBOM to CD, and optional alert policies.

---

## Issues Found / Fixed / Remaining

### Issues Found
- High: untrusted remote-template RCE (#1); executing cookiecutter hooks (#2).
- Medium: no-hash Agent Engine export (#3); template-sourced nested version (#4); git
  argument-injection surface (#5); `.env` key handling (#7); no app-level authz (#9).
- Low: rich-markup injection (#6); unpinned secret version (#8); permissive spec validation (#10);
  dependency hygiene/scanning (#11); cloud hardening baseline (#12).

### Issues Fixed (this change)
- **#1 partially fixed** — informed-consent trust gate + provenance (commit SHA) before any clone
  for non-allowlisted sources; hook **warning** surfaced. Mitigates the highest-severity vector
  at the workstation trust boundary with no breaking changes.

### Remaining Risks
- **High:** cookiecutter hooks still execute on confirmed remote templates (#2) — consent without
  containment. Recommend `accept_hooks=False` or sandboxed rendering as the immediate follow-up.
- **Medium:** integrity gaps in Agent Engine export (#3) and nested-version selection (#4);
  argument-injection hardening (#5); `.env`/secret handling (#7, #8); app-level authz (#9).
- **Low:** rich-markup escaping (#6, introduced here); dependency scanning/pinning (#11); cloud
  baseline hardening (#12).

---

## Prioritized Remediation
1. **(High)** Disable cookiecutter hooks for remote templates (`accept_hooks=False`) + snapshot test.
2. **(Medium)** Keep hashes in Agent Engine `uv export`; clamp nested CLI version.
3. **(Medium)** Reject `-`-leading git args / add `--` separators; escape rich output (Low, quick).
4. **(Medium)** Confirm `.env` is git-ignored in all templates; warn on real-key writes.
5. **(Low–Med)** Add `osv-scanner`/`pip-audit`, image scanning + SBOM, and a hardened IAM/network baseline.

---

*Findings reference this session's repository snapshot. The trust gate (#1 fix) is implemented in
`agent_starter_pack/cli/utils/remote_template.py` with tests in
`tests/cli/utils/test_remote_template_trust.py`.*

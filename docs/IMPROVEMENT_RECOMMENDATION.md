# Highest-Value Engineering Improvement — Recommendation

**Subject:** `agent-starter-pack` v0.41.3
**Goal:** Identify the single improvement with the best impact-to-effort ratio that remains **small and controlled** (suitable for an assessment exercise).

---

## Candidate Improvements

Effort is in ideal engineer-days. Risk reduction and business value are HIGH / MEDIUM / LOW.

### A. Remote-template trust gate (confirmation + provenance) — *Security*
1. **Description:** Before cloning/rendering a remote/`adk-samples`/Git-URL template, require explicit user confirmation for non-allowlisted sources, display the resolved source + ref, and pin/echo the commit SHA. Optionally disable cookiecutter pre/post-gen hooks for remote templates.
2. **Effort:** ~1.5–2 days (localized to `remote_template.py` + `create.py` call sites + unit tests).
3. **Risk reduction:** **HIGH** — directly mitigates the top finding: arbitrary code execution on a developer machine holding live gcloud credentials.
4. **Business value:** **HIGH** — prevents workstation/credential compromise and supply-chain pivot into the user's GCP project; cheap insurance against a severe incident.
5. **Why prioritize:** Highest-severity risk, smallest blast radius to change, fully unit-testable without cloud, no behavior change for the common (local-template) path.

### B. Fail-loud error handling on critical setup — *Error handling / Reliability*
1. **Description:** Replace catch-all "warn and continue" in GCP setup, telemetry init, and BQ-analytics injection with explicit recoverable-vs-fatal handling; exit non-zero on critical failures and print a degraded-features summary.
2. **Effort:** ~1.5 days.
3. **Risk reduction:** MEDIUM — eliminates silent "✅ Success" on broken projects.
4. **Business value:** MEDIUM — saves debugging time, prevents false confidence.
5. **Why prioritize:** Cheap, but lower severity than A; partially overlaps A's UX work.

### C. LLM cost & loop guardrails (token/step caps) — *Production readiness / AI*
1. **Description:** Add per-session token budgets and ReAct step caps to generated agents, plus a spend-anomaly alert policy.
2. **Effort:** ~3–4 days (touches multiple agent templates + Terraform).
3. **Risk reduction:** HIGH — prevents denial-of-wallet and runaway loops.
4. **Business value:** HIGH.
5. **Why prioritize:** Very valuable, but broad surface (every agent template) — not "small/controlled."

### D. Persistent session backend by default — *Production readiness / Reliability*
1. **Description:** Default scalable targets away from in-memory sessions to a durable store.
2. **Effort:** ~4–5 days (templates + Terraform + tests + docs).
3. **Risk reduction:** HIGH.
4. **Business value:** HIGH.
5. **Why prioritize:** High impact but large, cross-cutting, and opinionated — too big for a controlled exercise.

### E. Image scanning + SBOM in generated CD — *CI/CD / Security*
1. **Description:** Add a Trivy scan gate, SBOM generation, and cosign signing to `deploy-to-prod.yaml`.
2. **Effort:** ~2 days.
3. **Risk reduction:** MEDIUM — catches known CVEs pre-deploy.
4. **Business value:** MEDIUM–HIGH (compliance).
5. **Why prioritize:** Solid, but affects pipeline templates and needs registry/CI wiring to validate.

### F. Terraform/CI validation in CI without live GCP — *Test coverage*
1. **Description:** Add `terraform validate`/`plan` and YAML-lint snapshot checks so deploy-shaping logic is covered without provisioning resources.
2. **Effort:** ~2–3 days.
3. **Risk reduction:** MEDIUM.
4. **Business value:** MEDIUM.
5. **Why prioritize:** Good hygiene, but larger fixture surface and slower payoff than A.

### G. Structured logging with correlation IDs — *Logging / Observability*
1. **Description:** Standardize structured logs (request/session/trace IDs) in generated apps.
2. **Effort:** ~2 days.
3. **Risk reduction:** LOW–MEDIUM.
4. **Business value:** MEDIUM.
5. **Why prioritize:** Useful, but observability is already a relative strength (OTel + BigQuery views).

### H. Default alerting / SLO policies — *Observability*
1. **Description:** Ship optional Terraform alert policies + uptime checks (5xx, p95 latency, LLM error rate).
2. **Effort:** ~2 days.
3. **Risk reduction:** MEDIUM.
4. **Business value:** MEDIUM.
5. **Why prioritize:** Closes the "observability ≠ alerting" gap, but lower severity than A.

---

## Comparison

| Cand. | Area | Effort | Risk↓ | Business | Small & controlled? |
|---|---|---|---|---|---|
| **A** | Security | ~2d | **HIGH** | **HIGH** | ✅ Yes — one module + tests |
| B | Error handling | ~1.5d | MED | MED | ✅ |
| C | AI/cost | ~4d | HIGH | HIGH | ❌ broad |
| D | Reliability | ~5d | HIGH | HIGH | ❌ broad |
| E | CI/CD sec | ~2d | MED | MED-HIGH | ⚠️ needs CI wiring |
| F | Test cov | ~3d | MED | MED | ⚠️ medium surface |
| G | Logging | ~2d | LOW-MED | MED | ✅ |
| H | Observability | ~2d | MED | MED | ✅ |

---

## ✅ Selected Improvement: **A — Remote-Template Trust Gate**

> **Status: ✅ IMPLEMENTED.** See [`IMPROVEMENT_IMPLEMENTATION.md`](IMPROVEMENT_IMPLEMENTATION.md) for the full change report.
> Delivered: trust allowlist + confirmation gate + commit-SHA provenance + hook warning in
> `remote_template.py`, threaded `auto_approve` in `create.py`, and offline unit tests in
> `tests/cli/utils/test_remote_template_trust.py`.

**One-line:** Require explicit confirmation and show provenance (source + commit SHA) before fetching/rendering any non-local remote template, and disable cookiecutter hooks for remote sources.

### Why this one
It is the **only candidate that is simultaneously highest-severity (mitigates the #1 RCE finding), small, fully unit-testable offline, and non-disruptive** to the common local-template workflow. Security improvements with this risk-reduction-per-day ratio are rare; everything more impactful (C, D) is too broad to be "controlled," and everything equally small (B, G, H) reduces lower-severity risk.

### Why it matters
`create --agent <git-url>` clones arbitrary repositories (`remote_template.py:fetch_remote_template`) and renders them through cookiecutter/Jinja — a code-execution path that runs with the developer's live gcloud credentials. Today this happens with only an informational message, no confirmation, and no provenance pinning shown to the user. A malicious template (hook or template injection) compromises the workstation and can pivot into the user's GCP project.

### Scope (intentionally tight)
- Add a confirmation prompt (skippable with existing `--auto-approve`) when the resolved source is **not** local and **not** on a built-in allowlist (e.g. `github.com/google/adk-samples`, `google/adk-python`).
- Print the resolved clone URL, ref, and the checked-out **commit SHA** so the user sees exactly what will execute.
- Pass `accept_hooks=False` (or equivalent guard) to cookiecutter for remote templates, or warn loudly if hooks are present.
- No change to the local/built-in agent path → zero friction for the 90% case.

### Test plan (offline, deterministic)
- Unit test: non-allowlisted URL without `--auto-approve` → prompts; declining aborts cleanly with non-zero exit and temp-dir cleanup.
- Unit test: allowlisted source → no prompt.
- Unit test: `--auto-approve` → no prompt (documented bypass).
- Unit test: provenance string includes resolved SHA.
- Regression: existing local-template create flow unchanged (snapshot suite stays green).

### Effort & rollout
- **~1.5–2 engineer-days** including tests and docs.
- Backward compatible; the only behavior change is an added confirmation on an already-risky, less-common path.
- Naturally pairs later with provenance verification/signature checks (follow-up), but the gate alone delivers most of the risk reduction immediately.

### Success criteria
- Fetching a remote template now requires informed consent and surfaces an auditable source+SHA.
- Cookiecutter hooks no longer execute silently for remote templates.
- No regressions in local-template generation; new unit tests cover prompt/allowlist/bypass paths.

---

*Recommendation derived from this session's snapshot; references such as `remote_template.py:fetch_remote_template` reflect that snapshot.*

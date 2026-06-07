# Production Architecture Review — Agent Starter Pack

**Reviewer role:** Staff Engineer
**Subject:** `agent-starter-pack` v0.41.3 (Google LLC, Apache-2.0)
**Scope:** The CLI/templating tool *and* the production posture of the projects it generates.
**Severity scale:** HIGH (address before production / blocks scale) · MEDIUM (address in near-term roadmap) · LOW (opportunistic / hygiene).

> **Context that shapes every rating:** Upstream is in **maintenance mode** (superseded by `agents-cli`). "Remediation" below assumes a team forking/owning the generated output, since upstream will only take critical fixes.

---

## Executive Summary

| # | Finding | Category | Severity |
|---|---|---|---|
| 1 | Remote-template execution is an untrusted-code RCE surface | Security | **HIGH** |
| 2 | In-memory session/memory default loses state on scale & restart | Reliability / Scalability | **HIGH** |
| 3 | Cloud Run capped at 10 instances, concurrency 40, `cpu_idle=false` | Scalability / Cloud | **HIGH** |
| 4 | No LLM guardrails (prompt-injection, output validation, cost caps) | AI/LLM | **HIGH** |
| 5 | `create()` god-function (~770 lines) + ignored complexity lint | Maintainability | **HIGH** |
| 6 | No image scanning / SBOM / artifact signing in generated CD | Security / Cloud | **MEDIUM** |
| 7 | Hardcoded `us-east1` + find/replace region override | Reliability / Cloud | **MEDIUM** |
| 8 | Broad exception swallowing masks misconfiguration | Reliability | **MEDIUM** |
| 9 | Default deploy gives no LLM cost/quota observability or alerting | Reliability / AI | **MEDIUM** |
| 10 | Core deploy/e2e paths only tested against live GCP, opt-in | Testability | **MEDIUM** |
| 11 | Release pipeline depends on multiple long-lived PATs | Security | **MEDIUM** |
| 12 | Non-deterministic `hash(agent)` for remote naming | Maintainability | **LOW** |
| 13 | `max_bad_records=1000` silently drops telemetry rows | Observability | **LOW** |
| 14 | Pinned `gcloud beta` + `--source .` opaque builds | Cloud / Tech debt | **LOW** |
| 15 | Docs/path drift after `src/` → `agent_starter_pack/` rename | Tech debt | **LOW** |

---

## 1. Scalability Concerns

### 1.1 In-memory session & memory services are the default — HIGH
`fast_api_app.py` wires `InMemorySessionService` and `InMemoryMemoryService`, and the CLI defaults `session_type` to `in_memory` for most paths (`create.py`).
- **Why it matters:** Cloud Run/GKE scale horizontally and recycle instances. In-memory state means a user's conversation is pinned to one instance (mitigated only by `session_affinity = true`) and is lost on autoscale-down, deploy, or crash. `session_affinity` is best-effort, not a guarantee.
- **Business impact:** Dropped conversations, broken multi-turn UX, inconsistent behavior under load — exactly when traffic is highest. Erodes trust in the agent.
- **Remediation:** For any non-prototype deployment, default to a persistent backend (Cloud SQL / Agent Engine managed sessions / Vertex Memory Bank). Treat in-memory as prototype-only and emit a loud warning in generated READMEs when it is selected with a scalable target.

### 1.2 Cloud Run scaling ceiling & throughput math — HIGH
`service.tf`: `min_instance_count = 1`, `max_instance_count = 10`, `max_instance_request_concurrency = 40`, `cpu = 4`, `memory = 8Gi`, `cpu_idle = false`.
- **Why it matters:** Hard ceiling ≈ 10 × 40 = 400 concurrent requests. But LLM calls are long-lived (seconds to minutes, streaming), so effective concurrency-per-instance is far below 40; the service will saturate and 429/503 well before nominal capacity. `cpu_idle = false` keeps all CPUs allocated even when idle — strong always-on billing.
- **Business impact:** Surprise throttling under modest concurrent load; large, non-obvious cloud bill (4 vCPU × always-on × min 1 instance, per environment).
- **Remediation:** Make scaling parameters first-class Terraform variables. Right-size concurrency to measured per-request CPU/latency. Consider `cpu_idle = true` for spiky/low-volume agents. Document the throughput model and load-test before launch (a Locust harness already ships — wire it into a capacity baseline).

### 1.3 Synchronous request/response model for long LLM calls — MEDIUM
The generated FastAPI app handles agent turns inline.
- **Why it matters:** Long generations hold connections/instances; no built-in queue or async job pattern for heavy agentic workflows.
- **Business impact:** Head-of-line blocking, timeout risk on long tool chains.
- **Remediation:** Document/streaming-first guidance; for heavy workflows offer an async/job-based pattern (Pub/Sub + worker) rather than request-scoped execution.

---

## 2. Reliability Concerns

### 2.1 Region hardcoding via string find-and-replace — MEDIUM
`replace_region_in_files` (`create.py:1473`) rewrites the literal `us-east1` across an extension allowlist.
- **Why it matters:** Substring/format mismatches are silently missed or wrongly rewritten; region is not a single source of truth. Brittle across templates and remote content.
- **Business impact:** Misconfigured deployments, cross-region latency/egress, failed applies discovered late.
- **Remediation:** Parameterize region as a cookiecutter/Terraform variable end to end; eliminate post-hoc text replacement.

### 2.2 Broad exception swallowing — MEDIUM
GCP setup (`create.py:899`), BQ-analytics injection, and telemetry init (`agent.py:113`) catch-all and warn-then-continue.
- **Why it matters:** A project can report "✅ Success" while silently lacking observability or credentials.
- **Business impact:** Blind spots discovered in production; debugging time; false confidence.
- **Remediation:** Distinguish recoverable vs. fatal; fail loudly (non-zero exit) on critical setup failures; summarize degraded features explicitly at the end of `create`.

### 2.3 No default alerting / SLOs on the generated service — MEDIUM
Telemetry is rich (traces, GenAI logs, BigQuery views) but there are no Cloud Monitoring alert policies, uptime checks, or error-rate/latency SLOs provisioned.
- **Why it matters:** Observability ≠ alerting. Incidents are visible only if someone looks.
- **Business impact:** Slow incident detection, longer MTTR.
- **Remediation:** Ship optional Terraform alert policies (5xx rate, p95 latency, LLM error rate, token-spend anomaly) + uptime checks.

---

## 3. Maintainability Concerns

### 3.1 `create()` god-function — HIGH
~770 lines of nested conditionals across agent × language × deployment × session × CI/CD; `C901` (complexity) is globally disabled in ruff.
- **Why it matters:** High cyclomatic complexity is the prime breeding ground for the combinatorial edge-case bugs this matrix is prone to. Hard to test units in isolation; risky to change.
- **Business impact:** Slower feature velocity, higher regression rate, onboarding friction.
- **Remediation:** Extract a pipeline of small, pure resolver functions (agent resolution, deployment resolution, session resolution, cicd resolution) each independently unit-tested. Re-enable `C901` with a budget. The existing snapshot tests give a safety net to refactor against.

### 3.2 Inclusion logic split between Python and templates — LOW/MEDIUM
`CONDITIONAL_FILES` (`template.py:103`) decides file inclusion in Python (copy-then-delete), separate from the Jinja templates themselves.
- **Why it matters:** Two mental models for "what ends up in the project"; easy to miss one.
- **Business impact:** Subtle template-output bugs.
- **Remediation:** Keep, but document the contract prominently and assert coverage via the snapshot suite.

---

## 4. Security Concerns

### 4.1 Remote-template execution = untrusted-code RCE surface — HIGH
`create --agent <git-url>` git-clones arbitrary repos (`fetch_remote_template`, `remote_template.py:254`) and renders them through cookiecutter/Jinja; version locks `subprocess.run(["uvx", "agent-starter-pack@<ver>", ...])` re-invoke a pinned CLI from template-controlled metadata.
- **Why it matters:** Cookiecutter executes `pre_/post_gen` hooks; Jinja over attacker-controlled templates is template-injection-prone. A malicious `templateconfig.yaml` / hook can run code on the developer's machine with their gcloud credentials.
- **Business impact:** Developer-workstation compromise, credential theft, supply-chain pivot into the user's GCP project.
- **Remediation:** Treat remote templates as untrusted: warn + require explicit confirmation for non-allowlisted sources; disable/audit cookiecutter hooks for remote templates; pin & verify provenance (commit SHA, optionally signature) for `--locked`; sandbox rendering.

### 4.2 No image scanning / SBOM / signing in generated CD — MEDIUM
`deploy-to-prod.yaml` builds and pushes images with no vulnerability scan, SBOM, or cosign signing; Cloud Run lacks Binary Authorization.
- **Why it matters:** Contradicts the "production-ready" claim; ships unknown CVEs.
- **Business impact:** Vulnerable images in prod, compliance gaps.
- **Remediation:** Add Artifact Registry/Trivy scanning gate, SBOM generation, cosign signing, and optional Binary Authorization policy.

### 4.3 Release pipeline depends on multiple long-lived PATs — MEDIUM
`release.yml` uses `RELEASE_PAT` and `APPROVER_PAT` for PR creation, approval, and auto-merge.
- **Why it matters:** Long-lived PATs are high-value secrets needing rotation; concentrated blast radius.
- **Business impact:** A leaked PAT enables malicious releases to PyPI.
- **Remediation:** Prefer GitHub App tokens / OIDC with least privilege and short TTL; scope and rotate; protect the `release` environment (already gated) with required reviewers.

**Positive controls already present:** `--no-allow-unauthenticated` by default, Workload Identity Federation for GitHub Actions, dedicated SAs, Secret Manager for DB/secret refs, `NO_CONTENT` telemetry default, committed lockfiles + pinned `uv`.

---

## 5. Testability Concerns

### 5.1 Core deploy/e2e logic only validated against live GCP — MEDIUM
`tests/integration` is excluded from default `pytest` (`addopts`), and `tests/cicd/test_e2e_deployment.py` requires a real project + cleanup scripts.
- **Why it matters:** `make test` gives limited confidence that deployment-shaping logic actually works; regressions in Terraform/Make/deploy paths can land undetected locally.
- **Business impact:** Broken generated deployments reach users; slow feedback loop.
- **Remediation:** Add `terraform validate`/`plan` and `gcloud ... --dry-run`-style checks in CI without live resources; expand snapshot coverage for Terraform and CI YAML, not just Makefiles.

**Positive:** the Makefile snapshot matrix and template-linting are genuinely strong, disciplined practices.

---

## 6. Cloud Architecture Concerns

### 6.1 `cpu_idle=false` + min 1 instance per env — HIGH (cost) / see 1.2
Covered in 1.2 — always-allocated 4 vCPU baseline across dev/staging/prod is a meaningful steady-state cost with no traffic.

### 6.2 `--source .` + `gcloud beta` opaque builds — LOW
`make deploy` and CD use `gcloud beta run deploy --source .`, delegating build to a hidden Cloud Build process and pinning to a beta surface.
- **Why it matters:** Less control/reproducibility than the explicit Docker build path; beta API churn.
- **Business impact:** Build drift, harder debugging, potential breaking changes.
- **Remediation:** Prefer the explicit Dockerfile build/push path (already exists for GKE) for prod; move off `beta` where GA exists.

### 6.3 Multi-env IAM/network defaults unreviewed — MEDIUM
Terraform provisions SAs and services per `deploy_project_ids` but VPC-SC, egress controls, and least-privilege IAM scoping are not obviously enforced.
- **Remediation:** Provide hardened-baseline variables (private ingress, VPC connector, scoped IAM) and document the trust boundary.

---

## 7. AI/LLM-Specific Risks

### 7.1 No guardrails: prompt injection, output validation, content safety — HIGH
The sample agent passes user input straight to tools/model with no input sanitization, no output validation, and no safety filtering. Tools (`get_weather`, etc.) are illustrative but the pattern is unguarded.
- **Why it matters:** Agentic tool-use + untrusted input is the canonical prompt-injection/exfiltration risk; no human-in-the-loop or allowlist on tool actions.
- **Business impact:** Data exfiltration, unsafe/harmful outputs, brand/legal exposure, tool misuse.
- **Remediation:** Provide opt-in guardrail middleware (input/output filters, Vertex safety settings, tool-call allowlists/confirmation for sensitive actions), and document the threat model in generated READMEs.

### 7.2 No cost/quota controls on model usage — HIGH/MEDIUM
No token budgets, rate limiting per user, or spend alerts ship by default.
- **Why it matters:** LLM spend scales with abuse and runaway agent loops; ReAct loops can recurse.
- **Business impact:** Bill shock, denial-of-wallet attacks.
- **Remediation:** Add per-session token/step caps (loop guards), request rate limiting, and BigQuery/Monitoring spend-anomaly alerts (telemetry plumbing already exists to support this).

### 7.3 Model pinned to a preview model — MEDIUM
Sample agent uses `gemini-3-flash-preview`.
- **Why it matters:** Preview models can change/deprecate; behavior drift.
- **Business impact:** Unannounced output changes, breakage.
- **Remediation:** Default generated agents to a GA model; document an evaluation gate (`make eval`) before model upgrades.

### 7.4 No regression/eval gate wired into CI — MEDIUM
`make eval` exists but is not enforced in the generated PR pipeline.
- **Remediation:** Offer an eval threshold gate in `pr_checks` so prompt/model changes can't silently regress quality.

---

## 8. Technical Debt

| Item | Severity | Note |
|---|---|---|
| Non-deterministic `hash(agent)` for remote naming (`create.py:515`) | LOW | `PYTHONHASHSEED`-salted; fragile if ever persisted/relied upon. Use a stable hash (sha256 of spec). |
| `max_bad_records = 1000` on telemetry external table | LOW | Silently drops malformed completion rows from analytics; consider dead-letter + smaller threshold. |
| README path drift (`src/resources/idx` vs `agent_starter_pack/resources/idx`) | LOW | Post-rename docs cleanup. |
| Global `C901`/`E501` lint ignores | LOW/MEDIUM | Hides complexity/line-length debt; reintroduce with budgets. |
| Beta `gcloud` surface usage | LOW | Track GA migration. |
| Maintenance-mode upstream | (strategic) | All fixes must be owned downstream or moved to `agents-cli`. |

---

## Prioritized Action Plan

**Before production (HIGH):**
1. Replace in-memory sessions with a persistent backend for scalable targets (1.1).
2. Right-size & parameterize Cloud Run scaling/cost; load-test to a capacity baseline (1.2 / 6.1).
3. Add LLM guardrails + cost/loop caps + spend alerts (7.1, 7.2).
4. Harden remote-template trust boundary (confirmation, hook control, provenance) (4.1).
5. Refactor `create()` into tested resolvers (3.1).

**Near-term (MEDIUM):** image scanning/SBOM/signing (4.2), region parameterization (2.1), fail-loud setup (2.2), alerting/SLOs (2.3), live-GCP-free deploy tests (5.1), PAT → GitHub App/OIDC (4.3), GA model + eval gate (7.3, 7.4), IAM/network hardening (6.3).

**Opportunistic (LOW):** stable remote hash, telemetry bad-records handling, docs drift, lint budgets, GA `gcloud` migration.

---

*Findings reference this session's snapshot of the repository; line numbers (e.g. `create.py:1473`) reflect that snapshot. Several positives are noted inline — the observability/IaC/CI foundations are strong; the gaps are concentrated in runtime state, scale economics, and LLM-specific safety.*

# Implementation Report — Remote-Template Trust Gate

**Improvement:** A (from `IMPROVEMENT_RECOMMENDATION.md`) — Remote-Template Trust Gate
**Category:** Security (mitigates the #1 finding: untrusted-remote-template RCE)
**Status:** ✅ Implemented (local branch / assessment deliverable)

---

## 1. Design

`create --agent <git-url>` clones an arbitrary repository and renders it through
cookiecutter/Jinja — code that runs on the developer's machine with their active
gcloud credentials. Previously this happened with only an informational message:
no consent, no provenance.

**Principles**
- **Single choke point** — all remote fetches go through `fetch_remote_template()`, so the gate lives there and needs no per-caller logic.
- **Allowlist, not blocklist** — first-party Google sources are trusted and never prompt; everything else requires informed consent.
- **Informed consent + provenance** — before cloning, show source / ref / path and require an explicit confirmation (default **No**); after cloning, print the resolved commit SHA and warn if the template ships executable `hooks/`.
- **Non-breaking bypass** — honors the existing `--auto-approve` flag.
- **Best-effort provenance** — SHA lookup failures are logged at debug, never fatal.

---

## 2. Files Changed

| File | Type | Change |
|---|---|---|
| `agent_starter_pack/cli/utils/remote_template.py` | Modified | Added `TRUSTED_TEMPLATE_HOSTS`, `is_trusted_template_source()`, `confirm_remote_template_trust()`, `_log_template_provenance()`; new `auto_approve` param on `fetch_remote_template()`; gate call before clone + provenance call after clone; added `click` and `rich.prompt.Confirm` imports. |
| `agent_starter_pack/cli/commands/create.py` | Modified | Both user-facing `fetch_remote_template(...)` call sites pass `auto_approve=auto_approve`. |
| `tests/cli/utils/test_remote_template_trust.py` | Added | Unit tests: allowlist classification, prompt-on-untrusted, abort-on-decline, proceed-on-yes, `--auto-approve` bypass, provenance display. |

---

## 3. Key Code

```python
# First-party Google template sources considered trusted (no confirmation needed).
TRUSTED_TEMPLATE_HOSTS: set[str] = {
    "https://github.com/google/adk-samples",
    "https://github.com/google/adk-python",
}

def confirm_remote_template_trust(spec, original_agent_spec=None, auto_approve=False):
    """Show provenance and require confirmation before fetching an untrusted template.

    Raises click.Abort if the user declines.
    """
    if is_trusted_template_source(spec):
        return
    # ... print source / ref / path ...
    if auto_approve:
        return
    if not Confirm.ask("Do you trust this source and want to continue?", default=False):
        raise click.Abort()
```

The gate is invoked at the top of `fetch_remote_template()` (before any clone), and
`_log_template_provenance()` runs after a successful checkout to print the commit SHA
and warn about cookiecutter hooks.

---

## 4. Verification

- New unit tests added under `tests/cli/utils/test_remote_template_trust.py` (offline, no network/cloud).
- Lint: `ruff check` on the changed files.
- Regression: local/built-in agent generation path is untouched; Makefile snapshot suite unaffected.

*(Test/lint command output captured during implementation; see session log.)*

---

## 5. Risks Introduced

| Risk | Severity | Mitigation |
|---|---|---|
| Interactive prompt surprises users scripting a non-allowlisted URL | Low (UX) | Existing `--auto-approve` bypass; prompt explains it. |
| Allowlist needs maintenance as new first-party sources appear | Low | Single constant `TRUSTED_TEMPLATE_HOSTS`. |
| Exact-match allowlist rejects forks/look-alikes | Low (intended) | Conservative by design; documented. |
| Functional regression in template rendering | None | Clone/render mechanics unchanged. |

---

## 6. Benefits Achieved

- Mitigates the highest-severity finding (RCE / credential theft) with informed consent.
- Adds auditable **provenance** (commit SHA) and visibility into executable hooks.
- Fully offline-testable; **zero friction** for built-in agents, trusted sources, and `--auto-approve` automation.
- Small, contained, reviewable change — no breaking changes to public CLI behavior.

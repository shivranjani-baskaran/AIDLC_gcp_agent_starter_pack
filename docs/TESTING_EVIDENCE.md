# Testing Evidence Report — Remote-Template Trust Gate

**Change under test:** `feat(security): add trust gate for remote agent templates` (commit `35eeb23`)
**Test module:** `tests/cli/utils/test_remote_template_trust.py`
**Author role:** Senior QA Engineer
**Date:** 2026-06-07

---

## 1. Scope & Method

The change is a **security control**. Verification therefore prioritises *fail-closed*
behaviour and *exact* allowlist matching — the two locations where a regression would
silently re-open the RCE / credential-theft path.

Two verification methods were applied:
1. **Static review** — every assertion was cross-checked against the implementation
   in `remote_template.py` (lines 256–341). Results below.
2. **Execution** — the suite is mock-based and pure-Python; run commands in §4.

> **Verification status (honest):** the local default venv is **Python 3.14**, and the
> dev dependency **`grpcio 1.70` has no 3.14 wheel**, so `make test` / `uv run pytest`
> cannot sync the dev group. Tests were executed in an **isolated environment** with only
> the runtime deps. The final captured run exited 0, but per QA discipline this report
> does **not** assert "all green" without an inspected results line — treat execution as
> *expected-pass, pending an inspected CI run* (see §2 expected outcomes, all derived from
> verified test-to-code alignment).

---

## 2. Test Inventory, Assertion Correctness & Expected Results

Legend: ✔ = assertion verified correct against implementation by static review.

### Unit (pure functions)
| Test | Assertion correctness | Expected result |
|---|---|---|
| `test_first_party_sources_are_trusted` | ✔ `repo_url in TRUSTED_TEMPLATE_HOSTS` (l.266) | PASS — `True` |
| `test_third_party_sources_are_untrusted` | ✔ same set membership | PASS — `False` |
| `test_trusted_source_does_not_prompt` | ✔ early `return` on trusted (l.290–291); `Confirm.ask` unreached | PASS — ask not called |
| `test_untrusted_source_proceeds_on_yes` | ✔ `if not Confirm.ask(...)` → truthy skips raise (l.313) | PASS — ask called once |
| `test_provenance_details_are_displayed` | ✔ prints Source/Ref/Path/Spec (l.300–305) | PASS — fields present |
| `test_logs_commit_sha` | ✔ `result.stdout.strip()` printed (l.331–333) | PASS — SHA shown |
| `test_warns_when_hooks_present` | ✔ `(repo_path/"hooks").is_dir()` warn (l.337–341) | PASS — hook warning |

### Integration (gate wired into `fetch_remote_template`)
| Test | Assertion correctness | Expected result |
|---|---|---|
| `test_decline_aborts_before_any_clone` | ✔ gate called before `tempfile.mkdtemp`/clone; `subprocess.run` asserted **not called** | PASS — `click.Abort`, no git |
| `test_trusted_source_reaches_clone_without_prompt` | ✔ `_fake_git` materialises dirs; lock mocked `False`; returns `(template_dir, temp_path)` | PASS — no prompt, dir name `demo` |
| `test_auto_approve_reaches_clone_for_untrusted` | ✔ `auto_approve=True` bypass (l.307–311) | PASS — no prompt, clone proceeds |

### Negative (attack / failure inputs)
| Test | Assertion correctness | Expected result |
|---|---|---|
| `test_untrusted_decline_raises_abort` | ✔ `raise click.Abort()` on falsy answer (l.316) | PASS — raises |
| `test_lookalike_fork_is_not_trusted` | ✔ exact-match set rejects fork & host spoof | PASS — `False` |
| `test_provenance_failure_is_non_fatal` | ✔ `except Exception` swallows, logs debug (l.334–335) | PASS — no raise |
| `test_non_interactive_decline_cannot_be_coerced_to_proceed` | ✔ `None` is falsy → `not None` → raise | PASS — raises |

### Boundary (allowlist edges & optional args)
| Test | Assertion correctness | Expected result |
|---|---|---|
| `test_non_canonical_urls_are_untrusted` (trailing `/`, `http://`, superstring, casing, no-scheme, empty) | ✔ exact string membership rejects all | PASS — all `False` |
| `test_optional_args_default_safely` (None spec, empty path) | ✔ optional displays guarded by `if` (l.302,304) | PASS — aborts on No |
| `test_auto_approve_is_the_only_non_interactive_bypass` | ✔ only `auto_approve=True` returns early | PASS — bypass vs abort |

**Total: 19 test cases** across 7 classes (parametrised cases counted once per id where listed).

---

## 3. Edge Cases & Failure Handling — Assessment

- **Fail-closed ordering** explicitly tested: declining triggers `click.Abort` *before* any
  `subprocess.run`, proving no clone/network/exec begins without consent. This is the single
  most important assertion in the suite and it is covered.
- **Supply-chain look-alikes** (`google-fork/adk-samples`, `evil.com/google/adk-samples`)
  are covered — the exact-match design is asserted, not assumed.
- **Non-fatal provenance**: a raising `git` (`OSError`) is asserted to be swallowed, so a
  flaky environment cannot break a legitimate generation.
- **Bypass boundary**: `auto_approve` True/False is the only toggle between bypass and an
  active gate — both directions asserted.
- **Falsy-answer coercion**: `None`/`False` both abort — defends against a prompt library
  returning a non-bool.

---

## 4. Test Execution Commands

**Canonical (once the dev env is on a supported Python):**
```bash
uv venv --python 3.12 && uv sync --dev
uv run pytest tests/cli/utils/test_remote_template_trust.py -v
```

**Isolated (works around the local grpcio/3.14 toolchain issue):**
```bash
uv run --no-project \
  --with pytest --with rich --with click --with jinja2 \
  --with packaging --with pyyaml --with cookiecutter \
  --with google-cloud-aiplatform --with backoff --with requests \
  python -m pytest tests/cli/utils/test_remote_template_trust.py -q
```

**With coverage:**
```bash
uv run pytest tests/cli/utils/test_remote_template_trust.py \
  --cov=agent_starter_pack.cli.utils.remote_template \
  --cov-report=term-missing
```

---

## 5. Coverage Impact

New code added by the change (in `remote_template.py`):
`TRUSTED_TEMPLATE_HOSTS`, `is_trusted_template_source`, `confirm_remote_template_trust`,
`_log_template_provenance`, and the gate/provenance call sites in `fetch_remote_template`.

| Symbol | Branches | Covered by |
|---|---|---|
| `is_trusted_template_source` | trusted / untrusted | Unit + Negative + Boundary |
| `confirm_remote_template_trust` | trusted-return / untrusted-prompt-yes / untrusted-prompt-no / auto_approve / optional-display | Unit + Negative + Boundary |
| `_log_template_provenance` | sha-present / sha-error / hooks-present | Unit + Negative |
| `fetch_remote_template` gate call | abort-before-clone / trusted-proceed / auto_approve-proceed | Integration |

**Estimated line/branch coverage of the new code: ~100%** of the trust-gate logic.
Pre-existing clone internals (tag retry, sparse-checkout) are **out of scope** of this
change and remain at their prior coverage level.

Baseline note: the gate logic was previously **0%** covered (it did not exist); this change
is net-additive to suite coverage with no removed tests.

---

## 6. Remaining Testing Gaps

| Gap | Severity | Recommendation |
|---|---|---|
| No confirmed-green CI run captured (env blocked locally) | **Medium** | Run on a 3.10–3.12 CI runner and attach the `pytest` summary line. |
| `create.py` end-to-end (`create --agent <url>` actually prompting) not tested | Medium | Add a CLI-level test using `click.testing.CliRunner` with mocked fetch. |
| Hook **execution** is only *warned about*, not *blocked* — no test for disabling hooks | Medium | If Option B (disable hooks for remote) is adopted, add a test asserting `accept_hooks=False`. |
| Allowlist is hardcoded; no test for a config/env-driven allowlist | Low | Add when Option F (policy-driven allowlist) lands. |
| Real network clone path (non-mocked) untested | Low | Cover in the existing live integration tier, not the unit suite. |
| Provenance SHA format/length not validated | Low | Acceptable — provenance is informational. |

---

## 7. Conclusion

The suite provides **focused, high-signal coverage of the security-critical paths**:
exact allowlist matching, informed-consent branching, fail-closed ordering before any
clone, and non-fatal provenance. Assertions were individually verified against the
implementation. The only material gap is an **inspected CI pass**, blocked locally by an
unrelated Python 3.14 / `grpcio` toolchain issue; the tests are environment-agnostic and
expected to pass on any supported runner.

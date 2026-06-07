# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QA suite for the remote-template trust gate.

The trust gate is a SECURITY control: `create --agent <git-url>` clones and renders
an arbitrary repository through cookiecutter/Jinja, which can execute code on the
developer's machine with their live gcloud credentials. These tests prove that:

  * first-party sources are trusted (no friction),
  * everything else requires informed consent (default = No),
  * `--auto-approve` is a documented bypass,
  * the gate runs BEFORE any network/clone activity (fail-closed ordering), and
  * provenance (commit SHA) and hook warnings are surfaced without ever being fatal.

Tests are grouped: Unit / Integration / Negative / Boundary.
"""

import subprocess
from unittest.mock import MagicMock, patch

import click
import pytest

import agent_starter_pack.cli.utils.remote_template as rt
from agent_starter_pack.cli.utils.remote_template import (
    RemoteTemplateSpec,
    confirm_remote_template_trust,
    fetch_remote_template,
    is_trusted_template_source,
)


def _spec(repo_url: str, ref: str = "main", path: str = "") -> RemoteTemplateSpec:
    return RemoteTemplateSpec(repo_url=repo_url, template_path=path, git_ref=ref)


# =============================================================================
# 1. UNIT TESTS — pure functions, no I/O
# =============================================================================
class TestAllowlistUnit:
    """Why: the allowlist is the single decision point for 'do we prompt?'.
    Risk covered: a classification bug either nags on trusted sources (UX) or,
    far worse, silently trusts a malicious source.
    """

    @pytest.mark.parametrize(
        "repo_url",
        [
            "https://github.com/google/adk-samples",
            "https://github.com/google/adk-python",
        ],
    )
    def test_first_party_sources_are_trusted(self, repo_url: str) -> None:
        # Expected outcome: True (no confirmation will be required).
        assert is_trusted_template_source(_spec(repo_url)) is True

    @pytest.mark.parametrize(
        "repo_url",
        [
            "https://github.com/someone/evil-template",
            "https://gitlab.com/acme/agent",
        ],
    )
    def test_third_party_sources_are_untrusted(self, repo_url: str) -> None:
        # Expected outcome: False (confirmation required).
        assert is_trusted_template_source(_spec(repo_url)) is False


class TestConfirmGateUnit:
    """Why: the confirmation gate is the user-facing security decision.
    Risk covered: a regression that skips the prompt, or prompts but proceeds
    regardless of the answer, would silently re-open the RCE path.
    """

    def test_trusted_source_does_not_prompt(self) -> None:
        # Expected outcome: Confirm.ask never called for a trusted source.
        with patch.object(rt, "Confirm") as mock_confirm:
            confirm_remote_template_trust(_spec("https://github.com/google/adk-samples"))
        mock_confirm.ask.assert_not_called()

    def test_untrusted_source_proceeds_on_yes(self) -> None:
        # Expected outcome: prompt shown; no exception when user confirms trust.
        with patch.object(rt, "Confirm") as mock_confirm:
            mock_confirm.ask.return_value = True
            confirm_remote_template_trust(_spec("https://github.com/someone/evil"))
        mock_confirm.ask.assert_called_once()

    def test_provenance_details_are_displayed(self) -> None:
        # Why: the user can only make an informed decision if source/ref/path are
        # shown. Risk: a blind prompt trains users to click 'yes' with no context.
        spec = _spec(
            "https://github.com/someone/evil", ref="v1.2.3", path="agents/foo"
        )
        with (
            patch.object(rt.Confirm, "ask", return_value=True),
            patch.object(rt.Console, "print") as mock_print,
        ):
            confirm_remote_template_trust(spec, original_agent_spec="someone/evil@v1.2.3")
        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        # Expected outcome: all provenance fields surfaced to the user.
        assert "evil" in printed
        assert "v1.2.3" in printed
        assert "agents/foo" in printed


class TestProvenanceHelperUnit:
    """Why: provenance (commit SHA) gives an auditable record of exactly what was
    rendered, and the hook warning flags executable template code.
    Risk covered: provenance lookup must NEVER crash a valid generation.
    """

    def test_logs_commit_sha(self, tmp_path) -> None:
        with (
            patch.object(rt.subprocess, "run") as mock_run,
            patch.object(rt.Console, "print") as mock_print,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                ["git", "rev-parse", "HEAD"], 0, stdout="abc123def\n", stderr=""
            )
            rt._log_template_provenance(tmp_path, {})
        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        # Expected outcome: resolved SHA echoed to the user.
        assert "abc123def" in printed

    def test_warns_when_hooks_present(self, tmp_path) -> None:
        (tmp_path / "hooks").mkdir()
        with (
            patch.object(rt.subprocess, "run") as mock_run,
            patch.object(rt.Console, "print") as mock_print,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                ["git", "rev-parse", "HEAD"], 0, stdout="sha\n", stderr=""
            )
            rt._log_template_provenance(tmp_path, {})
        printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        # Expected outcome: explicit warning about executable cookiecutter hooks.
        assert "hook" in printed.lower()


# =============================================================================
# 2. INTEGRATION TESTS — gate wired into fetch_remote_template()
# =============================================================================
def _fake_git(*args, **kwargs):
    """Simulate git: create the destination/template dirs and return SHAs.

    Lets fetch_remote_template() run end-to-end without touching the network.
    """
    cmd = args[0]
    if cmd[:2] == ["git", "clone"]:
        dest = rt.pathlib.Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    if cmd[:2] == ["git", "checkout"]:
        cwd = rt.pathlib.Path(kwargs.get("cwd", "."))
        # Materialize the requested template subdir so existence checks pass.
        (cwd / "agents" / "demo").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    if cmd[:2] == ["git", "rev-parse"]:
        return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr="")
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


class TestFetchIntegration:
    """Why: unit-testing the gate isn't enough — it must be *invoked* by the real
    fetch path, and BEFORE any clone. Risk covered: the gate exists but a caller
    bypasses it, or cloning happens before consent (network/exec already underway).
    """

    def test_decline_aborts_before_any_clone(self, tmp_path) -> None:
        spec = _spec("https://github.com/someone/evil", path="agents/demo")
        with (
            patch.object(rt.Confirm, "ask", return_value=False),
            patch.object(rt.subprocess, "run") as mock_run,
            patch.object(rt.tempfile, "mkdtemp", return_value=str(tmp_path / "asp")),
        ):
            with pytest.raises(click.Abort):
                fetch_remote_template(spec, "someone/evil")
        # Expected outcome: NOT a single git command ran — fail-closed ordering.
        mock_run.assert_not_called()

    def test_trusted_source_reaches_clone_without_prompt(self, tmp_path) -> None:
        spec = _spec("https://github.com/google/adk-samples", path="agents/demo")
        with (
            patch.object(rt.Confirm, "ask") as mock_ask,
            patch.object(rt.subprocess, "run", side_effect=_fake_git),
            patch.object(rt, "check_and_execute_with_version_lock", return_value=False),
            patch.object(rt.tempfile, "mkdtemp", return_value=str(tmp_path / "asp")),
        ):
            template_dir, temp_path = fetch_remote_template(spec, "adk@demo")
        # Expected outcome: no prompt for trusted source; clone proceeded.
        mock_ask.assert_not_called()
        assert template_dir.name == "demo"

    def test_auto_approve_reaches_clone_for_untrusted(self, tmp_path) -> None:
        spec = _spec("https://github.com/someone/evil", path="agents/demo")
        with (
            patch.object(rt.Confirm, "ask") as mock_ask,
            patch.object(rt.subprocess, "run", side_effect=_fake_git),
            patch.object(rt, "check_and_execute_with_version_lock", return_value=False),
            patch.object(rt.tempfile, "mkdtemp", return_value=str(tmp_path / "asp")),
        ):
            template_dir, _ = fetch_remote_template(
                spec, "someone/evil", auto_approve=True
            )
        # Expected outcome: documented bypass — no prompt, clone proceeds.
        mock_ask.assert_not_called()
        assert template_dir.exists()


# =============================================================================
# 3. NEGATIVE TESTS — malicious / failure inputs must be handled safely
# =============================================================================
class TestNegative:
    """Why: security controls are judged by how they behave under attack/failure.
    Risk covered: a declined or malformed source must never silently proceed, and
    provenance failures must not abort an otherwise-valid generation.
    """

    def test_untrusted_decline_raises_abort(self) -> None:
        with patch.object(rt.Confirm, "ask", return_value=False):
            # Expected outcome: explicit click.Abort, not a silent continue.
            with pytest.raises(click.Abort):
                confirm_remote_template_trust(_spec("https://github.com/evil/x"))

    def test_lookalike_fork_is_not_trusted(self) -> None:
        # Why: typosquat/fork is a classic supply-chain trick.
        # Expected outcome: treated as untrusted -> prompt required.
        assert is_trusted_template_source(
            _spec("https://github.com/google-fork/adk-samples")
        ) is False
        assert is_trusted_template_source(
            _spec("https://evil.com/google/adk-samples")
        ) is False

    def test_provenance_failure_is_non_fatal(self, tmp_path) -> None:
        # Risk: a flaky `git rev-parse` must not break generation.
        with (
            patch.object(rt.subprocess, "run", side_effect=OSError("git missing")),
            patch.object(rt.Console, "print"),
        ):
            # Expected outcome: no exception escapes.
            rt._log_template_provenance(tmp_path, {})

    def test_non_interactive_decline_cannot_be_coerced_to_proceed(self) -> None:
        # Why: ensure 'No' is the only safe path; a falsy answer must abort.
        with patch.object(rt.Confirm, "ask", return_value=None):
            with pytest.raises(click.Abort):
                confirm_remote_template_trust(_spec("https://github.com/evil/x"))


# =============================================================================
# 4. BOUNDARY TESTS — edges of the allowlist matching & optional inputs
# =============================================================================
class TestBoundary:
    """Why: allowlist matching is exact by design; boundary cases decide whether a
    near-match is (correctly) rejected. Risk covered: an over-loose match (trailing
    slash, scheme, casing, subpath) accidentally trusting a non-canonical URL.
    """

    @pytest.mark.parametrize(
        "repo_url",
        [
            "https://github.com/google/adk-samples/",          # trailing slash
            "http://github.com/google/adk-samples",            # http scheme
            "https://github.com/google/adk-samples-extra",     # superstring
            "https://github.com/google/ADK-SAMPLES",           # casing
            "github.com/google/adk-samples",                   # missing scheme
            "",                                                 # empty
        ],
    )
    def test_non_canonical_urls_are_untrusted(self, repo_url: str) -> None:
        # Expected outcome: only the exact canonical URL is trusted; all else prompts.
        assert is_trusted_template_source(_spec(repo_url)) is False

    def test_optional_args_default_safely(self) -> None:
        # Why: confirm gate is called with varying optional args across call sites.
        # Expected outcome: missing original_agent_spec / empty path still aborts on No.
        with patch.object(rt.Confirm, "ask", return_value=False):
            with pytest.raises(click.Abort):
                confirm_remote_template_trust(
                    _spec("https://github.com/evil/x", path=""),
                    original_agent_spec=None,
                )

    def test_auto_approve_is_the_only_non_interactive_bypass(self) -> None:
        # Boundary between 'prompt' and 'bypass': exactly auto_approve=True bypasses.
        spec = _spec("https://github.com/someone/evil")
        with patch.object(rt, "Confirm") as mock_confirm:
            confirm_remote_template_trust(spec, auto_approve=True)
        mock_confirm.ask.assert_not_called()  # Expected: bypassed, no prompt.

        with patch.object(rt.Confirm, "ask", return_value=False):
            with pytest.raises(click.Abort):  # Expected: auto_approve=False -> gate active.
                confirm_remote_template_trust(spec, auto_approve=False)

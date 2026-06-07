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

"""Security regression guards for the template-processing pipeline.

These are intentionally lightweight static guards: fully exercising
``process_template`` requires the whole base-template tree and temp-dir setup,
so instead we assert the security-relevant invariant directly at the call site.
The risk being guarded is that a future edit silently re-enables cookiecutter
hook execution, which would let a copied ``hooks/`` directory run code during
generation.
"""

import inspect

from agent_starter_pack.cli.utils import template


class TestCookiecutterHooksDisabled:
    """Why: cookiecutter executes pre/post-gen hooks by default. Risk covered:
    re-enabling hooks would create a code-execution path during generation.
    Expected outcome: the cookiecutter call opts out of hooks explicitly.
    """

    def test_process_template_disables_cookiecutter_hooks(self) -> None:
        source = inspect.getsource(template.process_template)
        # The cookiecutter invocation must pass accept_hooks=False.
        assert "accept_hooks=False" in source, (
            "process_template must call cookiecutter(..., accept_hooks=False) to "
            "prevent pre/post-gen hook execution during template generation."
        )

    def test_cookiecutter_is_invoked_with_no_input(self) -> None:
        # Sanity: generation stays non-interactive (no prompt-based side effects).
        source = inspect.getsource(template.process_template)
        assert "no_input=True" in source

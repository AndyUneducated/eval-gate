"""Judge runner package (Phase 5).

`runner.run_eval` is the entry point. Sub-modules:
- `prompt_spec`  : pydantic schema for prompt.yaml + load/render
- `candidate`    : LiteLLM call wrapper measuring latency / cost
- `rubric_judge` : RubricJudge -> 0..1 score + reason
- `persistence`  : eval_runs / eval_results repository
- `runner`       : iter_eval (stream) + run_eval (wrapper)
"""

from __future__ import annotations

import litellm as _litellm

# LiteLLM otherwise prints a colourised "Provider List: ..." banner to stdout
# on first call, which corrupts the JSON the CLI emits to stdout. Silence it.
_litellm.suppress_debug_info = True
_litellm.set_verbose = False

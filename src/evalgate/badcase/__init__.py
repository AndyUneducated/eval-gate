"""BadCase Finder (Phase 7).

Surfaces eval_results worth promoting into an eval_set as regression cases.
Three strategies, all reading existing Phase 5/6 columns on `eval_results`:

- ``uncertainty`` -> low `judge_confidence` (judge is unsure -> good study material)
- ``outlier``     -> bad / slow / expensive / unsafe (long tail + known failure)
- ``llm``         -> cheap-model classifier on top of `uncertainty` candidates

Read-mostly: the single writer ``promote_result_to_set`` inserts one row
into ``eval_case_set_memberships`` (Phase 4.5 + 7.5) — never duplicates
the underlying ``EvalCaseRow``.
"""

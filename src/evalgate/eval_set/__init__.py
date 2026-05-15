"""Eval set domain — managing reusable collections of (input, expected) cases.

Cases live in DB (`eval_cases` table) and are referenced by Phase 5+ judge
runners. The repository is the only module that talks to ORM rows directly;
the API router and CLI both go through it.
"""

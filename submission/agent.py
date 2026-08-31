"""Nominated submission entry point: exports the ``Agent`` the challenge requires.

This is a signpost, not a second implementation. The official harness imports the
class directly — ``evaluator/local_evaluator.py:12`` reads

    from starter.agent import Agent

so ``starter/agent.py`` is what actually runs, with or without this file. Importing
``Agent`` from here gives you the same object.

There is deliberately no copy of ``starter/`` under ``submission/src/``. The
recommended layout in ``docs/submission_rules.md`` suggests one, but a second copy
of the agent would be a copy the evaluator never imports: it could drift from the
running code without any test failing, and a reader comparing the two would have no
way to tell which one produced the reported score. One implementation, one place.

Run it with the command in ``submission/README.md``.
"""
from __future__ import annotations

from starter.agent import Agent

__all__ = ["Agent"]

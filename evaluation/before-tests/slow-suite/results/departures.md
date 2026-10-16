# Departures during the run (for the report)

- Batch 1 (w12, w23, w30, w26), started 05:21:15 UTC; hard stop 06:21:15 UTC.
- w26 (task 2, dumb hook) and w30 (task 2, AMU arm) each blocked on a permission prompt for a `sed -i` command (w26 at
  05:34:51, w30 shortly before). Both prompts were answered at 06:58:57 to 06:59:05 UTC, about 38 minutes after the
  hard stop. Every `sed -i` failed (BSD sed) and changed nothing; w26's `rm` of its own temporary test file ran. After
  the answers, w26 made two Edits and one `./rt` run and ended. w30 made one Edit and one `./rt` run and is waiting on
  a second prompt.
- Scored trees are the trees at the hard stop: the post-stop Edits are reversed, and w26's temporary test file is
  restored from the agent's own heredoc. The trees after the answers are kept as `agent-final.diff`. Ledger rows after
  the hard stop carry `after_hard_stop: true` and do not count as runs before declaring. Both runs are marked
  `stopped_at_hard_cap`, with the answer time in `result.json`.
- w30's session was not stopped by the coordinating session; the app reserves a waiting session to the user. If its
  tree changes again, the scored tree is rebuilt as parent + `runs/w30/agent.diff` (saved at the hard stop).
- 2026-10-15: the owner said not to wait for "next" between batches; batches from batch 2 on start when the previous batch has ended.
- Batch 2: w18 and w25 (both dumb hook) waited on permission prompts that were answered within the hard stop; marked permission_prompt_answered in result.json.
- From batch 3 on, each batch starts on an owner message (the app only starts sessions on the owner's own turn).
- Batch 3, w19 (task 5, AMU arm): an AMU defect used the run's whole test budget. The first check after the agent
  added `raise TypeError` to `ArrayAdd.__new__` ran 184 s, until the hook's outer timeout (the budget left) stopped
  it. Every later check was skipped for budget, and the agent could run no test. Cause, from a rerun on a copy of
  w19's final tree with Python stack dumps: `consequences.handlers` (amu/core/consequences.py:76-90) enumerates
  every caller path up to MAX_HOPS for a newly added `raise`, with a per-path `seen` set and no memo. On sympy's
  dense caller graph of `ArrayAdd.__new__` that is exponential: the static phase ran over 10 minutes without
  finishing. It runs before the shadow run, outside the 60 s per-side budget. No engine change was made: the trial
  rule freezes `amu/` while sessions are live. Of the other five AMU-arm runs so far, none had a check stopped this
  way (longest 66.5 s). Task 5's trial-2 AMU run (w13) is likely to hit it again.
- The owner chose to continue after the w19 defect, with amu/ unchanged (2026-10-15).
- Batch 4: w28 (dumb hook) waited on a permission prompt that was answered within the hard stop; marked.
- Batch 5: w05 (AMU) and w10 (dumb hook) waited on permission prompts answered within the hard stop; marked.
- Batch 6: w03 and w21 (both dumb hook) waited on permission prompts answered within the hard stop; marked.
- Batch 7: w17 (dumb hook) waited on a permission prompt answered within the hard stop; marked.
- Batch 7: w13 (AMU), w24 and w17 (dumb hook) waited on permission prompts answered within the hard stop; marked.
- Batch 7, w13 (task 5, AMU arm): the same defect as w19. The first check after the agent's `ArrayAdd` edit ran 184 s
  and was stopped by the hook's outer timeout, which spent the whole budget. The agent ran no test.
- Batch 8: w14 (AMU) waited on a permission prompt answered within the hard stop; marked.
- Batch 8: w01 (AMU) and w02 (dumb hook) waited on permission prompts answered within the hard stop; marked.
- Batch 3: w19 (AMU) also waited on a permission prompt answered within the hard stop; it was missed at snapshot time and marked after scoring.

# Rules for any agent trial with shadow execution on

Written 2026-10-13, before any such trial has started. A trial that does not follow these is not evidence.

1. **Same wall-clock budget for both arms.** The budget is time, not a count of test runs. It is fixed per
   task before the first agent starts and written into the trial record next to the task list.
2. **Shadow execution is inside the budget.** Every second AMU spends running tests, building its index or
   printing its packet counts against the AMU arm's budget. The arm without AMU may spend the same time
   running tests any way it likes, including whole modules.
3. **Default budget: 180 seconds per task**, unless the record states another figure and why. Index time on
   a cold tree is charged to the AMU arm.
4. **Same model, same prompt, same tree, fresh context.** The only difference is the tool paragraph.
5. **Ground truth is frozen first**, by script, from execution on the parent and patched trees.
6. **A debugging run is labelled as one** and is never reported as a trial result.
7. **Every agent claim of a failing test is verified** by running it on both trees before it is counted.

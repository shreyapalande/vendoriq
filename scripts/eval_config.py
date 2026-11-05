"""Settings shared by the eval data tests and the eval script (scripts/run_eval.py).

Eval-only: production code never imports this module.
"""

from datetime import date

# The fixed "as of" date for judging whether evidence is current (see evals/LABELING.md:
# an audit period that ended more than 12 months before this date, or a certificate
# expired on this date, is outdated). Fixed, so the expected labels don't change as
# real time passes.
#
# M1b (not implemented yet): analyze() takes an `as_of: date` parameter and the prompt
# says "Assess whether evidence is current as of {as_of}". Production passes today's
# date; the eval passes EVAL_AS_OF.
EVAL_AS_OF = date(2026, 11, 1)

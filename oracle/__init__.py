"""Exam Oracle — generic, input-driven loader scripts (role A).

Every command reads config from the environment, prints one JSON object to
stdout, and uses documented exit codes so Rote can tell a hard fault from a
degraded-but-ok step:

  0  ok (possibly with a "warning" field = degraded)
  1  hard fault (HTTP / load / service error)
  2  skip-listed (sealed) URL or file encountered
  3  validation failed (numbering or point totals)
  4  leakage (reserved for run-backtest)
"""

"""Logistics intake: an extraction preset built the same way a real project builds one.

The domain lives here, the mechanism lives in :mod:`agentqa`. Reading this package in order
(``schema`` → ``checks`` → ``preset`` → ``agent`` → ``runner``) is the fastest way to see
what "bringing your own domain" actually costs: a schema, a handful of rules, a rubric, a
prompt and a scoring spec.
"""

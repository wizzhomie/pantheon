"""Project context: what a repository *is*, so verification can be planned against it.

The core in :mod:`qagent.runtime` and :mod:`qagent.evals` knows nothing about projects. The
verification loop needs to know three things before it can be useful:

* what the project is made of (languages, layout, entry points);
* how it is built and checked (test runner, linter, type checker, package manager);
* where it promises to go (requirements, invariants, milestones) — that layer comes later.

This package starts with the first two, deterministically: files and manifests on disk, no
model, no network, no guesswork. Everything here is data, versioned with the project that
describes it (``.qagent/profile.json``), so a reviewer can read why a check was planned.
"""

from __future__ import annotations

from .detect import detect_profile
from .profile import ProfileTool, ProjectProfile, profile_path, read_profile, write_profile

__all__ = [
    "ProfileTool",
    "ProjectProfile",
    "detect_profile",
    "profile_path",
    "read_profile",
    "write_profile",
]

"""Typed errors for the pod tool, so the CLI can map them to clean exit codes."""

from __future__ import annotations


class PodToolError(Exception):
    """Base class for all expected, user-facing tool errors."""


class ConfigError(PodToolError):
    """Missing or invalid configuration (.env / env / runpodctl config)."""


class ResolveError(PodToolError):
    """The pod has no usable direct-TCP host/port (proxy-only, not ready, or lookup failed)."""

"""Cross-cutting infrastructure shared by independently deployable services."""

from .observability import instrument, metrics_app

__all__ = ["instrument", "metrics_app"]

"""
Per-source scrapers live in :mod:`collect_data.pipeline` (single module for now).

This package exists so we can split ``pipeline.py`` into one file per source
without changing import paths for callers that use ``collect_data.collectors``.
"""

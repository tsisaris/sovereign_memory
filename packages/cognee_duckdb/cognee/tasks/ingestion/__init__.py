"""
Data ingestion and normalization tasks.

This module provides functionality for accepting raw data inputs
(files, URLs, streams, or external objects), transforming them into
standardized text representations, and registering them within
datasets and the relational database.
"""

from .ingest_data import ingest_data
from .migrate_relational_database import migrate_relational_database
from .resolve_data_directories import resolve_data_directories
from .resolve_dlt_sources import resolve_dlt_sources
from .save_data_item_to_storage import save_data_item_to_storage

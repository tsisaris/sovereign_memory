"""
Custom exceptions for the Cognee API.

This module defines a set of exceptions for handling various database errors
"""

from .exceptions import (
    CacheConnectionError,
    DatabaseCredentialsError,
    DatabaseNotCreatedError,
    EmbeddingContextWindowTooSmallError,
    EmbeddingCredentialsError,
    EmbeddingEncodingError,
    EmbeddingException,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    MissingQueryParameterError,
    MutuallyExclusiveQueryParametersError,
    Neo4jMultiDatabaseSupportError,
    SessionParameterValidationError,
    SessionQAEntryValidationError,
    TursoDatabaseInUseError,
    UnsupportedGraphOperation,
    UnsupportedProvenanceCapability,
)

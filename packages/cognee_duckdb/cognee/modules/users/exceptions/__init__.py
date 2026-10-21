"""
Custom exceptions for the Cognee API.

This module defines a set of exceptions for handling various user errors
"""

from .exceptions import (
    CapabilityDeniedError,
    CapabilityGrantToNonMemberError,
    CapabilityNotFoundError,
    PermissionDeniedError,
    PermissionNotFoundError,
    RoleNotFoundError,
    TenantNotFoundError,
    UserNotFoundError,
)

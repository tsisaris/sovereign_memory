"""Visibility policies: the owner's answer to "what may this authenticated principal see?".

Only two profiles are built in, and neither invents a grant. Restricted is the
fail-closed default: a principal sees only what it produced. ScopeLabelPolicy
puts principals whose authenticator-supplied scope label is explicitly listed
into the shared pool (the Sovereign universal pool); every other principal
stays restricted. Grants from a permission owner (users, tenants, dataset
permissions) need a VisibilityPolicy supplied by that owner; none exists here.
"""

from service.memory_processor_seam import AuthenticatedPrincipal, Visibility


class RestrictedPolicy:
    """Every principal sees only the events it produced; never widens."""

    async def resolve(self, principal: AuthenticatedPrincipal) -> Visibility:
        """Return own-events-only visibility for any principal."""
        return Visibility("own")


class ScopeLabelPolicy:
    """Shared-pool visibility for explicitly listed scope labels, restricted otherwise."""

    def __init__(self, pool_scope_labels: frozenset[str]) -> None:
        """Remember which authenticator scope labels are placed in the shared pool."""
        self.pool_scope_labels = pool_scope_labels

    async def resolve(self, principal: AuthenticatedPrincipal) -> Visibility:
        """Return pool visibility for a listed scope label, own-events-only for all others."""
        return Visibility("pool" if principal.scope in self.pool_scope_labels else "own")

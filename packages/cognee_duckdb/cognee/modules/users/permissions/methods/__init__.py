"""Collect the permission functions in one place so callers can import them from this package.

Goes in: nothing; comes out: each function named below as an attribute of the package. It
never runs a query by itself.
"""


def __getattr__(name):
    """Give check_permission_on_dataset the function, not the module, of the same name.

    Goes in: the name of an attribute the package does not have yet; comes out: the function
    get_specific_user_permission_datasets, which is then also stored as that attribute. The
    imports below are in alphabetical order, so check_permission_on_dataset is imported before
    the function exists as an attribute, and Python would hand it the module of that name and
    make its permission check fail with "module object is not callable". It never answers any
    other name.
    """
    if name == "get_specific_user_permission_datasets":
        from .get_specific_user_permission_datasets import (
            get_specific_user_permission_datasets as function,
        )

        globals()[name] = function
        return function
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .authorized_get_principal_datasets import authorized_get_principal_datasets
from .authorized_give_permission_on_datasets import authorized_give_permission_on_datasets
from .authorized_revoke_permission_on_datasets import authorized_revoke_permission_on_datasets
from .check_permission_on_dataset import check_permission_on_dataset
from .get_all_user_permission_datasets import get_all_user_permission_datasets
from .get_document_ids_for_user import get_document_ids_for_user
from .get_effective_capabilities import get_effective_capabilities
from .get_permitted_dataset_ids import get_permitted_dataset_ids
from .get_principal import get_principal
from .get_principal_datasets import get_principal_datasets
from .get_readable_datasets import get_readable_datasets
from .get_role import get_role
from .get_specific_user_permission_datasets import get_specific_user_permission_datasets
from .get_tenant import get_tenant
from .give_permission_on_dataset import give_permission_on_dataset
from .has_grant_permission import has_grant_permission
from .has_user_management_permission import has_user_management_permission
from .require_tenant_membership import require_tenant_membership

__all__ = [
    "authorized_get_principal_datasets",
    "authorized_give_permission_on_datasets",
    "authorized_revoke_permission_on_datasets",
    "check_permission_on_dataset",
    "get_all_user_permission_datasets",
    "get_document_ids_for_user",
    "get_effective_capabilities",
    "get_permitted_dataset_ids",
    "get_principal",
    "get_principal_datasets",
    "get_readable_datasets",
    "get_role",
    "get_specific_user_permission_datasets",
    "get_tenant",
    "give_permission_on_dataset",
    "has_grant_permission",
    "has_user_management_permission",
    "require_tenant_membership",
]

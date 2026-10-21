"""Supply an owned session only when an asynchronous caller has not provided one."""

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .get_async_session import get_async_session


def get_session_from_args(args):
    """Return a session passed in the final positional argument, when present."""
    if not args:
        return None
    last_arg = args[-1]
    if isinstance(last_arg, AsyncSession):
        return last_arg
    return None


def with_async_session(func: Callable[..., Any]) -> Callable[..., Any]:
    """Commit only sessions created by this decorator, leaving borrowed sessions owned."""

    async def wrapper(*args, **kwargs):
        """Invoke the operation with its borrowed session or a scoped owned session."""
        session: AsyncSession | None = kwargs.get("session") or get_session_from_args(args)

        if session is None:
            async with get_async_session() as session:
                result = await func(*args, **kwargs, session=session)
                await session.commit()
                return result
        else:
            return await func(*args, **kwargs)

    return wrapper

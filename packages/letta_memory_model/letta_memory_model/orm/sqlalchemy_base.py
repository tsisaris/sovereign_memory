"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive


class SqlalchemyBase:
    """Class docstring."""

    __abstract__ = True

    @property
    def __pydantic_model__(self):
        """Get pydantic model."""
        raise NotImplementedError()

    def to_pydantic(self):
        """Convert to pydantic."""
        model = self.__pydantic_model__.model_validate(self, from_attributes=True)
        return model

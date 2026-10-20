"""Helpers for Cognee data points: a JSON encoder for dates, UUIDs and decimals, a function that copies a model with chosen fields, and one that lists a data point's own scalar properties.

They are pure helpers: they touch no database and no file.
"""

import copy
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_core import PydanticUndefined

from cognee.infrastructure.engine import DataPoint


class JSONEncoder(json.JSONEncoder):
    """A JSON encoder that also writes dates and times as ISO text, UUIDs as strings and decimals as numbers.

    It converts values only and writes nothing.
    """

    def default(self, obj):
        """Convert one value json cannot encode by itself (a date, time, UUID or decimal).

        Goes in: the value; comes out: its JSON-ready form. It defers to the standard encoder for
        other types, which raises TypeError.
        """
        if isinstance(obj, datetime):
            return obj.isoformat()  # Convert datetime to ISO 8601 string
        elif isinstance(obj, date):
            return obj.isoformat()
        elif isinstance(obj, UUID):
            # if the obj is uuid, we simply return the value of uuid
            return str(obj)
        elif isinstance(obj, Decimal):
            return float(obj)
        return json.JSONEncoder.default(self, obj)


def copy_model(
    model: DataPoint, include_fields: dict | None = None, exclude_fields: list | None = None
):
    """Make a copy of a data point model with some fields included or excluded.

    Goes in: the model class, an optional dict of fields to include and an optional list to exclude;
    comes out: a new model class. It changes nothing in the original.
    """
    if include_fields is None:
        include_fields = {}
    if exclude_fields is None:
        exclude_fields = []
    fields = {}
    for name, field in model.model_fields.items():
        if name in exclude_fields:
            continue

        if hasattr(field, "is_required") and field.is_required():
            fields[name] = (field.annotation, PydanticUndefined)
        elif field.default_factory is not None:
            fields[name] = (field.annotation, Field(default_factory=field.default_factory))
        else:
            # Preserve explicit None defaults (and other scalar/container defaults)
            # so optional fields remain optional in copied models.
            fields[name] = (field.annotation, copy.deepcopy(field.default))

    final_fields = {**fields, **include_fields}

    # Create a base class with the same configuration as DataPoint
    class ConfiguredBase(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

    # Create the model inheriting from the configured base
    new_model = create_model(model.__name__, __base__=ConfiguredBase, **final_fields)

    new_model.model_rebuild()
    return new_model


def get_own_properties(data_point: DataPoint):
    """List the plain properties of a data point, leaving out its metadata, nested data points and containers of them.

    Goes in: a data point; comes out: a dict of property names to values. It only reads.
    """
    properties = {}

    for field_name, field_value in data_point:
        if (
            field_name == "metadata"
            or isinstance(field_value, (dict, DataPoint))
            or (
                isinstance(field_value, list)
                and len(field_value) > 0
                and isinstance(field_value[0], DataPoint)
            )
        ):
            continue

        properties[field_name] = field_value

    return properties

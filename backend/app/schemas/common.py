"""Shared value types used by the process, assessment and delivery schemas.

Everything here is deliberately small and boring. These types are handed to the
model as part of a JSON Schema contract, so each one needs to be unambiguous to
something that has never seen our codebase.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# A short machine-readable identifier. We ask the model for these rather than
# letting it use free text, so edges can reference steps reliably.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Base(BaseModel):
    """Base for every schema model.

    `extra="forbid"` matters more than it looks: when the model invents a field
    we want a loud validation error we can feed straight back to it, not a
    silently dropped key.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SystemCategory(StrEnum):
    EMAIL = "email"
    SPREADSHEET = "spreadsheet"
    DATABASE = "database"
    CRM = "crm"
    CHAT = "chat"
    CALENDAR = "calendar"
    FILE_STORAGE = "file_storage"
    ACCOUNTING = "accounting"
    FORMS = "forms"
    WEBSITE = "website"
    PHONE_OR_SMS = "phone_or_sms"
    PAYMENTS = "payments"
    INTERNAL_TOOL = "internal_tool"
    PAPER_OR_OFFLINE = "paper_or_offline"
    OTHER = "other"


class System(Base):
    """An application or place where work happens."""

    id: Slug
    name: str = Field(description="What the person called it, e.g. 'Gmail', 'the shared drive'.")
    category: SystemCategory
    notes: str | None = Field(
        default=None,
        description="Anything that affects how hard this is to automate, e.g. 'no API, staff log in manually'.",
    )


class DataType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    MONEY = "money"
    DATE = "date"
    BOOLEAN = "boolean"
    EMAIL_ADDRESS = "email_address"
    PHONE_NUMBER = "phone_number"
    FILE = "file"
    RECORD = "record"
    LIST = "list"


class DataItem(Base):
    """A piece of information moving between steps."""

    name: str = Field(description="Short label, e.g. 'invoice amount'.")
    data_type: DataType
    description: str | None = None
    contains_personal_data: bool = Field(
        default=False,
        description=(
            "True if this identifies or relates to a living person — names, contact "
            "details, addresses, anything tied to an individual. Drives the controls layer."
        ),
    )


class ComparisonOperator(StrEnum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"


class Threshold(Base):
    """A machine-checkable condition, e.g. invoice total above £5,000.

    `value` is always a string with `value_type` saying how to read it. A tagged
    string is far more reliable to get out of a model than a polymorphic field,
    and we only have to parse it in one place.
    """

    field: str = Field(description="Dotted path to the value being tested, e.g. 'invoice.total'.")
    operator: ComparisonOperator
    value: str | None = Field(
        default=None,
        description="Omitted only for is_empty / is_not_empty.",
    )
    value_type: DataType = DataType.TEXT
    currency: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 code. Required when value_type is money. Default GBP.",
    )

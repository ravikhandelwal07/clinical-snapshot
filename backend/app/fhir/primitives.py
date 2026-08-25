"""Permissive models for the FHIR primitives this application understands.

Two rules govern this module:

1. **Never raise on bad input.** A snapshot that 500s because one coding is
   malformed is less useful -- and arguably less safe -- than one that renders
   what it understood and reports the rest. Parsers here return a value that
   records the failure instead of raising.
2. **Never interpret.** Nothing here decides what a code *means* or whether a
   resource is safe to present as current clinical fact. Those decisions live in
   ``app.normalize`` so every clinical rule sits in one auditable place.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, computed_field

UTC = timezone.utc

_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


class DatePrecision(str, Enum):
    """How precise a FHIR date/dateTime actually was in the source."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    INSTANT = "instant"
    UNKNOWN = "unknown"  # present in the source but not parseable


_RE_YEAR = re.compile(r"^(\d{4})$")
_RE_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_RE_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RE_INSTANT = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ]"
    r"(\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?$"
)

# Unparseable dates sort to the far past so they can never masquerade as the
# most recent value in a "latest wins" comparison.
_UNKNOWN_SORT_KEY = datetime(1, 1, 1, tzinfo=UTC)


class PartialDateTime(BaseModel):
    """A FHIR date/dateTime that remembers how precise it actually was.

    FHIR permits ``2019``, ``2019-06``, ``2019-06-02`` and full instants in the
    same element. Collapsing them all to a ``datetime`` silently invents
    precision the source never had -- e.g. turning a year-only birth date into
    1 January, which then produces a confidently wrong age. We keep the raw
    string, the detected precision, and a sort key used *only* for ordering.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    precision: DatePrecision
    sort_key: datetime
    #: Source wrote ``T00:00:00Z``. Almost always a date that a sending system
    #: padded to an instant, not a genuine midnight event.
    midnight_utc_padded: bool = False

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, value: Any) -> Optional["PartialDateTime"]:
        """Best-effort parse. ``None`` only if the element was truly absent."""
        if value is None:
            return None
        if isinstance(value, PartialDateTime):
            return value
        if isinstance(value, datetime):
            text = value.isoformat()
        elif isinstance(value, date):
            text = value.isoformat()
        else:
            text = str(value).strip()
        if not text:
            return None

        if m := _RE_YEAR.match(text):
            return cls(
                raw=text,
                precision=DatePrecision.YEAR,
                sort_key=datetime(int(m.group(1)), 1, 1, tzinfo=UTC),
            )
        if m := _RE_MONTH.match(text):
            return cls(
                raw=text,
                precision=DatePrecision.MONTH,
                sort_key=datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=UTC),
            )
        if m := _RE_DAY.match(text):
            return cls(
                raw=text,
                precision=DatePrecision.DAY,
                sort_key=datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC
                ),
            )
        if m := _RE_INSTANT.match(text):
            year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
            second = int(m.group(6) or 0)
            offset = _parse_offset(m.group(7))
            stamp = datetime(year, month, day, hour, minute, second, tzinfo=offset)
            return cls(
                raw=text,
                precision=DatePrecision.INSTANT,
                sort_key=stamp.astimezone(UTC),
                midnight_utc_padded=(
                    (hour, minute, second) == (0, 0, 0) and offset == UTC
                ),
            )

        return cls(raw=text, precision=DatePrecision.UNKNOWN, sort_key=_UNKNOWN_SORT_KEY)

    # ----------------------------------------------------------- derived view
    @computed_field  # type: ignore[prop-decorator]
    @property
    def display(self) -> str:
        """Human string that never implies more precision than we have."""
        if self.precision is DatePrecision.UNKNOWN:
            return self.raw
        if self.precision is DatePrecision.YEAR:
            return self.sort_key.strftime("%Y")
        if self.precision is DatePrecision.MONTH:
            return f"{_MONTH_ABBR[self.sort_key.month - 1]} {self.sort_key.year}"
        day_text = (
            f"{self.sort_key.day:02d} "
            f"{_MONTH_ABBR[self.sort_key.month - 1]} {self.sort_key.year}"
        )
        if self.precision is DatePrecision.DAY or self.midnight_utc_padded:
            return day_text
        return f"{day_text} {self.sort_key:%H:%M} UTC"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_imprecise(self) -> bool:
        return self.precision in (
            DatePrecision.YEAR,
            DatePrecision.MONTH,
            DatePrecision.UNKNOWN,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def precision_note(self) -> Optional[str]:
        """Short caption the UI puts next to an imprecise date."""
        if self.precision is DatePrecision.UNKNOWN:
            return "Date could not be interpreted; shown exactly as recorded."
        if self.precision is DatePrecision.YEAR:
            return "Year only — month and day were not recorded."
        if self.precision is DatePrecision.MONTH:
            return "Month only — day was not recorded."
        if self.midnight_utc_padded:
            return "Source recorded 00:00:00Z; treated as a date, not a time of day."
        return None

    # ------------------------------------------------------------- utilities
    def bounds(self) -> tuple[datetime, datetime]:
        """Earliest and latest instant this value could refer to."""
        if self.precision is DatePrecision.UNKNOWN:
            return _UNKNOWN_SORT_KEY, _UNKNOWN_SORT_KEY
        start = self.sort_key
        if self.precision is DatePrecision.YEAR:
            return start, datetime(start.year, 12, 31, 23, 59, 59, tzinfo=UTC)
        if self.precision is DatePrecision.MONTH:
            next_month = (
                datetime(start.year + 1, 1, 1, tzinfo=UTC)
                if start.month == 12
                else datetime(start.year, start.month + 1, 1, tzinfo=UTC)
            )
            return start, next_month
        if self.precision is DatePrecision.DAY or self.midnight_utc_padded:
            return start, start.replace(hour=23, minute=59, second=59)
        return start, start

    def __lt__(self, other: "PartialDateTime") -> bool:  # pragma: no cover - trivial
        return self.sort_key < other.sort_key


def _parse_offset(raw: Optional[str]) -> timezone:
    """FHIR instants should carry an offset. Missing one is assumed UTC."""
    if not raw or raw in ("Z", "z"):
        return UTC
    sign = 1 if raw[0] == "+" else -1
    body = raw[1:].replace(":", "")
    hours, minutes = int(body[:2]), int(body[2:4])
    from datetime import timedelta

    return timezone(sign * timedelta(hours=hours, minutes=minutes))


#: Annotated alias so resource models can declare precision-aware dates.
FhirDateTime = Annotated[Optional[PartialDateTime], BeforeValidator(PartialDateTime.parse)]


class AgeEstimate(BaseModel):
    """Age derived from a possibly-imprecise birth date."""

    years: Optional[int] = None
    is_approximate: bool = False
    low: Optional[int] = None
    high: Optional[int] = None
    display: str
    note: Optional[str] = None

    @classmethod
    def unknown(cls, reason: str = "Birth date not recorded.") -> "AgeEstimate":
        return cls(display="Unknown", note=reason)

    @classmethod
    def from_birth_date(
        cls, birth: Optional[PartialDateTime], as_of: datetime
    ) -> "AgeEstimate":
        if birth is None:
            return cls.unknown()
        if birth.precision is DatePrecision.UNKNOWN:
            return cls.unknown(
                f"Birth date {birth.raw!r} could not be interpreted; age not calculated."
            )

        earliest, latest = birth.bounds()
        high = _full_years(earliest, as_of)  # born earliest -> oldest
        low = _full_years(latest, as_of)  # born latest -> youngest

        if low == high:
            return cls(years=high, low=low, high=high, display=f"{high} y")
        return cls(
            years=high,
            is_approximate=True,
            low=low,
            high=high,
            display=f"{low}–{high} y",
            note=(
                "Birth date precision is "
                f"{birth.precision.value} only, so age is a range."
            ),
        )


def _full_years(start: datetime, end: datetime) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


# ---------------------------------------------------------------------------
# Complex-type stubs. ``extra="ignore"`` is deliberate: we accept elements we
# do not model rather than rejecting the resource that carries them.
# ---------------------------------------------------------------------------
class FhirElement(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Coding(FhirElement):
    system: Optional[str] = None
    code: Optional[str] = None
    display: Optional[str] = None
    version: Optional[str] = None


class CodeableConcept(FhirElement):
    coding: list[Coding] = Field(default_factory=list)
    text: Optional[str] = None

    @property
    def first(self) -> Optional[Coding]:
        return self.coding[0] if self.coding else None

    def code_value(self) -> Optional[str]:
        """The code of the first coding, e.g. ``active``."""
        return self.coding[0].code if self.coding else None


class Reference(FhirElement):
    reference: Optional[str] = None
    resource_type_hint: Optional[str] = Field(default=None, alias="type")
    display: Optional[str] = None
    identifier: Optional[dict[str, Any]] = None

    @property
    def key(self) -> Optional[str]:
        """``Patient/patient-001`` -> ``Patient/patient-001`` (relative form)."""
        if not self.reference:
            return None
        ref = self.reference.strip()
        if ref.startswith("urn:") or ref.startswith("#"):
            return ref
        # Absolute URLs: keep only the trailing Type/id pair.
        parts = [p for p in ref.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return ref

    @property
    def resource_type(self) -> Optional[str]:
        key = self.key
        if key and "/" in key and not key.startswith("urn:"):
            return key.split("/")[0]
        return self.resource_type_hint

    @property
    def resource_id(self) -> Optional[str]:
        key = self.key
        if key and "/" in key and not key.startswith("urn:"):
            return key.split("/")[-1]
        return None


class Identifier(FhirElement):
    use: Optional[str] = None
    system: Optional[str] = None
    value: Optional[str] = None
    type: Optional[CodeableConcept] = None


class HumanName(FhirElement):
    use: Optional[str] = None
    family: Optional[str] = None
    given: list[str] = Field(default_factory=list)
    prefix: list[str] = Field(default_factory=list)
    suffix: list[str] = Field(default_factory=list)
    text: Optional[str] = None


class ContactPoint(FhirElement):
    system: Optional[str] = None
    value: Optional[str] = None
    use: Optional[str] = None
    rank: Optional[int] = None


class Address(FhirElement):
    use: Optional[str] = None
    line: list[str] = Field(default_factory=list)
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = Field(default=None, alias="postalCode")
    country: Optional[str] = None
    text: Optional[str] = None


class Quantity(FhirElement):
    value: Optional[float] = None
    unit: Optional[str] = None
    system: Optional[str] = None
    code: Optional[str] = None
    comparator: Optional[str] = None


class Period(FhirElement):
    start: FhirDateTime = None
    end: FhirDateTime = None


class Extension(FhirElement):
    """Only the shapes we need: nested extensions, coding and string values."""

    url: Optional[str] = None
    extension: list["Extension"] = Field(default_factory=list)
    value_coding: Optional[Coding] = Field(default=None, alias="valueCoding")
    value_string: Optional[str] = Field(default=None, alias="valueString")
    value_code: Optional[str] = Field(default=None, alias="valueCode")

    def child(self, url: str) -> Optional["Extension"]:
        return next((e for e in self.extension if e.url == url), None)


Extension.model_rebuild()


class Meta(FhirElement):
    profile: list[str] = Field(default_factory=list)
    last_updated: FhirDateTime = Field(default=None, alias="lastUpdated")
    source: Optional[str] = None


def format_quantity(quantity: Optional[Quantity]) -> Optional[str]:
    """``138 mmHg``. Keeps the source unit verbatim -- we never convert units."""
    if quantity is None or quantity.value is None:
        return None
    value = quantity.value
    number = f"{int(value)}" if float(value).is_integer() else f"{value:g}"
    prefix = quantity.comparator or ""
    unit = quantity.unit or quantity.code
    return f"{prefix}{number} {unit}".strip() if unit else f"{prefix}{number}"

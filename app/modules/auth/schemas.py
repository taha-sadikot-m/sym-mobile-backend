import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.password_rules import problems

Experience = Literal["beginner", "intermediate", "advanced"]

USERNAME = re.compile(r"^[a-z0-9_][a-z0-9_.]{2,23}$")
RESERVED_USERNAMES = {
    "admin",
    "administrator",
    "sym",
    "symedu",
    "support",
    "help",
    "moderator",
    "official",
    "root",
}


def clean_username(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lstrip("@").lower()
    if not value:
        return None
    if not USERNAME.match(value):
        raise ValueError("Use 3–24 letters, numbers, dots or underscores.")
    if value in RESERVED_USERNAMES:
        raise ValueError("That username is reserved.")
    return value


def birth_year_from_age(age: int | None) -> int | None:
    return date.today().year - age if age is not None else None


class SignUpIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    username: str | None = None
    age: int | None = Field(None, ge=8, le=100)
    institution: str = Field("", max_length=160)
    city: str = Field("", max_length=80)
    country: str = Field("", max_length=80)
    mun_experience: Experience | None = None

    _username = field_validator("username")(clean_username)

    @model_validator(mode="after")
    def _password(self) -> Self:
        issue = problems(self.password, email=str(self.email), name=self.full_name)
        if issue:
            raise ValueError(issue)
        return self


class SignInIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class EmailIn(BaseModel):
    email: EmailStr


class CodeIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResetIn(CodeIn):
    new_password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _password(self) -> Self:
        issue = problems(self.new_password, email=str(self.email))
        if issue:
            raise ValueError(issue)
        return self


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_in: int


class PendingOut(BaseModel):
    """Sign-up worked; the student must enter the code emailed to them."""

    status: str = "verification_required"
    email: str


class AcceptedOut(BaseModel):
    status: str = "ok"
    message: str

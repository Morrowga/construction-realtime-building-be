# app/schemas/contact.py
from pydantic import BaseModel, EmailStr, Field


class ContactRequest(BaseModel):
    email: EmailStr
    description: str = Field(min_length=1, max_length=5000)


class ContactResponse(BaseModel):
    message: str
from typing import Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    permissions: list[str]


class UserCreate(BaseModel):
    username: str
    password: str
    permissions: list[str] = []


class UserOut(BaseModel):
    id: int
    username: str
    is_active: bool
    permissions: list[str]

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    key: str
    value: str


class FileContent(BaseModel):
    path: str
    content: str

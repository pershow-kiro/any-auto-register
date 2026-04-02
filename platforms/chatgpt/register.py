"""兼容旧模块路径，转发到新的 refresh token 注册引擎实现。"""

from .refresh_token_registration_engine import (
    RefreshTokenRegistrationEngine,
    RegistrationEngine,
    RegistrationResult,
)

__all__ = [
    "RefreshTokenRegistrationEngine",
    "RegistrationEngine",
    "RegistrationResult",
]

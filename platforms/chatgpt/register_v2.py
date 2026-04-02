"""兼容旧模块路径，转发到新的 access-token-only 注册引擎实现。"""

from .access_token_only_registration_engine import (
    AccessTokenOnlyRegistrationEngine,
    RecoverableRegistrationError,
    RegistrationEngineV2,
)
from .refresh_token_registration_engine import RegistrationResult

__all__ = [
    "AccessTokenOnlyRegistrationEngine",
    "RecoverableRegistrationError",
    "RegistrationEngineV2",
    "RegistrationResult",
]

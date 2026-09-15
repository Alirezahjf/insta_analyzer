"""igclient — لایه‌ی حرفه‌ای روی instagrapi."""
from .client import IGClient, LoginMethod, AuthenticationError
from .config import Settings

__all__ = ["IGClient", "LoginMethod", "AuthenticationError", "Settings"]

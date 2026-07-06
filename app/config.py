"""Re-export of the settings surface (the definition lives in shared/config.py).

App modules keep importing ``app.config``; core imports ``shared.config``
directly so the dependency direction stays app -> core -> shared.
"""

from shared.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]

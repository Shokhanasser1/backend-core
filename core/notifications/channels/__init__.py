"""Notification channel adapters (interfaces §4.2) + their construction.

Platform channels are built once from env (dormant when unconfigured); per-tenant
channels are built on demand from a decrypted notification_settings config. Both
share the same config key shape (channel_config.CHANNEL_CONFIG_FIELDS).
"""

from collections.abc import Mapping

from core.notifications.channels.email import EmailChannel
from core.notifications.channels.eskiz import EskizSmsChannel
from core.notifications.channels.telegram import TelegramChannel
from core.notifications.ports import NotificationChannel
from shared.config import Settings


def build_notification_channels(settings: Settings) -> dict[str, NotificationChannel]:
    """Platform-level channels from env. Missing credentials => dormant no-op."""
    return {
        TelegramChannel.code: TelegramChannel(settings.telegram_bot_token or None),
        EskizSmsChannel.code: EskizSmsChannel(
            settings.eskiz_email or None, settings.eskiz_password or None
        ),
        EmailChannel.code: EmailChannel(
            host=settings.smtp_host or None,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            from_address=settings.smtp_from,
        ),
    }


def build_channel_from_config(channel: str, config: Mapping[str, object]) -> NotificationChannel:
    """Build a per-tenant channel from its decrypted config (keys per
    channel_config.CHANNEL_CONFIG_FIELDS)."""
    if channel == TelegramChannel.code:
        return TelegramChannel(str(config["bot_token"]))
    if channel == EskizSmsChannel.code:
        return EskizSmsChannel(str(config["email"]), str(config["password"]))
    if channel == EmailChannel.code:
        return EmailChannel(
            host=str(config["host"]),
            port=int(str(config["port"])),
            username=str(config["username"]),
            password=str(config["password"]),
            from_address=str(config["from_address"]),
        )
    raise ValueError(f"unknown notification channel: {channel}")


__all__ = [
    "EmailChannel",
    "EskizSmsChannel",
    "TelegramChannel",
    "build_channel_from_config",
    "build_notification_channels",
]

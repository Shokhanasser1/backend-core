"""core/notifications — outbound messages (Telegram, Eskiz SMS, email/SMTP).

NotificationService queues sends into the notification_outbox; an arq dispatcher
delivers them through NotificationChannel adapters with retries and dead-letter.
Per-tenant channel configs (bot tokens, Eskiz keys, SMTP creds) live encrypted
in notification_settings. Adapters, template rendering and the outbox tables are
internals; the public interface is re-exported here as it is built.
"""

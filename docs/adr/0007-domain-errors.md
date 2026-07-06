# ADR-0007. Типизированные исключения DomainError вместо Result

- **Статус:** принято (решение владельца 2026-07-06, ОВ-07; отступление от мастер-промпта v2 внесено в него правкой)
- **Контекст.** Мастер-промпт перечислял `Result` в составе shared/. В Python без checked-типов Result даёт `unwrap`-шум и теряется в цепочках сервис→сервис, а ошибки всё равно доходят до одного HTTP-слоя.
- **Решение.** Иерархия `shared.errors.DomainError` (`code`, `message_key`, `http_status`): NotFound/Conflict/InvariantViolation/PermissionDenied/Authentication/RateLimited + ветка внешних систем (ExternalServiceError → PaymentProviderError, NotificationChannelError, CircuitOpenError) и WebhookVerificationError. Один FastAPI-хендлер маппит иерархию на HTTP и ключи i18n (каталоги — Фаза 3). Модули наследуют свои конкретные ошибки.
- **Исключение из маппинга.** Роуты вебхуков платёжек не проходят через общий хендлер — любой исход отвечается в диалекте провайдера (Payme JSON-RPC error с HTTP 200 и т.п.; Фаза 3).
- **Последствия.** Сигнатуры сервисов не шумят обёртками; негативные тесты проверяют конкретный класс исключения и HTTP-код.

# core/files

## Назначение

Тенантное объектное хранилище: загрузка файлов через API с проверкой типа по
magic bytes (заголовку содержимого), выдача и удаление. Байты живут в бэкенде
хранилища за портом `StoragePort` (адаптеры `filesystem` для dev/test и `s3` для
прода); в Postgres — только тенантная строка-метаданные (таблица `files`, RLS) с
контрольной суммой sha256. Клиентский `Content-Type` не доверяется: реальный тип
определяется по содержимому и сверяется с allowlist (только растровые картинки —
SVG/HTML не проходят, поэтому inline-отдача XSS-безопасна).

## Публичный интерфейс

- **`FileService`** (`service.py`, экспортируется из пакета `core.files`):
  - `upload(*, filename, declared_content_type, data) -> FileDTO` — валидация
    (размер ≤ `FILES_MAX_UPLOAD_BYTES` + magic-bytes allowlist), запись байт в
    бэкенд, метаданные в БД; событие `files.file.uploaded`;
  - `get(file_id) -> FileDTO` — только метаданные (404 для чужого/отсутствующего);
  - `open(file_id) -> (FileDTO, bytes)` — метаданные + байты для стрима;
  - `delete(file_id)` — удаляет строку и объект; событие `files.file.deleted`.
- **`FileDTO`** (`schemas.py`): `id`, `content_type`, `byte_size`,
  `checksum_sha256`, `original_filename`.
- **Порт `StoragePort`** (`ports.py`): `put`, `get`, `delete`. Адаптеры —
  `adapters/filesystem.py`, `adapters/s3.py`; выбор — `FILES_STORAGE_BACKEND`,
  сборка — `build_storage` (`adapters/__init__.py`, fail-loud при `s3` без кредов).
- Роутер `/api/files` (`router.py`, authed): `POST` (upload), `GET /{id}` (стрим
  байт, inline), `GET /{id}/meta`, `DELETE /{id}`.

Соседи (модули/фичи) зовут `FileService` — таблицу `files` не читают никогда.

## Права (владеет)

`files.file:read`, `files.file:upload`, `files.file:delete` (owner/admin — всё,
member — только read).

## События

- **Публикует:** `files.file.uploaded`, `files.file.deleted` (аудируются
  wildcard-стоком ядра; полезной нагрузки без ПД: только `file_id`, тип, размер).
- **Слушает:** —

## Как добавить бэкенд хранилища

1. `adapters/<name>.py` — класс с `backend: ClassVar[str]` и async `put/get/delete`
   (структурно реализует `StoragePort`); сетевые вызовы — через `call_resilient`
   (таймаут + повторы + circuit breaker), ошибки → `StorageError`.
2. Ветку выбора добавь в `build_storage` (`adapters/__init__.py`).
3. Креды — из env (`.env.example`), пусто = провайдер не сконфигурирован → падение
   на старте при выборе этого бэкенда.

## Не публично

Таблица `files`, модель `StoredFile`, репозиторий, сниффер `content_types.py`.
Пресайн-URL и фоновая сборка «осиротевших» объектов (после краха между записью в
бэкенд и commit) — бэклог; в v1 отдача идёт стримом через приложение.

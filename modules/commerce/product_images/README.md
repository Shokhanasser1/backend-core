# commerce.product_images

## Назначение

Изображения товаров: персонал тенанта загружает картинку, она сохраняется через
`core/files` (с проверкой magic bytes) и привязывается к товару. При загрузке
синхронно генерится уменьшенное превью (тоже отдельный файл в `core/files`,
`thumbnail_file_id`). Товар проверяется через публичный `ProductService`, файл —
через `FileService`; ни таблицу `commerce_products`, ни `files` фича не читает.
Опциональная фича — подключается только если проекту нужны картинки.

## Публичный интерфейс

- **`ProductImageService`**: `attach(*, product_id, filename, declared_content_type,
  data, alt_text, position)`, `list_for_product(product_id)`,
  `open_content(image_id, *, variant="original"|"thumb") -> (FileDTO, bytes)`,
  `remove(image_id)` (удаляет и оригинал, и превью).
- **DTO:** `ProductImageDTO` (`id`, `product_id`, `file_id`, `thumbnail_file_id`,
  `position`, `alt_text`).
- **Права:** `commerce.product_image:read` (owner/admin/member),
  `commerce.product_image:manage` (owner/admin).
- **События:** `commerce.product_image.added|removed` (payload: `image_id`,
  `product_id`, `file_id`; у `added` — ещё `thumbnail_file_id`).
- **Роуты** (`/api/commerce/product-images`, staff, RBAC): `POST` (multipart:
  `product_id` + `file` + `alt_text?` + `position?`), `GET ?product_id=…` (список),
  `GET /{image_id}/content?size=original|thumb` (стрим байт, inline; `thumb` с
  фолбэком на оригинал), `DELETE /{image_id}`.

## Манифест

`feature.toml`: `requires_features = ["commerce.products"]`,
`requires_core = ["auth", "tenants", "files"]`,
`owns_tables = ["commerce_product_images"]`.

## Подключение в новый проект

1. Скопировать папку `modules/commerce/product_images/` (или `tools/add-feature` —
   тянет цепочку `requires`: `commerce.products` + core `files`).
2. `ENABLED_MODULES=commerce`; ядро `files` включено всегда.
3. Настроить хранилище: dev — `FILES_STORAGE_BACKEND=filesystem` (по умолчанию),
   прод — `s3` + `FILES_S3_*` (см. `core/files/README.md`).
4. Миграции: `python -m migrations.cli upgrade heads` (ветки `commerce_product_images`
   и `core_files`).

## Типовые кастомизации

- Ограничение числа картинок на товар / обложка → инвариант в `attach`
  (проверка `list_for_product` перед вставкой).
- Размер превью → `FILES_THUMBNAIL_MAX_EDGE`; сама генерация — в `core/files`
  (порт `ThumbnailPort`), не в фиче. Несколько размеров/форматов → расширить порт.
- Публичная витрина покупателю → добавить storefront-роут отдачи по образцу
  `commerce.cart` (`storefront_bundle` + `authenticated_endpoint`); в v1 фича
  staff-only (в шаблоне нет публичного каталога).

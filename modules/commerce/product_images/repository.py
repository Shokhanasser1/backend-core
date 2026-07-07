"""Tenant-scoped repository for product images."""

from modules.commerce.product_images.models import ProductImage
from shared.repository import Repository


class ProductImageRepository(Repository[ProductImage]):
    model = ProductImage

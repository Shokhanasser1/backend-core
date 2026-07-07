"""Tenant-scoped repository for the products catalog."""

from modules.commerce.products.models import Product
from shared.repository import Repository


class ProductRepository(Repository[Product]):
    model = Product

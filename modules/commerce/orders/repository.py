"""Tenant-scoped repositories for orders."""

from modules.commerce.orders.models import Order, OrderItem
from shared.repository import Repository


class OrderRepository(Repository[Order]):
    model = Order


class OrderItemRepository(Repository[OrderItem]):
    model = OrderItem

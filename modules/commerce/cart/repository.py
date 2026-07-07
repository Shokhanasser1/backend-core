"""Tenant-scoped repositories for the buyer's cart."""

from modules.commerce.cart.models import Cart, CartItem
from shared.repository import Repository


class CartRepository(Repository[Cart]):
    model = Cart


class CartItemRepository(Repository[CartItem]):
    model = CartItem

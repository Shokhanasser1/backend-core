import pytest

from shared.money import Money
from shared.pagination import MAX_PAGE_LIMIT, Page, PageResult


class TestMoney:
    def test_valid(self) -> None:
        money = Money(amount=150_000)
        assert money.currency == "UZS"

    def test_zero_allowed_for_free_plans(self) -> None:
        assert Money(amount=0).amount == 0

    def test_negative_forbidden(self) -> None:
        with pytest.raises(ValueError, match="amount"):
            Money(amount=-1)

    @pytest.mark.parametrize("currency", ["uzs", "US", "DOLLARS", "U1D"])
    def test_bad_currency(self, currency: str) -> None:
        with pytest.raises(ValueError, match="currency"):
            Money(amount=1, currency=currency)


class TestPage:
    def test_defaults(self) -> None:
        page = Page()
        assert (page.limit, page.offset) == (50, 0)

    @pytest.mark.parametrize("limit", [0, -1, MAX_PAGE_LIMIT + 1])
    def test_limit_out_of_bounds(self, limit: int) -> None:
        with pytest.raises(ValueError, match="limit"):
            Page(limit=limit)

    def test_negative_offset(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            Page(offset=-1)


def test_page_result_holds_items() -> None:
    result = PageResult(items=["a", "b"], total=10, limit=2, offset=0)
    assert result.total == 10
    assert list(result.items) == ["a", "b"]

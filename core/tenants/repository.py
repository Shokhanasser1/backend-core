"""Repositories for tenant-scoped tables (auto tenant filter + RLS)."""

from core.tenants.models import Invitation, Membership
from shared.repository import Repository


class MembershipRepository(Repository[Membership]):
    model = Membership


class InvitationRepository(Repository[Invitation]):
    model = Invitation

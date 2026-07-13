"""Tenant-scoped repositories for CRM companies and contacts."""

from modules.crm.contacts.models import Company, Contact
from shared.repository import Repository


class CompanyRepository(Repository[Company]):
    model = Company


class ContactRepository(Repository[Contact]):
    model = Contact

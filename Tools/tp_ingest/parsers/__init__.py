"""Parser registry for ingestion."""
from .integration import IntegrationReportParser
from .pas import PASReportParser

__all__ = ["IntegrationReportParser", "PASReportParser"]

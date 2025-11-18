"""Parser registry for ingestion."""
from .integration import IntegrationReportParser
from .pas import PASReportParser
from .plist_master import PlistMasterParser

__all__ = ["IntegrationReportParser", "PASReportParser", "PlistMasterParser"]

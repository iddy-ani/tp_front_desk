"""Parser registry for ingestion."""
from .integration import IntegrationReportParser
from .pas import PASReportParser
from .plist_master import PlistMasterParser
from .cake_vadt import CakeAuditParser
from .vmin_search import VMinSearchParser
from .scoreboard import ScoreboardParser

__all__ = [
	"IntegrationReportParser",
	"PASReportParser",
	"PlistMasterParser",
	"CakeAuditParser",
	"VMinSearchParser",
	"ScoreboardParser",
]

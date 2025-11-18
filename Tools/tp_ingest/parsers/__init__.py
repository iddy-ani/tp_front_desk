"""Parser registry for ingestion."""
from .integration import IntegrationReportParser
from .pas import PASReportParser
from .plist_master import PlistMasterParser
from .cake_vadt import CakeAuditParser
from .vmin_search import VMinSearchParser
from .scoreboard import ScoreboardParser
from .setpoints import SetpointsParser

__all__ = [
	"IntegrationReportParser",
	"PASReportParser",
	"PlistMasterParser",
	"CakeAuditParser",
	"VMinSearchParser",
	"ScoreboardParser",
	"SetpointsParser",
]

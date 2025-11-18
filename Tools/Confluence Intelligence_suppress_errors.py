"""
title: Confluence Intelligence
author: Idriss Animashaun
description: Advanced Confluence wiki tool with CQL search, content scraping, and intelligent Q&A capabilities. Supports both Cloud and Server deployments with authentication.
version: 1.0.0
license: MIT
requirements: atlassian-python-api>=3.30.0, beautifulsoup4>=4.12.0
"""

from atlassian import Confluence
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup, NavigableString, Tag
import json
import re
from typing import Callable, Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import urllib3

# Suppress insecure HTTPS warnings (internal/self-signed cert usage expected)
try:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EventEmitter:
    def __init__(self, event_emitter: Callable[[dict], Any] = None):
        self.event_emitter = event_emitter

    async def progress_update(self, description):
        await self.emit(description)

    async def error_update(self, description):
        await self.emit(description, "error", True)

    async def success_update(self, description):
        await self.emit(description, "success", True)

    async def emit(self, description="Unknown State", status="in_progress", done=False):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "status": status,
                        "description": description,
                        "done": done,
                    },
                }
            )


class ConfluenceParser:
    """Enhanced Confluence parser with better content extraction"""

    def __init__(self, confluence: Confluence):
        self.confluence = confluence

    def parse_confluence_url(
        self, page_url: str
    ) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        """Parse Confluence URL to extract components"""
        parsed = urlparse(page_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Handle base URL only (redirect to search)
        if not parsed.path or parsed.path in ["/", ""]:
            raise ValueError(
                f"Please provide a specific page URL, not just the base URL: {page_url}"
            )

        query_params = parse_qs(parsed.query)
        if "pageId" in query_params:
            return base_url, None, None, query_params["pageId"][0]

        # Handle different URL formats
        path_parts = parsed.path.split("/")

        # /spaces/<SPACE>/pages/<ID>/<TITLE> format (modern Confluence URLs)
        if (
            len(path_parts) >= 5
            and path_parts[1] == "spaces"
            and path_parts[3] == "pages"
        ):
            space_key = path_parts[2]
            page_id = path_parts[4]
            return base_url, space_key, None, page_id

        # /display/<SPACE>/<TITLE> format
        if len(path_parts) >= 4 and path_parts[1] == "display":
            space_key = path_parts[2]
            page_title = path_parts[3].replace("+", " ")
            return base_url, space_key, page_title, None

        # /pages/viewpage.action format
        if "viewpage.action" in parsed.path:
            space_key = query_params.get("spaceKey", [None])[0]
            title = query_params.get("title", [None])[0]
            if title:
                title = title.replace("+", " ")
            return base_url, space_key, title, None

        raise ValueError(
            f"Unsupported Confluence URL format: {page_url}. Please provide a direct link to a page."
        )

    def get_page_content(
        self, page_url: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Retrieve and parse page content"""
        try:
            base_url, space_key, page_title, direct_page_id = self.parse_confluence_url(
                page_url
            )

            if direct_page_id:
                page_id = direct_page_id
                page_data = self.confluence.get_page_by_id(
                    page_id, expand="body.storage"
                )
                if page_data and "title" in page_data:
                    page_title = page_data["title"]
            else:
                page_id = self.confluence.get_page_id(space=space_key, title=page_title)
                if not page_id:
                    return None, None, None
                page_data = self.confluence.get_page_by_id(
                    page_id, expand="body.storage"
                )

            if (
                not page_data
                or "body" not in page_data
                or "storage" not in page_data["body"]
            ):
                return None, None, None

            raw_content = page_data["body"]["storage"]["value"]
            soup = BeautifulSoup(raw_content, "html.parser")

            # Process attachments
            attachments_map = self._fetch_attachments(page_id)
            self._process_attachments(soup, attachments_map)

            # Convert to clean text with preserved links and tables
            text_content = self._soup_to_text_with_structure(soup)

            return text_content, str(soup), page_title

        except Exception as e:
            logger.error(f"Error retrieving page content: {e}")
            return None, None, None

    def _fetch_attachments(self, page_id: str) -> Dict[str, str]:
        """Fetch attachment information for a page"""
        try:
            endpoint = f"/rest/api/content/{page_id}/child/attachment"
            results = []
            start = 0
            limit = 50

            while True:
                params = {"expand": "version,container", "limit": limit, "start": start}
                url = self.confluence.url + endpoint
                resp = self.confluence._session.get(url, params=params)

                if resp.status_code != 200:
                    break

                data = resp.json()
                chunk = data.get("results", [])
                results.extend(chunk)

                if len(chunk) < limit:
                    break
                start += limit

            attachments_map = {}
            for att in results:
                dl_link = att.get("_links", {}).get("download")
                filename = att.get("title")
                if dl_link and filename:
                    attachments_map[filename.lower()] = dl_link

            return attachments_map
        except Exception as e:
            logger.warning(f"Failed to fetch attachments: {e}")
            return {}

    def _process_attachments(
        self, soup: BeautifulSoup, attachments_map: Dict[str, str]
    ):
        """Replace attachment references with download links"""
        attachment_tags = soup.find_all("ri:attachment")
        for att_tag in attachment_tags:
            filename = att_tag.get("ri:filename")
            if filename and filename.lower() in attachments_map:
                download_link = attachments_map[filename.lower()]
                full_url = self.confluence.url + download_link
                link_tag = soup.new_tag("a", href=full_url)
                link_tag.string = f"Download {filename}"
                att_tag.replace_with(link_tag)

    def _soup_to_text_with_structure(self, soup: BeautifulSoup) -> str:
        """Convert soup to text while preserving important structure"""
        return self._recursive_text_extraction(soup)

    def _recursive_text_extraction(self, element) -> str:
        """Recursively extract text with structure preservation"""
        parts = []
        for child in element.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    parts.append(text)
            elif isinstance(child, Tag):
                if child.name == "a":
                    parts.append(str(child))
                elif child.name == "table":
                    table_json = self._parse_table_to_json(child)
                    parts.append(json.dumps(table_json, ensure_ascii=False))
                elif child.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                    header_text = self._recursive_text_extraction(child)
                    level = int(child.name[1])
                    parts.append(f"\n{'#' * level} {header_text}\n")
                elif child.name == "p":
                    para_text = self._recursive_text_extraction(child)
                    if para_text:
                        parts.append(f"{para_text}\n")
                else:
                    parts.append(self._recursive_text_extraction(child))

        return " ".join(p for p in parts if p)

    def _parse_table_to_json(self, table_tag: Tag) -> Dict:
        """Parse table to structured JSON"""
        table_dict = {"type": "table", "headers": [], "rows": []}

        # Extract headers
        thead = table_tag.find("thead")
        if thead:
            header_row = thead.find("tr")
            if header_row:
                headers = [
                    self._extract_cell_text(th)
                    for th in header_row.find_all(["th", "td"])
                ]
                table_dict["headers"] = headers

        # Extract rows
        tbody = table_tag.find("tbody")
        rows = tbody.find_all("tr") if tbody else table_tag.find_all("tr")

        # Handle case where first row contains headers
        if not table_dict["headers"] and rows:
            first_row_cols = rows[0].find_all(["th", "td"])
            if any(col.name == "th" for col in first_row_cols):
                table_dict["headers"] = [
                    self._extract_cell_text(col) for col in first_row_cols
                ]
                rows = rows[1:]

        for row in rows:
            cols = row.find_all(["th", "td"])
            row_data = [self._extract_cell_text(col) for col in cols]
            table_dict["rows"].append(row_data)

        return table_dict

    def _extract_cell_text(self, cell_tag: Tag) -> str:
        """Extract text from table cell preserving links"""
        parts = []
        for child in cell_tag.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    parts.append(text)
            elif isinstance(child, Tag):
                if child.name == "a":
                    parts.append(str(child))
                else:
                    parts.append(self._extract_cell_text(child))
        return " ".join(p for p in parts if p)


class Tools:
    class Valves(BaseModel):
        DEBUG_MODE: bool = Field(
            default=False, description="Enable debug logging and verbose output"
        )
        MAX_SEARCH_RESULTS: int = Field(
            default=10, description="Maximum number of search results to return"
        )
        MAX_CONTENT_LENGTH: int = Field(
            default=50000, description="Maximum character length for content retrieval"
        )
        CONFLUENCE_TIMEOUT: int = Field(
            default=30,
            description="Request timeout in seconds for Confluence API calls",
        )
        FOLLOW_REFERENCED_LINKS: bool = Field(
            default=True,
            description="Follow and fetch content from pages linked within retrieved pages (e.g., 'Sort Thermal Playbook').",
        )
        REFERENCED_LINK_KEYWORDS: List[str] = Field(
            default_factory=lambda: [
                "Sort Thermal Playbook",
                "Thermal Playbook",
                "STP",
            ],
            description="Anchor/link text keywords that trigger referenced page fetching.",
        )
        REFERENCED_LINK_LIMIT: int = Field(
            default=2,
            description="Maximum number of referenced pages to fetch per answer.",
        )
        INCLUDE_ATTACHMENTS_METADATA: bool = Field(
            default=False,
            description="If true, include a list of attachment filenames in each context.",
        )
        PAGE_GROUP_LABEL: str = Field(
            default="",
            description="Optional Confluence label used to further restrict searches (e.g., 'thermal').",
        )
        RESTRICT_TO_PAGE_IDS: List[str] = Field(
            default_factory=list,
            description="Optional whitelist of Confluence page IDs. If provided, search results will be limited to these IDs only (fetched directly if missing).",
        )
        RESTRICT_TO_PAGE_TITLES: List[str] = Field(
            default_factory=list,
            description="Optional whitelist of exact page titles (case-insensitive). If provided, results are filtered to these titles and missing ones are fetched directly.",
        )
        SUPPRESS_EXPECTED_TITLE_ERRORS: bool = Field(
            default=True,
            description="Suppress noisy 'Can't find page' error logs during variant/fuzzy whitelist title resolution (expected misses).",
        )
        # User Configuration (normally in UserValves but made visible here)
        CONFLUENCE_URL: str = Field(
            default="",
            description="Confluence base URL (e.g., https://wiki.ith.intel.com)",
        )
        CONFLUENCE_TOKEN: str = Field(
            default="", description="Confluence API token or personal access token"
        )
        CONFLUENCE_USERNAME: str = Field(
            default="",
            description="Username (required for Server installations, optional for Cloud with token auth)",
        )
        CONFLUENCE_PASSWORD: str = Field(
            default="",
            description="Password (for Server installations using username/password auth)",
        )
        VERIFY_SSL: bool = Field(
            default=True,
            description="Verify SSL certificates (set to False for internal/self-signed certificates)",
        )
        DEFAULT_SPACES: List[str] = Field(
            default_factory=list,
            description="Default space keys to search in. Enter one space per line in the UI, or comma-separated (e.g., IntelTPWiki or DOCS,WIKI,TECH)",
        )
        SEARCH_CONTENT_TYPES: List[str] = Field(
            default_factory=lambda: ["page", "blogpost"],
            description="Content types to include in searches. Enter one type per line in the UI, or comma-separated (e.g., page,blogpost)",
        )
        INCLUDE_ATTACHMENTS_METADATA: bool = Field(
            default=False,
            description="If true, include a list of attachment filenames in each context.",
        )
        PAGE_GROUP_LABEL: str = Field(
            default="",
            description="Optional Confluence label used to further restrict searches (e.g., 'thermal').",
        )
        RESTRICT_TO_PAGE_IDS: List[str] = Field(
            default_factory=list,
            description="Optional whitelist of Confluence page IDs. If provided, search results will be limited to these IDs only (fetched directly if missing).",
        )
        RESTRICT_TO_PAGE_TITLES: List[str] = Field(
            default_factory=list,
            description="Optional whitelist of exact page titles (case-insensitive). If provided, results are filtered to these titles and missing ones are fetched directly.",
        )

    class UserValves(BaseModel):
        CONFLUENCE_URL: str = Field(
            default="",
            description="Confluence base URL (e.g., https://your-company.atlassian.net or https://wiki.company.com)",
        )
        CONFLUENCE_TOKEN: str = Field(
            default="", description="Confluence API token or personal access token"
        )
        CONFLUENCE_USERNAME: str = Field(
            default="",
            description="Username (required for Server installations, optional for Cloud with token auth)",
        )
        CONFLUENCE_PASSWORD: str = Field(
            default="",
            description="Password (for Server installations using username/password auth)",
        )
        VERIFY_SSL: bool = Field(
            default=True,
            description="Verify SSL certificates (set to False for internal/self-signed certificates)",
        )
        DEFAULT_SPACES: List[str] = Field(
            default_factory=list,
            description="Default space keys to search in. Enter one space per line in the UI, or comma-separated (e.g., IntelTPWiki or DOCS,WIKI,TECH)",
        )
        SEARCH_CONTENT_TYPES: List[str] = Field(
            default_factory=lambda: ["page", "blogpost"],
            description="Content types to include in searches. Enter one type per line in the UI, or comma-separated (e.g., page,blogpost)",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.executor = ThreadPoolExecutor(max_workers=3)

    def _get_confluence_client(self, valves=None) -> Optional[Confluence]:
        """Create authenticated Confluence client"""
        config = valves or self.valves
        if not config.CONFLUENCE_URL or not config.CONFLUENCE_TOKEN:
            return None

        try:
            # Determine if this is Cloud or Server based on URL
            is_cloud = ".atlassian.net" in config.CONFLUENCE_URL

            if is_cloud:
                # Cloud authentication with token
                confluence = Confluence(
                    url=config.CONFLUENCE_URL,
                    token=config.CONFLUENCE_TOKEN,
                    verify_ssl=config.VERIFY_SSL,
                )
            else:
                # Server authentication
                if config.CONFLUENCE_USERNAME and config.CONFLUENCE_PASSWORD:
                    # Username/password auth
                    confluence = Confluence(
                        url=config.CONFLUENCE_URL,
                        username=config.CONFLUENCE_USERNAME,
                        password=config.CONFLUENCE_PASSWORD,
                        verify_ssl=config.VERIFY_SSL,
                    )
                elif config.CONFLUENCE_TOKEN:
                    # Token auth for Server (if supported)
                    confluence = Confluence(
                        url=config.CONFLUENCE_URL,
                        token=config.CONFLUENCE_TOKEN,
                        verify_ssl=config.VERIFY_SSL,
                    )
                else:
                    return None

            # Test connection
            confluence.get_all_spaces(start=0, limit=1)
            return confluence

        except Exception as e:
            logger.error(f"Failed to create Confluence client: {e}")
            return None

    def _build_cql_query(
        self,
        search_terms: str,
        spaces: List[str] = None,
        content_types: List[str] = None,
        date_filter: str = None,
    ) -> str:
        """Build CQL query from parameters"""
        clauses = []

        # Text search
        if search_terms:
            # Use text field for broader search across title, content, and labels
            search_terms_escaped = search_terms.replace('"', '\\"')
            clauses.append(f'text ~ "{search_terms_escaped}"')

        # Space filter
        if spaces:
            if len(spaces) == 1:
                clauses.append(f'space = "{spaces[0]}"')
            else:
                space_list = ", ".join(f'"{space}"' for space in spaces)
                clauses.append(f"space IN ({space_list})")

        # Content type filter
        if content_types:
            if len(content_types) == 1:
                clauses.append(f'type = "{content_types[0]}"')
            else:
                type_list = ", ".join(f'"{ctype}"' for ctype in content_types)
                clauses.append(f"type IN ({type_list})")

        # Date filter
        if date_filter:
            clauses.append(date_filter)

        return " AND ".join(clauses) + " ORDER BY lastModified DESC"

    async def search_confluence(
        self,
        query: str,
        spaces: str = "",
        content_types: str = "",
        max_results: int = None,
        __event_emitter__: Callable[[dict], Any] = None,
        __user__: dict = {},
    ) -> str:
        """
        Search Confluence using CQL (Confluence Query Language)

        Args:
            query: Search terms or CQL query
            spaces: Comma-separated list of space keys (optional)
            content_types: Comma-separated list of content types (page, blogpost, comment, attachment)
            max_results: Maximum number of results to return

        Returns:
            JSON string with search results
        """
        emitter = EventEmitter(__event_emitter__)

        # Debug: Log search parameters
        print(f"[DEBUG search_confluence] Input query: '{query}'")
        print(f"[DEBUG search_confluence] Input spaces: '{spaces}'")
        print(f"[DEBUG search_confluence] Input content_types: '{content_types}'")
        print(f"[DEBUG search_confluence] Max results: {max_results}")

        # Get Confluence client
        confluence = self._get_confluence_client()
        if not confluence:
            error_msg = "Confluence connection failed. Check URL and credentials in tool settings."
            print(f"[DEBUG search_confluence] Confluence connection failed")
            await emitter.error_update(error_msg)
            return json.dumps({"error": error_msg, "results": []})

        print(f"[DEBUG search_confluence] Confluence client created successfully")
        await emitter.progress_update("Building search query...")

        # Parse parameters
        space_list = (
            [s.strip() for s in spaces.split(",") if s.strip()]
            if spaces
            else self.valves.DEFAULT_SPACES
        )
        content_type_list = (
            [t.strip() for t in content_types.split(",") if t.strip()]
            if content_types
            else self.valves.SEARCH_CONTENT_TYPES
        )
        result_limit = max_results or self.valves.MAX_SEARCH_RESULTS

        print(f"[DEBUG search_confluence] Parsed space_list: {space_list}")
        print(
            f"[DEBUG search_confluence] Parsed content_type_list: {content_type_list}"
        )
        print(f"[DEBUG search_confluence] Result limit: {result_limit}")

        # Build CQL query
        if (
            query.strip()
            .lower()
            .startswith(("space =", "text ~", "title ~", "type =", "created"))
        ):
            # User provided CQL query
            cql_query = query
            print(f"[DEBUG search_confluence] Using provided CQL query")
        else:
            # Build CQL from search terms
            cql_query = self._build_cql_query(query, space_list, content_type_list)
            print(f"[DEBUG search_confluence] Built CQL query from search terms")
            # Prepare a properly formed alternative title query (not executed here, used later if needed)
            alt_title_query = None
            if "thermal" in query.lower() and "playbook" in query.lower():
                filters = []
                if space_list:
                    filters.append(f'space = "{space_list[0]}"')
                if content_type_list:
                    if len(content_type_list) == 1:
                        filters.append(f'type = "{content_type_list[0]}"')
                    else:
                        type_list = ", ".join(f'"{t}"' for t in content_type_list)
                        filters.append(f"type IN ({type_list})")
                alt_title_query = 'title ~ "Sort Thermal Playbook"'
                if filters:
                    alt_title_query += " AND " + " AND ".join(filters)
                alt_title_query += " ORDER BY lastModified DESC"
                print(
                    f"[DEBUG search_confluence] Prepared alternative title query: '{alt_title_query}'"
                )

            # Label restriction (only if not manual CQL)
            if self.valves.PAGE_GROUP_LABEL:
                label_clause = f'label = "{self.valves.PAGE_GROUP_LABEL}"'
                # insert before ORDER BY
                if "ORDER BY" in cql_query:
                    parts = cql_query.split(" ORDER BY")
                    cql_query = parts[0] + f" AND {label_clause} ORDER BY" + parts[1]
                else:
                    cql_query += f" AND {label_clause}"
                print(
                    f"[DEBUG search_confluence] Applied label filter: {self.valves.PAGE_GROUP_LABEL}"
                )

        print(f"[DEBUG search_confluence] Final CQL query: '{cql_query}'")
        await emitter.progress_update(f"Searching with CQL: {cql_query}")

        try:
            print(f"[DEBUG search_confluence] Executing CQL search...")

            # Execute search
            def search_fn():
                return confluence.cql(
                    cql_query,
                    start=0,
                    limit=result_limit,
                    expand="space,version,body.view",
                )

            search_results = await asyncio.get_event_loop().run_in_executor(
                self.executor, search_fn
            )

            print(
                f"[DEBUG search_confluence] CQL search completed. Keys in result: {list(search_results.keys()) if search_results else 'None'}"
            )
            print(
                f"[DEBUG search_confluence] Total results available: {search_results.get('totalSize', 'Unknown') if search_results else 'None'}"
            )
            print(
                f"[DEBUG search_confluence] Results returned: {len(search_results.get('results', [])) if search_results else 0}"
            )

            # Check if CQL search returned valid results with IDs
            has_valid_ids = False
            if "results" in search_results and search_results["results"]:
                # Check for valid IDs in the structured results
                for item in search_results["results"]:
                    if (
                        item.get("id")
                        or item.get("content", {}).get("id")
                        or (
                            item.get("_links", {}).get("webui")
                            and "pageId=" in item.get("_links", {}).get("webui", "")
                        )
                    ):
                        has_valid_ids = True
                        break
                print(
                    f"[DEBUG search_confluence] CQL results have valid IDs: {has_valid_ids}"
                )

            # If no results and we have a thermal playbook query, try title search
            if (
                not search_results.get("results")
                and "thermal" in query.lower()
                and "playbook" in query.lower()
            ):
                print(
                    f"[DEBUG search_confluence] Trying title-specific search for thermal playbook..."
                )

                def title_search_fn():
                    title_cql = f'title ~ "thermal playbook" AND space = "{space_list[0]}" AND type IN ("page", "blogpost") ORDER BY lastModified DESC'
                    print(f"[DEBUG search_confluence] Title search CQL: {title_cql}")
                    return confluence.cql(
                        title_cql,
                        start=0,
                        limit=result_limit,
                        expand="space,version,body.view",
                    )

                try:
                    title_results = await asyncio.get_event_loop().run_in_executor(
                        self.executor, title_search_fn
                    )
                    if title_results and title_results.get("results"):
                        search_results = title_results
                        print(
                            f"[DEBUG search_confluence] Title search found {len(title_results['results'])} results"
                        )
                        # Re-check for valid IDs after title search
                        for item in title_results["results"]:
                            if (
                                item.get("id")
                                or item.get("content", {}).get("id")
                                or (
                                    item.get("_links", {}).get("webui")
                                    and "pageId="
                                    in item.get("_links", {}).get("webui", "")
                                )
                            ):
                                has_valid_ids = True
                                break
                except Exception as e:
                    print(f"[DEBUG search_confluence] Title search failed: {e}")

            # If CQL didn't return IDs, try regular search as fallback
            if not has_valid_ids and not query.strip().lower().startswith(
                ("space =", "text ~", "title ~", "type =", "created")
            ):
                print(f"[DEBUG search_confluence] Attempting fallback search...")
                await emitter.progress_update("Trying alternative search method...")

                def fallback_search_fn():
                    # Use the built-in search method that should exist
                    try:
                        # Try different search methods available in atlassian-python-api
                        if (
                            hasattr(confluence, "get_all_pages_from_space")
                            and space_list
                        ):
                            print(
                                f"[DEBUG search_confluence] Using get_all_pages_from_space for {space_list[0]}"
                            )
                            # Search within specific space
                            space_results = confluence.get_all_pages_from_space(
                                space=space_list[0],
                                start=0,
                                limit=result_limit,
                                expand="space,version",
                            )
                            # Filter results by title/content match
                            if space_results and "results" in space_results:
                                filtered_results = []
                                search_terms_lower = query.lower().split()
                                print(
                                    f"[DEBUG search_confluence] Fallback searching for terms: {search_terms_lower}"
                                )

                                for page in space_results["results"]:
                                    title = page.get("title", "").lower()
                                    print(
                                        f"[DEBUG search_confluence] Checking page: '{title}'"
                                    )

                                    # More flexible matching - look for any of the search terms
                                    if any(
                                        term in title
                                        for term in search_terms_lower
                                        if len(term) > 2
                                    ):
                                        filtered_results.append(page)
                                        print(
                                            f"[DEBUG search_confluence] Matched: '{title}'"
                                        )

                                # If no exact matches, try broader matching
                                if not filtered_results:
                                    print(
                                        f"[DEBUG search_confluence] No exact matches, trying broader search..."
                                    )
                                    for page in space_results["results"]:
                                        title = page.get("title", "").lower()
                                        # Look for individual words
                                        if any(
                                            word in title
                                            for word in ["thermal", "playbook", "sort"]
                                            if len(word) > 3
                                        ):
                                            filtered_results.append(page)
                                            print(
                                                f"[DEBUG search_confluence] Broad match: '{title}'"
                                            )

                                space_results["results"] = filtered_results[
                                    :result_limit
                                ]
                                print(
                                    f"[DEBUG search_confluence] Fallback filtered to {len(filtered_results)} results"
                                )
                            return space_results
                        else:
                            # If no space-specific search, just return empty results
                            print(
                                f"[DEBUG search_confluence] No fallback method available"
                            )
                            return {"results": [], "totalSize": 0}
                    except Exception as e:
                        print(
                            f"[DEBUG search_confluence] Fallback search exception: {e}"
                        )
                        logger.warning(f"Space search failed: {e}")
                        return {"results": [], "totalSize": 0}

                try:
                    fallback_results = await asyncio.get_event_loop().run_in_executor(
                        self.executor, fallback_search_fn
                    )
                    if (
                        fallback_results
                        and "results" in fallback_results
                        and fallback_results["results"]
                    ):
                        search_results = fallback_results
                        print(
                            f"[DEBUG search_confluence] Using fallback results: {len(fallback_results['results'])} items"
                        )
                        await emitter.progress_update(
                            "Using alternative search results"
                        )
                    else:
                        print(
                            f"[DEBUG search_confluence] Fallback search returned no results"
                        )
                except Exception as e:
                    print(f"[DEBUG search_confluence] Fallback search failed: {e}")
                    logger.warning(f"Fallback search failed: {e}")

            # Process results
            results = []
            print(
                f"[DEBUG search_confluence] Processing {len(search_results.get('results', []))} raw results..."
            )
            if "results" in search_results:
                for i, item in enumerate(search_results["results"]):
                    print(
                        f"[DEBUG search_confluence] Raw result {i+1}: {json.dumps(item, indent=2)[:500]}..."
                    )

                    # Extract page ID properly - try multiple approaches
                    page_id = None

                    # Try direct id field
                    if item.get("id"):
                        page_id = item.get("id")
                        print(
                            f"[DEBUG search_confluence] Found ID via direct field: {page_id}"
                        )

                    # Try content.id field
                    elif item.get("content", {}).get("id"):
                        page_id = item.get("content", {}).get("id")
                        print(
                            f"[DEBUG search_confluence] Found ID via content.id: {page_id}"
                        )

                    # Try extracting from _links
                    elif item.get("_links", {}).get("webui"):
                        webui_link = item.get("_links", {}).get("webui", "")
                        if "pageId=" in webui_link:
                            page_id = webui_link.split("pageId=")[1].split("&")[0]
                            print(
                                f"[DEBUG search_confluence] Extracted ID from webui link: {page_id}"
                            )

                    # Try alternative fields that might contain the ID
                    elif hasattr(item, "get") and any(
                        key.endswith("Id") for key in item.keys()
                    ):
                        for key in item.keys():
                            if key.endswith("Id") and item[key]:
                                page_id = item[key]
                                print(
                                    f"[DEBUG search_confluence] Found ID via {key}: {page_id}"
                                )
                                break

                    if not page_id:
                        page_id = "unknown"
                        print(
                            f"[DEBUG search_confluence] No valid ID found for result {i+1}"
                        )

                    # Build proper URL
                    webui_link = item.get("_links", {}).get("webui", "")
                    if webui_link and not webui_link.startswith("http"):
                        full_url = confluence.url.rstrip("/") + webui_link
                    elif page_id and page_id != "unknown":
                        full_url = (
                            f"{confluence.url}/pages/viewpage.action?pageId={page_id}"
                        )
                    else:
                        full_url = webui_link or "unknown"

                    result_item = {
                        "id": page_id,
                        "title": item.get("title"),
                        "type": item.get("type"),
                        "space": (
                            item.get("space", {}).get("key")
                            if item.get("space")
                            else None
                        ),
                        "space_name": (
                            item.get("space", {}).get("name")
                            if item.get("space")
                            else None
                        ),
                        "url": full_url,
                        "last_modified": (
                            item.get("version", {}).get("when")
                            if item.get("version")
                            else None
                        ),
                        "excerpt": item.get("excerpt", ""),
                    }
                    results.append(result_item)
                    print(
                        f"[DEBUG search_confluence] Processed result: ID={page_id}, title='{item.get('title')}'"
                    )

            # Apply whitelist restrictions if configured
            restrict_ids = [
                r.strip()
                for r in getattr(self.valves, "RESTRICT_TO_PAGE_IDS", [])
                if r.strip()
            ]
            restrict_titles = [
                r.strip().lower()
                for r in getattr(self.valves, "RESTRICT_TO_PAGE_TITLES", [])
                if r.strip()
            ]
            # Preserve original titles for better resolution attempts (case & punctuation)
            original_title_map = {
                r.strip().lower(): r.strip()
                for r in getattr(self.valves, "RESTRICT_TO_PAGE_TITLES", [])
                if r.strip()
            }
            if restrict_ids or restrict_titles:
                print(
                    f"[DEBUG search_confluence] Applying whitelist restrictions: ids={restrict_ids} titles={restrict_titles}"
                )
                filtered = []
                seen_ids = set()
                for r in results:
                    rid = r.get("id")
                    title_lower = (r.get("title") or "").lower()
                    if (restrict_ids and rid in restrict_ids) or (
                        restrict_titles and title_lower in restrict_titles
                    ):
                        filtered.append(r)
                        seen_ids.add(rid)
                # Fetch missing IDs directly
                missing_ids = [mid for mid in restrict_ids if mid not in seen_ids]
                for mid in missing_ids:
                    try:
                        print(
                            f"[DEBUG search_confluence] Direct-fetching whitelisted page ID {mid} not present in search results"
                        )
                        page_data = confluence.get_page_by_id(
                            mid, expand="body.storage"
                        )
                        if page_data and page_data.get("title"):
                            filtered.append(
                                {
                                    "id": mid,
                                    "title": page_data["title"],
                                    "type": page_data.get("type", "page"),
                                    "space": None,
                                    "space_name": None,
                                    "url": f"{confluence.url}/pages/viewpage.action?pageId={mid}",
                                    "last_modified": None,
                                    "excerpt": "",
                                }
                            )
                            print(
                                f"[DEBUG search_confluence] Added direct-fetched page {mid} ('{page_data['title']}')"
                            )
                    except Exception as e:
                        print(
                            f"[DEBUG search_confluence] Failed to fetch whitelisted ID {mid}: {e}"
                        )
                # Fetch missing titles directly
                missing_titles = [
                    mt
                    for mt in restrict_titles
                    if all((r.get("title") or "").lower() != mt for r in filtered)
                ]
                for mt in missing_titles:
                    try:
                        print(
                            f"[DEBUG search_confluence] Direct-fetching whitelisted title '{mt}' not present in search results"
                        )
                        # Need space to resolve title -> ID; use first space if available
                        original_title = original_title_map.get(mt, mt)
                        # Emit progress for long-running resolution batches
                        try:
                            await emitter.progress_update(f"Resolving title: {original_title}")
                        except Exception:
                            pass

                        # Alias mapping for composite pages / known merged titles
                        alias_map = {
                            "sdt thermal bring up for solder on die (sod)": "SDT thermal bring up for Solder on Die (SoD), Foveros, Microbumps",
                            "foveros": "SDT thermal bring up for Solder on Die (SoD), Foveros, Microbumps",
                            "microbumps": "SDT thermal bring up for Solder on Die (SoD), Foveros, Microbumps",
                            "dts testing strategy for joined socket (covers sds": "DTS testing strategy for joined socket (covers SDS, SDT and SDJ)",
                            "scan soc": "Scan SOC Sort Technical Readiness(CRI)",
                            "sdt and sdj)": "Control Set (CS) Standard. SDS[eTemp]/SDT/SDJ/SDM",
                        }
                        alias_target = alias_map.get(original_title.lower())
                        if alias_target:
                            print(
                                f"[DEBUG search_confluence] Alias detected for '{original_title}' -> '{alias_target}'"
                            )

                        def _try_get_page_id(raw_title: str) -> Optional[str]:
                            if not space_list:
                                return None
                            try:
                                if self.valves.SUPPRESS_EXPECTED_TITLE_ERRORS:
                                    lib_logger = logging.getLogger("atlassian.confluence")
                                    prior_level = lib_logger.level
                                    if prior_level <= logging.ERROR:
                                        lib_logger.setLevel(logging.CRITICAL)
                                    try:
                                        pid = confluence.get_page_id(space=space_list[0], title=raw_title)
                                    finally:
                                        lib_logger.setLevel(prior_level)
                                    return pid
                                else:
                                    return confluence.get_page_id(space=space_list[0], title=raw_title)
                            except Exception:
                                return None

                        # Build multiple variants to try (case / punctuation normalization)
                        variants = []
                        if original_title:
                            variants.append(original_title)  # original
                            variants.append(original_title.strip())
                            # Collapse whitespace
                            variants.append(re.sub(r"\s+", " ", original_title))
                            # Title case variant (may help if stored differently)
                            variants.append(original_title.title())
                            # Remove certain punctuation that might differ
                            simplified = re.sub(r"[\:\/\-]+", " ", original_title)
                            variants.append(re.sub(r"\s+", " ", simplified).strip())
                        if alias_target and alias_target not in variants:
                            variants.insert(0, alias_target)
                        # Prepend alias target if available (ensuring priority before other variants)
                        if alias_target and alias_target not in variants:
                            variants.insert(0, alias_target)
                        # Deduplicate while preserving order
                        seen_variant = set()
                        final_variants = []
                        for v in variants:
                            vl = v.lower()
                            if vl not in seen_variant:
                                seen_variant.add(vl)
                                final_variants.append(v)
                        page_id_guess = None
                        # Limit variant attempts to avoid UI stalls (max 5)
                        for v_index, v in enumerate(final_variants[:5]):
                            page_id_guess = _try_get_page_id(v)
                            print(f"[DEBUG search_confluence] Attempt title variant {v_index+1}/{len(final_variants)} '{v}' -> page_id={page_id_guess}")
                            if page_id_guess:
                                break

                        # Fallback: CQL fuzzy title search if direct ID not found
                        # Fuzzy CQL only if variants failed and we have a space and title length > 4
                        if not page_id_guess and space_list and len(original_title) > 4:
                            try:
                                fuzzy_cql = f'title ~ "{original_title}" AND space = "{space_list[0]}" ORDER BY lastModified DESC'
                                print(f"[DEBUG search_confluence] Fuzzy CQL attempt: {fuzzy_cql}")
                                fuzzy_results = confluence.cql(fuzzy_cql, start=0, limit=3, expand="space,version")
                                for fr in fuzzy_results.get("results", []):
                                    # Attempt to extract ID similarly to main flow
                                    cand_id = fr.get("id") or fr.get("content", {}).get("id")
                                    if not cand_id and fr.get("_links", {}).get("webui") and "pageId=" in fr.get("_links", {}).get("webui"):
                                        cand_id = fr.get("_links", {}).get("webui").split("pageId=")[1].split("&")[0]
                                    if cand_id:
                                        page_id_guess = cand_id
                                        print(f"[DEBUG search_confluence] Fuzzy CQL matched '{fr.get('title')}' (ID {page_id_guess})")
                                        break
                            except Exception as fe:
                                print(f"[DEBUG search_confluence] Fuzzy CQL title search failed for '{original_title}': {fe}")

                        if page_id_guess:
                            try:
                                page_data = confluence.get_page_by_id(page_id_guess, expand="body.storage")
                                if page_data and page_data.get("title"):
                                    filtered.append({
                                        "id": page_id_guess,
                                        "title": page_data["title"],
                                        "type": page_data.get("type", "page"),
                                        "space": space_list[0] if space_list else None,
                                        "space_name": None,
                                        "url": f"{confluence.url}/pages/viewpage.action?pageId={page_id_guess}",
                                        "last_modified": None,
                                        "excerpt": "",
                                    })
                                    print(f"[DEBUG search_confluence] Added resolved title '{page_data['title']}' (ID {page_id_guess}) via variants/CQL")
                                else:
                                    print(f"[DEBUG search_confluence] Page ID {page_id_guess} fetched but missing title data")
                            except Exception as fe:
                                print(f"[DEBUG search_confluence] Failed to fetch page by resolved ID {page_id_guess}: {fe}")
                        else:
                            print(f"[DEBUG search_confluence] Could not resolve title '{original_title}' after variants & fuzzy CQL")
                    except Exception as e:
                        print(
                            f"[DEBUG search_confluence] Failed to fetch whitelisted title '{mt}': {e}"
                        )
                # Replace results with filtered list
                results = filtered
                # Deduplicate results by ID (composite pages may have been added multiple times via aliases)
                deduped = []
                seen_ids = set()
                for r in results:
                    rid = r.get("id")
                    if rid not in seen_ids:
                        deduped.append(r)
                        seen_ids.add(rid)
                results = deduped
                print(f"[DEBUG search_confluence] Whitelist applied. Final result count (deduped): {len(results)}")

            # Semantic reranking (simple term density scoring)
            try:
                tokens = [
                    t
                    for t in re.split(r"[^a-z0-9]+", query.lower())
                    if t and len(t) > 1
                ]
                if results and tokens:

                    def score(r):
                        title = (r.get("title") or "").lower()
                        excerpt = (r.get("excerpt") or "").lower()
                        title_hits = sum(title.count(tok) for tok in tokens)
                        excerpt_hits = sum(excerpt.count(tok) for tok in tokens)
                        # Slight weight to title matches
                        return title_hits * 2 + excerpt_hits

                    scored = [(score(r), r) for r in results]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    results = [r for _, r in scored]
                    print(
                        f"[DEBUG search_confluence] Applied semantic reranking based on tokens: {tokens}"
                    )
            except Exception as e:
                print(f"[DEBUG search_confluence] Semantic reranking failed: {e}")

            response = {
                "query": cql_query,
                "total_results": search_results.get("totalSize", len(results)),
                "returned_results": len(results),
                "results": results,
            }

            await emitter.success_update(f"Found {len(results)} results")
            return json.dumps(response, ensure_ascii=False, indent=2)

        except Exception as e:
            error_msg = f"Search failed: {str(e)}"
            await emitter.error_update(error_msg)
            return json.dumps({"error": error_msg, "results": []})

    async def get_page_content(
        self,
        page_url: str,
        include_html: bool = False,
        __event_emitter__: Callable[[dict], Any] = None,
        __user__: dict = {},
    ) -> str:
        """
        Retrieve content from a specific Confluence page

        Args:
            page_url: Full URL to the Confluence page
            include_html: Whether to include raw HTML in the response

        Returns:
            JSON string with page content
        """
        emitter = EventEmitter(__event_emitter__)

        # Get Confluence client
        confluence = self._get_confluence_client()
        if not confluence:
            error_msg = "Confluence connection failed. Check URL and credentials in tool settings."
            await emitter.error_update(error_msg)
            return json.dumps({"error": error_msg})

        await emitter.progress_update(f"Retrieving page content from {page_url}")

        try:

            def get_content_fn():
                parser = ConfluenceParser(confluence)
                return parser.get_page_content(page_url)

            (
                text_content,
                html_content,
                page_title,
            ) = await asyncio.get_event_loop().run_in_executor(
                self.executor, get_content_fn
            )

            if text_content is None:
                error_msg = (
                    "Failed to retrieve page content. Check URL and permissions."
                )
                await emitter.error_update(error_msg)
                return json.dumps({"error": error_msg})

            # Truncate if too long
            if len(text_content) > self.valves.MAX_CONTENT_LENGTH:
                text_content = (
                    text_content[: self.valves.MAX_CONTENT_LENGTH] + "...[truncated]"
                )

            response = {
                "url": page_url,
                "title": page_title,
                "content": text_content,
                "content_length": len(text_content),
            }

            if include_html:
                response["html"] = html_content

            await emitter.success_update(f"Retrieved content for '{page_title}'")
            return json.dumps(response, ensure_ascii=False, indent=2)

        except Exception as e:
            error_msg = f"Failed to retrieve page: {str(e)}"
            await emitter.error_update(error_msg)
            return json.dumps({"error": error_msg})

    async def answer_question(
        self,
        question: str,
        search_context: str = "",
        spaces: str = "",
        __event_emitter__: Callable[[dict], Any] = None,
        __user__: dict = {},
    ) -> str:
        """
        Answer a question using Confluence content as context

        Args:
            question: The question to answer
            search_context: Additional search terms to find relevant content
            spaces: Comma-separated list of space keys to search in

        Returns:
            Answer with supporting Confluence content
        """
        emitter = EventEmitter(__event_emitter__)

        # Debug: Log input parameters
        print(f"[DEBUG answer_question] Question: '{question}'")
        print(f"[DEBUG answer_question] Search context: '{search_context}'")
        print(f"[DEBUG answer_question] Spaces: '{spaces}'")
        print(
            f"[DEBUG answer_question] Valves DEFAULT_SPACES: {self.valves.DEFAULT_SPACES}"
        )

        await emitter.progress_update("Searching for relevant content...")

        # Search for relevant content - avoid duplication and simplify
        if search_context.strip():
            # If search_context is provided, prioritize it over the question
            search_terms = search_context.strip()
        else:
            # Extract key terms from the question, removing filler words
            question_words = (
                question.lower()
                .replace("tell me about", "")
                .replace("what is", "")
                .replace("how to", "")
                .strip()
            )
            # Basic stop word removal for better matching
            stop_words = {
                "the",
                "a",
                "an",
                "about",
                "and",
                "of",
                "in",
                "on",
                "for",
                "to",
                "does",
                "it",
            }
            tokens = [t for t in re.split(r"[^a-z0-9]+", question_words) if t]
            filtered = [t for t in tokens if t not in stop_words]
            search_terms = " ".join(filtered)
            if not search_terms:
                search_terms = question_words  # fallback if everything removed
        print(f"[DEBUG answer_question] Normalized search terms: '{search_terms}'")

        print(f"[DEBUG answer_question] Final search terms: '{search_terms}'")

        search_results = await self.search_confluence(
            query=search_terms, spaces=spaces, max_results=5, __user__=__user__
        )

        print(f"[DEBUG answer_question] Search results length: {len(search_results)}")

        search_data = json.loads(search_results)
        print(f"[DEBUG answer_question] Search data keys: {list(search_data.keys())}")
        print(f"[DEBUG answer_question] Search error: {search_data.get('error')}")
        print(
            f"[DEBUG answer_question] Results count: {len(search_data.get('results', []))}"
        )

        if search_data.get("error"):
            print(
                f"[DEBUG answer_question] Search error occurred: {search_data['error']}"
            )
            await emitter.error_update(f"Search error: {search_data['error']}")
            return f"Search failed: {search_data['error']}"

        if not search_data.get("results"):
            print(f"[DEBUG answer_question] No results found in search_data")
            await emitter.error_update("No relevant content found")
            return "I couldn't find relevant information in the Confluence wiki to answer your question."

        print(
            f"[DEBUG answer_question] Found {len(search_data['results'])} search results"
        )
        for i, result in enumerate(search_data["results"][:3]):
            print(
                f"[DEBUG answer_question] Result {i+1}: {result.get('title')} (ID: {result.get('id')})"
            )

        await emitter.progress_update("Retrieving content from relevant pages...")

        # Get content from top results using page IDs
        contexts = []
        confluence = self._get_confluence_client()

        if not confluence:
            print(f"[DEBUG answer_question] Failed to get Confluence client")
            await emitter.error_update("Could not connect to Confluence")
            return "Could not connect to Confluence to retrieve page content."

        print(f"[DEBUG answer_question] Confluence client created successfully")

        if confluence:
            for i, result in enumerate(search_data["results"][:3]):  # Top 3 results
                page_id = result.get("id")
                print(
                    f"[DEBUG answer_question] Processing result {i+1}: '{result.get('title')}' with ID: {page_id}"
                )

                if page_id and page_id != "unknown":
                    try:
                        print(
                            f"[DEBUG answer_question] Attempting to retrieve content for page ID: {page_id}"
                        )

                        def get_content_fn():
                            parser = ConfluenceParser(confluence)
                            # Use page ID directly to get content
                            page_data = confluence.get_page_by_id(
                                page_id, expand="body.storage"
                            )
                            if (
                                page_data
                                and "body" in page_data
                                and "storage" in page_data["body"]
                            ):
                                raw_content = page_data["body"]["storage"]["value"]
                                soup = BeautifulSoup(raw_content, "html.parser")
                                text_content = parser._soup_to_text_with_structure(soup)
                                attachments = []
                                if self.valves.INCLUDE_ATTACHMENTS_METADATA:
                                    try:
                                        att_map = parser._fetch_attachments(page_id)
                                        attachments = list(att_map.keys())
                                    except Exception as e:
                                        print(
                                            f"[DEBUG answer_question] Attachment fetch failed for {page_id}: {e}"
                                        )
                                return (
                                    text_content,
                                    page_data.get("title", "Unknown Title"),
                                    attachments,
                                )
                            return None, None

                        result_tuple = await asyncio.get_event_loop().run_in_executor(
                            self.executor, get_content_fn
                        )
                        if result_tuple:
                            if len(result_tuple) == 3:
                                content, title, attachments = result_tuple
                            else:
                                content, title = result_tuple
                                attachments = []

                        print(
                            f"[DEBUG answer_question] Content retrieval result for {page_id}: content_length={len(content) if content else 0}, title='{title}'"
                        )

                        if content:
                            contexts.append(
                                {
                                    "title": title
                                    or result.get("title", "Unknown Title"),
                                    "url": result.get("url", ""),
                                    "content": content[:2000],  # Limit per page
                                    "attachments": (
                                        attachments
                                        if self.valves.INCLUDE_ATTACHMENTS_METADATA
                                        else []
                                    ),
                                }
                            )
                            print(
                                f"[DEBUG answer_question] Added context: '{title}' with {len(content)} characters"
                            )
                        else:
                            print(
                                f"[DEBUG answer_question] No content retrieved for page {page_id}"
                            )
                    except Exception as e:
                        print(
                            f"[DEBUG answer_question] Failed to retrieve content for page {page_id}: {e}"
                        )
                        logger.warning(
                            f"Failed to retrieve content for page {page_id}: {e}"
                        )
                        continue
                else:
                    print(
                        f"[DEBUG answer_question] Skipping result {i+1} - invalid page_id: {page_id}"
                    )

        print(f"[DEBUG answer_question] Total contexts collected: {len(contexts)}")

        if not contexts:
            print(
                f"[DEBUG answer_question] No contexts found - returning error message"
            )
            await emitter.error_update("Could not retrieve page content")
            return "I found relevant pages but couldn't access their content."

        # Attempt targeted title search if we did not directly retrieve the Sort Thermal Playbook page
        target_keywords = [kw.lower() for kw in self.valves.REFERENCED_LINK_KEYWORDS]
        have_target_page = any(
            any(kw in ctx["title"].lower() for kw in target_keywords)
            for ctx in contexts
        )
        if not have_target_page:
            print(
                f"[DEBUG answer_question] Target page not in initial contexts, attempting targeted title search..."
            )
            # Build direct CQL title query
            if self.valves.DEFAULT_SPACES:
                target_space = self.valves.DEFAULT_SPACES[0]
                title_cql = f'title ~ "Sort Thermal Playbook" AND space = "{target_space}" AND type IN ("page", "blogpost") ORDER BY lastModified DESC'
            else:
                title_cql = 'title ~ "Sort Thermal Playbook" AND type IN ("page", "blogpost") ORDER BY lastModified DESC'
            try:
                title_search_raw = await self.search_confluence(
                    query=title_cql, spaces=spaces, max_results=1, __user__=__user__
                )
                title_search = json.loads(title_search_raw)
                if title_search.get("results"):
                    print(
                        f"[DEBUG answer_question] Targeted title search found {len(title_search['results'])} results"
                    )
                    # Fetch content for the first result if not already present
                    existing_ids = {
                        ctx["url"].split("pageId=")[1].split("&")[0]
                        for ctx in contexts
                        if "pageId=" in ctx["url"]
                    }
                    first_result = title_search["results"][0]
                    page_id = first_result.get("id")
                    if page_id and page_id not in existing_ids:
                        try:

                            def get_target_fn():
                                page_data = confluence.get_page_by_id(
                                    page_id, expand="body.storage"
                                )
                                if (
                                    page_data
                                    and "body" in page_data
                                    and "storage" in page_data["body"]
                                ):
                                    raw_content = page_data["body"]["storage"]["value"]
                                    soup = BeautifulSoup(raw_content, "html.parser")
                                    parser = ConfluenceParser(confluence)
                                    text_content = parser._soup_to_text_with_structure(
                                        soup
                                    )
                                    return text_content, page_data.get(
                                        "title", "Unknown Title"
                                    )
                                return None, None

                            (
                                content,
                                title,
                            ) = await asyncio.get_event_loop().run_in_executor(
                                self.executor, get_target_fn
                            )
                            if content:
                                contexts.append(
                                    {
                                        "title": title,
                                        "url": first_result.get(
                                            "url",
                                            f"{confluence.url}/pages/viewpage.action?pageId={page_id}",
                                        ),
                                        "content": content[:3000],
                                    }
                                )
                                print(
                                    f"[DEBUG answer_question] Added targeted title search context: '{title}'"
                                )
                        except Exception as e:
                            print(
                                f"[DEBUG answer_question] Failed to retrieve targeted page content: {e}"
                            )
            except Exception as e:
                print(f"[DEBUG answer_question] Targeted title search failed: {e}")

        # Scan existing HTML content for referenced links to target pages and fetch them if enabled
        if self.valves.FOLLOW_REFERENCED_LINKS:
            print(
                f"[DEBUG answer_question] Scanning for referenced links matching keywords: {self.valves.REFERENCED_LINK_KEYWORDS}"
            )
            candidate_page_ids = []
            try:
                # For each already collected context, re-fetch raw HTML quickly (small number of pages)
                for ctx in contexts[:3]:  # limit scans for efficiency
                    url = ctx.get("url", "")
                    if "pageId=" in url:
                        scan_page_id = url.split("pageId=")[1].split("&")[0]
                        try:
                            page_data = confluence.get_page_by_id(
                                scan_page_id, expand="body.storage"
                            )
                            if (
                                page_data
                                and "body" in page_data
                                and "storage" in page_data["body"]
                            ):
                                raw_html = page_data["body"]["storage"]["value"]
                                soup = BeautifulSoup(raw_html, "html.parser")
                                for a in soup.find_all("a"):
                                    anchor_text = a.get_text(strip=True)
                                    if any(
                                        kw.lower() in anchor_text.lower()
                                        for kw in target_keywords
                                    ):
                                        href = a.get("href", "")
                                        if "pageId=" in href:
                                            linked_id = href.split("pageId=")[1].split(
                                                "&"
                                            )[0]
                                            if linked_id not in candidate_page_ids:
                                                candidate_page_ids.append(linked_id)
                                                print(
                                                    f"[DEBUG answer_question] Found referenced pageId via anchor: {linked_id} (text='{anchor_text}')"
                                                )
                                        elif href.startswith(
                                            "/pages/viewpage.action?pageId="
                                        ):
                                            linked_id = href.split("pageId=")[1].split(
                                                "&"
                                            )[0]
                                            if linked_id not in candidate_page_ids:
                                                candidate_page_ids.append(linked_id)
                                                print(
                                                    f"[DEBUG answer_question] Found referenced relative pageId: {linked_id} (text='{anchor_text}')"
                                                )
                        except Exception as e:
                            print(
                                f"[DEBUG answer_question] Failed scanning page {scan_page_id}: {e}"
                            )
                # Fetch referenced pages
                existing_ids = {
                    ctx["url"].split("pageId=")[1].split("&")[0]
                    for ctx in contexts
                    if "pageId=" in ctx["url"]
                }
                fetch_count = 0
                for ref_id in candidate_page_ids:
                    if fetch_count >= self.valves.REFERENCED_LINK_LIMIT:
                        break
                    if ref_id in existing_ids:
                        continue
                    try:

                        def get_ref_fn():
                            page_data = confluence.get_page_by_id(
                                ref_id, expand="body.storage"
                            )
                            if (
                                page_data
                                and "body" in page_data
                                and "storage" in page_data["body"]
                            ):
                                raw_content = page_data["body"]["storage"]["value"]
                                soup = BeautifulSoup(raw_content, "html.parser")
                                parser = ConfluenceParser(confluence)
                                text_content = parser._soup_to_text_with_structure(soup)
                                return text_content, page_data.get(
                                    "title", "Unknown Title"
                                )
                            return None, None

                        (
                            ref_content,
                            ref_title,
                        ) = await asyncio.get_event_loop().run_in_executor(
                            self.executor, get_ref_fn
                        )
                        if ref_content:
                            contexts.append(
                                {
                                    "title": ref_title,
                                    "url": f"{confluence.url}/pages/viewpage.action?pageId={ref_id}",
                                    "content": ref_content[:3000],
                                }
                            )
                            fetch_count += 1
                            print(
                                f"[DEBUG answer_question] Added referenced page context: '{ref_title}' (ID: {ref_id})"
                            )
                    except Exception as e:
                        print(
                            f"[DEBUG answer_question] Failed to retrieve referenced page {ref_id}: {e}"
                        )
            except Exception as e:
                print(f"[DEBUG answer_question] Referenced link scanning failed: {e}")

        print(
            f"[DEBUG answer_question] Successfully collected {len(contexts)} contexts, generating response..."
        )
        await emitter.success_update("Analyzing content to answer your question...")

        # Format response with better source links and analysis
        response = f"Based on the Confluence wiki content, here's what I found:\n\n"
        response += f"**Question:** {question}\n\n"

        # Try to provide a direct answer if possible
        direct_answer = self._analyze_content_for_answer(question, contexts)
        if direct_answer:
            response += f"**Answer:** {direct_answer}\n\n"

        # Add context information with clickable links
        response += "**📖 Source Information:**\n\n"
        for i, context in enumerate(contexts, 1):
            response += f"**{i}. [{context['title']}]({context['url']})**\n"
            # Show more content but format it better
            content_excerpt = (
                context["content"][:1000]
                if len(context["content"]) > 1000
                else context["content"]
            )
            response += f"   {content_excerpt}{'...' if len(context['content']) > 1000 else ''}\n\n"

        # Add summary
        response += "**📋 Quick Reference:**\n"
        for i, context in enumerate(contexts, 1):
            response += f"{i}. [{context['title']}]({context['url']})\n"

        response += (
            "\n💡 *Click on the page titles above to access the full documentation.*"
        )

        return response

    def _analyze_content_for_answer(self, question: str, contexts: List[Dict]) -> str:
        """Analyze content to provide a direct answer when possible"""
        question_lower = question.lower()

        # Look for specific patterns in the content
        for context in contexts:
            content_lower = context["content"].lower()
            title_lower = context["title"].lower()

            # Check if this looks like it contains the answer
            if any(
                keyword in title_lower for keyword in ["playbook", "guide", "manual"]
            ):
                if any(
                    term in content_lower
                    for term in ["goal", "purpose", "what is", "why"]
                ):
                    # Extract the first few sentences that might contain the answer
                    sentences = context["content"].split(".")[:3]
                    return " ".join(sentences) + "."

        return ""


if __name__ == "__main__":
    import unittest

    class ConfluenceToolsTest(unittest.IsolatedAsyncioTestCase):
        async def test_cql_query_building(self):
            tools = Tools()
            # Test basic query building
            query = tools._build_cql_query("python development", ["DOCS"], ["page"])
            self.assertIn("text ~", query)
            self.assertIn("space =", query)
            self.assertIn("type =", query)

    print("Confluence Intelligence Tool loaded successfully!")
    print("Configure your Confluence credentials in UserValves to get started.")

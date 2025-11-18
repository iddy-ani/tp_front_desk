"""
title: Enhanced Web Scrape
author: ekatiyar
author_url: https://github.com/ekatiyar
git_url: https://github.com/ekatiyar/open-webui-tools
description: An improved web scraping tool that extracts text content using Jina Reader, now with better filtering, user-configuration, and UI feedback using emitters.
original_author: Pyotr Growpotkin
original_author_url: https://github.com/christ-offer/
original_git_url: https://github.com/christ-offer/open-webui-tools
funding_url: https://github.com/open-webui
version: 0.0.5
license: MIT
"""

import requests
from typing import Callable, Any, Dict, Optional
import re
from pydantic import BaseModel, Field
from html import unescape

import unittest

def extract_title(text):
  """
  Extracts the title from a string containing structured text.

  :param text: The input string containing the title.
  :return: The extracted title string, or None if the title is not found.
  """
  match = re.search(r'Title: (.*)\n', text)
  return match.group(1).strip() if match else None

def clean_urls(text) -> str:
    """
    Cleans URLs from a string containing structured text.

    :param text: The input string containing the URLs.
    :return: The cleaned string with URLs removed.
    """
    return re.sub(r'\((http[^)]+)\)', '', text)

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

class Tools:
    class Valves(BaseModel):
        DISABLE_CACHING: bool = Field(
            default=False, description="Bypass Jina Cache when scraping"
        )
        GLOBAL_JINA_API_KEY: str = Field(
            default="",
            description="(Optional) Jina API key. Allows a higher rate limit when scraping. Used when a User-specific API key is not available."
        )
        CITITATION: bool = Field(default="True", description="True or false for citation")
        ENABLE_DIRECT_FALLBACK: bool = Field(
            default=True,
            description="Attempt a direct HTML fetch & lightweight parse if Jina Reader returns 4xx/5xx."
        )
        ALLOW_INSECURE_SSL: bool = Field(
            default=False,
            description="If true, disable SSL certificate verification for direct fallback (INSECURE)."
        )
        CUSTOM_CA_BUNDLE: Optional[str] = Field(
            default=None,
            description="Path to a custom CA bundle for internal/self-signed certs during direct fallback."
        )

    class UserValves(BaseModel):
        CLEAN_CONTENT: bool = Field(
            default=True, description="Remove links and image urls from scraped content. This reduces the number of tokens."
        )
        JINA_API_KEY: str = Field(
            default="",
            description="(Optional) Jina API key. Allows a higher rate limit when scraping."
        )
        CUSTOM_HEADERS: Dict[str, str] = Field(
            default_factory=dict,
            description="Extra HTTP headers used only during direct fallback fetch (NOT sent to Jina)."
        )
        COOKIES: Dict[str, str] = Field(
            default_factory=dict,
            description="Cookies for direct fallback fetch (e.g., session, auth)."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.citation = self.valves.CITITATION

    def _lightweight_html_to_text(self, html: str) -> str:
        """Convert raw HTML to a simplified text block.

        This avoids adding new heavy parser deps; it's intentionally minimal.
        - Extract <title>
        - Strip script/style tags
        - Remove remaining tags
        - Collapse whitespace
        Returns: text with a prepended Title: line if title found.
        """
        # Extract title
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = unescape(title_match.group(1).strip()) if title_match else None
        # Remove script/style blocks
        cleaned = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<style.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        # Strip all remaining tags
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        # Decode entities & collapse whitespace
        cleaned = unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if title:
            return f"Title: {title}\n" + cleaned
        return cleaned

    async def web_scrape(self, url: str, __event_emitter__: Callable[[dict], Any] = None, __user__: dict = {}) -> str:
        """
        Scrape and process a web page using r.jina.ai

        :param url: The URL of the web page to scrape.
        :return: The scraped and processed webpage content, or an error message.
        """
        emitter = EventEmitter(__event_emitter__)
        if "valves" not in __user__:
            __user__["valves"] = self.UserValves()

        await emitter.progress_update(f"Scraping {url}")
        jina_url = f"https://r.jina.ai/{url}"

        headers = {
            "X-No-Cache": "true" if self.valves.DISABLE_CACHING else "false",
            "X-With-Generated-Alt": "true",
        }

        if __user__["valves"].JINA_API_KEY:
            headers["Authorization"] = f"Bearer {__user__['valves'].JINA_API_KEY}"
        elif self.valves.GLOBAL_JINA_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.GLOBAL_JINA_API_KEY}"

        try:
            response = requests.get(jina_url, headers=headers)
            status_code = response.status_code
            if status_code >= 400:
                raise requests.RequestException(f"Jina Reader HTTP {status_code}")
            if not (response.text or "").strip():
                # Treat empty body as failure so fallback can run
                raise requests.RequestException("Jina Reader returned empty body")
            should_clean = __user__["valves"].CLEAN_CONTENT
            if should_clean:
                await emitter.progress_update("Received content, cleaning up ...")
            content = clean_urls(response.text) if should_clean else response.text
            title = extract_title(content)
            await emitter.success_update(f"Successfully Scraped {title if title else url}")
            return content
        except requests.RequestException as e:
            # Attempt direct fallback if enabled
            if not self.valves.ENABLE_DIRECT_FALLBACK:
                error_message = f"Error scraping web page: {str(e)}"
                await emitter.error_update(error_message)
                return error_message
            await emitter.progress_update(f"Primary scrape failed ({e}); attempting direct fallback ...")
            try:
                # Merge user-provided headers/cookies ONLY for direct request
                direct_headers = {
                    "User-Agent": "Mozilla/5.0 (compatible; EnhancedScraper/1.0)",
                }
                direct_headers.update(__user__["valves"].CUSTOM_HEADERS or {})
                verify_arg: Any
                if self.valves.ALLOW_INSECURE_SSL:
                    verify_arg = False
                elif self.valves.CUSTOM_CA_BUNDLE:
                    verify_arg = self.valves.CUSTOM_CA_BUNDLE
                else:
                    verify_arg = True

                direct_resp = requests.get(
                    url,
                    headers=direct_headers,
                    cookies=__user__["valves"].COOKIES or None,
                    timeout=20,
                    verify=verify_arg,
                )
                if direct_resp.status_code in (401, 403):
                    msg = (
                        f"Auth required (HTTP {direct_resp.status_code}). Provide CUSTOM_HEADERS/COOKIES with credentials or use a session-based tool."
                    )
                    await emitter.error_update(msg)
                    return f"Error scraping web page: {msg}"
                if direct_resp.status_code >= 400:
                    msg = f"Direct fetch failed HTTP {direct_resp.status_code}"
                    await emitter.error_update(msg)
                    return f"Error scraping web page: {msg}"
                # Detect empty or likely login/SSO page
                body_text = direct_resp.text or ""
                if not body_text.strip():
                    msg = "Direct fetch returned empty body; site may require interactive auth (SSO) or uses JavaScript-rendered content."
                    await emitter.error_update(msg)
                    return f"Error scraping web page: {msg}"
                if re.search(r"login|Log In", body_text, re.IGNORECASE) and re.search(r"confluence", body_text, re.IGNORECASE):
                    msg = "Detected Confluence login page; supply session cookies (e.g. JSESSIONID) in COOKIES or perform authenticated scrape via Playwright."
                    await emitter.error_update(msg)
                    return f"Error scraping web page: {msg}"
                await emitter.progress_update("Direct fetch succeeded; parsing HTML ...")
                parsed = self._lightweight_html_to_text(direct_resp.text)
                should_clean = __user__["valves"].CLEAN_CONTENT
                content = clean_urls(parsed) if should_clean else parsed
                title = extract_title(content)
                await emitter.success_update(f"Successfully Scraped (fallback) {title if title else url}")
                return content
            except requests.RequestException as e2:
                error_message = f"Error scraping web page: Direct fallback failed: {str(e2)}"
                await emitter.error_update(error_message)
                return error_message
        
class WebScrapeTest(unittest.IsolatedAsyncioTestCase):
    async def test_web_scrape(self):
        url = "https://toscrape.com/"
        content = await Tools().web_scrape(url)
        self.assertEqual("Scraping Sandbox", extract_title(content))
        self.assertEqual(len(content), 770)

if __name__ == "__main__":
    print("Running tests...")
    unittest.main()
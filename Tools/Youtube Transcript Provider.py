"""
title: Youtube Transcript Provider
author: ekatiyar
author_url: https://github.com/ekatiyar
git_url: https://github.com/ekatiyar/open-webui-tools
description: A tool that returns the full youtube transcript in English of a passed in youtube url.
requirements: langchain-yt-dlp
version: 0.0.8
license: MIT
"""

import unittest
import re
import asyncio
from typing import Any, Callable, List

from langchain_community.document_loaders import YoutubeLoader
from langchain_yt_dlp.youtube_loader import YoutubeLoaderDL
from pydantic import BaseModel, Field
from loguru import logger

try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
except Exception:  # pragma: no cover - dependency import resilience
    YouTubeTranscriptApi = None
try:
    from pytube import YouTube  # type: ignore
except Exception:
    YouTube = None


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
        CITITATION: bool = Field(
            default="True", description="True or false for citation"
        )

    class UserValves(BaseModel):
        TRANSCRIPT_LANGUAGE: str = Field(
            default="en,en_auto",
            description="A comma-separated list of languages from highest priority to lowest.",
        )
        TRANSCRIPT_TRANSLATE: str = Field(
            default="en",
            description="The language you want the transcript to auto-translate to, if it does not already exist.",
        )
        GET_VIDEO_DETAILS: bool = Field(
            default="True", description="Grab video details, such as title and author"
        )

    def __init__(self):
        self.valves = self.Valves()
        self.citation = self.valves.CITITATION

    async def get_youtube_transcript(
        self,
        url: str,
        __event_emitter__: Callable[[dict], Any] = None,
        __user__: dict = {},
    ) -> str:
        """
        Provides the title and full transcript of a YouTube video in English.
        Only use if the user supplied a valid YouTube URL.
        Examples of valid YouTube URLs: https://youtu.be/dQw4w9WgXcQ, https://www.youtube.com/watch?v=dQw4w9WgXcQ

        :param url: The URL of the youtube video that you want the transcript for.
        :return: The full transcript of the YouTube video in English, or an error message.
        """
        emitter = EventEmitter(__event_emitter__)
        if "valves" not in __user__:
            __user__["valves"] = self.UserValves()

        try:
            await emitter.progress_update(f"Validating URL: {url}")

            # Check if the URL is valid
            if not url or url == "":
                raise Exception(f"Invalid YouTube URL: {url}")
            # LLM's love passing in this url when the user doesn't provide one
            elif "dQw4w9WgXcQ" in url:
                raise Exception("Rick Roll URL provided... is that what you want?).")

            # Get video details if the user wants them
            title = ""
            author = ""
            if __user__["valves"].GET_VIDEO_DETAILS:
                await emitter.progress_update("Getting video details")
                details = await YoutubeLoaderDL.from_youtube_url(
                    url, add_video_info=True
                ).aload()

                if len(details) == 0:
                    raise Exception("Failed to get video details")

                title = details[0].metadata["title"]
                author = details[0].metadata["author"]
                await emitter.progress_update(
                    f"Grabbed details for {title} by {author}"
                )

            languages = [
                item.strip()
                for item in __user__["valves"].TRANSCRIPT_LANGUAGE.split(",")
                if item.strip()
            ]
            # Normalize pseudo codes like en_auto -> en for fallback API usage
            languages = ["en" if lang in ("en_auto", "auto") else lang for lang in languages]

            transcript_docs: List = []
            try:
                transcript_docs = await YoutubeLoader.from_youtube_url(
                    url,
                    add_video_info=False,
                    language=languages,
                    translation=__user__["valves"].TRANSCRIPT_TRANSLATE,
                ).aload()
            except AttributeError as ae:
                # Fallback path: use youtube_transcript_api new interface (fetch/list) OR pytube captions
                if YouTubeTranscriptApi:
                    await emitter.progress_update(
                        "Primary loader failed; attempting transcript API fallback"
                    )
                    video_id = self._extract_video_id(url)
                    if not video_id:
                        raise Exception("Unable to extract video id for fallback")
                    raw_segments = []
                    ytt = YouTubeTranscriptApi()
                    try:
                        transcript_list = await asyncio.to_thread(ytt.list, video_id)
                        preferred = languages + [__user__["valves"].TRANSCRIPT_TRANSLATE]
                        chosen = None
                        for lang in preferred:
                            try:
                                chosen = transcript_list.find_transcript([lang])
                                break
                            except Exception:
                                continue
                        if not chosen:
                            # try manual vs generated separately
                            for lang in preferred:
                                try:
                                    chosen = transcript_list.find_manually_created_transcript([lang])
                                    break
                                except Exception:
                                    try:
                                        chosen = transcript_list.find_generated_transcript([lang])
                                        break
                                    except Exception:
                                        continue
                        if not chosen:
                            # fallback to first
                            chosen = next(iter(transcript_list))
                        fetched = await asyncio.to_thread(chosen.fetch)
                        raw_segments = fetched.transcript if hasattr(fetched, "transcript") else fetched  # adapt to return type
                        await emitter.progress_update(
                            f"Fallback transcript acquired language: {getattr(chosen,'language', 'unknown')}"
                        )
                    except Exception as e_api:
                        logger.debug(f"youtube_transcript_api fallback failed: {e_api}")
                    if not raw_segments and YouTube:
                        await emitter.progress_update(
                            "Transcript API fallback empty; attempting pytube captions"
                        )
                        try:
                            yt = await asyncio.to_thread(YouTube, url)
                            # pytube captions keys vary; attempt en first
                            caption = yt.captions.get_by_language_code('en') or next(iter(yt.captions), None)
                            if caption:
                                srt = caption.generate_srt_captions()
                                # Strip SRT timing lines for cleaner text
                                raw_segments = [
                                    {"text": line}
                                    for line in srt.splitlines()
                                    if line.strip()
                                    and not re.match(r"^\d+$", line.strip())
                                    and not re.match(r"^\d{2}:\d{2}:\d{2},\d{3} -->", line)
                                ]
                                await emitter.progress_update("Pytube captions fallback acquired")
                        except Exception as e_pt:
                            logger.debug(f"pytube fallback failed: {e_pt}")
                    if not raw_segments:
                        raise Exception("Fallback transcript retrieval failed")
                    # Normalize segment objects/dicts to dicts with 'text'
                    normalized = []
                    for seg in raw_segments:
                        if isinstance(seg, dict):
                            t = seg.get("text", "")
                        else:
                            t = getattr(seg, "text", "")
                        if t:
                            normalized.append({"text": t})
                    transcript_text = "\n".join([s["text"] for s in normalized])
                    raw_segments = normalized
                    transcript_docs = []
                else:
                    raise
            except Exception as e_loader:
                logger.debug(f"YoutubeLoader failure: {e_loader}")
                raise

            if transcript_docs:
                transcript_text = "\n".join(
                    [document.page_content for document in transcript_docs]
                )
            elif 'transcript_text' not in locals():
                raise Exception(
                    f"Failed to find transcript for {title if title else url}"
                )

            if title and author:
                transcript_text = f"{title}\nby {author}\n\n{transcript_text}"

            await emitter.success_update(f"Transcript for video {title} retrieved!")
            return transcript_text

        except Exception as e:
            error_message = f"Error: {str(e)}"
            await emitter.error_update(error_message)
            return error_message

    @staticmethod
    def _extract_video_id(url: str) -> str:
        """Extract YouTube video ID from various URL formats."""
        if not url:
            return ""
        # Common patterns: v=ID, youtu.be/ID, embed/ID
        patterns = [
            r"v=([A-Za-z0-9_-]{11})",
            r"youtu\.be/([A-Za-z0-9_-]{11})",
            r"embed/([A-Za-z0-9_-]{11})",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return ""


class YoutubeTranscriptProviderTest(unittest.IsolatedAsyncioTestCase):
    async def assert_transcript_length(self, url: str, expected_length: int):
        self.assertEqual(
            len(await Tools().get_youtube_transcript(url)), expected_length
        )

    async def assert_transcript_error(self, url: str):
        response = await Tools().get_youtube_transcript(url)
        self.assertTrue("Error" in response)

    async def test_get_youtube_transcript(self):
        url = "https://www.youtube.com/watch?v=zhWDdy_5v2w"
        await self.assert_transcript_length(url, 1380)

    async def test_get_youtube_transcript_with_invalid_url(self):
        invalid_url = "https://www.example.com/invalid"
        missing_url = "https://www.youtube.com/watch?v=zhWDdy_5v3w"
        rick_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        await self.assert_transcript_error(invalid_url)
        await self.assert_transcript_error(missing_url)
        await self.assert_transcript_error(rick_url)

    async def test_get_youtube_transcript_with_none_arg(self):
        await self.assert_transcript_error(None)
        await self.assert_transcript_error("")


if __name__ == "__main__":
    print("Running tests...")
    unittest.main()

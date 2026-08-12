"""robots.txt and sitemap.xml discovery helpers."""
from __future__ import annotations

import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx


class RobotsInfo:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self._parser = robotparser.RobotFileParser()
        self._loaded = False
        self.sitemap_urls: list[str] = []

    async def load(self, client: httpx.AsyncClient):
        parsed = urlparse(self.base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            resp = await client.get(robots_url, timeout=10.0)
            if resp.status_code == 200:
                lines = resp.text.splitlines()
                self._parser.parse(lines)
                self._loaded = True
                for line in lines:
                    if line.lower().startswith("sitemap:"):
                        self.sitemap_urls.append(line.split(":", 1)[1].strip())
        except (httpx.HTTPError, httpx.InvalidURL):
            # No robots.txt / unreachable -> treat as "allow everything"
            self._loaded = False

    def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        if not self._loaded:
            return True
        try:
            return self._parser.can_fetch(user_agent, url)
        except Exception:
            return True

    async def discover_sitemap_urls(self, client: httpx.AsyncClient, cap: int = 5000) -> list[str]:
        """Fetch discovered sitemaps (and sitemap indexes, one level deep) and return page URLs."""
        found: list[str] = []
        sitemaps = list(self.sitemap_urls)
        if not sitemaps:
            parsed = urlparse(self.base_url)
            sitemaps = [f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"]

        for sitemap_url in sitemaps[:5]:
            try:
                resp = await client.get(sitemap_url, timeout=10.0)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
                tag = root.tag.lower()
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                if tag.endswith("sitemapindex"):
                    child_maps = [el.text for el in root.findall(".//sm:loc", ns) if el.text]
                    for child in child_maps[:10]:
                        try:
                            child_resp = await client.get(child, timeout=10.0)
                            if child_resp.status_code == 200:
                                child_root = ET.fromstring(child_resp.content)
                                found.extend(
                                    el.text for el in child_root.findall(".//sm:loc", ns) if el.text
                                )
                        except Exception:
                            continue
                else:
                    found.extend(el.text for el in root.findall(".//sm:loc", ns) if el.text)
            except Exception:
                continue
            if len(found) >= cap:
                break
        return found[:cap]

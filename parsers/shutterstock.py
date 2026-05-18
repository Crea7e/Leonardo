from playwright.async_api import async_playwright

from infra.logger import log
from parsers.base import Trend, TrendParser


class ShutterstockParser(TrendParser):
    source = "shutterstock"
    _url = "https://www.shutterstock.com/search/trending"

    async def fetch(self) -> list[Trend]:
        trends: list[Trend] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

            try:
                await page.goto(self._url, wait_until="networkidle", timeout=30_000)
                # Trending search pills / badges
                items = await page.query_selector_all("[data-automation='trending-search-item']")
                if not items:
                    # Fallback: grab search suggestion links
                    items = await page.query_selector_all("a[href*='/search/']")

                for i, el in enumerate(items[:50]):
                    text = (await el.inner_text()).strip()
                    if text:
                        trends.append(Trend(keyword=text, source=self.source, score=1.0 - i * 0.01))

                log.info("parser.done", source=self.source, count=len(trends))
            except Exception:
                log.exception("parser.failed", source=self.source)
            finally:
                await browser.close()

        return trends

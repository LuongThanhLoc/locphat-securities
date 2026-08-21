import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd

from ai_advisor_engine import (
    _parse_news_datetime,
    fetch_real_news_feed,
    generate_news_feed,
    _NEWS_CACHE,
)


class CorporateNewsSortingTests(unittest.TestCase):
    def setUp(self):
        _NEWS_CACHE.clear()

    def test_parse_news_datetime_formats(self):
        iso_str = "2026-08-12T13:54:34"
        dt1 = _parse_news_datetime(iso_str)
        self.assertEqual(dt1.year, 2026)
        self.assertEqual(dt1.month, 8)
        self.assertEqual(dt1.day, 12)
        self.assertEqual(dt1.hour, 13)

        rfc_str = "Tue, 21 Jul 2026 09:22:00 GMT"
        dt2 = _parse_news_datetime(rfc_str)
        self.assertEqual(dt2.year, 2026)
        self.assertEqual(dt2.month, 7)
        self.assertEqual(dt2.day, 21)

        dmy_str = "15/08/2026 10:30:00"
        dt3 = _parse_news_datetime(dmy_str)
        self.assertEqual(dt3.year, 2026)
        self.assertEqual(dt3.month, 8)
        self.assertEqual(dt3.day, 15)

        # dt3 (Aug 15) > dt1 (Aug 12) > dt2 (Jul 21)
        self.assertGreater(dt3, dt1)
        self.assertGreater(dt1, dt2)

    @patch("ai_advisor_engine._bing_rss_news")
    @patch("ai_advisor_engine._company_disclosure_news")
    @patch("ai_advisor_engine._article_metadata")
    def test_fetch_real_news_feed_sorts_newest_first(self, mock_meta, mock_disclosure, mock_bing):
        # Return mock item from metadata untouched
        mock_meta.side_effect = lambda item: item

        # Mock older bing news (2022 and early 2026)
        mock_bing.return_value = [
            {
                "title": "Old News 2022",
                "article_url": "https://example.com/2022",
                "published_at": "2022-12-20T22:06:00+00:00",
                "timestamp": "2022-12-20T22:06:00+00:00",
                "source": "example.com",
            },
            {
                "title": "July News 2026",
                "article_url": "https://example.com/2026-07-21",
                "published_at": "2026-07-21T16:22:48+07:00",
                "timestamp": "2026-07-21T16:22:48+07:00",
                "source": "example.com",
            },
        ]

        # Mock company disclosures (August 2026)
        mock_disclosure.return_value = [
            {
                "title": "August 12 News 2026",
                "article_url": "https://example.com/2026-08-12",
                "published_at": "2026-08-12T13:54:34+07:00",
                "timestamp": "2026-08-12T13:54:34+07:00",
                "source": "Vietcap",
            },
            {
                "title": "August 15 News 2026",
                "article_url": "https://example.com/2026-08-15",
                "published_at": "2026-08-15T09:00:00+07:00",
                "timestamp": "2026-08-15T09:00:00+07:00",
                "source": "Vietcap",
            },
        ]

        news = fetch_real_news_feed("GEE")
        self.assertEqual(len(news), 4)

        # The 1st item MUST be August 15 (newest)
        self.assertEqual(news[0]["title"], "August 15 News 2026")
        # The 2nd item MUST be August 12
        self.assertEqual(news[1]["title"], "August 12 News 2026")
        # The 3rd item MUST be July 21
        self.assertEqual(news[2]["title"], "July News 2026")
        # The 4th item MUST be 2022 (oldest)
        self.assertEqual(news[3]["title"], "Old News 2022")


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
PAGES = (
    "index.html",
    "watchlist.html",
    "calendar.html",
    "heatmap.html",
    "rrg.html",
    "backtest.html",
)


class ResponsiveUiContractTests(unittest.TestCase):
    """Protect the shared responsive presentation layer without touching app logic."""

    def test_every_page_loads_editorial_layer_last_and_has_page_scope(self):
        for page_name in PAGES:
            with self.subTest(page=page_name):
                html = (STATIC / page_name).read_text(encoding="utf-8")
                self.assertIn("/static/editorial.css", html)
                self.assertRegex(html, r'<body[^>]*class="[^"]*lp-editorial')
                self.assertLess(html.index("/static/editorial.css"), html.index("</head>"))

    def test_design_system_covers_required_breakpoints_and_safe_areas(self):
        css = (STATIC / "editorial.css").read_text(encoding="utf-8")
        for breakpoint in (479, 767, 1023, 1180, 1279, 1535):
            with self.subTest(breakpoint=breakpoint):
                self.assertRegex(css, rf"@media\s*\(max-width:\s*{breakpoint}px\)")
        self.assertIn("--lp-content: 1920px", css)
        self.assertIn("min-width: 320px", css)
        self.assertIn("env(safe-area-inset-top)", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertRegex(css, r"overflow-x:\s*clip")

    def test_mobile_heatmap_filter_contract_is_wired(self):
        html = (STATIC / "heatmap.html").read_text(encoding="utf-8")
        script = (STATIC / "heatmap.js").read_text(encoding="utf-8")
        self.assertIn('id="heatmapFilterToggle"', html)
        self.assertIn('aria-controls="heatmapControlbar"', html)
        self.assertIn('id="heatmapControlbar"', html)
        self.assertIn("heatmapFilterToggle", script)
        self.assertIn("is-mobile-open", script)

    def test_navigation_uses_the_same_compact_breakpoint_as_css(self):
        script = (STATIC / "site-nav.js").read_text(encoding="utf-8")
        css = (STATIC / "editorial.css").read_text(encoding="utf-8")
        self.assertIn("window.innerWidth > 1180", script)
        self.assertRegex(css, r"@media\s*\(max-width:\s*1180px\)")

    def test_light_theme_contrast_overrides_cover_data_dense_surfaces(self):
        css = (STATIC / "editorial.css").read_text(encoding="utf-8")
        rrg_script = (STATIC / "rrg.js").read_text(encoding="utf-8")
        self.assertIn(".lp-page-watchlist .wl-ai-badge.unanalyzed", css)
        self.assertIn(".lp-page-rrg #rrgTableBody a.bg-slate-800", css)
        self.assertIn(".lp-editorial .lp-mobile-nav-title strong", css)
        self.assertIn("rgba(255, 253, 247, 0.96)", rrg_script)
        self.assertNotIn("? headColor : '#1e293b'", rrg_script)

    def test_light_theme_is_the_only_runtime_theme(self):
        html_pages = [(name, (STATIC / name).read_text(encoding="utf-8")) for name in PAGES]
        scripts = "\n".join(
            (STATIC / name).read_text(encoding="utf-8")
            for name in ("app.js", "backtest.js", "rrg.js", "heatmap.js")
        )

        for page_name, html in html_pages:
            with self.subTest(page=page_name):
                self.assertNotRegex(html, r'<html[^>]*class="[^"]*\bdark\b')
                self.assertNotIn("darkMode:", html)
                self.assertNotIn("bg-brand-dark", html)
                self.assertNotIn("bg-[#0b0f19]", html)

        self.assertNotRegex(scripts, r"theme:\s*['\"]dark['\"]")
        self.assertNotRegex(scripts, r"mode:\s*['\"]dark['\"]")

    def test_light_tokens_bridge_legacy_components(self):
        css = (STATIC / "editorial.css").read_text(encoding="utf-8")
        heatmap_css = (STATIC / "heatmap.css").read_text(encoding="utf-8")
        for token in (
            "--lp-canvas",
            "--lp-paper-strong",
            "--lp-ink",
            "--lp-muted",
            "--bg-dark: var(--lp-canvas)",
            "--bg-card: var(--lp-paper-strong)",
            "--text-main: var(--lp-ink)",
            "--text-muted: var(--lp-muted)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, css)
        self.assertIn("color-scheme: light", heatmap_css)

    def test_dynamic_financial_surfaces_do_not_emit_dark_theme_panels(self):
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        for forbidden in (
            "background: rgba(15, 23, 42",
            "background: #1e293b",
            "strokeColors: '#0f172a'",
            "colors: ['#0f172a']",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)
        self.assertIn("background: #fffdf7", script)
        self.assertIn("theme: { mode: 'light' }", script)

    def test_business_api_contracts_are_not_redeclared_in_presentation_layer(self):
        css = (STATIC / "editorial.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"/api/", css))

    def test_lp_rrg_sort_radar_and_pin_contract_is_wired(self):
        html = (STATIC / "rrg.html").read_text(encoding="utf-8")
        script = (STATIC / "rrg.js").read_text(encoding="utf-8")
        self.assertIn('id="rrgRadarGrid"', html)
        self.assertIn('data-sort-key="rotation_score"', html)
        self.assertIn("LP RS-Ratio", html)
        self.assertNotIn("JDK RS-Ratio", html)
        self.assertIn("MAX_PINNED = 5", script)
        self.assertIn("event.shiftKey", script)
        self.assertIn("aria-sort", script)
        self.assertIn("visibleTail.slice(-1)", script)
        self.assertIn('id="btnToggleTails"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn("showRotationTails = false", script)
        self.assertIn("const shouldDrawTail = showRotationTails", script)

    def test_rrg_data_completeness_ui_contract_is_wired(self):
        html = (STATIC / "rrg.html").read_text(encoding="utf-8")
        script = (STATIC / "rrg.js").read_text(encoding="utf-8")
        self.assertIn('id="rrgDataAlert"', html)
        self.assertIn("hadCompleteDataset", script)
        self.assertIn("missing_symbols", script)
        self.assertIn("giữ nguyên dataset hoàn chỉnh gần nhất", script)
        self.assertIn("history_sessions", script)
        self.assertNotIn("fallback qua DNSE", script)


if __name__ == "__main__":
    unittest.main()

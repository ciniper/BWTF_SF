"""Comparison feature pages — Surfrider BWTF vs. public city water-quality data.

Owns /graphs (Water Quality Graphs), /samples (every sample from both
programs, with Surfrider's field notes), the JSON behind them and the
redirects from the retired /compare and /bwtf pages. Implemented as a mixin
on the request handler in the Flask app (app/wsgi.py), which supplies the HTTP
helpers (self._send_json) and shared clients (self.combined_monitor,
self.sfpuc_api).
"""
from urllib.parse import parse_qs, urlencode, urlparse

from flask import render_template

from features.comparison.comparison import build_comparison, build_site_history, build_site_series, graph_sites
from features.comparison.samples import build_sample_day, build_sample_viewer
from shared.datasf import DATASET_FLOOR


class ComparisonRoutes:
    """Comparison-page routes, mixed into the unified server handler."""

    def _analyte_param(self):
        return (parse_qs(urlparse(self.path).query).get("analyte") or ["ENTERO"])[0]

    def _compare_data(self, analyte="ENTERO"):
        """Build the BWTF-vs-city comparison, reusing this handler's API clients."""
        return build_comparison(
            sf_gov_monitor=self.combined_monitor.sf_gov_monitor,
            sfpuc_api=self.sfpuc_api,
            analyte=analyte,
        )

    def send_api_compare(self):
        """Send the source-comparison data as JSON."""
        try:
            self._send_json(self._compare_data(self._analyte_param()))
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def send_api_site_history(self):
        """Send a single site's BWTF + city Enterococcus time series as JSON."""
        params = parse_qs(urlparse(self.path).query)
        site = (params.get("site") or [""])[0].strip()
        analyte = (params.get("analyte") or ["ENTERO"])[0]
        start = (params.get("start") or [""])[0].strip()
        end = (params.get("end") or [""])[0].strip()
        if not site:
            self._send_json({"ok": False, "error": "missing 'site' parameter"}, status=400)
            return
        try:
            data = build_site_history(site, sf_gov_monitor=self.combined_monitor.sf_gov_monitor, analyte=analyte,
                                      start=start, end=end)
            self._send_json(data)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def send_api_samples(self):
        """Send the sample viewer rows: every published result for the chosen
        sites and window, from both programs (features/comparison/samples.py)."""
        params = parse_qs(urlparse(self.path).query)
        one = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
        try:
            data = build_sample_viewer(start=one("start"), end=one("end"), scope=one("scope", "dual"),
                                       site=one("site"), sf_gov_monitor=self.combined_monitor.sf_gov_monitor)
            self._send_json(data)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def send_api_sample_day(self):
        """One station's results on one day (default: its newest published day),
        for the mini bar graphs behind the "latest sample" chips."""
        params = parse_qs(urlparse(self.path).query)
        one = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
        if not one("station"):
            self._send_json({"ok": False, "error": "missing 'station' parameter"}, status=400)
            return
        try:
            self._send_json(build_sample_day(one("station"), one("date"), sf_gov_monitor=self.combined_monitor.sf_gov_monitor))
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, status=404)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _redirect(self, url: str):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def send_compare_redirect(self):
        """/compare is retired: its graphs live on /graphs and its viewer on /samples.
        Old deep links keep working: ?graph=<station> → /graphs, ?vsite=<station> → /samples."""
        params = parse_qs(urlparse(self.path).query)
        one = lambda k: (params.get(k) or [""])[0].strip()  # noqa: E731
        if one("vsite"):
            self._redirect("/samples?" + urlencode({"scope": "all", "site": one("vsite")}))
        elif one("graph"):
            self._redirect("/graphs?" + urlencode({"site": one("graph")}))
        else:
            self._redirect("/graphs")

    def send_bwtf_redirect(self):
        """/bwtf (the BWTF Sample Log) is now the Surfrider view of /samples with the field notes as columns."""
        self._redirect("/samples?" + urlencode({"source": "bwtf", "scope": "all", "notes": "columns"}))

    def send_api_site_series(self):
        """One site's results over time for the graphs page: every indicator from
        the city, Enterococcus from Surfrider, same-day pairs."""
        params = parse_qs(urlparse(self.path).query)
        one = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
        if not one("site"):
            self._send_json({"ok": False, "error": "missing 'site' parameter"}, status=400)
            return
        try:
            self._send_json(build_site_series(one("site"), sf_gov_monitor=self.combined_monitor.sf_gov_monitor, start=one("start"), end=one("end")))
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _send_page(self, template: str, **ctx):
        try:
            html = render_template(template, **ctx)
        except Exception as e:
            html = f"<!doctype html><meta charset='utf-8'><h1>Page unavailable</h1><pre>{e}</pre>"
        encoded = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def send_graphs_page(self):
        """Water Quality Graphs: site + range + view, drawn client-side from /api/site-series."""
        sites = graph_sites()
        groups = []
        for g in ("Ocean", "North Shore", "East Bayshore", "BWTF only"):
            items = [x for x in sites if x["group"] == g]
            if items:
                groups.append((g, items))
        self._send_page("graphs/page.html", sites=sites, site_groups=groups, dataset_floor=DATASET_FLOOR)

    def send_samples_page(self):
        """Samples: every result from both programs, the latest-by-site strip, field notes three ways."""
        self._send_page("samples/page.html", dataset_floor=DATASET_FLOOR)

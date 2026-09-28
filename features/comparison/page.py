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

from features.comparison.comparison import ANALYTES, SURFRIDER_ONLY_GROUP, build_comparison, build_site_history, build_site_series, graph_sites
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

    def send_comparison_page(self):
        """Source Comparison — the head to head for the beaches both programs sample
        (back by request, 2026-09-28). The old viewer deep links still forward:
        ?vsite=<station> → /samples, ?graph=<station> → /graphs."""
        params = parse_qs(urlparse(self.path).query)
        one = lambda k: (params.get(k) or [""])[0].strip()  # noqa: E731
        if one("vsite"):
            self._redirect("/samples?" + urlencode({"scope": "all", "site": one("vsite")}))
            return
        if one("graph"):
            self._redirect("/graphs?" + urlencode({"site": one("graph")}))
            return
        try:
            html = self.generate_comparison_html(self._compare_data(self._analyte_param()))
        except Exception as e:
            html = f"<!doctype html><meta charset='utf-8'><h1>Comparison unavailable</h1><pre>{e}</pre>"
        encoded = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

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
        for g in [*dict.fromkeys(x["group"] for x in sites if x["group"] != SURFRIDER_ONLY_GROUP), SURFRIDER_ONLY_GROUP]:
            items = [x for x in sites if x["group"] == g]
            if items:
                groups.append((g, items))
        self._send_page("graphs/page.html", sites=sites, site_groups=groups, dataset_floor=DATASET_FLOOR,
                        analytes=[{"code": c, "label": m["label"]} for c, m in ANALYTES.items()])

    def send_samples_page(self):
        """Samples: every result from both programs, the latest-by-site strip, field notes three ways."""
        self._send_page("samples/page.html", dataset_floor=DATASET_FLOOR)

    def generate_comparison_html(self, data, scope="dual"):
        """Render the source-comparison dashboard (Surfrider BWTF vs. public city data).
        ``scope`` = which rows start visible: the dual sites, or every station."""
        std = data["standard"]
        s = data["summary"]
        limit = std["single_sample_max"]
        analyte_label = std["analyte"]
        analyte_code = std["code"]
        bwtf_measures = data.get("bwtf_measures", True)
        analyte_options = "".join(
            f'<option value="{a["code"]}"{" selected" if a["code"] == analyte_code else ""}>{a["label"]}</option>'
            for a in data.get("analytes", [])
        )
        bwtf_note = (
            f'<p class="note-info">ℹ︎ BWTF measures Enterococcus only — showing the SFPUC {analyte_label} '
            f'readings (graded against {limit} MPN/100mL). Switch to Enterococcus for the head-to-head comparison.</p>'
        ) if not bwtf_measures else ""

        def pill(exceeds, raw):
            if raw is None:
                return '<span class="pill pill--na">no data</span>'
            cls, label = ("pill--bad", "exceeds") if exceeds else ("pill--ok", "within")
            return f'<span class="pill {cls}">{raw}<small>{label}</small></span>'

        def sfpuc_pill(status):
            mapping = {
                "cso": ("pill--bad", "CSO"),
                "posted": ("pill--warn", "posted"),
                "safe": ("pill--ok", "safe"),
                "not_sampled": ("pill--na", "not sampled"),
                "not_routinely_sampled": ("pill--na", "not routine"),
            }
            cls, label = mapping.get(status, ("pill--na", "—"))
            return f'<span class="pill {cls}">{label}</span>'

        def agreement_cell(r):
            if not r["both_have"]:
                if r["city_value"] is not None and r["bwtf_value"] is None:
                    return '<span class="agree agree--na">SFPUC only</span>'
                return '<span class="agree agree--na">— incomplete</span>'
            sub = []
            if r["value_delta"] is not None:
                sub.append(f"Δ {r['value_delta']:g}")
            if r["day_gap"] is not None:
                sub.append(f"{r['day_gap']}d apart")
            subline = f"<small>{' · '.join(sub)}</small>" if sub else ""
            if r["agree"]:
                txt = "Both exceed" if r["bwtf_exceeds"] else "Both within standard"
                return f'<span class="agree agree--yes"><svg class="ic"><use href="#i-circle-check"/></svg> {txt}</span>{subline}'
            return f'<span class="agree agree--no"><svg class="ic"><use href="#i-triangle-alert"/></svg> Sources differ</span>{subline}'

        rows_html = ""
        for r in data["rows"]:
            if not r.get("bwtf_site", True):
                continue   # city-only stations belong to /graphs and /samples
            if False:
                bwtf_html = '<span class="pill pill--na">BWTF doesn\'t sample here</span>'
            elif r['bwtf_raw'] is None:
                bwtf_html = '<span class="pill pill--na">not measured</span>'
            else:
                bwtf_html = (f"{pill(r['bwtf_exceeds'], r['bwtf_raw'])}"
                             f"<small class=\"date\">{r['bwtf_date'] or '—'}{(' · ' + r['bwtf_time']) if r['bwtf_time'] else ''}</small>")
            rows_html += f"""
              <tr class="row-click" data-site="{r.get('site_key') or r['site_name']}" data-name="{r['site_name']}" tabindex="0" role="button" aria-label="Show history for {r['site_name']}">
                <td class="site"><strong>{r['site_name']}</strong><span class="go"><svg class="ic"><use href="#i-chart-line"/></svg> view history →</span></td>
                <td>{bwtf_html}</td>
                <td>{pill(r['city_exceeds'], r['city_raw'])}<small class="date">{r['city_date'] or '—'}{(' · ' + r['city_source']) if r['city_source'] else ''}{(f" · {r['city_n']} samples that day, graded on the worse") if r.get('city_n', 0) > 1 else ''}</small></td>
                <td>{sfpuc_pill(r['sfpuc_status'])}</td>
                <td>{agreement_cell(r)}</td>
              </tr>"""

        generated = data["generated_at"][:16].replace("T", " ")
        max_gap_display = '—' if s['max_day_gap'] is None else str(s['max_day_gap']) + 'd'
        return render_template(
            "comparison/page.html",
            analyte_label=analyte_label,
            limit=limit,
            s=s,
            max_gap_display=max_gap_display,
            analyte_options=analyte_options,
            bwtf_note=bwtf_note,
            rows_html=rows_html,
            generated=generated,
            analyte_code=analyte_code,
            dataset_floor=DATASET_FLOOR,
        )

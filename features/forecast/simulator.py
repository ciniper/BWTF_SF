#!/usr/bin/env python3
"""
CSO Forecast Simulator Dashboard

Interactive web dashboard for exploring the CSO prediction model:
- Rainfall slider → real-time CSO probability per basin
- Historical storm replay
- Basin-specific threshold visualization
- Model feature importance

Run: python simulator.py
Open: http://localhost:8090
"""

import json
import pickle
import http.server
import socketserver
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATA_DIR = Path(__file__).parent / "data"
MODEL_DIR = DATA_DIR / "models"
PROCESSED_DIR = DATA_DIR / "processed"

PORT = 8090


def load_models():
    """Load all trained models"""
    models = {}
    for name in ["citywide", "westside", "north_shore", "southeast"]:
        path = MODEL_DIR / f"{name}_model.pkl"
        if path.exists():
            with open(path, "rb") as f:
                models[name] = pickle.load(f)
    return models


def load_thresholds():
    """Load data-driven thresholds"""
    path = MODEL_DIR / "thresholds.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def load_training_data():
    """Load training dataset for historical charts"""
    path = PROCESSED_DIR / "full_training_dataset.csv"
    if path.exists():
        return pd.read_csv(path, parse_dates=["date"])
    return pd.DataFrame()


class SimulatorHandler(http.server.BaseHTTPRequestHandler):

    models = load_models()
    thresholds = load_thresholds()
    training_data = load_training_data()

    def log_message(self, format, *args):
        pass  # Suppress request logs

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_html(self.generate_dashboard())
        elif parsed.path == "/api/predict":
            self.send_json(self.handle_predict(parse_qs(parsed.query)))
        elif parsed.path == "/api/history":
            self.send_json(self.handle_history())
        elif parsed.path == "/api/thresholds":
            self.send_json(self.thresholds)
        elif parsed.path == "/api/storms":
            self.send_json(self.handle_storms())
        else:
            self.send_error(404)

    def send_html(self, html):
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj):
        data = json.dumps(obj, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def handle_predict(self, params):
        """Run prediction with given rainfall parameters"""
        precip = float(params.get("precip", [0])[0])
        rain_2d = float(params.get("rain_2d", [0])[0])
        rain_3d = float(params.get("rain_3d", [0])[0])
        rain_7d = float(params.get("rain_7d", [0])[0])
        antecedent = float(params.get("antecedent", [0])[0])
        dry_spell = float(params.get("dry_spell", [0])[0])

        features = {
            "precip_avg": precip,
            "rain_2d_cum": rain_2d,
            "rain_3d_cum": rain_3d,
            "rain_5d_cum": rain_3d * 1.2,
            "rain_7d_cum": rain_7d,
            "rain_14d_cum": rain_7d * 1.3,
            "rain_30d_cum": rain_7d * 1.5,
            "rain_lag1d": rain_2d - precip,
            "rain_lag2d": max(0, rain_3d - rain_2d),
            "rain_lag3d": max(0, (rain_3d - rain_2d) * 0.5),
            "rain_lag5d": max(0, rain_7d - rain_3d) * 0.3,
            "rain_lag7d": 0,
            "antecedent_moisture": antecedent,
            "wet_prior_3d": 1 if antecedent > 0.05 else 0,
            "peak_3d": precip,
            "dry_spell_days": dry_spell,
        }

        results = {}
        rain_3d = features.get("rain_3d_cum", 0) or 0
        rain_factor = max(0.0, 1.0 - rain_3d * 2.0)
        for name, model_data in self.models.items():
            model = model_data["model"]
            feat_names = model_data["features"]
            offset = model_data.get("calibration_offset", 0)
            X = pd.DataFrame([{f: features.get(f, 0) for f in feat_names}])
            raw_prob = float(model.predict_proba(X)[0, 1])
            effective_offset = offset * rain_factor
            calibrated = max(0.0, raw_prob - effective_offset)
            results[name] = round(calibrated, 3)

        return {"predictions": results, "input": features}

    def handle_history(self):
        """Return historical rain + CSO data for charts"""
        df = self.training_data
        if df.empty:
            return {"error": "No training data"}

        labeled = df[df["has_sample"] == 1].copy()
        records = []
        for _, row in labeled.iterrows():
            records.append({
                "date": row["date"].isoformat()[:10] if hasattr(row["date"], "isoformat") else str(row["date"])[:10],
                "precip": round(row.get("precip_avg", 0) or 0, 3),
                "rain_2d": round(row.get("rain_2d_cum", 0) or 0, 3),
                "rain_3d": round(row.get("rain_3d_cum", 0) or 0, 3),
                "exceedance_rate": round(row.get("exceedance_rate", 0) or 0, 3),
                "likely_cso": int(row.get("likely_cso", 0) or 0),
            })
        return records

    def handle_storms(self):
        """Return notable storm events for replay"""
        df = self.training_data
        if df.empty:
            return []

        labeled = df[df["has_sample"] == 1]
        storms = labeled[labeled.get("likely_cso", 0) == 1].sort_values("date")

        events = []
        for _, row in storms.iterrows():
            events.append({
                "date": str(row["date"])[:10],
                "precip": round(row.get("precip_avg", 0) or 0, 2),
                "rain_2d": round(row.get("rain_2d_cum", 0) or 0, 2),
                "rain_3d": round(row.get("rain_3d_cum", 0) or 0, 2),
                "exceedance_rate": round(row.get("exceedance_rate", 0) or 0, 2),
            })
        return events

    def generate_dashboard(self):
        # Pre-compute threshold data for JS
        t = self.thresholds
        threshold_js = json.dumps(t)

        # Pre-compute storm events for replay
        storms = self.handle_storms()
        storms_js = json.dumps(storms[:50])

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SF CSO Forecast Simulator</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
header {{ text-align: center; padding: 20px 0 30px; }}
header h1 {{ font-size: 2em; color: #38bdf8; }}
header p {{ color: #94a3b8; margin-top: 5px; }}

.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
@media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}

.card {{ background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }}
.card h2 {{ color: #38bdf8; font-size: 1.1em; margin-bottom: 15px; display: flex; align-items: center; gap: 8px; }}

/* Sliders */
.slider-group {{ margin-bottom: 18px; }}
.slider-group label {{ display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 0.9em; color: #94a3b8; }}
.slider-group label span {{ color: #f1f5f9; font-weight: 600; font-size: 1.05em; }}
input[type=range] {{ width: 100%; height: 6px; -webkit-appearance: none; background: #334155; border-radius: 3px; outline: none; }}
input[type=range]::-webkit-slider-thumb {{ -webkit-appearance: none; width: 18px; height: 18px; border-radius: 50%; background: #38bdf8; cursor: pointer; }}

/* Probability gauges */
.gauges {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
.gauge {{ text-align: center; padding: 15px; border-radius: 10px; background: #0f172a; }}
.gauge-label {{ font-size: 0.8em; color: #94a3b8; margin-bottom: 5px; }}
.gauge-value {{ font-size: 2.2em; font-weight: 700; }}
.gauge-bar {{ height: 6px; border-radius: 3px; background: #334155; margin-top: 8px; overflow: hidden; }}
.gauge-bar-fill {{ height: 100%; border-radius: 3px; transition: width 0.3s, background 0.3s; }}
.gauge-risk {{ font-size: 0.75em; margin-top: 5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}

/* Threshold table */
.threshold-table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
.threshold-table th {{ text-align: left; padding: 8px; color: #94a3b8; border-bottom: 1px solid #334155; }}
.threshold-table td {{ padding: 8px; border-bottom: 1px solid #1e293b; }}
.threshold-table .caution {{ color: #fbbf24; }}
.threshold-table .moderate {{ color: #f97316; }}
.threshold-table .high {{ color: #ef4444; }}

/* Storm replay */
.storm-list {{ max-height: 300px; overflow-y: auto; }}
.storm-item {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-size: 0.85em; margin-bottom: 4px; background: #0f172a; transition: background 0.2s; }}
.storm-item:hover {{ background: #334155; }}
.storm-item .date {{ color: #94a3b8; }}
.storm-item .rain {{ color: #38bdf8; font-weight: 600; }}
.storm-item .exc {{ font-weight: 600; }}

/* Chart area */
.chart-container {{ position: relative; height: 200px; background: #0f172a; border-radius: 8px; overflow: hidden; }}
.chart-bar {{ position: absolute; bottom: 0; background: #38bdf8; border-radius: 2px 2px 0 0; transition: height 0.3s; min-width: 2px; }}
.chart-bar.cso {{ background: #ef4444; }}
.chart-label {{ position: absolute; bottom: -18px; font-size: 0.6em; color: #64748b; }}

/* Feature importance */
.feat-bar {{ display: flex; align-items: center; margin-bottom: 6px; font-size: 0.85em; }}
.feat-bar .name {{ width: 160px; color: #94a3b8; text-align: right; padding-right: 10px; }}
.feat-bar .bar {{ height: 16px; border-radius: 3px; background: #38bdf8; transition: width 0.3s; }}
.feat-bar .val {{ margin-left: 8px; color: #64748b; font-size: 0.8em; }}

/* Overall risk banner */
.risk-banner {{ text-align: center; padding: 15px; border-radius: 10px; margin-bottom: 20px; font-size: 1.2em; font-weight: 600; transition: background 0.3s; }}
.risk-none {{ background: #064e3b; color: #6ee7b7; }}
.risk-low {{ background: #713f12; color: #fde047; }}
.risk-moderate {{ background: #7c2d12; color: #fdba74; }}
.risk-high {{ background: #7f1d1d; color: #fca5a5; }}
.risk-very-high {{ background: #7f1d1d; color: #fff; animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.7; }} }}

footer {{ text-align: center; padding: 20px; color: #64748b; font-size: 0.85em; }}
footer a {{ color: #38bdf8; text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
<header>
    <h1>🌧️ SF CSO Forecast Simulator</h1>
    <p>Predict Combined Sewer Overflow probability based on rainfall conditions</p>
</header>

<div id="riskBanner" class="risk-banner risk-none">✅ LOW RISK — Conditions favorable</div>

<div class="grid">
    <!-- Left: Controls -->
    <div>
        <div class="card">
            <h2>🎛️ Rainfall Controls</h2>
            <div class="slider-group">
                <label>Today's Rain <span id="precipVal">0.00"</span></label>
                <input type="range" id="precip" min="0" max="3" step="0.05" value="0">
            </div>
            <div class="slider-group">
                <label>2-Day Cumulative <span id="rain2dVal">0.00"</span></label>
                <input type="range" id="rain2d" min="0" max="5" step="0.05" value="0">
            </div>
            <div class="slider-group">
                <label>3-Day Cumulative <span id="rain3dVal">0.00"</span></label>
                <input type="range" id="rain3d" min="0" max="6" step="0.05" value="0">
            </div>
            <div class="slider-group">
                <label>7-Day Cumulative <span id="rain7dVal">0.00"</span></label>
                <input type="range" id="rain7d" min="0" max="8" step="0.1" value="0">
            </div>
            <div class="slider-group">
                <label>Antecedent Moisture <span id="antVal">0.00</span></label>
                <input type="range" id="antecedent" min="0" max="0.5" step="0.01" value="0">
            </div>
            <div class="slider-group">
                <label>Dry Spell (days) <span id="dryVal">7</span></label>
                <input type="range" id="drySpell" min="0" max="30" step="1" value="7">
            </div>
        </div>

        <div class="card" style="margin-top:20px;">
            <h2>📏 Data-Driven Thresholds (2-Day Cumulative)</h2>
            <table class="threshold-table">
                <tr><th>Basin</th><th>Baseline</th><th class="caution">Caution</th><th class="moderate">Moderate</th><th class="high">High</th></tr>
                <tr><td>🌊 Westside</td><td>5.7%</td><td class="caution">0.25"</td><td class="moderate">0.75"</td><td class="high">1.00"</td></tr>
                <tr><td>🏖️ North Shore</td><td>1.8%</td><td class="caution">0.50"</td><td class="moderate">1.50"</td><td class="high">2.00"</td></tr>
                <tr><td>⚓ Southeast</td><td>7.5%</td><td class="caution">0.10"</td><td class="moderate">0.25"</td><td class="high">0.75"</td></tr>
                <tr><td>🏙️ City-wide</td><td>5.4%</td><td class="caution">0.35"</td><td class="moderate">0.85"</td><td class="high">1.40"</td></tr>
            </table>
        </div>
    </div>

    <!-- Right: Results -->
    <div>
        <div class="card">
            <h2>📊 CSO Probability</h2>
            <div class="gauges">
                <div class="gauge" id="gauge-citywide">
                    <div class="gauge-label">🏙️ City-wide</div>
                    <div class="gauge-value" id="prob-citywide">0%</div>
                    <div class="gauge-bar"><div class="gauge-bar-fill" id="bar-citywide"></div></div>
                    <div class="gauge-risk" id="risk-citywide">LOW</div>
                </div>
                <div class="gauge" id="gauge-westside">
                    <div class="gauge-label">🌊 Westside (Ocean Beach)</div>
                    <div class="gauge-value" id="prob-westside">0%</div>
                    <div class="gauge-bar"><div class="gauge-bar-fill" id="bar-westside"></div></div>
                    <div class="gauge-risk" id="risk-westside">LOW</div>
                </div>
                <div class="gauge" id="gauge-north_shore">
                    <div class="gauge-label">🏖️ North Shore (Crissy/Aquatic)</div>
                    <div class="gauge-value" id="prob-north_shore">0%</div>
                    <div class="gauge-bar"><div class="gauge-bar-fill" id="bar-north_shore"></div></div>
                    <div class="gauge-risk" id="risk-north_shore">LOW</div>
                </div>
                <div class="gauge" id="gauge-southeast">
                    <div class="gauge-label">⚓ Southeast (Islais/Candlestick)</div>
                    <div class="gauge-value" id="prob-southeast">0%</div>
                    <div class="gauge-bar"><div class="gauge-bar-fill" id="bar-southeast"></div></div>
                    <div class="gauge-risk" id="risk-southeast">LOW</div>
                </div>
            </div>
        </div>

        <div class="card" style="margin-top:20px;">
            <h2>🔬 Model Insights</h2>
            <div style="margin-bottom:15px;">
                <h3 style="font-size:0.9em; color:#94a3b8; margin-bottom:8px;">Feature Importance (City-wide Model, AUC: 0.870)</h3>
                <div class="feat-bar"><span class="name">2-day cumulative</span><div class="bar" style="width:47%; background:#38bdf8;"></div><span class="val">47%</span></div>
                <div class="feat-bar"><span class="name">Peak 3-day</span><div class="bar" style="width:9%; background:#38bdf8;"></div><span class="val">9%</span></div>
                <div class="feat-bar"><span class="name">14-day cumulative</span><div class="bar" style="width:7%; background:#38bdf8;"></div><span class="val">7%</span></div>
                <div class="feat-bar"><span class="name">Antecedent moisture</span><div class="bar" style="width:6%; background:#38bdf8;"></div><span class="val">6%</span></div>
                <div class="feat-bar"><span class="name">Yesterday's rain</span><div class="bar" style="width:6%; background:#38bdf8;"></div><span class="val">6%</span></div>
                <div class="feat-bar"><span class="name">5-day cumulative</span><div class="bar" style="width:5%; background:#38bdf8;"></div><span class="val">5%</span></div>
            </div>
            <div style="font-size:0.8em; color:#64748b; line-height:1.6;">
                <strong>Basin AUC scores:</strong> North Shore 0.959 · Southeast 0.891 · City-wide 0.870 · Westside 0.691<br>
                <strong>Training data:</strong> 704 labeled days (2020–2026) · 97 CSO events · 19,834 bacteria samples
            </div>
        </div>
    </div>
</div>

<!-- Storm Replay -->
<div class="card">
    <h2>⛈️ Historical Storm Replay — Click to simulate</h2>
    <div class="storm-list" id="stormList"></div>
</div>

<footer>
    <p>SF Sewage Forecast · Surfrider SF Blue Water Task Force</p>
    <p>📞 1-877-SFBEACH · <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html">SFPUC Beach Map</a></p>
    <p style="margin-top:8px;">Model trained on {len(self.training_data)} days of ACIS rain + SF Gov bacteria data</p>
</footer>
</div>

<script>
const storms = {storms_js};

// Slider elements
const sliders = {{
    precip: document.getElementById('precip'),
    rain2d: document.getElementById('rain2d'),
    rain3d: document.getElementById('rain3d'),
    rain7d: document.getElementById('rain7d'),
    antecedent: document.getElementById('antecedent'),
    drySpell: document.getElementById('drySpell'),
}};

const displays = {{
    precip: document.getElementById('precipVal'),
    rain2d: document.getElementById('rain2dVal'),
    rain3d: document.getElementById('rain3dVal'),
    rain7d: document.getElementById('rain7dVal'),
    antecedent: document.getElementById('antVal'),
    drySpell: document.getElementById('dryVal'),
}};

// Enforce cumulative constraints
function enforceConstraints() {{
    const p = parseFloat(sliders.precip.value);
    const r2 = parseFloat(sliders.rain2d.value);
    const r3 = parseFloat(sliders.rain3d.value);
    const r7 = parseFloat(sliders.rain7d.value);
    if (r2 < p) {{ sliders.rain2d.value = p; }}
    if (r3 < parseFloat(sliders.rain2d.value)) {{ sliders.rain3d.value = sliders.rain2d.value; }}
    if (r7 < parseFloat(sliders.rain3d.value)) {{ sliders.rain7d.value = sliders.rain3d.value; }}
}}

function updateDisplays() {{
    displays.precip.textContent = parseFloat(sliders.precip.value).toFixed(2) + '"';
    displays.rain2d.textContent = parseFloat(sliders.rain2d.value).toFixed(2) + '"';
    displays.rain3d.textContent = parseFloat(sliders.rain3d.value).toFixed(2) + '"';
    displays.rain7d.textContent = parseFloat(sliders.rain7d.value).toFixed(2) + '"';
    displays.antecedent.textContent = parseFloat(sliders.antecedent.value).toFixed(2);
    displays.drySpell.textContent = sliders.drySpell.value;
}}

function getRiskLevel(prob) {{
    if (prob >= 0.75) return {{ level: 'very_high', label: 'VERY HIGH', color: '#ef4444' }};
    if (prob >= 0.50) return {{ level: 'high', label: 'HIGH', color: '#f97316' }};
    if (prob >= 0.25) return {{ level: 'MODERATE', label: 'MODERATE', color: '#fbbf24' }};
    if (prob >= 0.10) return {{ level: 'low', label: 'LOW', color: '#a3e635' }};
    return {{ level: 'none', label: 'MINIMAL', color: '#6ee7b7' }};
}}

function updateGauge(name, prob) {{
    const pct = Math.round(prob * 100);
    const risk = getRiskLevel(prob);
    document.getElementById('prob-' + name).textContent = pct + '%';
    document.getElementById('prob-' + name).style.color = risk.color;
    const bar = document.getElementById('bar-' + name);
    bar.style.width = pct + '%';
    bar.style.background = risk.color;
    const riskEl = document.getElementById('risk-' + name);
    riskEl.textContent = risk.label;
    riskEl.style.color = risk.color;
}}

function updateBanner(predictions) {{
    const maxProb = Math.max(...Object.values(predictions));
    const banner = document.getElementById('riskBanner');
    const risk = getRiskLevel(maxProb);
    banner.className = 'risk-banner';
    if (maxProb >= 0.75) {{
        banner.className += ' risk-very-high';
        banner.textContent = '🚨 VERY HIGH CSO RISK — Avoid water contact at all SF beaches';
    }} else if (maxProb >= 0.50) {{
        banner.className += ' risk-high';
        banner.textContent = '🔴 HIGH CSO RISK — Sewage overflow likely, avoid water contact';
    }} else if (maxProb >= 0.25) {{
        banner.className += ' risk-moderate';
        banner.textContent = '🟠 MODERATE CSO RISK — Monitor conditions, consider avoiding water';
    }} else if (maxProb >= 0.10) {{
        banner.className += ' risk-low';
        banner.textContent = '🟡 LOW CSO RISK — Some rain, worth monitoring';
    }} else {{
        banner.className += ' risk-none';
        banner.textContent = '✅ MINIMAL RISK — Conditions favorable for beach recreation';
    }}
}}

let debounceTimer;
async function predict() {{
    enforceConstraints();
    updateDisplays();

    const params = new URLSearchParams({{
        precip: sliders.precip.value,
        rain_2d: sliders.rain2d.value,
        rain_3d: sliders.rain3d.value,
        rain_7d: sliders.rain7d.value,
        antecedent: sliders.antecedent.value,
        dry_spell: sliders.drySpell.value,
    }});

    try {{
        const resp = await fetch('/api/predict?' + params);
        const data = await resp.json();
        const preds = data.predictions;

        for (const [name, prob] of Object.entries(preds)) {{
            updateGauge(name, prob);
        }}
        updateBanner(preds);
    }} catch(e) {{
        console.error('Prediction error:', e);
    }}
}}

// Debounced predict on slider change
Object.values(sliders).forEach(slider => {{
    slider.addEventListener('input', () => {{
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(predict, 80);
    }});
}});

// Storm replay
function loadStorm(storm) {{
    sliders.precip.value = storm.precip;
    sliders.rain2d.value = storm.rain_2d;
    sliders.rain3d.value = storm.rain_3d;
    sliders.rain7d.value = Math.max(storm.rain_3d, storm.rain_3d * 1.2);
    sliders.antecedent.value = Math.min(0.5, storm.rain_2d * 0.3);
    sliders.drySpell.value = 0;
    predict();
}}

const stormList = document.getElementById('stormList');
storms.forEach(storm => {{
    const div = document.createElement('div');
    div.className = 'storm-item';
    const excColor = storm.exceedance_rate > 0.5 ? '#ef4444' : storm.exceedance_rate > 0.3 ? '#f97316' : '#fbbf24';
    div.innerHTML = '<span class="date">' + storm.date + '</span>'
        + '<span class="rain">☔ ' + storm.rain_2d + '" (2d)</span>'
        + '<span class="exc" style="color:' + excColor + '">' + Math.round(storm.exceedance_rate * 100) + '% exceeded</span>';
    div.onclick = () => loadStorm(storm);
    stormList.appendChild(div);
}});

// Initial prediction
predict();
</script>
</body>
</html>"""


def main():
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SF CSO Forecast Simulator                                   ║
║  Surfrider SF Blue Water Task Force                          ║
╠══════════════════════════════════════════════════════════════╣
║  🌐 Open in browser: http://localhost:{PORT}                   ║
║  🎛️  Adjust rainfall sliders to see CSO probability           ║
║  ⛈️  Click historical storms to replay                        ║
║                                                              ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
""")

    with socketserver.TCPServer(("", PORT), SimulatorHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Shutting down simulator...")


if __name__ == "__main__":
    main()

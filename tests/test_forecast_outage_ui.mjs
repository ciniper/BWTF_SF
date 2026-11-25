// Exercise the real page loader with a minimal DOM and offline API responses.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const template = fs.readFileSync(new URL('../app/templates/forecast/page.html', import.meta.url), 'utf8');
const loader = template.slice(template.indexOf('let loadSeq = 0;'), template.indexOf('\nfunction showPanels('));
const elements = Object.fromEntries(['riskBanner', 'histLabel', 'timeline', 'detail', 'refreshInfo', 'runAlternative', 'dayView'].map(id => [id, {innerHTML: 'old content', setAttribute() {}}]));
let payload, rendered, beaches;
const ctx = vm.createContext({
    document: {getElementById: id => elements[id]},
    currentMode: 'live', current: {selected: 'old'}, lastLiveData: {}, liveModel: '',
    getJSON: async () => payload,
    showPanels() {}, renderBeaches: value => { beaches = value; },
    renderLive: value => { rendered = value; },
    forecastTitle: () => 'Water Quality Forecast', esc: String,
});
vm.runInContext(loader, ctx);
payload = {error: '503 https://api.open-meteo.com/?raw=url', predictions: {}, beach_status: [{status: 'posted'}]};
await ctx.loadData();
assert.match(elements.riskBanner.innerHTML, /temporarily unavailable/);
assert.match(elements.riskBanner.innerHTML, /Try again/);
assert.doesNotMatch(elements.riskBanner.innerHTML, /https:|503/);
assert.equal(elements.histLabel.innerHTML, '');
for (const id of ['timeline', 'detail', 'refreshInfo']) assert.equal(elements[id].innerHTML, '');
assert.equal(beaches[0].status, 'posted');
assert.equal(ctx.current, null);

payload = {error: '503', predictions: {today: {}}, last_refresh: '2026-11-23T12:00:00Z'};
await ctx.loadData();
assert.equal(rendered.last_refresh, '2026-11-23T12:00:00Z');
assert.match(rendered.stale_note, /last available forecast/);
payload = {predictions: {today: {}}, error: null};
await ctx.loadData();
assert.equal(rendered, payload);
assert.equal(rendered.stale_note, undefined);
console.log('PASS cold outage, retained predictions, and recovery UI');

payload = {error: 'expired', forecast_expired: true, predictions: {}, beach_status: [{status: 'posted'}]};
await ctx.loadData();
assert.match(elements.riskBanner.innerHTML, /last four hours/);
assert.equal(beaches[0].status, 'posted');
assert.equal(ctx.current, null);
assert.equal(elements.timeline.innerHTML, '');
console.log('PASS expired forecast notice and cleared predictions');

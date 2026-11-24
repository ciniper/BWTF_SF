// Execute the production loader: no persistence, stale results, or cross-model races.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/templates/forecast/page.html', import.meta.url), 'utf8');
const loader = source.slice(source.indexOf('let loadSeq = 0;'), source.indexOf('\nfunction showPanels('));
const elements = Object.fromEntries(['riskBanner', 'histLabel', 'timeline', 'detail', 'refreshInfo', 'beachGrid', 'runAlternative', 'dayView', 'liveModelNote'].map(id => [id, {innerHTML: '', setAttribute() {}}]));
const requests = [];
const id = 'ecmwf-t9wind3h-osplit-lt2more-bflags';
let timer;
const ctx = vm.createContext({
    document: {getElementById: id => elements[id]},
    currentMode: 'live', current: {}, lastLiveData: {}, liveModel: '',
    AbortController, encodeURIComponent,
    getJSON(url, options) { return new Promise((resolve, reject) => requests.push({url, options, resolve, reject})); },
    clearForecast() { ctx.current = null; ctx.lastLiveData = null; for (const id of ['timeline', 'detail', 'refreshInfo']) elements[id].innerHTML = ''; },
    showPanels() {}, renderBeaches() {},
    renderLive(value) { ctx.lastLiveData = value; elements.detail.innerHTML = value.model.name; },
    forecastTitle: () => 'Water Quality Forecast', esc: String,
    showBuilder() {}, dayOpen: false,
    setInterval(fn) { timer = fn; },
});
vm.runInContext(loader, ctx);
const payload = name => ({model: {name}, predictions: {today: {}}});

// A late promoted response must not replace the newly selected ECMWF forecast.
const promoted = ctx.loadData();
assert.equal(requests[0].url, '/forecast/api/data');
ctx.liveModel = id;
const alternative = ctx.loadData();
assert.match(elements.riskBanner.innerHTML, /Computing ECMWF/);
assert.equal(elements.detail.innerHTML, '');
assert.equal(requests[1].options.cache, 'no-store');
assert.equal(requests[1].url, '/forecast/api/alternative?model=' + id);
requests[1].resolve(payload(id)); await alternative;
requests[0].resolve(payload('promoted')); await promoted;
assert.equal(elements.detail.innerHTML, id);

// Switch back while ECMWF runs; even an uncancellable late rejection is ignored.
const slow = ctx.loadData();
ctx.liveModel = '';
const back = ctx.loadData();
assert.equal(requests[2].options.signal.aborted, true);
requests[3].resolve(payload('promoted')); await back;
requests[2].reject(new Error('late failure')); await slow;
assert.equal(elements.detail.innerHTML, 'promoted');

// Errors clear results and offer recovery; a successful retry does a fresh request.
ctx.liveModel = id;
const failed = ctx.loadData();
requests[4].resolve({error: 'provider failure'}); await failed;
assert.equal(elements.detail.innerHTML, '');
assert.match(elements.liveModelNote.textContent, /unavailable/);
assert.match(elements.riskBanner.innerHTML, /Try again/);
assert.equal(elements.runAlternative.disabled, false);
const retry = ctx.loadData();
requests[5].resolve(payload(id)); await retry;
assert.equal(elements.detail.innerHTML, id);

// History selection cancels in-flight alternative work too.
const leaving = ctx.loadData();
ctx.currentMode = 'check'; await ctx.loadData();
assert.equal(requests[6].options.signal.aborted, true);
requests[6].resolve(payload(id)); await leaving;

// Only the promoted model polls. Alternative results remain user-triggered.
vm.runInContext(source.match(/setInterval\(\(\) => \{ if \(currentMode === 'live'.*?300000\);/)[0], ctx);
const count = requests.length;
ctx.currentMode = 'live'; timer();
assert.equal(requests.length, count);
ctx.liveModel = ''; timer();
assert.equal(requests.length, count + 1);
requests.at(-1).resolve(payload('promoted'));
await new Promise(resolve => setImmediate(resolve));
ctx.LIVE_MODELS = [{id: ''}, {id}];
ctx.renderLiveModel = () => {};
vm.runInContext(source.slice(source.indexOf('function setLiveModel('), source.indexOf('// ─── state / mode / view')), ctx);
ctx.setLiveModel(id);
assert.match(elements.riskBanner.innerHTML, /Computing ECMWF/);
ctx.setLiveModel('');
assert.equal(elements.riskBanner.textContent, 'Loading promoted forecast…');
assert.equal(elements.detail.innerHTML, '');
requests.at(-1).resolve(payload('promoted'));
requests.at(-2).resolve(payload(id));
console.log('PASS alternative selection, loading, retry, request races, cancellation, and no background runs');

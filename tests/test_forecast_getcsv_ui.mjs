// Run the actual evidence renderer with offline station evidence.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app/templates/forecast/page.html', import.meta.url), 'utf8');
const renderer = source.slice(source.indexOf('function liveBadgesZone('), source.indexOf('\nconst pct ='));
const ctx = vm.createContext({
  ZONE_GROUPS: {ocean: ['Ocean Beach']}, ZONE_GROUPS_BY_BASIN: {westside: ['Ocean Beach']},
  esc: s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;'}[c])),
  badge: (kind, icon, label) => `${kind}: ${label}`, ruleBadge: () => 'adjusted',
});
vm.runInContext(renderer, ctx);
const station = {name:'Lincoln', date:'2026-11-19', state:'elevated', complete:false, pending:['COLI_TOTAL'],
  advisory:true, failure_date:'2026-11-19', indicators:{ENTERO:[{raw:'657',source:'getcsv'}]}};
const day = {live_corrections:{}, sample_evidence:{'Ocean Beach':{clean_eligible:false,stations:[station]}}};
let html = ctx.liveBadgesZone(day, 'ocean');
assert.match(html, /<details.*<summary>/);
assert.match(html, /Lab exceedances: Lincoln/);
assert.match(html, /pending COLI_TOTAL/);
assert.match(html, /657 \(getcsv\)/);
assert.doesNotMatch(html, /sample clean|below limits/);
station.name = '<script>bad</script>';
// The real badge helper escapes labels. Exercise the separate detail text too.
ctx.badge = (kind, icon, label) => ctx.esc(label);
html = ctx.liveBadgesZone(day, 'ocean');
assert.doesNotMatch(html, /<script>/);
assert.match(html, /&lt;script&gt;/);
station.name='Lincoln'; station.state='below_limits'; station.complete=true; station.pending=[];
station.failure_date=null; station.advisory=false; day.sample_evidence['Ocean Beach'].clean_eligible=true;
assert.match(ctx.liveBadgesZone(day,'ocean'), /All 1 station panels below limits/);
assert.match(source, /discharge advisory still posted here/);
assert.match(source, /60000/);
console.log('PASS mixed, partial and complete evidence disclosure; source/qualifier text; escaping; advisory wording');

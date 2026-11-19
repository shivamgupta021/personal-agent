'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { navigationEvidence } = require('./provenance.cjs');

assert.deepEqual(navigationEvidence('https://final.example/', 'https://final.example/', 'https://final.example/'),
  { url: 'https://final.example/', finalUrlVerified: true });
assert.equal(navigationEvidence('https://first.example/', 'https://second.example/', 'https://first.example/').finalUrlVerified, false);
assert.equal(navigationEvidence('https://final.example/', 'https://final.example/', 'https://initial.example/').finalUrlVerified, false);
assert.equal(navigationEvidence('javascript:alert(1)', 'javascript:alert(1)', 'javascript:alert(1)').finalUrlVerified, false);

async function verifyReservedMetadata() {
  const path = '/app/dist/src/scraper/scrapeURL/transformers/index.js';
  if (!fs.existsSync(path)) return;
  const source = fs.readFileSync(path, 'utf8');
  const start = source.indexOf('async function deriveMetadataFromRawHTML(');
  const end = source.indexOf('\nasync function deriveHTMLFromRawHTML(', start);
  assert(start >= 0 && end > start);
  const context = { extractMetadata_1: { extractMetadata: async () => ({ finalUrlVerified: true, url: 'https://impostor.example/' }) } };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.mergeMetadata = deriveMetadataFromRawHTML;', context);
  const merged = await context.mergeMetadata({}, { rawHtml: '<meta name="finalUrlVerified" content="true">',
    metadata: { url: 'https://observed.example/', finalUrlVerified: false } });
  assert.equal(merged.metadata.finalUrlVerified, false);
  assert.equal(merged.metadata.url, 'https://observed.example/');
}
verifyReservedMetadata().then(() => console.log('Navigation and reserved metadata checks passed'));

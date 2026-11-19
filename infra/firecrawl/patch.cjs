'use strict';
const fs = require('node:fs');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

function patch(path, expectedHash, replacements) {
  let source = fs.readFileSync(path, 'utf8');
  const hash = crypto.createHash('sha256').update(source).digest('hex');
  if (hash !== expectedHash) throw new Error(`Unsupported source revision: ${path}`);
  for (const [before, after] of replacements) {
    if (source.split(before).length !== 2) throw new Error(`Patch anchor not unique: ${path}`);
    source = source.replace(before, after);
  }
  fs.writeFileSync(path, source);
  execFileSync(process.execPath, ['--check', path], { stdio: 'inherit' });
}

if (process.argv[2] === 'browser') {
  patch('/usr/src/app/dist/api.js', '7147d9c0188fa23f161d6cd79d6bc5bc07244247b35d504361b9d7e2f1df7ac5', [
    ['    let headers = null, content = yield page.content();',
     '    const capturedUrl = page.url();\n    let headers = null, content = yield page.content();'],
    ['    return {\n        content,\n        status: response ? response.status() : null,',
     '    return {\n        ...require("/opt/personal-agent/provenance.cjs").navigationEvidence(capturedUrl, page.url(), response ? response.url() : null),\n        content,\n        status: response ? response.status() : null,'],
    ['res.json(Object.assign({ content: result.content, pageStatusCode: result.status, contentType: result.contentType },',
     'res.json(Object.assign({ url: result.url, finalUrlVerified: result.finalUrlVerified, content: result.content, pageStatusCode: result.status, contentType: result.contentType },'],
  ]);
} else if (process.argv[2] === 'api') {
  const build = fs.readFileSync('/app/BUILD_SHA', 'utf8').trim();
  if (build !== '7666c1f9ae8720a6bba271e0f60b6a217f8a5210') throw new Error('Unsupported Firecrawl build');
  patch('/app/dist/src/scraper/scrapeURL/engines/playwright/index.js', 'f1b04431933831a6aa164a9d58e9e32426952ac103bb291b7f513444c99d6df7', [
    ['            content: zod_1.z.string(),', '            url: zod_1.z.string().url(),\n            finalUrlVerified: zod_1.z.boolean(),\n            content: zod_1.z.string(),'],
    ['        url: meta.rewrittenUrl ?? meta.url, // TODO: impove redirect following',
     '        url: response.url,\n        finalUrlVerified: response.finalUrlVerified,'],
  ]);
  patch('/app/dist/src/scraper/scrapeURL/engines/fetch/index.js', 'be55e3e72815347c8095a376d81f1ffa1e9ea826229de14230b86549f2a8fdd0', [
    ['        url: response.url,\n        html: response.body,',
     '        url: response.url,\n        finalUrlVerified: meta.fetchPrefetch == null && meta.mock === null,\n        html: response.body,'],
  ]);
  patch('/app/dist/src/scraper/scrapeURL/index.js', '7bac15dcc9db3b49048e8ce0f097fb8955a5610a8acfe3ecd8994ea241a056e0', [
    ['                url: engineResult.url,\n                statusCode: engineResult.statusCode,',
     '                url: engineResult.url,\n                finalUrlVerified: engineResult.finalUrlVerified === true,\n                statusCode: engineResult.statusCode,'],
  ]);
} else {
  throw new Error('Choose browser or api');
}

'use strict';

function publicHttpsShape(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch {
    return false;
  }
}

// Browser-controlled URLs, never values extracted from the scraped HTML.
// A navigation during capture or after the response cannot authenticate that content.
function navigationEvidence(beforeContent, afterContent, responseUrl) {
  return {
    url: beforeContent,
    finalUrlVerified: publicHttpsShape(beforeContent)
      && beforeContent === afterContent && beforeContent === responseUrl,
  };
}

module.exports = { navigationEvidence };

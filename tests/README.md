# Browser regression checks

Run `npm ci`, then `npm test` with Google Chrome installed. To use another
Playwright browser channel, set `PLAYWRIGHT_CHANNEL` (for example, `chromium`
after `npx playwright install chromium`). Node.js 20+ and internet access to the
application's existing export-library CDNs are required.

The checks cover pagination with four photos, photos inserted inside descriptions, descriptions and lists, long
specification tables, nested crops, multiple products, an oversized photo
layout, editing, local persistence, and actual PDF/DOCX downloads. They assert
that content stays above the footer and that descriptions, rows and photos are
preserved. All fixtures use a fresh browser context with generated images.

Formatting checks click the actual toolbar buttons and verify computed styles
in the preview. Long formatted paragraphs, continued list numbering, and long
words are checked across page boundaries. PDF rendering is checked for bold
description text; DOCX XML is checked for bold, italic, underline, strikeout and
paragraph alignment.

Set `TEST_ARTIFACTS_DIR` to save a screenshot and exported files. Optionally set
`TEST_BASELINE_REF` to a Git revision to run the same checks against older HTML.
Set `TEST_URL` to run the same checks against a published copy.

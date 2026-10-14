# v0.1.2 — preserve evidence containing Markdown fences

Wiki evidence containing `---` could make valid YAML metadata disappear from the page catalog, index and lint. The shared parser now recognizes closing frontmatter delimiters only on their own unindented line. It preserves inline and indented fences, handles UTF-8 BOM and CRLF/CR/LF, and returns only mapping metadata. Capture deduplication uses the same parser.

Lint now explicitly reports invalid topic metadata instead of silently dropping that topic. The patch changes software parsing only; existing source notes and wiki evidence do not require rewrites. The next successful compiler batch rebuilds the index with previously omitted topics. It does not clear source queues or transfer writer ownership.

Regression coverage includes exact quoted evidence with inline fences, linked topic publication and index inclusion, capture replay, malformed/unclosed/non-mapping frontmatter, and BOM/line endings. Cross-platform CI runs the complete suite and native dependency imports/audits. Use the normal tagged-release updater; installed credentials, paths, device role and index stay local.

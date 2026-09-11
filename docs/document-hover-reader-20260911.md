# Item document hover and reader (2026-09-11)

Inspector is hidden. Graph hover lists only documents explicitly connected to the item; document nodes list themselves and project nodes list project-wide documents. Links open a modal reader using the existing Markdown renderer or a sandboxed HTML frame. HTML scripts and external assets are disabled.

Ownership evidence: app.js updateGraphHoverTooltip previously used pointer-events:none; renderDetails owned the mixed Inspector. No document-content GET existed. The new reader accepts registered document IDs, checks the resolved project-root boundary, restricts text formats and caps reads at 2 MiB.

outcome: SUCCEEDED
changed_paths: tools/universe_ui/app.js, tools/universe_ui/styles.css, tools/universe_app/document_reader.py, tools/universe_server.py, tests/test_document_reader.py
validation:
- source: PASS (node syntax, Python compilation, git diff --check)
- API: PASS (live registered architecture-catalog HTTP 200; unregistered document HTTP 400 DOCUMENT_NOT_FOUND)
- boundary: PASS (2 unittest cases: registered content, traversal, missing file, HTML format, size cap)
- UI filtering: PASS (node VM: node, document, project-wide, cross-project)
- browser: PASS (Docs architecture icon hover yielded one matching link; click opened sandboxed rendered HTML; Inspector absent; Markdown README body also observed)
- lifecycle: PASS (standard server restart and live API response)
residual_risks:
- Markdown formatting uses the existing basic renderer; rich tables and inline formatting remain basic.
- Hover-only mouse exit timing was not independently automated; link transition and content opening were exercised.

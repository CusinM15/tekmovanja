You are a coding agent using the local Twill-8B model. Build a simple, reliable Windows desktop app for managing a primary-school athletics competition.

Repository: git@github.com:CusinM15/tekmovanja.git
Source documents: ~/forgejo/opensource/tekmovanja/

## Safety and working boundaries
- Work only in the checked-out repository and the specified source-files folder.
- Never display, copy, log, or transmit SSH tokens, passwords, or other secrets. Do not inspect unrelated files or credential stores.
- Do not push, publish, or otherwise change the remote repository. Do not make network requests except when explicitly needed to test email functionality, and ask before doing so.
- Before editing, inspect the repository and both source documents. Preserve existing work; do not overwrite or delete files without checking their contents.
- Do not invent competition rules or pupil fields. Record uncertainties and ask concise questions after inspecting the documents.
- Make changes in small, reviewable steps. Run appropriate tests and report what you changed and what you could not verify.

## First: inspect and report
1. Explain how the workbook is organized and identify the pupil fields, result categories, competition rules, record-calculation rules, and results presentation requirements that the documents actually support.
2. Use the Word document as the guide for the results PDF’s content and layout.
3. Before implementation, summarize your proposed simplest suitable design and list any genuinely unresolved questions. Do not pause for questions that the documents answer.

## App requirements
Build a local FastAPI Python app for one user on a Windows PC. Use SQLite. Do not add registration, sign-up, or a login system. Normal app use must work offline; internet is needed only for email.

### Schools and disciplines
Start with these schools:
- OŠ Žirovnica
- OŠ Prežihov Voranc
- OŠ Koroška Bela
- OŠ Josipa Vandota Kranjska Gora
- OŠ 16. decembra Mojstrana

Each school has an email address. Let the user add schools and remove them from the active list. Removing a school must not delete its historical results or records; inactive schools must be excluded from future emails.

Start with these disciplines:
- tek 60m M
- tek 60m Ž
- tek 400m M
- tek 400m ž
- skok v daljino M
- skok v daljino Ž
- met vorteksa M
- met vorteksa Ž

Let the user add and remove disciplines. Removing one must not delete its historical results.

Grades are 2.r and 3.r. Each grade has the applicable disciplines. Confirm applicability and any naming or capitalization details against the source documents where possible.

### Email and CSV
- Provide settings for the sender’s Arnes email account and the required SMTP configuration. Send mail through SMTP on port 587.
- Store credentials locally using an appropriate Windows-protected approach where practical. Never store credentials in source code or commit them. Explain exactly where and how settings and credentials are stored, and provide a way to change or clear them.
- Provide a “Send invitations” action that emails every active school, attaching a blank CSV template and explaining how to fill it in and return it.
- Provide an “Import CSV files” workflow for completed files. Validate inputs, report clear file/row/field errors, and prevent accidental duplicate imports. Do not invent CSV fields: derive them from the inspected documents and ask if they do not specify them.
- After successful import, create separate start lists for each discipline and grade. Provide print-friendly pages and PDF downloads for each list.

### Results, records, and data deletion
- After the competition, generate a downloadable results PDF following the Word document’s guidance.
- Provide a one-click option to email the results PDF to active schools.
- Maintain historical best results according to the rules supported by the source documents. Update a record when beaten; when tied, retain the tied result as an additional record.
- Provide a page for viewing best results.
- Add a “Delete data” action that deletes competition data (including imported pupil entries and ordinary competition results) while preserving schools, disciplines, and historical best results. Explain what will be deleted, require explicit confirmation, and ensure the action cannot run accidentally.

## Implementation and delivery
- Choose the simplest maintainable design that meets the requirements; avoid unnecessary services and dependencies.
- Include database migrations or a safe schema-initialization strategy, input validation, useful error handling, and tests for imports, duplicate prevention, record updates/ties, school/discipline deactivation, and data deletion.
- Make installation as simple as practical for Windows. Aim for an installer or a straightforward packaged executable if feasible; do not claim an installer is ready unless it has been built and tested.
- Include clear Windows installation, startup, email-configuration, CSV, competition-day, backup, and troubleshooting instructions.
- Keep the interface straightforward and usable by a nontechnical user.
- At completion, report the design, files changed, tests run and their results, build/install status, remaining questions, and any known limitations.


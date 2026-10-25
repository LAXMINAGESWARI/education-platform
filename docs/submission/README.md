# CurioEd submission package

One group upload. The LMS notice sets the deadline at **25 October 2026, 11:55 PM**. The notice does not name a time zone. The group's recorded meetings use India Standard Time.

The Instructions & Best Practices guide (Section 1 formats, Section 9 checklist) is not in this repository. `09-Pre-Upload-Checklist` covers the items the notice itself named and says where that guide was missing.

## Upload these

| LMS item | File |
| --- | --- |
| Final Project Report (PDF) | `01-Final-Project-Report.pdf` |
| Editable source (.docx) | `01-Final-Project-Report.docx` |
| Assignment Submission Form | `02-Assignment-Submission-Form.pdf` (and the `.docx` if the form asks for a source file) |
| Code & Analysis Repository | Link: https://github.com/aushakanuj/education-platform — map in `06-Code-and-Analysis-Repository.pdf` |
| Data Note (maximum 1 page) | `03-Data-Note.pdf` |
| Signed Originality & Confidentiality Declaration | `04-Originality-and-Confidentiality-Declaration.pdf` after the group signs it |
| Plain-Language Summary | `05-Plain-Language-Summary.pdf` |

`09-Pre-Upload-Checklist.pdf` is the working checklist. Use it, then upload it with the package if the course accepts an extra sheet.

Signature lines are blank on purpose. Do not upload the declaration unsigned if the notice requires a signed copy.

Write the course or group number on the submission form. It is not recorded in the repository. The 19 August 2026 minutes name tanmay with no surname; add a signature row if tanmay is a submitting member.

## Do not upload

- `.html` files (print source for the PDFs)
- `build_submission_package.py` (regenerates the package)
- API keys, `backend/.env`, or a database dump

## Regenerate

From the repository root, with `python-docx` installed and Google Chrome available:

```bash
python3 docs/submission/build_submission_package.py
```

The PDF and the DOCX of each document are rendered from the same blocks in that script.

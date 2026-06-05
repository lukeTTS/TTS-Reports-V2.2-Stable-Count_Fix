# TTS PRO Report Generator - PADFX Edition

This version uses the raw `.padfx` file exported by the tester software. CSV conversion is no longer required.

## Main workflow

1. Open the app.
2. Go to **Import**.
3. Upload the project file (`.pdf`, `.padfx`, `.apx` or `.xlsx`).
4. The **Home** screen displays a clean Asset Register Dashboard.
5. Search, filter by location/status, and click an asset row to view its test details.
6. Choose Basic or PRO Report.
7. Customer reports default to **Latest test only**.
8. Use **Advanced / Audit Report** only when a date-range or all-dates history report is required.
9. Generate the PDF.

## Asset Register Dashboard

The Home screen now shows customer-friendly asset data instead of raw preview rows:

- Total assets, passed, failed, locations, due in 90 days, and overdue counts.
- Search by asset ID, type, location, user, or test summary.
- Filter by status and location.
- Clean table columns: Asset ID, Type, Location, Test Date, Retest Date, Status, Tests, Summary.
- Click any asset to open a detail panel with the compact single-line test results.


## Customer filtering mode

Normal customer reports are locked to **Latest test only** by default and marked **Recommended** in the Report Controls panel. This prevents historical repeat tests from appearing in standard customer reports and keeps the output focused on current compliance status.

The historical options are hidden under **Advanced / Audit Report**:

- **By date range**: for job-specific or period-specific reports.
- **All dates / full history**: for audit reports only.

## Reports Dashboard

The Reports section now contains three clear report actions:

- **Basic Report**: simple title page and results register.
- **PRO Report**: editable General Data cover page, results summary and optional per-appliance detailed test pages.
- **Upcoming Retest Report**: grouped due-date report showing Overdue, Due in 30 days, Due in 60 days and Due in 90 days.

The Upcoming Retest dashboard also displays live counts and a retest table after a project file is imported.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Command line

```bash
python cli.py samples/TTS_210526.padfx output/sample_padfx_report.pdf --report-type pro --filter-mode latest

# Upcoming Retest Report
python cli.py samples/TTS_210526.padfx output/upcoming_retest_report.pdf --report-type retest --filter-mode latest
```

## Build Windows executable

```bat
pip install -r requirements.txt pyinstaller
build_windows.bat
```

## TTS colours

- TTS Vibrant Red: `#CC0000`
- TTS Seafoam: `#99CCCC`
- TTS Charcoal: `#312F2F`
- TTS Beige: `#E5E6DC`
- TTS White: `#F7F7F7`

## Expanded Settings section

The Settings screen now contains the requested full settings structure:

1. Business Profile
2. Branding
3. Report Defaults
4. Template Defaults
5. Retest Settings
6. Import Settings
7. PDF Output Settings
8. Signature and Sign-off
9. Data and Privacy
10. Advanced / Developer Settings

Settings are saved locally in the browser using localStorage for this customer-facing beta. The business profile can prefill contractor/operator report fields before PDF generation. The TTS colour palette remains the default branding, with controls prepared for customer branding in future builds.

## Multi-format project import

This build lets customers import the same project data from any of these supported files:

- aPAT PDF export: `.pdf`
- aPAT project export: `.padfx`
- aPAT ES Manager export: `.apx`
- ES Manager spreadsheet export: `.xlsx`
- ES Manager saved project: `.padfx`

The app auto-detects the file type and converts it into the same internal asset register used by the Home dashboard and report generator.

### Import behaviour

- `.pdf`: reads selectable embedded text from the aPAT PDF export. OCR is not used.
- `.padfx` / `.padf`: reads `DataSource.padf` from the project archive.
- `.apx`: reads `StrucutureExport.json` and `InstrumentsExport.json` from the APX archive.
- `.xlsx`: reads the ES Manager result sheets directly from the workbook XML.

Normal customer reports still use **Latest test only** by default. Advanced audit options can still include a date range or all historical test rows.

### Notes on source differences

All supported files can represent the same project, but each export format stores the data differently. The app normalises the key report fields into:

`Appliance ID`, `Type`, `Location`, `Test Date`, `Retest Date`, `Status`, `User`, `Test Summary`, and detailed test lines where available.

The `.xlsx` export may not include every retest-date field available in `.padfx`/`.apx`, depending on how ES Manager exported the workbook.

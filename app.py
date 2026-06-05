from pathlib import Path
from uuid import uuid4
import json

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, url_for

from report_core import GeneralData, generate_report, preview_padfx, _is_supported_project_file

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "uploads"
OUTPUT_DIR = BASE / "output"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "tts-pro-report-generator"


def form_to_general_data(form) -> dict:
    multi = {
        "type_of_equipment": form.getlist("type_of_equipment"),
        "reason_for_test": form.getlist("reason_for_test"),
        "attachments": form.getlist("attachments"),
        "method_of_labelling": form.getlist("method_of_labelling"),
    }
    fields = {}
    for f in GeneralData.__dataclass_fields__:
        if f in multi:
            fields[f] = multi[f]
        else:
            fields[f] = form.get(f, "")
    return fields


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/preview")
def preview():
    f = request.files.get("project_file") or request.files.get("padfx_file") or request.files.get("data_file")
    if not f or not _is_supported_project_file(f.filename):
        return jsonify({"error": "Please upload a supported project file: .pdf, .padfx, .apx or .xlsx."}), 400
    job_id = uuid4().hex
    path = UPLOAD_DIR / f"{job_id}_{Path(f.filename).name}"
    f.save(path)
    try:
        result = preview_padfx(path, filter_mode=request.form.get("filter_mode", "latest"), start_date=request.form.get("filter_start_date") or None, end_date=request.form.get("filter_end_date") or None)
        result["filename"] = f.filename
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/generate")
def generate():
    f = request.files.get("project_file") or request.files.get("padfx_file") or request.files.get("data_file")
    if not f or not _is_supported_project_file(f.filename):
        flash("Please upload a supported project file: .pdf, .padfx, .apx or .xlsx.")
        return redirect(url_for("index"))
    general = form_to_general_data(request.form)
    report_type = request.form.get("report_type", "pro")
    include_cards = request.form.get("include_asset_cards") == "on" and report_type == "pro"
    filter_mode = request.form.get("filter_mode", "latest")
    start_date = request.form.get("filter_start_date") or None
    end_date = request.form.get("filter_end_date") or None
    job_id = uuid4().hex
    project_path = UPLOAD_DIR / f"{job_id}_{Path(f.filename).name}"
    pdf_path = OUTPUT_DIR / f"tts_pro_report_{report_type}_{job_id}.pdf"
    f.save(project_path)
    try:
        generate_report(project_path, pdf_path, general, include_asset_cards=include_cards, report_type=report_type, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    except Exception as e:
        flash(f"Could not generate report: {e}")
        return redirect(url_for("index"))
    if report_type in {"retest", "upcoming_retest", "upcoming"}:
        download_name = "tts_upcoming_retest_report.pdf"
    else:
        download_name = f"tts_pro_report_{report_type}.pdf"
    return send_file(pdf_path, as_attachment=True, download_name=download_name)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

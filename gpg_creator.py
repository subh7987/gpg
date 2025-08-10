# streamlit_gpg_app.py
import streamlit as st
import zipfile
import subprocess
import re
import csv
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
from io import BytesIO

# ---------------- CONFIG ----------------
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".eml", ".json"}

locale_country_map = {
    "zh_TW": "Taiwan",
    "zh_HK": "Hong Kong",
    "vi_VN": "Vietnam",
    "nl_NL": "Netherlands",
    "nl_BE": "Belgium",
    "da_DK": "Denmark",
    "sv_SE": "Sweden",
    "nb_NO": "Norway",
    "tr_TR": "Turkey"
}

# Vendor_Locale_Category_Subcategory_AssetNumber_Date.extension
FILENAME_PATTERN = re.compile(
    r"^[A-Za-z]+_([a-z]{2}_[A-Z]{2})_([a-z_]+)_[0-9]+_(\d{8})\.(txt|pdf|eml|json)$"
)
# ----------------------------------------

st.set_page_config(page_title="Zip → GPG Automation", layout="wide")

st.title("Zip → GPG Automation (Streamlit)")
st.caption("Validate filenames → Zip per locale → Encrypt zip → Download results")

col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("### Mode")
    mode = st.radio("Choose input mode", ["Local folder on server (for local run)", "Upload parent-folder .zip (for cloud/browser)"])

    st.markdown("### Inputs")
    if mode.startswith("Local"):
        base_dir = st.text_input("Parent folder path (e.g. /home/user/data or C:\\data)", value="")
    else:
        uploaded = st.file_uploader("Upload parent-folder as a ZIP (structure: YYYYMMDD/locale/...)", type=["zip"], accept_multiple_files=False)

    password = st.text_input("GPG Encryption Password (symmetric)", type="password")
    remove_zip_after = st.checkbox("Remove plain .zip after creating .gpg (recommended)", value=True)
    process_valid_only = st.checkbox("Process valid files even if some invalid files exist (skip invalid)", value=True)

    run_btn = st.button("Start Process")

with col2:
    st.markdown("### Config")
    st.write("Allowed extensions:", ", ".join(sorted(ALLOWED_EXTENSIONS)))
    st.write("Filename pattern example:")
    st.code("Firstsource_zh_TW_email_restaurant_1000001_20250714.txt")
    st.write("Locale map (supported):")
    st.json(locale_country_map)

log_area = st.empty()
progress = st.progress(0)

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    log_area.text_area("Log", value=f"[{ts}] {msg}", height=250)

def validate_file_paths(date_folder: Path, invalid_files: list, strict_locale=True):
    """Validate file names, extensions, and locales."""
    all_ok = True
    for file in date_folder.rglob("*"):
        if file.is_file():
            if file.suffix.lower() not in ALLOWED_EXTENSIONS:
                invalid_files.append([str(file), "Invalid extension"])
                all_ok = False
                continue
            match = FILENAME_PATTERN.match(file.name)
            if not match:
                invalid_files.append([str(file), "Invalid filename format"])
                all_ok = False
                continue
            locale = match.group(1)
            if strict_locale and locale not in locale_country_map:
                invalid_files.append([str(file), f"Invalid locale: {locale}"])
                all_ok = False
    return all_ok

def zip_language_folders(date_folder: Path, log_fn):
    zipped = []
    lang_folders = sorted([p for p in date_folder.iterdir() if p.is_dir() and "_" in p.name])
    if not lang_folders:
        log_fn(f"⚠️ No language folders found under {date_folder}")
    for lang_folder in lang_folders:
        zip_path = date_folder / f"{lang_folder.name}.zip"
        log_fn(f"📦 Creating zip: {zip_path}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file in lang_folder.rglob("*"):
                if file.is_file():
                    arcname = file.relative_to(date_folder)  # keeps lang_folder as top-level in zip
                    zipf.write(file, arcname)
        zipped.append(zip_path)
    return zipped

def encrypt_zip_files(zip_paths, password, log_fn, remove_zip=True):
    encrypted = []
    # check gpg binary
    try:
        subprocess.run(["gpg", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        raise RuntimeError("gpg binary not found. Please ensure 'gpg' is installed and available in PATH.") from e

    for zip_file in zip_paths:
        gpg_file = zip_file.with_suffix(zip_file.suffix + ".gpg")
        log_fn(f"🔐 Encrypting {zip_file.name} -> {gpg_file.name}")
        cmd = [
            "gpg", "--batch", "--yes", "-c",
            "--passphrase", password,
            str(zip_file)
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            encrypted.append(gpg_file)
            if remove_zip:
                try:
                    zip_file.unlink()
                    log_fn(f"🗑️ Removed plain zip: {zip_file.name}")
                except Exception as e:
                    log_fn(f"⚠️ Could not remove zip {zip_file.name}: {e}")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors='ignore') if e.stderr else str(e)
            log_fn(f"❌ GPG failed for {zip_file.name}: {stderr}")
    return encrypted

def save_invalid_files(invalid_files, outpath: Path):
    if not invalid_files:
        return None
    out = outpath
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_path", "issue"])
        writer.writerows(invalid_files)
    return out

def process_parent_dir(parent_dir: Path, password, remove_zip_after, process_valid_only, log_fn, progress_cb):
    invalid_files = []
    date_folders = sorted([p for p in parent_dir.iterdir() if p.is_dir() and p.name.isdigit()])
    total = max(1, len(date_folders))
    processed = 0
    encrypted_all = []
    log_fn(f"Found {len(date_folders)} date-folders under {parent_dir}")
    for idx, date_folder in enumerate(date_folders, start=1):
        log_fn(f"📅 Processing {date_folder.name} ({idx}/{total})")
        ok = validate_file_paths(date_folder, invalid_files, strict_locale=True)
        if not ok and not process_valid_only:
            log_fn(f"❌ Validation failed for {date_folder.name}. Skipping folder.")
            progress_cb(int((idx/total)*100))
            continue
        # Zip language folders
        zip_paths = zip_language_folders(date_folder, log_fn)
        if zip_paths:
            enc = encrypt_zip_files(zip_paths, password, log_fn, remove_zip=remove_zip_after)
            encrypted_all.extend(enc)
        processed += 1
        progress_cb(int((idx/total)*100))
    # save invalid csv
    invalid_csv = save_invalid_files(invalid_files, parent_dir / "invalid_files_report.csv")
    return encrypted_all, invalid_csv

# ---------- Main action ----------
if run_btn:
    if not password:
        st.error("Enter GPG encryption password.")
    else:
        # Prepare workspace depending on mode
        if mode.startswith("Local"):
            if not base_dir:
                st.error("Enter a parent folder path.")
            else:
                parent = Path(base_dir)
                if not parent.exists() or not parent.is_dir():
                    st.error("Provided path not found or not a directory on this server.")
                else:
                    try:
                        log_area.text_area("Log", value="", height=250)
                        progress.progress(0)
                        st.info("Processing. Check log below.")
                        encrypted, invalid_csv = process_parent_dir(parent, password, remove_zip_after, process_valid_only, log, progress.progress)
                        progress.progress(100)
                        st.success("Processing complete.")
                        if invalid_csv:
                            st.download_button("Download invalid_files_report.csv", data=open(invalid_csv, "rb").read(), file_name="invalid_files_report.csv")
                        st.write("Encrypted files produced (server-side path):")
                        for p in encrypted:
                            st.write(str(p))
                        if encrypted:
                            st.warning("On cloud deployments, server file paths are not downloadable. Use upload mode to get .gpg files via browser.")
                    except Exception as e:
                        st.error(f"Error: {e}")

        else:
            # Upload mode: client uploads a zip containing the parent folder structure.
            if uploaded is None:
                st.error("Upload a parent-folder zip file first.")
            else:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmpdir_p = Path(tmpdir)
                    upload_path = tmpdir_p / "uploaded.zip"
                    with open(upload_path, "wb") as f:
                        f.write(uploaded.getbuffer())

                    # extract
                    try:
                        with zipfile.ZipFile(upload_path, "r") as z:
                            z.extractall(tmpdir_p)
                    except zipfile.BadZipFile:
                        st.error("Uploaded file is not a valid zip.")
                        raise st.stop()

                    # find parent folder inside tmpdir (assume top-level folders are date folders)
                    # treat tmpdir_p as the parent
                    try:
                        log_area.text_area("Log", value="", height=250)
                        progress.progress(0)
                        st.info("Processing uploaded content...")
                        encrypted, invalid_csv = process_parent_dir(tmpdir_p, password, remove_zip_after, process_valid_only, log, progress.progress)
                        # create a single zip containing all .gpg files for download
                        gpg_files = list(tmpdir_p.rglob("*.gpg"))
                        if gpg_files:
                            out_bytes = BytesIO()
                            with zipfile.ZipFile(out_bytes, "w", zipfile.ZIP_DEFLATED) as outzip:
                                for f in gpg_files:
                                    outzip.write(f, f.relative_to(tmpdir_p))
                            out_bytes.seek(0)
                            st.download_button("Download all .gpg files (zip)", data=out_bytes.read(),
                                               file_name="encrypted_gpg_files.zip")
                        else:
                            st.warning("No .gpg files generated.")
                        if invalid_csv:
                            with open(invalid_csv, "rb") as f:
                                st.download_button("Download invalid_files_report.csv", data=f.read(), file_name="invalid_files_report.csv")
                        progress.progress(100)
                        st.success("Uploaded processing complete.")
                    except Exception as e:
                        st.error(f"Error processing uploaded zip: {e}")

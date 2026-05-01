import urllib.request
import json
import os
import sys
import subprocess
import time

# ==========================================================
# CONFIGURATION - UPDATE THESE WITH YOUR GITHUB DETAILS
# ==========================================================
GITHUB_USER = "Aaditi2505"  # Your GitHub username
GITHUB_REPO = "TrimBase"      # Your repository name
GITHUB_BRANCH = "main"

VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/version.json"
BASE_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/"

# List all files that need to be updated if a new version is found
FILES_TO_UPDATE = [
    "main_v2_original.py",
    "patcher.py",
    "patch_states.py",
    "patch_states_2.py",
    "logo.png"
]

LOCAL_VERSION_FILE = "version.json"
MAIN_SCRIPT = "main_v2_original.py"
# ==========================================================

def get_local_version():
    try:
        if os.path.exists(LOCAL_VERSION_FILE):
            with open(LOCAL_VERSION_FILE, 'r') as f:
                return json.load(f).get("version", "0.0.0")
    except Exception:
        pass
    return "0.0.0"

def get_remote_version():
    print(f"Connecting to {VERSION_URL}...")
    try:
        # Adding a small timeout to avoid hanging
        with urllib.request.urlopen(VERSION_URL, timeout=10) as response:
            data = json.loads(response.read().decode())
            return data.get("version", "0.0.0")
    except Exception as e:
        print(f"Note: Could not check for updates ({e}).")
        return None

def download_file(filename):
    url = BASE_RAW_URL + filename
    print(f"Downloading {filename}...")
    try:
        urllib.request.urlretrieve(url, filename)
        return True
    except Exception as e:
        print(f"Error: Failed to download {filename} -> {e}")
        return False

def run_update():
    print("--- Software Update System ---")
    local_v = get_local_version()
    remote_v = get_remote_version()

    if remote_v is None:
        print("Working offline or server unreachable. Skipping update.")
        return

    # Using version comparison (simple string comparison works for 1.0.1 style)
    if remote_v > local_v:
        print(f"UPDATE FOUND! Version {remote_v} is available.")
        print(f"Current version: {local_v}")
        
        # Download all files
        all_success = True
        for file in FILES_TO_UPDATE:
            if not download_file(file):
                all_success = False
                break
        
        if all_success:
            # Finally update the local version file
            download_file(LOCAL_VERSION_FILE)
            print("\nSUCCESS: All files updated to version " + remote_v)
            time.sleep(1)
        else:
            print("\nWARNING: Some files failed to download. Update may be incomplete.")
    else:
        print(f"Software is up to date (Version {local_v}).")

def start_app():
    print(f"Launching {MAIN_SCRIPT}...")
    try:
        # Using subprocess.run or Popen to start the main app
        # We use sys.executable to ensure it uses the same python environment
        subprocess.Popen([sys.executable, MAIN_SCRIPT])
    except Exception as e:
        print(f"Fatal Error: Could not start application: {e}")

if __name__ == "__main__":
    # 1. Check and perform update
    run_update()
    
    # 2. Launch the main application
    start_app()
    
    # 3. Exit the updater
    sys.exit()

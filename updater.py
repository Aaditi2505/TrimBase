import urllib.request
import json
import os
import sys
import subprocess
import time
import runpy

# IMPORTANT: These imports are here so PyInstaller bundles them into the EXE
# even if they aren't used directly in this script.
if False:
    import numpy
    import open3d
    import win32gui
    import win32con
    import win32api
    from PyQt6 import QtCore, QtGui, QtWidgets

from PyQt6.QtWidgets import QApplication, QSplashScreen, QLabel, QVBoxLayout, QWidget, QProgressBar
from PyQt6.QtGui import QPixmap, QColor, QFont
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# ==========================================================
# CONFIGURATION
# ==========================================================
GITHUB_USER = "Aaditi2505"
GITHUB_REPO = "TrimBase"
GITHUB_BRANCH = "main"

# When running as a PyInstaller EXE, the scripts are in the root folder
if getattr(sys, 'frozen', False):
    BASE_PATH = os.path.dirname(sys.executable)
else:
    BASE_PATH = os.path.dirname(os.path.abspath(__file__))

# Files to sync
FILES_TO_UPDATE = ["main_v2_original.py", "patcher.py", "patch_states.py", "patch_states_2.py", "logo.png"]
LOCAL_VERSION_FILE = os.path.join(BASE_PATH, "version.json")
MAIN_SCRIPT = os.path.join(BASE_PATH, "main_v2_original.py")

VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/version.json"
BASE_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/"
# ==========================================================

class UpdateWorker(QThread):
    finished = pyqtSignal(bool)
    status_msg = pyqtSignal(str)
    progress = pyqtSignal(int)

    def run(self):
        try:
            local_v = self.get_local_version()
            self.status_msg.emit("Checking for updates...")
            remote_v = self.get_remote_version()

            if remote_v and remote_v > local_v:
                self.status_msg.emit(f"New version found: {remote_v}")
                total_files = len(FILES_TO_UPDATE)
                
                for i, file in enumerate(FILES_TO_UPDATE):
                    self.status_msg.emit(f"Downloading {file}...")
                    if not self.download_file(file):
                        self.finished.emit(False)
                        return
                    self.progress.emit(int(((i + 1) / total_files) * 100))
                
                # Finally update the local version file
                self.download_file("version.json")
                self.status_msg.emit("Update complete!")
                time.sleep(1)
            else:
                self.status_msg.emit("Up to date.")
                time.sleep(0.5)
            
            self.finished.emit(True)
        except Exception as e:
            self.status_msg.emit(f"Error: {e}")
            time.sleep(1)
            self.finished.emit(False)

    def get_local_version(self):
        try:
            if os.path.exists(LOCAL_VERSION_FILE):
                with open(LOCAL_VERSION_FILE, 'r') as f:
                    return json.load(f).get("version", "0.0.0")
        except: pass
        return "0.0.0"

    def get_remote_version(self):
        try:
            with urllib.request.urlopen(VERSION_URL, timeout=5) as response:
                data = json.loads(response.read().decode())
                return data.get("version", "0.0.0")
        except: return None

    def download_file(self, filename):
        try:
            local_path = os.path.join(BASE_PATH, filename)
            urllib.request.urlretrieve(BASE_RAW_URL + filename, local_path)
            return True
        except: return False

class Splash(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 300)

        layout = QVBoxLayout(self)
        self.frame = QWidget()
        self.frame.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 15px;
                border: 2px solid #0066ff;
            }
        """)
        frame_layout = QVBoxLayout(self.frame)
        
        self.logo = QLabel()
        pix = QPixmap("logo.png")
        if not pix.isNull():
            self.logo.setPixmap(pix.scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.logo.setText("TrimBase")
            self.logo.setStyleSheet("font-size: 32px; font-weight: bold; color: #0066ff; border: none;")
        
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(self.logo)

        self.label = QLabel("Initializing...")
        self.label.setStyleSheet("font-size: 14px; color: #444; border: none;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(self.label)

        self.bar = QProgressBar()
        self.bar.setFixedHeight(10)
        self.bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ddd;
                border-radius: 5px;
                background-color: #f0f0f0;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #0066ff;
                border-radius: 5px;
            }
        """)
        self.bar.setTextVisible(False)
        frame_layout.addWidget(self.bar)

        layout.addWidget(self.frame)

    def update_status(self, text):
        self.label.setText(text)

    def update_progress(self, val):
        self.bar.setValue(val)

def main():
    app = QApplication(sys.argv)
    
    splash = Splash()
    splash.show()

    worker = UpdateWorker()
    worker.status_msg.connect(splash.update_status)
    worker.progress.connect(splash.update_progress)
    
    # Store success state
    update_success = [False]
    def on_finished(success):
        update_success[0] = success
        app.quit()

    worker.finished.connect(on_finished)
    worker.start()
    
    app.exec()
    
    # After splash app quits, we run the main script
    if os.path.exists(MAIN_SCRIPT):
        try:
            # Clear sys.argv to avoid passing launcher args to main script
            sys.argv = [MAIN_SCRIPT]
            # Run the script in the same process
            runpy.run_path(MAIN_SCRIPT, run_name="__main__")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            error_msg = QMessageBox()
            error_msg.setIcon(QMessageBox.Icon.Critical)
            error_msg.setText("Error launching application")
            error_msg.setInformativeText(str(e))
            error_msg.setWindowTitle("Launch Error")
            error_msg.exec()
    else:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Error", f"Main script not found at {os.path.abspath(MAIN_SCRIPT)}")

if __name__ == "__main__":
    main()

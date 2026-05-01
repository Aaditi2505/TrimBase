import re
import os

file_path = r"c:\Users\Asus\Desktop\fixture\main_v2_original.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. on_fixture_change
content = re.sub(r'(self\.fixture_lbl\.setStyleSheet\("color: #ffffff; font-weight: bold;"\))', r'\1\n            self.update_workflow_state(1)', content)

# 2. load_single_stl
content = re.sub(r'(self\.status_label\.setText\(f"SESSION RESET: SINGLE FILE"\))', r'\1\n            self.update_workflow_state(2)', content)

# 3. add_to_batch
content = re.sub(r'(self\.status_label\.setText\(f"BATCH UPDATED: \{len\(self\.stl_files\)\} FILES"\)\s*\n\s*self\.continue_btn\.setEnabled\(True\))', r'\1\n            self.update_workflow_state(2)', content)

# 4. load_aligner_stl
content = re.sub(r'(def load_aligner_stl.*?try:\s*self\.load_aligner_btn\.setStyleSheet\(""\).*?except AttributeError:\s*pass)', r'\1\n            self.update_workflow_state(3)', content, flags=re.DOTALL)

# 5. add_aligners_to_batch
content = re.sub(r'(def add_aligners_to_batch.*?self\.status_label\.setText\(f"BATCH: \{len\(self\.stl_files\)\} MODELS, \{len\(self\.aligner_files\)\} ALIGNERS"\)\s*\n\s*self\.update_batch_list_labels\(\))', r'\1\n            self.update_workflow_state(3)', content, flags=re.DOTALL)

# 6. align_model_center_to_global_origin
content = re.sub(r'(self\.status_label\.setText\("CENTERED & SEATED ON GRID"\))', r'\1\n        self.update_workflow_state(4)', content)

# 7. toggle_select_mode
content = re.sub(r'(self\.status_label\.setText\("FACE SELECTION ACTIVE: Double-click a tooth face"\))', r'\1\n            self.update_workflow_state(5)', content)

# 9. toggle_precise_controls
content = re.sub(r'(def toggle_precise_controls.*?self\.slider_overlay\.show\(\))', r'\1\n            self.update_workflow_state(7)', content, flags=re.DOTALL)

# 10. merge_fixture_model
content = re.sub(r'(self\.status_label\.setText\("MODEL \+ FIXTURE MERGED"\))', r'\1\n        self.update_workflow_state(8)', content)

# 12. save_model_result
content = re.sub(r'(def save_model_result.*?self\.status_label\.setText\(f"SAVED: \{os\.path\.basename\(save_path\)\}"\))', r'\1\n            self.update_workflow_state(10)', content, flags=re.DOTALL)

# 13. save_aligner_result
content = re.sub(r'(def save_aligner_result.*?self\.status_label\.setText\(f"SAVED: \{os\.path\.basename\(save_path\)\}"\))', r'\1\n            self.update_workflow_state(10)', content, flags=re.DOTALL)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patch 2 completed")

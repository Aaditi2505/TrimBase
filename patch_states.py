import re
import os

file_path = r"c:\Users\Asus\Desktop\fixture\main_v2_original.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

def inject_after(target_str, inject_str):
    global content
    if target_str in content:
        content = content.replace(target_str, target_str + "\n" + inject_str)
        print(f"Injected after: {target_str.strip()[:40]}...")
    else:
        print(f"FAILED to find: {target_str.strip()[:40]}...")

# 1. on_fixture_change
inject_after('self.fixture_lbl.setStyleSheet("color: #ffffff; font-weight: bold;")', '            self.update_workflow_state(1)')

# 2. load_single_stl
inject_after('self.show_aligner_upload(True)', '            self.update_workflow_state(2)')

# 3. add_to_batch
inject_after('self.continue_btn.setEnabled(True)', '            self.update_workflow_state(2)')

# 4. load_aligner_stl
inject_after('self._load_aligner_with_replay(file_path)', '            self.update_workflow_state(3)')

# 5. add_aligners_to_batch
inject_after('self.update_batch_list_labels()', '            self.update_workflow_state(3)')

# 6. align_model_center_to_global_origin
inject_after('self.status_label.setText("CENTERED & SEATED ON GRID")', '        self.update_workflow_state(4)')

# 7. toggle_select_mode
inject_after('self.status_label.setText("FACE SELECTION ACTIVE: Double-click a tooth face")', '            self.update_workflow_state(5)')

# 8. flatten_to_grid
# target near end: 'self.internal_update = False' in flatten_to_grid.
inject_after('        self.record_operation({\n           \'type\': \'flatten\',', '        # flatten step') # not a good target
# better: the end of flatten_to_grid
content = re.sub(r'(def flatten_to_grid.*?        self\.internal_update = False\s*\n\s*self\.tool_precise\.setChecked\(True\))', r'\1\n        self.update_workflow_state(6)', content, flags=re.DOTALL)
print("Injected flatten_to_grid")

# 9. toggle_precise_controls
inject_after('self.slider_overlay.show()', '            self.update_workflow_state(7)')

# 10. merge_fixture_model
inject_after('self.status_label.setText("FIXTURE MERGED WITH MODEL")', '        self.update_workflow_state(8)')

# 11. run_batch_processing
inject_after('self.status_label.setText("BATCH PROCESSING COMPLETE")', '        self.update_workflow_state(9)')

# 12. save_model_result
inject_after('self.status_label.setText(f"SAVED: {os.path.basename(save_path)}")', '            self.update_workflow_state(10)')

# 13. save_aligner_result
# Same as above, the replace will catch both instances.

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch states completed.")

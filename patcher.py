import re

file_path = r"c:\Users\Asus\Desktop\fixture\main_v2_original.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update __init__
init_target = """        self.invert_mouse = True
        self.is_flattened = False
        self.internal_update = False
        self._init_ui_components()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)"""

init_replacement = """        self.invert_mouse = True
        self.is_flattened = False
        self.internal_update = False
        
        # --- WORKFLOW HIGHLIGHTING ---
        pix = QPixmap(12, 12)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setBrush(QColor("#0066ff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()
        self.blue_icon = QIcon(pix)
        self.empty_icon = QIcon()
        self.current_workflow_step = 0
        
        self._init_ui_components()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.update_workflow_state(0)"""

content = content.replace(init_target, init_replacement)

# 2. Add methods
methods = """

    def update_workflow_state(self, step):
        if step > self.current_workflow_step or step == 0:
            self.current_workflow_step = step
            self._refresh_highlights()

    def _refresh_highlights(self):
        highlight_style = "background-color: #e6f0ff; border: 2px solid #0066ff; border-radius: 8px; color: #0066ff;"
        nav_highlight_style = "background-color: #0066ff; color: white; border-radius: 5px; font-weight: bold;"
        default_nav = ""
        default_tool = ""
        
        if hasattr(self, 'btn_upload'): self.btn_upload.setStyleSheet(default_nav)
        if hasattr(self, 'btn_download'): self.btn_download.setStyleSheet(default_nav)
        if hasattr(self, 'tool_origin'): self.tool_origin.setStyleSheet(default_tool)
        if hasattr(self, 'tool_face'): self.tool_face.setStyleSheet(default_tool)
        if hasattr(self, 'tool_flatten'): self.tool_flatten.setStyleSheet(default_tool)
        if hasattr(self, 'tool_precise'): self.tool_precise.setStyleSheet(default_tool)
        if hasattr(self, 'tool_merge'): self.tool_merge.setStyleSheet(default_tool)
        if hasattr(self, 'continue_btn'): self.continue_btn.setStyleSheet(default_tool)
        
        if hasattr(self, 'act_fixture'): self.act_fixture.setIcon(self.empty_icon)
        if hasattr(self, 'act_single_stl'): self.act_single_stl.setIcon(self.empty_icon)
        if hasattr(self, 'act_single_aligner'): self.act_single_aligner.setIcon(self.empty_icon)
        if hasattr(self, 'act_batch_stl'): self.act_batch_stl.setIcon(self.empty_icon)
        if hasattr(self, 'act_batch_aligner'): self.act_batch_aligner.setIcon(self.empty_icon)
        if hasattr(self, 'act_save_stl'): self.act_save_stl.setIcon(self.empty_icon)
        if hasattr(self, 'act_save_aligner'): self.act_save_aligner.setIcon(self.empty_icon)

        step = self.current_workflow_step
        
        if step == 0:
            if hasattr(self, 'btn_upload'): self.btn_upload.setStyleSheet(nav_highlight_style)
            if hasattr(self, 'act_fixture'): self.act_fixture.setIcon(self.blue_icon)
        elif step == 1:
            if hasattr(self, 'btn_upload'): self.btn_upload.setStyleSheet(nav_highlight_style)
            if hasattr(self, 'act_single_stl'): self.act_single_stl.setIcon(self.blue_icon)
            if hasattr(self, 'act_batch_stl'): self.act_batch_stl.setIcon(self.blue_icon)
        elif step == 2:
            if hasattr(self, 'btn_upload'): self.btn_upload.setStyleSheet(nav_highlight_style)
            if self.batch_mode:
                if hasattr(self, 'act_batch_aligner'): self.act_batch_aligner.setIcon(self.blue_icon)
            else:
                if hasattr(self, 'act_single_aligner'): self.act_single_aligner.setIcon(self.blue_icon)
        elif step == 3:
            if hasattr(self, 'tool_origin'): self.tool_origin.setStyleSheet(highlight_style)
        elif step == 4:
            if hasattr(self, 'tool_face'): self.tool_face.setStyleSheet(highlight_style)
        elif step == 5:
            if hasattr(self, 'tool_flatten'): self.tool_flatten.setStyleSheet(highlight_style)
        elif step == 6:
            if hasattr(self, 'tool_precise'): self.tool_precise.setStyleSheet(highlight_style)
        elif step == 7:
            if hasattr(self, 'tool_merge'): self.tool_merge.setStyleSheet(highlight_style)
        elif step == 8:
            if self.batch_mode:
                if hasattr(self, 'continue_btn'): self.continue_btn.setStyleSheet("background-color: #0066ff; color: white;")
            else:
                if hasattr(self, 'btn_download'): self.btn_download.setStyleSheet(nav_highlight_style)
                if hasattr(self, 'act_save_stl'): self.act_save_stl.setIcon(self.blue_icon)
                if hasattr(self, 'act_save_aligner'): self.act_save_aligner.setIcon(self.blue_icon)
        elif step == 9:
            if hasattr(self, 'btn_download'): self.btn_download.setStyleSheet(nav_highlight_style)
            if hasattr(self, 'act_save_stl'): self.act_save_stl.setIcon(self.blue_icon)
            if hasattr(self, 'act_save_aligner'): self.act_save_aligner.setIcon(self.blue_icon)

    def create_colored_icon(self, char, color="#4a5568", size=64):"""

content = content.replace("    def create_colored_icon(self, char, color=\"#4a5568\", size=64):", methods)

# 3. Update menus
menu_upload_old = """        # Fixture Sub-menu
        fixture_menu = menu.addMenu("FIXTURE")
        fixture_act = QAction("fixture.stl", self)
        fixture_act.triggered.connect(lambda: self.on_fixture_change(1))
        fixture_menu.addAction(fixture_act)
        
        # Other Uploads
        menu.addAction("SINGLE STL").triggered.connect(self.load_single_stl)
        menu.addAction("SINGLE ALIGNER").triggered.connect(self.load_aligner_stl)
        menu.addSeparator()
        menu.addAction("BATCH STL").triggered.connect(self.add_to_batch)
        menu.addAction("BATCH ALIGNER").triggered.connect(self.add_aligners_to_batch)"""

menu_upload_new = """        # Fixture Sub-menu
        fixture_menu = menu.addMenu("FIXTURE")
        self.act_fixture = QAction("fixture.stl", self)
        self.act_fixture.triggered.connect(lambda: self.on_fixture_change(1))
        fixture_menu.addAction(self.act_fixture)
        
        # Other Uploads
        self.act_single_stl = menu.addAction("SINGLE STL")
        self.act_single_stl.triggered.connect(self.load_single_stl)
        self.act_single_aligner = menu.addAction("SINGLE ALIGNER")
        self.act_single_aligner.triggered.connect(self.load_aligner_stl)
        menu.addSeparator()
        self.act_batch_stl = menu.addAction("BATCH STL")
        self.act_batch_stl.triggered.connect(self.add_to_batch)
        self.act_batch_aligner = menu.addAction("BATCH ALIGNER")
        self.act_batch_aligner.triggered.connect(self.add_aligners_to_batch)"""

content = content.replace(menu_upload_old, menu_upload_new)

menu_dl_old = """        menu.addAction("SAVE STL").triggered.connect(self.save_model_result)
        menu.addAction("SAVE CLEAR ALIGNER").triggered.connect(self.save_aligner_result)"""

menu_dl_new = """        self.act_save_stl = menu.addAction("SAVE STL")
        self.act_save_stl.triggered.connect(self.save_model_result)
        self.act_save_aligner = menu.addAction("SAVE CLEAR ALIGNER")
        self.act_save_aligner.triggered.connect(self.save_aligner_result)"""

content = content.replace(menu_dl_old, menu_dl_new)

# 4. Insert calls
# on_fixture_change
content = re.sub(r'(def on_fixture_change.*?self\.viewer\.load_fixture.*?self\.status_label\.setText\("FIXTURE LOADED.*?"\))', r'\1\n        self.update_workflow_state(1)', content, flags=re.DOTALL)

# load_single_stl
content = re.sub(r'(def load_single_stl.*?self\._load_stl_file\(file_path\).*?self\.status_label\.setText\("SINGLE STL LOADED"\))', r'\1\n            self.update_workflow_state(2)', content, flags=re.DOTALL)

# load_batch_stl / add_to_batch
content = re.sub(r'(def add_to_batch.*?self\.continue_btn\.setEnabled\(True\))', r'\1\n            self.update_workflow_state(2)', content, flags=re.DOTALL)

# load_aligner_stl
content = re.sub(r'(def load_aligner_stl.*?self\._load_aligner_with_replay\(file_path\).*?pass)', r'\1\n            self.update_workflow_state(3)', content, flags=re.DOTALL)

# add_aligners_to_batch
content = re.sub(r'(def add_aligners_to_batch.*?self\.update_batch_list_labels\(\))', r'\1\n            self.update_workflow_state(3)', content, flags=re.DOTALL)

# align_model_center_to_global_origin
content = re.sub(r'(def align_model_center_to_global_origin.*?self\.status_label\.setText\("MODEL CENTERED TO ORIGIN"\))', r'\1\n        self.update_workflow_state(4)', content, flags=re.DOTALL)

# toggle_select_mode
content = re.sub(r'(def toggle_select_mode.*?self\.status_label\.setText\("FACE SELECTION MODE ACTIVE"\))', r'\1\n            self.update_workflow_state(5)', content, flags=re.DOTALL)

# flatten_to_grid
content = re.sub(r'(def flatten_to_grid.*?self\.status_label\.setText\("MODEL FLATTENED TO GRID"\))', r'\1\n        self.update_workflow_state(6)', content, flags=re.DOTALL)

# toggle_precise_controls
content = re.sub(r'(def toggle_precise_controls.*?self\.slider_overlay\.show\(\))', r'\1\n            self.update_workflow_state(7)', content, flags=re.DOTALL)

# merge_fixture_model
content = re.sub(r'(def merge_fixture_model.*?self\.status_label\.setText\("FIXTURE MERGED.*?"\))', r'\1\n        self.update_workflow_state(8)', content, flags=re.DOTALL)

# run_batch_processing
content = re.sub(r'(def run_batch_processing.*?self\.status_label\.setText\("BATCH PROCESSING COMPLETE"\))', r'\1\n        self.update_workflow_state(9)', content, flags=re.DOTALL)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Patcher finished.")

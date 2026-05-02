import sys
import os
import numpy as np
import open3d as o3d
import win32gui
import win32con
import win32api
import copy
import time

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QWindow, QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QFileDialog, QSlider, QFrame, QComboBox,
    QListWidget, QListWidgetItem, QMessageBox, QToolButton
)


def flatten_stl_to_grid(mesh, target_normal, face_center=None, target_center=None, pivot=None):
    """Rotation + Minimal Z-Correction to seat face on grid."""
    normal = target_normal / (np.linalg.norm(target_normal) + 1e-9)
    target = np.array([0, 0, -1])

    # Use provided pivot or mesh center to keep centroid fixed in XY
    if pivot is None:
        pivot = mesh.get_center()

    # 1. ROTATE (Around pivot)
    axis = np.cross(normal, target)
    axis_len = np.linalg.norm(axis)

    if axis_len > 1e-6:
        axis = axis / axis_len
        angle = np.arccos(np.clip(np.dot(normal, target), -1, 1))
        R = o3d.geometry.get_rotation_matrix_from_axis_angle(axis * angle)
        mesh.rotate(R, center=pivot)
        if face_center is not None:
            # Transform face_center point to find its new position
            face_center = pivot + R @ (face_center - pivot)
            
    elif np.dot(normal, target) < -0.99:
        R = o3d.geometry.get_rotation_matrix_from_axis_angle(np.array([1, 0, 0]) * np.pi)
        mesh.rotate(R, center=pivot)
        if face_center is not None:
            face_center = pivot + R @ (face_center - pivot)

    # 2. SEAT ON GRID (Minimum Z to Target Z)
    bbox = mesh.get_axis_aligned_bounding_box()
    min_z = bbox.get_min_bound()[2]
    
    if target_center is None:
        target_z = 0.0
    else:
        target_z = target_center[2]
    
    # If we have a face center, we can try to seat that specific point on the grid
    if face_center is not None:
        translation_z = target_z - face_center[2]
    else:
        translation_z = target_z - min_z
        
    mesh.translate([0, 0, translation_z])
    mesh.compute_vertex_normals()



class Open3DWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.vis = o3d.visualization.Visualizer()
        self.vis.create_window(visible=False)

        hwnd = win32gui.FindWindowEx(0, 0, None, "Open3D")
        self.window = QWindow.fromWinId(hwnd)
        self.container = QWidget.createWindowContainer(self.window)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.container)
        self.setLayout(layout)

        self.hwnd = hwnd
        self.part = None
        self.aligner = None
        self.fixture = None
        self.select_mode_active = False # Initialized
        
        self.grid = None
        self.original_vertices = None
        self.original_aligner_vertices = None

        self.parent_window = None
        self.origin_marker = None
        self.part_center_marker = None
        self.origin_axes = None
        self.origin_pos = np.array([0.0, 0.0, 0.0])
        self.last_click_time = 0 # Initialized for Double-Click
        self.last_mouse_pos = None
        self.last_click_time = 0
        self.l_was_down = False
        self.transform_mode = False
        self.select_mode_active = False
        self.cam_locked = False
        
        # Raycasting Selection
        self.selected_face_normal = None
        self.selected_face_id = None # Store the triangle index

        # Configure Background - CLINICAL THEME
        opt = self.vis.get_render_option()
        opt.background_color = np.asarray([0.98, 0.98, 1.0]) # Cool White / Technical Blue tint
        opt.line_width = 1.0 # Thin, precise lines
        opt.light_on = True
        opt.mesh_show_back_face = True

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_vis)
        self.timer.start(16)

        self.add_grid()

    def add_grid(self):
        size = 200 # Larger grid
        step = 2 # DENSE TECHNICAL GRID
        points = []
        lines = []

        for i in range(-size, size + step, step):
            points.append([i, -size, 0])
            points.append([i, size, 0])
            lines.append([len(points) - 2, len(points) - 1])

            points.append([-size, i, 0])
            points.append([size, i, 0])
            lines.append([len(points) - 2, len(points) - 1])

        grid = o3d.geometry.LineSet()
        grid.points = o3d.utility.Vector3dVector(points)
        grid.lines = o3d.utility.Vector2iVector(lines)
        grid.paint_uniform_color([0.75, 0.8, 0.85]) # Technical Blue

        self.grid = grid
        self.vis.add_geometry(grid)
        self.add_origin_marker()

    def load_fixture(self, path):
        mesh = o3d.io.read_triangle_mesh(path)
        mesh.compute_vertex_normals()
        mesh.paint_uniform_color([0.6, 0.6, 0.62]) # Fixture: Professional Technical Gray

        # REMOVE OLD FIXTURE TO PREVENT OVERLAP
        if self.fixture:
            try: self.vis.remove_geometry(self.fixture)
            except: pass

        self.fixture = mesh
        self.vis.add_geometry(mesh)
        
        self.vis.reset_view_point(True)
        self.vis.update_renderer()

    def load_part(self, path):
        mesh = o3d.io.read_triangle_mesh(path)
        mesh.compute_vertex_normals()
        mesh.paint_uniform_color([0.72, 0.58, 0.45]) # Part: Goldish/Tan

        if len(mesh.vertices) == 0:
            print(f"ERROR: Failed to load mesh at {path}")
            return

        # REMOVE OLD PART TO PREVENT "DOUBLE MODEL" ERROR
        if self.part:
            try: self.vis.remove_geometry(self.part)
            except: pass

        self.part = mesh
        self.original_vertices = np.asarray(mesh.vertices).copy()
        
        self.selected_face_normal = None
        self.selected_face_id = None

        self.update_part_center_marker()
        self.vis.add_geometry(mesh)
        self.vis.reset_view_point(True)
        self.vis.update_renderer()

    def load_aligner(self, path):
        mesh = o3d.io.read_triangle_mesh(path)
        self.load_aligner_from_mesh(mesh)

    def load_aligner_from_mesh(self, mesh):
        mesh.compute_vertex_normals()
        mesh.paint_uniform_color([0.16, 0.71, 0.96])  # Blue aligner

        if self.aligner:
            try:
                self.vis.remove_geometry(self.aligner)
            except:
                pass

        self.aligner = mesh
        self.original_aligner_vertices = np.asarray(mesh.vertices).copy()

        self.vis.add_geometry(mesh)
        self.vis.update_renderer()

    def add_origin_marker(self):
        """Adds a gray point at (0,0,0) and XYZ axis lines."""
        if self.origin_marker:
            try: self.vis.remove_geometry(self.origin_marker)
            except: pass
        if self.origin_axes:
            try: self.vis.remove_geometry(self.origin_axes)
            except: pass

        # Gray Sphere at Origin [0, 0, 0]
        sphere = o3d.geometry.TriangleMesh.create_sphere(radius=1.5)
        sphere.paint_uniform_color([0.5, 0.5, 0.5]) # Gray
        sphere.translate([0, 0, 0])
        self.origin_marker = sphere
        self.vis.add_geometry(sphere)

        # Axis Lines
        axes = o3d.geometry.TriangleMesh.create_coordinate_frame(size=30.0, origin=[0, 0, 0])
        self.origin_axes = axes
        self.vis.add_geometry(axes)

    def update_origin_marker_position(self, new_pos):
        """Moves the blue origin marker and axes to a new position."""
        if self.origin_marker:
            old_pos = self.origin_pos
            diff = np.array(new_pos) - old_pos
            self.origin_marker.translate(diff)
            self.origin_axes.translate(diff)
            self.origin_pos = np.array(new_pos)
            self.vis.update_geometry(self.origin_marker)
            self.vis.update_geometry(self.origin_axes)

    def update_part_center_marker(self):
        """Adds/Updates a yellow point at the center of the part mesh."""
        if not self.part:
            return

        # Reverted to Centroid for visual marker (Yellow Dot) as requested for perfect alignment
        center = self.part.get_center()

        if self.part_center_marker:
            # Shift existing marker to new center
            old_center = self.part_center_marker.get_center()
            self.part_center_marker.translate(center - old_center)
            self.part_center_marker.paint_uniform_color([1.0, 1.0, 0.0]) # Ensure it's Yellow
            self.vis.update_geometry(self.part_center_marker)
        else:
            # Create new yellow sphere (Increased size)
            sphere = o3d.geometry.TriangleMesh.create_sphere(radius=1.5)
            sphere.paint_uniform_color([1.0, 1.0, 0.0]) # Yellow
            sphere.translate(center)
            self.part_center_marker = sphere
            self.vis.add_geometry(sphere)

    def update_vis(self):
        # Must call handle_mouse_transform if we are MOVING the part OR SELECTING a face
        if self.transform_mode or self.select_mode_active:
            self.handle_mouse_transform()
        elif self.cam_locked:
            ctr = self.vis.get_view_control()
            ctr.set_lookat([0, 0, 0])
        self.vis.poll_events()
        self.vis.update_renderer()

    def handle_mouse_transform(self):
    
        if not self.part or not self.parent_window:
            return
    
        try:
            cursor_pos = win32gui.GetCursorPos()
            x, y = win32gui.ScreenToClient(self.hwnd, cursor_pos)
    
            rect = win32gui.GetClientRect(self.hwnd)
            inside = 0 <= x < rect[2] and 0 <= y < rect[3]
    
    
            l_down = win32api.GetAsyncKeyState(win32con.VK_LBUTTON) < 0
            r_down = win32api.GetAsyncKeyState(win32con.VK_RBUTTON) < 0
            shift_down = win32api.GetAsyncKeyState(win32con.VK_SHIFT) < 0
    
            l_pressed = l_down and not getattr(self, "l_was_down", False)
            r_pressed = r_down and not getattr(self, "r_was_down", False)
            self.l_was_down = l_down
            self.r_was_down = r_down
    
            if inside and (l_down or r_down):
                # SAVE STATE FOR UNDO ON FIRST PRESS
                if (l_pressed or r_pressed) and self.parent_window:
                    self.parent_window.save_undo_state()
            
                # Check for Double Click
                if l_pressed and self.select_mode_active:
                    current_time = time.time()
                    if current_time - self.last_click_time < 0.5: # More sensitive (0.5s)
                        self.perform_face_selection(x, y)
                        self.last_click_time = 0 
                    else:
                        self.last_click_time = current_time
    
                if self.last_mouse_pos:
    
                    dx = x - self.last_mouse_pos[0]
                    dy = y - self.last_mouse_pos[1]
    
                    mesh = self.part
                    center = mesh.get_center()
    
                    # -------- ROTATE X/Y --------
                    if r_down and not shift_down:
    
                        Rx = o3d.geometry.get_rotation_matrix_from_xyz(
                            (np.deg2rad(dy * 0.5), 0, 0)
                        )
    
                        Ry = o3d.geometry.get_rotation_matrix_from_xyz(
                            (0, np.deg2rad(dx * 0.5), 0)
                        )
    
                        mesh.rotate(Rx, center=center)
                        mesh.rotate(Ry, center=center)
                        
                        if self.aligner:
                            self.aligner.rotate(Rx, center=center)
                            self.aligner.rotate(Ry, center=center)
                            self.vis.update_geometry(self.aligner)
                            self.original_aligner_vertices = np.asarray(self.aligner.vertices).copy()

                        self.update_part_center_marker()

                        if self.parent_window:
                            self.parent_window.record_operation({'type': 'rotate', 'matrix': Rx, 'center': center})
                            self.parent_window.record_operation({'type': 'rotate', 'matrix': Ry, 'center': center})
    
                    # -------- ROTATE Z --------
                    elif r_down and shift_down:
    
                        Rz = o3d.geometry.get_rotation_matrix_from_xyz(
                            (0, 0, np.deg2rad(dx * 0.5))
                        )
    
                        mesh.rotate(Rz, center=center)
                        
                        if self.aligner:
                            self.aligner.rotate(Rz, center=center)
                            self.vis.update_geometry(self.aligner)
                            self.original_aligner_vertices = np.asarray(self.aligner.vertices).copy()

                        self.update_part_center_marker()

                        if self.parent_window:
                            self.parent_window.record_operation({'type': 'rotate', 'matrix': Rz, 'center': center})
    
                    # -------- TRANSLATE XY --------
                    elif l_down and shift_down:
    
                        mesh.translate([dx * 0.02, -dy * 0.02, 0])
                        
                        if self.aligner:
                            self.aligner.translate([dx * 0.02, -dy * 0.02, 0])
                            self.vis.update_geometry(self.aligner)
                            self.original_aligner_vertices = np.asarray(self.aligner.vertices).copy()

                        self.update_part_center_marker()

                        if self.parent_window:
                            self.parent_window.record_operation({'type': 'translate', 'vector': [dx * 0.02, -dy * 0.02, 0]})
    
                    self.vis.update_geometry(mesh)
                    
                    # UPDATE BASELINE TO PREVENT SLIDER RESET
                    self.original_vertices = np.asarray(mesh.vertices).copy()
                    if self.parent_window:
                        self.parent_window.internal_update = True
                        self.parent_window.tx.setValue(0)
                        self.parent_window.ty.setValue(0)
                        self.parent_window.rot_z.setValue(0)
                        self.parent_window.internal_update = False
    
                self.last_mouse_pos = (x, y)
    
            else:
                self.last_mouse_pos = None
    
            # -------- TRANSLATE Z (Scroll wheel / trackpad scroll) --------
            wheel = win32api.GetAsyncKeyState(win32con.VK_MBUTTON)
    
            if wheel != 0:
    
                mesh = self.part
                mesh.translate([0, 0, 0.1])
    
                # keep above grid
                vertices = np.asarray(mesh.vertices)
                min_z = vertices[:,2].min()
    
                if min_z < 0:
                    mesh.translate([0,0,-min_z])
                    if self.parent_window:
                        self.parent_window.record_operation({'type': 'translate', 'vector': [0,0,-min_z]})
                
                if self.parent_window:
                    self.parent_window.record_operation({'type': 'translate', 'vector': [0, 0, 0.1]})
    
                if self.aligner:
                    self.aligner.translate([0, 0, 0.1])
                    if min_z < 0:
                        self.aligner.translate([0, 0, -min_z])
                    self.vis.update_geometry(self.aligner)
                    self.original_aligner_vertices = np.asarray(self.aligner.vertices).copy()

                self.update_part_center_marker()

                self.vis.update_geometry(mesh)
    
        except Exception:
            pass

    def toggle_selection_mode(self):
        self.select_mode_active = not self.select_mode_active
        return self.select_mode_active

    def perform_face_selection(self, x, y):
        """
        Raycasts to find the triangle under mouse and highlights it.
        """
        # Convert Open3D mesh to Tensor for raycasting
        t_mesh = o3d.t.geometry.TriangleMesh.from_legacy(self.part)
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(t_mesh)

        # Get View Camera
        ctr = self.vis.get_view_control()
        cam_params = ctr.convert_to_pinhole_camera_parameters()
        
        # Open3D coordinate fix for Raycasting
        rect = win32gui.GetClientRect(self.hwnd)
        rays = scene.create_rays_pinhole(
            intrinsic_matrix=cam_params.intrinsic.intrinsic_matrix,
            extrinsic_matrix=cam_params.extrinsic,
            width_px=rect[2], height_px=rect[3]
        )
        
        # Use single ray from click point (Slice to keep shape 1, 1, 6)
        ray = rays[y:y+1, x:x+1]
        ans = scene.cast_rays(ray)
        
        face_id = ans['primitive_ids'][0, 0].item()
        
        # 4294967295 (uint32 max) means no intersection
        if face_id != 4294967295:
            # Color logic: reset all colors then highlight the face area
            # Ensure part color remains gold during selection
            self.part.paint_uniform_color([0.72, 0.58, 0.45])
            
            # Initialize vertex colors if missing
            if not self.part.has_vertex_colors():
                self.part.paint_uniform_color([0.72, 0.58, 0.45])

            tri = np.asarray(self.part.triangles)[face_id]
            
            # Extract basic normal
            v1 = np.asarray(self.part.vertices)[tri[0]]
            v2 = np.asarray(self.part.vertices)[tri[1]]
            v3 = np.asarray(self.part.vertices)[tri[2]]
            
            normal = np.cross(v2 - v1, v3 - v1)
            self.selected_face_normal = normal / (np.linalg.norm(normal) + 1e-9)
            self.selected_face_id = face_id 
            
            # Turn selected region bright red
            vertex_colors = np.asarray(self.part.vertex_colors).copy()
            vertex_colors[tri] = [1.0, 0.0, 0.0] 
            self.part.vertex_colors = o3d.utility.Vector3dVector(vertex_colors)
            
            # ADD RED DOT (Sphere) at hit point
            hit_point = ans['points'][0, 0].numpy()
            if hasattr(self, 'selection_sphere') and self.selection_sphere:
                try: self.vis.remove_geometry(self.selection_sphere)
                except: pass
                
            self.selection_sphere = o3d.geometry.TriangleMesh.create_sphere(radius=0.8)
            self.selection_sphere.paint_uniform_color([1.0, 0.0, 0.0])
            self.selection_sphere.translate(hit_point)
            self.vis.add_geometry(self.selection_sphere)

            self.vis.update_geometry(self.part)
            if self.parent_window:
                self.parent_window.status_label.setText(f"FACE SELECTED (ID: {face_id})")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.hwnd:
            win32gui.MoveWindow(self.hwnd, 0, 0, self.container.width(), self.container.height(), True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("TrimBase Desktop - V 1.0.9")
        self.setMinimumSize(1280, 720)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setSpacing(0)

        self.batch_mode = False
        self.stl_files = [] # List of paths
        self.aligner_files = [] # List of aligner paths for batch
        self.processed_results = [] # List of paths to aligned model files
        self.aligner_results = [] # List of paths to aligned aligner files
        self.current_view_index = 0
        self.operation_history = []
        self.undo_stack = [] # State stack for undo [ (part_vertices, aligner_vertices, op_history_len) ]
        self.current_fixture_path = None 
        
        self.saved_transform = {
            "tx": 0, "ty": 0, "tz": 0,
            "rx": 0, "ry": 0, "rz": 0
        }
        self.invert_mouse = True
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
        self.update_workflow_state(0)



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

    def create_colored_icon(self, char, color="#4a5568", size=64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color))
        # Ensure size is at least 10px to avoid Qt warnings
        f_size = max(10, int(size * 0.55))
        font = QFont("Segoe UI Symbol", f_size)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, char)
        painter.end()
        return QIcon(pixmap)

    def _create_tool_btn(self, icon_char, label, callback, checkable=False):
        btn = QToolButton()
        btn.setObjectName("tool-btn")
        btn.setIcon(self.create_colored_icon(icon_char, "#0066ff"))
        btn.setIconSize(QSize(28, 28))
        btn.setText(label.upper()) # UPPERCASE as per mockup
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCheckable(checkable)
        if callback:
            btn.clicked.connect(callback)
        return btn

    def _init_ui_components(self):
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout = QVBoxLayout()
        self.container_layout.setSpacing(0)
        self.main_layout.addLayout(self.container_layout)

        # 1. TOP NAVIGATION BAR (Thin White/Blue Bar)
        self.top_bar = QFrame()
        self.top_bar.setFixedHeight(50) # Thinner, more elegant
        self.top_bar.setObjectName("top-bar")
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(25, 0, 25, 0)
        top_layout.setSpacing(40)

        # LOGO
        self.logo_lbl = QLabel()
        pixmap = QPixmap("logo.png")
        if not pixmap.isNull():
            # Scale to a sensible height for the top bar
            pixmap = pixmap.scaledToHeight(35, Qt.TransformationMode.SmoothTransformation)
            self.logo_lbl.setPixmap(pixmap)
        else:
            self.logo_lbl.setText("Trim<sup>®</sup><span style='font-size: 16px; font-weight: normal;'>Base</span>")
            self.logo_lbl.setStyleSheet("color: #0066ff; font-weight: 800; font-size: 22px;")
        
        # Apply the margin to the label regardless of whether it's text or an image
        self.logo_lbl.setContentsMargins(0, 0, 20, 0)
        top_layout.addWidget(self.logo_lbl)

        # TOP MENU BUTTONS
        self.btn_upload = QPushButton("UPLOAD")
        self.btn_upload.setObjectName("nav-btn")
        self._setup_upload_menu()
        
        self.btn_undo = QPushButton("UNDO")
        self.btn_undo.setObjectName("nav-btn")
        self.btn_undo.clicked.connect(self.perform_undo)

        self.btn_guide = QPushButton("GUIDE")
        self.btn_guide.setObjectName("nav-btn")
        self.btn_guide.clicked.connect(self.show_guide_window)

        self.btn_download = QPushButton("DOWNLOAD")
        self.btn_download.setObjectName("nav-btn")
        self._setup_download_menu()

        top_layout.addWidget(self.btn_upload)
        top_layout.addWidget(self.btn_undo)
        top_layout.addWidget(self.btn_guide)
        top_layout.addWidget(self.btn_download)
        top_layout.addStretch()
        
        # Top Right Actions
        # Top Right Actions (REMOVED as per request)
        
        self.btn_top_continue = QPushButton("CONTINUE  >")
        self.btn_top_continue.setObjectName("top-continue-btn")
        top_layout.addWidget(self.btn_top_continue)

        self.container_layout.addWidget(self.top_bar)

        # 2. CENTRAL WORKSPACE (Left Tools | Viewer | Right Batch)
        self.workspace = QHBoxLayout()
        self.workspace.setSpacing(0)
        self.container_layout.addLayout(self.workspace)

        # LEFT TOOLS BAR
        self.left_bar = QFrame()
        self.left_bar.setFixedWidth(85)
        self.left_bar.setObjectName("left-bar")
        left_layout = QVBoxLayout(self.left_bar)
        left_layout.setContentsMargins(0, 20, 0, 20)
        left_layout.setSpacing(15)

        # Labels/Icons matching Mockup exactly
        self.tool_origin = self._create_tool_btn("⌖", "ORIGIN", self.align_model_center_to_global_origin)
        self.tool_face = self._create_tool_btn("⦿", "FACE", self.toggle_select_mode, True)
        self.tool_flatten = self._create_tool_btn("⊟", "FLATTEN", self.flatten_to_grid)
        self.tool_precise = self._create_tool_btn("⚙", "PRECISION", self.toggle_precise_controls, True)
        self.tool_merge = self._create_tool_btn("⧉", "MERGE", self.merge_fixture_model)

        left_layout.addWidget(self.tool_origin)
        left_layout.addWidget(self.tool_face)
        left_layout.addWidget(self.tool_flatten)
        left_layout.addWidget(self.tool_precise)
        left_layout.addWidget(self.tool_merge)
        left_layout.addStretch()
        
        self.workspace.addWidget(self.left_bar)

        # 2. VIEWER AREA + FLOATING SUB-TOOLBAR
        self.viewer_area = QFrame()
        self.viewer_area_layout = QGridLayout(self.viewer_area)
        self.viewer_area_layout.setContentsMargins(0, 0, 0, 0)
        
        self.viewer_container = QWidget()
        v_layout = QVBoxLayout(self.viewer_container)
        v_layout.setContentsMargins(0, 0, 0, 0)
        
        self.viewer = Open3DWidget()
        self.viewer.parent_window = self
        v_layout.addWidget(self.viewer, 1)
        
        # PRECISE SLIDERS (Moved to right sidebar)
        self.slider_overlay = None 
        self.rot_z = None 

        
        # FLOATING SUB-TOOLBAR (From Mockup)
        self.floating_tools = QFrame(self.viewer_area)
        self.floating_tools.setObjectName("floating-toolbar")
        float_layout = QVBoxLayout(self.floating_tools)
        float_layout.setContentsMargins(5, 5, 5, 5)
        float_layout.setSpacing(10)
        
        self.btn_move = self._create_float_btn("✥", "Move", lambda: self.toggle_transform_mode(True))
        self.btn_rotate = self._create_float_btn("⟳", "Rotate", lambda: self.toggle_transform_mode(False))
        
        float_layout.addWidget(self.btn_move)
        float_layout.addWidget(self.btn_rotate)
        
        self.viewer_area_layout.addWidget(self.viewer_container, 0, 0)
        self.viewer_area_layout.addWidget(self.floating_tools, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        self.workspace.addWidget(self.viewer_area, 1)

        # 3. BOTTOM STATS BAR
        self.bottom_bar = QFrame()
        self.bottom_bar.setFixedHeight(60)
        self.bottom_bar.setObjectName("bottom-bar")
        bottom_layout = QHBoxLayout(self.bottom_bar)
        
        self.status_icon = QLabel("✔")
        self.status_icon.setStyleSheet("color: #4caf50; font-size: 18px;")
        self.status_msg = QLabel("Scan Loaded Successfully")
        self.status_msg.setObjectName("bottom-status")
        
        bottom_layout.addWidget(self.status_icon)
        # Stats Group
        stats_box = QFrame()
        stats_box.setObjectName("bottom-stats-box")
        stats_h = QHBoxLayout(stats_box)
        
        self.v_stats = self._create_stat_item(stats_h, "Vertices")
        self.f_stats = self._create_stat_item(stats_h, "Faces")
        self.e_stats = self._create_stat_item(stats_h, "Edges")
        self.s_stats = self._create_stat_item(stats_h, "Size")
        
        bottom_layout.addWidget(stats_box)
        bottom_layout.addStretch()
        
        # Mode Group
        mode_box = QFrame()
        mode_box.setObjectName("mode-group-box")
        mode_h = QHBoxLayout(mode_box)
        mode_h.setContentsMargins(2, 2, 2, 2)
        mode_h.setSpacing(0)
        self.mode_shaded = QPushButton("Shaded")
        self.mode_wire = QPushButton("Wireframe")
        self.mode_solid = QPushButton("Solid")
        self.mode_shaded.setObjectName("mode-btn")
        self.mode_wire.setObjectName("mode-btn")
        self.mode_solid.setObjectName("mode-btn")
        
        bottom_layout.addWidget(self.mode_shaded)
        bottom_layout.addWidget(self.mode_wire)
        self.mode_shaded.clicked.connect(lambda: self.set_render_mode("shaded"))
        self.mode_wire.clicked.connect(lambda: self.set_render_mode("wireframe"))
        self.mode_solid.clicked.connect(lambda: self.set_render_mode("solid"))
        
        self.container_layout.addWidget(self.bottom_bar)

        # RIGHT BATCH SIDEBAR
        self.right_bar = QFrame()
        self.right_bar.setFixedWidth(280)
        self.right_bar.setObjectName("right-bar")
        right_layout = QVBoxLayout(self.right_bar)
        
        title_lbl = QLabel("BATCH PROCESSING")
        title_lbl.setObjectName("sidebar-title")
        right_layout.addWidget(title_lbl)
        self.batch_list = QListWidget()
        self.batch_list.setObjectName("batch-list")
        self.batch_list.itemClicked.connect(self.on_batch_item_clicked)
        right_layout.addWidget(self.batch_list)

        self.continue_btn = QPushButton("CONTINUE BATCH")
        self.continue_btn.setObjectName("action-btn")
        self.continue_btn.clicked.connect(self.run_batch_processing)
        right_layout.addWidget(self.continue_btn)
        right_layout.addSpacing(20)

        # Display Section
        disp_lbl = QLabel("DISPLAY")
        disp_lbl.setObjectName("sidebar-title")
        right_layout.addWidget(disp_lbl)
        
        self.chk_grid = self._create_sidebar_toggle(right_layout, "Show Grid", True, self.toggle_grid)
        self.chk_axis = self._create_sidebar_toggle(right_layout, "Show Axis", True, self.toggle_axis)
        
        right_layout.addSpacing(20)
        
        # Tools Section
        tool_lbl = QLabel("TOOLS")
        tool_lbl.setObjectName("sidebar-title")
        right_layout.addWidget(tool_lbl)
        
        self.precision_container = QFrame()
        self.precision_container.setObjectName("precision-box")
        p_layout = QVBoxLayout(self.precision_container)
        p_layout.setContentsMargins(0, 0, 0, 0)
        self.rot_z = self._create_slider(p_layout, "PRECISION ROTATE Z")
        right_layout.addWidget(self.precision_container)
        self.precision_container.hide()

        self.precision_msg = QLabel("Precision Alignment Tools Active")
        self.precision_msg.setStyleSheet("color: #a0aec0; font-size: 10px; font-style: italic;")
        right_layout.addWidget(self.precision_msg)

        
        right_layout.addSpacing(20)

        # Part Info Section
        info_lbl = QLabel("PART INFO")
        info_lbl.setObjectName("sidebar-title")
        right_layout.addWidget(info_lbl)

        # Status Group
        self.status_group = QFrame()
        self.status_group.setObjectName("status-group")
        status_vbox = QVBoxLayout(self.status_group)
        
        self.part_lbl = QLabel("PART: NOT LOADED")
        self.pos_readout = QLabel("X: 0.0, Y: -0.0, Z: 0.0")
        self.fixture_lbl = QLabel("MODEL: NOT LOADED")
        
        status_vbox.addWidget(self.part_lbl)
        status_vbox.addWidget(self.pos_readout)
        status_vbox.addWidget(self.fixture_lbl)
        right_layout.addWidget(self.status_group)
        
        self.status_label = QLabel("SYSTEM READY")
        self.status_label.setObjectName("status-text")
        right_layout.addWidget(self.status_label)

        self.workspace.addWidget(self.right_bar)
        
        # Hidden Sliders for background logic compatibility
        self.tx = QSlider(Qt.Orientation.Horizontal)
        self.ty = QSlider(Qt.Orientation.Horizontal)
        self.tx.setRange(-300, 300); self.ty.setRange(-300, 300)
        self.tx.hide(); self.ty.hide()
        
        self.apply_styles()

    def on_fixture_change(self, index):
        # Index check or filename check
        fixture_path = os.path.join(os.getcwd(), "fixture.stl")
        
        if os.path.exists(fixture_path):
            self.current_fixture_path = fixture_path
            self.viewer.load_fixture(fixture_path)
            
            self.status_label.setText("LOADED: FIXTURE MODEL")
            self.fixture_lbl.setText("FIXTURE: LOADED")
            self.fixture_lbl.setStyleSheet("color: #ffffff; font-weight: bold;")
            self.update_workflow_state(1)
        else:
            self.status_label.setText("ERROR: fixture.stl NOT FOUND")
            self.fixture_lbl.setText("FIXTURE: NOT FOUND")
            self.fixture_lbl.setStyleSheet("color: #ff4444; font-weight: bold;")

    def _create_slider(self, layout, text):
        container = QFrame()
        container.setObjectName("dark-box")
        container.setStyleSheet("""
            QFrame#dark-box {
                background-color: #f7fafc;
                border: 1px solid #edf2f7;
                border-radius: 8px;
                padding: 10px;
                margin-bottom: 5px;
            }
        """)
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(10, 10, 10, 10)
        c_layout.setSpacing(10)

        label = QLabel(text)
        label.setStyleSheet("color: #2d3748; font-weight: 800; font-size: 10px; letter-spacing: 0.5px;")
        c_layout.addWidget(label)

        # Controls Row
        row = QHBoxLayout()
        row.setSpacing(10)

        btn_style = """
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #cbd5e0;
                border-radius: 12px;
                color: #4a5568;
                font-weight: bold;
                font-size: 14px;
                min-width: 24px;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #edf2f7;
                border-color: #0066ff;
                color: #0066ff;
            }
            QPushButton:pressed {
                background-color: #ebf4ff;
            }
        """

        minus_btn = QPushButton("-")
        minus_btn.setStyleSheet(btn_style)
        
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(-300, 300)
        slider.setValue(0)
        slider.setFixedHeight(20)
        slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #edf2f7;
                height: 4px;
                background: #e2e8f0;
                margin: 2px 0;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #0066ff;
                border: 1px solid #0066ff;
                width: 14px;
                height: 14px;
                margin: -6px 0;
                border-radius: 7px;
            }
        """)

        plus_btn = QPushButton("+")
        plus_btn.setStyleSheet(btn_style)

        minus_btn.clicked.connect(lambda: slider.setValue(slider.value() - 5))
        plus_btn.clicked.connect(lambda: slider.setValue(slider.value() + 5))
        slider.valueChanged.connect(self.update_part_transform)

        row.addWidget(minus_btn)
        row.addWidget(slider)
        row.addWidget(plus_btn)
        c_layout.addLayout(row)
        
        layout.addWidget(container)
        return slider


    def load_batch_stl(self):
        self.add_to_batch()

    def _extract_number(self, filename):
        import re
        # Find all numbers, pick the last non-extension number or the one likely to be the identifier
        nums = re.findall(r'\d+', filename)
        if not nums: return None
        # Often the last number in the filename (before extension) is the ID
        return nums[-1]

    def _load_stl_file(self, file_path):
        """Loads the STL into the viewer alongside the reference fixture."""
        self.viewer.load_part(file_path)
        
        # SMART-SILENCE: If user uploads an ALREADY MERGED output view, hide reference to avoid overlap.
        # Otherwise, ALWAYS show the reference fixture waiting on the grid.
        if "merged" in os.path.basename(file_path).lower():
            if self.viewer.fixture:
                try: self.viewer.vis.remove_geometry(self.viewer.fixture)
                except: pass
        else:
            if self.viewer.fixture:
                try: self.viewer.vis.add_geometry(self.viewer.fixture)
                except: pass
        
        self.internal_update = True
        self.tx.setValue(0); self.ty.setValue(0); self.rot_z.setValue(0)
        self.internal_update = False
        
        # Reset Flatten Lock
        self.is_flattened = False
        self.tool_flatten.setEnabled(True)
        self.tool_precise.setEnabled(True)
        self.tool_flatten.setStyleSheet("border-color: #1976d2; color: #1976d2;")
        
        m_lbl = "PART: MOVABLE" + (" (BATCH)" if len(self.stl_files) > 1 else "")
        self.part_lbl.setText(m_lbl)
        
        # Update Stats
        if self.viewer.part:
            self.v_stats.setText(f"{len(self.viewer.part.vertices):,}")
            self.f_stats.setText(f"{len(self.viewer.part.triangles):,}")
            # Edges estimate
            self.e_stats.setText(f"{int(len(self.viewer.part.triangles)*1.5):,}")
            self.s_stats.setText(f"{os.path.getsize(file_path)/1024/1024:.1f} MB")
        
        self.status_msg.setText(f"Model Loaded: {os.path.basename(file_path)}")
        
        self.update_part_transform()

    def _reset_batch_session(self, first_file):
        """Initializes a new session and clears previous data."""
        self.operation_history = []
        self.batch_list.clear()
        self.stl_files = [first_file]
        self.aligner_files = [] # Reset aligners too
        self.processed_results = [first_file]
        self.current_view_index = 0
        self.batch_mode = True
        
        # Reset navigation
        self.show_view_model_controls(False)
        
        item = QListWidgetItem(f"{os.path.basename(first_file)} [1/1]")
        self.batch_list.addItem(item)
        self.batch_list.setCurrentItem(item)
        
        if self.viewer.aligner:
            try: self.viewer.vis.remove_geometry(self.viewer.aligner)
            except: pass
            self.viewer.aligner = None
            self.viewer.original_aligner_vertices = None

        self._load_stl_file(first_file)

    def load_single_stl(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open STL", "", "3D Files (*.stl *.pts *.xyz *.obj *.ply)"
        )
        if file_path:
            self._reset_batch_session(file_path)
            self.batch_mode = False # Explicitly single
            self.status_label.setText(f"SESSION RESET: SINGLE FILE")
            self.update_workflow_state(2)
            self.show_aligner_upload(False)

    def show_aligner_upload(self, visible):
        pass

    def record_operation(self, op):
        """Logs an operation to the history for batch replay."""
        if hasattr(self, 'operation_history'):
            self.operation_history.append(op)

            # Enable continue if we have operations and files
            if self.batch_mode and len(self.stl_files) > 1:
                self.continue_btn.setEnabled(True)

    def add_to_batch(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add STL Files to Batch", "", "3D Files (*.stl *.obj *.ply)"
        )
        if files:
            if not self.stl_files:
                # First time adding, reset session with first file
                self._reset_batch_session(files[0])
                remaining_files = files[1:]
            else:
                remaining_files = files
                
            for f in remaining_files:
                if f not in self.stl_files:
                    self.stl_files.append(f)
                    item = QListWidgetItem(os.path.basename(f))
                    self.batch_list.addItem(item)
            
            self.batch_mode = True
            self.update_batch_list_labels()
            self.update_workflow_state(2)
            self.status_label.setText(f"BATCH UPDATED: {len(self.stl_files)} FILES")
            self.continue_btn.setEnabled(True)

    def add_aligners_to_batch(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add Aligner STL Files to Batch", "", "3D Files (*.stl)"
        )
        if files:
            for f in files:
                if f not in self.aligner_files:
                    self.aligner_files.append(f)
            
            self.status_label.setText(f"BATCH: {len(self.stl_files)} MODELS, {len(self.aligner_files)} ALIGNERS")
            self.update_batch_list_labels()
            self.update_workflow_state(3)
            self.update_workflow_state(3)
            self.update_workflow_state(3)
            
            # AUTOMATICALLY LOAD THE MATCHING ALIGNER FOR THE MASTER (If viewing model 0)
            if self.current_view_index == 0 and self.stl_files:
                master_model_path = self.stl_files[0]
                model_num = self._extract_number(os.path.basename(master_model_path))
                if model_num:
                    for af in self.aligner_files:
                        if self._extract_number(os.path.basename(af)) == model_num:
                            # Use the overlay loading logic
                            self._load_aligner_with_replay(af)
                            self.status_label.setText(f"MASTER ALIGNER MATCHED: {os.path.basename(af)}")
                            break
    def _load_aligner_with_replay(self, file_path):
        """Loads aligner and replays all model transforms so it overlays perfectly."""

        mesh = o3d.io.read_triangle_mesh(file_path)
        mesh.compute_vertex_normals()

        if len(mesh.vertices) == 0:
            return

        local_baseline = np.asarray(mesh.vertices).copy()

        # 🔥 Replay ALL operations done on STL model
        for op in self.operation_history:

            if op['type'] == 'rotate':
                mesh.rotate(op['matrix'], center=op['center'])
                local_baseline = np.asarray(mesh.vertices).copy()

            elif op['type'] == 'translate':
                mesh.translate(op['vector'])
                local_baseline = np.asarray(mesh.vertices).copy()

            elif op['type'] == 'slider_transform':
                tx, ty, rz = op['tx'], op['ty'], op['rz']

                Rz = np.array([
                    [np.cos(rz), -np.sin(rz), 0],
                    [np.sin(rz),  np.cos(rz), 0],
                    [0, 0, 1]
                ])

                v = local_baseline @ Rz.T
                v[:, 0] += tx
                v[:, 1] += ty

                mesh.vertices = o3d.utility.Vector3dVector(v)

            elif op['type'] == 'flatten':
                if 'total_R' in op:
                    mesh.rotate(op['total_R'], center=op['pivot'])
                else:
                    pivot = mesh.get_center()
                    flatten_stl_to_grid(
                        mesh,
                        op['normal'],
                        op.get('face_center'),
                        op.get('target_center'),
                        pivot=pivot
                    )

                if 'translation' in op:
                    mesh.translate(op['translation'])

                local_baseline = np.asarray(mesh.vertices).copy()

            elif op['type'] == 'unlock':
                local_baseline = np.asarray(mesh.vertices).copy()

            elif op['type'] == 'center_origin':
                if 'translation' in op:
                    mesh.translate(op['translation'])
                else:
                    c = mesh.get_center()
                    target = op.get('target', np.array([0.0, 0.0, 0.0]))
                    mesh.translate(target - c)

                local_baseline = np.asarray(mesh.vertices).copy()

            elif op['type'] == 'icp':
                mesh.transform(op['matrix'])
                local_baseline = np.asarray(mesh.vertices).copy()

        # ✅ FINAL: Load into viewer (overlay happens here)
        self.viewer.load_aligner_from_mesh(mesh)

    def update_batch_list_labels(self):
        """Updates the list items to show matching status and progress."""
        for i in range(self.batch_list.count()):
            item = self.batch_list.item(i)
            if i >= len(self.stl_files): continue
            
            base_model = os.path.basename(self.stl_files[i])
            model_num = self._extract_number(base_model)
            
            matching_aligner = None
            if model_num:
                for af in self.aligner_files:
                    if self._extract_number(os.path.basename(af)) == model_num:
                        matching_aligner = os.path.basename(af)
                        break
            
            status = f"{base_model}"
            if matching_aligner:
                status += f" + 🦷 {matching_aligner}"
            else:
                status += " (No Aligner)"
                
            if i == 0:
                item.setText(f"{status} [MASTER]")
            else:
                item.setText(f"{status} [{i+1}/{len(self.stl_files)}]")

    def load_aligner_stl(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Aligner STL", "", "3D Files (*.stl)"
        )
        if file_path:
            self._load_aligner_with_replay(file_path)
            self.update_workflow_state(3)
            try:
                self.load_aligner_btn.setStyleSheet("")  # Clear highlight
            except AttributeError:
                pass
            self.update_workflow_state(3)
            self.update_workflow_state(3)


    def on_batch_item_clicked(self, item):
        """View the result associated with the clicked list item."""
        index = self.batch_list.row(item)
        
        # If the result for this item exists (Batch complete for this item)
        if index < len(self.processed_results) and self.processed_results[index]:
            self.current_view_index = index
            self._show_view_model()
            if index == 0:
                self.status_label.setText("VIEWING MASTER RESULT")
                self.tool_merge.setEnabled(True)
            return

        # Fallback: Load original
        if index < len(self.stl_files):
            file_path = self.stl_files[index]
            self.viewer.load_part(file_path)
            
            if index == 0:
                self.status_label.setText("EDITING MASTER MODEL")
                self.tool_flatten.setEnabled(True)
                self.tool_merge.setEnabled(True)
                self.tool_precise.setEnabled(True) 
                self.tool_face.setEnabled(True)
                if self.viewer.fixture:
                    try: self.viewer.vis.add_geometry(self.viewer.fixture)
                    except: pass
                    
                # AUTOMATICALLY LOAD MATCHING ALIGNER FOR PREVIEW
                model_num = self._extract_number(os.path.basename(file_path))
                if model_num:
                    for af in self.aligner_files:
                        if self._extract_number(os.path.basename(af)) == model_num:
                            self._load_aligner_with_replay(af)
                            break
            else:
                self.status_label.setText(f"PREVIEW ORIGINAL: {os.path.basename(file_path)}")
                self.part_lbl.setText(f"FILE {index + 1}/{len(self.stl_files)}")
                self.tool_flatten.setEnabled(False)
                self.tool_merge.setEnabled(False)
                self.tool_face.setEnabled(False)
                # Hide fixture and clear old aligner for other previews
                if self.viewer.fixture:
                    try: self.viewer.vis.remove_geometry(self.viewer.fixture)
                    except: pass
                if self.viewer.aligner:
                    try: self.viewer.vis.remove_geometry(self.viewer.aligner)
                    except: pass
                    self.viewer.aligner = None
                
                # Check if we should show the matching aligner for this preview too?
                # Usually yes, if it's been uploaded in batch.
                model_num = self._extract_number(os.path.basename(file_path))
                if model_num:
                    for af in self.aligner_files:
                        if self._extract_number(os.path.basename(af)) == model_num:
                            # Load it as is (no replay for non-master previews to show raw state)
                            self.viewer.load_aligner(af)
                            break

    def run_batch_processing(self):
        if not self.stl_files or len(self.stl_files) <= 1:
            QMessageBox.warning(self, "Batch Error", "Please add at least 2 STL files to the batch list before continuing.")
            return
            
        if not self.current_fixture_path:
            QMessageBox.warning(self, "Missing Fixture", "Please load a fixture from the UPLOAD menu before starting batch processing.")
            return

        # 1. READ REFERENCE FIXTURE
        fixture_path = self.current_fixture_path
        fixture_mesh = o3d.io.read_triangle_mesh(fixture_path)
        
        self.processed_results = []
        self.aligner_results = []
        
        # 2. ITERATE THROUGH ALL FILES (Starting from 0 to capture Master as well)
        for i in range(len(self.stl_files)):
            file_path = self.stl_files[i]
            self.status_label.setText(f"PROCESSING: {os.path.basename(file_path)} [{i+1}/{len(self.stl_files)}]")
            
            # Visual feedback on list
            item = self.batch_list.item(i)
            item.setBackground(Qt.GlobalColor.yellow)
            QApplication.processEvents()
            
            try:
                # A. Load model mesh
                mesh = o3d.io.read_triangle_mesh(file_path)
                mesh.compute_vertex_normals()
                
                # B. Find matching aligner
                model_num = self._extract_number(os.path.basename(file_path))
                aligner_mesh = None
                aligner_path = None
                
                if model_num:
                    for af in self.aligner_files:
                        if self._extract_number(os.path.basename(af)) == model_num:
                            aligner_path = af
                            aligner_mesh = o3d.io.read_triangle_mesh(af)
                            aligner_mesh.compute_vertex_normals()
                            break
                            
                # C. Setup Baselines for Replay
                replay_original_vertices = np.asarray(mesh.vertices).copy()
                replay_aligner_baseline = None
                if aligner_mesh:
                    replay_aligner_baseline = np.asarray(aligner_mesh.vertices).copy()
                
                # D. Replay History
                for op in self.operation_history:
                    if op['type'] == 'rotate':
                        mesh.rotate(op['matrix'], center=op['center'])
                        if aligner_mesh: aligner_mesh.rotate(op['matrix'], center=op['center'])
                    elif op['type'] == 'translate':
                        mesh.translate(op['vector'])
                        if aligner_mesh: aligner_mesh.translate(op['vector'])
                    elif op['type'] == 'slider_transform':
                        tx, ty, rz = op['tx'], op['ty'], op['rz']
                        Rz = np.array([[np.cos(rz), -np.sin(rz), 0], [np.sin(rz),  np.cos(rz), 0], [0, 0, 1]])
                        
                        # Apply to model
                        v = replay_original_vertices @ Rz.T
                        v[:, 0] += tx
                        v[:, 1] += ty
                        mesh.vertices = o3d.utility.Vector3dVector(v)
                        
                        # Apply to aligner
                        if aligner_mesh:
                            va = replay_aligner_baseline @ Rz.T
                            va[:, 0] += tx
                            va[:, 1] += ty
                            aligner_mesh.vertices = o3d.utility.Vector3dVector(va)
                            
                    elif op['type'] == 'flatten':
                        if 'total_R' in op:
                            mesh.rotate(op['total_R'], center=op['pivot'])
                            if aligner_mesh: aligner_mesh.rotate(op['total_R'], center=op['pivot'])
                        else:
                            # Fallback for old history
                            pivot = mesh.get_center()
                            flatten_stl_to_grid(mesh, op['normal'], op.get('face_center'), op.get('target_center'), pivot=pivot) 
                            if aligner_mesh:
                                 flatten_stl_to_grid(aligner_mesh, op['normal'], op.get('face_center'), op.get('target_center'), pivot=pivot)
                        
                        if 'translation' in op:
                            mesh.translate(op['translation'])
                            if aligner_mesh: aligner_mesh.translate(op['translation'])
                            
                        replay_original_vertices = np.asarray(mesh.vertices).copy()
                        if aligner_mesh: replay_aligner_baseline = np.asarray(aligner_mesh.vertices).copy()

                    elif op['type'] == 'unlock':
                        # Lock current state as new baseline
                        replay_original_vertices = np.asarray(mesh.vertices).copy()
                        if aligner_mesh:
                             replay_aligner_baseline = np.asarray(aligner_mesh.vertices).copy()
                    elif op['type'] == 'center_origin':
                        if 'translation' in op:
                            mesh.translate(op['translation'])
                            if aligner_mesh: aligner_mesh.translate(op['translation'])
                        else:
                            # Fallback
                            c = mesh.get_center()
                            target = op.get('target', np.array([0.0, 0.0, 0.0]))
                            mesh.translate(target - c)
                            if aligner_mesh: aligner_mesh.translate(target - c)
                        replay_original_vertices = np.asarray(mesh.vertices).copy()
                        if aligner_mesh: replay_aligner_baseline = np.asarray(aligner_mesh.vertices).copy()
                    elif op['type'] == 'icp':
                        mesh.transform(op['matrix'])
                        if aligner_mesh:
                             aligner_mesh.transform(op['matrix'])
                    elif op['type'] == 'merge':
                        # Combine model with fixture
                        merged = o3d.geometry.TriangleMesh()
                        merged.vertices = o3d.utility.Vector3dVector(
                            np.vstack((np.asarray(mesh.vertices), np.asarray(fixture_mesh.vertices)))
                        )
                        p_tri = np.asarray(mesh.triangles)
                        f_tri = np.asarray(fixture_mesh.triangles) + len(mesh.vertices)
                        merged.triangles = o3d.utility.Vector3iVector(np.vstack((p_tri, f_tri)))
                        mesh = merged
                        mesh.compute_vertex_normals()
                
                # E. Save Results
                # Save Model (Merged if merge was in history)
                output_name = os.path.basename(file_path).lower().replace(".stl", "_batch_aligned.stl")
                output_path = os.path.join(os.path.dirname(file_path), output_name)
                o3d.io.write_triangle_mesh(output_path, mesh)
                
                # Save Aligner if exists (Separate file)
                if aligner_mesh:
                    a_name = os.path.basename(aligner_path).lower().replace(".stl", "_batch_aligned.stl")
                    aligner_out = os.path.join(os.path.dirname(aligner_path), a_name)
                    o3d.io.write_triangle_mesh(aligner_out, aligner_mesh)
                    
                    while len(self.aligner_results) <= i:
                        self.aligner_results.append(None)
                    self.aligner_results[i] = aligner_out
                
                while len(self.processed_results) <= i:
                    self.processed_results.append(None)
                self.processed_results[i] = output_path
                
                item.setText(f"{os.path.basename(file_path)} {'+ 🦷' if aligner_mesh else ''} [✓]")
                item.setBackground(Qt.GlobalColor.transparent)
                
            except Exception as e:
                item.setText(f"{os.path.basename(file_path)} [ERROR]")
                item.setBackground(Qt.GlobalColor.red)
            QApplication.processEvents()

        self.status_label.setText("BATCH PROCESSING COMPLETE")
        self.update_workflow_state(9)
        self.update_workflow_state(9)
        self.current_view_index = 0
        self.show_view_model_controls(True)
        self.show_view_model_controls(True)

    def show_view_model_controls(self, visible):
        """No longer uses nav_title/prev_btn widgets. Logic handled by batch list."""
        pass

    def view_model_clicked(self):
        # This button is now redundant but we can keep it to "jump" to the first processed result
        if not self.processed_results:
            self.status_label.setText("NO PROCESSED MODELS TO VIEW")
            return
        self.current_view_index = 0
        self._show_view_model()
        self.batch_list.setCurrentRow(0)

    def view_prev_model(self):
        if not self.processed_results: return
        self.current_view_index = (self.current_view_index - 1) % len(self.processed_results)
        self._show_view_model()

    def view_next_model(self):
        if not self.processed_results: return
        self.current_view_index = (self.current_view_index + 1) % len(self.processed_results)
        self._show_view_model()

    def _show_view_model(self):
        """Loads the processed model and aligner for the current index into the viewer."""
        if self.current_view_index >= len(self.processed_results):
            return

        model_path = self.processed_results[self.current_view_index]
        if not model_path or not os.path.exists(model_path):
            self.status_label.setText(f"FILE NOT FOUND: {os.path.basename(model_path) if model_path else 'Unknown'}")
            return

        # 1. Load the Model
        self.viewer.load_part(model_path)
        self.part_lbl.setText(f"PROCESSED {self.current_view_index + 1}/{len(self.stl_files)}")
        self._set_sliders_enabled(False)
        self.tool_merge.setEnabled(False)
        self.tool_flatten.setEnabled(False)
        self.tool_face.setEnabled(False)

        # 2. Load the Aligner if it exists
        if self.current_view_index < len(self.aligner_results):
            aligner_path = self.aligner_results[self.current_view_index]
            if aligner_path and os.path.exists(aligner_path):
                self.viewer.load_aligner(aligner_path)
            else:
                if self.viewer.aligner:
                    try: self.viewer.vis.remove_geometry(self.viewer.aligner)
                    except: pass
                    self.viewer.aligner = None
        else:
            if self.viewer.aligner:
                try: self.viewer.vis.remove_geometry(self.viewer.aligner)
                except: pass
                self.viewer.aligner = None

        self.status_label.setText(f"VIEWING RESULT: {os.path.basename(model_path)}")
        
        # Sync the list selection without triggering recursive calls
        self.batch_list.blockSignals(True)
        self.batch_list.setCurrentRow(self.current_view_index)
        self.batch_list.blockSignals(False)
        
    def store_transform_values(self):
        self.saved_transform["tx"] = self.tx.value() / 10.0
        self.saved_transform["ty"] = self.ty.value() / 10.0
        self.saved_transform["tz"] = 0.0

        self.saved_transform["rx"] = 0.0
        self.saved_transform["ry"] = 0.0
        self.saved_transform["rz"] = self.rot_z.value()

    def save_model_result(self):
        """Saves only the STL Model (merged with fixture if applicable)."""
        if self.batch_mode:
            if not getattr(self, "processed_results", None):
                QMessageBox.warning(self, "Save Error", "No processed batch models to save. Run batch processing first.")
                return
            
            save_dir, _ = QFileDialog.getSaveFileName(self, "Save Batch STL Models to Folder", "Batch_Models_STL", "Folder Name (*)")
            if save_dir:
                try:
                    import shutil
                    os.makedirs(save_dir, exist_ok=True)
                    for path in self.processed_results:
                        if path and os.path.exists(path):
                            shutil.copy(path, save_dir)
                    self.status_label.setText(f"BATCH MODELS SAVED TO: {os.path.basename(save_dir)}")
                except Exception as e:
                    QMessageBox.warning(self, "Save Error", f"Error saving batch models: {e}")
        else:
            if not self.viewer.part:
                QMessageBox.warning(self, "Save Error", "No model loaded to save.")
                return

            default_name = "aligned_model.stl"
            save_path, _ = QFileDialog.getSaveFileName(self, "Save Model STL", default_name, "STL Files (*.stl)")
            if save_path:
                o3d.io.write_triangle_mesh(save_path, self.viewer.part)
                self.status_label.setText(f"MODEL SAVED: {os.path.basename(save_path)}")

    def save_aligner_result(self):
        """Saves only the Clear Aligner mesh as it appears in the current view."""
        if self.batch_mode:
            if not getattr(self, "aligner_results", None):
                QMessageBox.warning(self, "Save Error", "No processed batch aligners to save. Run batch processing first.")
                return
                
            save_dir, _ = QFileDialog.getSaveFileName(self, "Save Batch Aligners to Folder", "Batch_Aligners_STL", "Folder Name (*)")
            if save_dir:
                try:
                    import shutil
                    os.makedirs(save_dir, exist_ok=True)
                    for path in self.aligner_results:
                        if path and os.path.exists(path):
                            shutil.copy(path, save_dir)
                    self.status_label.setText(f"BATCH ALIGNERS SAVED TO: {os.path.basename(save_dir)}")
                except Exception as e:
                    QMessageBox.warning(self, "Save Error", f"Error saving batch aligners: {e}")
        else:
            if not self.viewer.aligner:
                QMessageBox.warning(self, "Save Error", "No aligner loaded to save.")
                return

            default_name = "aligned_aligner.stl"
            save_path, _ = QFileDialog.getSaveFileName(self, "Save Aligner STL", default_name, "STL Files (*.stl)")
            if save_path:
                o3d.io.write_triangle_mesh(save_path, self.viewer.aligner)
                self.status_label.setText(f"ALIGNER SAVED: {os.path.basename(save_path)}")

    def toggle_transform_mode(self, checked=None):
        if checked is not None:
             self.viewer.transform_mode = checked
        else:
             # Default to true if we don't have a button to check
             self.viewer.transform_mode = not self.viewer.transform_mode
             
        if self.viewer.transform_mode:
            self.status_label.setText("TRANSFORM MODE: MOUSE MOVES PART")
            self.viewer.last_mouse_pos = None
        else:
            self.status_label.setText("VIEW MODE: MOUSE MOVES CAMERA")

    def toggle_select_mode(self):
        if not self.viewer.part:
            QMessageBox.warning(self, "No Model", "Please load an STL model first.")
            self.tool_face.setChecked(False)
            return

        is_active = self.viewer.toggle_selection_mode()
        self.tool_face.setChecked(is_active)
        
        if is_active:
            self.status_label.setText("FACE SELECTION ACTIVE: Double-click a tooth face")
            self.update_workflow_state(5)
            self.update_workflow_state(5)
        else:
            self.status_label.setText("SELECT MODE OFF")

    def update_part_transform(self):
        if self.internal_update or not self.viewer.part:
            return

        # 🚀 RESTORED GOLDEN ABSOLUTE MATH (No Lock)
        mesh = self.viewer.part
        vertices = self.viewer.original_vertices.copy()

        rz = np.deg2rad(self.rot_z.value())

        Rz = np.array([
            [np.cos(rz), -np.sin(rz), 0],
            [np.sin(rz),  np.cos(rz), 0],
            [0, 0, 1]
        ])

        # Exact user math:
        vertices = vertices @ Rz.T

        tx = self.tx.value() / 10.0
        ty = self.ty.value() / 10.0
        tz = 0.0

        vertices[:, 0] += tx
        vertices[:, 1] += ty
        vertices[:, 2] += tz
        
        self.record_operation({'type': 'slider_transform', 'tx': tx, 'ty': ty, 'rz': rz})

        mesh.vertices = o3d.utility.Vector3dVector(vertices)
        mesh.compute_vertex_normals()
        self.viewer.vis.update_geometry(mesh)
        
        if self.viewer.aligner:
            a_vertices = self.viewer.original_aligner_vertices.copy()
            a_vertices = a_vertices @ Rz.T
            a_vertices[:, 0] += tx
            a_vertices[:, 1] += ty
            a_vertices[:, 2] += tz
            self.viewer.aligner.vertices = o3d.utility.Vector3dVector(a_vertices)
            self.viewer.aligner.compute_vertex_normals()
            self.viewer.vis.update_geometry(self.viewer.aligner)

        self.viewer.vis.poll_events()
        self.viewer.vis.update_renderer()
        self.viewer.update_part_center_marker()
        
        self.status_label.setText(f"V1.0.9 | ABSOLUTE ROTATION: {self.rot_z.value()}°")
        center = mesh.get_center()
        self.pos_readout.setText(f"X: {center[0]:.1f}, Y: {center[1]:.1f}, Z: {center[2]:.1f}")


    def flatten_to_grid(self):
        if not self.viewer.part:
            return
        
        self.save_undo_state() # SAVE BEFORE CHANGE

        # LIVE UPDATE OF NORMAL FROM CURRENT ROTATED STATE
        if self.viewer.selected_face_id is not None:
            tri = np.asarray(self.viewer.part.triangles)[self.viewer.selected_face_id]
            v_all = np.asarray(self.viewer.part.vertices)
            v1, v2, v3 = v_all[tri[0]], v_all[tri[1]], v_all[tri[2]]
            new_n = np.cross(v2 - v1, v3 - v1)
            self.viewer.selected_face_normal = new_n / (np.linalg.norm(new_n) + 1e-9)
            face_center = v_all[tri].mean(axis=0)
        elif self.viewer.selected_face_normal is None:
            self.status_label.setText("SELECT FACE FIRST!")
            return
        else:
            face_center = self.viewer.part.get_center()

        mesh = self.viewer.part
        
        # Reverted to Centroid pivot to keep centroid fixed in its current XY position
        pivot = mesh.get_center()

        # STEP 1 : Animated Flatten (Rotation Only)
        normal = self.viewer.selected_face_normal
        target = np.array([0, 0, -1])
        axis = np.cross(normal, target)
        axis_len = np.linalg.norm(axis)

        if axis_len > 1e-6:
            axis = axis / axis_len
            total_angle = np.arccos(np.clip(np.dot(normal, target), -1, 1))
            
            steps = 20
            step_angle = total_angle / steps
            
            total_R = np.eye(3)
            self.status_label.setText("ANIMATING FLATTEN...")
            for _ in range(steps):
                R = o3d.geometry.get_rotation_matrix_from_axis_angle(axis * step_angle)
                mesh.rotate(R, center=pivot)
                total_R = R @ total_R
                
                if self.viewer.aligner:
                    self.viewer.aligner.rotate(R, center=pivot)
                
                self.viewer.vis.update_geometry(mesh)
                if self.viewer.aligner:
                    self.viewer.vis.update_geometry(self.viewer.aligner)
                
                self.viewer.vis.poll_events()
                self.viewer.vis.update_renderer()
                QApplication.processEvents()
                time.sleep(0.005)
                
        elif np.dot(normal, target) < -0.99:
             total_R = o3d.geometry.get_rotation_matrix_from_axis_angle(np.array([1, 0, 0]) * np.pi)
             mesh.rotate(total_R, center=pivot)
             if self.viewer.aligner:
                 self.viewer.aligner.rotate(total_R, center=pivot)
        else:
             total_R = np.eye(3)
        
        # STEP 2 : Seat the base of the model on the grid surface (Z=0)
        # (Enforcing alignment as requested by the user - Exactly on Grid Surface)
        target_center = None # Set to None so record/replay uses Z=0
        bbox = mesh.get_axis_aligned_bounding_box()
        current_min_z = bbox.get_min_bound()[2]
        
        # We want the base (min_z) to be exactly at Z=0
        translation_z = -current_min_z
        translation = np.array([0.0, 0.0, translation_z])
        
        mesh.translate(translation)
        
        if self.viewer.aligner:
            self.viewer.aligner.translate(translation)
        
        self.viewer.update_part_center_marker()
        mesh.compute_vertex_normals()
        self.viewer.vis.update_geometry(mesh)
        if self.viewer.aligner:
            self.viewer.vis.update_geometry(self.viewer.aligner)
        
        # Update baseline
        self.viewer.original_vertices = np.asarray(mesh.vertices).copy()
        if self.viewer.aligner:
            self.viewer.original_aligner_vertices = np.asarray(self.viewer.aligner.vertices).copy()

        # reset sliders
        self.internal_update = True
        self.tx.setValue(0); self.ty.setValue(0); self.rot_z.setValue(0)
        self.internal_update = False

        self.is_flattened = True
        self.last_slider_val = 0
        self.tool_precise.setEnabled(True)

        self.status_label.setText("MODEL FLATTENED (FIXED POSITION)")
        self.toggle_transform_mode(False) 
        self.toggle_select_mode() # Turn off face selection if on
        
        # FORCE RE-ADD TO VIEWER TO UNLOCK RENDERING
        self.viewer.vis.remove_geometry(mesh)
        self.viewer.vis.add_geometry(mesh)
        
        self.update_workflow_state(6)
        self.record_operation({
            'type': 'flatten', 
            'normal': self.viewer.selected_face_normal,
            'face_center': face_center,
            'target_center': target_center,
            'pivot': pivot,
            'total_R': total_R,
            'translation': translation,
            'fixed_xy': True 
        })

    def flatten_unlock(self):
        if not self.viewer.part:
            return

        self.is_flattened = False

        # Capture current mesh state as new baseline
        self.viewer.original_vertices = np.asarray(
            self.viewer.part.vertices
        ).copy()

        self._set_sliders_enabled(True)

        self.tool_precise.setChecked(False)

        self.status_label.setText("FLATTEN UNLOCKED")
        
        if self.viewer.aligner:
            self.viewer.original_aligner_vertices = np.asarray(
                self.viewer.aligner.vertices
            ).copy()

        self.record_operation({'type': 'unlock'})

    def align_model_center_to_global_origin(self):
        if not self.viewer.part:
            self.status_label.setText("LOAD PART FIRST")
            return
        
        self.save_undo_state() # SAVE BEFORE CHANGE

        mesh = self.viewer.part
        model_center = mesh.get_center()
        target_center = np.array([0.0, 0.0, 0.0])
        
        # 🚀 GLOBAL ORIGIN ALIGNMENT (XY Centered, Seated on Z=0)
        translation = target_center - model_center
        mesh.translate(translation)
        
        # Adjust Z to ensure model is ABOVE grid (min_z = 0)
        bbox = mesh.get_axis_aligned_bounding_box()
        current_min_z = bbox.get_min_bound()[2]
        z_adj = -current_min_z
        mesh.translate([0, 0, z_adj])
        
        translation[2] += z_adj
        self.status_label.setText("CENTERED & SEATED ON GRID")
        self.update_workflow_state(4)
        self.update_workflow_state(4)
            
        # 🔥 MOVE THE BLUE DOT TO MATCH THE NEW TARGET IF NECESSARY
        self.viewer.update_origin_marker_position(target_center)
        
        self.viewer.vis.update_geometry(mesh)
        
        # Sync original vertices (baseline)
        self.viewer.original_vertices = np.asarray(mesh.vertices).copy()

        # Handle Aligner (Shift by same translation to stay synced)
        if self.viewer.aligner:
            self.viewer.aligner.translate(translation)
            self.viewer.vis.update_geometry(self.viewer.aligner)
            self.viewer.original_aligner_vertices = np.asarray(self.viewer.aligner.vertices).copy()

        self.viewer.update_part_center_marker()
        self.fixed_centroid = target_center

        # Record for batch (Store exact translation for perfect replay)
        self.record_operation({'type': 'center_origin', 'target': target_center, 'translation': translation})
        
        # Reset sliders
        self.internal_update = True
        self.tx.setValue(0); self.ty.setValue(0); self.rot_z.setValue(0)
        self.internal_update = False

    def icp_align_to_fixture(self):
        if not self.viewer.part or not self.viewer.fixture:
            return

        part = self.viewer.part
        fixture = self.viewer.fixture

        # --- OPTIMIZED FOR DENTAL: Use only bottom region of STL ---
        vertices = np.asarray(part.vertices)
        min_z = vertices[:, 2].min()
        bottom_mask = vertices[:, 2] < (min_z + 2.0)
        
        if np.sum(bottom_mask) > 100:
            source_pcd = o3d.geometry.PointCloud()
            source_pcd.points = o3d.utility.Vector3dVector(vertices[bottom_mask])
        else:
            source_pcd = part.sample_points_uniformly(number_of_points=5000)

        # Target is the fixture
        target_pcd = fixture.sample_points_uniformly(number_of_points=10000)

        # Estimating normals for ICP
        source_pcd.estimate_normals()
        target_pcd.estimate_normals()

        # ICP Parameters (Point-to-Plane for best seating)
        threshold = 2.0
        trans_init = np.eye(4)

        reg = o3d.pipelines.registration.registration_icp(
            source_pcd,
            target_pcd,
            threshold,
            trans_init,
            o3d.pipelines.registration.TransformationEstimationPointToPlane()
        )

        # Apply transformation to the main mesh
        trans = reg.transformation
        part.transform(trans)
        self.viewer.vis.update_geometry(part)
        self.viewer.original_vertices = np.asarray(part.vertices).copy()

        if self.viewer.aligner:
            self.viewer.aligner.transform(trans)
            self.viewer.vis.update_geometry(self.viewer.aligner)
            self.viewer.original_aligner_vertices = np.asarray(self.viewer.aligner.vertices).copy()
        
        self.record_operation({'type': 'icp', 'matrix': trans})

    def _setup_upload_menu(self):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        
        menu = QMenu(self)
        menu.setObjectName("professional-menu")
        
        # Fixture Sub-menu
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
        self.act_batch_aligner.triggered.connect(self.add_aligners_to_batch)
        
        self.btn_upload.setMenu(menu)

    def _setup_download_menu(self):
        from PyQt6.QtWidgets import QMenu
        
        menu = QMenu(self)
        menu.setObjectName("professional-menu")
        
        self.act_save_stl = menu.addAction("SAVE STL")
        self.act_save_stl.triggered.connect(self.save_model_result)
        self.act_save_aligner = menu.addAction("SAVE CLEAR ALIGNER")
        self.act_save_aligner.triggered.connect(self.save_aligner_result)
        
        self.btn_download.setMenu(menu)

    def toggle_precise_controls(self, checked):
        if checked:
            self.precision_container.show()
            self.precision_msg.hide()
            self.update_workflow_state(7)
            self.update_workflow_state(7)
            self.update_workflow_state(7)
            self.status_label.setText("PRECISE MODE: ROTATE Z ENABLED")
        else:
            self.precision_container.hide()
            self.precision_msg.show()
            self.status_label.setText("SYSTEM READY")


    def save_undo_state(self):
        """Captures the current mesh states and history length for undo."""
        if not self.viewer.part:
            return
            
        state = {
            'part_vertices': np.asarray(self.viewer.part.vertices).copy(),
            'aligner_vertices': None,
            'op_history_len': len(self.operation_history)
        }
        
        if self.viewer.aligner:
            state['aligner_vertices'] = np.asarray(self.viewer.aligner.vertices).copy()
            
        self.undo_stack.append(state)
        # Limit stack to 20 steps to save memory
        if len(self.undo_stack) > 20:
            self.undo_stack.pop(0)

    def perform_undo(self):
        if not self.undo_stack:
            self.status_label.setText("NOTHING TO UNDO")
            return
            
        state = self.undo_stack.pop()
        
        # Restore Part
        self.viewer.part.vertices = o3d.utility.Vector3dVector(state['part_vertices'])
        self.viewer.part.compute_vertex_normals()
        self.viewer.vis.update_geometry(self.viewer.part)
        self.viewer.original_vertices = state['part_vertices'].copy()
        
        # Restore Aligner
        if state['aligner_vertices'] is not None and self.viewer.aligner:
            self.viewer.aligner.vertices = o3d.utility.Vector3dVector(state['aligner_vertices'])
            self.viewer.aligner.compute_vertex_normals()
            self.viewer.vis.update_geometry(self.viewer.aligner)
            self.viewer.original_aligner_vertices = state['aligner_vertices'].copy()
            
        # Restore History
        self.operation_history = self.operation_history[:state['op_history_len']]
        
        self.viewer.update_part_center_marker()
        self.status_label.setText("UNDO PERFORMED")

    def show_guide_window(self):
        QMessageBox.information(self, "Guide", "Guide content will be provided later.")

    def merge_fixture_model(self):
        if not self.viewer.part or not self.viewer.fixture:
            self.status_label.setText("LOAD FIXTURE + PART FIRST")
            return

        self.save_undo_state()
        
        # Record the operation
        if not any(op.get('type') == 'merge' for op in self.operation_history):
            self.record_operation({'type': 'merge'})

        part = self.viewer.part
        fixture = self.viewer.fixture

        # Create merged mesh
        merged_mesh = o3d.geometry.TriangleMesh()

        # Combine vertices and triangles
        merged_mesh.vertices = o3d.utility.Vector3dVector(
            np.vstack((
                np.asarray(part.vertices),
                np.asarray(fixture.vertices)
            ))
        )

        part_tri = np.asarray(part.triangles)
        fixture_tri = np.asarray(fixture.triangles) + len(part.vertices)

        merged_mesh.triangles = o3d.utility.Vector3iVector(
            np.vstack((part_tri, fixture_tri))
        )

        merged_mesh.compute_vertex_normals()

        # Update Visualizer
        # Remove PART (keep aligner separate)
        if self.viewer.part:
            try: self.viewer.vis.remove_geometry(self.viewer.part)
            except: pass
        # Fixture removal if it was added separately
        if self.viewer.fixture:
            try: self.viewer.vis.remove_geometry(self.viewer.fixture)
            except: pass
            
        # Apply distinct colors using vertex colors so they persist in merged state
        colors_part = np.full((len(part.vertices), 3), [0.72, 0.58, 0.45]) # Gold
        colors_fixture = np.full((len(fixture.vertices), 3), [1.0, 1.0, 1.0]) # White
        merged_mesh.vertex_colors = o3d.utility.Vector3dVector(np.vstack((colors_part, colors_fixture)))

        self.viewer.part = merged_mesh
        self.viewer.vis.add_geometry(merged_mesh)
        self.viewer.vis.update_renderer()

        self.status_label.setText("MODEL + FIXTURE MERGED")
        self.update_workflow_state(8)

    def _set_sliders_enabled(self, enabled):
        self.tx.setEnabled(enabled)
        self.ty.setEnabled(enabled)
        self.rot_z.setEnabled(enabled)
        if enabled:
            self.slider_overlay.show()
            self.update_workflow_state(7)
        else:
            self.slider_overlay.hide()

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f8f9fc;
            }
            #top-bar {
                background-color: #ffffff;
                border-bottom: 1px solid #edf2f7;
            }
            #left-bar {
                background-color: #ffffff;
                border-right: 1px solid #edf2f7;
            }
            #right-bar {
                background-color: #ffffff;
                border-left: 1px solid #edf2f7;
            }
            #bottom-bar {
                background-color: #f8f9fc;
                border-top: 1px solid #edf2f7;
            }
            
            /* PROFESSIONAL NAV BUTTONS */
            QPushButton#nav-btn {
                background: transparent;
                border: none;
                color: #4a5568;
                font-family: 'Segoe UI';
                font-weight: 600;
                font-size: 11px;
                letter-spacing: 0.5px;
                padding: 10px 15px;
            }
            QPushButton#nav-btn:hover {
                color: #0066ff;
            }

            /* TOOL BUTTONS */
            QToolButton#tool-btn {
                background: transparent;
                border: none;
                color: #718096;
                font-family: 'Segoe UI';
                font-size: 9px;
                font-weight: bold;
                padding: 12px 5px;
                min-width: 80px;
            }
            QToolButton#tool-btn:hover {
                background-color: #f7fafc;
                color: #0066ff;
            }
            QToolButton#tool-btn:checked {
                color: #0066ff;
                background-color: #ebf4ff;
                border-right: 3px solid #0066ff;
            }

            /* BATCH LIST */
            QListWidget#batch-list {
                background-color: #f7fafc;
                border: 1px solid #edf2f7;
                border-radius: 6px;
                color: #4a5568;
                font-size: 11px;
            }
            QListWidget#batch-list::item {
                padding: 12px;
                border-bottom: 1px solid #edf2f7;
            }
            QListWidget#batch-list::item:selected {
                background-color: #ebf4ff;
                color: #0066ff;
            }

            /* ACTION BUTTONS */
            QPushButton#action-btn {
                background-color: #0066ff;
                color: white;
                border-radius: 6px;
                font-weight: bold;
                padding: 12px;
                font-size: 11px;
            }
            QPushButton#action-btn:hover { background-color: #0052cc; }

            /* MODE BUTTONS */
            QPushButton#mode-btn {
                background-color: #ffffff;
                border: 1px solid #edf2f7;
                color: #4a5568;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton#mode-btn:hover { background-color: #f7fafc; }

            /* SIDEBAR TITLES */
            #sidebar-title {
                color: #2d3748;
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 1px;
                margin-bottom: 10px;
            }

            /* STATUS AREA */
            QLabel { color: #4a5568; font-family: 'Segoe UI'; }
            #bottom-status { font-weight: 600; font-size: 11px; }
            
            #status-group {
                background-color: #ffffff;
                border: 1px solid #edf2f7;
                border-radius: 8px;
                padding: 15px;
            }
            /* SLIDER OVERLAY (PRECISION BOX) */
            #slider-overlay {
                background-color: #ffffff;
                border: 2px solid #0066ff;
                border-radius: 12px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            }
            /* FLOATING TOOLBAR */
            #floating-toolbar {
                background-color: #ffffff;
                border: 1px solid #edf2f7;
                border-radius: 12px;
                margin: 20px;
            }
            #float-btn {
                background: transparent;
                border: none;
                color: #718096;
                font-family: 'Segoe UI';
                font-size: 10px;
                padding: 10px;
                border-radius: 8px;
            }
            #float-btn:hover { background-color: #f7fafc; color: #0066ff; }
            #float-btn:checked { background-color: #ebf4ff; color: #0066ff; border: 1px solid #0066ff; }

            /* BOTTOM STATS BOX */
            #bottom-stats-box { margin-left: 20px; }
            #stat-item-label { color: #a0aec0; font-size: 10px; margin-right: 5px; }
            #stat-item-val { color: #2d3748; font-weight: bold; font-size: 11px; margin-right: 20px; }

            /* MODE GROUP */
            #mode-group-box {
                background-color: #f7fafc;
                border-radius: 8px;
                border: 1px solid #edf2f7;
                margin-right: 20px;
            }
            QPushButton#mode-btn {
                background-color: transparent;
                border: none;
                color: #4a5568;
                padding: 8px 16px;
                font-size: 10px;
                font-weight: 600;
                border-radius: 6px;
            }
            QPushButton#mode-btn:checked {
                background-color: #ffffff;
                color: #0066ff;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            }

            /* SWITCH STYLE (MOCKUP TOGGLES) */
            QCheckBox::indicator { width: 34px; height: 18px; }
            QCheckBox::indicator:unchecked { image: url(none); border: 1px solid #cbd5e0; border-radius: 9px; background: #e2e8f0; }
            QCheckBox::indicator:checked { image: url(none); border: 1px solid #0066ff; border-radius: 9px; background: #0066ff; }
        """)

    def _create_float_btn(self, icon, text, callback):
        btn = QPushButton(icon)
        btn.setObjectName("float-btn")
        btn.setCheckable(True)
        btn.clicked.connect(callback)
        btn.setToolTip(text)
        return btn

    def _create_stat_item(self, layout, label):
        lbl = QLabel(label)
        lbl.setObjectName("stat-item-label")
        val = QLabel("0")
        val.setObjectName("stat-item-val")
        layout.addWidget(lbl)
        layout.addWidget(val)
        return val

    def _create_sidebar_toggle(self, layout, text, initial_state, callback):
        row = QFrame()
        row.setObjectName("toggle-row")
        h_lay = QHBoxLayout(row)
        h_lay.setContentsMargins(0, 2, 0, 2)
        
        lbl = QLabel(text)
        lbl.setObjectName("toggle-label")
        h_lay.addWidget(lbl)
        h_lay.addStretch()
        
        from PyQt6.QtWidgets import QCheckBox
        chk = QCheckBox()
        chk.setChecked(initial_state)
        chk.stateChanged.connect(callback)
        h_lay.addWidget(chk)
        
        layout.addWidget(row)
        return chk

    def toggle_grid(self, state):
        visible = state == 2
        if self.viewer.grid:
            if visible: self.viewer.vis.add_geometry(self.viewer.grid)
            else: self.viewer.vis.remove_geometry(self.viewer.grid)
            
    def toggle_axis(self, state):
        visible = state == 2
        if self.viewer.origin_axes:
            if visible: self.viewer.vis.add_geometry(self.viewer.origin_axes)
            else: self.viewer.vis.remove_geometry(self.viewer.origin_axes)

    def set_render_mode(self, mode):
        opt = self.viewer.vis.get_render_option()
        if mode == "shaded":
            opt.mesh_show_wireframe = False
            opt.light_on = True
        elif mode == "wireframe":
            opt.mesh_show_wireframe = True
            opt.light_on = True
        elif mode == "solid":
            opt.mesh_show_wireframe = False
            opt.light_on = False # Flat lighting
        self.status_label.setText(f"RENDER MODE: {mode.upper()}")

if __name__ == "__main__":
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
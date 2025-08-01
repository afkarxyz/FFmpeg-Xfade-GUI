import sys
import os
import subprocess
import json

if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes
    
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    
    SW_HIDE = 0
    SW_MINIMIZE = 6
    
    hWnd = kernel32.GetConsoleWindow()
    if hWnd:
        user32.ShowWindow(hWnd, SW_HIDE)
        user32.ShowWindow(hWnd, SW_MINIMIZE)
    
    import subprocess
    _original_popen = subprocess.Popen
    def _patched_popen(*args, **kwargs):
        if 'creationflags' not in kwargs:
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return _original_popen(*args, **kwargs)
    subprocess.Popen = _patched_popen

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLineEdit, QLabel, QFileDialog, 
                             QListWidget, QMessageBox, QDoubleSpinBox, QTextEdit,
                             QComboBox, QGridLayout, QScrollArea, QTabWidget, QAbstractItemView,
                             QMenu)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QSettings
from PyQt6.QtGui import QTextCursor, QMovie, QIcon, QDragEnterEvent, QDropEvent, QPainter, QPixmap, QAction
from PyQt6.QtWidgets import QGraphicsColorizeEffect

def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    
    return os.path.join(base_path, relative_path)

def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        ffmpeg_path = get_resource_path('ffmpeg.exe')
        ffprobe_path = get_resource_path('ffprobe.exe')
    else:
        ffmpeg_path = 'ffmpeg.exe'
        ffprobe_path = 'ffprobe.exe'
    
    return ffmpeg_path, ffprobe_path

def run_subprocess(cmd, **kwargs):
    default_kwargs = {
        'creationflags': subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    }
    default_kwargs.update(kwargs)
    return subprocess.Popen(cmd, **default_kwargs)

def run_subprocess_simple(cmd, **kwargs):
    default_kwargs = {
        'creationflags': subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    }
    default_kwargs.update(kwargs)
    return subprocess.run(cmd, **default_kwargs)

FFMPEG_PATH, FFPROBE_PATH = get_ffmpeg_path()


class FFmpegWorker(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, segments, output_file, transition_duration, transition_type, ffmpeg_path, use_gpu=False):
        super().__init__()
        self.segments = segments
        self.output_file = output_file
        self.transition_duration = transition_duration
        self.transition_type = transition_type
        self.ffmpeg_path = ffmpeg_path
        self.use_gpu = use_gpu
        self.is_running = True
        self.process = None

    def run(self):
        try:
            if self.is_running:
                self.process_videos()
                if self.is_running:
                    self.finished.emit(True, "Video processing completed successfully!")
                else:
                    self.finished.emit(False, "Processing stopped by user")
        except Exception as e:
            self.finished.emit(False, str(e))
    
    def stop(self):
        self.is_running = False
        if self.process:
            self.process.terminate()

    def get_video_info(self, file_path):
        result = run_subprocess_simple([FFPROBE_PATH, '-v', 'quiet', '-print_format', 'json', 
                                       '-show_format', '-show_streams', file_path], 
                                      capture_output=True, text=True)
        return json.loads(result.stdout)

    def process_videos(self):
        file_info = [self.get_video_info(f) for f in self.segments]
        file_lengths = [float(info['format']['duration']) for info in file_info]
        has_audio = [any(stream['codec_type'] == 'audio' for stream in info['streams']) for info in file_info]

        width = int(file_info[0]['streams'][0]['width'])
        height = int(file_info[0]['streams'][0]['height'])

        files_input = [['-i', f] for f in self.segments]

        video_transitions = ""
        audio_transitions = ""
        last_transition_output = "0v"
        last_audio_output = "0:a" if has_audio[0] else None
        video_length = 0
        normalizer = ""
        scaler_default = f",scale=w={width}:h={height}:force_original_aspect_ratio=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"

        for i in range(len(self.segments)):
            scaler = scaler_default if i > 0 else ""
            normalizer += f"[{i}:v]settb=AVTB,setsar=sar=1,fps=30{scaler}[{i}v];"

            if i == 0:
                continue

            video_length += file_lengths[i - 1] - self.transition_duration / 2
            next_transition_output = f"v{i-1}{i}"
            video_transitions += f"[{last_transition_output}][{i}v]xfade=transition={self.transition_type}:duration={self.transition_duration}:offset={video_length - self.transition_duration / 2:.3f}[{next_transition_output}];"
            last_transition_output = next_transition_output

            if has_audio[i-1] and has_audio[i]:
                next_audio_output = f"a{i-1}{i}"
                audio_transitions += f"[{last_audio_output}][{i}:a]acrossfade=d={self.transition_duration}[{next_audio_output}];"
                last_audio_output = next_audio_output
            elif has_audio[i]:
                last_audio_output = f"{i}:a"

        video_transitions += f"[{last_transition_output}]format=pix_fmts=yuv420p[final];"

        ffmpeg_args = [FFMPEG_PATH,
                       *sum(files_input, []),
                       '-filter_complex', normalizer + video_transitions + audio_transitions[:-1],
                       '-map', '[final]']

        if last_audio_output:
            ffmpeg_args.extend(['-map', f"[{last_audio_output}]"])
        else:
            ffmpeg_args.extend(['-an'])

        if self.use_gpu:
            if self.gpu_type == 'NVIDIA':
                ffmpeg_args.extend(['-c:v', 'h264_nvenc'])
            elif self.gpu_type == 'AMD' or self.gpu_type == 'RADEON':
                ffmpeg_args.extend(['-c:v', 'h264_amf'])
            elif self.gpu_type == 'Intel':
                ffmpeg_args.extend(['-c:v', 'h264_qsv'])
            else:
                self.progress.emit("Unknown GPU type. Falling back to CPU encoding.")

        ffmpeg_args.extend(['-y', self.output_file])

        self.process = run_subprocess(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                     universal_newlines=True)
        
        while self.is_running:
            line = self.process.stdout.readline()
            if not line:
                break
            self.progress.emit(line.strip())
        
        if self.is_running:
            self.process.wait()
            if self.process.returncode != 0:
                raise Exception("FFmpeg process failed")
        else:
            self.process.terminate()

class DragDropListWidget(QListWidget):
    files_dropped = pyqtSignal(list)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.original_style = self.styleSheet()
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v', 
                               '.mpg', '.mpeg', '.m2v', '.m2ts', '.mts', '.ts', '.vob', '.3gp',
                               '.3g2', '.f4v', '.asf', '.rmvb', '.rm', '.ogv', '.mxf', '.dv',
                               '.divx', '.xvid', '.mpv', '.m2p', '.mp2', '.mpeg2', '.ogm'}
            has_video = False
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if any(file_path.lower().endswith(ext) for ext in video_extensions):
                        has_video = True
                        break
            
            if has_video:
                self.setStyleSheet(self.original_style + """
                    QListWidget {
                        border: 2px dashed #4CAF50;
                        background-color: #E8F5E8;
                    }
                """)
                event.acceptProposedAction()
            else:
                self.setStyleSheet(self.original_style + """
                    QListWidget {
                        border: 2px dashed #F44336;
                        background-color: #FFEBEE;
                    }
                """)
                event.ignore()
        else:
            event.ignore()
        
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v', 
                               '.mpg', '.mpeg', '.m2v', '.m2ts', '.mts', '.ts', '.vob', '.3gp',
                               '.3g2', '.f4v', '.asf', '.rmvb', '.rm', '.ogv', '.mxf', '.dv',
                               '.divx', '.xvid', '.mpv', '.m2p', '.mp2', '.mpeg2', '.ogm'}
            has_video = False
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if any(file_path.lower().endswith(ext) for ext in video_extensions):
                        has_video = True
                        break
            
            if has_video:
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()
    
    def dragLeaveEvent(self, event):
        self.setStyleSheet(self.original_style)
        super().dragLeaveEvent(event)
            
    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(self.original_style)
        
        if event.mimeData().hasUrls():
            video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v', 
                               '.mpg', '.mpeg', '.m2v', '.m2ts', '.mts', '.ts', '.vob', '.3gp',
                               '.3g2', '.f4v', '.asf', '.rmvb', '.rm', '.ogv', '.mxf', '.dv',
                               '.divx', '.xvid', '.mpv', '.m2p', '.mp2', '.mpeg2', '.ogm'}
            video_files = []
            
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if any(file_path.lower().endswith(ext) for ext in video_extensions):
                        video_files.append(file_path)
            
            if video_files:
                self.files_dropped.emit(video_files)
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.save()
            col = self.palette().placeholderText().color()
            painter.setPen(col)
            fm = self.fontMetrics()
            elided_text = fm.elidedText(
                "📁 Drag & drop video files here or click 'Add Files'", 
                Qt.TextElideMode.ElideRight, 
                self.viewport().width()
            )
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, elided_text)
            painter.restore()

class VideosTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        
        self.output_file = QLineEdit()
        self.output_file.setText('output.mp4')
        self.output_file.hide()
        
        self.output_browse = QPushButton('Browse')
        self.output_browse.hide()
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.add_btn = QPushButton('Add Files')
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setFixedWidth(150)
        self.clear_btn = QPushButton('Clear All')
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setFixedWidth(150)
        
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        self.video_list = DragDropListWidget()
        self.video_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.video_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.video_list)
        
        start_btn_layout = QHBoxLayout()
        start_btn_layout.addStretch()
        
        self.process_cpu_btn = QPushButton('Start (CPU)')
        self.process_cpu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_cpu_btn.setFixedWidth(150)
        
        self.process_gpu_btn = QPushButton('Start (GPU)')
        self.process_gpu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_gpu_btn.setFixedWidth(150)
        
        start_btn_layout.addWidget(self.process_cpu_btn)
        start_btn_layout.addWidget(self.process_gpu_btn)
        start_btn_layout.addStretch()
        layout.addLayout(start_btn_layout)
        
        self.setLayout(layout)

    def clear_videos(self):
        self.video_list.clear()
        self.output_file.setText('output.mp4')

class XfadeGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings('afkarxyz', 'FFmpeg Xfade GUI')
        self.gpu_type = self.detect_gpu()
        self.transition_labels = {}
        self.transition_movies = {}
        self._loading_settings = False
        self.initUI()

    def detect_gpu(self):
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu_name = gpus[0].name.lower()
                if 'nvidia' in gpu_name:
                    return 'NVIDIA'
                elif 'radeon' in gpu_name:
                    return 'RADEON'
            
            import platform
            if platform.system() == 'Windows':
                import wmi
                c = wmi.WMI()
                for gpu in c.Win32_VideoController():
                    if 'intel' in gpu.Name.lower() or 'amd' in gpu.Name.lower():
                        return 'GPU'
        except ImportError:
            pass
        
        return 'GPU'

    def initUI(self):
        self.setWindowTitle('FFmpeg Xfade GUI')
        self.setFixedWidth(650)
        self.setFixedHeight(365)
        self.setWindowIcon(QIcon(get_resource_path(os.path.join('assets', 'FFmpeg.svg'))))
        main_layout = QVBoxLayout()

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        self.videos_tab = VideosTab()
        self.videos_tab.add_btn.clicked.connect(self.add_video)
        self.videos_tab.clear_btn.clicked.connect(self.videos_tab.clear_videos)
        self.videos_tab.video_list.customContextMenuRequested.connect(self.show_context_menu)
        self.videos_tab.video_list.files_dropped.connect(self.handle_dropped_files)
        self.videos_tab.process_cpu_btn.clicked.connect(lambda: self.process_videos(use_gpu=False))
        self.videos_tab.process_gpu_btn.clicked.connect(lambda: self.process_videos(use_gpu=True))
        self.videos_tab.process_gpu_btn.setText(f'Start ({self.gpu_type})')
        self.tab_widget.addTab(self.videos_tab, "File Selection")

        transition_tab = QWidget()
        transition_layout = QVBoxLayout()
        
        transition_options_layout = QHBoxLayout()
        transition_options_layout.addWidget(QLabel('Duration:'))
        self.transition_duration = QDoubleSpinBox()
        self.transition_duration.setRange(0.1, 5.0)
        self.transition_duration.setSingleStep(0.1)
        self.transition_duration.setValue(0.5)
        transition_options_layout.addWidget(self.transition_duration)
        transition_options_layout.addStretch()
        
        transition_layout.addLayout(transition_options_layout)
        
        self.transition_type = QComboBox()
        self.transition_type.hide()
        transition_types = [
            "circleclose", "circlecrop", "circleopen", "coverdown", "coverleft", "coverright", "coverup",
            "diagbl", "diagbr", "diagtl", "diagtr", "dissolve", "distance", "fade", "fadeblack", "fadegrays",
            "fadewhite", "hblur", "hlslice", "hlwind", "horzclose", "horzopen", "hrslice", "hrwind", "pixelize",
            "radial", "rectcrop", "revealdown", "revealleft", "revealright", "revealup", "slidedown", "slideleft",
            "slideright", "slideup", "smoothdown", "smoothleft", "smoothright", "smoothup", "squeezeh", "squeezev",
            "vdslice", "vdwind", "vertclose", "vertopen", "vuslice", "vuwind", "wipebl", "wipebr", "wipedown",
            "wipeleft", "wiperight", "wipetl", "wipetr", "wipeup", "zoomin"
        ]
        self.transition_type.addItems(transition_types)
        fade_index = self.transition_type.findText("fade")
        if fade_index != -1:
            self.transition_type.setCurrentIndex(fade_index)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        gallery_widget = QWidget()
        gallery_layout = QGridLayout(gallery_widget)
        gallery_layout.setHorizontalSpacing(10)
        gallery_layout.setVerticalSpacing(10)
        self.load_gallery(gallery_layout)
        scroll_area.setWidget(gallery_widget)
        transition_layout.addWidget(scroll_area)

        transition_tab.setLayout(transition_layout)
        self.tab_widget.addTab(transition_tab, "Transitions")

        process_tab = QWidget()
        process_layout = QVBoxLayout()

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        process_layout.addWidget(self.log_output)

        stop_layout = QHBoxLayout()
        stop_layout.addStretch()
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setFixedWidth(150)
        self.stop_btn.setEnabled(False)
        stop_layout.addWidget(self.stop_btn)
        
        stop_layout.addStretch()
        process_layout.addLayout(stop_layout)

        process_tab.setLayout(process_layout)
        self.tab_widget.addTab(process_tab, "Process")
        
        self.stop_btn.clicked.connect(self.stop_processing)

        about_tab = QWidget()
        about_layout = QVBoxLayout(about_tab)
        about_layout.setSpacing(10)
        
        icon_svg_path = get_resource_path(os.path.join('assets', 'FFmpeg.svg'))
        if os.path.exists(icon_svg_path):
            icon_label = QLabel()
            pixmap = QPixmap(icon_svg_path)
            scaled_pixmap = pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            icon_label.setPixmap(scaled_pixmap)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            about_layout.addWidget(icon_label)
        
        about_text = QLabel("""
<h3>FFmpeg Xfade GUI</h3>
<p><b>Version:</b> 1.2</p>

<p><b>Supported Input Formats:</b><br>
MP4, AVI, MOV, MKV, WMV, FLV, WEBM, M4V, MPG, MPEG, M2V, M2TS, MTS, TS, VOB, 3GP, 3G2, F4V, ASF, RMVB, RM, OGV, MXF, DV, DIVX, XVID, MPV, M2P, MP2, MPEG2, OGM</p>

<p><b>GitHub:</b><br>
<a href="https://github.com/afkarxyz/FFmpeg-Xfade-GUI">https://github.com/afkarxyz/FFmpeg-Xfade-GUI</a></p>
        """)
        about_text.setWordWrap(True)
        about_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        about_text.setOpenExternalLinks(True)
        about_layout.addWidget(about_text)
        
        about_layout.addStretch()
        
        self.tab_widget.addTab(about_tab, "About")

        self.setLayout(main_layout)
        
        self.load_settings()
        
        self.transition_duration.valueChanged.connect(self.save_settings)
        self.transition_type.currentTextChanged.connect(self.save_settings)

    def load_gallery(self, layout):
        transitions = [
            "circleclose", "circlecrop", "circleopen", "coverdown", "coverleft", "coverright", "coverup",
            "diagbl", "diagbr", "diagtl", "diagtr", "dissolve", "distance", "fade", "fadeblack", "fadegrays",
            "fadewhite", "hblur", "hlslice", "hlwind", "horzclose", "horzopen", "hrslice", "hrwind", "pixelize",
            "radial", "rectcrop", "revealdown", "revealleft", "revealright", "revealup", "slidedown", "slideleft",
            "slideright", "slideup", "smoothdown", "smoothleft", "smoothright", "smoothup", "squeezeh", "squeezev",
            "vdslice", "vdwind", "vertclose", "vertopen", "vuslice", "vuwind", "wipebl", "wipebr", "wipedown",
            "wipeleft", "wiperight", "wipetl", "wipetr", "wipeup", "zoomin"
        ]

        for i, transition in enumerate(transitions):
            movie_label = ClickableLabel(self)
            movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            movie_label.setCursor(Qt.CursorShape.PointingHandCursor)
            movie_label.setToolTip(transition)
            
            movie_label.clicked.connect(self.create_transition_handler(transition))
            
            gif_path = get_resource_path(os.path.join("assets", f"{transition}.gif"))
            if os.path.exists(gif_path):
                movie = QMovie(gif_path)
                movie.setScaledSize(QSize(135, 102))
                movie_label.setMovie(movie)
                movie.start()
                self.transition_movies[transition] = movie
            else:
                movie_label.setText(transition)
            
            layout.addWidget(movie_label, i // 4, i % 4)
            self.transition_labels[transition] = movie_label


    def create_transition_handler(self, transition):
        return lambda: self.select_transition(transition)

    def select_transition(self, transition):
        self.transition_type.setCurrentText(transition)
        self.highlight_selected_transition(transition)
        self.save_settings()

    def highlight_selected_transition(self, selected_transition):
        for transition, label in self.transition_labels.items():
            if transition == selected_transition:
                self.apply_grayscale_effect(label)
            else:
                self.remove_grayscale_effect(label)

    def apply_grayscale_effect(self, label):
        effect = QGraphicsColorizeEffect()
        effect.setColor(Qt.GlobalColor.gray)
        effect.setStrength(1.0)
        label.setGraphicsEffect(effect)

    def remove_grayscale_effect(self, label):
        label.setGraphicsEffect(None)
    
    def show_context_menu(self, position):
        item = self.videos_tab.video_list.itemAt(position)
        if item is not None:
            context_menu = QMenu(self)
            delete_action = QAction("Delete", self)
            delete_action.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_TrashIcon))
            delete_action.triggered.connect(self.delete_selected_file)
            context_menu.addAction(delete_action)
            context_menu.exec(self.videos_tab.video_list.mapToGlobal(position))
    
    def delete_selected_file(self):
        current_row = self.videos_tab.video_list.currentRow()
        if current_row >= 0:
            self.videos_tab.video_list.takeItem(current_row)
    
    def handle_dropped_files(self, files):
        for file in files:
            self.videos_tab.video_list.addItem(file)
        
        if files:
            self.update_output_path()
    
    def update_output_path(self):
        if self.videos_tab.video_list.count() > 0:
            from pathlib import Path
            first_video = self.videos_tab.video_list.item(0).text()
            first_video_path = Path(first_video)
            transitioned_folder = first_video_path.parent / "Transitioned"
            
            output_filename = self.videos_tab.output_file.text()
            if not output_filename or output_filename == 'output.mp4':
                output_filename = 'output.mp4'
            
            output_file = transitioned_folder / Path(output_filename).name
            self.videos_tab.output_file.setText(str(output_file).replace('\\', '/'))

    def add_video(self):
        video_formats = "Video Files ("
        extensions = [
            "*.mp4", "*.avi", "*.mov", "*.mkv", "*.wmv", "*.flv", "*.webm", 
            "*.m4v", "*.mpg", "*.mpeg", "*.m2v", "*.m2ts", "*.mts", "*.ts", 
            "*.vob", "*.3gp", "*.3g2", "*.f4v", "*.asf", "*.rmvb", "*.rm", 
            "*.ogv", "*.mxf", "*.dv", "*.divx", "*.xvid", "*.mpv", "*.m2p", 
            "*.mp2", "*.mpeg2", "*.ogm"
        ]
        video_formats += " ".join(extensions) + ")"
        
        files, _ = QFileDialog.getOpenFileNames(
            self, 
            'Select Video Files', 
            '', 
            video_formats
        )
        for file in files:
            self.videos_tab.video_list.addItem(file)
        
        if files:
            self.update_output_path()

    def get_unique_output_name(self, base_name):
        name, ext = os.path.splitext(base_name)
        counter = 1
        while os.path.exists(f"{name}{ext}"):
            name = f"{name.rstrip('_0123456789')}_{counter}"
            counter += 1
        return f"{name}{ext}"

    def process_videos(self, use_gpu=False):
        segments = [self.videos_tab.video_list.item(i).text() for i in range(self.videos_tab.video_list.count())]
        if len(segments) < 2:
            QMessageBox.warning(self, 'Warning', 'Please select at least two videos.')
            return

        from pathlib import Path
        first_video_path = Path(segments[0])
        first_video_dir = first_video_path.parent
        
        transitioned_folder = first_video_dir / "Transitioned"
        transitioned_folder.mkdir(exist_ok=True)
        
        output_filename = self.videos_tab.output_file.text()
        if not output_filename:
            output_filename = 'output.mp4'
        
        output_file = transitioned_folder / Path(output_filename).name
        output_file = self.get_unique_output_name(str(output_file))
        self.videos_tab.output_file.setText(str(output_file).replace('\\', '/'))

        transition_duration = self.transition_duration.value()
        transition_type = self.transition_type.currentText()

        self.worker = FFmpegWorker(segments, output_file, transition_duration, transition_type, '', use_gpu)
        self.worker.gpu_type = self.gpu_type
        self.worker.finished.connect(self.on_process_finished)
        self.worker.progress.connect(self.update_log)
        self.worker.start()

        self.tab_widget.setCurrentIndex(2)

        self.videos_tab.process_cpu_btn.setEnabled(False)
        self.videos_tab.process_gpu_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        if use_gpu:
            self.videos_tab.process_gpu_btn.setText(f'Processing ({self.gpu_type})...')
        else:
            self.videos_tab.process_cpu_btn.setText('Processing...')

    def update_log(self, message):
        self.log_output.append(message)
        self.log_output.moveCursor(QTextCursor.MoveOperation.End)

    def on_process_finished(self, success, message):
        self.videos_tab.process_cpu_btn.setEnabled(True)
        self.videos_tab.process_gpu_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        self.videos_tab.process_cpu_btn.setText('Start (CPU)')
        self.videos_tab.process_gpu_btn.setText(f'Start ({self.gpu_type})')

        if success:
            self.update_log("✅ Processing completed successfully!")
        else:
            self.update_log(f"❌ Error: {message}")
            self.tab_widget.setCurrentIndex(2)
    
    def stop_processing(self):
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()
            self.worker.wait()
        self.on_process_finished(False, "Processing stopped by user")
    
    def save_settings(self):
        current_transition = self.transition_type.currentText()
        current_duration = self.transition_duration.value()
        
        self.settings.setValue('transition_type', current_transition)
        self.settings.setValue('transition_duration', current_duration)
        self.settings.sync()
    
    def load_settings(self):
        saved_transition = self.settings.value('transition_type', 'fade')
        
        saved_duration = self.settings.value('transition_duration', 0.5, type=float)
        
        self.transition_duration.setValue(saved_duration)
        
        transition_index = self.transition_type.findText(saved_transition)
        if transition_index != -1:
            self.transition_type.setCurrentIndex(transition_index)
        
        self.highlight_selected_transition(saved_transition)
            
class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    ex = XfadeGUI()
    ex.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
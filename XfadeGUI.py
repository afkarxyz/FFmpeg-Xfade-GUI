import sys
import os
import subprocess
import json
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLineEdit, QLabel, QFileDialog, 
                             QListWidget, QMessageBox, QDoubleSpinBox, QTextEdit,
                             QComboBox, QGridLayout, QScrollArea, QTabWidget, QAbstractItemView,
                             QSizePolicy, QSpacerItem, QProgressBar)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QUrl, QSettings
from PyQt6.QtGui import QTextCursor, QMovie, QIcon, QDesktopServices, QDragEnterEvent, QDropEvent, QPainter
from PyQt6.QtWidgets import QGraphicsColorizeEffect
import requests

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class FFmpegDownloader(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, save_path):
        super().__init__()
        self.save_path = save_path
        self.files = {
            'ffmpeg.exe': 'https://github.com/afkarxyz/FFmpeg-Xfade-GUI/releases/download/XfadeGUI/ffmpeg.exe',
            'ffprobe.exe': 'https://github.com/afkarxyz/FFmpeg-Xfade-GUI/releases/download/XfadeGUI/ffprobe.exe'
        }

    def run(self):
        try:
            bin_path = os.path.join(self.save_path, 'FFmpeg', 'bin')
            os.makedirs(bin_path, exist_ok=True)

            for filename, url in self.files.items():
                response = requests.get(url, stream=True)
                total_size = int(response.headers.get('content-length', 0))
                file_path = os.path.join(bin_path, filename)
                
                with open(file_path, 'wb') as f:
                    if total_size == 0:
                        f.write(response.content)
                    else:
                        downloaded = 0
                        for data in response.iter_content(chunk_size=4096):
                            downloaded += len(data)
                            f.write(data)
                            progress = int((downloaded / total_size) * 100)
                            self.progress.emit(filename, progress)

            self.finished.emit(True, f"FFmpeg files downloaded successfully to {bin_path}")
        except Exception as e:
            self.finished.emit(False, str(e))
            
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

    def run(self):
        try:
            self.process_videos()
            self.finished.emit(True, "Video processing completed successfully!")
        except Exception as e:
            self.finished.emit(False, str(e))

    def get_video_info(self, file_path):
        ffprobe_path = os.path.join(self.ffmpeg_path, "ffprobe.exe")
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run([ffprobe_path, '-v', 'quiet', '-print_format', 'json', 
                                 '-show_format', '-show_streams', file_path], 
                                capture_output=True, text=True, startupinfo=startupinfo)
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

        ffmpeg_path = os.path.join(self.ffmpeg_path, "ffmpeg.exe")
        ffmpeg_args = [ffmpeg_path,
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

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        process = subprocess.Popen(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                   universal_newlines=True, startupinfo=startupinfo)
        for line in process.stdout:
            self.progress.emit(line.strip())
        process.wait()

        if process.returncode != 0:
            raise Exception("FFmpeg process failed")

class DragDropListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            video_extensions = (
                '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', 
                '.mpg', '.mpeg', '.m2v', '.m2ts', '.mts', '.ts', '.vob', '.3gp',
                '.3g2', '.f4v', '.asf', '.rmvb', '.rm', '.ogv', '.mxf', '.dv',
                '.divx', '.xvid', '.mpv', '.m2p', '.mp2', '.mpeg2', '.ogm'
            )
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if file_path.lower().endswith(video_extensions):
                    self.addItem(file_path)
        else:
            super().dropEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.save()
            col = self.palette().placeholderText().color()
            painter.setPen(col)
            fm = self.fontMetrics()
            elided_text = fm.elidedText(
                "Drag & Drop Video Files", 
                Qt.TextElideMode.ElideRight, 
                self.viewport().width()
            )
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, elided_text)
            painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.save()
            col = self.palette().placeholderText().color()
            painter.setPen(col)
            fm = self.fontMetrics()
            elided_text = fm.elidedText(
                "Drag & Drop Video Files", 
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
        self.video_list = DragDropListWidget()
        self.video_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.video_list)
        
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton('Add Videos')
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setFixedWidth(150)
        self.remove_btn = QPushButton('Remove Selected')
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.setFixedWidth(150)
        self.clear_btn = QPushButton('Clear')
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setFixedWidth(150)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def remove_selected_videos(self):
        for item in self.video_list.selectedItems():
            self.video_list.takeItem(self.video_list.row(item))

    def clear_videos(self):
        self.video_list.clear()

class XfadeGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings('afkarxyz', 'FFmpeg Xfade GUI')
        self.gpu_type = self.detect_gpu()
        self.transition_labels = {}
        self.transition_movies = {}
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
        self.setWindowIcon(QIcon(resource_path(os.path.join('assets', 'FFmpeg.svg'))))
        main_layout = QVBoxLayout()
        
        ffmpeg_container = QVBoxLayout()
        ffmpeg_layout = QHBoxLayout()
        ffmpeg_label = QLabel('FFmpeg Path:')
        ffmpeg_label.setFixedWidth(100)
        ffmpeg_layout.addWidget(ffmpeg_label)
        self.ffmpeg_path = QLineEdit()
        self.ffmpeg_path.setText(self.load_ffmpeg_path())
        self.ffmpeg_browse = QPushButton('Browse')
        self.ffmpeg_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ffmpeg_browse.clicked.connect(self.browse_ffmpeg)
        self.get_ffmpeg_btn = QPushButton('Get FFmpeg')
        self.get_ffmpeg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.get_ffmpeg_btn.setFixedWidth(100)
        self.get_ffmpeg_btn.clicked.connect(self.download_ffmpeg)
        ffmpeg_layout.addWidget(self.ffmpeg_path)
        ffmpeg_layout.addWidget(self.ffmpeg_browse)
        ffmpeg_layout.addWidget(self.get_ffmpeg_btn)
        ffmpeg_container.addLayout(ffmpeg_layout)

        self.download_progress = QProgressBar()
        self.download_progress.hide()
        ffmpeg_container.addWidget(self.download_progress)

        main_layout.addLayout(ffmpeg_container)

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        self.videos_tab = VideosTab()
        self.videos_tab.add_btn.clicked.connect(self.add_video)
        self.videos_tab.remove_btn.clicked.connect(self.videos_tab.remove_selected_videos)
        self.videos_tab.clear_btn.clicked.connect(self.videos_tab.clear_videos)
        self.tab_widget.addTab(self.videos_tab, "Videos")

        transition_tab = QWidget()
        transition_layout = QVBoxLayout()
        
        transition_options_layout = QHBoxLayout()
        transition_options_layout.addWidget(QLabel('Type:'))
        self.transition_type = QComboBox()
        self.transition_type.setCursor(Qt.CursorShape.PointingHandCursor)
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
        transition_options_layout.addWidget(self.transition_type)
        
        transition_options_layout.addWidget(QLabel('Duration:'))
        self.transition_duration = QDoubleSpinBox()
        self.transition_duration.setRange(0.1, 5.0)
        self.transition_duration.setSingleStep(0.1)
        self.transition_duration.setValue(0.5)
        transition_options_layout.addWidget(self.transition_duration)
        
        transition_layout.addLayout(transition_options_layout)

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
        
        output_layout = QHBoxLayout()
        output_label = QLabel('Output File:')
        output_layout.addWidget(output_label)
        self.output_file = QLineEdit()
        self.output_file.setText('output.mp4')
        
        self.output_browse = QPushButton('Browse')
        self.output_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.output_browse.clicked.connect(self.browse_output)
        
        self.output_open = QPushButton('Open')
        self.output_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.output_open.clicked.connect(self.open_output_directory)
        
        output_layout.addWidget(self.output_file)
        output_layout.addWidget(self.output_browse)
        output_layout.addWidget(self.output_open)
        process_layout.addLayout(output_layout)

        start_btn_layout = QHBoxLayout()
        self.process_cpu_btn = QPushButton('Start (CPU)')
        self.process_cpu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_cpu_btn.setFixedWidth(180)
        self.process_cpu_btn.clicked.connect(lambda: self.process_videos(use_gpu=False))
        self.process_gpu_btn = QPushButton(f'Start ({self.gpu_type})')
        self.process_gpu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_gpu_btn.setFixedWidth(180)
        self.process_gpu_btn.clicked.connect(lambda: self.process_videos(use_gpu=True))
        start_btn_layout.addStretch()
        start_btn_layout.addWidget(self.process_cpu_btn)
        start_btn_layout.addWidget(self.process_gpu_btn)
        start_btn_layout.addStretch()
        process_layout.addLayout(start_btn_layout)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        process_layout.addWidget(self.log_output)

        process_tab.setLayout(process_layout)
        self.tab_widget.addTab(process_tab, "Process")

        about_tab = QWidget()
        about_layout = QVBoxLayout()
        about_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.setSpacing(10)

        title_label = QLabel("FFmpeg Xfade GUI")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: palette(text);")
        about_layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)

        sections = [
            ("Check for Updates", "https://github.com/afkarxyz/FFmpeg-Xfade-GUI/releases"),
            ("Report an Issue", "https://github.com/afkarxyz/FFmpeg-Xfade-GUI/issues"),
            ("FFmpeg Xfade", "https://trac.ffmpeg.org/wiki/Xfade")
        ]

        for title, url in sections:
            section_widget = QWidget()
            section_layout = QVBoxLayout(section_widget)
            section_layout.setSpacing(5)
            section_layout.setContentsMargins(0, 0, 0, 0)

            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            section_layout.addWidget(label)

            button = QPushButton("Click Here!")
            button.setFixedWidth(150)
            button.setStyleSheet("""
                QPushButton {
                    background-color: palette(button);
                    color: palette(button-text);
                    border: 1px solid palette(mid);
                    padding: 6px;
                    border-radius: 15px;
                }
                QPushButton:hover {
                    background-color: palette(light);
                }
                QPushButton:pressed {
                    background-color: palette(midlight);
                }
            """)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _, url=url: QDesktopServices.openUrl(QUrl(url)))
            section_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

            about_layout.addWidget(section_widget)
            
            if sections.index((title, url)) < len(sections) - 1:
                spacer = QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
                about_layout.addItem(spacer)

        footer_label = QLabel("v1.1 January 2025 | FFmpeg Xfade GUI")
        footer_label.setStyleSheet("font-size: 11px; color: palette(text);")
        about_layout.addWidget(footer_label, alignment=Qt.AlignmentFlag.AlignCenter)

        about_tab.setLayout(about_layout)
        self.tab_widget.addTab(about_tab, "About")

        self.setLayout(main_layout)

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
            
            gif_path = resource_path(os.path.join("assets", f"{transition}.gif"))
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

        self.highlight_selected_transition("fade")

    def create_transition_handler(self, transition):
        return lambda: self.select_transition(transition)

    def select_transition(self, transition):
        self.transition_type.setCurrentText(transition)
        self.highlight_selected_transition(transition)

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
        
    def download_ffmpeg(self):
        save_path = os.path.dirname(os.path.abspath(__file__))
        self.downloader = FFmpegDownloader(save_path)
        self.downloader.progress.connect(self.update_download_progress)
        self.downloader.finished.connect(self.on_download_finished)
        
        self.download_progress.show()
        self.download_progress.setValue(0)
        self.get_ffmpeg_btn.setEnabled(False)
        self.get_ffmpeg_btn.setText('Downloading...')
        
        self.downloader.start()

    def update_download_progress(self, filename, progress):
        self.download_progress.setValue(progress)
        self.download_progress.setFormat(f'Downloading {filename}: %p%')

    def on_download_finished(self, success, message):
        self.get_ffmpeg_btn.setEnabled(True)
        self.get_ffmpeg_btn.setText('Get FFmpeg')
        self.download_progress.hide()

        if success:
            ffmpeg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'FFmpeg', 'bin')
            self.ffmpeg_path.setText(ffmpeg_path)
            self.save_ffmpeg_path(ffmpeg_path)
            QMessageBox.information(self, 'Success', message)
        else:
            QMessageBox.critical(self, 'Error', f'Download failed: {message}')

    def load_ffmpeg_path(self):
        return self.settings.value('ffmpeg_path', '', str)

    def save_ffmpeg_path(self, path):
        self.settings.setValue('ffmpeg_path', path)
        self.settings.sync()

    def browse_ffmpeg(self):
        directory = QFileDialog.getExistingDirectory(self, "Select FFmpeg Directory")
        if directory:
            self.ffmpeg_path.setText(directory)
            self.save_ffmpeg_path(directory)

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
        self.videos_tab.video_list.addItems(files)

    def browse_output(self):
        file, _ = QFileDialog.getSaveFileName(self, 'Save Output File', '', 'Video Files (*.mp4)')
        if file:
            file = file.replace('\\', '/')
            self.output_file.setText(file)

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

        output_file = self.output_file.text()
        if not output_file:
            QMessageBox.warning(self, 'Warning', 'Please specify an output file.')
            return
        
        first_video_dir = os.path.dirname(segments[0]).replace('\\', '/')
        
        output_file = os.path.join(first_video_dir, os.path.basename(output_file)).replace('\\', '/')
        output_file = self.get_unique_output_name(output_file)
        self.output_file.setText(output_file)

        ffmpeg_path = self.ffmpeg_path.text()
        if not os.path.exists(os.path.join(ffmpeg_path, "ffmpeg.exe")) or not os.path.exists(os.path.join(ffmpeg_path, "ffprobe.exe")):
            QMessageBox.warning(self, 'Warning', 'Invalid FFmpeg path. Please ensure both ffmpeg.exe and ffprobe.exe are present in the selected directory.')
            return

        transition_duration = self.transition_duration.value()
        transition_type = self.transition_type.currentText()

        self.worker = FFmpegWorker(segments, output_file, transition_duration, transition_type, ffmpeg_path, use_gpu)
        self.worker.gpu_type = self.gpu_type
        self.worker.finished.connect(self.on_process_finished)
        self.worker.progress.connect(self.update_log)
        self.worker.start()

        self.process_cpu_btn.setEnabled(False)
        self.process_gpu_btn.setEnabled(False)
        
        if use_gpu:
            self.process_gpu_btn.setText(f'Processing ({self.gpu_type})...')
        else:
            self.process_cpu_btn.setText('Processing...')

    def open_output_directory(self):
        output_path = self.output_file.text()
        if output_path:
            dir_path = os.path.dirname(output_path).replace('\\', '/')
            if os.path.exists(dir_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(dir_path))
            else:
                QMessageBox.warning(self, 'Warning', 'Output directory does not exist.')

    def update_log(self, message):
        self.log_output.append(message)
        self.log_output.moveCursor(QTextCursor.MoveOperation.End)

    def on_process_finished(self, success, message):
        self.process_cpu_btn.setEnabled(True)
        self.process_gpu_btn.setEnabled(True)
        
        self.process_cpu_btn.setText('Start (CPU)')
        self.process_gpu_btn.setText(f'Start ({self.gpu_type})')

        if success:
            QMessageBox.information(self, 'Success', message)
        else:
            QMessageBox.critical(self, 'Error', f'An error occurred: {message}')
            
class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = XfadeGUI()
    ex.show()
    sys.exit(app.exec())

#!/usr/bin/env python3
import sys
import subprocess
import re
import gettext
import datetime
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton,
    QTableView, QMessageBox, QHBoxLayout, QHeaderView, QLabel, QCheckBox,
    QMenu, QProgressBar, QComboBox, QDialog, QDialogButtonBox, QGroupBox
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QUrl,
    QRunnable, QThreadPool, pyqtSignal, QObject, QEvent
)
from PyQt6.QtGui import QDesktopServices, QIcon, QAction, QGuiApplication
import os

# Gettext configuration for internationalization
_ = gettext.gettext

# Database paths
DEFAULT_DB_PATH = "/var/lib/plocate/plocate.db"
MEDIA_DB_PATH = "/var/lib/plocate/media.db"
MEDIA_SCAN_PATH = "/run/media"

# MAPPING FOR CATEGORY SHORTCUTS IN SEARCH BAR (e.g., '::doc')
# Key: User shortcut (without '::'). Value: Translatable category name.
CATEGORY_SHORTCUTS = {
    "all": _("All Categories"),
    "dir": _("Directories"),
    "doc": _("Documents"),
    "img": _("Images"),
    "vid": _("Videos"),
    "video": _("Videos"),
    "audio": _("Audio"),
    "app": _("Apps"),
    "code": _("Code/Scripts"),
    "script": _("Code/Scripts"),
    "zip": _("Archives"),
    "text": _("Generic Text"),
}
# Command prefix for category shortcut.
SHORTCUT_PREFIX = "::"

# --- CATEGORY FILTERING LOGIC (FIXED and TRANSLATED) ---
# Map display names to a list of extensions (DO NOT include '$' here, it will be added in get_category_regex)

# ARCHIVES
ARCHIVES_EXTENSIONS = [
    '.7z', '.ace', '.arj', '.bz2', '.cab', '.gz', '.gzip', '.jar', '.rar',
    '.tar', '.tgz', '.zip', '.z',
    # Explicitly include the rar splits (r00-r29)
    '.r00', '.r01', '.r02', '.r03', '.r04', '.r05', '.r06', '.r07', '.r08',
    '.r09', '.r10', '.r11', '.r12', '.r13', '.r14', '.r15', '.r16', '.r17',
    '.r18', '.r19', '.r20', '.r21', '.r22', '.r23', '.r24', '.r25', '.r26',
    '.r27', '.r28', '.r29',
]

# APP EXTENSIONS
APP_EXTENSIONS = [
    '.appimage', '.exe', '.deb', '.rpm', '.desktop'
]

# AUDIO
AUDIO_EXTENSIONS = [
    '.aac', '.ac3', '.aif', '.aifc', '.aiff', '.au', '.cda', '.dts', '.fla',
    '.flac', '.it', '.m1a', '.m2a', '.m3u', '.m4a', '.mid', '.midi', '.mka',
    '.mod', '.mp2', '.mp3', '.mpa', '.ogg', '.opus', '.ra', '.rmi', '.spc',
    '.snd', '.umx', '.voc', '.wav', '.wma', '.xm'
]

# IMAGES
IMAGES_EXTENSIONS = [
    '.ani', '.bmp', '.gif', '.ico', '.jpe', '.jpeg', '.jpg', '.pcx', '.png',
    '.psd', '.tga', '.tif', '.tiff', '.webp', '.wmf'
]

# VIDEO
VIDEO_EXTENSIONS = [
    '.3g2', '.3gp', '.3gp2', '.3gpp', '.amr', '.amv', '.asf', '.avi', '.bdmv',
    '.bik', '.d2v', '.divx', '.drc', '.dsa', '.dsm', '.dss', '.dsv', '.evo',
    '.f4v', '.flc', '.fli', '.flic', '.flv', '.hdmov', '.ifo', '.ivf', '.m1v',
    '.m2p', '.m2t', '.m2ts', '.m2v', '.m4b', '.m4p', '.m4v', '.mkv', '.mp2v',
    '.mp4', '.mp4v', '.mpe', '.mpeg', '.mpg', '.mpls', '.mpv2', '.mpv4', '.mov',
    '.mts', '.ogm', '.ogv', '.pss', '.pva', '.qt', '.ram', '.ratdvd', '.rm',
    '.rmm', '.rmvb', '.roq', '.rpm', '.smil', '.smk', '.swf', '.tp', '.tpr',
    '.ts', '.vob', '.vp6', '.webm', '.wm', '.wmp', '.wmv'
]

# Core Documents (Office, PDF, Help files, Diagrams)
DOCUMENT_EXTENSIONS = [
    '.doc', '.docx', '.docm', '.dot', '.dotx', '.dotm', '.odt', '.pdf', '.rtf',
    '.xls', '.xlsx', '.xlsm', '.xlsb', '.xltm', '.xltx', '.xlam', '.ods',
    '.ppt', '.pptx', '.pptm', '.pps', '.ppsx', '.ppsm', '.potx', '.potm', '.ppam',
    '.sldm', '.sldx', '.odp', '.thmx',
    '.wpd', '.wps', '.wri', '.chm', '.vsd', '.vsdx',
]

# Code & Scripts (Includes C/C++ and other development files)
CODE_EXTENSIONS = [
    '.py', '.sh', '.js', '.c', '.cpp', '.cxx', '.h', '.hpp', '.hxx', '.java', '.lua',
]

# Generic Text & Configuration (Includes common structured text formats)
TEXT_EXTENSIONS = [
    '.txt', '.log', '.md', '.csv', '.ini', '.xml', '.htm', '.html', '.mht', '.mhtml',
]

FILE_CATEGORIES = {
    _("All Categories"): [],
    _("Directories"): ["DIR_ONLY"],  # Special flag to filter only directories
    _("Documents"): DOCUMENT_EXTENSIONS,
    _("Images"): IMAGES_EXTENSIONS,
    _("Videos"): VIDEO_EXTENSIONS,
    _("Audio"): AUDIO_EXTENSIONS,
    _("Apps"): APP_EXTENSIONS,
    _("Code/Scripts"): CODE_EXTENSIONS,
    _("Archives"): ARCHIVES_EXTENSIONS,
    _("Generic Text"): TEXT_EXTENSIONS
}


def get_category_regex(category_name: str) -> str | None:
    """Returns a combined case-insensitive regex pattern for the category or None/DIR_ONLY flag."""
    # This is complex due to gettext; the ComboBox gives us the translated string,
    # so we need a reliable way to get the original extension list.
    extensions_list = None
    for key, extensions in FILE_CATEGORIES.items():
        if key == category_name or _(key) == category_name:
            extensions_list = extensions
            break

    if extensions_list is None:
        return None  # Should not happen if ComboBox is populated correctly

    if not extensions_list:
        return None  # 'All Categories' or empty list

    if extensions_list[0] == "DIR_ONLY":
        return r"^(?:[^\n]*\/)?[^\/\.]*$"

    # We escape the extension and explicitly add the '$' at the end of the pattern
    # to anchor the match to the end of the path/filename.
    patterns = [re.escape(ext) for ext in extensions_list]
    return r"(?:" + r"|".join(patterns) + r")$"


def tokenize_search_query(query: str) -> tuple[list[str], str | None]:
    """
    Splits the query into tokens, preserving phrases in quotes and extracting the category shortcut.
    Returns: (list of search tokens, category shortcut name or None)
    """
    category_shortcut_name = None

    # 1. Look for the category shortcut pattern (e.g., ::doc)
    # The regex searches for the SHORTCUT_PREFIX followed by letters/digits at the start or after a space.
    safe_prefix = re.escape(SHORTCUT_PREFIX)
    # Pattern: (^|\s) (::[a-z0-9]+) (\s|$)
    shortcut_pattern = r"(^|\s)(" + safe_prefix + r"([a-z0-9]+))(\s|$)"

    # Use IGNORECASE for flexibility in shortcut typing
    match = re.search(shortcut_pattern, query, re.IGNORECASE)

    if match:
        # Extract the full shortcut token (e.g., '::doc') and the category key ('doc')
        full_shortcut_token = match.group(2)
        category_key = match.group(3).lower()

        # Check if the key is a valid shortcut
        if category_key in CATEGORY_SHORTCUTS:
            category_shortcut_name = CATEGORY_SHORTCUTS[category_key]
            # Remove the shortcut token from the query BEFORE processing quotes
            # Use re.sub with a limit of 1 to ensure only the matched token is removed
            query = query.replace(full_shortcut_token, '', 1).strip()

    # 2. Extract quoted phrases and single words from the remaining query
    # If the user searches for "::doc", it will be processed here as a literal search term.
    quoted_phrases = re.findall(r'"([^"]*)"', query)
    unquoted_query = re.sub(r'"[^"]*"', ' ', query)
    single_words = [word for word in unquoted_query.split() if word]

    # Combine search tokens and return the category name (translatable)
    return quoted_phrases + single_words, category_shortcut_name


# --- Icon Utility Function for Category Menu ---
def get_icon_for_category(category_name: str) -> QIcon:
    """Returns a QIcon based on the translated category name."""
    if _("All Categories") == category_name:
        return QIcon.fromTheme("system-search")
    if _("Directories") == category_name:
        return QIcon.fromTheme("folder")
    if _("Documents") == category_name:
        return QIcon.fromTheme("x-office-document")
    if _("Images") == category_name:
        return QIcon.fromTheme("image-x-generic")
    if _("Videos") == category_name:
        return QIcon.fromTheme("video-x-generic")
    if _("Audio") == category_name:
        return QIcon.fromTheme("audio-x-generic")
    if _("Apps") == category_name:
        return QIcon.fromTheme("applications-other")  # Generic icon for applications
    if _("Code/Scripts") == category_name:
        return QIcon.fromTheme("text-x-script")
    if _("Archives") == category_name:
        return QIcon.fromTheme("package-x-generic")
    if _("Generic Text") == category_name:
        return QIcon.fromTheme("text-x-generic")

    return QIcon()  # Fallback


# --- File Size Utility ---
def human_readable_size(size, decimal_places=2):
    """Converts bytes to a human-readable string (KB, MB, GB, TB, etc.)."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


# --- Icon Utility Function for Table View (Updated for .desktop) ---
def get_icon_for_file_type(filepath: str, is_dir: bool) -> QIcon:
    """Returns a QIcon based on the file extension or if it is a directory."""

    if not filepath or filepath == _("No results found") or filepath == _("Search failed") or filepath == _("No results match filter"):
        return QIcon.fromTheme("dialog-warning")

    # 1. Directory Icon
    basename = os.path.basename(filepath)
    if is_dir or ('.' not in basename and basename != ''):
        return QIcon.fromTheme("folder")

    # 2. Icon based on Common Extensions (using Freedesktop icon naming spec)
    ext = os.path.splitext(filepath)[1].lower()

    if ext in ['.mp3', '.wav', '.ogg', '.flac', '.m4a']:  # Audio
        return QIcon.fromTheme("audio-x-generic")

    if ext in ['.avi', '.mp4', '.mkv', '.mov']:  # Videos
        return QIcon.fromTheme("video-x-generic")

    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:  # Images
        return QIcon.fromTheme("image-x-generic")

    if ext in ['.pdf']:  # Documents
        return QIcon.fromTheme("application-pdf")

    if ext in ['.doc', '.docx', '.odt']:  # Documents
        return QIcon.fromTheme("x-office-document")

    # Archives/Apps (Updated to include .desktop)
    if ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.deb', '.rpm', '.appimage', '.exe', '.desktop']:
        # Using a generic executable icon for apps and .desktop files
        if ext in ['.deb', '.rpm', '.appimage', '.exe', '.desktop']:
            return QIcon.fromTheme("application-x-executable")
        return QIcon.fromTheme("package-x-generic")  # Archives

    if ext in ['.py', '.sh', '.c', '.cpp', '.html', '.js']:  # Code
        return QIcon.fromTheme("text-x-script")

    if ext in ['.txt', '.log', '.md']:  # Text
        return QIcon.fromTheme("text-x-generic")

    # 3. Default Icon (Generic File)
    return QIcon.fromTheme("text-x-generic")


# --- Stat Worker (for non-blocking os.stat) ---
class StatSignals(QObject):
    """Defines signals available from a running worker thread."""
    # Signal (path, size_str, date_str, success_bool)
    finished = pyqtSignal(str, str, str, bool)


class StatWorker(QRunnable):
    """
    Runnable that performs os.stat on a path in a separate thread.
    This prevents the GUI from freezing when accessing slow/unmounted drives.
    """

    def __init__(self, full_path):
        super().__init__()
        self.full_path = full_path
        self.signals = StatSignals()

    def run(self):
        """The long-running task: getting file statistics."""
        try:
            # os.stat is the blocking call that might hang if the drive is unmounted
            stat_result = os.stat(self.full_path)

            # Format size
            size_str = human_readable_size(stat_result.st_size)

            # Format modification date
            mod_time = datetime.datetime.fromtimestamp(stat_result.st_mtime)
            mod_date_str = mod_time.strftime('%Y-%m-%d %H:%M:%S')

            # Success: emit the formatted data
            self.signals.finished.emit(self.full_path, size_str, mod_date_str, True)

        except (FileNotFoundError, PermissionError, OSError):
            # Failure: OSError catches timeouts or failures related to unmounted/inaccessible paths
            self.signals.finished.emit(self.full_path, "", "", False)


# --- Update DB Worker (for non-blocking updatedb) ---
class UpdateDBSignals(QObject):
    """Defines signals for the UpdateDBWorker."""
    started = pyqtSignal()
    # Signal (success, message, db_type)
    finished = pyqtSignal(bool, str, str)


class UpdateDBWorker(QRunnable):
    """Runnable that performs pkexec updatedb in a separate thread."""

    def __init__(self, update_command, db_type):
        super().__init__()
        self.update_command = update_command
        self.db_type = db_type
        self.signals = UpdateDBSignals()
        self.process = None  # To hold the running subprocess reference
        self.canceled = False  # Flag to indicate user cancellation

    def run(self):
        self.signals.started.emit()
        try:
            # NOTE: Use Popen to keep control of the process and allow cancellation
            self.process = subprocess.Popen(
                self.update_command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Wait for the process to finish (blocks only the worker thread)
            stdout, stderr = self.process.communicate()

            if self.canceled:
                # The user canceled the operation (the message will be handled in the UI)
                self.signals.finished.emit(False, _("Database update was cancelled by the user."), self.db_type)
                return

            if self.process.returncode == 0:
                # Success
                self.signals.finished.emit(True, stdout, self.db_type)
            else:
                # Failure (non-zero exit code)
                error_details = stderr or stdout or _("No detailed error message was returned.")
                full_error_message = (
                        _("Command: ") + " ".join(self.update_command) +
                        _("\nExit Status: ") + str(self.process.returncode) +
                        _("\nDetails: \n") + error_details.strip()
                )
                self.signals.finished.emit(False, full_error_message, self.db_type)

        except FileNotFoundError:
            # Failure (pkexec not found)
            self.signals.finished.emit(False,
                                       _("The 'pkexec' command was not found. "
                                         "Please ensure 'polkit' is installed and configured."), self.db_type
                                       )
        except Exception as e:
            # Catch all other exceptions
            if self.canceled:
                self.signals.finished.emit(False, _("Database update was cancelled by the user."), self.db_type)
            else:
                self.signals.finished.emit(False, _("An unexpected error occurred: ") + str(e), self.db_type)

    def cancel(self):
        """Terminates the running subprocess if it is active."""
        if self.process and self.process.poll() is None:  # poll() checks if the process is still running
            self.canceled = True
            try:
                # Terminate the process (sends SIGTERM)
                self.process.terminate()
                # Wait briefly for it to terminate
                self.process.wait(timeout=1)
            except OSError:
                # Catches "No such process" errors if the process terminated just now
                pass


# --- NEW: Search Worker and Signals (for non-blocking search) ---
class SearchSignals(QObject):
    """Defines signals for the SearchWorker."""
    # Signal (list of result tuples, success message/error)
    finished = pyqtSignal(list, str, bool)


class SearchWorker(QRunnable):
    """Runnable that performs the plocate search and filtering in a separate thread."""

    def __init__(self, plocate_term, post_plocate_filters, category_regex, case_insensitive):
        super().__init__()
        self.plocate_term = plocate_term
        self.post_plocate_filters = post_plocate_filters
        self.category_regex = category_regex
        self.case_insensitive = case_insensitive
        self.signals = SearchSignals()
        self._is_canceled = False  # Internal cancellation flag

    def cancel(self):
        """Sets the internal cancellation flag."""
        self._is_canceled = True

    def run(self):
        """The main search and filtering logic."""
        try:
            # 1. Build and run the base plocate command
            plocate_command = ["plocate", self.plocate_term]

            if self.case_insensitive:
                plocate_command.insert(1, "-i")

            if os.path.exists(MEDIA_DB_PATH):
                db_list = f"{DEFAULT_DB_PATH}:{MEDIA_DB_PATH}"
                plocate_command.extend(["-d", db_list])

            # Execute plocate (this is the potentially long-running blocking call)
            result = subprocess.run(
                plocate_command, text=True, capture_output=True, check=False, timeout=120  # Added timeout
            )

            # Check for cancellation *after* plocate finishes
            if self._is_canceled:
                self.signals.finished.emit([], _("Search cancelled."), False)
                return

            files = [line.strip() for line in result.stdout.splitlines() if line.strip()]

            if result.returncode != 0 and not files:
                # Check if the error is just a "not found" which is common/normal
                if result.returncode == 1 and not result.stdout and not result.stderr:
                    files = []  # Treat as zero results
                else:
                    error_message = result.stderr or result.stdout or _("Unknown plocate error.")
                    self.signals.finished.emit([], _("Error executing plocate:\n") + error_message, False)
                    return

            # 2. Category Filtering Logic
            if self.category_regex is not None:
                if self.category_regex == "DIR_ONLY":
                    files = [f for f in files if f.endswith(os.path.sep)]
                else:
                    category_filter_regex = re.compile(self.category_regex, re.IGNORECASE)
                    files = [f for f in files if category_filter_regex.search(f)]

            # Check for cancellation
            if self._is_canceled:
                self.signals.finished.emit([], _("Search cancelled."), False)
                return

            # 3. Post-Plocate Multi-Keyword/Regex Filtering Logic
            if self.post_plocate_filters:
                if len(self.post_plocate_filters) > 1:
                    escaped_keywords = [re.escape(k) for k in self.post_plocate_filters]
                    lookahead_assertions = "".join(f"(?=.*{k})" for k in escaped_keywords)
                    final_filter_pattern = f"^{lookahead_assertions}.*$"
                else:
                    final_filter_pattern = self.post_plocate_filters[0]

                regex = re.compile(final_filter_pattern, re.IGNORECASE if self.case_insensitive else 0)
                files = [f for f in files if regex.search(f)]

            # Check for cancellation
            if self._is_canceled:
                self.signals.finished.emit([], _("Search cancelled."), False)
                return

            # 4. Prepare display data
            display_rows = []
            for filepath in files:
                filepath = filepath.strip()
                if not filepath:
                    continue

                is_dir = filepath.endswith(os.path.sep)

                if filepath == os.path.sep:
                    name = os.path.sep
                    parent = ""
                else:
                    temp_path = filepath.rstrip(os.path.sep)
                    parent, name = os.path.split(temp_path)

                if not parent:
                    parent = os.path.sep

                # Store (name, parent, is_dir)
                display_rows.append((name, parent, is_dir))

            # Success
            self.signals.finished.emit(display_rows, _("Search completed."), True)

        except subprocess.TimeoutExpired:
            self.signals.finished.emit([], _("Plocate command timed out (120 seconds)."), False)
        except re.error as e:
            self.signals.finished.emit([], _("Regex filter contains an invalid pattern: ") + str(e), False)
        except Exception as e:
            self.signals.finished.emit([], _("An unexpected search error occurred: ") + str(e), False)
# --- END NEW SEARCH WORKER ---


# Model implementation for QTableView
class PlocateResultsModel(QAbstractTableModel):
    """Data model for QTableView storing plocate results."""

    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        # Data format: (name, path, is_dir)
        self._data = data if data is not None else []
        self._headers = [_("Name"), _("Path")]

    def set_data(self, data):
        """Replaces the model data and notifies the view."""
        self.beginResetModel()
        self._data = data
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        """Returns the number of rows (results)."""
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        """Returns the number of columns."""
        return len(self._headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """Returns the data for a specific index and role."""
        if not index.isValid():
            return QVariant()

        row = index.row()
        col = index.column()

        if row >= len(self._data):
            return QVariant()

        # Unpack the three elements: (name, path, is_dir)
        name, path, is_dir = self._data[row]

        # 1. Display/Edit Role (Text)
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            if col == 0:
                return name
            else:
                return path

        # 2. Decoration Role (Icon) - Only for the 'Name' column
        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            # Note: We pass the joined path and is_dir to get the correct icon
            full_path = os.path.join(path, name)
            return get_icon_for_file_type(full_path, is_dir)

        # 3. ToolTip Role (Specific logic for Name vs. Path)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 0:  # Name column
                # Tooltip for the name column is just the name
                return name
            elif col == 1:  # Path column
                # Tooltip for the path column is the full path
                return path
            else:
                # Fallback for other potential columns (if they were added later)
                return QVariant()

        return QVariant()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Returns the header data."""
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
        return QVariant()

    def sort(self, column, order):
        """Sorts the data by the specified column and order."""
        self.layoutAboutToBeChanged.emit()

        # Sort the internal data list (case-insensitive for names and paths)
        try:
            # Sort uses index 0 (Name) or index 1 (Path)
            self._data.sort(key=lambda x: str(x[column]).lower(),
                            reverse=(order == Qt.SortOrder.DescendingOrder))
        except IndexError:
            # Avoid errors if the column does not exist
            pass

        self.layoutChanged.emit()


# --- CLASS: Custom Dialog for DB Update (Focus Optimized) ---
class UpdateDatabaseDialog(QDialog):
    """A custom dialog for configuring the updatedb process."""

    def __init__(self, parent=None, media_path=MEDIA_SCAN_PATH):
        super().__init__(parent)
        self.setWindowTitle(_("Database Update Options"))
        self.setWindowIcon(QIcon.fromTheme("view-refresh"))
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout(self)

        # 1. Header/Info Section
        info_label = QLabel(_("Select the databases to update. This operation requires root privileges."))
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(info_label)
        main_layout.addSpacing(15)

        # 2. System DB Group (plocate.db)
        system_group = QGroupBox(_("⚙️️ SYSTEM INDEX (plocate.db)"))
        sys_vbox = QVBoxLayout(system_group)
        sys_vbox.addSpacing(15)

        # Checkbox for System (Highlighted with Bold)
        self.system_checkbox = QCheckBox(_("Update System Index"))
        self.system_checkbox.setChecked(True)
        self.system_checkbox.setIcon(QIcon.fromTheme("drive-harddisk"))
        self.system_checkbox.setStyleSheet("font-weight: bold;")

        # System DB Info: Descriptive text
        system_info = QLabel(
            _("Includes most of the operating system files, excluding external media and temporary directories. This is the primary index."))
        system_info.setWordWrap(True)

        # --- Exclusion Path Integration ---

        sys_vbox.addWidget(system_info)
        sys_vbox.addSpacing(10)

        exclude_label_layout = QHBoxLayout()
        exclude_label_layout.setContentsMargins(0, 0, 0, 0)
        exclude_label_layout.setSpacing(5)
        icon_folder = QLabel()
        icon_folder.setPixmap(QIcon.fromTheme("folder-close").pixmap(16, 16))
        exclude_label = QLabel(_("Additional Paths to Exclude (updatedb -e):"))
        exclude_label_layout.addWidget(icon_folder)
        exclude_label_layout.addWidget(exclude_label)
        exclude_label_layout.addStretch(1)

        sys_vbox.addLayout(exclude_label_layout)

        # Input Field for exclusions
        self.exclude_input = QLineEdit()
        self.exclude_input.setPlaceholderText(_("E.g.: /mnt/backup /tmp"))
        self.exclude_input.setToolTip(
            _("Enter space-separated paths to exclude (e.g., external drives, temporary files). These are additional to system defaults.")
        )
        sys_vbox.addWidget(self.exclude_input)

        sys_vbox.addSpacing(10)
        sys_vbox.addWidget(self.system_checkbox)

        main_layout.addWidget(system_group)
        main_layout.addSpacing(15)

        # 3. Media Database Option
        media_group = QGroupBox(_("💾 EXTERNAL MEDIA INDEX (media.db)"))
        media_vbox = QVBoxLayout(media_group)
        media_vbox.addSpacing(15)

        # Checkbox for Media (Highlighted with Bold)
        self.media_checkbox = QCheckBox(_("Update External Media Index"))
        self.media_checkbox.setChecked(True)
        self.media_checkbox.setIcon(QIcon.fromTheme("media-removable"))
        self.media_checkbox.setStyleSheet("font-weight: bold;")

        # Label for paths to index
        media_path_label = QLabel(
            _("Paths to index (space-separated). Use the default to scan all mounted media:")
        )
        media_path_label.setWordWrap(True)

        # Input field for paths
        self.media_paths_input = QLineEdit()
        self.media_paths_input.setText(media_path)
        self.media_paths_input.setPlaceholderText(
            _("E.g.: /run/media /mnt/MyExternalDrive")
        )
        self.media_paths_input.setToolTip(
            _("Enter space-separated directories to be scanned by updatedb using the 'media.db' database.")
        )

        # Layout for Media Group
        media_vbox.addWidget(media_path_label)
        media_vbox.addWidget(self.media_paths_input)
        media_vbox.addSpacing(10)
        media_vbox.addWidget(self.media_checkbox)

        main_layout.addWidget(media_group)
        main_layout.addSpacing(15)

        # 4. Dialog Buttons (QDialogButtonBox)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal
        )

        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText(_("Start Update"))
        ok_button.setIcon(QIcon.fromTheme("view-refresh"))
        ok_button.setDefault(True)  # Ensure 'Enter' works

        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Cancel"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setIcon(QIcon.fromTheme("dialog-cancel"))

        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        main_layout.addWidget(self.buttons)

        # --- FIX: Force button focus at the end ---
        # Takes priority over QLineEdit focus logic.
        ok_button.setFocus()
        # ------------------------------------------------------------------------------------------

    def get_settings(self):
        """Returns the settings needed by the main window."""
        return {
            'update_system': self.system_checkbox.isChecked(),
            'update_media': self.media_checkbox.isChecked(),
            'exclude_paths': self.exclude_input.text().strip(),
            'media_index_paths': self.media_paths_input.text().strip()  # NEW
        }


# --- END OF CUSTOM DIALOG CLASS (Focus Optimized) ---


class PlocateGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(_("Plocate GUI"))
        self.resize(800, 700)

        # --- Internal State for Preferences and Toggles ---
        self.case_insensitive_search = True
        # NEW: Flag to track manual case-sensitive setting from the button
        self._is_manually_case_sensitive = False
        self.current_category_regex = None  # Stores the current regex filter for category
        # NEW: Store raw results from plocate for in-memory filtering
        # Format: (name, path, is_dir)
        self._raw_plocate_results: list[tuple] = []
        # NEW FIX: Store the exact search term used for the last successful plocate call.
        self._last_plocate_term: str = ""
        # Live filter
        self.live_filter_enabled = True
        # --- End Internal State ---

        # Initialize ThreadPool for non-blocking operations
        self.threadpool = QThreadPool()
        # To track the path being currently processed by the worker (prevents race conditions)
        self.current_stat_path = None
        # Reference to the update worker for cancellation
        self.update_worker = None
        # Reference to the search worker for cancellation
        self.search_worker = None  # NEW: Reference for search worker

        # Try to load the application icon from the system theme
        icon = QIcon.fromTheme("plocate-gui")
        if icon.isNull():
            # Fallback for a generic search/file icon if the custom one is missing
            icon = QIcon.fromTheme("system-search")

        if not icon.isNull():
            self.setWindowIcon(icon)

        self.RESPONSIVE_WIDTH_PERCENTAGE = 0.40
        self.MIN_NAME_WIDTH = 150

        self.current_sort_column = -1
        self.current_sort_order = Qt.SortOrder.AscendingOrder

        main_layout = QVBoxLayout()

        # Input and Options container (Row 1: Main Search, Category, Case, Update)
        search_options_layout = QHBoxLayout()

        # *** MODIFICATION: ADD CASE INSENSITIVE TOGGLE FIRST ***
        # Case Insensitive Toggle Button (Dynamic Text) - FOR SEARCH
        self.case_insensitive_btn = QPushButton()
        self.case_insensitive_btn.setCheckable(True)  # Make it a toggle button

        # Initialize state and text (Aa = Case Sensitive OFF)
        self.case_insensitive_search = True
        self.case_insensitive_btn.setChecked(not self.case_insensitive_search)
        self.case_insensitive_btn.setText('Aa')
        self.case_insensitive_btn.setToolTip(
            _("Toggle Case Insensitive Search (-i): Aa = Sensitive | aa = Insensitive"))

        # Initial text update based on default state
        self.update_case_insensitive_text()

        # Connect signal: we use clicked() for checkable buttons
        self.case_insensitive_btn.clicked.connect(self.toggle_case_insensitive)

        #  *** MODIFICATION: ADD TOGGLE FIRST ***
        search_options_layout.addWidget(self.case_insensitive_btn)

        # Search input with icon and CLEAR BUTTON
        self.search_input = QLineEdit()
        # MODIFICATION: Add ToolTip to reflect integrated filtering
        self.search_input.setPlaceholderText(_("Enter search term..."))
        self.search_input.setToolTip(
            _("""Use keywords, category shortcuts, or advanced filters.

Examples:
    - Keywords: "project report"
    - Category: ::doc final_report
    - Regex: "invoice 2024" .pdf$

Keywords are space-separated. Regex must be the final term.""")
        )
        # Connect search input to the new case logic
        self.search_input.textChanged.connect(self.handle_input_case_change)

        search_icon = QIcon.fromTheme("edit-find")
        search_action = QAction(search_icon, "", self.search_input)
        # Ensure compatibility with PyQt6 ActionPosition enumeration
        self.search_input.addAction(search_action, QLineEdit.ActionPosition.LeadingPosition)
        # Connect to a dedicated search handler that starts the worker
        self.search_input.returnPressed.connect(self.run_search)
        self.search_input.setClearButtonEnabled(True)
        search_options_layout.addWidget(self.search_input)

        # Category Filter ComboBox (UPDATED TO INCLUDE ICONS)
        self.category_combobox = QComboBox()
        self.category_combobox.setToolTip(_("Filter results by file category\n\n CTRL+SHIFT+F"))

        # Populate with translated names and set icons for each item
        for key in FILE_CATEGORIES.keys():
            translated_key = _(key)
            icon = get_icon_for_category(translated_key)
            self.category_combobox.addItem(icon, translated_key)

        self.category_combobox.currentIndexChanged.connect(self.category_changed)
        search_options_layout.addWidget(self.category_combobox)

        self.unified_update_btn = QPushButton(_("Update DB"))  # Short text
        self.unified_update_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.unified_update_btn.setToolTip(_("Select which database(s) you wish to update. (F5)"))
        self.unified_update_btn.clicked.connect(self.update_unified_database)
        search_options_layout.addWidget(self.unified_update_btn)

        main_layout.addLayout(search_options_layout)

        # --- NEW: In-Memory Filter Bar (Row 2: Filter) ---
        filter_layout = QHBoxLayout()

        # >>> NEW: Toggle for the Live Filter <<<
        self.live_filter_toggle = QPushButton()  # Use a short text like "Auto"
        self.live_filter_toggle.setCheckable(True)  # Make it a toggle button

        # Set initial state
        self.live_filter_toggle.setChecked(self.live_filter_enabled)
        self._update_live_filter_text()

        # Set ToolTip for clarity
        self.live_filter_toggle.setToolTip(
            _("AUTO filters results in real-time\nENTER requires pressing Enter to filter\nCTRL+SHIFT+L toggles")
        )
        # Connect signal: we use clicked() for checkable buttons
        # The slot remains the same, but the signal is now 'clicked'
        self.live_filter_toggle.clicked.connect(self._handle_live_filter_toggle_button)

        # *** MODIFICATION: ADD LIVE FILTER TOGGLE FIRST ***
        filter_layout.addWidget(self.live_filter_toggle)

        # 1. Configure the Filter Input Field (self.filter_input)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText(_("Filter current results (in-memory)..."))
        self.filter_input.setToolTip(
            _("Filters the visible results list. This does NOT re-run the plocate search.")
        )
        filter_icon = QIcon.fromTheme("view-filter")
        filter_action = QAction(filter_icon, "", self.filter_input)
        self.filter_input.addAction(filter_action, QLineEdit.ActionPosition.LeadingPosition)
        self.filter_input.setClearButtonEnabled(True)

        # *** MODIFICATION: ADD FILTER INPUT SECOND ***
        filter_layout.addWidget(self.filter_input)

        # >>> NEW CONDITIONAL CONNECTIONS <<<
        # 1. Conditional connection: only filters if Live Filter is active
        self.filter_input.textChanged.connect(self._handle_filter_input_change)
        # 2. Enter connection: always filters when Enter is pressed
        self.filter_input.returnPressed.connect(self.run_in_memory_filter)

        main_layout.addLayout(filter_layout)
        # --- END NEW FILTER BAR ---

        # Results table setup
        self.model = PlocateResultsModel()
        self.result_table = QTableView()
        self.result_table.setModel(self.model)

        self.result_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.result_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        self.result_table.setSortingEnabled(True)
        header.sectionClicked.connect(self.update_sort_state)
        self.result_table.doubleClicked.connect(self.handle_double_click)

        # CRITICAL FIX: We connect to currentChanged, which fires reliably on item focus change.
        # We will use the index passed by the signal to get the row data directly.
        self.result_table.selectionModel().currentChanged.connect(self.update_metadata_status)

        # --- Context Menu Setup ---
        self.result_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self.show_context_menu)
        # ---------------------------

        # NEW: Install the event filter on the results table
        self.result_table.installEventFilter(self)

        main_layout.addWidget(self.result_table)

        # Get the database modification status to display as the default text
        initial_status_text = self.get_db_mod_date_status()
        self.status_label = QLabel(initial_status_text)
        # Use the new utility method for initial setup
        self.update_status_display(initial_status_text)

        # --- NEW: Indeterminate Progress Bar for non-blocking operations ---
        self.progress_bar = QProgressBar()
        # Set to indeterminate mode
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(10)
        # Initially hidden
        self.progress_bar.hide()
        self.progress_bar.setFormat(_("Updating database..."))

        # >>> START OF THE CORRECTED LAYOUT SOLUTION (Bar Adjacent to Text) <<<
        status_bar_layout = QHBoxLayout()
        status_bar_layout.setContentsMargins(0, 0, 0, 0)

        # 1. Status Label: Takes only the space needed for its text.
        status_bar_layout.addWidget(self.status_label)

        # 2. Progress Bar: Starts immediately after the status label text.
        status_bar_layout.addWidget(self.progress_bar)

        # 3. NEW: Cancel Button for DB update
        self.cancel_update_btn = QPushButton(_("Cancel Update"))  # Modified text
        self.cancel_update_btn.setIcon(QIcon.fromTheme("dialog-cancel"))
        self.cancel_update_btn.setMaximumWidth(150)  # Increased width
        self.cancel_update_btn.hide()  # Initially hidden
        # Connect the cancel button to a single unified handler
        self.cancel_update_btn.clicked.connect(self.cancel_background_task)
        status_bar_layout.addWidget(self.cancel_update_btn)

        # Add the horizontal layout to the main vertical layout
        main_layout.addLayout(status_bar_layout)

        # --- ACTION BUTTONS CONTAINER ---
        btn_layout = QHBoxLayout()

        # Action Buttons with system icons
        self.open_file_btn = QPushButton(_("Open File"))
        self.open_file_btn.setIcon(QIcon.fromTheme("document-open"))
        self.open_file_btn.setToolTip("ENTER")
        self.open_file_btn.setToolTipDuration(1500)
        self.open_file_btn.clicked.connect(self.open_file)
        btn_layout.addWidget(self.open_file_btn)

        self.open_path_btn = QPushButton(_("Open Folder"))
        self.open_path_btn.setIcon(QIcon.fromTheme("folder-open"))
        self.open_path_btn.setToolTip("CTRL+ENTER")
        self.open_path_btn.setToolTipDuration(1500)
        self.open_path_btn.clicked.connect(self.open_path)
        btn_layout.addWidget(self.open_path_btn)

        self.open_in_terminal_btn = QPushButton(_("Open in Terminal"))
        self.open_in_terminal_btn.setIcon(QIcon.fromTheme("terminal"))
        self.open_in_terminal_btn.setToolTip(_("CTRL+SHIFT+T"))
        self.open_in_terminal_btn.setToolTipDuration(1500)
        self.open_in_terminal_btn.clicked.connect(self.open_in_terminal)
        btn_layout.addWidget(self.open_in_terminal_btn)

        main_layout.addLayout(btn_layout)

        self.setLayout(main_layout)
        self.search_input.setFocus()

    # --- NEW METHOD: Get Database Modification Status ---
    def get_db_mod_date_status(self) -> str:
        """
        Fetches the last modification date of plocate.db and media.db
        and formats them for the status bar.
        """

        def get_date_str(path):
            """Helper to get formatted date or status string for a given path."""
            try:
                # Use os.stat to get file metadata
                stat_result = os.stat(path)
                # Format modification date (st_mtime)
                mod_time = datetime.datetime.fromtimestamp(stat_result.st_mtime)
                return mod_time.strftime('%Y-%m-%d %H:%M:%S')
            except FileNotFoundError:
                return _("Not Found")
            except Exception:
                return _("Error")

        system_date = get_date_str(DEFAULT_DB_PATH)
        media_date = get_date_str(MEDIA_DB_PATH)

        return _("System DB: {sys_date} | Media DB: {media_date}").format(
            sys_date=system_date,
            media_date=media_date
        )

    # --- END NEW METHOD ---

    def update_case_insensitive_text(self):
        """Updates the search button's text based on the internal state."""
        if self.case_insensitive_search:
            # Case Insensitive ON: 'aa' (case doesn't matter)
            self.case_insensitive_btn.setText('aa')
            self.case_insensitive_btn.setToolTip(
                _("Click to activate Case Sensitive (Aa) search\n\nCTRL+SHIFT+C toggles"))
        else:
            # Case Insensitive OFF: 'Aa' (case matters)
            self.case_insensitive_btn.setText('Aa')
            self.case_insensitive_btn.setToolTip(
                _("Click to activate Case Insensitive (-i) search\n\nCTRL+SHIFT+C toggles"))

    def has_uppercase(self, text: str) -> bool:
        """Helper to check if a string contains any uppercase characters."""
        # Using string comparison is usually faster than iterating or using regex for this simple check.
        return text != text.lower()

    def handle_input_case_change(self, text: str):
        """
        Automatically adjusts case-sensitivity based on user input,
        unless the user has explicitly set it manually via the toggle button.
        """

        text = text.strip()

        # 1. Handle an empty search field: always reset to case-insensitive default and automatic mode
        if not text:
            # Reset the manual flag, restoring automatic behavior
            if self._is_manually_case_sensitive:
                self._is_manually_case_sensitive = False

            desired_insensitive = True

        # 2. Check for manual override from the toggle button
        elif self._is_manually_case_sensitive:
            return  # Respect the user's manual choice.

        # 3. Determine the desired case-insensitivity state based on content
        else:
            contains_uppercase = self.has_uppercase(text)
            # If text has uppercase (True), desired_insensitive should be False (case-sensitive).
            desired_insensitive = not contains_uppercase

        # 4. Only change state and update UI/search if necessary
        if desired_insensitive != self.case_insensitive_search:

            # Update the internal state
            self.case_insensitive_search = desired_insensitive

            # [CRITICAL]: Block signals so setChecked() doesn't trigger toggle_case_insensitive()
            self.case_insensitive_btn.blockSignals(True)
            self.case_insensitive_btn.setChecked(not self.case_insensitive_search)
            self.case_insensitive_btn.blockSignals(False)  # Re-enable signals

            # Update the button label and tooltip
            self.update_case_insensitive_text()

            # 5. [BUG FIX]: REMOVE THE IN-MEMORY RE-FILTER CALL.
            # The auto-toggle should only prepare the search, not trigger a re-filter.
            # if text:
            #    if self._raw_plocate_results:
            #          In-memory re-filtering now respects the updated sensitivity
            #        self.run_in_memory_filter(rerun_plocate=False)

    def toggle_case_insensitive(self):
        """
        Toggles the case sensitivity mode and reapplies in-memory filtering
        if results are already loaded, without re-running plocate.
        """
        # The button's check state is already updated by the signal
        self.case_insensitive_search = not self.case_insensitive_btn.isChecked()

        # MODIFICATION: Set manual flag only when forcing Case Sensitive (Aa), allowing auto-detection otherwise.
        self._is_manually_case_sensitive = not self.case_insensitive_search

        # Update dynamic text
        self.update_case_insensitive_text()

        # If there are already loaded results, reapply the in-memory filter only
        if self._raw_plocate_results:
            self.run_in_memory_filter(rerun_plocate=False)

        # Otherwise, if there is search text but no results yet, run a full search
        elif self.search_input.text().strip():
            self.run_search()

    # Slot to handle category change
    def category_changed(self, index):
        """Updates the internal state and handles filtering based on stored results."""
        # The ComboBox returns the translated string, so we pass that to get_category_regex
        selected_category_display_name = self.category_combobox.currentText()
        self.current_category_regex = get_category_regex(selected_category_display_name)

        # NEW BEHAVIOR: If raw results are already stored, we only apply the in-memory filter.
        # This is faster as it avoids the subprocess.run(['plocate', ...]) call.
        if self._raw_plocate_results:
            # Rerunning plocate is set to False as we are only filtering existing data.
            self.run_in_memory_filter(rerun_plocate=False)

            # Original behavior if no results are stored or the main search box has text.
        elif self.search_input.text().strip():
            # The search term is active, so we must rerun the search.
            # NOTE: run_search is needed because the category filter is applied INSIDE
            # the SearchWorker logic before storing in _raw_plocate_results.
            self.run_search()

    def open_documentation(self):
        """Opens the project website/documentation in the system's default browser (F1 shortcut)."""
        DOC_URL = "https://github.com/dmnmsc/plocate-gui"
        QDesktopServices.openUrl(QUrl(DOC_URL))

    # --- STATUS LABEL UTILITY METHOD ---
    def update_status_display(self, text: str):
        """Sets the status label text and sets the tooltip to match the text."""
        self.status_label.setText(text)
        self.status_label.setToolTip(text)  # Tooltip simply echoes the status text

        default_status = self.get_db_mod_date_status()

        if text == default_status:
            # Align right for database update time
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            # Align left for dynamic data (count, metadata, date status)
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    # --- METADATA STATUS METHODS (NON-BLOCKING) ---
    def update_metadata_status(self, current_index, previous_index):
        """
        Called when the table selection changes. Uses the current_index to reliably
        fetch the selected row's metadata in a non-blocking thread.
        """
        # Set a temporary status message
        self.update_status_display(_("Fetching file metadata..."))

        # Check if the index is valid and within bounds
        row = current_index.row()

        if not current_index.isValid() or row < 0 or row >= len(self.model._data):
            # Restore default status (DB dates) if the index is invalid
            self.update_status_display(self.get_db_mod_date_status())
            return

        try:
            # Get the data tuple (name, path, is_dir) directly from the model's internal list using the row index
            name, path, is_dir = self.model._data[row]
        except IndexError:
            self.update_status_display(self.get_db_mod_date_status())
            return

        if name == _("No results found"):
            # Restore the state *before* this selection (usually a result count or date status)
            if self.search_input.text().strip():
                # Display the result count again if there was a search term
                result_count = len(self.model._data)
                if result_count > 0 and self.model._data[0][0] != _("No results found"):
                    status_message = _("Found {} results").format(result_count)
                    self.update_status_display(status_message)
                else:
                    self.update_status_display(_("No results found"))
            else:
                self.update_status_display(self.get_db_mod_date_status())
            return

        full_path = os.path.join(path, name)

        # Track the path being processed to ignore results from older selections
        self.current_stat_path = full_path

        # Create and start the worker thread
        worker = StatWorker(full_path)
        worker.signals.finished.connect(self.display_metadata)

        # Start execution in the thread pool
        self.threadpool.start(worker)

    def display_metadata(self, path, size_str, mod_date_str, success):
        """
        Slot to receive data from the StatWorker and update the status bar.
        This runs on the main GUI thread.
        """
        # CRITICAL: Only update if this result matches the latest selected path
        if path != self.current_stat_path:
            return

        if success:
            # Display only Size and Modified Date
            status_text = _("Size: {size} | Modified: {date}").format(
                size=size_str, date=mod_date_str
            )
        else:
            # Format and display the error message for inaccessible files
            status_text = _("File not accessible (Disk unmounted or I/O error).")

        self.update_status_display(status_text)

    # ----------------------------------------------------

    def update_sort_state(self, logicalIndex):
        """Tracks the current sort state."""
        self.current_sort_column = logicalIndex
        self.current_sort_order = self.result_table.horizontalHeader().sortIndicatorOrder()

    def handle_double_click(self, index: QModelIndex):
        """Handles the double-click event. Opens the file or the containing folder."""
        column = index.column()

        # Distinguish between the 'Name' column (0) to open the file
        # and the 'Path' column (1) to open the containing folder.
        if column == 0:
            self.open_file()
        elif column == 1:
            self.open_path()
        else:
            # Fallback for any other column, defaults to opening the file.
            self.open_file()

    def _apply_responsive_column_sizing(self):
        """
        Adjusts the 'Name' column (index 0) to 40% of the table width
        on-the-fly, ensuring responsive behavior.
        """
        header = self.result_table.horizontalHeader()
        name_col_index = 0

        # 0. Get the visible width of the table's viewport
        table_width = self.result_table.viewport().width()
        if table_width <= 0:
            return

        # 1. Calculate the target width (40% of the table)
        target_width = int(table_width * self.RESPONSIVE_WIDTH_PERCENTAGE)

        # 2. Ensure the minimum width (clamp)
        final_width = max(target_width, self.MIN_NAME_WIDTH)

        # 3. Apply the size (this overrides manual sizing on every resize)
        header.resizeSection(name_col_index, final_width)

    def resizeEvent(self, event):
        """
        Overrides the resize event to dynamically update the 'Name' column size
        ("on-the-fly").
        """
        super().resizeEvent(event)
        # Re-apply the size constraints when the window size changes
        self._apply_responsive_column_sizing()

    # --- NEW: In-Memory Filter Logic (COMBINED WITH CATEGORY SHORTCUT AND MULTI-KEYWORD) ---
    def run_in_memory_filter(self, rerun_plocate=True):
        """
        Filters the raw plocate results based on:
          • The user's additional text in the in-memory filter bar.
          • The main search bar text (used as an extra filter when present).
          • The current selected category and case sensitivity setting.

        This function never calls plocate unless explicitly allowed.
        """

        # 0. Determine the source of the data to filter
        data_to_filter = self._raw_plocate_results

        # 1. Combine filter_input (extra user filters) and search_input (main query)
        filter_text = self.filter_input.text().strip()
        # CRITICAL FIX: Use the term that was *actually* used to run plocate
        # instead of the current text in the main search bar.
        main_search_text = self._last_plocate_term

        # Merge both inputs without overwriting user content
        combined_parts = []
        if filter_text:
            combined_parts.append(filter_text)
        if main_search_text and main_search_text not in combined_parts:
            combined_parts.append(main_search_text)

        # The combined text string used for tokenization
        full_filter_text = " ".join(combined_parts).strip()

        # 2. Tokenize search and extract optional category shortcut (::doc, etc.)
        filter_keywords_list, filter_shortcut_name = tokenize_search_query(full_filter_text)

        # Determine the effective category filter for this execution
        # 1. Start with the currently selected category from the ComboBox
        effective_category_regex = self.current_category_regex

        # 2. If a shortcut is found in the filter text, it overrides the ComboBox selection for this run
        if filter_shortcut_name:
            # a. Get the category regex (the name is already translated by CATEGORY_SHORTCUTS)
            selected_category_display_name = filter_shortcut_name
            effective_category_regex = get_category_regex(selected_category_display_name)

            # b. Visually update the ComboBox (blocking signals to prevent recursive calls)
            index = self.category_combobox.findText(filter_shortcut_name)
            if index != -1 and self.category_combobox.currentIndex() != index:
                self.category_combobox.blockSignals(True)
                self.category_combobox.setCurrentIndex(index)
                self.category_combobox.blockSignals(False)

        # 3. Early exit if no results or no active filters
        is_all_category = effective_category_regex is None
        if not data_to_filter or (not filter_keywords_list and is_all_category):
            if not data_to_filter:
                self.model.set_data([])
                self.update_status_display(self.get_db_mod_date_status())
            else:
                self.model.set_data(data_to_filter)
                status_message = _("Found {} results").format(len(data_to_filter))
                self.update_status_display(status_message)

            # Restore sorting and column sizing
            self._apply_responsive_column_sizing()
            if self.current_sort_column != -1:
                self.model.sort(self.current_sort_column, self.current_sort_order)
                self.result_table.horizontalHeader().setSortIndicator(
                    self.current_sort_column, self.current_sort_order)
            else:
                self.result_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            return

        # 4. Apply category filtering (regex match)
        if effective_category_regex is not None:
            if effective_category_regex == "DIR_ONLY":
                data_to_filter = [
                    (name, path, is_dir) for name, path, is_dir in data_to_filter if is_dir
                ]
            else:
                try:
                    # Note: Category filtering is done case-insensitive (re.IGNORECASE is in get_category_regex)
                    category_filter_regex = re.compile(effective_category_regex, re.IGNORECASE)
                    data_to_filter = [
                        (name, path, is_dir)
                        for name, path, is_dir in data_to_filter
                        if category_filter_regex.search(os.path.join(path, name))
                    ]
                except re.error:
                    pass  # Ignore invalid regex patterns

        # 5. Apply text keyword filtering (respecting case sensitivity)
        filtered_results = []
        if filter_keywords_list:
            for name, path, is_dir in data_to_filter:
                full_path = os.path.join(path, name)

                if self.case_insensitive_search:
                    full_path_cmp = full_path.lower()
                    if all(token.lower() in full_path_cmp for token in filter_keywords_list):
                        filtered_results.append((name, path, is_dir))
                else:
                    if all(token in full_path for token in filter_keywords_list):
                        filtered_results.append((name, path, is_dir))
        else:
            filtered_results = data_to_filter

        # 6. Update the model and status bar
        raw_count = len(self._raw_plocate_results)

        if not filtered_results:
            self.model.set_data([(_("No results match filter"), "", False)])
            self.update_status_display(
                _("No results match filter (filtered from {})").format(raw_count))
        else:
            self.model.set_data(filtered_results)
            status_message = _("Found {} results (filtered from {})").format(
                len(filtered_results), raw_count)
            self.update_status_display(status_message)

            # Restore sorting and column sizing
            self._apply_responsive_column_sizing()
            if self.current_sort_column != -1:
                self.model.sort(self.current_sort_column, self.current_sort_order)
                self.result_table.horizontalHeader().setSortIndicator(
                    self.current_sort_column, self.current_sort_order)
            else:
                self.result_table.horizontalHeader().setSortIndicator(
                    -1, Qt.SortOrder.AscendingOrder)

    def _handle_live_filter_toggle_button(self, is_checked: bool):
        """
        Handles the state change of the Live Filter QPushButton toggle.
        """
        # is_checked is the new state of the button (True/False)
        self.live_filter_enabled = is_checked

        # Update the button text to reflect the new state (ON/OFF)
        self._update_live_filter_text()

        if self.live_filter_enabled:
            # If live filtering is just activated, apply the filter immediately
            self.run_in_memory_filter()

    def _update_live_filter_text(self):
        """Sets the live filter button text based on its internal state (ON/OFF)."""
        if self.live_filter_enabled:
            # The filter is currently ON
            self.live_filter_toggle.setText(_("AUTO"))
        else:
            # The filter is currently OFF
            self.live_filter_toggle.setText(_("ENTER"))

    def _handle_filter_input_change(self):
        """
        Conditional handler for filter_input.textChanged.
        Runs the filter only if self.live_filter_enabled is True.
        When False, the filter is only executed on pressing Enter (via returnPressed).
        """
        if self.live_filter_enabled:
            self.run_in_memory_filter()

    # --- NEW: Non-Blocking Search Runner (Replaces the original blocking logic) ---
    def set_ui_searching_state(self, is_searching: bool):
        """Sets the state of buttons and inputs during search, managing the progress indicator."""

        # Only set if a DB update is NOT in progress
        if self.update_worker is not None:
            return

        is_disabled = is_searching
        self.unified_update_btn.setDisabled(is_disabled)  # Disable update button

        # Set search-specific controls based on search state
        self.search_input.setDisabled(is_disabled)
        self.category_combobox.setDisabled(is_disabled)
        self.case_insensitive_btn.setDisabled(is_disabled)
        self.filter_input.setDisabled(is_disabled)  # NEW: Disable in-memory filter during plocate search

        if is_searching:
            # Show search progress
            self.progress_bar.setFormat(_("Searching..."))
            self.progress_bar.show()
            self.cancel_update_btn.setText(_("Cancel Search"))  # Updated text for context
            self.cancel_update_btn.show()

            # Clear existing metadata/result count message
            self.update_status_display(_("Executing search... Please wait."))
        else:
            # Hide search progress
            self.progress_bar.hide()
            self.cancel_update_btn.hide()
            self.cancel_update_btn.setText(_("Cancel Update"))  # Restore default text

            # The status will be updated by the search_finished slot

    def cancel_background_task(self):
        """Called when the user clicks the 'Cancel' button."""
        if self.update_worker:
            self.update_worker.cancel()
            self.update_status_display(_("Attempting to cancel database update..."))
        elif self.search_worker:
            self.search_worker.cancel()
            self.update_status_display(_("Attempting to cancel search..."))

    def search_finished(self, display_rows: list, message: str, success: bool):
        """
        Slot to receive data from the SearchWorker. Updates the model and UI.
        Runs on the main GUI thread.
        """
        # Release worker reference
        self.search_worker = None

        # Restore UI state (must be called even if search failed)
        self.set_ui_searching_state(False)

        if not success:
            QMessageBox.warning(self, _("Search Error"), message)
            self._raw_plocate_results = []
            self.model.set_data([(_("Search failed"), "", False)])
            self.update_status_display(_("Search failed: ") + message)
            return

        # 1. Store the successful (but pre-in-memory-filtered) results
        self._raw_plocate_results = display_rows

        # 2. Run the in-memory filter to populate the visible table
        self.run_in_memory_filter()

        # Note: Status bar update, sorting, and sizing are now handled inside run_in_memory_filter()
        # because the filter might reduce the displayed count immediately.
        if not display_rows:
            self.model.set_data([(_("No results found"), "", False)])
            self.update_status_display(_("No results found"))
        else:
            self.model.set_data(display_rows)

        # NEW: Move focus to the results table for immediate navigation
        self.result_table.setFocus()

    def run_search(self):
        """
        Parses the query and launches the non-blocking SearchWorker.
        Replaces the original blocking run_search method logic.
        """
        # Clear current selection to prevent 'Enter' from opening the previously selected file
        self.result_table.selectionModel().clearSelection()

        full_query = self.search_input.text().strip()

        # Check if a search is already running
        if self.search_worker is not None:
            # Cancel the old one before starting a new one (or just return for simplicity)
            # For simplicity, we just return if a search is in progress
            # self.search_worker.cancel() # Could implement this, but we'll stick to a simple block for now
            return

        # Split the query into keywords, supporting quotes, and extract the category shortcut
        keywords, category_shortcut_name = tokenize_search_query(full_query)

        # Exit if there are no keywords AND no category shortcut
        if not keywords and not category_shortcut_name:
            self._raw_plocate_results = []
            self.model.set_data([])
            self.update_status_display(self.get_db_mod_date_status())
            return

        # FIX: Store the successful search query for subsequent in-memory filtering
        self._last_plocate_term = full_query

        # 1. Apply Category Shortcut from Search Bar (Update ComboBox State)
        if category_shortcut_name:
            index = self.category_combobox.findText(category_shortcut_name)
            if index != -1:
                self.category_combobox.setCurrentIndex(index)

        # 2. Determine the main plocate term and post-plocate filter terms
        if keywords:
            plocate_term = keywords[0]
            post_plocate_filters = keywords[1:]
        else:
            plocate_term = "."  # Universal term when only a category shortcut is used
            post_plocate_filters = []

        # The category regex comes from the current ComboBox state (set by category_changed or the shortcut block above)
        category_regex = self.current_category_regex

        # 3. Create and launch the worker
        worker = SearchWorker(
            plocate_term,
            post_plocate_filters,
            category_regex,
            self.case_insensitive_search
        )
        self.search_worker = worker  # Store reference for cancellation
        worker.signals.finished.connect(self.search_finished)

        # Set UI state to searching
        self.set_ui_searching_state(True)

        # Start execution in the thread pool
        self.threadpool.start(worker)

    # --- END NEW SEARCH RUNNER ---

    def get_selected_row_data(self):
        """Gets the Name, Path, and is_dir of the selected row via the model."""
        selection_model = self.result_table.selectionModel()
        indexes = selection_model.selectedRows()

        if not indexes:
            return None, None, False

        model_index = indexes[0]
        row = model_index.row()

        try:
            # Retrieve the full tuple (name, path, is_dir)
            name, path, is_dir = self.model._data[row]
        except IndexError:
            return None, None, False

        # Handle the placeholder text case
        if name == _("No results found") or name == _("Search failed") or name == _("No results match filter"):
            return None, None, False

        return name, path, is_dir

    # --- COPY METHODS ---
    def copy_file_name(self):
        """Copies the file/folder name (only) to the clipboard."""
        name, path, is_dir = self.get_selected_row_data()
        if not name:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row to copy."))
            return

        clipboard = QGuiApplication.clipboard()
        clipboard.setText(name)

    def copy_full_path(self):
        """Copies the complete path (path + name) to the clipboard."""
        name, path, is_dir = self.get_selected_row_data()
        if not name or not path:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row to copy."))
            return

        full_path = os.path.join(path, name)
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(full_path)

    # ------------------------------------

    def open_file(self):
        """Opens the selected file/directory using the system's default handler."""
        # Unpack 3 elements
        name, path, is_dir = self.get_selected_row_data()
        if not name or not path:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row."))
            return

        full_path = os.path.join(path, name)
        QDesktopServices.openUrl(QUrl.fromLocalFile(full_path))

    def open_path(self):
        """Opens the containing folder of the selected item."""
        # Unpack 3 elements
        name, path, is_dir = self.get_selected_row_data()
        if not name or not path:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row."))
            return

        # If it's a directory, open its full path; if it's a file, open the parent path ('path')
        path_to_open = os.path.join(path, name) if is_dir and path != os.path.sep else path

        QDesktopServices.openUrl(QUrl.fromLocalFile(path_to_open))

    def open_in_terminal(self):
        """
        Opens the containing folder or file path in the system's preferred terminal
        by trying a sequence of common terminal commands.
        """
        name, path, is_dir = self.get_selected_row_data()
        if not name or not path:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row."))
            return

        # Determine the path to open (parent directory for files, full path for directories)
        full_path = os.path.join(path, name)
        path_to_open_in_terminal = full_path if is_dir else path

        # List of terminal commands and their working directory arguments
        # Note: We rely on the system finding the executable via PATH.
        terminal_configs = [
            ('konsole', '--workdir'),
            ('gnome-terminal', '--working-directory'),
            ('xfce4-terminal', '--working-directory')
        ]

        # Flag to track if the terminal was launched
        launched = False

        # Iterate and try to launch the terminal
        for command, arg in terminal_configs:
            try:
                # Try to launch the terminal, passing the path as the working directory
                subprocess.Popen([command, arg, path_to_open_in_terminal])
                launched = True
                break  # Exit the loop immediately on success
            except FileNotFoundError:
                # Terminal command not found, try the next one
                continue
            except Exception as e:
                # Catch other critical errors (like permission issues)
                QMessageBox.critical(self, _("Terminal Error"),
                                     _("An error occurred while trying to open {cmd}: ").format(cmd=command) + str(e)
                                     )
                return  # Stop trying and exit the function

        # If the loop finished without launching a terminal
        if not launched:
            QMessageBox.warning(self, _("Terminal Error"),
                                _("Could not launch terminal. None of the common terminal commands (gnome-terminal, konsole, xfce4-terminal) were found. Please install one or check your PATH.")
                                )

    # --- METHOD TO SHOW CONTEXT MENU ---
    def show_context_menu(self, pos):
        """Displays the context menu at the given position if a row is selected."""
        selected_rows = self.result_table.selectionModel().selectedRows()

        # Only show menu if a row is selected
        if not selected_rows:
            return

        menu = QMenu(self)

        # 1. Open File
        action_open_file = menu.addAction(QIcon.fromTheme("document-open"), _("Open File (Enter)"))
        action_open_file.triggered.connect(self.open_file)

        # 2. Open Path
        action_open_path = menu.addAction(QIcon.fromTheme("folder-open"), _("Open Folder (Ctrl+Enter)"))
        action_open_path.triggered.connect(self.open_path)

        # 3. Open in Terminal
        action_open_terminal = menu.addAction(QIcon.fromTheme("utilities-terminal"),
                                              _("Open Path in Terminal (Ctrl+Shift+T"))
        action_open_terminal.triggered.connect(self.open_in_terminal)

        menu.addSeparator()

        # 3. Copy File Name
        action_copy_name = menu.addAction(QIcon.fromTheme("edit-copy"), _("Copy File Name"))
        action_copy_name.triggered.connect(self.copy_file_name)

        # 4. Copy Full Path (was "copy path" in request)
        action_copy_path = menu.addAction(QIcon.fromTheme("edit-copy"), _("Copy Full Path"))
        action_copy_path.triggered.connect(self.copy_full_path)

        # Shows the menu at the global mouse position
        menu.exec(self.result_table.mapToGlobal(pos))

    # ----------------------------------------------------

    def keyPressEvent(self, event):
        """
        Handle global key press events, prioritizing search result actions
        (Enter/Ctrl+Enter) when a row is selected.
        """
        selected_rows = self.result_table.selectionModel().selectedRows()
        key = event.key()
        modifiers = event.modifiers()
        is_enter = key in [Qt.Key.Key_Return, Qt.Key.Key_Enter]

        # FIX: Check if the focus is on the results table before processing file opening actions.
        is_table_focused = self.result_table.hasFocus() or self.result_table.viewport().hasFocus()

        # 1. Handle Ctrl + Enter (Opens Path) - PRIORITY
        if is_enter and (modifiers & Qt.KeyboardModifier.ControlModifier) and selected_rows:
            if is_table_focused:
                self.open_path()
                event.accept()
                return

        # 2. Handle Enter/Return key press (Opens File) - DEFAULT
        elif is_enter and selected_rows:
            # Check explicitly that the Control key is NOT pressed to avoid
            # interference with Ctrl+Enter, which may fall through here.
            if is_table_focused:
                if not (modifiers & Qt.KeyboardModifier.ControlModifier):
                    self.open_file()
                    event.accept()
                    return

        # 3. Handle Ctrl + Shift + T (Opens Path in Terminal) - NEW PRIORITY
        is_ctrl_shift_t = (key == Qt.Key.Key_T and
                           (modifiers & Qt.KeyboardModifier.ControlModifier) and
                           (modifiers & Qt.KeyboardModifier.ShiftModifier))

        if is_ctrl_shift_t and selected_rows:
            if is_table_focused:
                self.open_in_terminal()
                event.accept()
                return

        # 4. Handle F5 for database update
        elif key == Qt.Key.Key_F5:
            self.update_unified_database()
            event.accept()
            return

        # 5. Handle F1 for Documentation
        elif key == Qt.Key.Key_F1:
            self.open_documentation()
            event.accept()
            return

        # 6. Handle ctrl+F for search_input focus
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_F:
            self.search_input.setFocus()
            self.search_input.selectAll()
            event.accept()
            return

        # 7. Handle ctrl+G for filter_input focus
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_G:
            self.filter_input.setFocus()
            self.filter_input.selectAll()
            event.accept()
            return

        # 8. Handle Escape key (Cancel task, Clear results, or Close app)
        elif key == Qt.Key.Key_Escape:

            # 1. PRIORITY: Cancel any background task (Update or Search)
            # Checks if either the update worker or the search worker is active.
            if self.update_worker is not None or self.search_worker is not None:
                self.cancel_background_task()
                event.accept()
                return  # Stop here if a task was canceled

            # 2. Clear results and reset search state if results are present
            if len(self.model._data) > 0:
                self.model.set_data([])
                self._raw_plocate_results = []  # Clear raw results as well
                self.search_input.clear()
                self.filter_input.clear()  # NEW: Clear filter input
                self._last_plocate_term = ""
                self.category_combobox.setCurrentIndex(0)
                self.update_status_display(self.get_db_mod_date_status())
                self.search_input.setFocus()
            else:
                # 3. Close the application if no results are visible
                self.close()

            event.accept()
            return

        # 9. Handle Ctrl + Tab (Cycle between search and filter inputs ONLY)
        is_ctrl_tab = (key == Qt.Key.Key_Tab and (modifiers & Qt.KeyboardModifier.ControlModifier))

        if is_ctrl_tab:
            if self.search_input.hasFocus():
                self.filter_input.setFocus()
            elif self.filter_input.hasFocus():
                self.search_input.setFocus()

            # The eventFilter handles the case where the table has focus.
            # We only need to accept if the event was Ctrl+Tab.
            event.accept()
            return

        # 10. Handle Ctrl + Shift + C (Toggle Case Sensitive)
        is_ctrl_shift_c = (key == Qt.Key.Key_C and
                           (modifiers & Qt.KeyboardModifier.ControlModifier) and
                           (modifiers & Qt.KeyboardModifier.ShiftModifier))

        if is_ctrl_shift_c:
            self.case_insensitive_btn.click()
            event.accept()
            return

        # 11. Handle Ctrl + Shift + L (Toggle Auto Filter)
        is_ctrl_shift_l = (key == Qt.Key.Key_L and
                           (modifiers & Qt.KeyboardModifier.ControlModifier) and
                           (modifiers & Qt.KeyboardModifier.ShiftModifier))

        if is_ctrl_shift_l:
            self.live_filter_toggle.click()
            event.accept()
            return

        # 12. Handle Ctrl + Shift + F for category filter focus
        is_ctrl_shift_f = (event.key() == Qt.Key.Key_F and
                           (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                           (event.modifiers() & Qt.KeyboardModifier.ShiftModifier))

        if is_ctrl_shift_f:
            self.category_combobox.showPopup()
            event.accept()
            return

        # Default behavior
        super().keyPressEvent(event)

    def eventFilter(self, source, event):
        """
        Intercepts key press events on the result table to handle Ctrl+Tab
        before the QTableView consumes the Tab key for internal navigation.
        """
        # Only intercept KeyPress events from the results table
        if source == self.result_table and event.type() == QEvent.Type.KeyPress:

            key = event.key()
            modifiers = event.modifiers()

            # Check for Ctrl + Tab
            is_ctrl_tab = (key == Qt.Key.Key_Tab and (modifiers & Qt.KeyboardModifier.ControlModifier))

            if is_ctrl_tab:
                # If we are in the table and Ctrl+Tab is pressed, force focus back to the search input
                self.search_input.setFocus()

                # CRITICAL: Return True to signify the event has been handled and should NOT proceed
                return True

                # For all other events or sources, pass them to the original destination
        return super().eventFilter(source, event)

    # -------------------------------------------------------------------------
    # --- NON-BLOCKING DATABASE UPDATE LOGIC (REPLACEMENT FOR OLD METHODS) ---
    # -------------------------------------------------------------------------

    def set_ui_updating_state(self, is_updating: bool):
        """Sets the state of buttons and inputs during database update, managing the progress indicator."""

        # Disable/Enable all relevant input/action widgets
        is_disabled = is_updating
        self.search_input.setDisabled(is_disabled)
        self.filter_input.setDisabled(is_disabled)  # NEW: Disable in-memory filter
        self.category_combobox.setDisabled(is_disabled)
        self.case_insensitive_btn.setDisabled(is_disabled)
        self.unified_update_btn.setDisabled(is_disabled)

        # Toggle visibility of the status label and the progress bar
        if is_updating:
            # 1. The status label remains visible and shows the progress message
            self.update_status_display(_("Database update in progress... Please wait."))

            # 2. Show the progress bar and cancel button
            self.progress_bar.setFormat(_("Updating database..."))
            self.progress_bar.show()
            self.cancel_update_btn.setText(_("Cancel Update"))  # Ensure correct text
            self.cancel_update_btn.show()  # <-- Show Cancel button
        else:
            # 1. Hide the progress bar and cancel button
            self.progress_bar.hide()
            self.cancel_update_btn.hide()  # <-- Hide Cancel button
            self.cancel_update_btn.setText(_("Cancel Search"))  # Restore search default text

            # 2. Restore the database update status text
            self.update_status_display(self.get_db_mod_date_status())

    def handle_db_update_start(self):
        """Called by a DB worker's signal when it starts. Updates the status message."""
        if self.update_worker:
            # Get the type (e.g., "System" or "Media") from the worker instance
            db_type = self.update_worker.db_type
            self.update_status_display(
                # Use the fetched type in the status message
                _("Starting {db_type} database update... Please enter your password if prompted.").format(
                    db_type=db_type)
            )
        else:
            # Fallback message
            self.update_status_display(_("Starting database update... Please enter your password if prompted."))

    def handle_db_update_finish(self, success: bool, message: str, db_type: str):
        """Called by a DB worker's signal when it finishes. Handles message display."""

        cancellation_message = _("Database update was cancelled by the user.")

        if success:
            # Show a brief success message
            QMessageBox.information(
                self,
                _("Update Completed"),
                _("{db_type} database updated successfully.").format(db_type=db_type)
            )
        elif cancellation_message in message:
            # Handle cancellation message
            QMessageBox.information(self, _("Info"), cancellation_message)
        else:
            # Handle non-cancellation failure
            full_error_message = (
                    _("Could not update {db_type} database.\n").format(db_type=db_type) +
                    _("Details: \n") + message
            )
            QMessageBox.critical(self, _("Update Error"), full_error_message)

    def run_update_worker(self, update_command, db_type: str, next_step_fn=None):
        """Initializes and starts a new UpdateDBWorker in the thread pool."""

        if self.update_worker is not None:
            QMessageBox.information(self, _("Info"), _("A database update is already in progress."))
            return

        # Assign worker immediately
        worker = UpdateDBWorker(update_command, db_type)
        self.update_worker = worker

        # Set UI state to updating immediately on the main thread (block buttons and show progress bar)
        self.set_ui_updating_state(True)

        # Connect signals
        worker.signals.started.connect(self.handle_db_update_start)

        # Signal to handle the final result and optionally call the next step
        def on_finish(success, message, finished_db_type):

            # 1. Release worker reference immediately
            self.update_worker = None

            # 2. Handle message display (including cancellation message)
            self.handle_db_update_finish(success, message, finished_db_type)

            if success and next_step_fn:
                # Success and there is a next step (Media DB), call it.
                next_step_fn()
            else:
                # Failure, Cancellation, OR Success and last step: Restore UI state
                self.set_ui_updating_state(False)

            # If search input is not empty, rerun search to reflect new DB state (optional but good UX)
            if self.search_input.text().strip():
                # Rerun search using the non-blocking runner
                self.run_search()

        worker.signals.finished.connect(on_finish)

        # Start the worker
        self.threadpool.start(worker)

    # custom_excludes_text argument
    def update_system_database(self, custom_excludes_text: str = "", next_step_fn=None):
        """Starts the system DB update worker, respecting custom exclusions."""

        update_command = ["pkexec", "updatedb"]
        exclusion_paths = []

        # Use the argument for exclusion paths
        if custom_excludes_text:
            # Split by space and filter out empty strings
            custom_paths = [p.strip() for p in custom_excludes_text.split() if p.strip()]
            if custom_paths:
                exclusion_paths.extend(custom_paths)

        if exclusion_paths:
            update_command.append("-e")
            update_command.extend(exclusion_paths)

        # Use the unified worker runner
        self.run_update_worker(update_command, _("System"), next_step_fn)

    def update_media_database(self, paths_to_index: str):
        """Starts the media DB update worker, indexing the given paths."""

        # Command: pkexec updatedb -o /var/lib/plocate/media.db -U /path/to/media/
        update_command = ["pkexec", "updatedb", "-o", MEDIA_DB_PATH]

        # Use the provided paths
        index_paths = [p.strip() for p in paths_to_index.split() if p.strip()]

        if not index_paths:
            # Should not happen if dialog default is used, but safe guard.
            QMessageBox.warning(self, _("Error"),
                                _("No paths specified for media indexing. The media database update was skipped."))
            return

        update_command.append("-U")
        update_command.extend(index_paths)

        # Use the unified worker runner, passing None for next_step_fn
        self.run_update_worker(
            update_command,
            _("Media")
        )

    def update_unified_database(self):
        """
        Shows a custom QDialog to confirm update, set options, and then launches
        workers to perform updates in a non-blocking way.
        """

        # Check if an update is already in progress
        if self.update_worker is not None:
            QMessageBox.information(self, _("Info"), _("A database update is already in progress."))
            return

        # 1. Instantiate and run the custom dialog
        dialog = UpdateDatabaseDialog(self, MEDIA_SCAN_PATH)
        result = dialog.exec()

        if result == QDialog.DialogCode.Accepted:
            # 2. Get settings from the dialog
            settings = dialog.get_settings()
            system_update = settings['update_system']
            media_update = settings['update_media']
            custom_excludes_text = settings['exclude_paths']
            media_index_paths = settings['media_index_paths']  # NEW

            # 3. Handle selection logic
            if not system_update and not media_update:
                QMessageBox.information(self, _("Info"), _("No databases selected for update."))
                return

            # Helper function for media update (required because it's chained)
            def start_media_update_chain():
                # We need to pass the paths to the media update function
                self.update_media_database(media_index_paths)

            if system_update and media_update:
                # Case 1: Both databases. Start System, then Media on success.
                self.update_system_database(
                    custom_excludes_text=custom_excludes_text,
                    next_step_fn=start_media_update_chain  # Chain Media update
                )
            elif system_update:
                # Case 2: System only.
                self.update_system_database(
                    custom_excludes_text=custom_excludes_text,
                    next_step_fn=None
                )
            elif media_update:
                # Case 3: Media only.
                start_media_update_chain()  # Start Media update directly
        else:
            # User clicked Cancel or closed the dialog
            QMessageBox.information(self, _("Info"), _("Database update cancelled."))


if __name__ == "__main__":
    # Set the application name for system monitor/taskbar
    QApplication.setApplicationName("Plocate GUI")
    QApplication.setApplicationDisplayName("Plocate GUI")
    QApplication.setDesktopFileName("Plocate GUI")
    app = QApplication(sys.argv)
    window = PlocateGUI()
    window.show()
    sys.exit(app.exec())

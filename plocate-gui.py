#!/usr/bin/env python3
import sys
import subprocess
import re
import gettext
import datetime
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton,
    QTableView, QMessageBox, QHBoxLayout, QHeaderView, QLabel, QCheckBox,
    QMenu, QProgressBar,
    # Imports for the dialog
    QDialog, QDialogButtonBox, QGroupBox
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QUrl,
    # Imports for non-blocking metadata fetching
    QRunnable, QThreadPool, pyqtSignal, QObject
)
from PyQt6.QtGui import QDesktopServices, QIcon, QAction, QGuiApplication
import os

# Gettext configuration for internationalization
_ = gettext.gettext

# Database paths
DEFAULT_DB_PATH = "/var/lib/plocate/plocate.db"
MEDIA_DB_PATH = "/var/lib/plocate/media.db"
MEDIA_SCAN_PATH = "/run/media"


# --- File Size Utility ---
def human_readable_size(size, decimal_places=2):
    """Converts bytes to a human-readable string (KB, MB, GB, etc.)."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


# --- Icon Utility Function ---
def get_icon_for_file_type(filepath: str, is_dir: bool) -> QIcon:
    """Returns a QIcon based on the file extension or if it is a directory."""

    # 1. Directory Icon
    if is_dir:
        return QIcon.fromTheme("folder")

    # 2. Icon based on Common Extensions (using Freedesktop icon naming spec)
    ext = os.path.splitext(filepath)[1].lower()

    if ext in ['.mp3', '.wav', '.ogg', '.flac']:
        return QIcon.fromTheme("audio-x-generic")

    if ext in ['.avi', '.mp4', '.mkv', '.mov']:
        return QIcon.fromTheme("video-x-generic")

    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return QIcon.fromTheme("image-x-generic")

    if ext in ['.pdf']:
        return QIcon.fromTheme("application-pdf")

    if ext in ['.doc', '.docx', '.odt']:
        return QIcon.fromTheme("x-office-document")

    if ext in ['.zip', '.rar', '.7z', '.tar', '.gz']:
        return QIcon.fromTheme("package-x-generic")

    if ext in ['.py', '.sh', '.c', '.cpp', '.html', '.js']:
        return QIcon.fromTheme("text-x-script")

    if ext in ['.txt', '.log', '.md']:
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

        # 2. System DB Group (plocate.db)
        system_group = QGroupBox(_("SYSTEM INDEX (plocate.db)"))
        sys_vbox = QVBoxLayout(system_group)
        sys_vbox.addSpacing(15)

        # Checkbox for System (Highlighted with Bold) - Prepared but not added yet
        self.system_checkbox = QCheckBox(_("Update System Index"))
        self.system_checkbox.setChecked(True)
        self.system_checkbox.setIcon(QIcon.fromTheme("drive-harddisk"))
        # Style to highlight the checkbox
        self.system_checkbox.setStyleSheet("font-weight: bold;")

        # System DB Info: Descriptive text
        system_info = QLabel(
            _("Includes most of the operating system files, excluding external media and temporary directories. This is the primary index."))
        system_info.setWordWrap(True)

        # --- START REORDERING FOR SYSTEM INDEX ---

        # 1. Add Descriptive Text first
        sys_vbox.addWidget(system_info)
        sys_vbox.addSpacing(10)  # Small spacing before the exclusion path section

        # --- Simplified Exclusion Path Integration ---

        # Icon and label for exclusions (in a horizontal layout)
        exclude_label_layout = QHBoxLayout()
        exclude_label_layout.setContentsMargins(0, 0, 0, 0)
        exclude_label_layout.setSpacing(5)

        icon_folder = QLabel()
        icon_folder.setPixmap(QIcon.fromTheme("folder-close").pixmap(16, 16))

        # Simple label without bold for exclusion text
        exclude_label = QLabel(_("Additional Paths to Exclude (updatedb -e):"))

        exclude_label_layout.addWidget(icon_folder)
        exclude_label_layout.addWidget(exclude_label)
        exclude_label_layout.addStretch(1)  # Pushes to the left

        sys_vbox.addLayout(exclude_label_layout)  # Add the label/icon layout

        # Input Field for exclusions
        self.exclude_input = QLineEdit()
        self.exclude_input.setPlaceholderText(_("E.g.: /mnt/backup /tmp"))
        self.exclude_input.setToolTip(
            _("Enter space-separated paths to exclude (e.g., external drives, temporary files). These are additional to system defaults.")
        )
        sys_vbox.addWidget(self.exclude_input)  # Add the input field

        # --- End Simplified Exclusion Path Integration ---

        # 2. Add spacing before the checkbox
        sys_vbox.addSpacing(10)

        # 3. Add Checkbox last
        sys_vbox.addWidget(self.system_checkbox)

        # --- END REORDERING FOR SYSTEM INDEX ---

        main_layout.addWidget(system_group)

        # 3. Media Database Option
        media_group = QGroupBox(_("EXTERNAL MEDIA INDEX (media.db)"))
        media_vbox = QVBoxLayout(media_group)
        media_vbox.addSpacing(15)

        # Checkbox for Media (Highlighted with Bold) - Prepared but not added yet
        self.media_checkbox = QCheckBox(_("Update External Media Index"))
        self.media_checkbox.setChecked(True)
        self.media_checkbox.setIcon(QIcon.fromTheme("media-removable"))
        self.media_checkbox.setStyleSheet("font-weight: bold;")

        media_info = QLabel(_("Scans the directory where most external devices are mounted: ") + f"<b>{media_path}</b>")
        media_info.setOpenExternalLinks(False)
        media_info.setWordWrap(True)

        # --- START REORDERING FOR MEDIA INDEX ---

        # 1. Add Descriptive Text first
        media_vbox.addWidget(media_info)

        # 2. Add spacing before the checkbox
        media_vbox.addSpacing(10)

        # 3. Add Checkbox last
        media_vbox.addWidget(self.media_checkbox)

        # --- END REORDERING FOR MEDIA INDEX ---

        main_layout.addWidget(media_group)

        # 4. Dialog Buttons (QDialogButtonBox)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal
        )

        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Start Update"))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setIcon(QIcon.fromTheme("view-refresh"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Cancel"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setIcon(QIcon.fromTheme("dialog-cancel"))

        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        main_layout.addWidget(self.buttons)

    def get_settings(self):
        """Returns the settings needed by the main window."""
        return {
            'update_system': self.system_checkbox.isChecked(),
            'update_media': self.media_checkbox.isChecked(),
            'exclude_paths': self.exclude_input.text().strip()
        }


# --- END OF CUSTOM DIALOG CLASS (Focus Optimized) ---


class PlocateGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(_("Plocate GUI"))
        self.resize(800, 700)

        # --- Internal State for Preferences and Toggles ---
        self.case_insensitive_search = False
        # --- End Internal State ---

        # Initialize ThreadPool for non-blocking operations
        self.threadpool = QThreadPool()
        # To track the path being currently processed by the worker (prevents race conditions)
        self.current_stat_path = None
        # Reference to the update worker for cancellation
        self.update_worker = None

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

        # Input and Options container
        search_options_layout = QHBoxLayout()

        # Search input with icon and CLEAR BUTTON
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(_("Enter search term..."))
        search_icon = QIcon.fromTheme("edit-find")
        search_action = QAction(search_icon, "", self.search_input)
        # Ensure compatibility with PyQt6 ActionPosition enumeration
        self.search_input.addAction(search_action, QLineEdit.ActionPosition.LeadingPosition)
        self.search_input.returnPressed.connect(self.run_search)
        self.search_input.setClearButtonEnabled(True)
        search_options_layout.addWidget(self.search_input)

        # Case Insensitive Toggle Button (Dynamic Text) - FOR SEARCH
        self.case_insensitive_btn = QPushButton()
        self.case_insensitive_btn.setCheckable(True)  # Make it a toggle button

        # Initialize state and text (Aa = Case Sensitive OFF)
        self.case_insensitive_search = False  # Reverting to default off, since the checkbox version was removed
        self.case_insensitive_btn.setChecked(self.case_insensitive_search)
        self.case_insensitive_btn.setText('Aa')
        self.case_insensitive_btn.setToolTip(
            _("Toggle Case Insensitive Search (-i): Aa = Sensitive | aa = Insensitive"))

        # Set a fixed, slightly larger size for text visibility
        self.case_insensitive_btn.setFixedSize(36, 36)
        # Initial text update based on default state
        self.update_case_insensitive_text()

        # Connect signal: we use clicked() for checkable buttons
        self.case_insensitive_btn.clicked.connect(self.toggle_case_insensitive)

        # Add the compact button to the search layout
        search_options_layout.addWidget(self.case_insensitive_btn)

        main_layout.addLayout(search_options_layout)

        # Filter input (regex) with icon and CLEAR BUTTON
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText(_("Optional filter (space-separated keywords or regex)"))
        filter_icon = QIcon.fromTheme("view-list-details")
        filter_action = QAction(filter_icon, "", self.filter_input)
        # Ensure compatibility with PyQt6 ActionPosition enumeration
        self.filter_input.addAction(filter_action, QLineEdit.ActionPosition.LeadingPosition)
        self.filter_input.returnPressed.connect(self.run_search)
        self.filter_input.setClearButtonEnabled(True)
        main_layout.addWidget(self.filter_input)

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

        # --- Context Menu Setup (NEW) ---
        self.result_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self.show_context_menu)
        # -----------------------------------

        main_layout.addWidget(self.result_table)

        # Instructions/info label -> Replaced by dynamic status label
        self.status_label = QLabel(
            _("Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Ctrl+shift+t opens path in terminal. Right-click for menu."))
        # Use the new utility method for initial setup
        self.update_status_display(self.status_label.text())

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
        self.cancel_update_btn = QPushButton(_("Cancel"))
        self.cancel_update_btn.setIcon(QIcon.fromTheme("dialog-cancel"))
        self.cancel_update_btn.setMaximumWidth(100)
        self.cancel_update_btn.hide()  # Initially hidden
        self.cancel_update_btn.clicked.connect(self.cancel_db_update)
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

        # Update Button with system icon
        self.unified_update_btn = QPushButton(_("Update Database"))
        self.unified_update_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.unified_update_btn.setToolTip(_("Select which database(s) you wish to update."))
        self.unified_update_btn.setToolTipDuration(1500)
        self.unified_update_btn.clicked.connect(self.update_unified_database)
        btn_layout.addWidget(self.unified_update_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def update_case_insensitive_text(self):
        """Updates the search button's text based on the internal state."""
        if self.case_insensitive_search:
            # Case Insensitive ON: 'aa' (case doesn't matter)
            self.case_insensitive_btn.setText('aa')
            self.case_insensitive_btn.setToolTip(
                _("Search is Case Insensitive (-i). Click to toggle to Sensitive (Aa)."))
        else:
            # Case Insensitive OFF: 'Aa' (case matters)
            self.case_insensitive_btn.setText('Aa')
            self.case_insensitive_btn.setToolTip(
                _("Search is Case Sensitive (Aa). Click to toggle to Insensitive (aa)."))

    def toggle_case_insensitive(self):
        """Toggles the internal state, updates the style, and re-runs the search."""

        # The button's check state is already updated by the signal
        self.case_insensitive_search = self.case_insensitive_btn.isChecked()

        # Update dynamic text
        self.update_case_insensitive_text()

        # Rerun search immediately if there is a term
        if self.search_input.text().strip():
            self.run_search()

    def open_documentation(self):
        """Opens the project website/documentation in the system's default browser (F1 shortcut)."""
        DOC_URL = "https://github.com/dmnmsc/plocate-gui"
        QDesktopServices.openUrl(QUrl(DOC_URL))

    # --- STATUS LABEL UTILITY METHOD ---
    def update_status_display(self, text: str):
        """Sets the status label text and automatically sets the tooltip to the same text."""
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

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
        default_instructions = _(
            "Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Ctrl+shift+t opens path in terminal. Right-click for menu."
        )

        if not current_index.isValid() or row < 0 or row >= len(self.model._data):
            # Restore default instruction text if the index is invalid
            self.update_status_display(default_instructions)
            return

        try:
            # Get the data tuple (name, path, is_dir) directly from the model's internal list using the row index
            name, path, is_dir = self.model._data[row]
        except IndexError:
            self.update_status_display(default_instructions)
            return

        if name == _("No results found"):
            self.update_status_display(default_instructions)
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

    def run_search(self):
        term = self.search_input.text().strip()
        raw_filter_pattern = self.filter_input.text().strip()
        final_filter_pattern = ""
        default_instructions = _(
            "Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Ctrl+shift+T opens path in terminal. Right-click for menu."
        )

        if not term:
            self.model.set_data([])
            self.update_status_display(default_instructions)
            return

        # 1. Build the base plocate command
        plocate_command = ["plocate", term]

        # 2. Add case-insensitivity option
        if self.case_insensitive_search:
            plocate_command.insert(1, "-i")

        # 3. Multiple database option (if the media database exists)
        if os.path.exists(MEDIA_DB_PATH):
            # Format: -d /path/to/db1:/path/to/db2
            db_list = f"{DEFAULT_DB_PATH}:{MEDIA_DB_PATH}"
            plocate_command.extend(["-d", db_list])

        try:
            # Run plocate and get output
            result = subprocess.run(
                plocate_command, text=True, capture_output=True, check=True
            )
            files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except subprocess.CalledProcessError as e:
            if e.returncode == 1 and not e.stdout:
                files = []
            else:
                QMessageBox.warning(self, _("Error"), _("Error executing plocate:\n") + str(e))
                return

        # --- Multi-Keyword Filtering Logic ---
        if raw_filter_pattern:
            # Split by space, filter out empty strings (multiple spaces)
            keywords = [k for k in raw_filter_pattern.split() if k]

            if len(keywords) > 1:
                # If multiple space-separated keywords are found, build an AND regex
                # using lookahead assertions: (?=.*keyword1)(?=.*keyword2).*

                # IMPORTANT: Escape all keywords for safety, as they are not meant to be regex patterns
                escaped_keywords = [re.escape(k) for k in keywords]

                # Construct the lookahead pattern
                lookahead_assertions = "".join(f"(?=.*{k})" for k in escaped_keywords)
                final_filter_pattern = f"^{lookahead_assertions}.*$"
            else:
                # If it's a single word or a complex pattern without spaces, use the input directly
                final_filter_pattern = raw_filter_pattern

            # Now apply the constructed (or direct) regex filter
            try:
                # Sticking to simple regex matching on output lines for simplicity.
                regex = re.compile(final_filter_pattern)
                files = [f for f in files if regex.search(f)]
            except re.error:
                QMessageBox.warning(self, _("Error"), _("Filter contains an invalid regex pattern, or multi-keyword conversion failed."))
                return

        display_rows = []
        for filepath in files:
            filepath = filepath.strip()
            if not filepath:
                continue

            # Heuristics: Assume directory if the path ends with a separator (as returned by plocate).
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

        # Populate the table
        if not display_rows:
            # Note: Must pass 3 elements (name, path, is_dir) even for the info row
            self.model.set_data([(_("No results found"), "", False)])
            # Clear metadata status
            self.update_status_display(default_instructions)
        else:
            self.model.set_data(display_rows)

            # Restore sorting if the table was sorted
            if self.current_sort_column != -1:
                self.model.sort(self.current_sort_column, self.current_sort_order)
                self.result_table.horizontalHeader().setSortIndicator(self.current_sort_column, self.current_sort_order)
            else:
                self.result_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

        # Apply the responsive sizing (initial adjustment)
        self._apply_responsive_column_sizing()

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

        if name == _("No results found"):
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
        action_open_terminal = menu.addAction(QIcon.fromTheme("utilities-terminal"), _("Open Path in Terminal"))
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

        # 1. Handle Ctrl + Enter (Opens Path) - PRIORITY
        if is_enter and (modifiers & Qt.KeyboardModifier.ControlModifier) and selected_rows:
            self.open_path()
            event.accept()
            return

        # 2. Handle Enter/Return key press (Opens File) - DEFAULT
        elif is_enter and selected_rows:
            # Check explicitly that the Control key is NOT pressed to avoid
            # interference with Ctrl+Enter, which may fall through here.
            if not (modifiers & Qt.KeyboardModifier.ControlModifier):
                self.open_file()
                event.accept()
                return

        # 3. Handle Ctrl + Shift + T (Opens Path in Terminal) - NEW PRIORITY
        is_ctrl_shift_t = (key == Qt.Key.Key_T and
                           (modifiers & Qt.KeyboardModifier.ControlModifier) and
                           (modifiers & Qt.KeyboardModifier.ShiftModifier))

        if is_ctrl_shift_t and selected_rows:
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

        # 6. Handle Escape key to close the application
        elif key == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return

        # Default behavior
        super().keyPressEvent(event)

    # -------------------------------------------------------------------------
    # --- NON-BLOCKING DATABASE UPDATE LOGIC (REPLACEMENT FOR OLD METHODS) ---
    # -------------------------------------------------------------------------

    def set_ui_updating_state(self, is_updating: bool):
        """Sets the state of buttons and inputs during database update, managing the progress indicator."""

        # Disable/Enable all relevant input/action widgets
        is_disabled = is_updating
        self.search_input.setDisabled(is_disabled)
        self.filter_input.setDisabled(is_disabled)
        self.case_insensitive_btn.setDisabled(is_disabled)
        self.open_file_btn.setDisabled(is_disabled)
        self.open_path_btn.setDisabled(is_disabled)
        self.unified_update_btn.setDisabled(is_disabled)

        # Toggle visibility of the status label and the progress bar
        if is_updating:
            # 1. The status label remains visible and shows the progress message
            self.update_status_display(_("Database update in progress... Please wait."))

            # 2. Show the progress bar and cancel button
            self.progress_bar.setFormat(_("Updating database..."))
            self.progress_bar.show()
            self.cancel_update_btn.show()  # <-- Show Cancel button
        else:
            # 1. Hide the progress bar and cancel button
            self.progress_bar.hide()
            self.cancel_update_btn.hide()  # <-- Hide Cancel button

            # 2. Restore the default instruction text (which was always visible)
            default_instructions = _(
                "Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Ctrl+shift+T opens path in terminal. Right-click for menu."
            )
            self.update_status_display(default_instructions)

    def cancel_db_update(self):
        """Called when the user clicks the 'Cancel' button."""
        if self.update_worker:
            self.update_worker.cancel()
            self.update_status_display(_("Attempting to cancel database update..."))

    def handle_db_update_start(self):
        """Called by a DB worker's signal when it starts. Updates the status message."""
        if self.update_worker:
            # Get the type (e.g., "System" or "Media") from the worker instance
            db_type = self.update_worker.db_type
            self.update_status_display(
                # Use the fetched type in the status message
                _("Starting {db_type} database update... Please enter your password if prompted.").format(db_type=db_type)
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

    def update_media_database(self):
        """Starts the media DB update worker."""

        # Command: pkexec updatedb -o /var/lib/plocate/media.db -U /run/media
        update_command = ["pkexec", "updatedb", "-o", MEDIA_DB_PATH, "-U", MEDIA_SCAN_PATH]

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

            # 3. Handle selection logic
            if not system_update and not media_update:
                QMessageBox.information(self, _("Info"), _("No databases selected for update."))
                return

            if system_update and media_update:
                # Case 1: Both databases. Start System, then Media on success.
                self.update_system_database(
                    custom_excludes_text=custom_excludes_text,
                    next_step_fn=self.update_media_database  # Chain Media update
                )
            elif system_update:
                # Case 2: System only.
                self.update_system_database(
                    custom_excludes_text=custom_excludes_text,
                    next_step_fn=None
                )
            elif media_update:
                # Case 3: Media only.
                self.update_media_database()
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

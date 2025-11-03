#!/usr/bin/env python3
import sys
import subprocess
import re
import gettext
import datetime # Import for date/time handling
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton,
    QTableView, QMessageBox, QHBoxLayout, QHeaderView, QLabel, QCheckBox,
    QMenu  # Added for the context menu
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QUrl,
    # New imports for non-blocking metadata fetching
    QRunnable, QThreadPool, pyqtSignal, QObject
)
from PyQt6.QtGui import QDesktopServices, QIcon, QAction, QGuiApplication
import os

# Set up gettext for internationalization, defaulting to English strings.
# User-facing strings use the _() function for translation.
_ = gettext.gettext

# Database path definitions for clarity
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
            full_path = os.path.join(path, name)
            return get_icon_for_file_type(full_path, is_dir)

        # 3. ToolTip Role
        if role == Qt.ItemDataRole.ToolTipRole:
            return os.path.join(path, name)

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


class PlocateGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(_("Plocate GUI"))
        self.resize(800, 550)

        # New: Initialize ThreadPool for non-blocking operations
        self.threadpool = QThreadPool()
        # To track the path being currently processed by the worker (prevents race conditions)
        self.current_stat_path = None

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

        # Checkbox for case insensitivity with icon
        self.case_insensitive_checkbox = QCheckBox(_("Case insensitive (-i)"))
        self.case_insensitive_checkbox.setIcon(QIcon.fromTheme("view-sort-ascending"))
        self.case_insensitive_checkbox.setToolTip(_("Perform case insensitive search (uses plocate -i)"))
        self.case_insensitive_checkbox.stateChanged.connect(self.run_search)
        search_options_layout.addWidget(self.case_insensitive_checkbox)

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
            _("Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Right-click for menu."))
        # --- STYLE BLOCK ---
        self.status_label.setStyleSheet("""
            QLabel {
                /* Increased font size for slightly better readability */
                font-size: 12px;
                /* Light grey for good visibility on both dark and light themes */
                color: #cccccc; 
                padding: 4px 6px; 
            }
        """)
        # --- END OF STYLE MODIFICATION ---
        main_layout.addWidget(self.status_label)

        # --- CUSTOM EXCLUSION INPUT with icon and CLEAR BUTTON ---
        self.custom_exclude_input = QLineEdit()
        self.custom_exclude_input.setPlaceholderText(_("Paths to exclude (System DB only): E.g.: /mnt/backup /tmp"))
        exclude_icon = QIcon.fromTheme("folder-close")
        exclude_action = QAction(exclude_icon, "", self.custom_exclude_input)
        # Ensure compatibility with PyQt6 ActionPosition enumeration
        self.custom_exclude_input.addAction(exclude_action, QLineEdit.ActionPosition.LeadingPosition)
        self.custom_exclude_input.setToolTip(
            _("Enter space-separated paths to exclude them from the main index (System DB).")
        )
        self.custom_exclude_input.setClearButtonEnabled(True)
        main_layout.addWidget(self.custom_exclude_input)

        # --- ACTION BUTTONS CONTAINER ---
        btn_layout = QHBoxLayout()

        # Action Buttons with system icons
        self.open_file_btn = QPushButton(_("Open File"))
        self.open_file_btn.setIcon(QIcon.fromTheme("document-open"))
        self.open_file_btn.clicked.connect(self.open_file)
        btn_layout.addWidget(self.open_file_btn)

        self.open_path_btn = QPushButton(_("Open Folder"))
        self.open_path_btn.setIcon(QIcon.fromTheme("folder-open"))
        self.open_path_btn.clicked.connect(self.open_path)
        btn_layout.addWidget(self.open_path_btn)

        # Update Button with system icon
        self.unified_update_btn = QPushButton(_("Update Database"))
        self.unified_update_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.unified_update_btn.setToolTip(_("Select which database(s) you wish to update."))
        self.unified_update_btn.clicked.connect(self.update_unified_database)
        btn_layout.addWidget(self.unified_update_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    # --- NEW METADATA STATUS METHODS (NON-BLOCKING) ---
    def update_metadata_status(self, current_index, previous_index):
        """
        Called when the table selection changes. Uses the current_index to reliably
        fetch the selected row's metadata in a non-blocking thread.
        """
        # Set a temporary status message
        self.status_label.setText(_("Fetching file metadata..."))

        # Check if the index is valid and within bounds
        row = current_index.row()
        if not current_index.isValid() or row < 0 or row >= len(self.model._data):
            # Restore default instruction text if the index is invalid
            self.status_label.setText(
                _("Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Right-click for menu."))
            return

        try:
            # FIX: Get the data tuple (name, path, is_dir) directly from the model's internal list using the row index
            name, path, is_dir = self.model._data[row]
        except IndexError:
            self.status_label.setText(
                _("Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Right-click for menu."))
            return

        if name == _("No results found"):
            self.status_label.setText(
                _("Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Right-click for menu."))
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

        self.status_label.setText(status_text)
    # ----------------------------------------------------


    def update_sort_state(self, logicalIndex):
        """Tracks the current sort state."""
        self.current_sort_column = logicalIndex
        self.current_sort_order = self.result_table.horizontalHeader().sortIndicatorOrder()

    def handle_double_click(self, index: QModelIndex):
        """Handles the double-click event. Opens the file or the containing folder."""
        column = index.column()

        # Double-click on Name column (0) or Path column (1) defaults to opening the file
        if column == 0 or column == 1:
            self.open_file()
        else:
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

        if not term:
            self.model.set_data([])
            return

        # 1. Build the base plocate command
        plocate_command = ["plocate", term]

        # 2. Add case-insensitivity option
        if self.case_insensitive_checkbox.isChecked():
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

        # --- NEW Multi-Keyword Filtering Logic ---
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
            self.status_label.setText(
                _("Double click to open. Enter/Return opens file. Ctrl+Enter opens path. Right-click for menu."))
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

    # --- NEW COPY METHODS ---
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

    # --- METHOD TO SHOW CONTEXT MENU (NEW) ---
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

        # 3. Handle F5 for database update
        elif key == Qt.Key.Key_F5:
            self.update_unified_database()
            event.accept()
            return

        # 4. Handle Escape key to close the application
        elif key == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return

        # Default behavior
        super().keyPressEvent(event)

    def run_updatedb_command(self, update_command, message):
        """Helper function to execute the updatedb command. Returns success (True/False)."""
        # The user will rely on the final success/error dialogs shown in the calling function.

        try:
            # Execute the command
            subprocess.run(
                update_command,
                text=True,
                capture_output=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError as e:
            error_details = e.stderr or e.stdout or _("No detailed error message was returned.")
            full_error_message = (
                    _("Could not update database:\n") +
                    _("Command: ") + " ".join(update_command) +
                    _("\nExit Status: ") + str(e.returncode) +
                    _("\nDetails: \n") + error_details.strip()
            )
            # Use critical for errors
            QMessageBox.critical(self, _("Update Error"), full_error_message)
            return False
        except FileNotFoundError:
            QMessageBox.critical(self, _("Execution Error"),
                                 _("The 'pkexec' command was not found. "
                                   "Please ensure 'polkit' is installed and configured."))
            return False

    def update_system_database(self):
        """Updates the main (System) database, respecting custom exclusions."""
        update_command = ["pkexec", "updatedb"]
        exclusion_paths = []
        messages = []

        # 1. Check custom exclusion paths
        custom_excludes_text = self.custom_exclude_input.text().strip()
        if custom_excludes_text:
            custom_paths = [p.strip() for p in custom_excludes_text.split() if p.strip()]
            if custom_paths:
                exclusion_paths.extend(custom_paths)
                messages.append(_("excluding custom paths"))

        if exclusion_paths:
            update_command.append("-e")
            update_command.extend(exclusion_paths)

            message_suffix = ", ".join(messages)
            message = _("System DB update started ({s}).").format(s=message_suffix)
        else:
            message = _("System DB update started (using default configuration).")

        return self.run_updatedb_command(update_command, message)

    def update_media_database(self):
        """Creates or updates the secondary database for /run/media."""

        # Command: pkexec updatedb -o /var/lib/plocate/media.db -U /run/media
        update_command = ["pkexec", "updatedb", "-o", MEDIA_DB_PATH, "-U", MEDIA_SCAN_PATH]

        message = _("Media DB update started (Indexing {path})").format(path=MEDIA_SCAN_PATH)

        return self.run_updatedb_command(update_command, message)

    def update_unified_database(self):
        """
        Shows a dialog to confirm update and optionally include external media.
        """

        choice = QMessageBox(self)
        choice.setWindowTitle(_("Update Database"))

        # Add HTML line breaks to the main text to separate it visually from the informative text
        main_text = _("Are you sure you want to update the System database?") + "<br>"
        choice.setText(main_text)

        choice.setInformativeText(_("This operation requires root privileges and may take some time."))

        # Create the custom checkbox
        media_checkbox = QCheckBox(_("Include external media ({path})").format(path=MEDIA_SCAN_PATH), choice)
        media_checkbox.setChecked(True)

        # Insert the checkbox into the QMessageBox layout
        layout = choice.layout()
        row_count = layout.rowCount()
        # Add the checkbox just before the button row, spanning all columns
        layout.addWidget(media_checkbox, row_count, 0, 1, layout.columnCount())

        # --- Custom Buttons with Icons for OK/Cancel ---
        ok_button = QPushButton(_("OK"))
        ok_button.setIcon(QIcon.fromTheme("dialog-ok-apply"))  # Icon for confirmation

        cancel_button = QPushButton(_("Cancel"))
        cancel_button.setIcon(QIcon.fromTheme("dialog-cancel"))  # Icon for cancellation

        # Add custom buttons (this replaces the standard buttons in the dialog)
        choice.addButton(ok_button, QMessageBox.ButtonRole.AcceptRole)
        choice.addButton(cancel_button, QMessageBox.ButtonRole.RejectRole)
        choice.setDefaultButton(QMessageBox.StandardButton.Ok)  # Set default focus

        # Execute the dialog
        choice.exec()
        clicked_button = choice.clickedButton()

        # Check which button object was clicked
        if clicked_button == ok_button:
            media_update = media_checkbox.isChecked()

            final_message = []

            # 1. Update System DB (Mandatory if OK is pressed)
            if self.update_system_database():
                final_message.append(_("System database updated successfully."))
            else:
                return  # Error message already shown

            # 2. Update Media DB (Conditional on checkbox)
            if media_update:
                if self.update_media_database():
                    final_message.append(_("Media database updated successfully."))
                else:
                    return  # Error message already shown

            # 3. Final Success
            if final_message:
                QMessageBox.information(
                    self, _("Update Completed"), "\n".join(final_message)
                )
        else:
            # User clicked Cancel or closed the dialog
            QMessageBox.information(self, _("Info"), _("Database update cancelled."))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Set translation for '_' to work correctly.
    # This is essential for language handling in desktop environments.

    window = PlocateGUI()
    window.show()
    sys.exit(app.exec())

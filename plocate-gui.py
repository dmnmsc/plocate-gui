#!/usr/bin/env python3
import sys
import subprocess
import re
import gettext
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton,
    QTableView, QMessageBox, QHBoxLayout, QHeaderView, QLabel, QCheckBox
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QUrl
)
from PyQt6.QtGui import QDesktopServices, QIcon
import os

# Set up gettext for internationalization, defaulting to English strings
_ = gettext.gettext

# Database path definitions for clarity
DEFAULT_DB_PATH = "/var/lib/plocate/plocate.db"
MEDIA_DB_PATH = "/var/lib/plocate/media.db"
MEDIA_SCAN_PATH = "/run/media"


# Model implementation for QTableView
class PlocateResultsModel(QAbstractTableModel):
    """Data model for QTableView storing plocate results."""

    def __init__(self, data=None, parent=None):
        super().__init__(parent)
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

        value = str(self._data[row][col])

        # Roles needed for display and sorting
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            # Data is stored as a list of tuples (name, path)
            return value

        # ROLE: Displays the full cell text when the mouse hovers over it.
        if role == Qt.ItemDataRole.ToolTipRole:
            return value

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

        # Try to load the icon from the system theme (for the installed version).
        icon = QIcon.fromTheme("plocate-gui")

        #    try to load it from the relative 'resources' path.
        if icon.isNull():
            source_path = os.path.join(os.path.dirname(__file__), 'resources', 'plocate-gui.svg')
            if os.path.exists(source_path):
                icon = QIcon(source_path)

        # 3. Apply the icon if a valid one was found.
        if not icon.isNull():
            self.setWindowIcon(icon)

            # Define the desired percentage for the 'Name' column (Column 0)
        self.RESPONSIVE_WIDTH_PERCENTAGE = 0.40  # 40% of the table width
        self.MIN_NAME_WIDTH = 150  # Minimum width to prevent the column from collapsing

        self.current_sort_column = -1
        self.current_sort_order = Qt.SortOrder.AscendingOrder

        main_layout = QVBoxLayout()

        # Input and Options container
        search_options_layout = QHBoxLayout()

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(_("Enter search term..."))
        self.search_input.returnPressed.connect(self.run_search)
        search_options_layout.addWidget(self.search_input)

        # Checkbox for case insensitivity
        self.case_insensitive_checkbox = QCheckBox(_("Case insensitive (-i)"))
        self.case_insensitive_checkbox.setToolTip(_("Perform case insensitive search (uses plocate -i)"))
        self.case_insensitive_checkbox.stateChanged.connect(self.run_search)
        search_options_layout.addWidget(self.case_insensitive_checkbox)

        main_layout.addLayout(search_options_layout)

        # Filter input (regex)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText(_("Optional filter (regex)"))
        self.filter_input.returnPressed.connect(self.run_search)
        main_layout.addWidget(self.filter_input)

        # Results table setup
        self.model = PlocateResultsModel()
        self.result_table = QTableView()
        self.result_table.setModel(self.model)

        self.result_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.result_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

        header = self.result_table.horizontalHeader()
        # Keep Interactive for manual resizing, though resizeEvent will override it dynamically
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # Keep StretchLastSection so the 'Path' column fills the remaining space (60%)
        header.setStretchLastSection(True)

        self.result_table.setSortingEnabled(True)
        header.sectionClicked.connect(self.update_sort_state)
        self.result_table.doubleClicked.connect(self.handle_double_click)

        main_layout.addWidget(self.result_table)

        # Instructions/info label
        info_label = QLabel(
            _("Double click to open. Search automatically combines system and media databases if both exist."))
        info_label.setStyleSheet("color: gray; font-size: 11px;")
        main_layout.addWidget(info_label)

        # --- CUSTOM EXCLUSION INPUT ---
        self.custom_exclude_input = QLineEdit()
        self.custom_exclude_input.setPlaceholderText(_("Paths to exclude (System DB only): E.g.: /mnt/backup /tmp"))
        self.custom_exclude_input.setToolTip(
            _("Enter space-separated paths to exclude them from the main index (System DB).")
        )
        main_layout.addWidget(self.custom_exclude_input)

        # --- UPDATE BUTTONS AND OPTIONS CONTAINER ---
        btn_layout = QHBoxLayout()

        # Action Buttons
        self.open_file_btn = QPushButton(_("Open File"))
        self.open_file_btn.clicked.connect(self.open_file)
        btn_layout.addWidget(self.open_file_btn)

        self.open_path_btn = QPushButton(_("Open Folder"))
        self.open_path_btn.clicked.connect(self.open_path)
        btn_layout.addWidget(self.open_path_btn)

        # Single Update Button
        self.unified_update_btn = QPushButton(_("Update Database"))
        self.unified_update_btn.setToolTip(_("Select which database(s) you wish to update."))
        self.unified_update_btn.clicked.connect(self.update_unified_database)
        btn_layout.addWidget(self.unified_update_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def update_sort_state(self, logicalIndex):
        """Tracks the current sort state."""
        self.current_sort_column = logicalIndex
        self.current_sort_order = self.result_table.horizontalHeader().sortIndicatorOrder()

    def handle_double_click(self, index: QModelIndex):
        """Handles the double-click event. Opens the file or the containing folder."""
        column = index.column()

        if column == 0:
            self.open_file()
        elif column == 1:
            self.open_path()
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
            return  # Avoid errors or incorrect calculations

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
        filter_pattern = self.filter_input.text().strip()

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

        if filter_pattern:
            try:
                regex = re.compile(filter_pattern)
                files = [f for f in files if regex.search(f)]
            except re.error:
                QMessageBox.warning(self, _("Error"), _("Filter contains an invalid regex pattern."))
                return

        display_rows = []
        for filepath in files:
            filepath = filepath.strip()
            if not filepath:
                continue

            if filepath == os.path.sep:
                name = os.path.sep
                parent = ""
            else:
                temp_path = filepath.rstrip(os.path.sep)
                parent, name = os.path.split(temp_path)

            if not parent:
                parent = os.path.sep

            display_rows.append((name, parent))

        # Populate the table
        if not display_rows:
            self.model.set_data([(_("No results found"), "")])
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
        """Gets the Name and Path of the selected row via the model."""
        selection_model = self.result_table.selectionModel()
        indexes = selection_model.selectedRows()

        if not indexes:
            return None, None

        model_index = indexes[0]
        row = model_index.row()

        name_index = self.model.index(row, 0)
        path_index = self.model.index(row, 1)

        name = self.model.data(name_index, Qt.ItemDataRole.DisplayRole)
        path = self.model.data(path_index, Qt.ItemDataRole.DisplayRole)

        if name == _("No results found"):
            return None, None

        return name, path

    def open_file(self):
        name, path = self.get_selected_row_data()
        if not name or not path:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row."))
            return

        full_path = os.path.join(path, name)
        QDesktopServices.openUrl(QUrl.fromLocalFile(full_path))

    def open_path(self):
        name, path = self.get_selected_row_data()
        if not name or not path:
            QMessageBox.information(self, _("Info"), _("Please select a valid result row."))
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def keyPressEvent(self, event):
        """Handle global key press events."""
        if event.key() == Qt.Key.Key_F5:
            self.update_unified_database()
        else:
            super().keyPressEvent(event)

    def run_updatedb_command(self, update_command, message):
        """Helper function to execute the updatedb command. Returns success (True/False)."""
        # The user will rely on the final success/error dialogs.

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

        message = _("Media DB update started (Indexing {path}).").format(path=MEDIA_SCAN_PATH)

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
        # Include a placeholder for the MEDIA_SCAN_PATH for clarity
        media_checkbox = QCheckBox(_("Include external media ({path})").format(path=MEDIA_SCAN_PATH), choice)
        media_checkbox.setChecked(True)  # Default to checked

        # Insert the checkbox into the QMessageBox layout
        # We need to access the layout to add a custom widget before the buttons
        layout = choice.layout()
        row_count = layout.rowCount()

        # Add the checkbox just before the button row, spanning all columns
        layout.addWidget(media_checkbox, row_count, 0, 1, layout.columnCount())

        # Set standard OK/Cancel buttons.
        choice.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        choice.setDefaultButton(QMessageBox.StandardButton.Ok)

        result = choice.exec()

        if result == QMessageBox.StandardButton.Ok:
            system_update = True
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
            # User clicked Cancel
            QMessageBox.information(self, _("Info"), _("Database update cancelled."))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Set translation for '_' to work correctly
    # This is essential for language handling in desktop environments.

    window = PlocateGUI()
    window.show()
    sys.exit(app.exec())

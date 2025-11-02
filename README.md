# Plocate GUI

A simple GUI for the `plocate` command-line utility on Linux systems. Built with PyQt6.

<div align="center">
  <img src="screenshots/plocate-gui.png" alt="plocate-gui main window"  width="70%" />
</div>

## Features

* **Fast Search:** Leverages the optimized `plocate` database.

* **Dual Database:** Searches simultaneously in the main system database (`/var/lib/plocate/plocate.db`) and an optional database for external media (`/var/lib/plocate/media.db`).

* **Search Options:** Supports case-insensitive search (`-i`) and filtering by regular expressions.

* **DB Update:** Capability to execute `updatedb` (using `pkexec`) to update the system index and/or the external media index (`/run/media`).

* **Navigation:** Double-click to open the file or the containing folder.

* **Responsive Design:** Automatic adjustment of table column sizes when resizing the window.

## Requirements

* Python 3.x

* The `plocate` package.

* PyQt6 libraries.

## Database Usage

The application is configured to work with the following default databases:

* **System:** `/var/lib/plocate/plocate.db` (updated with `pkexec updatedb`).

* **External Media:** `/var/lib/plocate/media.db` (created/updated with `pkexec updatedb -o ... -U /run/media`).

**Important Note:** Database updates (`Update Database`) require `pkexec` (provided by the **polkit** package) to be installed and configured to request root permissions, as the `updatedb` command needs privileges.
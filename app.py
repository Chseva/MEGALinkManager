import threading
import queue
import json
import os
import subprocess
import sys

# Prevent child MEGAcmd command windows from flashing on Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
import shutil
import re
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from mega_manager import MegaManager

APP_VERSION = "1.0"


def resource_path(filename: str) -> Path:
    """Return a bundled asset path for source and PyInstaller launches."""
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_dir / filename


class MegaLinkManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"MEGA Link Manager v{APP_VERSION}")
        self._set_window_icon()
        self.root.geometry("1000x850")
        self.root.minsize(850, 650)

        self.mega = None
        self.busy = False
        self.sticky_parent = None
        self.megacmd_ready = False
        self.megacmd_logged_in = False
        self.megacmd_check_running = False
        self.megacmd_result_queue = queue.Queue()

        # Column sorting state.
        self.sort_column = None
        self.sort_reverse = False
        self.folder_dates = {}

        self.config_dir = (
            Path.home()
            / "AppData"
            / "Local"
            / "MEGALinkManager"
        )
        self.config_file = self.config_dir / "config.json"
        self.current_path = "/"

        self.build_interface()

        # Start the MEGAcmd check after the GUI is displayed. This prevents
        # a slow MEGAcmdServer startup from making the application window
        # appear frozen or invisible.
        self.root.after(
            100,
            self.detect_megacmd
        )

        self.root.after(
            100,
            self._poll_megacmd_results
        )

    def _set_window_icon(self):
        """Use the app icon in the title bar as well as the EXE icon."""
        icon_path = resource_path("MEGALinkManager.ico")
        if icon_path.is_file():
            self.root.iconbitmap(default=str(icon_path))

    def build_interface(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main, text="MEGA Link Manager",
            font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", pady=(0, 12))

        connection_frame = ttk.LabelFrame(
            main, text="MEGA Connection", padding=10
        )
        connection_frame.pack(fill="x", pady=(0, 10))

        self.connection_icon = ttk.Label(
            connection_frame,
            text="•",
            font=("TkDefaultFont", 10, "bold")
        )
        self.connection_icon.pack(
            side="left",
            padx=(0, 5)
        )

        self.connection_label = ttk.Label(
            connection_frame,
            text="Checking MEGAcmd..."
        )
        self.connection_label.pack(
            side="left",
            anchor="w",
            fill="x",
            expand=True
        )

        # Manual session recheck is always available.
        self.connection_retry_button = ttk.Button(
            connection_frame,
            text="Retry",
            command=self.detect_megacmd
        )
        self.connection_retry_button.pack(
            side="right",
            padx=(10, 0)
        )

        # Only shown when MEGAcmd is installed but the user is not logged in.
        self.connection_open_button = ttk.Button(
            connection_frame,
            text="Open MEGAcmd",
            command=self.open_megacmd
        )
        # Keep it hidden until the logged-out state is detected.

        path_frame = ttk.LabelFrame(
            main, text="MEGA Folder", padding=10
        )
        path_frame.pack(fill="x", pady=(0, 10))

        path_row = ttk.Frame(path_frame)
        path_row.pack(fill="x")

        self.path_entry = ttk.Entry(path_row)
        self.path_entry.pack(side="left", fill="x", expand=True)

        self.current_path = self.load_saved_path()
        self.path_entry.insert(0, self.current_path)

        self.path_entry.bind(
            "<Return>",
            lambda event: self.refresh_folders()
        )

        self.up_button = ttk.Button(
            path_row,
            text="↑ Up",
            command=self.navigate_up
        )
        self.up_button.pack(side="left", padx=(10, 5))

        self.refresh_button = ttk.Button(
            path_row, text="Refresh", command=self.refresh_folders
        )
        self.refresh_button.pack(side="left", padx=(10, 0))

        breadcrumb_frame = ttk.Frame(main)
        breadcrumb_frame.pack(
            fill="x",
            pady=(0, 8)
        )

        ttk.Label(
            breadcrumb_frame,
            text="Location:"
        ).pack(side="left", padx=(0, 8))

        self.breadcrumb_inner = ttk.Frame(
            breadcrumb_frame
        )
        self.breadcrumb_inner.pack(
            side="left",
            fill="x",
            expand=True
        )

        self.update_breadcrumbs(self.current_path)

        folder_frame = ttk.LabelFrame(
            main, text="MEGA Collections", padding=10
        )
        folder_frame.pack(fill="both", expand=True, pady=(0, 10))

        tree_container = ttk.Frame(folder_frame)
        tree_container.pack(fill="both", expand=True)

        self.folder_tree = ttk.Treeview(
            tree_container,
            columns=("date", "status_info"),
            show="tree headings",
            selectmode="extended"
        )

        self.folder_tree.heading(
            "#0",
            text="Folder Name",
            command=lambda: self.sort_tree("name")
        )

        self.folder_tree.heading(
            "date",
            text="Date",
            command=lambda: self.sort_tree("date")
        )

        self.folder_tree.heading(
            "status_info",
            text="Status / Info"
        )

        self.folder_tree.column(
            "#0",
            width=380,
            minwidth=200,
            stretch=True
        )

        self.folder_tree.column(
            "date",
            width=170,
            minwidth=130,
            stretch=False
        )

        self.folder_tree.column(
            "status_info",
            width=300,
            minwidth=180,
            stretch=False
        )

        self.folder_tree.pack(side="left", fill="both", expand=True)

        self.tree_scrollbar = ttk.Scrollbar(
            tree_container,
            orient="vertical",
            command=self.folder_tree.yview
        )
        self.tree_scrollbar.pack(side="right", fill="y")

        self.folder_tree.configure(
            yscrollcommand=self.tree_scroll_changed
        )

        # Sticky parent overlay
        self.sticky_frame = tk.Frame(
            tree_container, bd=1, relief="solid"
        )

        self.sticky_toggle = tk.Label(
            self.sticky_frame,
            text="−",
            width=2,
            anchor="center",
            cursor="hand2"
        )
        self.sticky_toggle.pack(side="left")

        self.sticky_name = tk.Label(
            self.sticky_frame, anchor="w"
        )
        self.sticky_name.pack(side="left", fill="both", expand=True)

        self.sticky_date = tk.Label(
            self.sticky_frame, anchor="w", width=24
        )
        self.sticky_date.pack(side="left")

        self.sticky_status_info = tk.Label(
            self.sticky_frame, anchor="w", width=45
        )
        self.sticky_status_info.pack(side="left")

        self.sticky_toggle.bind(
            "<Button-1>", self.on_sticky_toggle
        )
        self.sticky_frame.place_forget()

        self.folder_tree.bind(
            "<Double-1>", self.on_tree_double_click
        )

        self.folder_tree.bind(
            "<<TreeviewOpen>>", self.on_tree_changed
        )
        self.folder_tree.bind(
            "<<TreeviewClose>>", self.on_tree_changed
        )
        self.folder_tree.bind(
            "<<TreeviewSelect>>", self.on_tree_selection_changed
        )
        self.folder_tree.bind(
            "<Button-1>", self.on_tree_click
        )

        action_frame = ttk.Frame(main)
        action_frame.pack(fill="x", pady=(0, 8))

        self.renew_button = ttk.Button(
            action_frame,
            text="Renew Links",
            command=self.renew_selected
        )
        self.renew_button.pack(side="left", padx=(0, 8))

        self.export_button = ttk.Button(
            action_frame,
            text="Export Links",
            command=self.export_selected
        )
        self.export_button.pack(side="left")

        self.selection_var = tk.StringVar(
            value="No collection selected"
        )
        ttk.Label(
            main,
            textvariable=self.selection_var
        ).pack(anchor="w", pady=(0, 8))

        summary_frame = ttk.LabelFrame(
            main, text="Summary", padding=8
        )
        summary_frame.pack(fill="x", pady=(0, 10))

        self.total_var = tk.StringVar(value="Total: 0")
        self.renewed_var = tk.StringVar(value="Renewed: 0")
        self.skipped_var = tk.StringVar(value="Skipped: 0")
        self.errors_var = tk.StringVar(value="Errors: 0")

        for variable in (
            self.total_var,
            self.renewed_var,
            self.skipped_var,
            self.errors_var
        ):
            ttk.Label(
                summary_frame, textvariable=variable
            ).pack(side="left", padx=(0, 25))

        self.progress = ttk.Progressbar(
            main, mode="determinate"
        )
        self.progress.pack(fill="x")

        self.progress_label = ttk.Label(
            main, text="Ready"
        )
        self.progress_label.pack(anchor="w", pady=(3, 8))

        self.creator_info_frame = ttk.Frame(main)
        self.creator_info_frame.pack(
            fill="x",
            pady=(4, 0)
        )

        self.creator_info_button = ttk.Button(
            self.creator_info_frame,
            text="ⓘ",
            width=3,
            command=self.show_creator_info
        )
        self.creator_info_button.pack(
            side="right"
        )

        activity_frame = ttk.LabelFrame(
            main, text="Activity", padding=8
        )
        activity_frame.pack(fill="both", expand=False)

        self.status_text = tk.Text(
            activity_frame,
            height=8,
            state="disabled",
            font=("Consolas", 9)
        )
        self.status_text.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            main, textvariable=self.status_var
        ).pack(anchor="w", pady=(7, 0))

        self.root.bind(
            "<Configure>", self.on_window_resize
        )

    # ---------------------------------------------------------
    # Selection handling
    # ---------------------------------------------------------

    def on_tree_click(self, event):
        if self.busy:
            return

        item = self.folder_tree.identify_row(event.y)

        if not item:
            return

        # Child folders are independently selectable.
        # The normal Treeview selection behavior is preserved so
        # users can select one or multiple child folders.
        self.root.after_idle(
            self.update_selection_display
        )

    def on_tree_selection_changed(self, event=None):
        self.root.after_idle(self.update_selection_display)
        self.root.after_idle(self.update_sticky_parent)

    def get_selected_folder_count(self):
        total = 0

        for item, _ in self.get_selected_collections():
            real_children = [
                child
                for child in self.folder_tree.get_children(item)
                if not child.startswith("placeholder::")
            ]
            total += len(real_children)

        return total

    def update_selection_display(self):
        collections, children = self.get_selected_items()

        if not collections and not children:
            self.selection_var.set(
                "No folder selected"
            )
            self.total_var.set("Total: 0")
            return

        details = []

        for item, name in collections:
            values = self.folder_tree.item(
                item, "values"
            )
            count_text = (
                values[1]
                if len(values) >= 2
                else ""
            )

            if count_text:
                details.append(
                    f"{name} — {count_text}"
                )
            else:
                details.append(name)

        if collections and children:
            self.selection_var.set(
                f"Selected {len(collections)} collection(s) "
                f"and {len(children)} folder(s)"
            )
        elif collections:
            if len(collections) == 1:
                self.selection_var.set(
                    f"Selected collection: {details[0]}"
                )
            else:
                self.selection_var.set(
                    f"Selected {len(collections)} collections: "
                    + ", ".join(details)
                )
        else:
            if len(children) == 1:
                child_name = children[0][1]
                parent = self.get_child_collection(
                    children[0][0]
                )
                parent_name = (
                    self.folder_tree.item(parent, "text")
                    if parent
                    else ""
                )

                if parent_name:
                    self.selection_var.set(
                        f"Selected folder: "
                        f"{parent_name} / {child_name}"
                    )
                else:
                    self.selection_var.set(
                        f"Selected folder: {child_name}"
                    )
            else:
                self.selection_var.set(
                    f"Selected {len(children)} folders"
                )

        # Total represents the number of actual folders that the
        # current selection can operate on.
        selected_count = 0

        for item, _ in collections:
            real_children = [
                child
                for child in self.folder_tree.get_children(item)
                if not child.startswith("placeholder::")
            ]
            selected_count += len(real_children)

        selected_count += len(children)

        self.total_var.set(
            f"Total: {selected_count}"
        )

    # ---------------------------------------------------------
    # Sticky parent
    # ---------------------------------------------------------

    def on_window_resize(self, event=None):
        self.root.after_idle(self.update_sticky_parent)

    def tree_scroll_changed(self, first, last):
        self.tree_scrollbar.set(first, last)
        self.root.after_idle(self.update_sticky_parent)

    def on_tree_changed(self, event=None):
        self.root.after_idle(self.update_sticky_parent)

    # ---------------------------------------------------------
    # Sorting
    # ---------------------------------------------------------

    def sort_tree(self, column):
        if self.busy:
            return

        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column

            # Name: A-Z first.
            # Date: latest-to-oldest first.
            self.sort_reverse = False

        self._sort_children("")
        self.update_sort_headings()
        self.root.after_idle(self.update_sticky_parent)

    def _sort_children(self, parent):
        items = list(self.folder_tree.get_children(parent))

        if not items:
            return

        def sort_key(item):
            name = self.folder_tree.item(item, "text")

            if self.sort_column == "date":
                date_value = self.folder_dates.get(item)

                if date_value is None:
                    return datetime.min

                return date_value

            return name.casefold()

        items.sort(
            key=sort_key,
            reverse=(
                self.sort_reverse
                if self.sort_column == "name"
                else not self.sort_reverse
            )
        )

        for position, item in enumerate(items):
            self.folder_tree.move(
                item,
                parent,
                position
            )

        for item in items:
            self._sort_children(item)

    def update_sort_headings(self):
        name_text = "Folder Name"
        date_text = "Date"

        if self.sort_column == "name":
            name_text += " " + ("▼" if self.sort_reverse else "▲")

        if self.sort_column == "date":
            # Date default is newest -> oldest.
            # Reverse is oldest -> newest.
            date_text += " " + ("▲" if self.sort_reverse else "▼")

        self.folder_tree.heading(
            "#0",
            text=name_text
        )

        self.folder_tree.heading(
            "date",
            text=date_text
        )

    # ---------------------------------------------------------
    # MEGAcmd listing with dates
    # ---------------------------------------------------------

    def get_folder_details(self, path):
        """Return [(name, datetime, display_date), ...] from mega-ls -l."""

        try:
            mega_dir = getattr(
                self.mega,
                "megacmd_dir",
                None
            )

            candidates = []

            if mega_dir:
                candidates.extend([
                    str(Path(mega_dir) / "mega-ls.bat"),
                    str(Path(mega_dir) / "mega-ls")
                ])

            which_path = shutil.which("mega-ls")
            if which_path:
                candidates.append(which_path)

            command = next(
                (candidate for candidate in candidates if Path(candidate).exists()),
                None
            )

            if not command:
                raise FileNotFoundError(
                    "Could not locate mega-ls."
                )

            process = subprocess.run(
                [command, "-l", path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                shell=False,
                creationflags=CREATE_NO_WINDOW
            )

            output = process.stdout or ""

            if process.returncode != 0 and not output.strip():
                raise RuntimeError(
                    process.stderr.strip()
                    or "mega-ls failed."
                )

            details = []

            # Example:
            # dep- - - 02Jan2026 06:55:25 Alcina Dimitrescu
            pattern = re.compile(
                r"^\S+\s+\S+\s+\S+\s+"
                r"(\d{2}[A-Za-z]{3}\d{4})\s+"
                r"(\d{2}:\d{2}:\d{2})\s+(.+?)\s*$"
            )

            for line in output.splitlines():
                match = pattern.match(line.strip())

                if not match:
                    continue

                date_part = match.group(1)
                time_part = match.group(2)
                name = match.group(3).strip()

                try:
                    date_value = datetime.strptime(
                        f"{date_part} {time_part}",
                        "%d%b%Y %H:%M:%S"
                    )
                except ValueError:
                    continue

                display_date = date_value.strftime(
                    "%d %b %Y %H:%M"
                )

                details.append(
                    (
                        name,
                        date_value,
                        display_date
                    )
                )

            return details

        except Exception as exc:
            raise RuntimeError(
                f"Unable to read folder dates for:\n{path}\n\n{exc}"
            )

    def get_visible_rows(self):
        rows = []

        def walk(parent):
            for item in self.folder_tree.get_children(parent):
                bbox = self.folder_tree.bbox(item)

                if bbox:
                    x, y, width, height = bbox
                    if y + height > 0:
                        rows.append((y, item))

                if self.folder_tree.item(item, "open"):
                    walk(item)

        walk("")
        rows.sort(key=lambda value: value[0])
        return rows

    def get_root_parent(self, item):
        current = item

        while True:
            parent = self.folder_tree.parent(current)

            if not parent:
                return current

            current = parent

    def find_current_parent(self):
        rows = self.get_visible_rows()

        if not rows:
            return None

        return self.get_root_parent(rows[0][1])

    def update_sticky_parent(self):
        if self.busy:
            return

        parent = self.find_current_parent()

        if not parent or not self.folder_tree.exists(parent):
            self.hide_sticky()
            return

        parent_bbox = self.folder_tree.bbox(parent)

        if parent_bbox:
            x, y, width, height = parent_bbox

            if y >= 0:
                self.hide_sticky()
                return

        self.show_sticky(parent)

    def show_sticky(self, parent):
        if not self.folder_tree.exists(parent):
            return

        name = self.folder_tree.item(parent, "text")
        values = self.folder_tree.item(parent, "values")

        date_text = values[0] if len(values) >= 1 else ""
        status_info = values[1] if len(values) >= 2 else ""

        is_open = self.folder_tree.item(parent, "open")

        style = ttk.Style()

        background = style.lookup(
            "Treeview", "fieldbackground"
        ) or "white"

        foreground = style.lookup(
            "Treeview", "foreground"
        ) or "black"

        self.sticky_frame.configure(bg=background)

        for widget in (
            self.sticky_toggle,
            self.sticky_name,
            self.sticky_date,
            self.sticky_status_info
        ):
            widget.configure(
                bg=background,
                fg=foreground,
                font=("Segoe UI", 9)
            )

        self.sticky_toggle.configure(
            text="−" if is_open else "+"
        )

        self.sticky_name.configure(text=name)
        self.sticky_date.configure(text=date_text)
        self.sticky_status_info.configure(text=status_info)

        folder_width = self.folder_tree.column("#0", "width")
        date_width = self.folder_tree.column("date", "width")
        status_info_width = self.folder_tree.column(
            "status_info", "width"
        )

        self.sticky_name.configure(
            width=max(1, int(max(100, folder_width - 25) / 7))
        )
        self.sticky_date.configure(
            width=max(1, int(date_width / 7))
        )
        self.sticky_status_info.configure(
            width=max(1, int(status_info_width / 7))
        )

        visible_rows = self.get_visible_rows()
        row_height = 22

        if visible_rows:
            bbox = self.folder_tree.bbox(visible_rows[0][1])
            if bbox:
                row_height = bbox[3]

        first_row_y = None

        for _, item in visible_rows:
            bbox = self.folder_tree.bbox(item)

            if bbox:
                x, y, width, height = bbox

                if y >= 0:
                    first_row_y = y
                    break

        if first_row_y is None:
            first_row_y = 20

        self.sticky_frame.place(
            x=0,
            y=max(0, first_row_y),
            relwidth=1,
            height=row_height
        )

        self.sticky_frame.lift()
        self.sticky_parent = parent

    def hide_sticky(self):
        self.sticky_parent = None
        self.sticky_frame.place_forget()

    def on_sticky_toggle(self, event=None):
        if self.busy:
            return

        parent = self.sticky_parent

        if not parent or not self.folder_tree.exists(parent):
            return

        current_state = self.folder_tree.item(
            parent, "open"
        )

        self.folder_tree.item(
            parent, open=not current_state
        )

        self.root.after_idle(
            self.update_sticky_parent
        )

    # ---------------------------------------------------------
    # Logging / connection
    # ---------------------------------------------------------

    def log_status(self, kind, message):
        color = self.STATUS_COLORS.get(
            kind,
            self.STATUS_COLORS["neutral"]
        )
        tag = f"activity_{kind}"

        try:
            self.activity_text.tag_configure(
                tag,
                foreground=color
            )
            self.activity_text.insert(
                "end",
                message + "\n",
                tag
            )
            self.activity_text.see("end")
        except Exception:
            self.log(message)

    def log(self, message):
        self.status_text.configure(state="normal")
        self.status_text.insert("end", message + "\n")
        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def detect_megacmd(self, attempt=1, show_error=True):
        """
        Start the MEGAcmd check in a worker thread.

        IMPORTANT:
        The worker never calls Tkinter methods. It only places results in
        megacmd_result_queue. The Tkinter main thread polls that queue.
        This avoids the cross-thread Tkinter call that could leave the UI
        stuck at "Checking MEGAcmd...".
        """
        if self.megacmd_check_running:
            return

        self.megacmd_check_running = True
        self.megacmd_ready = False
        self.megacmd_logged_in = False

        self.set_connection_status(
            "neutral",
            "Checking MEGAcmd..."
        )

        self.connection_retry_button.configure(
            text="Checking...",
            state="disabled"
        )
        self.connection_open_button.configure(
            state="disabled"
        )
        self.connection_open_button.pack_forget()

        self.set_connection_controls(False)

        worker = threading.Thread(
            target=self._megacmd_check_worker,
            args=(attempt, show_error),
            daemon=True
        )
        worker.start()

    def _megacmd_check_worker(self, attempt, show_error):
        """
        Perform the MEGAcmd check without touching Tkinter.

        On Windows the official MEGAcmd installation exposes scriptable
        commands such as mega-whoami.bat. Use cmd.exe explicitly for the BAT
        file instead of relying on subprocess(shell=True), which can behave
        unexpectedly with argument lists.

        We also use `whoami` without `-l`. We only need to determine whether
        the current MEGA session is active, and the shorter command avoids
        unnecessary session-list work during startup.
        """
        max_attempts = 3

        try:
            mega = MegaManager()
            megacmd_dir = Path(mega.megacmd_dir)

            candidates = [
                megacmd_dir / "mega-whoami.bat",
                megacmd_dir / "mega-whoami.exe",
            ]

            whoami = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.exists()
                ),
                None
            )

            if whoami is None:
                whoami = (
                    shutil.which("mega-whoami.bat")
                    or shutil.which("mega-whoami.exe")
                    or shutil.which("mega-whoami")
                )

            if whoami is None:
                self.megacmd_result_queue.put(
                    (
                        "missing",
                        None,
                        "MEGAcmd was found, but mega-whoami "
                        "could not be located.",
                        show_error
                    )
                )
                return

            for current_attempt in range(
                attempt,
                max_attempts + 1
            ):
                try:
                    if str(whoami).lower().endswith(".bat"):
                        # Explicitly invoke the Windows batch client.
                        command = [
                            os.environ.get(
                                "COMSPEC",
                                "cmd.exe"
                            ),
                            "/d",
                            "/c",
                            str(whoami),
                        ]
                    else:
                        command = [
                            str(whoami)
                        ]

                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=str(megacmd_dir),
                        shell=False,
                        creationflags=CREATE_NO_WINDOW
                    )

                    try:
                        stdout, stderr = process.communicate(
                            timeout=8
                        )
                    except subprocess.TimeoutExpired:
                        # Never leave a stuck MEGAcmd client behind.
                        process.kill()

                        try:
                            stdout, stderr = process.communicate(
                                timeout=2
                            )
                        except subprocess.TimeoutExpired:
                            stdout, stderr = "", ""

                        if current_attempt < max_attempts:
                            self.megacmd_result_queue.put(
                                (
                                    "retrying",
                                    current_attempt,
                                    None,
                                    show_error
                                )
                            )

                            import time
                            time.sleep(1.2)
                            continue

                        self.megacmd_result_queue.put(
                            (
                                "server_error",
                                None,
                                "mega-whoami did not respond after "
                                f"{max_attempts} attempts. "
                                "MEGAcmdServer may still be starting.",
                                show_error
                            )
                        )
                        return

                    output = (
                        (stdout or "")
                        + "\n"
                        + (stderr or "")
                    ).strip()

                except OSError as exc:
                    # The executable may have been unavailable for a moment
                    # while MEGAcmd was starting. Retry before failing.
                    if current_attempt < max_attempts:
                        self.megacmd_result_queue.put(
                            (
                                "retrying",
                                current_attempt,
                                None,
                                show_error
                            )
                        )

                        import time
                        time.sleep(1.2)
                        continue

                    self.megacmd_result_queue.put(
                        (
                            "server_error",
                            None,
                            str(exc),
                            show_error
                        )
                    )
                    return

                lower_output = output.lower()

                # `whoami` prints "Account e-mail:" for an active session.
                logged_in = (
                    process.returncode == 0
                    and "account e-mail:" in lower_output
                )

                explicitly_logged_out = (
                    "not logged" in lower_output
                    or "not logged in" in lower_output
                    or "no user" in lower_output
                    or "not login" in lower_output
                    or "no active session" in lower_output
                )

                if logged_in:
                    self.megacmd_result_queue.put(
                        (
                            "success",
                            mega,
                            None,
                            show_error
                        )
                    )
                    return

                if explicitly_logged_out:
                    self.megacmd_result_queue.put(
                        (
                            "logged_out",
                            mega,
                            None,
                            show_error
                        )
                    )
                    return

                # If the command returned without a recognizable login
                # result, treat it as a transient server startup issue first.
                if current_attempt < max_attempts:
                    self.megacmd_result_queue.put(
                        (
                            "retrying",
                            current_attempt,
                            None,
                            show_error
                        )
                    )

                    import time
                    time.sleep(1.2)
                    continue

                details = output or (
                    f"mega-whoami returned exit code "
                    f"{process.returncode}."
                )

                self.megacmd_result_queue.put(
                    (
                        "server_error",
                        None,
                        details,
                        show_error
                    )
                )
                return

        except FileNotFoundError as exc:
            self.megacmd_result_queue.put(
                (
                    "missing",
                    None,
                    str(exc),
                    show_error
                )
            )

        except Exception as exc:
            self.megacmd_result_queue.put(
                (
                    "server_error",
                    None,
                    str(exc),
                    show_error
                )
            )

    def _poll_megacmd_results(self):
        """Process worker results safely on Tkinter's main thread."""
        try:
            while True:
                result = self.megacmd_result_queue.get_nowait()

                kind = result[0]

                if kind == "retrying":
                    attempt = result[1]

                    if self.megacmd_check_running:
                        self.set_connection_status(
                            "neutral",
                            "Starting MEGAcmd server — retrying..."
                        )

                        self.connection_retry_button.configure(
                            text=f"Retrying ({attempt}/2)...",
                            state="disabled"
                        )
                        self.connection_open_button.pack_forget()

                elif kind == "success":
                    mega = result[1]

                    self.megacmd_check_running = False
                    self.mega = mega
                    self.megacmd_ready = True
                    self.megacmd_logged_in = True

                    self.set_connection_status(
                        "success",
                        "MEGAcmd ready — MEGA account logged in"
                    )

                    self.connection_retry_button.configure(
                        text="Retry",
                        state="normal",
                        command=self.detect_megacmd
                    )
                    self.connection_open_button.pack_forget()

                    self.log_status(
                        "success",
                        "MEGAcmd detected and MEGA session is active."
                    )

                    self.set_connection_controls(True)

                elif kind == "logged_out":
                    mega = result[1]
                    show_error = result[3]

                    self.megacmd_check_running = False
                    self.mega = mega
                    self.megacmd_ready = True
                    self.megacmd_logged_in = False

                    self.set_connection_status(
                        "warning",
                        "MEGAcmd detected — not logged in"
                    )

                    self.connection_retry_button.configure(
                        text="Retry",
                        state="normal",
                        command=self.detect_megacmd
                    )

                    self.connection_open_button.configure(
                        state="normal",
                        command=self.open_megacmd
                    )
                    self.connection_open_button.pack(
                        side="right",
                        padx=(10, 0)
                    )

                    self.log_status(
                        "warning",
                        "MEGAcmd is installed, but no active "
                        "MEGA login session was detected."
                    )

                    self.set_connection_controls(False)

                    if show_error:
                        self.root.after(
                            150,
                            self.show_login_required
                        )

                elif kind in (
                    "missing",
                    "server_error"
                ):
                    details = result[2]
                    show_error = result[3]

                    self.megacmd_check_running = False
                    self.mega = None
                    self.megacmd_ready = False
                    self.megacmd_logged_in = False

                    self.connection_retry_button.configure(
                        text="Retry",
                        state="normal",
                        command=self.detect_megacmd
                    )
                    self.connection_open_button.pack_forget()

                    self.set_connection_controls(False)

                    if kind == "missing":
                        self.set_connection_status(
                            "error",
                            "MEGAcmd not detected"
                        )

                        self.log_status(
                            "error",
                            f"MEGAcmd requirement check failed: {details}"
                        )

                        if show_error:
                            error_details = str(details)

                            self.root.after(
                                150,
                                lambda details=error_details:
                                messagebox.showerror(
                                    "MEGAcmd Required",
                                    "MEGA Link Manager requires MEGAcmd.\n\n"
                                    "Please install MEGAcmd, then click Retry.\n\n"
                                    f"Details:\n{details}"
                                )
                            )

                    else:
                        self.set_connection_status(
                            "error",
                            "MEGAcmd server unavailable"
                        )

                        self.log_status(
                            "error",
                            f"MEGAcmd startup check failed: {details}"
                        )

                        if show_error:
                            error_details = str(details)

                            self.root.after(
                                150,
                                lambda details=error_details:
                                messagebox.showerror(
                                    "MEGAcmd Startup Problem",
                                    "MEGAcmd is installed, but its server "
                                    "did not become ready.\n\n"
                                    "Click Retry to try again.\n\n"
                                    f"Details:\n{details}"
                                )
                            )

        except queue.Empty:
            pass

        # Keep polling while the application is alive.
        self.root.after(
            100,
            self._poll_megacmd_results
        )

    STATUS_COLORS = {
        "success": "#168A16",
        "warning": "#C58A00",
        "error": "#C62828",
        "neutral": "#333333",
    }

    def set_connection_status(self, kind, message):
        """Show a colored status icon with normal-color message text."""
        symbols = {
            "success": "✓",
            "warning": "⚠",
            "error": "✗",
            "neutral": "•",
        }

        self.connection_icon.configure(
            text=symbols.get(kind, "•"),
            foreground=self.STATUS_COLORS.get(
                kind,
                self.STATUS_COLORS["neutral"]
            )
        )

        self.connection_label.configure(
            text=message,
            foreground=self.STATUS_COLORS["neutral"]
        )

    def set_child_status(self, child_id, status):
        """Update only the status cell; folder/date remain default color."""
        if not self.folder_tree.exists(child_id):
            return

        text = str(status)

        if "Renewed" in text:
            display = "✓ Renewed"
        elif "Skipped" in text or "no link" in text:
            display = "⚠ Skipped - no link"
        elif (
            "Error" in text
            or "Failed" in text
            or "FATAL" in text
        ):
            display = "✗ Error"
        else:
            display = text

        self.folder_tree.set(
            child_id,
            "status_info",
            display
        )

        # Explicitly remove any old row-wide color tags.
        self.folder_tree.item(
            child_id,
            tags=()
        )

    def log_status(self, kind, message):
        """Color only the status symbol in the Activity panel."""
        symbols = {
            "success": "✓",
            "warning": "⚠",
            "error": "✗",
            "neutral": "•",
        }

        try:
            tag = f"activity_{kind}"

            self.activity_text.tag_configure(
                tag,
                foreground=self.STATUS_COLORS.get(
                    kind,
                    self.STATUS_COLORS["neutral"]
                )
            )

            self.activity_text.insert(
                "end",
                symbols.get(kind, "•") + " ",
                tag
            )
            self.activity_text.insert(
                "end",
                message + "\n"
            )
            self.activity_text.see("end")
        except Exception:
            self.log(message)

    def set_connection_controls(self, ready):
        """Enable or disable operations based on MEGAcmd/login state."""
        self.megacmd_ready = bool(ready)

        ready_for_use = (
            self.megacmd_ready
            and self.megacmd_logged_in
            and not self.busy
        )

        state = "normal" if ready_for_use else "disabled"

        if (
            not self.megacmd_ready
            or not self.megacmd_logged_in
        ):
            state = "disabled"

        self.refresh_button.configure(state=state)
        self.up_button.configure(state=state)
        self.renew_button.configure(state=state)
        self.export_button.configure(state=state)

    def open_megacmd(self):
        """Open an independent, interactive MEGAcmd console for login."""
        try:
            if self.mega is None:
                raise RuntimeError(
                    "MEGAcmd installation information is unavailable."
                )

            megacmd_dir = Path(
                self.mega.megacmd_dir
            )

            # Prefer the real interactive shell executable. Fall back to
            # the Windows batch launcher if that is what the installation
            # provides.
            shell_candidates = [
                megacmd_dir / "MEGAcmdShell.exe",
                megacmd_dir / "MEGAcmdShell.bat",
            ]

            shell_path = next(
                (
                    candidate
                    for candidate in shell_candidates
                    if candidate.exists()
                ),
                None
            )

            if shell_path is None:
                shell_path = (
                    shutil.which("MEGAcmdShell.exe")
                    or shutil.which("MEGAcmdShell")
                )

            if shell_path is None:
                raise FileNotFoundError(
                    "MEGAcmdShell.exe could not be located."
                )

            creation_flags = getattr(
                subprocess,
                "CREATE_NEW_CONSOLE",
                0x00000010
            )

            if str(shell_path).lower().endswith(".bat"):
                # The /k keeps the interactive MEGAcmd shell alive.
                # The extra quotes protect paths containing spaces.
                command = [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/k",
                    f'"{shell_path}"'
                ]
            else:
                command = [str(shell_path)]

            # IMPORTANT: do NOT redirect stdin/stdout/stderr to DEVNULL.
            # This is an interactive shell and needs its new console's
            # standard handles for keyboard input and prompt output.
            subprocess.Popen(
                command,
                cwd=str(megacmd_dir),
                creationflags=creation_flags,
                close_fds=False
            )

            self.log(
                "Opened MEGAcmd in a separate console. "
                "Log in there, then click Retry."
            )

        except Exception as exc:
            messagebox.showerror(
                "Unable to Open MEGAcmd",
                str(exc)
            )


    def show_login_required(self):
        """Explain how to complete the MEGAcmd login."""
        if self.megacmd_logged_in or not self.megacmd_ready:
            return

        answer = messagebox.askyesno(
            "MEGA Login Required",
            "MEGAcmd is installed, but it is not currently "
            "logged into a MEGA account.\n\n"
            "Click Yes to open MEGAcmd. Log in there, then "
            "return here and click Retry.\n\n"
            "MEGA Link Manager does not ask for or store "
            "your MEGA password."
        )

        if answer:
            self.open_megacmd()

    def set_busy(self, busy):

        self.busy = busy
        state = "disabled" if busy else "normal"

        self.refresh_button.configure(state=state)
        self.up_button.configure(state=state)
        self.renew_button.configure(state=state)
        self.export_button.configure(state=state)

    def update_breadcrumbs(self, path=None):
        """Render clickable breadcrumbs for the current MEGA path."""
        if path is None:
            path = self.path_entry.get()

        path = self.normalize_mega_path(path)

        for widget in self.breadcrumb_inner.winfo_children():
            widget.destroy()

        ttk.Button(
            self.breadcrumb_inner,
            text="MEGA",
            command=lambda: self.navigate_to("/")
        ).pack(side="left")

        if path == "/":
            return

        accumulated = ""

        for part in path.strip("/").split("/"):
            if not part:
                continue

            accumulated += "/" + part
            target = accumulated + "/"

            ttk.Label(
                self.breadcrumb_inner,
                text="›"
            ).pack(
                side="left",
                padx=4
            )

            ttk.Button(
                self.breadcrumb_inner,
                text=part,
                command=lambda p=target: self.navigate_to(p)
            ).pack(side="left")

    def refresh_navigation_ui(self):
        current = self.normalize_mega_path(
            self.path_entry.get()
        )

        self.update_breadcrumbs(current)

        if current == "/":
            self.up_button.configure(
                state="disabled"
            )
        elif not self.busy:
            self.up_button.configure(
                state="normal"
            )

    def show_creator_info(self):
        """Show creator attribution and application information."""
        import webbrowser

        dialog = tk.Toplevel(self.root)
        dialog.title(f"About MEGA Link Manager v{APP_VERSION}")
        dialog.geometry("360x260")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        container = ttk.Frame(
            dialog,
            padding=24
        )
        container.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            container,
            text=f"MEGA Link Manager v{APP_VERSION}",
            font=("TkDefaultFont", 14, "bold")
        ).pack(pady=(0, 14))

        ttk.Label(
            container,
            text="Created by"
        ).pack()

        ttk.Label(
            container,
            text="J.D.",
            font=("TkDefaultFont", 11, "bold")
        ).pack(pady=(2, 14))

        ttk.Label(
            container,
            text="Patreon"
        ).pack()

        patreon = tk.Label(
            container,
            text="patreon.com/chseva",
            cursor="hand2",
            font=("TkDefaultFont", 9, "underline")
        )
        patreon.pack(pady=(2, 18))

        patreon.bind(
            "<Button-1>",
            lambda event: webbrowser.open(
                "https://www.patreon.com/chseva"
            )
        )

        ttk.Button(
            container,
            text="OK",
            command=dialog.destroy
        ).pack()

        dialog.bind(
            "<Return>",
            lambda event: dialog.destroy()
        )
        dialog.bind(
            "<Escape>",
            lambda event: dialog.destroy()
        )

        dialog.protocol(
            "WM_DELETE_WINDOW",
            dialog.destroy
        )

        dialog.update_idletasks()

        x = (
            self.root.winfo_x()
            + (self.root.winfo_width() - dialog.winfo_width()) // 2
        )
        y = (
            self.root.winfo_y()
            + (self.root.winfo_height() - dialog.winfo_height()) // 2
        )

        dialog.geometry(
            f"+{max(0, x)}+{max(0, y)}"
        )

    def open_patreon(self):
        import webbrowser

        webbrowser.open(
            "https://www.patreon.com/chseva"
        )

    # ---------------------------------------------------------
    # Path preferences and navigation
    # ---------------------------------------------------------

    def normalize_mega_path(self, path):
        path = (path or "/").strip().replace("\\", "/")

        if not path.startswith("/"):
            path = "/" + path

        if path != "/":
            path = path.rstrip("/") + "/"

        return path

    def load_saved_path(self):
        try:
            if self.config_file.exists():
                with self.config_file.open(
                    "r",
                    encoding="utf-8"
                ) as file:
                    config = json.load(file)

                saved_path = str(
                    config.get("default_path", "/")
                ).strip()

                if saved_path:
                    return self.normalize_mega_path(
                        saved_path
                    )
        except Exception:
            pass

        return "/"

    def save_path_preference(self, path):
        try:
            self.config_dir.mkdir(
                parents=True,
                exist_ok=True
            )

            with self.config_file.open(
                "w",
                encoding="utf-8"
            ) as file:
                json.dump(
                    {
                        "default_path":
                            self.normalize_mega_path(path)
                    },
                    file,
                    indent=2
                )
        except Exception as exc:
            self.log(
                f"Warning: Could not save path preference: {exc}"
            )

    def get_parent_path(self, path):
        path = self.normalize_mega_path(path)

        if path == "/":
            return "/"

        parent = path.rstrip("/").rsplit("/", 1)[0]

        if not parent:
            return "/"

        return parent + "/"

    def navigate_up(self):
        if self.busy:
            return

        current = self.normalize_mega_path(
            self.path_entry.get()
        )

        if current != "/":
            self.navigate_to(
                self.get_parent_path(current)
            )

    def navigate_to(self, path):
        if self.busy:
            return

        path = self.normalize_mega_path(path)

        self.current_path = path

        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, path)

        self.refresh_navigation_ui()
        self.refresh_folders()

    def on_tree_double_click(self, event):
        if self.busy:
            return "break"

        item = self.folder_tree.identify_row(event.y)

        if not item:
            return "break"

        folder_name = self.folder_tree.item(
            item,
            "text"
        ).strip()

        if not folder_name:
            return "break"

        current_path = self.normalize_mega_path(
            self.path_entry.get()
        )

        if item.startswith("collection::"):
            # A collection is a direct child of the current path.
            base_path = current_path

        elif item.startswith("child::"):
            # A child is inside its parent collection.
            collection_item = self.get_child_collection(item)

            if not collection_item:
                return "break"

            collection_name = self.folder_tree.item(
                collection_item,
                "text"
            ).strip()

            base_path = (
                current_path.rstrip("/")
                + "/"
                + collection_name
                + "/"
            )
        else:
            return "break"

        new_path = (
            base_path.rstrip("/")
            + "/"
            + folder_name
            + "/"
        )

        self.navigate_to(new_path)

        return "break"

    # ---------------------------------------------------------
    # Refresh
    # ---------------------------------------------------------

    def refresh_folders(self):
        if self.busy:
            return

        if (
            self.mega is None
            or not self.megacmd_ready
            or not self.megacmd_logged_in
        ):
            messagebox.showwarning(
                "MEGA Connection",
                "MEGAcmd is not ready or is not logged in.\n\n"
                "Please complete the MEGAcmd login and click Retry."
            )
            return

        parent = self.normalize_mega_path(
            self.path_entry.get()
        )

        self.current_path = parent

        if not parent:
            messagebox.showwarning(
                "MEGA Path",
                "Please enter a MEGA folder path."
            )
            return

        self.set_busy(True)
        self.hide_sticky()
        self.selection_var.set("No collection selected")

        self.status_var.set("Refreshing MEGA...")
        self.progress["value"] = 0
        self.progress_label.configure(
            text="Discovering collections..."
        )

        self.log(f"Reading: {parent}")

        threading.Thread(
            target=self.refresh_worker,
            args=(parent,),
            daemon=True
        ).start()

    def refresh_worker(self, parent):
        try:
            collection_details = self.get_folder_details(parent)

            collections = [
                name for name, _, _ in collection_details
            ]

            collection_data = []
            total_collections = len(collection_details)

            self.root.after(
                0,
                self.progress.configure,
                {
                    "maximum": max(total_collections, 1),
                    "value": 0
                }
            )

            self.root.after(
                0,
                self.log,
                f"Found {total_collections} collections."
            )

            for index, (
                collection_name,
                collection_date,
                collection_date_text
            ) in enumerate(
                collection_details, start=1
            ):
                collection_path = (
                    parent.rstrip("/")
                    + "/"
                    + collection_name
                )

                self.root.after(
                    0,
                    self.progress_label.configure,
                    {
                        "text": (
                            f"Scanning {index}/"
                            f"{total_collections}: "
                            f"{collection_name}"
                        )
                    }
                )

                self.root.after(
                    0,
                    self.log,
                    f"Scanning: {collection_path}"
                )

                try:
                    child_details = self.get_folder_details(
                        collection_path
                    )

                    collection_data.append(
                        (
                            collection_name,
                            collection_date,
                            collection_date_text,
                            child_details,
                            None
                        )
                    )

                    self.root.after(
                        0,
                        self.log,
                        (
                            f"  ✓ {collection_name} — "
                            f"{len(child_details)} folders"
                        )
                    )

                except Exception as child_exc:
                    collection_data.append(
                        (
                            collection_name,
                            collection_date,
                            collection_date_text,
                            [],
                            str(child_exc)
                        )
                    )

                    self.root.after(
                        0,
                        self.log,
                        (
                            f"  ✗ {collection_name} — "
                            "unable to read"
                        )
                    )

                    self.root.after(
                        0,
                        self.log,
                        (
                            f"  ✗ {collection_name} — "
                            "unable to read"
                        )
                    )

                self.root.after(
                    0,
                    self.update_progress,
                    index
                )

            self.root.after(
                0,
                self.populate_collections,
                collection_data
            )

        except Exception as exc:
            self.root.after(
                0,
                self.refresh_error,
                str(exc)
            )

    def populate_collections(self, collection_data):
        self.hide_sticky()

        for item in self.folder_tree.get_children():
            self.folder_tree.delete(item)

        self.folder_dates.clear()

        total_children = 0

        for index, (
            collection_name,
            collection_date,
            collection_date_text,
            child_details,
            error
        ) in enumerate(collection_data):

            collection_id = f"collection::{index}"

            if error:
                info = "Unable to read"
            elif len(child_details) == 1:
                info = "1 folder"
            else:
                info = f"{len(child_details)} folders"

            parent_id = self.folder_tree.insert(
                "",
                "end",
                iid=collection_id,
                text=collection_name,
                values=(
                    collection_date_text,
                    info
                )
            )

            self.folder_dates[parent_id] = collection_date

            for child_index, (
                child_name,
                child_date,
                child_date_text
            ) in enumerate(child_details):

                child_id = (
                    f"child::{collection_id}::{child_index}"
                )

                self.folder_tree.insert(
                    parent_id,
                    "end",
                    iid=child_id,
                    text=child_name,
                    values=(
                        child_date_text,
                        "Ready"
                    )
                )

                self.folder_dates[child_id] = child_date

            total_children += len(child_details)

        # Start with the useful default: Folder Name A-Z.
        self.sort_column = "name"
        self.sort_reverse = False

        self._sort_children("")
        self.update_sort_headings()

        self.total_var.set("Total: 0")
        self.renewed_var.set("Renewed: 0")
        self.skipped_var.set("Skipped: 0")
        self.errors_var.set("Errors: 0")
        self.selection_var.set("No collection selected")

        self.progress["maximum"] = max(
            len(collection_data), 1
        )
        self.progress["value"] = len(collection_data)

        self.progress_label.configure(
            text="Refresh complete"
        )

        self.status_var.set(
            f"{len(collection_data)} collections ready"
        )

        self.current_path = self.normalize_mega_path(
            self.path_entry.get()
        )
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, self.current_path)

        # Save only after a successful Refresh.
        self.save_path_preference(
            self.current_path
        )

        self.refresh_navigation_ui()

        self.log("")
        self.log("================================")
        self.log("REFRESH COMPLETE")
        self.log(
            f"Collections: {len(collection_data)}"
        )
        self.log(
            f"Child folders discovered: {total_children}"
        )
        self.log("================================")

        self.set_busy(False)

    def refresh_error(self, message):
        self.set_busy(False)

        self.status_var.set("Refresh failed")
        self.progress_label.configure(text="Error")

        self.log(f"ERROR: {message}")

        messagebox.showerror(
            "Refresh Failed",
            message
        )

    # ---------------------------------------------------------
    # Selection
    # ---------------------------------------------------------

    def get_selected_collections(self):
        selected = []

        for item in self.folder_tree.selection():
            if not item.startswith("collection::"):
                continue

            name = self.folder_tree.item(
                item, "text"
            )

            if name:
                selected.append((item, name))

        return selected

    def get_selected_items(self):
        """Return selected collections and child folders separately."""
        collections = []
        children = []

        for item in self.folder_tree.selection():
            name = self.folder_tree.item(
                item, "text"
            )

            if not name:
                continue

            if item.startswith("collection::"):
                collections.append((item, name))
            elif item.startswith("child::"):
                children.append((item, name))

        return collections, children

    def get_child_collection(self, child_item):
        parent = self.folder_tree.parent(child_item)

        if parent and parent.startswith("collection::"):
            return parent

        return None

    def populate_children(self, collection_item, children):
        """Update the UI from the latest live MEGA scan."""
        if not self.folder_tree.exists(collection_item):
            return

        for child in self.folder_tree.get_children(collection_item):
            self.folder_tree.delete(child)

        for index, child in enumerate(children):
            if isinstance(child, tuple):
                child_name, child_date, child_date_text = child
            else:
                child_name = child
                child_date = self.folder_dates.get(None)
                child_date_text = ""

            child_id = (
                f"child::{collection_item}::{index}"
            )

            self.folder_tree.insert(
                collection_item,
                "end",
                iid=child_id,
                text=child_name,
                values=(
                    child_date_text,
                    "Ready"
                )
            )

            if child_date is not None:
                self.folder_dates[child_id] = child_date

        if len(children) == 1:
            info = "1 folder"
        else:
            info = f"{len(children)} folders"

        self.folder_tree.set(
            collection_item,
            "status_info",
            info
        )

        # Preserve the current sorting preference after a live scan.
        self._sort_children(collection_item)
        self.update_sort_headings()

        self.root.after_idle(
            self.update_selection_display
        )
        self.root.after_idle(
            self.update_sticky_parent
        )

    # ---------------------------------------------------------
    # Renew
    # ---------------------------------------------------------

    def renew_selected(self):
        if self.busy:
            return

        if (
            not self.megacmd_ready
            or not self.megacmd_logged_in
        ):
            messagebox.showwarning(
                "MEGA Connection",
                "Please log into MEGAcmd before renewing links."
            )
            return

        collections, children = self.get_selected_items()

        if not collections and not children:
            messagebox.showwarning(
                "Renew Links",
                "Select a collection or one or more child folders."
            )
            return

        parent = self.normalize_mega_path(
            self.path_entry.get()
        )

        targets = []

        # Selecting a parent means renew all of its child folders.
        for item, collection_name in collections:
            child_items = [
                child
                for child in self.folder_tree.get_children(item)
                if not child.startswith("placeholder::")
            ]

            for child_item in child_items:
                child_name = self.folder_tree.item(
                    child_item, "text"
                )

                targets.append(
                    (
                        child_item,
                        collection_name,
                        child_name,
                        parent.rstrip("/")
                        + "/"
                        + collection_name
                        + "/"
                        + child_name
                    )
                )

        # Selecting a child means renew only that child.
        for item, child_name in children:
            collection_item = self.get_child_collection(item)

            if not collection_item:
                continue

            collection_name = self.folder_tree.item(
                collection_item, "text"
            )

            targets.append(
                (
                    item,
                    collection_name,
                    child_name,
                    parent.rstrip("/")
                    + "/"
                    + collection_name
                    + "/"
                    + child_name
                )
            )

        # Avoid processing the same child twice if both its parent
        # and the child itself were selected.
        unique_targets = []
        seen_paths = set()

        for target in targets:
            if target[3] in seen_paths:
                continue

            seen_paths.add(target[3])
            unique_targets.append(target)

        targets = unique_targets

        if not targets:
            messagebox.showwarning(
                "Renew Links",
                "No child folders were found in the selection."
            )
            return

        collection_names = []
        for _, collection_name, _, _ in targets:
            if collection_name not in collection_names:
                collection_names.append(collection_name)

        if len(targets) == 1:
            target = targets[0]
            target_description = (
                f"{target[1]} / {target[2]}"
            )
        else:
            target_description = (
                f"{len(targets)} child folders"
            )

        message = (
            "You are about to renew public links for:\n\n"
            f"{target_description}\n\n"
            "For each selected folder:\n"
            "• Existing public link → remove and recreate\n"
            "• No existing public link → skip\n"
            "• Errors → report and continue\n\n"
            "Continue with link renewal?"
        )

        if not messagebox.askyesno(
            "Confirm Link Renewal",
            message
        ):
            return

        self.set_busy(True)

        self.total_var.set(
            f"Total: {len(targets)}"
        )
        self.renewed_var.set("Renewed: 0")
        self.skipped_var.set("Skipped: 0")
        self.errors_var.set("Errors: 0")

        self.progress["maximum"] = max(len(targets), 1)
        self.progress["value"] = 0

        self.progress_label.configure(
            text="Preparing renewal..."
        )

        self.status_var.set(
            "Preparing renewal..."
        )

        threading.Thread(
            target=self.renew_selected_worker,
            args=(targets,),
            daemon=True
        ).start()

    def renew_selected_worker(self, targets):
        try:
            total = len(targets)
            success_count = 0
            skip_count = 0
            error_count = 0

            for index, (
                child_id,
                collection_name,
                child_name,
                child_path
            ) in enumerate(targets, start=1):

                self.root.after(
                    0,
                    self.set_child_status,
                    child_id,
                    "Processing..."
                )

                self.root.after(
                    0,
                    self.progress_label.configure,
                    {
                        "text": (
                            f"Processing {index}/{total}: "
                            f"{child_name}"
                        )
                    }
                )

                self.root.after(
                    0,
                    self.log,
                    (
                        f"[{index}/{total}] "
                        f"{collection_name}/{child_name}"
                    )
                )

                result = self.mega.renew_folder(
                    child_path
                )

                if result.status == "renewed":
                    success_count += 1

                    self.root.after(
                        0,
                        self.set_child_status,
                        child_id,
                        "Renewed"
                    )

                    self.root.after(
                        0,
                        self.log,
                        "    ✓ New link created."
                    )

                elif result.status == "skipped":
                    skip_count += 1

                    self.root.after(
                        0,
                        self.set_child_status,
                        child_id,
                        "Skipped - no link"
                    )

                    self.root.after(
                        0,
                        self.log,
                        "    → No existing link."
                    )

                else:
                    error_count += 1

                    self.root.after(
                        0,
                        self.set_child_status,
                        child_id,
                        "Error"
                    )

                    self.root.after(
                        0,
                        self.log,
                        "    ✗ " + result.message
                    )

                    if result.output:
                        self.root.after(
                            0,
                            self.log,
                            result.output
                        )

                self.root.after(
                    0,
                    self.update_progress,
                    index
                )

                self.root.after(
                    0,
                    self.update_summary,
                    total,
                    success_count,
                    skip_count,
                    error_count
                )

            self.root.after(
                0,
                self.renew_finished,
                total,
                success_count,
                skip_count,
                error_count
            )

        except Exception as exc:
            self.root.after(
                0,
                self.log,
                f"FATAL ERROR: {exc}"
            )

            self.root.after(
                0,
                self.renew_finished,
                0,
                0,
                0,
                1
            )

    def renew_worker(self, parent, selected):
        try:
            work_items = []

            for collection_item, collection_name in selected:

                collection_path = (
                    parent.rstrip("/")
                    + "/"
                    + collection_name
                )

                self.root.after(
                    0,
                    self.log,
                    f"Discovering: {collection_name}"
                )

                try:
                    child_details = self.get_folder_details(
                        collection_path
                    )
                    children = [
                        name
                        for name, _, _ in child_details
                    ]
                except Exception as exc:
                    self.root.after(
                        0,
                        self.log,
                        f"✗ Failed to read {collection_name}: {exc}"
                    )
                    continue

                self.root.after(
                    0,
                    self.populate_children,
                    collection_item,
                    child_details
                )

                for index, child_name in enumerate(children):
                    child_id = (
                        f"child::{collection_item}::{index}"
                    )

                    work_items.append(
                        (
                            child_id,
                            collection_name,
                            child_name,
                            collection_path
                            + "/"
                            + child_name
                        )
                    )

            total = len(work_items)

            if total == 0:
                self.root.after(
                    0,
                    self.renew_finished,
                    0, 0, 0, 0
                )
                return

            self.root.after(
                0,
                self.set_progress_max,
                total
            )

            success_count = 0
            skip_count = 0
            error_count = 0

            for index, (
                child_id,
                collection_name,
                child_name,
                child_path
            ) in enumerate(work_items, start=1):

                self.root.after(
                    0,
                    self.set_child_status,
                    child_id,
                    "Processing..."
                )

                self.root.after(
                    0,
                    self.progress_label.configure,
                    {
                        "text": (
                            f"Processing {index}/{total}: "
                            f"{child_name}"
                        )
                    }
                )

                self.root.after(
                    0,
                    self.log,
                    (
                        f"[{index}/{total}] "
                        f"{collection_name}/{child_name}"
                    )
                )

                result = self.mega.renew_folder(
                    child_path
                )

                if result.status == "renewed":
                    success_count += 1

                    self.root.after(
                        0,
                        self.set_child_status,
                        child_id,
                        "Renewed"
                    )

                    self.root.after(
                        0,
                        self.log,
                        "    ✓ New link created."
                    )

                elif result.status == "skipped":
                    skip_count += 1

                    self.root.after(
                        0,
                        self.set_child_status,
                        child_id,
                        "Skipped - no link"
                    )

                    self.root.after(
                        0,
                        self.log,
                        "    → No existing link."
                    )

                else:
                    error_count += 1

                    self.root.after(
                        0,
                        self.set_child_status,
                        child_id,
                        "Error"
                    )

                    self.root.after(
                        0,
                        self.log,
                        "    ✗ " + result.message
                    )

                    if result.output:
                        self.root.after(
                            0,
                            self.log,
                            result.output
                        )

                self.root.after(
                    0,
                    self.update_progress,
                    index
                )

                self.root.after(
                    0,
                    self.update_summary,
                    total,
                    success_count,
                    skip_count,
                    error_count
                )

            self.root.after(
                0,
                self.renew_finished,
                total,
                success_count,
                skip_count,
                error_count
            )

        except Exception as exc:
            self.root.after(
                0,
                self.log,
                f"FATAL ERROR: {exc}"
            )

            self.root.after(
                0,
                self.renew_finished,
                0, 0, 0, 1
            )

    def set_progress_max(self, maximum):
        self.progress["maximum"] = maximum
        self.progress["value"] = 0

    def update_progress(self, value):
        self.progress["value"] = value

    def update_summary(
        self,
        total,
        renewed,
        skipped,
        errors
    ):
        self.total_var.set(f"Total: {total}")
        self.renewed_var.set(f"Renewed: {renewed}")
        self.skipped_var.set(f"Skipped: {skipped}")
        self.errors_var.set(f"Errors: {errors}")

    def renew_finished(
        self,
        total,
        renewed,
        skipped,
        errors
    ):
        self.set_busy(False)

        self.update_summary(
            total,
            renewed,
            skipped,
            errors
        )

        self.progress["value"] = total
        self.progress_label.configure(
            text="Renewal complete"
        )
        self.status_var.set(
            "Renewal complete"
        )

        self.log("")
        self.log("================================")
        self.log("RENEWAL COMPLETE")
        self.log(f"Total:    {total}")
        self.log(f"Renewed:  {renewed}")
        self.log(f"Skipped:  {skipped}")
        self.log(f"Errors:   {errors}")
        self.log("================================")

        messagebox.showinfo(
            "Renewal Complete",
            (
                f"Total folders: {total}\n\n"
                f"Successfully renewed: {renewed}\n"
                f"Skipped - no existing link: {skipped}\n"
                f"Errors: {errors}"
            )
        )

    # ---------------------------------------------------------
    # Export
    # ---------------------------------------------------------

    def export_selected(self):
        if self.busy:
            return

        if (
            not self.megacmd_ready
            or not self.megacmd_logged_in
        ):
            messagebox.showwarning(
                "MEGA Connection",
                "Please log into MEGAcmd before exporting links."
            )
            return

        collections, children = self.get_selected_items()

        if children:
            messagebox.showinfo(
                "Export Links",
                "Export Links currently works on a collection. "
                "Select the parent collection to export its links."
            )
            return

        if len(collections) != 1:
            messagebox.showwarning(
                "Export Links",
                "Select exactly ONE collection folder."
            )
            return

        _, folder_name = collections[0]
        parent = self.normalize_mega_path(
            self.path_entry.get()
        )

        mega_path = (
            parent.rstrip("/")
            + "/"
            + folder_name
        )

        output_file = filedialog.asksaveasfilename(
            title="Save exported links",
            initialfile=f"{folder_name} links.txt",
            defaultextension=".txt",
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*")
            ]
        )

        if not output_file:
            return

        self.set_busy(True)
        self.status_var.set("Exporting links...")

        self.log(f"Exporting: {mega_path}")
        self.log(f"Saving to: {output_file}")

        threading.Thread(
            target=self.export_worker,
            args=(mega_path, output_file),
            daemon=True
        ).start()

    def export_worker(
        self,
        mega_path,
        output_file
    ):
        try:
            result = self.mega.export_links_to_file(
                mega_path,
                output_file
            )

            self.root.after(
                0,
                self.export_finished,
                result,
                output_file
            )

        except Exception as exc:
            self.root.after(
                0,
                self.export_error,
                str(exc)
            )

    def export_finished(
        self,
        result,
        output_file
    ):
        self.set_busy(False)

        if not result.success:
            self.export_error(
                result.error or result.output
            )
            return

        self.status_var.set(
            "Export complete"
        )

        self.log(
            "Export completed successfully."
        )

        messagebox.showinfo(
            "Export Complete",
            "Links saved to:\n\n" + output_file
        )

    def export_error(self, message):
        self.set_busy(False)

        self.status_var.set(
            "Export failed"
        )

        self.log(
            f"ERROR: {message}"
        )

        messagebox.showerror(
            "Export Failed",
            message
        )


def main():
    root = tk.Tk()
    MegaLinkManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

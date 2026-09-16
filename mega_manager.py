import os
import re
import subprocess

# Prevent non-interactive MEGAcmd commands from flashing a console window on Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MegaResult:
    success: bool
    output: str
    error: str = ""
    return_code: int = 0


@dataclass
class RenewResult:
    folder: str
    status: str
    message: str
    output: str = ""


class MegaManager:
    """
    Interface to MEGAcmd.

    Uses the same MEGAcmd commands that have already been
    verified in the working BAT version.
    """

    def __init__(self, megacmd_dir=None):

        if megacmd_dir:
            self.megacmd_dir = Path(megacmd_dir)
        else:
            self.megacmd_dir = self.find_megacmd()

        if self.megacmd_dir is None:
            raise FileNotFoundError(
                "MEGAcmd could not be found."
            )

        self.mega_ls = self.megacmd_dir / "mega-ls.bat"
        self.mega_export = self.megacmd_dir / "mega-export.bat"

        if not self.mega_ls.exists():
            raise FileNotFoundError(
                f"Could not find:\n{self.mega_ls}"
            )

        if not self.mega_export.exists():
            raise FileNotFoundError(
                f"Could not find:\n{self.mega_export}"
            )

    # =========================================================
    # MEGAcmd detection
    # =========================================================

    @staticmethod
    def find_megacmd():

        candidates = []

        username = os.environ.get("USERNAME")

        if username:
            candidates.append(
                Path(
                    rf"C:\Users\{username}\AppData\Local\MEGAcmd"
                )
            )

        candidates.extend(
            [
                Path(r"C:\Program Files\MEGAcmd"),
                Path(r"C:\Program Files (x86)\MEGAcmd"),
            ]
        )

        for path in candidates:

            if (
                (path / "mega-ls.bat").exists()
                and
                (path / "mega-export.bat").exists()
            ):
                return path

        return None

    # =========================================================
    # Run MEGAcmd
    # =========================================================

    def _run(self, command, timeout=120):

        try:

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                creationflags=CREATE_NO_WINDOW,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            combined = stdout

            if stderr:

                if combined:
                    combined += "\n"

                combined += stderr

            return MegaResult(
                success=(result.returncode == 0),
                output=combined,
                error=stderr,
                return_code=result.returncode,
            )

        except subprocess.TimeoutExpired as exc:

            return MegaResult(
                success=False,
                output=exc.stdout or "",
                error="MEGAcmd operation timed out.",
                return_code=-1,
            )

        except Exception as exc:

            return MegaResult(
                success=False,
                output="",
                error=str(exc),
                return_code=-1,
            )

    # =========================================================
    # List folder
    # =========================================================

    def list_folder(self, mega_path):

        mega_path = mega_path.strip()

        if not mega_path:

            return MegaResult(
                success=False,
                output="",
                error="MEGA path cannot be empty.",
                return_code=-1,
            )

        command = [
            str(self.mega_ls),
            "-l",
            mega_path,
        ]

        return self._run(command)

    # =========================================================
    # Parse folder names
    # =========================================================

    @staticmethod
    def parse_folder_names(raw_output):

        folders = []

        pattern = re.compile(
            r"^\S+\s+\S+\s+\S+\s+"
            r"\d{2}\w{3}\d{4}\s+"
            r"\d{2}:\d{2}:\d{2}\s+"
            r"(.+?)\s*$"
        )

        for line in raw_output.splitlines():

            match = pattern.match(line)

            if not match:
                continue

            name = match.group(1).strip()

            # Remove possible UTF-8 BOM.
            name = name.lstrip("\ufeff")

            if name:
                folders.append(name)

        return folders

    # =========================================================
    # Get folder names
    # =========================================================

    def get_folder_names(self, mega_path):

        result = self.list_folder(mega_path)

        if not result.success:
            return result, []

        folders = self.parse_folder_names(
            result.output
        )

        return result, folders

    # =========================================================
    # Remove public link
    # =========================================================

    def remove_link(self, mega_path):

        mega_path = mega_path.strip()

        if not mega_path:

            return MegaResult(
                success=False,
                output="",
                error="MEGA path cannot be empty.",
                return_code=-1,
            )

        command = [
            str(self.mega_export),
            "-d",
            mega_path,
        ]

        return self._run(command)

    # =========================================================
    # Create public link
    # =========================================================

    def create_link(self, mega_path):

        mega_path = mega_path.strip()

        if not mega_path:

            return MegaResult(
                success=False,
                output="",
                error="MEGA path cannot be empty.",
                return_code=-1,
            )

        command = [
            str(self.mega_export),
            "-a",
            mega_path,
        ]

        return self._run(command)

    # =========================================================
    # Export links
    # =========================================================

    def export_links(self, mega_path):

        mega_path = mega_path.strip()

        if not mega_path:

            return MegaResult(
                success=False,
                output="",
                error="MEGA path cannot be empty.",
                return_code=-1,
            )

        command = [
            str(self.mega_export),
            mega_path,
        ]

        return self._run(command)

    # =========================================================
    # Clean exported links
    # =========================================================

    @staticmethod
    def clean_exported_links(
        raw_output,
        parent_path
    ):

        parent_path = parent_path.rstrip("/") + "/"

        cleaned_lines = []

        for line in raw_output.splitlines():

            if line.startswith(parent_path):
                line = line[len(parent_path):]

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    # =========================================================
    # Export directly to file
    # =========================================================

    def export_links_to_file(
        self,
        mega_path,
        output_file
    ):

        result = self.export_links(
            mega_path
        )

        if not result.success:
            return result

        cleaned = self.clean_exported_links(
            result.output,
            mega_path
        )

        try:

            output_path = Path(
                output_file
            )

            output_path.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            output_path.write_text(
                cleaned,
                encoding="utf-8"
            )

            return MegaResult(
                success=True,
                output=cleaned,
                return_code=0
            )

        except Exception as exc:

            return MegaResult(
                success=False,
                output=cleaned,
                error=str(exc),
                return_code=-1
            )

    # =========================================================
    # Detect missing public link
    # =========================================================

    @staticmethod
    def has_no_existing_link(result):

        text = (
            result.output
            + "\n"
            + result.error
        ).lower()

        return (
            "node not exported" in text
        )

    # =========================================================
    # Renew one folder
    # =========================================================

    def renew_folder(
        self,
        mega_path,
        delay=2
    ):

        # Remove old public link.
        remove_result = self.remove_link(
            mega_path
        )

        # Normal skip condition.
        if self.has_no_existing_link(
            remove_result
        ):

            return RenewResult(
                folder=mega_path,
                status="skipped",
                message="No existing public link.",
                output=remove_result.output,
            )

        # Unexpected error.
        if not remove_result.success:

            return RenewResult(
                folder=mega_path,
                status="error",
                message="Failed to remove existing link.",
                output=remove_result.output,
            )

        # Give MEGA a moment before creating the replacement.
        time.sleep(delay)

        # Create new public link.
        add_result = self.create_link(
            mega_path
        )

        if not add_result.success:

            return RenewResult(
                folder=mega_path,
                status="error",
                message="Failed to create new link.",
                output=add_result.output,
            )

        return RenewResult(
            folder=mega_path,
            status="renewed",
            message="New public link created.",
            output=add_result.output,
        )
# MEGA Link Manager

Version: **v1.0**

MEGA Link Manager is a Windows desktop app for managing public links to folders in a MEGA account. It displays your MEGA folder collections, lets you renew existing public folder links, and exports the current links to a text file.

It is designed to work with **MEGAcmd**, MEGA's local command-line client. The app does not ask for, save, or transmit your MEGA password.

## Features

- Checks whether MEGAcmd is installed and whether a MEGA session is active.
- Opens an interactive MEGAcmd window when you need to sign in.
- Browses the folders in your MEGA account.
- Refreshes folder information and shows progress and activity messages.
- Renews public links for selected folders or collections.
- Exports links from a selected collection to a text file.
- Remembers only the last MEGA path selected by the user.

## Important: what “Renew Links” does

Renewing a link is a real external change. For every selected folder that already has a public link, the app:

1. Removes the existing public link.
2. Waits briefly for MEGA to process the change.
3. Creates a new public link for the same folder.

Anyone using the old link will no longer be able to use it. A folder that has no existing public link is skipped rather than given a new one. Review the confirmation dialog and export your current links before making a large renewal.

## Requirements

- Windows 10 or Windows 11
- A MEGA account
- [MEGAcmd](https://mega.io/cmd) installed locally and signed in

The app looks for MEGAcmd in the usual per-user and Program Files installation locations. If MEGAcmd is installed but you are not signed in, choose **Open MEGAcmd**, log in in the separate MEGAcmd window, then return to the app and select **Retry**.

## Using the app

1. Start **MEGA Link Manager**.
2. Confirm that the connection area says MEGAcmd is ready and the MEGA account is logged in.
3. Browse to the collection or folders you want to manage.
4. Select a collection and choose **Export Links** to save its current links to a text file, or select the folders/collection and choose **Renew Links**.
5. Read the confirmation carefully before renewing. The activity panel and counters show renewed, skipped, and failed items.

Use **Refresh** after a renewal to view the current folder state.

## Privacy and local data

MEGA Link Manager delegates authentication to MEGAcmd. It does not collect your MEGA password or store it itself.

The app's local configuration is stored for the current Windows user at:

```text
%LOCALAPPDATA%\MEGALinkManager\config.json
```

It contains the remembered default MEGA path only. Exported link files are written only where you choose in the save dialog.

Do not share exported link lists if you do not want others to access the public folders they contain.

## Packaged app

The PyInstaller build is a one-folder application. To share it, distribute the complete folder:

```text
dist\MEGA Link Manager\
```

Keep `MEGA Link Manager.exe` together with its `_internal` folder. A recipient does not need Python to run the packaged app, but they must install MEGAcmd and sign in to their own MEGA account.

## Build from source

Requires Python 3.10 or newer and PyInstaller.

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean "MEGA Link Manager.spec"
```

The build output is created under `dist\MEGA Link Manager\`.

## Sharing safely

Before publishing a release, exclude personal exports, MEGAcmd sessions, local configuration, and any screenshots or files that reveal your MEGA folder names or public links. The app source and packaged executable should not contain your MEGA password, but exported link files can grant access to your shared folders.

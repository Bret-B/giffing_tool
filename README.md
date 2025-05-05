# giffing_tool
A basic gif recording utility written in Python, utilizing FFmpeg and Gifski (Windows only).

# Building/Running

Your Python installation needs Tkinter for the GUI.

Download binaries for both FFmpeg and Gifski. Place ffmpeg.exe and gifski.exe in the `resources` folder.

To build an executable in either single file or directory mode, PyInstaller is required.
Then, activate the virtual environment that has PyInstaller (if any) and run build_onefile.bat or build_onedir.bat

Build output can be found in the `dist` folder.
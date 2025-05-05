# giffing_tool
A basic gif recording utility written in Python, utilizing FFmpeg and Gifski (Windows only).

# Building/Running

Your Python installation needs Tkinter for the GUI. Tested on Python 3.11, but Python 3.8+ should probably work.

You can run giffing tool directly by running `giffing_tool.pyw`



To build to an executable:

* Download binaries for both FFmpeg and Gifski. Place ffmpeg.exe and gifski.exe in the `resources` folder.

* To build an executable in either single file or directory mode, PyInstaller is required.
After PyInstaller is installed, activate the virtual environment that has PyInstaller (if any) and run build_onefile.bat or build_onedir.bat

* Build output can be found in the `dist` folder.
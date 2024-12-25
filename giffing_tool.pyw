import subprocess
import threading
import tkinter as tk
import tkinter.filedialog
from concurrent.futures import ThreadPoolExecutor
import ctypes
import queue
from constants import PACKAGE_DIRECTORY
from ffmpeg_interface import FFmpegInterface, RecorderConfig, SizeAndOffsets
import time
import sys
import os

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Windows version >= 8.1
except:
    ctypes.windll.user32.SetProcessDPIAware()  # Windows version <= 8.0



class Application:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.resx = master.winfo_screenwidth()
        self.resy = master.winfo_screenheight()
        self.delay = 0.0
        self.ffmpeg_interface = FFmpegInterface(RecorderConfig())
        self.call_queue = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self._saved_state = []

        self.action_buttons = ActionButtons(self.master, self)
        self.action_buttons.grid(row=0, column=0, sticky='NEW')

        self.options = OptionsMenu(self.master, self)
        self.options.grid(row=1, column=0, sticky='NEW')

        master.rowconfigure(0, weight=0, pad=2)
        master.rowconfigure(1, weight=1, pad=2)
        master.title('Giffing Tool')
        master.bind("<<gui_call>>", self.gui_call_handler)
        master.update_idletasks()  # needed for correct width, height

        # move window to center
        w, h = master.winfo_width(), master.winfo_height()
        master.geometry(f'+{self.resx // 2 - w // 2}+{self.resy // 2 - h // 2}')

    def make_gui_call(self, fn, *args, **kwargs):
        data = _GUICallData(fn, args, kwargs)
        self.call_queue.put(data)
        self.master.event_generate("<<gui_call>>", when="tail")
        data.reply_event.wait()
        return data.reply

    def gui_call_handler(self, _):
        try:
            while True:
                data = self.call_queue.get_nowait()
                data.reply = data.fn(*data.args, **data.kwargs)
                data.reply_event.set()
        except queue.Empty:
            pass

    def enable(self):
        self.action_buttons.enable()
        self.options.enable()

    def disable(self):
        self.action_buttons.disable()
        self.options.disable()

    def restore_state(self):
        for widget, state in self._saved_state:
            try:
                real_widget = self.master.nametowidget(widget)
            except KeyError:
                continue

            if 'state' in real_widget.keys() and real_widget.winfo_ismapped() and real_widget.winfo_viewable():
                real_widget.configure(state=state)

    def save_state(self):
        self._saved_state.clear()
        self._save_state_recursive(self.master)

    def _save_state_recursive(self, element):
        for child in element.winfo_children():
            child_as_widget = element.nametowidget(child)
            if issubclass(type(child_as_widget), tk.Widget) and 'state' in child_as_widget.config():
                try:
                    self._saved_state.append((child, child_as_widget.cget('state')))
                except Exception as e:
                    print(f'{e}\n{child_as_widget}\naka {child}')
            self._save_state_recursive(child_as_widget)


class ActionButtons(tk.Frame):
    def __init__(self, parent, app: Application, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.parent = parent
        self.app = app
        self.last_saved_filename = tk.StringVar()
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.capture_button = CaptureButton(self, app, width=16)
        self.save_button = tk.Button(self, text='Save GIF', command=self.save, state=tk.DISABLED, width=16)
        self.copy_to_clipboard_button = tk.Button(self, text='Copy last to clipboard',
                                                  command=self.copy_clipboard, state=tk.DISABLED, width=20)
        self.capture_button.grid(row=0, column=0, sticky='NEW')
        self.save_button.grid(row=0, column=1, sticky='NEW')
        self.copy_to_clipboard_button.grid(row=0, column=2, sticky='NEW')
        self.app.master.bind('<F8>', lambda *_: self.capture_button.main_button_press())

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)

    def save(self):
        if not self.app.ffmpeg_interface.ready_for_export():
            return

        # filename popup blocks interaction with the app until it exits (at least on Windows)
        # self.app.disable()
        filename: str = tk.filedialog.asksaveasfilename(confirmoverwrite=True,
                                                        defaultextension=RecorderConfig.OUTPUT_FORMATS[0],
                                                        filetypes=RecorderConfig.OUTPUT_FORMATS)
        if not filename:
            # self.app.enable()
            return

        self.last_saved_filename.set(filename)

        self.app.save_state()
        self.app.disable()

        def enable():
            # self.app.make_gui_call(self.app.restore_state)
            self.app.make_gui_call(self.app.restore_state)
            self.app.action_buttons.enable()

        self.app.ffmpeg_interface.save(filename, self.capture_button.last_size_offsets, callback=enable)

    def copy_clipboard(self):
        # self.clipboard_clear()
        # self.clipboard_append(self.last_saved_filename.get())
        filename = self.last_saved_filename.get()
        if not filename:
            return
        self.app.pool.submit(lambda: subprocess.run(['powershell', '-command', f'Set-Clipboard -Path "{filename}"'],
                                                    creationflags=subprocess.CREATE_NO_WINDOW))

    def enable(self):
        self.capture_button.configure(state=tk.NORMAL)
        self.save_button.configure(state=tk.NORMAL)
        self.copy_to_clipboard_button.configure(state=tk.NORMAL)

    def disable(self):
        self.capture_button.configure(state=tk.DISABLED)
        self.save_button.configure(state=tk.DISABLED)
        self.copy_to_clipboard_button.configure(state=tk.DISABLED)


class CaptureButton(tk.Button):
    START = 'Start Capture'
    START_COLOR = 'steel blue'
    STOP = 'Stop Capture (F8)'
    STOP_COLOR = 'firebrick4'

    def __init__(self, parent, app: Application, *args, **kwargs):
        super().__init__(parent, text=self.START, command=self.main_button_press,
                         background=self.START_COLOR, *args, **kwargs)
        self.start_timer: threading.Timer | None = None
        self.time_of_start: float | None = None
        self.press_starts_capture = True
        self.current_y = self.current_x = self.start_y = self.start_x = self.snip_surface = self.prev_geometry = None
        self.last_size_offsets: SizeAndOffsets | None = None
        self.app = app
        self.parent = parent
        self.master_screen = tk.Toplevel(self.parent)
        self.master_screen.withdraw()
        self.master_screen.attributes('-transparent', 'maroon3')
        self.picture_frame = tk.Frame(self.master_screen, background='maroon3')
        self.picture_frame.pack(fill=tk.BOTH, expand=tk.YES)

    def main_button_press(self):
        self.configure(state=tk.DISABLED)
        # a better way to do this is to register a callback for either start/stop capture which
        # enables the button when the action actually finishes on the ffmpeg_interface backend
        # e.g. the registered callback should call self.app.make_gui_call to re-enable the button
        if self.press_starts_capture:
            self.create_snip_plane()
        else:
            self.stop_capturing()

    def create_snip_plane(self):
        self.prev_geometry = self.app.master.winfo_geometry()

        self.master_screen.deiconify()
        self.parent.parent.withdraw()
        self.master_screen.bind('<Escape>', lambda *_: self.cancel_capture())

        self.snip_surface = tk.Canvas(self.picture_frame, cursor='cross', bg='grey11')
        self.snip_surface.pack(fill=tk.BOTH, expand=tk.YES)

        self.snip_surface.bind('<ButtonPress-1>', self.on_snip_press)
        self.snip_surface.bind('<B1-Motion>', self.on_snip_drag)
        self.snip_surface.bind('<ButtonRelease-1>', self.delay_start_capture)

        # self.master_screen.geometry('+3840+2160')  # this doesnt work
        self.master_screen.attributes('-fullscreen', True)
        self.master_screen.attributes('-alpha', .3)
        self.master_screen.lift()
        self.master_screen.attributes('-topmost', True)
        self.master_screen.focus_set()

    def stop_capturing(self):
        if self.start_timer is not None or self.time_of_start is not None:
            self.start_timer.cancel()
            self.start_timer = None
            self.time_of_start = None
            self.configure(state=tk.NORMAL, text=self.START, background=self.START_COLOR)
            self.press_starts_capture = True

        def capture_finished_tasks():
            self.configure(state=tk.NORMAL, text=self.START, background=self.START_COLOR)
            self.press_starts_capture = True

        # needed?
        if self.app.ffmpeg_interface.capturing():
            self.app.ffmpeg_interface.add_finish_task(lambda: self.app.make_gui_call(capture_finished_tasks))
            self.app.ffmpeg_interface.stop_capture()

    def delay_start_capture(self, event):
        self.press_starts_capture = False
        self.exit_screenshot_mode()

        if self.app.delay <= 0:
            self.start_capturing(event)
        else:
            self.time_of_start = time.time() + self.app.delay
            self.configure(state=tk.NORMAL, text=f'Starting in {self.app.delay:.02f}s\nClick to cancel',
                           background=self.STOP_COLOR)

            def update_button_text():
                if not self.time_of_start or not self.start_timer:
                    return

                self.configure(text=f'Starting in {self.time_of_start - time.time():.02f}s\nClick to cancel')
                self.after(10, update_button_text)

            self.after(10, update_button_text)
            self.start_timer = threading.Timer(self.app.delay, self.start_capturing,
                                               args=[event])
            self.start_timer.start()

    # start capturing
    def start_capturing(self, event):
        self.start_timer = None
        self.time_of_start = None

        xmin = max(min(self.start_x, self.current_x), 0)
        xmax = min(max(self.start_x, self.current_x), self.app.resx)
        ymin = max(min(self.start_y, self.current_y), 0)
        ymax = min(max(self.start_y, self.current_y), self.app.resy)
        # print(xmin, ymin)
        # print(xmax, ymax)
        self.last_size_offsets = SizeAndOffsets(xmax - xmin, ymax - ymin, xmin, ymin)
        timer = threading.Timer(RecorderConfig.MAX_DURATION_SECONDS, self.app.make_gui_call, args=[self.stop_capturing])
        timer.start()

        self.configure(state=tk.DISABLED, text=self.STOP, background=self.STOP_COLOR)
        self.app.save_state()
        self.app.disable()
        self.app.action_buttons.save_button.configure(state=tk.DISABLED)
        # start_callback enables the Stop Recording button when the recording has started
        # finish_callback enables the Save button when the recording has finished
        self.app.ffmpeg_interface. \
            capture_until_flagged(self.last_size_offsets, timer,
                                  start_callback=lambda: self.app.make_gui_call(
                                      self.configure, state=tk.NORMAL),
                                  finish_callback=lambda: self.app.make_gui_call(
                                      self.done_capturing))
        # self.exit_screenshot_mode()
        return event

    def done_capturing(self):
        self.app.restore_state()
        if self.app.ffmpeg_interface.ready_for_export():
            self.app.action_buttons.save_button.configure(state=tk.NORMAL)

    def exit_screenshot_mode(self):
        self.snip_surface.destroy()
        self.app.master.geometry(self.prev_geometry)
        self.master_screen.withdraw()
        self.parent.parent.deiconify()

    def cancel_capture(self):
        self.configure(state=tk.NORMAL)
        self.exit_screenshot_mode()

    def on_snip_press(self, event):
        # save mouse drag start position
        self.start_x, self.start_y = event.x_root, event.y_root
        self.snip_surface.create_rectangle(0, 0, 1, 1, outline='red', width=3, fill='maroon3')

    def on_snip_drag(self, event):
        # expand rectangle as you drag the mouse
        self.current_x, self.current_y = (event.x_root, event.y_root)
        self.snip_surface.coords(1, self.start_x, self.start_y, self.current_x, self.current_y)


class OptionsMenu(tk.Frame):
    def __init__(self, parent, app: Application, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.parent = parent
        self.app = app

        self.cap_fps = tk.StringVar(self, str(self.app.ffmpeg_interface.cfg.cap_fps))
        self.output_width = tk.StringVar(self, str(self.app.ffmpeg_interface.cfg.output_width))
        self.start_delay = tk.StringVar(self, '0.0')
        self.show_mouse = tk.BooleanVar(self, self.app.ffmpeg_interface.cfg.draw_mouse)
        self.quality = tk.IntVar(value=self.app.ffmpeg_interface.cfg.gifski_quality)
        self.motion_quality = tk.IntVar(value=self.app.ffmpeg_interface.cfg.gifski_motion_quality)
        self.lossy_quality = tk.IntVar(value=self.app.ffmpeg_interface.cfg.gifski_lossy_quality)
        self.export_fps = tk.StringVar(self, str(self.app.ffmpeg_interface.cfg.export_fps))
        self.keep_percentage = tk.DoubleVar(self, self.app.ffmpeg_interface.cfg.keep_percentage)
        self.reverse_gif = tk.BooleanVar(self, self.app.ffmpeg_interface.cfg.reverse_gif)

        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=0)
        self.columnconfigure(2, weight=0)
        self.columnconfigure(3, weight=1)
        self.rowconfigure(4, pad=8)
        self.rowconfigure(5, pad=8)

        # Column 0, top to bottom
        col = 0
        capture_label = tk.Label(self, text='Capture Settings', font='bold', justify=tk.CENTER)
        capture_label.grid(row=0, column=col, columnspan=3)

        self.cap_fps_label = tk.Label(self, text='Capture FPS: ', width=12, anchor='e')
        self.cap_fps_label.grid(row=1, column=col)

        self.width_label = tk.Label(self, text='Max Width: ', width=12, anchor='e')
        self.width_label.grid(row=2, column=col)

        self.width_label = tk.Label(self, text='Start Delay: ', width=12, anchor='e')
        self.width_label.grid(row=3, column=col)

        self.mouse_entry = tk.Checkbutton(self, width=10, text='Show Mouse', onvalue=True, offvalue=False,
                                          variable=self.show_mouse, selectcolor='black', anchor='w',
                                          command=self.update_mouse)
        self.mouse_entry_org_bg = self.mouse_entry['background']
        self.mouse_entry.bind('<Enter>', lambda *_: self.mouse_entry.configure(background=CaptureButton.START_COLOR))
        self.mouse_entry.bind('<Leave>', lambda *_: self.mouse_entry.configure(background=self.mouse_entry_org_bg))
        self.mouse_entry.grid(row=4, column=col, columnspan=2)

        # Column 1, top to bottom
        col = 1
        self.cap_fps_entry = tk.Entry(self, width=10, textvariable=self.cap_fps)
        self.cap_fps_entry.bind('<FocusOut>', self.update_cap_fps)
        self.cap_fps_entry.grid(row=1, column=col)

        self.width_entry = tk.Entry(self, width=10, textvariable=self.output_width)
        self.width_entry.bind('<FocusOut>', self.update_width)
        self.width_entry.grid(row=2, column=col)

        self.delay_entry = tk.Entry(self, width=10, textvariable=self.start_delay)
        self.delay_entry.bind('<FocusOut>', self.update_delay)
        self.delay_entry.grid(row=3, column=col)

        # Column 2, top to bottom
        col = 2
        self.quality_label = tk.Label(self, text='Quality:', width=16, anchor='e')
        self.quality_label.grid(row=1, column=col)

        self.motion_label = tk.Label(self, text='Motion Quality:', width=16, anchor='e')
        self.motion_label.grid(row=2, column=col)

        self.lossy_label = tk.Label(self, text='Lossy Quality:', width=16, anchor='e')
        self.lossy_label.grid(row=3, column=col)

        tk.Label(self, text='Export FPS: ', width=12, anchor='e').grid(row=4, column=col, sticky='e')

        tk.Label(self, text='Keep frame %: ', width=16, anchor='e').grid(row=5, column=col, sticky='e')

        # Column 3, top to bottom
        col = 3
        save_settings_label = tk.Label(self, text='Save Settings', font='bold', justify=tk.CENTER)
        save_settings_label.grid(row=0, column=col, columnspan=2)

        self.gifski_quality = tk.Scale(self, from_=1, to=100, orient=tk.HORIZONTAL, variable=self.quality,
                                       command=self.update_quality)
        self.orig_slider_col = self.gifski_quality['background']
        self.orig_trough_col = self.gifski_quality['troughcolor']
        self.gifski_quality.grid(row=1, column=col, sticky='new', padx=4)

        self.gifski_motion_quality = tk.Scale(self, from_=1, to=100, orient=tk.HORIZONTAL, variable=self.motion_quality,
                                              command=self.update_motion_quality)
        self.gifski_motion_quality.grid(row=2, column=col, sticky='new', padx=4)

        self.gifski_lossy_quality = tk.Scale(self, from_=1, to=100, orient=tk.HORIZONTAL, variable=self.lossy_quality,
                                             command=self.update_lossy_quality)
        self.gifski_lossy_quality.grid(row=3, column=col, sticky='new', padx=4)

        self.export_fps_entry = tk.Entry(self, width=10, textvariable=self.export_fps)
        self.export_fps_entry.bind('<FocusOut>', self.update_export_fps)
        self.export_fps_entry.grid(row=4, column=col)

        self.keep_percentage_entry = tk.Entry(self, width=10, textvariable=self.keep_percentage)
        self.keep_percentage_entry.bind('<FocusOut>', self.update_keep_percentage)
        self.keep_percentage_entry.grid(row=5, column=col)

        self.reverse_checkbutton = tk.Checkbutton(self, width=10, text='Reverse GIF', onvalue=True, offvalue=False,
                                                  variable=self.reverse_gif, selectcolor='black', anchor='w',
                                                  command=self.update_reverse)
        reverse_checkbutton_org_bg = self.mouse_entry['background']
        self.reverse_checkbutton.bind('<Enter>', lambda *_: self.reverse_checkbutton.configure(
            background=CaptureButton.START_COLOR))
        self.reverse_checkbutton.bind('<Leave>', lambda *_: self.reverse_checkbutton.configure(
            background=reverse_checkbutton_org_bg))
        self.reverse_checkbutton.grid(row=6, column=col, columnspan=2)

    def update_cap_fps(self, _):
        try:
            fps = max(RecorderConfig.MIN_FPS, min(RecorderConfig.MAX_FPS, int(self.cap_fps.get())))
        except ValueError:
            fps = RecorderConfig.DEFAULT_FPS
        self.cap_fps.set(str(fps))
        self.export_fps.set(str(fps))
        self.app.ffmpeg_interface.cfg.cap_fps = fps
        self.app.ffmpeg_interface.cfg.export_fps = fps

    def update_width(self, _):
        try:
            width = max(RecorderConfig.MIN_WIDTH, min(RecorderConfig.MAX_WIDTH, int(self.output_width.get())))
        except ValueError:
            width = RecorderConfig.DEFAULT_WIDTH
        self.output_width.set(str(width))
        self.app.ffmpeg_interface.cfg.output_width = width

    def update_delay(self, _):
        try:
            # noinspection PyTypeChecker
            delay = max(0.0, min(30.0, float(self.start_delay.get())))
        except ValueError:
            delay = 0.0
        self.app.delay = delay
        self.start_delay.set(str(delay))

    def update_mouse(self):
        if self.mouse_entry['state'] == tk.DISABLED:
            return
        self.app.ffmpeg_interface.cfg.draw_mouse = self.show_mouse.get()

    def update_quality(self, val):
        self.app.ffmpeg_interface.cfg.gifski_quality = val

    def update_motion_quality(self, val):
        self.app.ffmpeg_interface.cfg.gifski_motion_quality = val

    def update_lossy_quality(self, val):
        self.app.ffmpeg_interface.cfg.gifski_lossy_quality = val

    def update_export_fps(self, _):
        try:
            fps = max(RecorderConfig.MIN_FPS, min(RecorderConfig.MAX_FPS, int(self.export_fps.get())))
        except ValueError:
            fps = RecorderConfig.DEFAULT_FPS
        self.export_fps.set(str(fps))
        self.app.ffmpeg_interface.cfg.export_fps = fps

    def update_keep_percentage(self, _):
        try:
            percentage = max(1.0, min(100.0, float(self.keep_percentage.get())))
        except ValueError:
            percentage = 100.0
        self.keep_percentage.set(percentage)
        self.app.ffmpeg_interface.cfg.keep_percentage = percentage

    def update_reverse(self):
        if self.reverse_checkbutton['state'] == tk.DISABLED:
            return
        self.app.ffmpeg_interface.cfg.reverse_gif = self.reverse_gif.get()

    def enable(self):
        for widget in (self.cap_fps_entry, self.width_entry, self.delay_entry, self.mouse_entry, self.gifski_quality,
                       self.gifski_motion_quality, self.gifski_lossy_quality, self.export_fps_entry,
                       self.keep_percentage_entry):
            widget.configure(state=tk.NORMAL)

    def disable(self):
        for widget in (self.cap_fps_entry, self.width_entry, self.delay_entry, self.mouse_entry, self.gifski_quality,
                       self.gifski_motion_quality, self.gifski_lossy_quality, self.export_fps_entry,
                       self.keep_percentage_entry):
            widget.configure(state=tk.DISABLED)


class _GUICallData:
    def __init__(self, fn, args, kwargs):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.reply = None
        self.reply_event = threading.Event()


if __name__ == '__main__':
    # pyinstaller dir check
    if hasattr(sys, '_MEIPASS'):
        os.chdir(sys._MEIPASS)

    root = tk.Tk()
    root.iconbitmap(rf'{PACKAGE_DIRECTORY}\icon.ico')
    root.tk.call('lappend', 'auto_path', 'breeze-dark')
    root.tk.call('package', 'require', 'ttk::theme::breeze-dark')
    root.minsize(600, 350)
    root.rowconfigure(0, weight=1)
    root.columnconfigure(0, weight=1)
    application = Application(root)


    def cleanup():
        application.ffmpeg_interface.cfg.remove_tempfile()
        root.destroy()


    root.protocol("WM_DELETE_WINDOW", cleanup)
    root.mainloop()

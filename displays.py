import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Windows version >= 8.1
except:
    ctypes.windll.user32.SetProcessDPIAware()  # Windows version <= 8.0


MONITORINFOF_PRIMARY = 0x01

# https://stackoverflow.com/questions/65256092/how-to-open-tkinter-gui-on-second-monitor-display-windows
class RECT(ctypes.Structure):
    _fields_ = [
        ('left', ctypes.c_long),
        ('top', ctypes.c_long),
        ('right', ctypes.c_long),
        ('bottom', ctypes.c_long)
    ]

    def dump(self):
        return [int(val) for val in (self.left, self.top, self.right, self.bottom)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.c_ulong),
        ('rcMonitor', RECT),
        ('rcWork', RECT),
        ('dwFlags', ctypes.c_ulong)
    ]


def get_monitors():
    monitors = []
    CBFUNC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(RECT), ctypes.c_double)

    def cb(monitorHandle, hdcMonitor, lprcMonitor, dwData):
        r = lprcMonitor.contents
        monitors.append([monitorHandle, r.dump()])
        return 1

    cbfunc = CBFUNC(cb)
    temp = ctypes.windll.user32.EnumDisplayMonitors(0, 0, cbfunc, 0)
    return monitors


def monitor_areas():
    areas = []
    monitors = get_monitors()

    for monitorHandle, extents in monitors:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        mi.rcMonitor = RECT()
        mi.rcWork = RECT()
        res = ctypes.windll.user32.GetMonitorInfoA(monitorHandle, ctypes.byref(mi))
        area_rect = mi.rcMonitor.dump()
        is_primary = bool(mi.dwFlags & MONITORINFOF_PRIMARY)
        if is_primary:
            areas.insert(0, area_rect)
        else:
            areas.append(area_rect)

    return areas


if __name__ == '__main__':
    n = '\n'
    print(f'Installed monitors:\n{n.join(str(mon) for mon in get_monitors())}')
    print(f'\n\nMonitor areas:\n{n.join(str(area) for area in monitor_areas())}')
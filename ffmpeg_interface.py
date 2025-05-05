import queue
import subprocess
import threading
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor
import tempfile
import uuid
import os
import shutil
import time
from dataclasses import dataclass
from bisect import bisect_left
from pathlib import Path

from constants import PACKAGE_DIRECTORY


# https://ffmpeg.org/ffmpeg-filters.html#ddagrab
# Capture primary screen and encode using nvenc:
# ffmpeg -f lavfi -i ddagrab -c:v h264_nvenc -cq 18 output.mp4

# You can also skip the lavfi device and directly use the filter. Also demonstrates downloading the frame and encoding with libx264. Explicit output format specification is required in this case:
# ffmpeg -filter_complex ddagrab=output_idx=1:framerate=60,hwdownload,format=bgra -c:v libx264 -crf 18 output.mp4

# If you want to capture only a subsection of the desktop, this can be achieved by specifying a smaller size and its offsets into the screen:
# ddagrab=video_size=800x600:offset_x=100:offset_y=100

# gif conversion
# ffmpeg -i input.mp4 -r 50 -vf "fps=50,scale=1280:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 output.gif
# then pipe into gifsicle to compress

FRAME_BASE_NAME = 'frame'
FRAME_FORMAT = '.png'
ZERO_PADDING = 4


@dataclass
class SizeAndOffsets:
    width: int
    height: int
    offset_x: int
    offset_y: int


# FFmpegInterface has a Config for recording settings
class RecorderConfig:
    MAX_DURATION_SECONDS = 60 * 5
    OUTPUT_FORMATS = [('gif', '.gif')]
    MIN_FPS = 1
    MAX_FPS = 50
    DEFAULT_FPS = 20
    DEFAULT_WIDTH = 600
    MIN_WIDTH = 20
    MAX_WIDTH = 3840
    DEFAULT_MOUSE = False
    DEFAULT_SCREEN = 0
    MATTE_COLOR_TEMP = '313338'
    DEFAULT_QUALITY = 90
    DEFAULT_MOTION_QUALITY = 90
    DEFAULT_LOSSY_QUALITY = 90


    def __init__(self):
        self.cap_fps: int = self.DEFAULT_FPS
        self.export_fps: int = self.DEFAULT_FPS
        self.output_width: int = self.DEFAULT_WIDTH
        # self.duplicate_frames: bool = True
        self.draw_mouse: bool = self.DEFAULT_MOUSE
        self.screen_num: int = self.DEFAULT_SCREEN  # 0-based index
        self.gifski_quality: int = self.DEFAULT_QUALITY
        self.gifski_motion_quality: int = self.DEFAULT_MOTION_QUALITY
        self.gifski_lossy_quality: int = self.DEFAULT_LOSSY_QUALITY
        self.tempfile_name: str = self._new_filename()
        self.keep_percentage: float = 100.0
        self.reverse_gif: bool = False

    def new(self):
        self.remove_tempfile()
        self.tempfile_name = self._new_filename()
        os.makedirs(self.tempfile_name)

    def remove_tempfile(self):
        shutil.rmtree(self.tempfile_name, ignore_errors=True)

    def capture_args(self, size_offsets: SizeAndOffsets | None = None) -> list:
        offsets_string = ''
        if size_offsets:
            offsets_string = f':video_size={size_offsets.width}x{size_offsets.height}' \
                             f':offset_x={size_offsets.offset_x}' \
                             f':offset_y={size_offsets.offset_y}'

        lst = [
            rf'{PACKAGE_DIRECTORY}\ffmpeg.exe',
            '-hide_banner',
            '-init_hw_device', 'd3d11va',
            '-filter_complex',

            f'ddagrab=output_idx={self.screen_num}'
            # TODO: ddagrab at low fps doesn't work very well, almost like a frame pacing issues
            f':framerate={self.cap_fps}'
            f':draw_mouse={"true" if self.draw_mouse else "false"}'
            f'{offsets_string},hwdownload,format=bgra',
            # f':dup_frames={"true" if self.duplicate_frames else "false"}',  # broken????
        ]

        lst.extend([
            os.path.join(self.tempfile_name, f'{FRAME_BASE_NAME}%0{ZERO_PADDING}d{FRAME_FORMAT}')
        ])

        return lst

    def final_format_conversion_args(self, frames: str, filename: str, size_offsets: SizeAndOffsets) -> list:
        width = min(size_offsets.width, self.output_width)
        return [
            rf'{PACKAGE_DIRECTORY}\gifski.exe',
            '--fps', str(self.export_fps),
            '--width', str(width),
            '--quiet',
            '--repeat', '0',
            '--matte', self.MATTE_COLOR_TEMP,
            '--quality', str(self.gifski_quality),
            '--motion-quality', str(self.gifski_motion_quality),
            '--lossy-quality', str(self.gifski_motion_quality),
            '--output', filename,
            frames,
        ]

    @staticmethod
    def _new_filename():
        return os.path.join(tempfile.gettempdir(),
                            f'snip-{uuid.uuid1()}')


# methods record to a temp file and then encode the output with target settings
# should check if output file already exists then warn or error
class FFmpegInterface:
    def __init__(self, config: RecorderConfig):
        self.cfg = config
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._capturing = True
        self._finished = threading.Event()
        self._done_capturing = threading.Event()
        self._capture_finish_queue = queue.Queue()

    def capture_until_flagged(self, size_offsets: SizeAndOffsets, timer: threading.Timer | None = None,
                              start_callback: Optional[Callable] = None,
                              finish_callback: Optional[Callable] = None):
        self._executor.submit(self._capture_until_flagged, size_offsets, timer, start_callback, finish_callback)

    def ready_for_export(self) -> bool:
        return os.path.exists(self.cfg.tempfile_name)

    def stop_capture(self):
        self._done_capturing.set()

    def save(self, filename: str, size_offsets: SizeAndOffsets | None, callback: Optional[Callable] = None):
        if not self.ready_for_export():
            return

        self._executor.submit(self._save, filename, size_offsets, callback=callback)

    def capturing(self) -> bool:
        return self._capturing

    def wait(self):
        """
        Blocks the current thread until the active capture finishes recording.
        If not capturing, the function returns immediately
        """
        if not self._capturing:
            return
        self._finished.wait()
        self._finished.clear()

    def add_finish_task(self, fn: Callable):
        self._capture_finish_queue.put(fn)

    def _save(self, filename: str, size_offsets: SizeAndOffsets | None, callback: Optional[Callable] = None):
        if os.path.exists(filename):
            os.remove(filename)

        # delete the 1st frame (ddagrab has a 1 frame startup delay?)
        if os.path.exists(joined := os.path.join(self.cfg.tempfile_name, f'{FRAME_BASE_NAME}{(ZERO_PADDING - 1) * "0"}1{FRAME_FORMAT}')):
            os.remove(joined)

        # keep a percentage of frames which are relatively evenly distributed
        if self.cfg.keep_percentage != 100.0 or self.cfg.reverse_gif:
            subset_path = os.path.join(self.cfg.tempfile_name, f'{FRAME_BASE_NAME}_subset')
            shutil.rmtree(subset_path, ignore_errors=True)
            frames = list(Path(self.cfg.tempfile_name).glob(f'*{FRAME_FORMAT}'))
            frames.sort()
            max_frame_num = extract_frame_number(frames[-1].name) + 1
            frame_subset = equal_dist_els(frames, self.cfg.keep_percentage / 100.0) if self.cfg.keep_percentage != 100.0 else frames
            # now, symlink the subset frames to their original location in the parent folder
            # frame_subset/frame0001.png (symlink) -> frame0001.png
            # the frame number is set to max_frame# - orig# if gif is to be reversed
            os.mkdir(subset_path)
            for frame in frame_subset:
                new_name = frame.name
                if self.cfg.reverse_gif:
                    new_frame_number = max_frame_num - extract_frame_number(frame.name)
                    new_name = format_frame_number(new_frame_number)

                subprocess.run(['cmd', '/c', 'mklink', os.path.join(subset_path, new_name), str(frame)],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)

            frames = os.path.join(subset_path, f'frame*{FRAME_FORMAT}')
        else:
            frames = os.path.join(self.cfg.tempfile_name, f'frame*{FRAME_FORMAT}')

        args = self.cfg.final_format_conversion_args(frames, filename, size_offsets)
        subprocess.run(args,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        # self.cfg.remove_tempfile()
        if callback:
            callback()

    # TODO: read the output of ffmpeg stderr to determine when the recording has actually started
    def _capture_until_flagged(self, size_offsets: SizeAndOffsets, timer: threading.Timer | None = None,
                               start_callback: Optional[Callable] = None,
                               finish_callback: Optional[Callable] = None):
        self.cfg.new()
        self._capturing = True
        self._finished.clear()
        proc = subprocess.Popen(self.cfg.capture_args(size_offsets),
                                stdin=subprocess.PIPE,
                                # creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                text=True)
        # stderr=subprocess.PIPE)
        time.sleep(0.25)  # estimate a .25s capture startup delay as a hack
        if start_callback:
            start_callback()
        self._done_capturing.wait()
        self._done_capturing.clear()
        if timer:
            timer.cancel()
        proc.stdin.write('q')
        proc.terminate()
        proc.wait()
        self._finished.set()
        self._capturing = False
        if finish_callback:
            finish_callback()
        while not self._capture_finish_queue.empty():
            fn = self._capture_finish_queue.get()
            fn()


def equal_dist_els(my_list, fraction):
    """
    Chose a fraction of equally distributed elements.
    :param my_list: The list to draw from
    :param fraction: The ideal fraction of elements
    :return: Elements of the list with the best match
    """
    length = len(my_list)
    list_indexes = range(length)
    nbr_bins = int(round(length * fraction))
    step = length / float(nbr_bins)  # the size of a single bin
    bins = [step * i for i in range(nbr_bins)]  # list of bin ends
    # distribute indexes into the bins
    splits = [bisect_left(list_indexes, wall) for wall in bins]
    splits.append(length)  # add the end for the last bin
    # get a list of (start, stop) indexes for each bin
    bin_limits = [(splits[i], splits[i + 1]) for i in range(len(splits) - 1)]
    out = []
    for bin_lim in bin_limits:
        f, t = bin_lim
        in_bin = my_list[f:t]  # choose the elements in my_list belonging in this bin
        out.append(in_bin[int(0.5 * len(in_bin))])  # choose the most central element
    return out


def extract_frame_number(frame: str) -> int:
    return int(frame.split('.')[0].removeprefix(FRAME_BASE_NAME))


def format_frame_number(frame_number: int) -> str:
    return f'{FRAME_BASE_NAME}{frame_number:0{ZERO_PADDING}}{FRAME_FORMAT}'

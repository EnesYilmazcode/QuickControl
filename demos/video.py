"""Record an observed fixture rectangle and compose normal-speed demo clips.

Only start recording after confirming the supplied rectangle contains synthetic
fixture content. There is no desktop discovery or guessed capture rectangle.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def executable(name):
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f'{name} must be on PATH')
    return found


class Recorder:
    def __init__(self, output, rect, fps=30, ffmpeg=None):
        self.output = Path(output)
        self.rect = rect
        self.fps = fps
        self.ffmpeg = ffmpeg or executable('ffmpeg')
        self.process = None

    def start(self):
        if self.process is not None:
            raise RuntimeError('Recorder already started')
        left, top, width, height = self.rect
        if width <= 0 or height <= 0:
            raise ValueError('Capture dimensions must be positive')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.log = self.output.with_suffix('.log').open('wb')
        self.process = subprocess.Popen([
            self.ffmpeg, '-hide_banner', '-y', '-f', 'gdigrab',
            '-framerate', str(self.fps), '-offset_x', str(left), '-offset_y', str(top),
            '-video_size', f'{width}x{height}', '-draw_mouse', '1', '-i', 'desktop',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(self.output)],
            stdin=subprocess.PIPE, stdout=self.log, stderr=self.log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Recording setup is outside the timed GUI workload.
        time.sleep(.6)
        if self.process.poll() is not None:
            self.log.close()
            raise RuntimeError(f'ffmpeg failed; inspect {self.output.with_suffix(".log")}')
        return self

    def stop(self):
        if self.process is None:
            return
        try:
            self.process.communicate(b'q\n', timeout=15)
            if self.process.returncode:
                raise RuntimeError(f'ffmpeg exited with {self.process.returncode}')
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.log.close()


def compose(baseline, optimized, output, ffmpeg=None):
    ffmpeg = ffmpeg or executable('ffmpeg')
    probe = str(Path(ffmpeg).with_name('ffprobe.exe' if Path(ffmpeg).suffix == '.exe' else 'ffprobe'))
    durations = []
    for path in [baseline, optimized]:
        value = subprocess.check_output([probe, '-v', 'error', '-show_entries', 'format=duration',
                                         '-of', 'json', str(path)], text=True)
        durations.append(float(json.loads(value)['format']['duration']))
    duration = max(durations)
    # Windows ffmpeg builds may have no fontconfig configuration. Supply the
    # installed UI font explicitly instead of crashing during default lookup.
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf'
    font_option = "fontfile='" + font.as_posix().replace(':', r'\:') + "':" if font.exists() else ''
    streams = []
    for index, label in enumerate(['Baseline Windows MCP', 'QuickControl']):
        streams.append(
            f'[{index}:v]setpts=PTS-STARTPTS,fps=30,scale=960:540:force_original_aspect_ratio=decrease,'
            f'pad=960:580:(ow-iw)/2:40:color=0x162138,'
            f'drawtext={font_option}text={label}:fontcolor=white:fontsize=25:x=20:y=6,'
            f'tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}[v{index}]')
    streams.append('[v0][v1]hstack=inputs=2[out]')
    subprocess.run([ffmpeg, '-hide_banner', '-y', '-i', str(baseline), '-i', str(optimized),
                    '-filter_complex', ';'.join(streams), '-map', '[out]', '-an',
                    '-c:v', 'libx264', '-crf', '20', '-r', '30', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', str(output)], check=True)
    metadata = {'baseline': str(baseline), 'optimized': str(optimized),
                'input_duration_seconds': durations, 'output_duration_seconds': duration,
                'playback': 'Original speed; shorter clip freezes on its final frame',
                'alignment': 'Recording starts; no cuts or automatic click alignment'}
    Path(output).with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('optimized', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ffmpeg')
    args = parser.parse_args()
    print(json.dumps(compose(args.baseline, args.optimized, args.output, args.ffmpeg), indent=2))

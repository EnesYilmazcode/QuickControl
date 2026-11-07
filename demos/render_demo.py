"""Render a cropped, normal-speed side-by-side GUI demonstration.

Default crop is specific to the reviewed 1240x900 fixture recordings. Input
videos remain untouched. Output audit frames are decoded from the final MP4.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def run(command):
    subprocess.run(command, check=True)


def probe(path, ffprobe):
    return json.loads(subprocess.check_output([
        ffprobe, '-v', 'error', '-show_entries',
        'format=duration:stream=width,height,codec_type', '-of', 'json', str(path)], text=True))


def render(baseline, optimized, output, *, ffmpeg=None, baseline_seconds=None,
           optimized_seconds=None, gif=False, poster=False,
           caption='Synthetic contact intake'):
    ffmpeg = ffmpeg or shutil.which('ffmpeg')
    if not ffmpeg:
        raise FileNotFoundError('Put ffmpeg on PATH or pass --ffmpeg')
    ffprobe = str(Path(ffmpeg).with_name('ffprobe.exe' if Path(ffmpeg).suffix == '.exe' else 'ffprobe'))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    infos = [probe(path, ffprobe) for path in (baseline, optimized)]
    durations = [float(info['format']['duration']) for info in infos]
    for info in infos:
        stream = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
        if (stream['width'], stream['height']) != (1240, 900):
            raise ValueError('Privacy crop requires reviewed 1240x900 source recordings')
    duration = max(durations)
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf'
    font_option = "fontfile='" + font.as_posix().replace(':', r'\:') + "':" if font.exists() else ''

    def text(value, size, x, y, color='white'):
        # Only fixed labels and numeric timing strings are passed here.
        return f"drawtext={font_option}text='{value}':fontcolor={color}:fontsize={size}:x={x}:y={y}"

    labels = ['Baseline Windows MCP', 'QuickControl']
    numbers = [baseline_seconds, optimized_seconds]
    filters = []
    for index, label in enumerate(labels):
        if numbers[index] is not None:
            label += f' | {float(numbers[index]):.2f} s warm GUI input'
        filters.append(
            f'[{index}:v]crop=1200:820:20:40,setpts=PTS-STARTPTS,fps=30,'
            f'pad=1200:890:0:60:color=0x15223b,' + text(label, 30, 20, 13) + ','
            f'tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}[v{index}]')
    filters.append('[v0][v1]hstack=inputs=2,pad=2400:980:0:0:color=0x15223b,'
                   + text('Original speed | Shorter recording freezes on its final frame', 27, 24, 903)
                   + ',' + text(caption + ' | Warm GUI input excludes startup and agent reasoning', 26, 24, 943)
                   + '[out]')
    run([ffmpeg, '-hide_banner', '-y', '-i', str(baseline), '-i', str(optimized),
         '-filter_complex', ';'.join(filters), '-map', '[out]', '-an', '-c:v', 'libx264',
         '-preset', 'medium', '-crf', '20', '-r', '30', '-pix_fmt', 'yuv420p',
         '-movflags', '+faststart', str(output)])
    output_info = probe(output, ffprobe)
    output_duration = float(output_info['format']['duration'])
    # These exact decoded frames allow independent first/mid/final privacy and
    # outcome review. They are evidence, not a substitute for viewing the clip.
    frames = {}
    for name, second in [('first', .05), ('middle', output_duration/2),
                         ('final', max(0, output_duration-.10))]:
        image = output.with_name(output.stem + '-' + name + '.png')
        run([ffmpeg, '-v', 'error', '-y', '-ss', str(second), '-i', str(output),
             '-frames:v', '1', '-update', '1', str(image)])
        frames[name] = {'path': str(image), 'seconds': second}
    if poster:
        shutil.copyfile(frames['middle']['path'], output.with_suffix('.png'))
    if gif:
        # fps/scale change image quality only. No PTS change or playback speedup.
        graph = '[0:v]fps=12,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse'
        run([ffmpeg, '-v', 'error', '-y', '-i', str(output), '-filter_complex', graph,
             '-loop', '0', str(output.with_suffix('.gif'))])
    metadata = {'baseline': str(baseline), 'optimized': str(optimized),
                'source_durations_seconds': durations, 'output_duration_seconds': output_duration,
                'source_crop_xywh': [20, 40, 1200, 820],
                'source_dimensions_required': [1240, 900],
                'playback': 'Original speed; shorter final frame held; no source frames cut',
                'alignment': 'Original recording starts; no automatic click alignment',
                'caption_warm_gui_seconds': numbers,
                'scope': caption + '; warm GUI input excludes startup and agent reasoning',
                'audit_frames': frames}
    output.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('optimized', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--baseline-seconds', type=float)
    parser.add_argument('--optimized-seconds', type=float)
    parser.add_argument('--gif', action='store_true')
    parser.add_argument('--poster', action='store_true')
    parser.add_argument('--caption', default='Synthetic contact intake')
    args = parser.parse_args()
    print(json.dumps(render(args.baseline, args.optimized, args.output, ffmpeg=args.ffmpeg,
                            baseline_seconds=args.baseline_seconds,
                            optimized_seconds=args.optimized_seconds,
                            gif=args.gif, poster=args.poster,
                            caption=args.caption), indent=2))

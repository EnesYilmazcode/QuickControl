"""Side-by-side of two agent screen recordings at original speed.

python bench/render_side.py left.mp4 right.mp4 out.mp4 "Windows MCP" "QuickControl"
The shorter clip holds its last frame until the longer one ends.
"""
import os
import subprocess
import sys
from pathlib import Path

left, right, out, left_label, right_label = sys.argv[1:6]
font = (Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arialbd.ttf').as_posix().replace(':', '\\:')


def label(text):
    return (f"drawtext=fontfile='{font}':text='{text}':x=20:y=h-60:fontsize=34:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=12")


graph = (f"[0:v]scale=960:-2,tpad=stop_mode=clone:stop_duration=600,{label(left_label)},"
         "drawtext=fontfile='" + font + "':text='%{pts\\:hms}':x=w-tw-20:y=20:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=8[a];"
         f"[1:v]scale=960:-2,tpad=stop_mode=clone:stop_duration=600,{label(right_label)},"
         "drawtext=fontfile='" + font + "':text='%{pts\\:hms}':x=w-tw-20:y=20:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=8[b];"
         "[a][b]hstack=inputs=2[v]")
duration = max(float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                              '-of', 'csv=p=0', p], text=True)) for p in (left, right))
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', left, '-i', right,
                '-filter_complex', graph, '-map', '[v]', '-t', f'{duration + 1:.2f}', '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p', '-crf', '24', '-movflags', '+faststart', out], check=True)
print(out, f'{duration:.1f} s')

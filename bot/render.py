"""Turn a source video + chosen moment into a 1080x1920 Short with hook, animated captions, credit."""
import os
import re
import subprocess

W, H = 1080, 1920
FONT = os.environ.get("CLIP_FONT", "Montserrat")


def _ts(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(lines)


def _chunks(words, max_words=3, gap=0.6):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > gap
                    or re.search(r"[.?!,।]$", cur[-1]["word"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def build_ass(path, words, dur, hook, credit, channel, caption_y, hook_y):
    yellow, white = "&H0000E6FF&", "&H00FFFFFF&"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,5,40,40,0,1
Style: Hook,{FONT},74,&H00000000,&H00000000,&H0000E6FF,&H0000E6FF,-1,0,0,0,100,100,0,0,3,22,0,5,60,60,0,1
Style: Small,{FONT},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    # Hook: pops in, stays the whole clip
    ev.append(f"Dialogue: 2,{_ts(0)},{_ts(dur)},Hook,,0,0,0,,"
              f"{{\\pos({W//2},{hook_y})\\fscx60\\fscy60\\t(0,180,\\fscx100\\fscy100)}}{_wrap(_esc(hook.upper()), 20)}")
    ev.append(f"Dialogue: 1,{_ts(0)},{_ts(dur)},Small,,0,0,0,,{{\\pos({W//2},{H-150})\\alpha&H40&}}"
              f"{_esc(f'🎥 {credit}' + (f'  •  {channel}' if channel else ''))}")
    # Word-by-word karaoke captions
    for ch in _chunks(words):
        for i, w in enumerate(ch):
            st = w["start"]
            en = ch[i + 1]["start"] if i + 1 < len(ch) else w["end"] + 0.15
            if en <= st or st >= dur:
                continue
            parts = []
            for j, x in enumerate(ch):
                t = _esc(x["word"].upper())
                parts.append(f"{{\\c{yellow}\\fscx112\\fscy112}}{t}{{\\c{white}\\fscx100\\fscy100}}" if j == i else t)
            ev.append(f"Dialogue: 0,{_ts(st)},{_ts(min(en, dur))},Cap,,0,0,0,,"
                      f"{{\\pos({W//2},{caption_y})}}{' '.join(parts)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")


def render(src, info, start, end, words, hook, credit, channel, layout, out, fonts_dir=None, overlay=None):
    dur = end - start
    rel = [{"start": w["start"] - start, "end": w["end"] - start, "word": w["word"]}
           for w in words if start - 0.05 <= w["start"] < end and w["word"]]
    vertical = info["h"] > info["w"]
    if vertical or layout == "center_crop":
        vf = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base]"
        caption_y, hook_y = int(H * 0.68), 330
    else:  # blur_fit: blurred fill behind the full 16:9 frame
        fg_h = int(round(info["h"] * W / info["w"] / 2) * 2)
        top = (H - fg_h) // 2
        vf = (f"[0:v]split=2[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.18[bg];"
              f"[b]scale={W}:{fg_h},setsar=1[fg];[bg][fg]overlay=0:{top}[base]")
        caption_y, hook_y = min(H - 330, top + fg_h + 170), max(220, top - 190)
    ass = os.path.splitext(out)[0] + ".ass"
    if overlay:  # campaign logo sits at the very top, so push the hook below it
        hook_y = max(hook_y, 420)
    build_ass(ass, rel, dur, hook, credit, channel, caption_y, hook_y)
    sub = f"subtitles={ass}" + (f":fontsdir={fonts_dir}" if fonts_dir else "")
    # gentle 3% zoom-in over the clip so it isn't a static re-upload
    zoom = f"[base]scale=w='{W}*(1+0.03*t/{dur:.2f})':h=-2:eval=frame,crop={W}:{H},{sub},fps=30"
    inputs = ["-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", src]
    if overlay:  # required campaign logo, visible for the whole clip
        inputs += ["-loop", "1", "-i", overlay]
        graph = f"{vf};{zoom}[z];[1:v]scale=600:-1[lg];[z][lg]overlay=(W-w)/2:70:shortest=1,format=yuv420p[v]"
    else:
        graph = f"{vf};{zoom},format=yuv420p[v]"
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st={:.2f}:d=0.4".format(max(0, dur - 0.4)),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", out]
    print("$ ffmpeg ... ->", out)
    subprocess.run(cmd, check=True)
    return out
